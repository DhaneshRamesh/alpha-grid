import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import App from "./App";
import { TradeDesk, type HistoricalCache } from "./TradingWorkspace";
import type { WireEngineSnapshot } from "./generated/engine-snapshot";

const engineState = vi.hoisted(() => ({ snapshot: null as unknown }));
vi.mock("./stream", () => ({
  useEngineStream: () => ({
    snapshot: engineState.snapshot,
    connection: "DISCONNECTED",
    messageLagMs: null,
    reconnects: 0,
  }),
}));
vi.mock("./Chart", () => ({ default: () => <div data-testid="chart-observations" /> }));

beforeEach(() => {
  engineState.snapshot = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, options?: RequestInit) => ({
      ok: url !== "/trade/account",
      json: async () =>
        url === "/trade/status"
          ? {
              execution: "UNAVAILABLE",
              account: "NOT_CONNECTED",
              marketFeed: "NOT_CONNECTED",
              research: "NOT_CONNECTED",
              reason: "Live services are unavailable",
            }
          : options?.method === "PUT"
            ? { settings: JSON.parse(options.body as string) }
            : { settings: null },
    })),
  );
});

describe("trading workspace", () => {
  it("shows a genuine raw WebSocket price live without presenting it as a candle", () => {
    const received = String(Date.now() * 1_000_000);
    engineState.snapshot = { mode: "OBSERVE_ONLY", engine_running: true, feed_connected: true,
      storage_status: "JOURNALED", session_id: "fixture", counters: { fsync_latency: {} }, audit: [],
      no_trade_reasons: ["RAW_CAPTURE_VALIDATION_PENDING"], instruments: [{
        instrument: { key: "NSE_EQ:2885:RELIANCE", security_id: "2885", symbol: "RELIANCE", exchange_segment: "NSE_EQ" },
        candles: [], market: { packet_kind: "RAW_QUOTE", price_nanos: "100000000000",
          cumulative_traded_volume: "500", source_receive_timestamp_ns: received,
          source_last_trade_timestamp_ns: null, freshness: "FRESH", quality: { stale: false },
          provenance: { raw_frame_sha256: "a".repeat(64), source_frame_id: "1" } },
      }] };
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Candle workstation/ }));
    expect(screen.getByText(/LIVE PRICE OBSERVATIONS \/ NO TRADE/)).toBeTruthy();
    expect(screen.getByText(/RAW_CAPTURE_ONLY · NO LIVE BAR/)).toBeTruthy();
    expect(screen.getByText(/LAST OBSERVED LTP/)).toBeTruthy();
    expect(screen.getByTestId("chart-observations")).toBeTruthy();
  });
  it("separates live raw capture and REST quotes from unavailable candidates and account sync", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
      ok: url !== "/trade/account",
      status: url === "/trade/account" ? 503 : 200,
      json: async () => url === "/trade/status"
        ? { execution: "UNAVAILABLE", account: "READ_ONLY", marketFeed: "ONLINE_REQUESTED",
            research: "NOT_CONNECTED", reason: "Candidate output inactive" }
        : { settings: null },
    })));
    const rawSnapshot = { mode: "OBSERVE_ONLY", feed_connected: true, journal_blocked: false,
      no_trade_reasons: ["RAW_CAPTURE_VALIDATION_PENDING"], audit: [], instruments: [] } as unknown as WireEngineSnapshot;
    render(<TradeDesk onSettings={() => {}} engineConnection="CONNECTED" historical={null}
      engineSnapshot={rawSnapshot}
      restSnapshot={{ source: "REST_SNAPSHOT", received_at: new Date().toISOString(),
        sha256: "fixture-only", quotes: [{ symbol: "RELIANCE", security_id: "2885", last_price: 100 }] }} />);
    await waitFor(() => expect(screen.getByText(/DHAN ACCOUNT: LOCAL_ACCOUNT_HTTP_503/)).toBeTruthy());
    expect(screen.getByText(/RAW MARKET DATA AVAILABLE · SIMULATION PAUSED/)).toBeTruthy();
    expect(screen.getByText(/MARKET DATA · RAW_CAPTURE \+ REST_SNAPSHOT/)).toBeTruthy();
    expect(screen.getByText(/RELIANCE · 2885: ₹100/)).toBeTruthy();
    expect(screen.getByText(/NO VALIDATED CANDIDATES · NO TRADE/)).toBeTruthy();
  });
  it("shows ten focused stocks and same-packet Full depth as observations", async () => {
    const ids = ["2885", "1333", "4963", "1594", "11536", "10604", "3045", "5900", "11483", "5097"];
    engineState.snapshot = {
      mode: "OBSERVE_ONLY", engine_running: true, feed_connected: true,
      storage_status: "JOURNALED", session_id: "fixture", counters: { fsync_latency: {} },
      audit: [], no_trade_reasons: [],
      instruments: ids.map((id) => ({ instrument: { key: `NSE_EQ:${id}:FIXTURE${id}`, security_id: id,
        symbol: `FIXTURE${id}`, exchange_segment: "NSE_EQ" },
      market: id === "2885" ? { freshness: "FRESH", quality: { stale: false }, source_receive_timestamp_ns: "1793763000000000000",
        price_nanos: "100000000000", best_bid_price_nanos: "99000000000",
        best_ask_price_nanos: "101000000000", packet_kind: "FULL" } : null,
      depth: id === "2885" ? { source: "STANDARD_FULL", receive_timestamp_ns: "1793763000000000000",
        bids: Array.from({ length: 5 }, (_, i) => ({ price_nanos: String((99 - i) * 1e9),
          displayed_quantity: "100", order_count: 2, cumulative_visible_quantity: String((i + 1) * 100) })),
        asks: Array.from({ length: 5 }, (_, i) => ({ price_nanos: String((101 + i) * 1e9),
          displayed_quantity: "120", order_count: 3, cumulative_visible_quantity: String((i + 1) * 120) })),
        book_metrics: { quoted_spread_bps: 200, bid_quantity: "500", ask_quantity: "600",
          bid_orders: 10, ask_orders: 15, five_level_imbalance: -0.0909 } } : null })),
    };
    const rows = (engineState.snapshot as { instruments: Array<{ depth: Record<string, unknown> | null }> }).instruments;
    rows[1].depth = { ...rows[0].depth, source: "STANDARD_FULL" };
    rows[0].depth = { ...rows[0].depth, source: "STANDARD_FULL_RAW",
      provenance: { raw_frame_sha256: "a".repeat(64), raw_durable_at_ns: "1793763000000000001" } };
    (engineState.snapshot as { instruments: Array<{ market: unknown }> }).instruments[0].market = null;
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Command Center/ }));
    expect(screen.getByText("Ten-stock liquidity radar")).toBeTruthy();
    expect(screen.getAllByRole("row").length).toBeGreaterThanOrEqual(11);
    expect(screen.getAllByText(/STANDARD_FULL/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/500 \/ 600/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/RAW OBSERVATION · VALIDATION PENDING/).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: /Feature monitor/ }));
    expect(screen.getByText("Five-level displayed book")).toBeTruthy();
    expect(screen.getAllByText(/Descriptive only; no fill, signal or training eligibility/).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: /Market intelligence/ }));
    expect(screen.getAllByText(/RAW OBSERVATION \/ VALIDATION PENDING/).length).toBeGreaterThan(0);
  });
  it("shows archived replay abstentions without presenting a live quote or fill", async () => {
    // UI contract fixture only; these values are never market evidence.
    const historical: HistoricalCache = {
      schema_version: "dhan-historical-cache-v1",
      status: "AVAILABLE",
      source: "DHAN_HISTORICAL",
      response_sha256: "fixture-only",
      retrieved_at: "2026-10-16T16:00:00Z",
      coverage: { "2026-10-16": 1 },
      causal_eligibility: "BLOCKED",
      bars: [
        {
          timestamp_ns: "1792143840000000000",
          open_nanos: "100",
          high_nanos: "101",
          low_nanos: "99",
          close_nanos: "100",
          volume: "10",
        },
      ],
      replay: {
        mode: "HISTORICAL_REPLAY",
        status: "COMPLETE_NO_TRADE",
        source_sha256: "fixture-only",
        processed_bars: 1,
        total_bars: 1,
        latest_session: "2026-10-16",
        latest_source_timestamp_ns: "1792143840000000000",
        missing_source_minutes: 0,
        retrospective_baseline_samples: 0,
        latest_retrospective_baseline: null,
        baseline_eligibility: "DESCRIPTIVE_ONLY",
        outcomes: "WITHHELD_UNVERIFIED_COMPLETION",
        decision: "NO_TRADE",
        no_trade_evaluations: 1,
        reasons: ["CANDLE_COMPLETION_UNVERIFIED"],
        paper_eligible: 0,
        paper_orders: 0,
        paper_fills: 0,
        qp01_records_written: 0,
        broker_orders: "DISABLED",
      },
    };
    render(
      <TradeDesk
        onSettings={() => {}}
        engineSnapshot={null}
        engineConnection="DISCONNECTED"
        historical={historical}
      />,
    );
    expect(screen.getByText(/HISTORICAL REPLAY/)).toBeTruthy();
    expect(screen.getByText(/1 NO TRADE evaluations/)).toBeTruthy();
    expect(screen.getByText(/paper orders 0/)).toBeTruthy();
    expect(
      screen.getByText(/not live quotes or attainable fills/),
    ).toBeTruthy();
  });

  it("starts with unavailable Dhan data and no fictional candidate", async () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: "Command Center." })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Trade desk/ }));
    expect(screen.getByRole("heading", { name: "Trade desk." })).toBeTruthy();
    expect(screen.getAllByText(/DATA UNAVAILABLE/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/BUY 8 SHARES/)).toBeNull();
    expect(screen.queryByText(/sample price/)).toBeNull();
    expect(
      screen.getByText(/cockpit does not retry historical requests/),
    ).toBeTruthy();
    await waitFor(() =>
      expect(screen.getByText(/Live services are unavailable/)).toBeTruthy(),
    );
    expect(
      vi.mocked(fetch).mock.calls.some(([url]) => url === "/trade/account"),
    ).toBe(true);
  });

  it("keeps the separate local risk-settings tab", async () => {
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Trading settings/ }));
    expect(
      screen.getByRole("heading", { name: "Trading settings." }),
    ).toBeTruthy();
    for (const [label, value] of [
      ["Maximum per trade", "5000"],
      ["Maximum daily loss", "1000"],
      ["Stop-loss", "2"],
      ["Profit target", "4"],
    ]) {
      fireEvent.change(
        screen.getByLabelText(label, { exact: false, selector: "input" }),
        { target: { value } },
      );
    }
    fireEvent.click(screen.getByRole("button", { name: "Save limits" }));
    await waitFor(() => expect(screen.getByText(/Limits saved/)).toBeTruthy());
    const saved = vi
      .mocked(fetch)
      .mock.calls.find(
        ([url, options]) =>
          url === "/trade/settings" && options?.method === "PUT",
      );
    expect(saved).toBeTruthy();
    expect(JSON.parse(saved![1]!.body as string)).toMatchObject({
      maxTrade: { unit: "INR", value: "5000" },
      maxDailyLoss: { unit: "INR", value: "1000" },
      stopLoss: { unit: "PERCENT", value: "2" },
      profitTarget: { unit: "PERCENT", value: "4" },
    });
  });

  it("shows blocked online data without a connected Rust feed", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => ({
        ok: url !== "/trade/account",
        json: async () =>
          url === "/trade/status"
            ? {
                execution: "UNAVAILABLE",
                account: "READ_ONLY",
                marketFeed: "ONLINE_REQUESTED",
                research: "NOT_CONNECTED",
                reason: "Feed preflight blocked",
              }
            : { settings: null },
      })),
    );
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Trade desk/ }));
    await waitFor(() =>
      expect(screen.getByText(/MARKET DATA.*BLOCKED/)).toBeTruthy(),
    );
    expect(screen.getAllByText(/NO TRADE/).length).toBeGreaterThan(0);
  });

  it("labels verified offline account cache and independent failures accurately", async () => {
    const recent = new Date().toISOString();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => ({
        ok: true,
        json: async () =>
          url === "/trade/status"
            ? {
                execution: "UNAVAILABLE",
                account: "READ_ONLY",
                marketFeed: "CACHE_ONLY",
                research: "NOT_CONNECTED",
                reason: "Offline",
              }
            : url === "/trade/account"
              ? {
                  status: "CACHE_ONLY",
                  panels: {
                    fundlimit: {
                      endpoint: "fundlimit",
                      status: "AVAILABLE",
                      delivery: "CACHE_ONLY",
                      retrieved_at: recent,
                      last_http_status: 200,
                      data: { available_balance_inr: "123.45" },
                    },
                    holdings: {
                      endpoint: "holdings",
                      status: "DATA_UNAVAILABLE",
                      last_http_status: 500,
                    },
                    positions: {
                      endpoint: "positions",
                      status: "DATA_UNAVAILABLE",
                      reason: "CACHE_UNVERIFIABLE",
                    },
                  },
                }
              : { settings: null },
      })),
    );
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Trade desk/ }));
    await waitFor(() =>
      expect(
        screen.getByText(/DHAN ACCOUNT: VERIFIED LOCAL CACHE/),
      ).toBeTruthy(),
    );
    expect(screen.getByText("DESK")).toBeTruthy();
    expect(screen.getByText(/MARKET DATA.*CACHE_ONLY/)).toBeTruthy();
    expect(
      screen.getByText(
        /funds CACHE_ONLY; holdings DATA UNAVAILABLE \(last HTTP 500\); positions UNVERIFIABLE/,
      ),
    ).toBeTruthy();
  });
});
