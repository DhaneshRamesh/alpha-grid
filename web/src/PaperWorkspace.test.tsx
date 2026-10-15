import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import PaperWorkspace from "./PaperWorkspace";
import type { WireEngineSnapshot } from "./generated/engine-snapshot";

const snapshot = {
  journal_blocked: false,
  latest_data_timestamp_ns: "1700000001000000000",
  paper: {
    schema_version: 1,
    mode: "PAPER_REPLAY",
    ledger_healthy: true,
    ledger_recovered_tail_bytes: "0",
    last_source_record_index: "1",
    fee_schedule_version: "provisional-v1",
    policy_version: "hypothesis-v1",
    risk_rules_version: "risk-v1",
    portfolio: {
      starting_capital_nanos: "20000000000000",
      cash_nanos: "20000000000000",
      reserved_cash_nanos: "0",
      available_cash_nanos: "20000000000000",
      realized_pnl_nanos: "0",
      fees_paid_nanos: "0",
      marked_equity_nanos: "20000000000000",
      max_drawdown_nanos: "0",
      exposure_nanos: "0",
      remaining_session_loss_nanos: "500000000000",
      halted: false,
      positions: [],
    },
    recent_decisions: [
      {
        decision_id: "d1",
        source_record_index: "1",
        instrument: {
          key: "NSE:NSE_EQ:1333",
          venue: "NSE",
          exchange_segment: "NSE_EQ",
          security_id: "1333",
          symbol: "RELIANCE",
        },
        action: "NoTrade",
        candidate_action: "Buy",
        reason: "UncalibratedModel",
        hypothesis_id: "hypothesis-v1",
        evidence: ["Snapshot OFI hypothesis"],
        model_version: "untrained-v1",
        model_status: "NotTrained",
        expected_gross_edge_nanos: null,
        expected_net_edge_nanos: null,
        forecast_uncertainty_nanos: null,
        estimated_cost_nanos: "25000000000",
        adverse_cost_nanos: "30000000000",
        cost_provisional: true,
        risk_approved: false,
        risk_reasons: ["UncalibratedModel"],
        quantity: "8",
        notional_nanos: "800000000000",
        source_receive_timestamp_ns: "1700000001000000000",
        expires_at_ns: "1700000011000000000",
        volume_complete: true,
        cross_feed_uncertain: true,
      },
    ],
    orders: [],
    recent_fills: [],
    recent_outcomes: [],
    risk_limits: {
      max_deployed_nanos: "15000000000000",
      max_instrument_notional_nanos: "5000000000000",
      max_positions: 3,
      max_loss_per_decision_nanos: "200000000000",
      max_session_loss_nanos: "500000000000",
      max_spread_bps: 25,
      max_market_age_ns: "2000000000",
      max_slippage_bps: 20,
      max_return_volatility: 0.01,
      max_order_age_ns: "10000000000",
      allow_uncalibrated_paper: false,
    },
  },
} as unknown as WireEngineSnapshot;

describe("paper workspace", () => {
  it("shows unknown predictions and zero paper P&L as different values", () => {
    render(
      <PaperWorkspace
        snapshot={snapshot}
        connection="CONNECTED"
        instrumentKey="NSE:NSE_EQ:1333"
      />,
    );
    expect(screen.getByText("PAPER / SYNTHETIC / REPLAY")).toBeTruthy();
    expect(screen.getAllByText("N/A").length).toBeGreaterThan(1);
    expect(screen.getAllByText("₹0.00").length).toBeGreaterThan(0);
    expect(screen.getAllByText("MODEL NOT TRAINED").length).toBeGreaterThan(0);
    expect(screen.getByText(/Real profitability unknown/i)).toBeTruthy();
  });
  it("keeps the workspace empty when paper mode is disabled", () => {
    render(
      <PaperWorkspace
        snapshot={{ ...snapshot, paper: null }}
        connection="DISCONNECTED"
        instrumentKey={null}
      />,
    );
    expect(screen.getByText(/Paper engine not active/)).toBeTruthy();
  });
});
