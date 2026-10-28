import { useEffect, useState } from "react";
import type { Envelope } from "./generated/envelope";
import type { WireEngineSnapshot } from "./generated/engine-snapshot";
import type { WireEngineUpdate } from "./generated/engine-update";

export type Connection = "CONNECTING" | "CONNECTED" | "DISCONNECTED";
export interface StreamState {
  snapshot: WireEngineSnapshot | null;
  bootId: string | null;
  sequence: string | null;
  connection: Connection;
  messageLagMs: number | null;
  reconnects: number;
}
export const initialState: StreamState = {
  snapshot: null,
  bootId: null,
  sequence: null,
  connection: "CONNECTING",
  messageLagMs: null,
  reconnects: 0,
};

let localBootstrap: Promise<void> | null = null;

function validSnapshot(value: unknown): value is WireEngineSnapshot {
  const usable = (value: unknown) => {
    if (typeof value !== "object" || value === null) return false;
    const item = value as WireEngineSnapshot["instruments"][number];
    return [item.market, item.features].every(
      (observation) =>
        observation == null ||
        (typeof observation.quality?.stale === "boolean" &&
          typeof observation.quality?.source_time_unverified === "boolean" &&
          ["DhanStandardV2", "DhanDepth20"].includes(
            observation.provenance?.source_protocol ?? "",
          )),
    );
  };
  return (
    typeof value === "object" &&
    value !== null &&
    "mode" in value &&
    "journal_blocked" in value &&
    "instruments" in value &&
    Array.isArray(value.instruments) &&
    "approved_security_ids" in value &&
    Array.isArray(value.approved_security_ids) &&
    "no_trade_reasons" in value &&
    Array.isArray(value.no_trade_reasons) &&
    ((value.mode === "OBSERVE_ONLY" &&
      value.journal_blocked === false &&
      (!("paper" in value) || value.paper == null) &&
      (value.no_trade_reasons as string[]).includes("OBSERVATION_ONLY") &&
      (value.no_trade_reasons as string[]).includes("NO_ELIGIBLE_DHAN_FEED") &&
      (value.no_trade_reasons as string[]).includes(
        "SESSION_CLASSIFICATION_UNVERIFIED",
      ) &&
      value.instruments.every(usable) &&
      value.instruments.every(
        (item: WireEngineSnapshot["instruments"][number]) =>
          item.instrument.venue === "NSE" &&
          item.instrument.exchange_segment === "NSE_EQ" &&
          item.instrument.security_id === "2885" &&
          item.features == null &&
          item.depth == null &&
          item.candles.length === 0 &&
          (!item.market ||
            item.market.provenance.source_protocol === "DhanStandardV2") &&
          (!item.market?.quality.stale ||
            (value.no_trade_reasons as string[]).includes(
              "MARKET_DATA_STALE",
            )) &&
          (!item.market?.quality.source_time_unverified ||
            (value.no_trade_reasons as string[]).includes(
              "SOURCE_TIME_UNVERIFIED",
            )),
      )) ||
      (value.mode === "DATA_ONLY" &&
        "feed_connected" in value &&
        value.feed_connected === false &&
        "paper" in value &&
        value.paper == null &&
        value.instruments.every(
          (item: WireEngineSnapshot["instruments"][number]) =>
            item.market == null &&
            item.features == null &&
            item.depth == null &&
            item.candles.length === 0,
        ))) &&
    "counters" in value
  );
}

export function applyEnvelope(
  state: StreamState,
  message: Envelope,
): StreamState {
  if (message.schema_version !== 1) return state;
  const lag = Math.max(
    0,
    Date.now() - Number(BigInt(message.publication_timestamp_ns) / 1_000_000n),
  );
  if (
    message.message_type === "engine.snapshot" &&
    validSnapshot(message.payload)
  ) {
    if (
      state.bootId === message.engine_boot_id &&
      state.sequence &&
      BigInt(message.publication_sequence) < BigInt(state.sequence)
    )
      return state;
    return {
      ...state,
      snapshot: message.payload,
      bootId: message.engine_boot_id,
      sequence: message.publication_sequence,
      connection: "CONNECTED",
      messageLagMs: lag,
    };
  }
  if (
    message.message_type !== "engine.update" ||
    !state.snapshot ||
    !state.sequence ||
    state.bootId !== message.engine_boot_id
  )
    return state;
  const next = message.payload as WireEngineUpdate;
  if (
    !next ||
    !Array.isArray(next.instruments) ||
    !next.status ||
    BigInt(message.publication_sequence) !== BigInt(state.sequence) + 1n
  )
    return state;
  const changed = new Map(
    next.instruments.map((item) => [item.instrument.key, item]),
  );
  const instruments = state.snapshot.instruments.map(
    (item) => changed.get(item.instrument.key) ?? item,
  );
  const snapshot: WireEngineSnapshot = {
    ...state.snapshot,
    ...next.status,
    generation: next.generation,
    instruments,
    audit: next.audit,
  };
  if (!validSnapshot(snapshot))
    return { ...initialState, connection: state.connection };
  return {
    ...state,
    snapshot,
    sequence: message.publication_sequence,
    connection: "CONNECTED",
    messageLagMs: lag,
  };
}

export function useEngineStream(enabled = true): StreamState {
  const [state, setState] = useState<StreamState>(initialState);
  useEffect(() => {
    if (!enabled) return;
    let closed = false;
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let failures = 0;
    function scheduleRetry() {
      if (closed) return;
      setState((current) => ({
        ...current,
        snapshot: null,
        connection: "DISCONNECTED",
        reconnects: current.reconnects + 1,
      }));
      failures += 1;
      retry = setTimeout(
        () => void connect(),
        Math.min(5_000, 250 * 2 ** Math.min(failures, 5)),
      );
    }
    async function connect() {
      if (closed) return;
      const bootstrap = /^#bootstrap=([0-9a-f]{64})$/.exec(window.location.hash)?.[1];
      if (window.location.hash.startsWith("#bootstrap=")) {
        window.history.replaceState(null, "", window.location.pathname + window.location.search);
      }
      try {
        if (bootstrap && !localBootstrap) {
          localBootstrap = fetch("/trade/session/bootstrap", {
            method: "POST",
            credentials: "same-origin",
            cache: "no-store",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token: bootstrap }),
          }).then((response) => {
            if (!response.ok) throw new Error("Local sign-in unavailable");
          }).finally(() => { localBootstrap = null; });
        }
        if (localBootstrap) await localBootstrap;
        const session = await fetch("/api/session", {
          credentials: "same-origin",
          cache: "no-store",
        });
        if (session.status === 401) {
          setState((current) => ({
            ...current,
            snapshot: null,
            connection: "DISCONNECTED",
          }));
          return;
        }
        if (!session.ok) throw new Error("UI session unavailable");
      } catch {
        scheduleRetry();
        return;
      }
      if (closed) return;
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${location.host}/ws`);
      socket.onopen = () => {
        failures = 0;
      };
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data) as Envelope;
          setState((current) => {
            if (
              message.message_type === "engine.update" &&
              current.sequence &&
              BigInt(message.publication_sequence) !==
                BigInt(current.sequence) + 1n
            ) {
              void fetch("/api/snapshot", { cache: "no-store" })
                .then((response) => {
                  if (!response.ok)
                    throw new Error("resynchronization unavailable");
                  return response.json();
                })
                .then((full) =>
                  setState((latest) => applyEnvelope(latest, full as Envelope)),
                )
                .catch(() => {});
              return { ...current, snapshot: null, connection: "DISCONNECTED" };
            }
            return applyEnvelope(current, message);
          });
        } catch {
          /* Malformed presentation messages never touch capture. */
        }
      };
      socket.onclose = () => {
        scheduleRetry();
      };
      socket.onerror = () => socket?.close();
    }
    void connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      socket?.close();
    };
  }, [enabled]);
  return state;
}

export async function forgetCockpitSession(): Promise<boolean> {
  const response = await fetch("/api/session", {
    method: "DELETE",
    credentials: "same-origin",
    cache: "no-store",
  });
  return response.ok;
}
