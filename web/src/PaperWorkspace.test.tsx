import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import PaperWorkspace from "./PaperWorkspace";
import type { WireEngineSnapshot } from "./generated/engine-snapshot";

const snapshot = {
  journal_blocked: false,
  latest_data_timestamp_ns: "1700000001000000000",
  approved_security_ids: [],
  instruments: [],
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
    expect(screen.getByText("PAPER RESEARCH / SIMULATED FILLS")).toBeTruthy();
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
  it("allows an authenticated ledger approval without inventing a paper position", async () => {
    const post = vi.fn().mockResolvedValue({ ok: true, status: 204 });
    vi.stubGlobal("fetch", post);
    render(
      <PaperWorkspace
        snapshot={
          {
            ...snapshot,
            paper: null,
            instruments: [
              {
                instrument: {
                  key: "NSE:NSE_EQ:2885",
                  security_id: "2885",
                  symbol: null,
                },
              },
            ],
          } as WireEngineSnapshot
        }
        connection="CONNECTED"
        instrumentKey={null}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Approve stock" }));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        "/api/paper/approval",
        expect.objectContaining({ method: "POST", credentials: "same-origin" }),
      ),
    );
    expect(JSON.parse(post.mock.calls[0][1].body)).toEqual({
      security_id: "2885",
      approved: true,
    });
    expect(screen.getByText(/no validated stock candidates/i)).toBeTruthy();
    vi.unstubAllGlobals();
  });
  it("shows three abstaining horizons and queues paper-only approval", async () => {
    const post = vi.fn().mockResolvedValue({ ok: true, status: 204 });
    vi.stubGlobal("fetch", post);
    const assessment = {
      instrument: snapshot.paper!.recent_decisions[0].instrument,
      approved: false,
      source_receive_timestamp_ns: "1700000001000000000",
      model_version: "untrained-v1",
      model_status: "NotTrained",
      expected_gross_edge_nanos: null,
      expected_cost_nanos: null,
      adverse_cost_nanos: null,
      uncertainty_nanos: null,
      robust_net_edge_nanos: null,
      reason: "ApprovalRequired",
      horizon_ns: null,
    };
    const paper = {
      ...snapshot.paper!,
      mode: "PAPER_REPLAY_APPROVED_RESEARCH",
      approved_security_ids: [],
      scanner: ["30m", "60m", "intraday"].map((horizon) => ({
        ...assessment,
        horizon,
      })),
    };
    render(
      <PaperWorkspace
        snapshot={{ ...snapshot, paper } as WireEngineSnapshot}
        connection="CONNECTED"
        instrumentKey={null}
      />,
    );
    expect(screen.getAllByText(/NO TRADE · ApprovalRequired/)).toHaveLength(3);
    expect(screen.getByText("intraday")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Approve stock" }));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        "/api/paper/approval",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    expect(screen.getByRole("status").textContent).toMatch(/QP01 paper ledger/);
    fireEvent.click(screen.getByRole("button", { name: "Halt paper entries" }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(JSON.parse(post.mock.calls[1][1].body).action).toBe("halt_paper");
    vi.unstubAllGlobals();
  });
});
