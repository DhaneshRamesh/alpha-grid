import { describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { applyEnvelope, initialState, useEngineStream } from "./stream";
import { price } from "./format";
import type { Envelope } from "./generated/envelope";
import type { WireEngineSnapshot } from "./generated/engine-snapshot";

const snapshot = {
  generation: "1",
  replay_epoch: "0",
  mode: "OBSERVE_ONLY",
  engine_running: true,
  feed_connected: false,
  journal_blocked: false,
  replay_paused: false,
  session_id: "synthetic",
  latest_data_timestamp_ns: null,
  storage_status: "JOURNALED",
  backpressure_state: "CLEAR",
  audit: [],
  approved_security_ids: [],
  no_trade_reasons: [
    "NO_ELIGIBLE_DHAN_FEED",
    "OBSERVATION_ONLY",
    "SESSION_CLASSIFICATION_UNVERIFIED",
  ],
  counters: { replayed_records: 1 },
  instruments: [
    {
      instrument: {
        key: "NSE:NSE_EQ:2885",
        venue: "NSE",
        exchange_segment: "NSE_EQ",
        security_id: "2885",
      },
      candles: [],
      market: null,
      features: null,
      depth: null,
      last_update_timestamp_ns: null,
      previous_close_nanos: null,
    },
  ],
} as unknown as WireEngineSnapshot;
const envelope = (
  message_type: Envelope["message_type"],
  sequence: string,
  payload: unknown,
): Envelope => ({
  schema_version: 1,
  message_type,
  payload,
  engine_boot_id: "boot-a",
  publication_sequence: sequence,
  publication_timestamp_ns: "1700000000000000000",
});

describe("telemetry recovery", () => {
  it("uses a retained HttpOnly session without prompting after browser reopening", async () => {
    const prompt = vi.spyOn(window, "prompt").mockReturnValue("unused");
    const fetchMock = vi.fn().mockResolvedValue({ status: 204, ok: true });
    vi.stubGlobal("fetch", fetchMock);
    class TestSocket {
      static created = 0;
      constructor() {
        TestSocket.created += 1;
      }
      close() {}
    }
    const socket = vi.spyOn(TestSocket.prototype, "close");
    vi.stubGlobal("WebSocket", TestSocket);
    const view = renderHook(() => useEngineStream());
    await waitFor(() => expect(TestSocket.created).toBe(1));
    expect(fetchMock).toHaveBeenCalledWith("/api/session", {
      credentials: "same-origin",
      cache: "no-store",
    });
    expect(prompt).not.toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    view.unmount();
    expect(socket).toHaveBeenCalledOnce();
    prompt.mockRestore();
    vi.unstubAllGlobals();
  });
  it("signs in automatically from a plain loopback visit", async () => {
    const prompt = vi.spyOn(window, "prompt").mockReturnValue("unused");
    let sessionChecks = 0;
    const fetchMock = vi.fn((url: string, options?: RequestInit) => Promise.resolve(
      options?.method === "POST" && url !== "/trade/session/local"
        ? { status: 400, ok: false }
        :
      url === "/api/session" && ++sessionChecks === 1
        ? { status: 401, ok: false }
        : { status: 204, ok: true },
    ));
    vi.stubGlobal("fetch", fetchMock);
    class TestSocket { static created = 0; constructor() { TestSocket.created++; } close() {} }
    vi.stubGlobal("WebSocket", TestSocket);
    const view = renderHook(() => useEngineStream());
    await waitFor(() => expect(TestSocket.created).toBe(1));
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/session", "/trade/session/local", "/api/session",
    ]);
    expect(fetchMock.mock.calls[1][1]).toMatchObject({
      method: "POST", headers: { "X-Alpha-Grid-Local": "1" },
    });
    expect(prompt).not.toHaveBeenCalled();
    view.unmount();
    prompt.mockRestore();
    vi.unstubAllGlobals();
  });
  it("exchanges a one-use launcher fragment and removes it before connecting", async () => {
    const token = "a".repeat(64);
    window.history.replaceState(null, "", `/#bootstrap=${token}`);
    const prompt = vi.spyOn(window, "prompt").mockReturnValue("unused");
    const fetchMock = vi.fn().mockResolvedValue({ status: 204, ok: true });
    vi.stubGlobal("fetch", fetchMock);
    class TestSocket {
      close() {}
    }
    vi.stubGlobal("WebSocket", TestSocket);
    const view = renderHook(() => useEngineStream());
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[0][0]).toBe("/trade/session/bootstrap");
    expect(fetchMock.mock.calls[0][1].body).toBe(JSON.stringify({ token }));
    expect(window.location.hash).toBe("");
    expect(prompt).not.toHaveBeenCalled();
    view.unmount();
    prompt.mockRestore();
    vi.unstubAllGlobals();
  });
  it("waits for one-use sign-in across StrictMode's repeated startup effect", async () => {
    const token = "c".repeat(64);
    window.history.replaceState(null, "", `/#bootstrap=${token}`);
    let finishBootstrap!: (response: { status: number; ok: boolean }) => void;
    const fetchMock = vi.fn((url: string) =>
      url === "/trade/session/bootstrap"
        ? new Promise<{ status: number; ok: boolean }>((resolve) => {
            finishBootstrap = resolve;
          })
        : Promise.resolve({ status: 204, ok: true }),
    );
    vi.stubGlobal("fetch", fetchMock);
    class TestSocket {
      close() {}
    }
    vi.stubGlobal("WebSocket", TestSocket);
    const view = renderHook(() => useEngineStream(), { wrapper: StrictMode });
    expect(
      fetchMock.mock.calls.filter(
        ([url]) => url === "/trade/session/bootstrap",
      ),
    ).toHaveLength(1);
    expect(
      fetchMock.mock.calls.filter(([url]) => url === "/api/session"),
    ).toHaveLength(0);
    finishBootstrap({ status: 204, ok: true });
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter(([url]) => url === "/api/session").length,
      ).toBeGreaterThan(0),
    );
    view.unmount();
    vi.unstubAllGlobals();
  });
  it("accepts a blank DATA_ONLY snapshot and a versioned approval update", () => {
    const blank = { ...snapshot, mode: "DATA_ONLY", paper: null };
    const first = applyEnvelope(
      initialState,
      envelope("engine.snapshot", "1", blank),
    );
    expect(first.snapshot?.no_trade_reasons).toContain("NO_ELIGIBLE_DHAN_FEED");
    const approved = applyEnvelope(
      first,
      envelope("engine.update", "2", {
        generation: "2",
        status: { approved_security_ids: ["1333"] },
        audit: [],
        instruments: [],
      }),
    );
    expect(approved.snapshot?.approved_security_ids).toEqual(["1333"]);
    const forged = {
      ...blank,
      instruments: [
        { ...snapshot.instruments[0], market: { price_nanos: "1" } },
      ],
    };
    expect(
      applyEnvelope(initialState, envelope("engine.snapshot", "1", forged))
        .snapshot,
    ).toBeNull();
  });
  it("rejects synthetic replay snapshots before they reach the cockpit", () => {
    const replay = { ...snapshot, mode: "REPLAY" };
    expect(
      applyEnvelope(initialState, envelope("engine.snapshot", "1", replay))
        .snapshot,
    ).toBeNull();
  });
  it("does not promote observation quotes to an unqualified feed mode", () => {
    const unqualified = { ...snapshot, mode: "DHAN_VERIFIED" };
    expect(
      applyEnvelope(initialState, envelope("engine.snapshot", "1", unqualified))
        .snapshot,
    ).toBeNull();
  });
  it("displays a stale Dhan-tagged quote only with its NO TRADE warning", () => {
    const stale = {
      ...snapshot,
      no_trade_reasons: [...snapshot.no_trade_reasons, "MARKET_DATA_STALE"],
      instruments: [
        {
          ...snapshot.instruments[0],
          market: {
            quality: { stale: true, source_time_unverified: false },
            provenance: { source_protocol: "DhanStandardV2" },
          },
        },
      ],
    };
    const observed = applyEnvelope(
      initialState,
      envelope("engine.snapshot", "1", stale),
    );
    expect(observed.snapshot?.instruments[0].market?.quality.stale).toBe(true);
    expect(observed.snapshot?.no_trade_reasons).toContain("MARKET_DATA_STALE");
  });
  it("shows source-time-uncertain quote values only with provenance and NO TRADE", () => {
    const quote = {
      ...snapshot,
      feed_connected: true,
      no_trade_reasons: [
        ...snapshot.no_trade_reasons,
        "SOURCE_TIME_UNVERIFIED",
        "SOURCE_SEQUENCE_UNVERIFIED",
      ],
      instruments: [
        {
          ...snapshot.instruments[0],
          market: {
            price_nanos: "100000000000",
            cumulative_traded_volume: "500",
            quality: { stale: false, source_time_unverified: true },
            provenance: { source_protocol: "DhanStandardV2" },
          },
        },
      ],
    };
    const observed = applyEnvelope(
      initialState,
      envelope("engine.snapshot", "1", quote),
    );
    expect(
      observed.snapshot?.instruments[0].market?.cumulative_traded_volume,
    ).toBe("500");
    expect(observed.snapshot?.no_trade_reasons).toContain(
      "SOURCE_TIME_UNVERIFIED",
    );
    expect(
      applyEnvelope(
        initialState,
        envelope("engine.snapshot", "1", {
          ...quote,
          no_trade_reasons: [],
        }),
      ).snapshot,
    ).toBeNull();
    expect(
      applyEnvelope(
        initialState,
        envelope("engine.snapshot", "1", {
          ...quote,
          paper: { positions: [] },
        }),
      ).snapshot,
    ).toBeNull();
    const unproven = {
      ...quote,
      instruments: [
        {
          ...quote.instruments[0],
          market: {
            ...quote.instruments[0].market,
            provenance: { source_protocol: "Unknown" },
          },
        },
      ],
    };
    expect(
      applyEnvelope(initialState, envelope("engine.snapshot", "1", unproven))
        .snapshot,
    ).toBeNull();
  });
  it("accepts a full snapshot then a contiguous instrument delta", () => {
    const first = applyEnvelope(
      initialState,
      envelope("engine.snapshot", "1", snapshot),
    );
    const updated = applyEnvelope(
      first,
      envelope("engine.update", "2", {
        generation: "2",
        status: { engine_running: true },
        audit: [],
        instruments: [
          { ...snapshot.instruments[0], previous_close_nanos: "99500000000" },
        ],
      }),
    );
    expect(updated.sequence).toBe("2");
    expect(updated.snapshot?.instruments[0].previous_close_nanos).toBe(
      "99500000000",
    );
    expect(updated.snapshot?.instruments).toHaveLength(1);
  });
  it("accepts the gateway's 25-name raw-capture snapshot", () => {
    const instruments = Array.from({ length: 25 }, (_, index) => ({
      ...snapshot.instruments[0],
      instrument: {
        ...snapshot.instruments[0].instrument,
        key: `NSE:NSE_EQ:${index + 1000}`,
        security_id: String(index + 1000),
        symbol: `STOCK${index}`,
      },
    }));
    const observed = applyEnvelope(initialState, envelope("engine.snapshot", "1", {
      ...snapshot, feed_connected: true, instruments,
    }));
    expect(observed.connection).toBe("CONNECTED");
    expect(observed.snapshot?.instruments).toHaveLength(25);
  });
  it("keeps a same-packet raw Full book visible without accepting mismatched provenance", () => {
    const hash = "a".repeat(64);
    const provenance = {
      source_protocol: "DhanStandardV2", feed_session_id: "feed-1",
      source_frame_id: "12", packet_offset: 0, raw_frame_sha256: hash,
    };
    const level = { price_nanos: "100000000000", displayed_quantity: "10", cumulative_visible_quantity: "10" };
    const instrument = {
      ...snapshot.instruments[0],
      market: {
        price_nanos: "100000000000", packet_kind: "RAW_FULL",
        source_receive_timestamp_ns: "1000", provenance,
        quality: { stale: false, source_time_unverified: false },
      },
      depth: {
        source: "STANDARD_FULL_RAW", receive_timestamp_ns: "1000", provenance,
        bids: Array.from({ length: 5 }, () => level),
        asks: Array.from({ length: 5 }, () => level),
      },
    };
    const raw = { ...snapshot, no_trade_reasons: [...snapshot.no_trade_reasons, "RAW_CAPTURE_VALIDATION_PENDING"], instruments: [instrument] };
    const accepted = applyEnvelope(initialState, envelope("engine.snapshot", "1", raw));
    expect(accepted.snapshot?.instruments[0].depth?.source).toBe("STANDARD_FULL_RAW");
    expect(applyEnvelope(initialState, envelope("engine.snapshot", "1", {
      ...raw, instruments: [{ ...instrument, depth: {
        ...instrument.depth, provenance: { ...provenance, raw_frame_sha256: "b".repeat(64) },
      } }],
    })).snapshot).toBeNull();
    expect(applyEnvelope(initialState, envelope("engine.snapshot", "1", {
      ...raw, no_trade_reasons: ["OBSERVATION_ONLY", "NO_ELIGIBLE_DHAN_FEED"],
    })).snapshot).toBeNull();
  });
  it("accepts an eligible Full book without a raw-capture warning", () => {
    const hash = "b".repeat(64);
    const provenance = {
      source_protocol: "DhanStandardV2", feed_session_id: "feed-2",
      source_frame_id: "13", packet_offset: 0, raw_frame_sha256: hash,
    };
    const level = { price_nanos: "100000000000", displayed_quantity: "10", cumulative_visible_quantity: "10" };
    const eligible = {
      ...snapshot,
      no_trade_reasons: ["OBSERVATION_ONLY", "NO_ELIGIBLE_DHAN_FEED"],
      instruments: [{
        ...snapshot.instruments[0],
        market: { packet_kind: "FULL", source_receive_timestamp_ns: "1000", provenance,
          quality: { stale: false, source_time_unverified: false } },
        depth: { source: "STANDARD_FULL", receive_timestamp_ns: "1000", provenance,
          bids: Array.from({ length: 5 }, () => level), asks: Array.from({ length: 5 }, () => level) },
      }],
    };
    expect(applyEnvelope(initialState, envelope("engine.snapshot", "1", eligible)).snapshot?.instruments[0].depth?.source).toBe("STANDARD_FULL");
  });
  it("accepts a 20-level depth snapshot with derived pairing", () => {
    const level = { price_nanos: "100000000000", displayed_quantity: "10", cumulative_visible_quantity: "10" };
    const depth20Snapshot = {
      ...snapshot,
      instruments: [{
        ...snapshot.instruments[0],
        depth: {
          source: "DEPTH_20",
          receive_timestamp_ns: "1793770000000000000",
          derived_pairing: true,
          atomic_exchange_snapshot_verified: false,
          bids: Array.from({ length: 20 }, () => level),
          asks: Array.from({ length: 20 }, () => level),
        },
      }],
    };
    const accepted = applyEnvelope(initialState, envelope("engine.snapshot", "1", depth20Snapshot));
    expect(accepted.snapshot?.instruments[0].depth?.source).toBe("DEPTH_20");
    expect(accepted.snapshot?.instruments[0].depth?.bids.length).toBe(20);
  });
  it("rejects a missed delta and accepts resynchronization after boot change", () => {
    const first = applyEnvelope(
      initialState,
      envelope("engine.snapshot", "1", snapshot),
    );
    expect(
      applyEnvelope(
        first,
        envelope("engine.update", "3", {
          generation: "3",
          status: {},
          audit: [],
          instruments: [],
        }),
      ),
    ).toBe(first);
    const restarted = applyEnvelope(first, {
      ...envelope("engine.snapshot", "1", { ...snapshot, replay_epoch: "1" }),
      engine_boot_id: "boot-b",
    });
    expect(restarted.bootId).toBe("boot-b");
    expect(restarted.snapshot?.replay_epoch).toBe("1");
  });
  it("preserves unknown separately from numeric zero", () => {
    expect(price(null)).toBe("N/A");
    expect(price("0")).toBe("₹0.00");
    expect(price("1234567890123")).toBe("₹1,234.56");
  });
});
