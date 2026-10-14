import { describe, expect, it } from "vitest";
import { applyEnvelope, initialState } from "./stream";
import { price } from "./format";
import type { Envelope } from "./generated/envelope";
import type { WireEngineSnapshot } from "./generated/engine-snapshot";

const snapshot = {
  generation: "1",
  replay_epoch: "0",
  mode: "REPLAY",
  engine_running: true,
  feed_connected: false,
  journal_blocked: false,
  replay_paused: false,
  session_id: "synthetic",
  latest_data_timestamp_ns: null,
  storage_status: "JOURNALED",
  backpressure_state: "CLEAR",
  audit: [],
  counters: { replayed_records: 1 },
  instruments: [
    {
      instrument: {
        key: "NSE:NSE_EQ:1333",
        venue: "NSE",
        exchange_segment: "NSE_EQ",
        security_id: "1333",
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
