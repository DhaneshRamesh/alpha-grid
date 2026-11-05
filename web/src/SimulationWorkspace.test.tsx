import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import SimulationWorkspace, {
  type SimulationResult,
} from "./SimulationWorkspace";
import type { HistoricalCache } from "./TradingWorkspace";

describe("offline trading simulator view", () => {
  it("loads the verified adaptive research panel without treating it as live trading", async () => {
    const fetchMock = vi.fn((url: string) => Promise.resolve(url === "/trade/cross-equity-adaptive"
      ? { ok: true, json: async () => ({ source: "EXPLORATORY_ARCHIVE", sha256: "a".repeat(64),
        decision_log_sha256: "b".repeat(64), report: {
          schema_version: "alpha-grid-cross-equity-adaptive-v2", bars: 45300,
          ml_challenger: null, fixed_baseline_ending_inr: "994692.47",
          momentum_baseline_ending_inr: "1000556.82", no_trade_ending_inr: "1000000",
          specification: { slots: "Every 5 minutes", input_cutoff: "Past only",
            exits: "Adaptive invalidation", risk: "Virtual limits" },
          variants: { price_volume_60s: { variant: "PRICE_PLUS_VOLUME",
            assumed_post_completion_delay_seconds: 60, selected_opportunities: 1,
            selected_sessions: 1, ending_virtual_capital_inr: "999900",
            net_reference_inr: "-100", gross_reference_inr: "10",
            fees_inr: "10", assumed_friction_inr: "100", max_drawdown_inr: "100",
            abstention_slots: 2, session_bootstrap_95pct_mean_net_inr: null,
            equity_curve: [{ day: "2026-10-26", virtual_equity_inr: "999900",
              session_net_inr: "-100" }], trade_ledger: [] } },
          paired_volume_ablation: {} }, fixed_audit: { coverage: {
            evaluated_rows: 125, complete_past_rows: 125 }, rows: [],
            selected_paths: [] } }) }
      : { ok: false, json: async () => null }));
    vi.stubGlobal("fetch", fetchMock);
    try {
      render(<SimulationWorkspace historical={null} />);
      await waitFor(() => expect(screen.getByRole("heading", {
        name: "Cross-equity adaptive reference replay" })).toBeTruthy());
      expect(screen.getByText(/Historical candle availability and actual fills are unverified/)).toBeTruthy();
      expect(screen.getByText(/All variants remain net negative/)).toBeTruthy();
    } finally { vi.unstubAllGlobals(); }
  });
  it("shows reference accounting and excluded diagnostics without fill claims", () => {
    // UI contract fixture only; never market research evidence.
    const trade = {
      day: "2026-10-26",
      symbol: "RELIANCE",
      rule: "OPENING_RANGE_BREAKOUT",
      status: "CLOSED_REFERENCE_ONLY",
      shares: 10,
      entry_close_reference_inr: "100",
      exit_close_reference_inr: "101",
      gross_reference_inr: "10.00",
      fee_components_inr: { brokerage: "6.00", total: "8.00" },
      assumed_friction_inr: "4.00",
      net_reference_inr: "-2.00",
      capital_utilization: "0.001",
      actual_fill: false,
    } as const;
    const summary = {
      selected_sessions: 1,
      independent_sessions: 1,
      ending_virtual_capital_inr: "999998.00",
      mean_session_net_inr: "-2.00",
      mean_capital_utilization: "0.001",
      max_drawdown_inr: "2.00",
      session_bootstrap_95pct_mean_net_inr: null,
      equity_curve: [
        {
          day: "2026-10-26",
          virtual_equity_inr: "999998.00",
          session_net_inr: "-2.00",
        },
      ],
    };
    const simulation = {
      schema_version: "alpha-grid-simulation-v1",
      mode: "SIMULATION_ONLY",
      source_hashes: { RELIANCE: ["fixture-only"] },
      universe_asof: "2026-10-30",
      timing_assumption: "UNVERIFIED",
      eligibility: "EXPLORATORY_MECHANICS_ONLY",
      virtual_starting_capital_inr: "1000000.00",
      fixed_rule: "Frozen 10:00 opening range",
      friction_assumption_bps: "40",
      available_symbols: ["RELIANCE"],
      missing_symbols: ["INFY"],
      candidate: summary,
      momentum: summary,
      no_trade: {
        ending_virtual_capital_inr: "1000000.00",
        selected_sessions: 0,
      },
      paired_candidate_minus_momentum_95pct_inr: null,
      trade_ledger: [trade],
      momentum_ledger: [trade],
      forced_diagnostics_excluded: [
        { ...trade, day: "2026-10-27", rule: "FORCED_DIAGNOSTIC_EXCLUDED" },
      ],
      decisions: [
        {
          day: "2026-10-26",
          symbol: "RELIANCE",
          decision: "RULE_SELECTED_REFERENCE",
        },
      ],
      decision_counts: { RULE_SELECTED_REFERENCE: 1, NO_GENUINE_ARCHIVE: 1 },
      virtual_positions_end: [],
      paper_orders: 0,
      qp01_writes: 0,
      broker_orders: 0,
      evaluation_status: "NO_UNTOUCHED_CROSS_STOCK_HOLDOUT",
    } satisfies SimulationResult;
    render(
      <SimulationWorkspace
        historical={
          {
            schema_version: "dhan-historical-cache-v1",
            status: "AVAILABLE",
            source: "DHAN_HISTORICAL",
            causal_eligibility: "BLOCKED",
            bars: [],
            simulation,
          } satisfies HistoricalCache
        }
      />,
    );
    expect(
      screen.getByRole("heading", { name: "Trading simulator." }),
    ).toBeTruthy();
    expect(
      screen.getByText(/Virtual capital and reference close prices/),
    ).toBeTruthy();
    expect(screen.getByText(/Forced diagnostic selections: 1/)).toBeTruthy();
    expect(
      screen.getByText(/QP01 writes 0; paper orders 0; broker orders 0/),
    ).toBeTruthy();
    expect(
      screen.getByRole("img", { name: "Virtual equity curve" }),
    ).toBeTruthy();
  });
});
