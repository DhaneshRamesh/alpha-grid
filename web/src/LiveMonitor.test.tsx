import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import LiveMonitor from "./LiveMonitor";
import type { WireEngineSnapshot } from "./generated/engine-snapshot";
import type { HistoricalCache, RestSnapshot } from "./TradingWorkspace";

vi.mock("./Chart", () => ({ default: () => <div data-testid="chart-observations" /> }));

describe("LiveMonitor", () => {
  const mockSnapshot: WireEngineSnapshot = {
    schema_version: 1,
    engine_running: true,
    feed_connected: true,
    storage_status: "JOURNALED",
    journal_blocked: false,
    session_id: "test-session-1",
    generation: "1",
    backpressure_state: "CLEAR",
    mode: "OBSERVE_ONLY",
    latest_data_timestamp_ns: "1793779693812892000",
    counters: {
      frames_received: 245000,
      packets_decoded: 245000,
      fsync_latency: { sample_count: 0 },
      decode_latency: { sample_count: 0 },
      feature_latency: { sample_count: 0 },
      processing_latency: { sample_count: 0 },
      telemetry_publication_latency: { sample_count: 0 },
      connected_clients: 1,
      durably_committed_records: 245000,
      journal_records_accepted: 245000,
      malformed_frames: 0,
      queue_current: 0,
      queue_high_water: 0,
      queue_rejections: 0,
      reconnects: 0,
      replayed_records: 0,
      telemetry_coalesced_updates: 0,
      telemetry_skipped_updates: 0,
    },
    audit: [],
    approved_security_ids: ["2885"],
    replay_epoch: "0",
    replay_paused: false,
    no_trade_reasons: ["OBSERVATION_ONLY"],
    instruments: [
      {
        instrument: {
          key: "NSE_EQ:2885:RELIANCE",
          security_id: "2885",
          symbol: "RELIANCE",
          exchange_segment: "NSE_EQ",
          venue: "NSE",
        },
        candles: [],
        market: {
          freshness: "FRESH",
          packet_kind: "QUOTE",
          quality: {
            stale: false,
            source_time_unverified: false,
            book_pairing_unverified: false,
            cross_feed_uncertain: false,
            inconsistent_last_trade_quantity: false,
            volume_interval_incomplete: false,
          },
          source_receive_timestamp_ns: `${Date.now() * 1_000_000}`,
          price_nanos: "2885500000000",
          best_bid_price_nanos: "2885000000000",
          best_ask_price_nanos: "2886000000000",
          provenance: {
            source_protocol: "DhanStandardV2",
            source_frame_id: "12845",
            raw_frame_sha256: "d1974a10450b85555fc83ce33d8b5191eddfd011a77ffbff78d82a3d7e7badec",
            raw_durable_at_ns: `${Date.now() * 1_000_000 + 100}`,
            packet_offset: 48,
            feed_session_id: "test-session-1",
          },
        },
        depth: {
          receive_timestamp_ns: `${Date.now() * 1_000_000}`,
          source: "STANDARD_FULL",
          derived_pairing: false,
          atomic_exchange_snapshot_verified: true,
          bids: [
            { price_nanos: "2885000000000", displayed_quantity: "500", cumulative_visible_quantity: "500", order_count: 5 },
            { price_nanos: "2884500000000", displayed_quantity: "1200", cumulative_visible_quantity: "1700", order_count: 12 },
          ],
          asks: [
            { price_nanos: "2886000000000", displayed_quantity: "800", cumulative_visible_quantity: "800", order_count: 8 },
            { price_nanos: "2886500000000", displayed_quantity: "1500", cumulative_visible_quantity: "2300", order_count: 15 },
          ],
          book_metrics: {
            quoted_spread_bps: 3.46,
            five_level_imbalance: 0.12,
            bid_quantity: "1700",
            ask_quantity: "2300",
            bid_orders: 17,
            ask_orders: 23,
          },
        },
      },
    ],
  };

  const mockHistorical: HistoricalCache = {
    schema_version: "dhan-historical-cache-v1",
    status: "AVAILABLE",
    source: "DHAN_HISTORICAL",
    instrument: "NSE_EQ:2885:RELIANCE",
    response_sha256: "bcace97b4dfd1b1cbfe10481b4718b770368a50c5264fb1f61a50f9c325d9861",
    retrieved_at: "2026-11-01T19:49:00Z",
    causal_eligibility: "BLOCKED",
    bars: [
      {
        timestamp_ns: "1793779200000000000",
        open_nanos: "2880000000000",
        high_nanos: "2890000000000",
        low_nanos: "2875000000000",
        close_nanos: "2885000000000",
        volume: "15000",
      },
    ],
  };

  const mockRestSnapshot: RestSnapshot = {
    source: "REST_SNAPSHOT",
    received_at: "2026-11-09T01:30:00Z",
    sha256: "aabbccddeeff00112233445566778899aabbccddeeff00112233445566778899",
    snapshot_state: "CURRENT_RECEIPT",
    quotes: [
      {
        symbol: "RELIANCE",
        security_id: "2885",
        last_price: 2885.5,
        volume: 45000,
        ohlc: { open: 2880.0, high: 2892.0, low: 2878.0, close: 2885.5 },
        depth: {
          buy: [{ price: 2885.0, quantity: 450, orders: 4 }],
          sell: [{ price: 2886.0, quantity: 720, orders: 7 }],
        },
      },
    ],
  };

  const mockReplayDecisions = {
    schema_version: "alpha-grid-replay-decisions-v1",
    total: 3,
    limit: 50,
    symbol: "RELIANCE",
    decisions: [
      {
        sequence: 1,
        symbol: "RELIANCE",
        date: "2025-10-29",
        time: "10:00:00",
        p_fav: 0.084,
        p_adv: 0.352,
        action: "NO_TRADE",
        reason: "MODEL_STAGE3_REJECTED: Predeclared economic acceptance criteria unmet",
        reference_price: "2885.50",
        target_shares: 0,
      },
      {
        sequence: 2,
        symbol: "RELIANCE",
        date: "2025-10-29",
        time: "10:15:00",
        p_fav: 0.052,
        p_adv: 0.421,
        action: "NO_TRADE",
        reason: "MODEL_STAGE3_REJECTED: Predeclared economic acceptance criteria unmet",
        reference_price: "2882.10",
        target_shares: 0,
      },
    ],
  };

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/trade/simulation/decisions")) {
          return {
            ok: true,
            status: 200,
            json: async () => mockReplayDecisions,
          };
        }
        if (url.includes("/trade/historical/")) {
          return {
            ok: true,
            status: 200,
            json: async () => mockHistorical,
          };
        }
        return { ok: false, status: 404, json: async () => ({}) };
      }),
    );
  });

  it("1. exhibits strict source isolation: LIVE_WS, HISTORICAL_ARCHIVE, REST_SNAPSHOT", async () => {
    render(
      <LiveMonitor
        snapshot={mockSnapshot}
        connection="CONNECTED"
        historical={mockHistorical}
        restSnapshot={mockRestSnapshot}
        selectedInstrumentKey="NSE_EQ:2885:RELIANCE"
        onSelectInstrument={vi.fn()}
        feedState="LIVE"
      />,
    );

    // Initial default is HISTORICAL_ARCHIVE
    expect(screen.getByText("SOURCE: HISTORICAL_ARCHIVE")).toBeTruthy();
    expect(
      screen.getByText(/Strict Source Isolation: Live WS observations, Historical Archive bars, and REST snapshots are never blended/),
    ).toBeTruthy();

    // Switch to LIVE_WS
    const liveWsBtn = screen.getByRole("button", { name: /LIVE_WS/ });
    fireEvent.click(liveWsBtn);
    expect(screen.getByText("SOURCE: LIVE_WS")).toBeTruthy();

    // Switch to REST_SNAPSHOT
    const restBtn = screen.getByRole("button", { name: /REST_SNAPSHOT/ });
    fireEvent.click(restBtn);
    expect(screen.getByText("SOURCE: REST_SNAPSHOT")).toBeTruthy();
    expect(screen.getByText(/REST Quote received:/)).toBeTruthy();
  });

  it("2. advances compact observer avatar only on real durable events", async () => {
    const { rerender } = render(
      <LiveMonitor
        snapshot={mockSnapshot}
        connection="CONNECTED"
        historical={mockHistorical}
        restSnapshot={mockRestSnapshot}
        selectedInstrumentKey="NSE_EQ:2885:RELIANCE"
        onSelectInstrument={vi.fn()}
        feedState="LIVE"
      />,
    );

    await waitFor(() => {
      expect(screen.getByText(/Reading Standard Full five-level book/)).toBeTruthy();
      expect(screen.getByText("📚")).toBeTruthy();
    });

    const counterText = screen.getByText(/Observed Events:/).textContent;
    expect(counterText).toContain("1");

    // Rerender with identical timestamp => advance count should NOT advance
    rerender(
      <LiveMonitor
        snapshot={mockSnapshot}
        connection="CONNECTED"
        historical={mockHistorical}
        restSnapshot={mockRestSnapshot}
        selectedInstrumentKey="NSE_EQ:2885:RELIANCE"
        onSelectInstrument={vi.fn()}
        feedState="LIVE"
      />,
    );

    expect(screen.getByText(/Observed Events:/).textContent).toBe(counterText);

    // Rerender with new genuine timestamp => advance count increments
    const advancedSnapshot: WireEngineSnapshot = {
      ...mockSnapshot,
      instruments: [
        {
          ...mockSnapshot.instruments[0],
          market: {
            ...mockSnapshot.instruments[0].market!,
            source_receive_timestamp_ns: `${Date.now() * 1_000_000 + 500_000_000}`,
            provenance: {
              ...mockSnapshot.instruments[0].market!.provenance,
              source_frame_id: "12846",
            },
          },
        },
      ],
    };

    rerender(
      <LiveMonitor
        snapshot={advancedSnapshot}
        connection="CONNECTED"
        historical={mockHistorical}
        restSnapshot={mockRestSnapshot}
        selectedInstrumentKey="NSE_EQ:2885:RELIANCE"
        onSelectInstrument={vi.fn()}
        feedState="LIVE"
      />,
    );

    expect(screen.getByText(/Observed Events:/).textContent).toContain("2");
  });

  it("3. renders 5-step durable evidence pipeline with real timestamps and hashes", async () => {
    render(
      <LiveMonitor
        snapshot={mockSnapshot}
        connection="CONNECTED"
        historical={mockHistorical}
        restSnapshot={mockRestSnapshot}
        selectedInstrumentKey="NSE_EQ:2885:RELIANCE"
        onSelectInstrument={vi.fn()}
        feedState="LIVE"
      />,
    );

    expect(screen.getByText("1. FRAME RECEIVED")).toBeTruthy();
    expect(screen.getByText("2. RAW PERSISTED")).toBeTruthy();
    expect(screen.getByText("3. DECODED")).toBeTruthy();
    expect(screen.getByText("4. FRESHNESS CHECK")).toBeTruthy();
    expect(screen.getByText("5. SIMULATOR DECISION")).toBeTruthy();

    expect(screen.getByText(/DhanStandardV2 · Frame #12845/)).toBeTruthy();
    expect(screen.getAllByText(/d1974a10450b/).length).toBeGreaterThan(0);
    expect(screen.getByText(/PASSED \(FRESH\)/)).toBeTruthy();
    expect(screen.getByText(/Virtual Ref \(Never a Fill\)/)).toBeTruthy();
  });

  it("4. displays observed bid/ask, quoted spread, five-level depth, and freshness age", async () => {
    render(
      <LiveMonitor
        snapshot={mockSnapshot}
        connection="CONNECTED"
        historical={mockHistorical}
        restSnapshot={mockRestSnapshot}
        selectedInstrumentKey="NSE_EQ:2885:RELIANCE"
        onSelectInstrument={vi.fn()}
        feedState="LIVE"
      />,
    );

    // Headline metrics
    expect(screen.getAllByText(/₹2,885.00/).length).toBeGreaterThan(0); // Bid
    expect(screen.getAllByText(/₹2,886.00/).length).toBeGreaterThan(0); // Ask
    expect(screen.getByText(/3.5 bps/)).toBeTruthy(); // Spread

    // 5-level depth levels
    expect(screen.getByText("500")).toBeTruthy();
    expect(screen.getByText("800")).toBeTruthy();
    expect(screen.getByText("1,200")).toBeTruthy();
    expect(screen.getByText("1,500")).toBeTruthy();

    // Provenance hash and receipt
    expect(screen.getAllByText(/d1974a10450b8555/).length).toBeGreaterThan(0);
  });

  it("5. renders scrollable decision timeline with explicit reasons and virtual reference warning", async () => {
    render(
      <LiveMonitor
        snapshot={mockSnapshot}
        connection="CONNECTED"
        historical={mockHistorical}
        restSnapshot={mockRestSnapshot}
        selectedInstrumentKey="NSE_EQ:2885:RELIANCE"
        onSelectInstrument={vi.fn()}
        feedState="LIVE"
      />,
    );

    await waitFor(() => {
      expect(screen.getAllByText(/#0001/).length).toBeGreaterThan(0);
      expect(screen.getAllByText(/MODEL_STAGE3_REJECTED/).length).toBeGreaterThan(0);
      expect(screen.getAllByText(/VIRTUAL REFERENCE · NEVER A FILL/).length).toBeGreaterThan(0);
      expect(screen.getAllByText(/0 shares \(₹0 deployed\)/).length).toBeGreaterThan(0);
    });
  });

  it("6. handles offline / stale-feed display without inventing events and supports replay stepping", async () => {
    const { container } = render(
      <LiveMonitor
        snapshot={{ ...mockSnapshot, feed_connected: false }}
        connection="DISCONNECTED"
        historical={mockHistorical}
        restSnapshot={mockRestSnapshot}
        selectedInstrumentKey="NSE_EQ:2885:RELIANCE"
        onSelectInstrument={vi.fn()}
        feedState="MARKET CLOSED"
      />,
    );

    // Shows market closed banner
    expect(
      screen.getByText(/🛑 FEED UNAVAILABLE \/ MARKET CLOSED · Replay & offline records active/),
    ).toBeTruthy();

    await waitFor(() => {
      expect(container.querySelector(".stepper-count")?.textContent).toContain("Slot 1 / 2");
    });

    // Step forward
    const nextBtn = screen.getByRole("button", { name: /▶ Next/ });
    fireEvent.click(nextBtn);

    expect(container.querySelector(".stepper-count")?.textContent).toContain("Slot 2 / 2");
    expect(screen.getAllByText(/10:15:00 IST/).length).toBeGreaterThan(0);
  });

  it("7. keeps listeners backend-owned with zero extra WebSocket creations", () => {
    const wsSpy = vi.fn();
    vi.stubGlobal("WebSocket", wsSpy);

    render(
      <LiveMonitor
        snapshot={mockSnapshot}
        connection="CONNECTED"
        historical={mockHistorical}
        restSnapshot={mockRestSnapshot}
        selectedInstrumentKey="NSE_EQ:2885:RELIANCE"
        onSelectInstrument={vi.fn()}
        feedState="LIVE"
      />,
    );

    expect(wsSpy).not.toHaveBeenCalled();
  });

  it("8. displays appropriate observer bubbles across different engine states", async () => {
    // A: Lookback unavailable
    const lookbackSnapshot: WireEngineSnapshot = {
      ...mockSnapshot,
      no_trade_reasons: ["PAST_LOOKBACK_UNAVAILABLE"],
      instruments: [
        {
          ...mockSnapshot.instruments[0],
          depth: undefined,
        },
      ],
    };

    const { rerender } = render(
      <LiveMonitor
        snapshot={lookbackSnapshot}
        connection="CONNECTED"
        historical={mockHistorical}
        restSnapshot={mockRestSnapshot}
        selectedInstrumentKey="NSE_EQ:2885:RELIANCE"
        onSelectInstrument={vi.fn()}
        feedState="LIVE"
      />,
    );

    expect(screen.getByText("Lookback unavailable")).toBeTruthy();
    expect(screen.getByText("⏳")).toBeTruthy();

    // B: Computing volatility
    const volSnapshot: WireEngineSnapshot = {
      ...mockSnapshot,
      no_trade_reasons: [],
      instruments: [
        {
          ...mockSnapshot.instruments[0],
          depth: undefined,
          features: {
            processing_timestamp_ns: `${Date.now() * 1_000_000}`,
            rolling_return_volatility: 0.0018,
            rolling_sample_count: 900,
            freshness: "FRESH",
            quality: {
              stale: false,
              source_time_unverified: false,
              book_pairing_unverified: false,
              cross_feed_uncertain: false,
              inconsistent_last_trade_quantity: false,
              volume_interval_incomplete: false,
            },
            provenance: mockSnapshot.instruments[0].market!.provenance,
          },
        },
      ],
    };

    rerender(
      <LiveMonitor
        snapshot={volSnapshot}
        connection="CONNECTED"
        historical={mockHistorical}
        restSnapshot={mockRestSnapshot}
        selectedInstrumentKey="NSE_EQ:2885:RELIANCE"
        onSelectInstrument={vi.fn()}
        feedState="LIVE"
      />,
    );

    expect(screen.getByText("Computing trailing volatility")).toBeTruthy();
    expect(screen.getByText("📊")).toBeTruthy();
  });
});
