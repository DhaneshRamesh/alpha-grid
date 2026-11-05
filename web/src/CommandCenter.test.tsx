import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import CommandCenter, {
  ActiveMissions,
  CAS_ELIGIBILITY_MAP,
  FOCUS,
  getInstrumentSessionStatus,
  getNseSessionStatus,
  priceChangePct,
  Sparkline,
  type LivePortfolio,
} from "./CommandCenter";
import type { WireEngineSnapshot } from "./generated/engine-snapshot";
import type { RestSnapshot } from "./TradingWorkspace";

describe("Gamified Command Center (Phase 1)", () => {
  const mockSnapshot = {
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
    },
    audit: [
      {
        kind: "FEED_LIFECYCLE",
        detail: "PREFLIGHT_PASSED",
        timestamp_ns: "1793779690000000000",
        instrument_key: "NSE:NSE_EQ:2885",
      },
      {
        kind: "FEED_LIFECYCLE",
        detail: "CONNECTED_TO_DHAN",
        timestamp_ns: "1793779691000000000",
      },
    ],
    no_trade_reasons: ["RAW_CAPTURE_VALIDATION_PENDING"],
    instruments: FOCUS.map((id, index) => ({
      instrument: {
        key: `NSE_EQ:${id}:SYM${id}`,
        security_id: id,
        symbol: id === "2885" ? "RELIANCE" : id === "1333" ? "HDFCBANK" : `SYM${id}`,
        exchange_segment: "NSE_EQ",
      },
      candles: [],
      market: {
        freshness: "FRESH",
        quality: { stale: false },
        source_receive_timestamp_ns: `${Date.now() * 1_000_000}`,
        price_nanos: String((1000 + index * 50) * 1e9),
        best_bid_price_nanos: String((999 + index * 50) * 1e9),
        best_ask_price_nanos: String((1001 + index * 50) * 1e9),
        packet_kind: "FULL",
        cumulative_traded_volume: String(10000 + index * 500),
      },
      previous_close_nanos: String((995 + index * 50) * 1e9),
      depth: {
        source: "STANDARD_FULL",
        receive_timestamp_ns: `${Date.now() * 1_000_000}`,
        bids: [
          { price_nanos: String((999 + index * 50) * 1e9), displayed_quantity: "250", order_count: 5 },
          { price_nanos: String((998 + index * 50) * 1e9), displayed_quantity: "500", order_count: 8 },
        ],
        asks: [
          { price_nanos: String((1001 + index * 50) * 1e9), displayed_quantity: "180", order_count: 4 },
          { price_nanos: String((1002 + index * 50) * 1e9), displayed_quantity: "320", order_count: 6 },
        ],
        book_metrics: {
          quoted_spread_bps: 2.0 + index * 0.5,
          bid_quantity: "750",
          ask_quantity: "500",
          five_level_imbalance: 0.2,
          bid_orders: 13,
          ask_orders: 10,
        },
      },
    })),
  } as unknown as WireEngineSnapshot;

  const mockRestSnapshot: RestSnapshot = {
    source: "REST_SNAPSHOT",
    received_at: "2026-11-04T13:30:00Z",
    sha256: "test-hash",
    quotes: FOCUS.map((id) => ({
      security_id: id,
      symbol: id === "2885" ? "RELIANCE" : id === "1333" ? "HDFCBANK" : `SYM${id}`,
      last_price: 1000,
      net_change: 5,
      volume: 50000,
    })),
  };

  const mockPortfolio: LivePortfolio = {
    schema_version: "alpha-grid-live-portfolio-simulation-v2",
    state: "SIMULATION_PAUSED_VALIDATION_PENDING",
    feed_status: "STALE",
    capital_start_inr: "20000",
    cash_inr: "20000",
    equity_inr: "20000",
    max_drawdown_inr: "0",
    remaining_deployable_inr: "15000",
    remaining_position_slots: 3,
    open_risk_bound_inr: "0",
    risk_limits: {
      max_session_loss_inr: "500",
      max_deployed_inr: "15000",
      max_instrument_inr: "5000",
      max_spread_bps: "25",
    },
    positions: [],
    decisions: [
      {
        kind: "GAP",
        reason: "RESEARCH_VALIDATION_PENDING",
        sequence: 1,
        observed_at_ns: Date.now() * 1_000_000,
      },
    ],
    rejected_count: 0,
    ledger_events: 1,
  };

  it("renders the Global Session HUD with authoritative backend metrics", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({
      ok: true,
      json: async () => mockPortfolio,
    })));

    render(
      <CommandCenter
        snapshot={mockSnapshot}
        restSnapshot={mockRestSnapshot}
        feedState="LIVE CAPTURE"
        standardSockets={1}
        depthSockets={1}
        onInspect={() => {}}
        onSimulator={() => {}}
      />
    );

    expect(screen.getByText("NSE SESSION")).toBeTruthy();
    expect(screen.getByText(/1 standard \/ 1 depth socket/)).toBeTruthy();
    expect(screen.getByText("VIRTUAL CAPITAL")).toBeTruthy();
    expect(screen.getAllByText("₹20,000.00").length).toBeGreaterThan(0);
    expect(screen.getByText("ACTIVE POSITIONS")).toBeTruthy();
    expect(screen.getByText("0 / 3")).toBeTruthy();
    expect(screen.getByText("DECISION WORKER")).toBeTruthy();
    expect(screen.getByText(/DATA_ONLY · SIMULATION_ONLY · NO REAL ORDERS/)).toBeTruthy();
  });

  it("populates the 10 focus stocks with genuine backend fields and sparklines", () => {
    render(
      <CommandCenter
        snapshot={mockSnapshot}
        restSnapshot={mockRestSnapshot}
        feedState="LIVE CAPTURE"
        standardSockets={1}
        depthSockets={1}
        onInspect={() => {}}
        onSimulator={() => {}}
      />
    );

    expect(screen.getAllByText("RELIANCE").length).toBeGreaterThan(0);
    expect(screen.getAllByText("HDFCBANK").length).toBeGreaterThan(0);
    // 10 focus stocks rendered in arena
    const stockButtons = screen.getAllByRole("button").filter((b) => b.classList?.contains("stock-card"));
    expect(stockButtons.length).toBe(10);

    // Verify bid/ask is rendered
    expect(screen.getAllByText(/BID \/ ASK/).length).toBeGreaterThan(0);
    // Verify spread is rendered
    expect(screen.getAllByText(/SPREAD/).length).toBeGreaterThan(0);
  });

  it("renders the 2D Market Radar and handles stock selection", () => {
    const inspectMock = vi.fn();
    render(
      <CommandCenter
        snapshot={mockSnapshot}
        restSnapshot={mockRestSnapshot}
        feedState="LIVE CAPTURE"
        standardSockets={1}
        depthSockets={1}
        onInspect={inspectMock}
        onSimulator={() => {}}
      />
    );

    expect(screen.getByLabelText("Interactive 2D Market Radar")).toBeTruthy();
    expect(screen.getByText("Liquidity vs. Relative Movement")).toBeTruthy();

    // Click stock card to open detail drawer
    const relianceCard = screen.getAllByRole("button").find((b) => b.textContent?.includes("RELIANCE"));
    expect(relianceCard).toBeTruthy();
    fireEvent.click(relianceCard!);

    // Detail drawer appears
    expect(screen.getByText(/TACTICAL DETAIL · NSE_EQ/)).toBeTruthy();
    expect(screen.getByText(/Five-Level Displayed Book/)).toBeTruthy();
    expect(screen.getByText(/Microstructure/)).toBeTruthy();
  });

  it("displays Active Missions without false celebration when zero positions are open", () => {
    const { unmount } = render(
      <CommandCenter
        snapshot={mockSnapshot}
        restSnapshot={mockRestSnapshot}
        feedState="LIVE CAPTURE"
        standardSockets={1}
        depthSockets={1}
        onInspect={() => {}}
        onSimulator={() => {}}
      />
    );

    expect(screen.getByLabelText("Active Missions")).toBeTruthy();
    // Because mockSnapshot has RAW_CAPTURE_VALIDATION_PENDING, zero positions must be shown as validation pending
    expect(screen.getByText("Zero Virtual Positions · Decisions Paused")).toBeTruthy();
    expect(screen.getByText(/regulatory and integrity constraints, not an active market timing success/)).toBeTruthy();
    unmount();

    // Verify unblocked 100% cash allocation state when not pending
    render(
      <ActiveMissions positions={[]} onSimulator={() => {}} rawPending={false} connected={true} />
    );
    expect(screen.getByText("Zero Virtual Positions · 100% Cash Allocation")).toBeTruthy();
    expect(screen.getByText(/No candidate instruments currently meet virtual entry rules/)).toBeTruthy();
  });

  it("displays Active Missions when virtual positions exist in portfolio", async () => {
    const portfolioWithPositions: LivePortfolio = {
      ...mockPortfolio,
      positions: [
        {
          symbol: "RELIANCE",
          remaining_shares: 10,
          ask_reference_inr: "1300",
          risk_bound_inr: "150",
          decision_slot: "13:45 IST",
        },
      ],
    };

    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({
      ok: true,
      json: async () => portfolioWithPositions,
    })));

    render(
      <CommandCenter
        snapshot={mockSnapshot}
        restSnapshot={mockRestSnapshot}
        feedState="LIVE CAPTURE"
        standardSockets={1}
        depthSockets={1}
        onInspect={() => {}}
        onSimulator={() => {}}
      />
    );

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /Open Positions \(1 \/ 3\)/ })).toBeTruthy();
      expect(screen.getByText("HOLDING · 10 shares")).toBeTruthy();
      expect(screen.getByText("Ask Ref Entry")).toBeTruthy();
      expect(screen.getAllByText("₹1,300.00").length).toBeGreaterThan(0);
    });
  });

  it("renders Risk Shield with session loss budget and guardrail status", () => {
    render(
      <CommandCenter
        snapshot={mockSnapshot}
        restSnapshot={mockRestSnapshot}
        feedState="LIVE CAPTURE"
        standardSockets={1}
        depthSockets={1}
        onInspect={() => {}}
        onSimulator={() => {}}
      />
    );

    expect(screen.getByLabelText("Risk Shield")).toBeTruthy();
    expect(screen.getByText("Shared Risk Envelopes")).toBeTruthy();
    expect(screen.getByText("Session Loss Budget")).toBeTruthy();
    expect(screen.getByText("Capital Deployed")).toBeTruthy();
    expect(screen.getByText("MAX PER INSTRUMENT")).toBeTruthy();
    expect(screen.getByText("MAX SPREAD TOLERANCE")).toBeTruthy();
    expect(screen.getByText("✓ Circuit Bounds Verified")).toBeTruthy();
    expect(screen.getByText("🔒 Broker Execution Blocked")).toBeTruthy();
  });

  it("renders Opportunity Leaderboard distinguishing candidate statuses", () => {
    render(
      <CommandCenter
        snapshot={mockSnapshot}
        restSnapshot={mockRestSnapshot}
        feedState="LIVE CAPTURE"
        standardSockets={1}
        depthSockets={1}
        onInspect={() => {}}
        onSimulator={() => {}}
      />
    );

    expect(screen.getByLabelText("Opportunity Leaderboard")).toBeTruthy();
    expect(screen.getByText("Engine Candidate Status")).toBeTruthy();
    expect(screen.getAllByText(/WATCHING · VALIDATION PENDING/).length).toBeGreaterThan(0);
  });

  it("renders Decision Timeline with filter tabs and ordered events", () => {
    render(
      <CommandCenter
        snapshot={mockSnapshot}
        restSnapshot={mockRestSnapshot}
        feedState="LIVE CAPTURE"
        standardSockets={1}
        depthSockets={1}
        onInspect={() => {}}
        onSimulator={() => {}}
      />
    );

    expect(screen.getByLabelText("Decision Timeline")).toBeTruthy();
    expect(screen.getByText("Event Timeline")).toBeTruthy();
    expect(screen.getByRole("button", { name: "All" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Decisions" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Feed" })).toBeTruthy();

    // Verify events from snapshot.audit appear
    expect(screen.getByText("PREFLIGHT_PASSED · NSE:NSE_EQ:2885")).toBeTruthy();
  });

  it("calculates price change percentage correctly and handles missing data", () => {
    // With nanos
    const pct = priceChangePct("101000000000", "100000000000");
    expect(pct).toBeCloseTo(1.0, 2);

    // Negative change
    const negPct = priceChangePct("99000000000", "100000000000");
    expect(negPct).toBeCloseTo(-1.0, 2);

    // Missing data returns null (no false coordinates)
    expect(priceChangePct(null, null)).toBeNull();
  });

  it("Sparkline renders SVG path when data exists and dashed line when empty", () => {
    const { container: emptyContainer } = render(<Sparkline points={[]} />);
    expect(emptyContainer.querySelector("line")).toBeTruthy();

    const { container: fullContainer } = render(<Sparkline points={[100, 102, 101, 105]} />);
    expect(fullContainer.querySelector("path")).toBeTruthy();
  });

  it("determines NSE session status based on IST clock and August 2026 CAS treatment", () => {
    // Current time
    const session = getNseSessionStatus();
    expect(["OPEN", "CAS", "PRE_OPEN", "CLOSED"]).toContain(session.status);
    expect(session.label).toMatch(/^NSE /);

    // Wednesday 2026-11-04 10:30:00 IST (05:00 UTC) -> Continuous trading
    const continuousDate = new Date("2026-11-04T05:00:00.000Z");
    const continuousSession = getNseSessionStatus(continuousDate);
    expect(continuousSession.status).toBe("OPEN");
    expect(continuousSession.label).toBe("NSE CONTINUOUS");
    expect(continuousSession.sub).toContain("09:15–15:15 IST");

    // Wednesday 2026-11-04 15:20:00 IST (09:50 UTC) -> Dual Session (CAS in auction, non-CAS continuous to 15:30)
    const casDate = new Date("2026-11-04T09:50:00.000Z");
    const casSession = getNseSessionStatus(casDate);
    expect(casSession.status).toBe("CAS");
    expect(casSession.label).toBe("NSE DUAL / CAS");
    expect(casSession.sub).toContain("15:15–15:35");

    // Wednesday 2026-11-04 15:32:00 IST (10:02 UTC) -> Phase 1 CAS concluding
    const casConcludeDate = new Date("2026-11-04T10:02:00.000Z");
    const casConcludeSession = getNseSessionStatus(casConcludeDate);
    expect(casConcludeSession.status).toBe("CAS");
    expect(casConcludeSession.label).toBe("NSE CAS AUCTION");

    // Wednesday 2026-11-04 09:05:00 IST (03:35 UTC) -> Pre-open
    const preDate = new Date("2026-11-04T03:35:00.000Z");
    const preSession = getNseSessionStatus(preDate);
    expect(preSession.status).toBe("PRE_OPEN");
    expect(preSession.label).toBe("NSE PRE-OPEN");

    // Wednesday 2026-11-04 18:00:00 IST (12:30 UTC) -> Closed
    const closedDate = new Date("2026-11-04T12:30:00.000Z");
    const closedSession = getNseSessionStatus(closedDate);
    expect(closedSession.status).toBe("CLOSED");
    expect(closedSession.label).toBe("NSE CLOSED");

    // Saturday 2026-11-07 11:00:00 IST (05:30 UTC) -> Weekend closed
    const weekendDate = new Date("2026-11-07T05:30:00.000Z");
    const weekendSession = getNseSessionStatus(weekendDate);
    expect(weekendSession.status).toBe("CLOSED");
  });

  it("derives per-instrument session phase from dated authoritative CAS evidence", () => {
    // Authoritative universe CAS flags
    expect(CAS_ELIGIBILITY_MAP["2885"]).toBe(1); // RELIANCE (derivative-enabled CAS equity)
    expect(CAS_ELIGIBILITY_MAP["1333"]).toBe(1); // HDFCBANK
    expect(CAS_ELIGIBILITY_MAP["29124"]).toBe(0); // PCJEWELLER (non-CAS cash equity)
    expect(CAS_ELIGIBILITY_MAP["766325"]).toBe(0); // MONEYVIEW (non-CAS)
    expect(CAS_ELIGIBILITY_MAP["999999"]).toBeUndefined(); // Unknown

    const morning = new Date("2026-11-04T05:00:00.000Z"); // 10:30 IST
    const splitWindow = new Date("2026-11-04T09:50:00.000Z"); // 15:20 IST
    const casOnlyWindow = new Date("2026-11-04T10:02:00.000Z"); // 15:32 IST
    const night = new Date("2026-11-04T12:30:00.000Z"); // 18:00 IST

    // In morning continuous session (09:15-15:15): all instruments are CONTINUOUS
    expect(getInstrumentSessionStatus(1, morning).status).toBe("OPEN");
    expect(getInstrumentSessionStatus(0, morning).status).toBe("OPEN");
    expect(getInstrumentSessionStatus(undefined, morning).status).toBe("OPEN");

    // In 15:15-15:30 split window:
    // CAS-eligible stock (1) enters CAS AUCTION
    const casStockSplit = getInstrumentSessionStatus(1, splitWindow);
    expect(casStockSplit.status).toBe("CAS");
    expect(casStockSplit.label).toBe("CAS AUCTION");

    // Non-CAS stock (0) continues in CONTINUOUS trading until 15:30
    const nonCasStockSplit = getInstrumentSessionStatus(0, splitWindow);
    expect(nonCasStockSplit.status).toBe("OPEN");
    expect(nonCasStockSplit.label).toBe("CONTINUOUS");

    // Unknown applicability stock (undefined) shows UNKNOWN rather than misclassifying
    const unknownStockSplit = getInstrumentSessionStatus(undefined, splitWindow);
    expect(unknownStockSplit.status).toBe("UNKNOWN");
    expect(unknownStockSplit.label).toBe("UNKNOWN");

    // In 15:30-15:35 window:
    // CAS-eligible stock is still in CAS AUCTION
    expect(getInstrumentSessionStatus(1, casOnlyWindow).status).toBe("CAS");
    // Non-CAS stock is CLOSED (closed at 15:30)
    expect(getInstrumentSessionStatus(0, casOnlyWindow).status).toBe("CLOSED");
    // Unknown stock shows UNKNOWN
    expect(getInstrumentSessionStatus(undefined, casOnlyWindow).status).toBe("UNKNOWN");

    // Off-hours: all instruments are CLOSED
    expect(getInstrumentSessionStatus(1, night).status).toBe("CLOSED");
    expect(getInstrumentSessionStatus(0, night).status).toBe("CLOSED");
  });
});
