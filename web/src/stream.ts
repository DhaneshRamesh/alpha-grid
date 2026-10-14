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

function validSnapshot(value: unknown): value is WireEngineSnapshot {
  return (
    typeof value === "object" &&
    value !== null &&
    "instruments" in value &&
    Array.isArray(value.instruments) &&
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
  return {
    ...state,
    snapshot,
    sequence: message.publication_sequence,
    connection: "CONNECTED",
    messageLagMs: lag,
  };
}

export function useEngineStream(): StreamState {
  const [state, setState] = useState<StreamState>(initialState);
  useEffect(() => {
    let closed = false;
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let failures = 0;
    async function resync() {
      try {
        const response = await fetch("/api/snapshot", { cache: "no-store" });
        if (response.ok) {
          const envelope = (await response.json()) as Envelope;
          if (!closed)
            setState((current) =>
              current.snapshot ? current : applyEnvelope(current, envelope),
            );
        }
      } catch {
        /* WebSocket retry owns connection status. */
      }
    }
    function connect() {
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
                .then((response) => response.json())
                .then((full) =>
                  setState((latest) => applyEnvelope(latest, full as Envelope)),
                )
                .catch(() => {});
              return current;
            }
            return applyEnvelope(current, message);
          });
        } catch {
          /* Malformed presentation messages never touch capture. */
        }
      };
      socket.onclose = () => {
        if (closed) return;
        setState((current) => ({
          ...current,
          connection: "DISCONNECTED",
          reconnects: current.reconnects + 1,
        }));
        failures += 1;
        retry = setTimeout(
          connect,
          Math.min(5_000, 250 * 2 ** Math.min(failures, 5)),
        );
      };
      socket.onerror = () => socket?.close();
    }
    void resync();
    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      socket?.close();
    };
  }, []);
  return state;
}
