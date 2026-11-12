import type { HistoricalCache } from "./TradingWorkspace";
import { useEffect, useMemo, useState } from "react";

type LiveSimulation = {
  schema_version: "alpha-grid-live-simulation-v1" | "alpha-grid-live-portfolio-simulation-v2";
  state: string;
  feed_status: string;
  universe_count: number;
  last_quote_age_seconds?: number | null;
  last_quote_time_ist?: string | null;
  cash_inr: string;
  equity_inr: string;
  unrealized_pnl_inr?: string;
  max_drawdown_inr: string;
  position: { symbol: string; shares: number; ask_reference: string } | null;
  positions?: { symbol: string; remaining_shares: number; ask_reference_inr: string }[];
  remaining_deployable_inr?: string;
  capital_buffer_at_full_deployment_inr?: string;
  reserved_cash_inr?: string;
  remaining_position_slots?: number;
  equity_basis?: string;
  trades: {
    day: string;
    symbol: string;
    net_reference?: string;
    net_reference_inr?: string;
    fees: { total: string };
    assumed_friction?: string;
    residual_friction_inr?: string;
  }[];
  decisions: {
    kind: string;
    symbol?: string;
    reason?: string;
    day?: string;
    message?: string;
    decision_slot?: string;
  }[];
  observed_quotes: {
    symbol: string;
    bid_nanos: string;
    ask_nanos: string;
    receive_ns: string;
    book_source?: string;
    receipt_to_worker_ms?: string;
    raw_frame_sha256?: string;
    rest_snapshot_sha256?: string | null;
  }[];
  opportunity_radar?: {
    symbol: string;
    security_id: string;
    bid_inr: string;
    ask_inr: string;
    spread_bps: string;
    momentum_bps: string;
    book_imbalance: string;
    status: string;
    shares_held: number;
    entry_price_inr: string | null;
    unrealized_pnl_inr: string;
    unrealized_pnl_bps: string;
    age_seconds?: number;
    receive_time_ist?: string;
  }[];
  equity_curve: {
    day: string;
    virtual_equity_inr?: string;
    virtual_book_equity_inr?: string;
    session_net_inr?: string;
  }[];
  rejected_count: number;
  ledger_events: number;
  risk_limits?: {
    version: string;
    max_deployed_inr: string;
    max_instrument_inr: string;
    max_positions: number;
    max_session_loss_inr: string;
    max_loss_per_decision_inr: string;
    max_spread_bps: string;
    max_market_age_ns: string;
  };
};

type VirtualTrade = {
  day: string;
  symbol: string;
  rule: string;
  status: "CLOSED_REFERENCE_ONLY";
  shares: number;
  entry_close_reference_inr: string;
  exit_close_reference_inr: string;
  gross_reference_inr: string;
  fee_components_inr: Record<string, string>;
  assumed_friction_inr: string;
  net_reference_inr: string;
  capital_utilization: string;
  actual_fill: false;
};

type Summary = {
  selected_sessions: number;
  independent_sessions: number;
  ending_virtual_capital_inr: string;
  mean_session_net_inr: string;
  mean_capital_utilization: string;
  max_drawdown_inr: string;
  session_bootstrap_95pct_mean_net_inr: [string, string] | null;
  equity_curve: {
    day: string;
    virtual_equity_inr: string;
    session_net_inr: string;
  }[];
};

type AdaptiveTrade = {
  day: string;
  symbol: string;
  entry_decision_time_ist: string;
  entry_asof_label_ist: string;
  entry_reference_label_ist: string;
  exit_decision_time_ist: string;
  exit_reason: string;
  exit_reference_label_ist: string;
  gross_reference_inr: string;
  fee_components_inr: { total: string };
  assumed_friction_inr: string;
  net_reference_inr: string;
  ranked_candidates: string[];
};

type AdaptiveVariant = {
  variant: string;
  assumed_post_completion_delay_seconds: number;
  selected_opportunities: number;
  selected_sessions: number;
  ending_virtual_capital_inr: string;
  net_reference_inr: string;
  gross_reference_inr: string;
  fees_inr: string;
  assumed_friction_inr: string;
  max_drawdown_inr: string;
  abstention_slots: number;
  session_bootstrap_95pct_mean_net_inr: [string, string] | null;
  equity_curve: Summary["equity_curve"];
  trade_ledger: AdaptiveTrade[];
};

type AdaptiveResearch = {
  source: "EXPLORATORY_ARCHIVE";
  sha256: string;
  decision_log_sha256: string;
  report: {
    schema_version: string;
    bars: number;
    ml_challenger: null;
    specification: Record<string, string>;
    variants: Record<string, AdaptiveVariant>;
    paired_volume_ablation: Record<
      string,
      {
        incremental_net_inr: string;
        session_bootstrap_95pct_mean_incremental_inr: [string, string] | null;
      }
    >;
    fixed_baseline_ending_inr: string;
    momentum_baseline_ending_inr: string;
    no_trade_ending_inr: string;
  };
  fixed_audit: {
    coverage: {
      sessions: number;
      instruments: number;
      evaluated_rows: number;
      complete_past_rows: number;
      missing_future_exit_rows: number;
    };
    future_exit_availability_used_in_original_eligibility: boolean;
    rows: {
      day: string;
      symbol: string;
      security_id: string;
      all_stock_strength_rank?: number;
      breakout_rank?: number;
      breakout_bps?: string;
      trailing_15m_bps?: string;
      volume_15m?: number;
      past_complete: boolean;
      original_decision: string;
    }[];
    selected_paths: {
      day: string;
      symbol: string;
      first_breakout_invalidation_label_ist: string | null;
    }[];
  };
};

export type SimulationResult = {
  schema_version: "alpha-grid-simulation-v1";
  mode: "SIMULATION_ONLY";
  source_hashes: Record<string, string[]>;
  universe_asof: string;
  timing_assumption: string;
  eligibility: "EXPLORATORY_MECHANICS_ONLY";
  virtual_starting_capital_inr: string;
  fixed_rule: string;
  friction_assumption_bps: string;
  available_symbols: string[];
  missing_symbols: string[];
  candidate: Summary;
  momentum: Summary;
  no_trade: { ending_virtual_capital_inr: string; selected_sessions: number };
  paired_candidate_minus_momentum_95pct_inr: [string, string] | null;
  trade_ledger: VirtualTrade[];
  momentum_ledger: VirtualTrade[];
  forced_diagnostics_excluded: VirtualTrade[];
  decisions: { day: string; symbol: string; decision: string }[];
  decision_counts: Record<string, number>;
  virtual_positions_end: [];
  paper_orders: 0;
  qp01_writes: 0;
  broker_orders: 0;
  evaluation_status: string;
};

const inr = (value: string | number) =>
  `₹${Number(value).toLocaleString("en-IN", {
    maximumFractionDigits: 2,
    minimumFractionDigits: 2,
  })}`;

function friendlyDecisionExplanation(reason?: string, symbol?: string): string {
  switch (reason) {
    case "PAST_LOOKBACK_UNAVAILABLE":
      return `${symbol ?? "Stock"}: Accumulating initial price history (warmup)`;
    case "NO_POSITIVE_NET_MOMENTUM_PROXY":
      return `${symbol ?? "Stock"}: Price flat or consolidating; waiting for upward momentum impulse`;
    case "QUOTED_SPREAD_LIMIT":
      return `${symbol ?? "Stock"}: Quoted spread exceeds 25 bps safety limit`;
    case "STALE_OR_UNVERIFIED_QUOTE":
      return `${symbol ?? "Stock"}: Quote stale or awaiting verified book pairing`;
    case "SESSION_LOSS_LIMIT":
      return `Session safety stop reached (-₹500 limit)`;
    case "DECISION_LOSS_LIMIT":
      return `${symbol ?? "Stock"}: Potential risk bound exceeds ₹200 single-trade limit`;
    case "POSITION_OR_EXPOSURE_LIMIT":
      return `Portfolio full: maximum 3 concurrent positions active`;
    case "ENTRY_CUTOFF":
      return `Past 14:40 IST cutoff: new entries paused`;
    case "MOMENTUM_REVERSAL":
      return `${symbol ?? "Stock"}: Momentum reversal detected — taking protective profit/disposal`;
    case "PROTECTIVE_ADVERSE_MOVE":
      return `${symbol ?? "Stock"}: Protective stop triggered (-15 bps adverse move)`;
    case "SESSION_CUTOFF":
      return `${symbol ?? "Stock"}: End-of-day market close disposal`;
    case "NO_EXIT_TRIGGER":
      return `${symbol ?? "Stock"}: Trend favorable — holding virtual position`;
    default:
      return `${symbol ? `${symbol}: ` : ""}${reason?.replaceAll("_", " ") ?? "Evaluating"}`;
  }
}

function EquityCurve({ values }: { values: Summary["equity_curve"] }) {
  if (!values.length) return null;
  const y = values.map((point) => Number(point.virtual_equity_inr));
  const low = Math.min(...y);
  const high = Math.max(...y);
  const span = Math.max(high - low, 1);
  const points = y
    .map(
      (value, index) =>
        `${20 + (index * 560) / Math.max(y.length - 1, 1)},${110 - ((value - low) / span) * 90
        }`,
    )
    .join(" ");
  return (
    <figure aria-label="Virtual end-of-session equity curve">
      <svg
        role="img"
        aria-label="Virtual equity curve"
        viewBox="0 0 600 130"
        width="100%"
        height="160"
      >
        <polyline
          points={points}
          fill="none"
          stroke="currentColor"
          strokeWidth="3"
        />
        {y.map((value, index) => (
          <circle
            key={`${values[index].day}-${index}`}
            cx={20 + (index * 560) / Math.max(y.length - 1, 1)}
            cy={110 - ((value - low) / span) * 90}
            r="4"
            fill="currentColor"
          />
        ))}
      </svg>
      <figcaption>
        {values[0].day}–{values[values.length - 1].day} · Daily marked reference
        equity; idle capital stays at zero return.
      </figcaption>
    </figure>
  );
}

export default function SimulationWorkspace({
  historical,
}: {
  historical: HistoricalCache | null;
}) {
  const [crossStock, setCrossStock] = useState<{
    source: "EXPLORATORY_ARCHIVE";
    sha256: string;
    simulation: SimulationResult;
  } | null>(null);
  const [adaptive, setAdaptive] = useState<AdaptiveResearch | null>(null);
  const [adaptiveKey, setAdaptiveKey] = useState("price_volume_60s");
  const sim = crossStock?.simulation ?? historical?.simulation;
  const [live, setLive] = useState<LiveSimulation | null>(null);
  const [stopRequested, setStopRequested] = useState(false);
  const [stopError, setStopError] = useState("");
  const [radarFilter, setRadarFilter] = useState<string>("ALL");
  const [radarSearch, setRadarSearch] = useState<string>("");
  const [activeTab, setActiveTab] = useState<"live" | "radar" | "trades" | "research">("live");

  useEffect(() => {
    let active = true;
    const read = () => {
      void fetch("/trade/simulation", {
        credentials: "same-origin",
        cache: "no-store",
      })
        .then((response) => (response.ok ? response.json() : null))
        .then((value: LiveSimulation | null) => {
          if (active)
            setLive(
              value?.schema_version === "alpha-grid-live-simulation-v1" ||
                value?.schema_version === "alpha-grid-live-portfolio-simulation-v2"
                ? value
                : null,
            );
        })
        .catch(() => {
          if (active) setLive(null);
        });
    };
    read();
    const timer = window.setInterval(read, 1500);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    let active = true;
    void fetch("/trade/cross-equity-adaptive", {
      credentials: "same-origin",
      cache: "no-store",
    })
      .then((response) => (response.ok ? response.json() : null))
      .then((value: AdaptiveResearch | null) => {
        if (
          active &&
          value?.source === "EXPLORATORY_ARCHIVE" &&
          value.report.schema_version === "alpha-grid-cross-equity-adaptive-v2"
        )
          setAdaptive(value);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    const read = () => {
      void fetch("/trade/cross-stock-simulation", {
        credentials: "same-origin",
        cache: "no-store",
      })
        .then((response) => (response.ok ? response.json() : null))
        .then((value) => {
          if (active)
            setCrossStock(
              value?.simulation?.schema_version === "alpha-grid-simulation-v1"
                ? value
                : null,
            );
        })
        .catch(() => {
          if (active) setCrossStock(null);
        });
    };
    read();
    const timer = window.setInterval(read, 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const stop = () => {
    setStopError("");
    void fetch("/trade/simulation/stop", {
      method: "POST",
      credentials: "same-origin",
    })
      .then((response) => {
        if (response.ok) setStopRequested(true);
        else setStopError(`Stop request rejected (${response.status}).`);
      })
      .catch(() => setStopError("Stop service unavailable."));
  };

  const realizedNet = useMemo(() => {
    if (!live?.trades) return "0";
    const total = live.trades.reduce(
      (acc, trade) =>
        acc + Number(trade.net_reference_inr ?? trade.net_reference ?? 0),
      0,
    );
    return total.toFixed(2);
  }, [live?.trades]);

  const deployedCapital = useMemo(() => {
    if (!live) return "0";
    const starting = 20000;
    const cash = Number(live.cash_inr ?? starting);
    return Math.max(0, starting - cash).toFixed(2);
  }, [live]);

  const filteredRadar = useMemo(() => {
    if (!live?.opportunity_radar) return [];
    return live.opportunity_radar.filter((item) => {
      const matchSearch =
        !radarSearch ||
        item.symbol.toLowerCase().includes(radarSearch.toLowerCase()) ||
        item.security_id.includes(radarSearch);
      if (!matchSearch) return false;
      if (radarFilter === "ACTIVE") return item.status === "OPEN_POSITION";
      if (radarFilter === "CANDIDATE") return item.status === "BUY_CANDIDATE";
      if (radarFilter === "MONITORING") return item.status === "MONITORING";
      if (radarFilter === "SPREAD_FILTERED") return item.status === "SPREAD_FILTERED";
      return true;
    });
  }, [live?.opportunity_radar, radarFilter, radarSearch]);

  const activePositionsList = useMemo(() => {
    if (live?.positions && live.positions.length > 0) {
      return live.positions.map((pos) => {
        const radarItem = live.opportunity_radar?.find((r) => r.symbol === pos.symbol);
        const askRef = Number(pos.ask_reference_inr);
        const currentBid = radarItem ? Number(radarItem.bid_inr) : askRef;
        const unrealizedInr = (currentBid - askRef) * pos.remaining_shares;
        const unrealizedBps = askRef > 0 ? ((currentBid - askRef) * 10000) / askRef : 0;
        return {
          ...pos,
          currentBid: currentBid.toFixed(2),
          unrealizedInr: unrealizedInr.toFixed(2),
          unrealizedBps: unrealizedBps.toFixed(1),
          notional: (askRef * pos.remaining_shares).toFixed(2),
          stopLoss: (askRef * 0.9985).toFixed(2),
        };
      });
    }
    if (live?.position) {
      const askRef = Number(live.position.ask_reference);
      const radarItem = live.opportunity_radar?.find((r) => r.symbol === live.position?.symbol);
      const currentBid = radarItem ? Number(radarItem.bid_inr) : askRef;
      const unrealizedInr = (currentBid - askRef) * live.position.shares;
      const unrealizedBps = askRef > 0 ? ((currentBid - askRef) * 10000) / askRef : 0;
      return [
        {
          symbol: live.position.symbol,
          remaining_shares: live.position.shares,
          ask_reference_inr: live.position.ask_reference,
          currentBid: currentBid.toFixed(2),
          unrealizedInr: unrealizedInr.toFixed(2),
          unrealizedBps: unrealizedBps.toFixed(1),
          notional: (askRef * live.position.shares).toFixed(2),
          stopLoss: (askRef * 0.9985).toFixed(2),
        },
      ];
    }
    return [];
  }, [live?.positions, live?.position, live?.opportunity_radar]);

  return (
    <>
      <div className="page-heading compact">
        <div>
          <div className="eyebrow">OFFLINE / SIMULATION_ONLY</div>
          <h1>
            Trading simulator<span className="heading-dot">.</span>
          </h1>
          <p>
            Virtual capital and reference close prices from verified Dhan
            history. No fills, paper intents or broker orders.
          </p>
        </div>
      </div>

      {/* Navigation view modes */}
      <div className="paper-tags" style={{ marginBottom: "1.25rem", display: "flex", gap: "0.5rem" }}>
        <button
          type="button"
          className={`chip ${activeTab === "live" ? "alert" : ""}`}
          onClick={() => setActiveTab("live")}
          style={{ cursor: "pointer", padding: "0.4rem 0.8rem", borderRadius: "4px" }}
        >
          ● Active Paper Desk
        </button>
        <button
          type="button"
          className={`chip ${activeTab === "radar" ? "alert" : ""}`}
          onClick={() => setActiveTab("radar")}
          style={{ cursor: "pointer", padding: "0.4rem 0.8rem", borderRadius: "4px" }}
        >
          ◈ Opportunity Radar (25 Stocks)
        </button>
        <button
          type="button"
          className={`chip ${activeTab === "trades" ? "alert" : ""}`}
          onClick={() => setActiveTab("trades")}
          style={{ cursor: "pointer", padding: "0.4rem 0.8rem", borderRadius: "4px" }}
        >
          ≡ Trade Ledger & Curve
        </button>
        <button
          type="button"
          className={`chip ${activeTab === "research" ? "alert" : ""}`}
          onClick={() => setActiveTab("research")}
          style={{ cursor: "pointer", padding: "0.4rem 0.8rem", borderRadius: "4px" }}
        >
          ◇ Research & Archives
        </button>
      </div>

      {/* PRIMARY ACTIVE SIMULATION SECTION */}
      <section className="panel" aria-label="Persistent live simulation">
        <div className="panel-head">
          <div>
            <div className="eyebrow">
              {live?.feed_status === "OBSERVING" && live?.last_quote_age_seconds !== undefined && live?.last_quote_age_seconds !== null ? (
                <span style={{ color: "#10b981", fontWeight: 700 }}>
                  🟢 LIVE DHAN FEED STREAMING · Updated {live.last_quote_age_seconds}s ago ({live.last_quote_time_ist ?? "now"}) · 25 NSE Equities
                </span>
              ) : live?.state === "OBSERVING" ? (
                "🟢 LIVE AUTONOMOUS SIMULATOR ACTIVE"
              ) : (
                `🟡 SIMULATOR STANDBY · ${live?.state ?? "INITIALIZING"}`
              )}
            </div>
            <h2>
              Autonomous Paper Trading Desk
              <span className="heading-dot">.</span>
            </h2>
          </div>
          <button
            type="button"
            onClick={stop}
            disabled={stopRequested || !live}
            style={{
              background: stopRequested ? "#374151" : "#b91c1c",
              color: "#fff",
              padding: "0.5rem 1rem",
              borderRadius: "4px",
              cursor: stopRequested ? "not-allowed" : "pointer",
            }}
          >
            {stopRequested ? "Stopping…" : "Stop local session"}
          </button>
        </div>
        {stopError && <p role="status" style={{ color: "#ef4444" }}>{stopError}</p>}

        {live ? (
          <>
            <p style={{ color: "#9ca3af", fontSize: "0.9rem", margin: "0.25rem 0 1rem" }}>
              Trading virtual ₹20,000 cash against live 25-stock Dhan Standard/Depth feeds.
              Zero real broker execution. Concurrent multi-stock risk limits active.
            </p>

            {/* HIGH IMPACT KPI METRICS */}
            <div className="metric-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "1rem" }}>
              <div className="metric" style={{ background: "rgba(16, 185, 129, 0.05)", border: "1px solid rgba(16, 185, 129, 0.2)", borderRadius: "6px", padding: "0.75rem" }}>
                <div className="metric-label">VIRTUAL EQUITY</div>
                <div className="metric-value" style={{ fontSize: "1.4rem", color: "#10b981" }}>
                  {inr(live.equity_inr)}
                </div>
                <small style={{ color: "#9ca3af" }}>
                  Unrealized P&amp;L: {inr(live.unrealized_pnl_inr ?? "0.00")}
                </small>
              </div>

              <div className="metric" style={{ background: "rgba(59, 130, 246, 0.05)", border: "1px solid rgba(59, 130, 246, 0.2)", borderRadius: "6px", padding: "0.75rem" }}>
                <div className="metric-label">VIRTUAL CASH</div>
                <div className="metric-value" style={{ fontSize: "1.4rem", color: "#60a5fa" }}>
                  {inr(live.cash_inr)}
                </div>
                <small style={{ color: "#9ca3af" }}>
                  Deployed: {inr(deployedCapital)} ({inr(live.remaining_deployable_inr ?? "15000")} free)
                </small>
              </div>

              <div className="metric" style={{ background: "rgba(139, 92, 246, 0.05)", border: "1px solid rgba(139, 92, 246, 0.2)", borderRadius: "6px", padding: "0.75rem" }}>
                <div className="metric-label">ACTIVE POSITIONS</div>
                <div className="metric-value" style={{ fontSize: "1.4rem", color: "#a78bfa" }}>
                  {activePositionsList.length} / {live.risk_limits?.max_positions ?? 3} slots
                </div>
                <small style={{ color: "#9ca3af" }}>
                  Cap per stock: {inr(live.risk_limits?.max_instrument_inr ?? "5000")}
                </small>
              </div>

              <div className="metric" style={{ background: "rgba(245, 158, 11, 0.05)", border: "1px solid rgba(245, 158, 11, 0.2)", borderRadius: "6px", padding: "0.75rem" }}>
                <div className="metric-label">REALIZED NET P&amp;L</div>
                <div
                  className="metric-value"
                  style={{
                    fontSize: "1.4rem",
                    color: Number(realizedNet) >= 0 ? "#10b981" : "#ef4444",
                  }}
                >
                  {inr(realizedNet)}
                </div>
                <small style={{ color: "#9ca3af" }}>
                  {live.trades.length} closed trade{live.trades.length === 1 ? "" : "s"}
                </small>
              </div>

              <div className="metric" style={{ background: "rgba(239, 68, 68, 0.05)", border: "1px solid rgba(239, 68, 68, 0.2)", borderRadius: "6px", padding: "0.75rem" }}>
                <div className="metric-label">RISK SHIELD</div>
                <div className="metric-value" style={{ fontSize: "1.4rem", color: "#f87171" }}>
                  {inr(live.max_drawdown_inr)}
                </div>
                <small style={{ color: "#9ca3af" }}>
                  Session stop limit: -{inr(live.risk_limits?.max_session_loss_inr ?? "500")}
                </small>
              </div>
            </div>

            {/* LIVE ACTIVE POSITIONS CARD */}
            <div style={{ marginTop: "1.5rem" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
                <h3 style={{ margin: 0, fontSize: "1.1rem" }}>
                  ⚡ Open Virtual Positions ({activePositionsList.length})
                </h3>
                <span className="live-tag live">MARK-TO-MARKET LIVE</span>
              </div>

              {activePositionsList.length > 0 ? (
                <div className="paper-table-wrap">
                  <table className="paper-table">
                    <thead>
                      <tr>
                        <th>Instrument</th>
                        <th>Shares</th>
                        <th>Entry Price</th>
                        <th>Current Bid</th>
                        <th>Position Value</th>
                        <th>Unrealized P&amp;L</th>
                        <th>Stop Loss</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {activePositionsList.map((pos) => {
                        const isProfitable = Number(pos.unrealizedInr) >= 0;
                        return (
                          <tr key={pos.symbol}>
                            <td style={{ fontWeight: 600 }}>{pos.symbol}</td>
                            <td>{pos.remaining_shares}</td>
                            <td>{inr(pos.ask_reference_inr)}</td>
                            <td>{inr(pos.currentBid)}</td>
                            <td>{inr(pos.notional)}</td>
                            <td style={{ color: isProfitable ? "#10b981" : "#ef4444", fontWeight: 600 }}>
                              {isProfitable ? "+" : ""}
                              {inr(pos.unrealizedInr)} ({isProfitable ? "+" : ""}
                              {pos.unrealizedBps} bps)
                            </td>
                            <td style={{ color: "#ef4444" }}>{inr(pos.stopLoss)}</td>
                            <td>
                              <span className="status-badge holding">HOLDING</span>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div
                  style={{
                    padding: "1.5rem",
                    border: "1px dashed rgba(255, 255, 255, 0.15)",
                    borderRadius: "6px",
                    textAlign: "center",
                    background: "rgba(255, 255, 255, 0.02)",
                  }}
                >
                  <p style={{ margin: 0, fontWeight: 500, color: "#9ca3af" }}>
                    🔭 Scanning 25 Liquid NSE Equities · Awaiting Next Valid Breakout
                  </p>
                  <small style={{ color: "#6b7280" }}>
                    The simulator continuously monitors L2 book imbalance and spread every second.
                    When net momentum overcomes friction and spread, positions trigger automatically.
                  </small>
                </div>
              )}
            </div>

            {/* LIVE OPPORTUNITY RADAR (WHEN ON RADAR TAB OR PREVIEW) */}
            {(activeTab === "radar" || activeTab === "live") && (
              <div style={{ marginTop: "2rem" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "0.5rem", marginBottom: "0.75rem" }}>
                  <div>
                    <h3 style={{ margin: 0, fontSize: "1.1rem" }}>
                      ◈ Live Opportunity Radar — 25 NSE Cash Equities
                    </h3>
                    <small style={{ color: "#9ca3af" }}>
                      Real-time spread, momentum impulse, and orderbook pressure evaluation.
                    </small>
                  </div>
                  <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
                    <input
                      type="text"
                      placeholder="Search stock..."
                      value={radarSearch}
                      onChange={(e) => setRadarSearch(e.target.value)}
                      style={{
                        padding: "0.3rem 0.6rem",
                        background: "#1f2937",
                        border: "1px solid #374151",
                        color: "#fff",
                        borderRadius: "4px",
                        fontSize: "0.85rem",
                      }}
                    />
                    <select
                      value={radarFilter}
                      onChange={(e) => setRadarFilter(e.target.value)}
                      style={{
                        padding: "0.3rem 0.6rem",
                        background: "#1f2937",
                        border: "1px solid #374151",
                        color: "#fff",
                        borderRadius: "4px",
                        fontSize: "0.85rem",
                      }}
                    >
                      <option value="ALL">All Stocks (25)</option>
                      <option value="ACTIVE">Active Positions</option>
                      <option value="CANDIDATE">Buy Candidates</option>
                      <option value="MONITORING">Monitoring</option>
                      <option value="SPREAD_FILTERED">Spread Filtered</option>
                    </select>
                  </div>
                </div>

                <div className="paper-table-wrap">
                  <table className="paper-table">
                    <thead>
                      <tr>
                        <th>Stock</th>
                        <th>Live Bid</th>
                        <th>Live Ask</th>
                        <th>Quoted Spread</th>
                        <th>Momentum</th>
                        <th>Book Imbalance</th>
                        <th>Freshness</th>
                        <th>Engine Assessment</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(filteredRadar.length > 0 ? filteredRadar : live.observed_quotes).map((row) => {
                        const isRadarItem = "status" in row;
                        const symbol = row.symbol;
                        const bid = isRadarItem ? Number(row.bid_inr) : Number(row.bid_nanos) / 1e9;
                        const ask = isRadarItem ? Number(row.ask_inr) : Number(row.ask_nanos) / 1e9;
                        const spreadBps = isRadarItem ? Number(row.spread_bps) : ((ask - bid) * 10000) / ((ask + bid) / 2);
                        const momentumBps = isRadarItem ? Number(row.momentum_bps) : 0;
                        const imbalance = isRadarItem ? Number(row.book_imbalance) : 0;
                        const status = isRadarItem ? row.status : "OBSERVING";
                        const age = isRadarItem ? row.age_seconds : undefined;
                        const recvTime = isRadarItem ? row.receive_time_ist : undefined;

                        return (
                          <tr key={symbol}>
                            <td style={{ fontWeight: 600 }}>{symbol}</td>
                            <td>{inr(bid)}</td>
                            <td>{inr(ask)}</td>
                            <td>
                              <span
                                style={{
                                  color: spreadBps <= 3 ? "#10b981" : spreadBps <= 10 ? "#f59e0b" : "#ef4444",
                                  fontWeight: 500,
                                }}
                              >
                                {spreadBps.toFixed(2)} bps
                              </span>
                            </td>
                            <td style={{ color: momentumBps > 0 ? "#10b981" : momentumBps < 0 ? "#ef4444" : "#9ca3af" }}>
                              {momentumBps > 0 ? "+" : ""}
                              {momentumBps.toFixed(2)} bps
                            </td>
                            <td>
                              <div style={{ display: "flex", alignItems: "center", gap: "0.3rem" }}>
                                <div
                                  style={{
                                    width: "40px",
                                    height: "6px",
                                    background: "#374151",
                                    borderRadius: "3px",
                                    overflow: "hidden",
                                    position: "relative",
                                  }}
                                >
                                  <div
                                    style={{
                                      width: `${Math.min(100, Math.max(0, (imbalance + 1) * 50))}%`,
                                      height: "100%",
                                      background: imbalance >= 0 ? "#10b981" : "#ef4444",
                                    }}
                                  />
                                </div>
                                <span style={{ fontSize: "0.8rem", color: "#9ca3af" }}>
                                  {imbalance.toFixed(2)}
                                </span>
                              </div>
                            </td>
                            <td>
                              <span
                                style={{
                                  fontSize: "0.8rem",
                                  color: age !== undefined && age <= 2 ? "#10b981" : "#f59e0b",
                                  fontWeight: 500,
                                }}
                              >
                                {age !== undefined ? `${age <= 2 ? "🟢" : "🟡"} ${age}s ago` : recvTime ? `🟢 ${recvTime}` : "🟢 Live"}
                              </span>
                            </td>
                            <td>
                              {status === "OPEN_POSITION" && (
                                <span className="status-badge holding">● OPEN POSITION</span>
                              )}
                              {status === "BUY_CANDIDATE" && (
                                <span className="status-badge" style={{ background: "#065f46", color: "#34d399" }}>
                                  🚀 BUY TRIGGER READY
                                </span>
                              )}
                              {status === "MONITORING" && (
                                <span className="status-badge watching">⏳ MONITORING</span>
                              )}
                              {status === "SPREAD_FILTERED" && (
                                <span className="status-badge rejected">⚠️ SPREAD &gt; 25 BPS</span>
                              )}
                              {status === "WARMUP" && (
                                <span className="status-badge stale">⏱️ WARMUP (30s)</span>
                              )}
                              {status === "OBSERVING" && (
                                <span className="status-badge watching">● STREAMING</span>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* REAL-TIME DECISION & ACTIVITY LOG */}
            <div style={{ marginTop: "2rem" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.5rem" }}>
                <h3 style={{ margin: 0, fontSize: "1.1rem" }}>
                  📡 Simulator Activity &amp; Decision Stream
                </h3>
                <small style={{ color: "#9ca3af" }}>
                  Total Evaluated Decisions: {live.decisions.length} · Rejections: {live.rejected_count}
                </small>
              </div>

              <div
                style={{
                  maxHeight: "220px",
                  overflowY: "auto",
                  background: "#111827",
                  border: "1px solid #1f2937",
                  borderRadius: "6px",
                  padding: "0.75rem",
                  fontFamily: "monospace",
                  fontSize: "0.85rem",
                }}
              >
                {live.decisions.length > 0 ? (
                  live.decisions
                    .slice(-15)
                    .reverse()
                    .map((d, index) => (
                      <div
                        key={index}
                        style={{
                          padding: "0.3rem 0",
                          borderBottom: "1px solid rgba(255, 255, 255, 0.05)",
                          display: "flex",
                          justifyContent: "space-between",
                          color:
                            d.kind === "BUY"
                              ? "#34d399"
                              : d.kind === "SELL" || d.kind === "REDUCE"
                                ? "#f87171"
                                : "#9ca3af",
                        }}
                      >
                        <span>
                          <strong>{d.kind}</strong> · {friendlyDecisionExplanation(d.reason, d.symbol)}
                        </span>
                        <span style={{ color: "#6b7280" }}>{d.decision_slot ?? d.day ?? "Live"}</span>
                      </div>
                    ))
                ) : (
                  <div style={{ color: "#6b7280" }}>Awaiting first decision cycle...</div>
                )}
              </div>
            </div>

            {/* REQUIRED AUDIT DETAILS / BACKWARD COMPATIBILITY */}
            <div style={{ marginTop: "1.5rem", fontSize: "0.85rem", color: "#6b7280" }}>
              <p>
                Worker: {live.state} · Feed: {live.feed_status} · Strategy: {live.schema_version} ·
                Universe: {live.universe_count} · Durable Events: {live.ledger_events} · Disposals: {live.trades.length}
              </p>
              {live.positions && (
                <p>
                  Shared risk budget: {inr(live.remaining_deployable_inr ?? "0")} deployable ·{" "}
                  {live.remaining_position_slots ?? 0} position slots ·{" "}
                  {inr(live.capital_buffer_at_full_deployment_inr ?? "0")} capital outside deployment limit.
                  Equity basis: {live.equity_basis}. No actual fills or broker orders.
                </p>
              )}
            </div>
          </>
        ) : (
          <p>Persistent worker unavailable. Saved historical replay remains separate.</p>
        )}
      </section>

      {/* CLOSED TRADES / EQUITY CURVE (WHEN ON TRADES OR LIVE) */}
      {(activeTab === "trades" || activeTab === "live") && live && live.trades.length > 0 && (
        <section className="panel" aria-label="Closed trade history">
          <div className="panel-head">
            <div>
              <div className="eyebrow">CLOSED VIRTUAL DISPOSALS</div>
              <h2>Completed Paper Trades</h2>
            </div>
          </div>
          <div className="paper-table-wrap">
            <table className="paper-table">
              <thead>
                <tr>
                  <th>Day</th>
                  <th>Symbol</th>
                  <th>Net Reference</th>
                  <th>Exchange Fees</th>
                  <th>Friction</th>
                </tr>
              </thead>
              <tbody>
                {live.trades.map((row, index) => (
                  <tr key={`${row.day}-${row.symbol}-${index}`}>
                    <td>{row.day}</td>
                    <td style={{ fontWeight: 600 }}>{row.symbol}</td>
                    <td
                      style={{
                        color:
                          Number(row.net_reference_inr ?? row.net_reference ?? 0) >= 0
                            ? "#10b981"
                            : "#ef4444",
                        fontWeight: 600,
                      }}
                    >
                      {inr(row.net_reference_inr ?? row.net_reference ?? "0")}
                    </td>
                    <td>{inr(row.fees.total)}</td>
                    <td>{inr(row.residual_friction_inr ?? row.assumed_friction ?? "0")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {live.equity_curve && live.equity_curve.length > 0 && (
            <EquityCurve
              values={live.equity_curve.map((row) => ({
                day: row.day,
                virtual_equity_inr: row.virtual_book_equity_inr ?? row.virtual_equity_inr ?? "0",
                session_net_inr: row.session_net_inr ?? "0",
              }))}
            />
          )}
        </section>
      )}

      {/* HISTORICAL RESEARCH ARCHIVES & ADAPTIVE BACKTESTS */}
      {adaptive && (
        <section className="panel" aria-label="Adaptive cross-equity research">
          <div className="panel-head">
            <div>
              <div className="eyebrow">EXPLORATORY_ARCHIVE · NO TRADE</div>
              <h2>Cross-equity adaptive reference replay</h2>
            </div>
          </div>
          <p>
            Archived single-position research version {adaptive.report.schema_version} ·{" "}
            {adaptive.report.bars.toLocaleString("en-IN")} verified Dhan bars · five already examined
            sessions. The separate live portfolio worker supports concurrent positions under shared
            limits. Historical candle availability and actual fills are unverified. ML challenger:
            none.
          </p>
          <p>
            Original fixed rule: {adaptive.fixed_audit.coverage.evaluated_rows} of 125 stock-days
            evaluated; {adaptive.fixed_audit.coverage.complete_past_rows} had complete past inputs.
            The original selector also checked future exit-bar availability and used the same 09:59
            close as trigger and entry reference.
          </p>
          <p>
            {adaptive.report.specification.slots}. {adaptive.report.specification.input_cutoff}.{" "}
            {adaptive.report.specification.exits}. {adaptive.report.specification.risk}.
          </p>
          <div className="paper-table-wrap">
            <table className="paper-table">
              <thead>
                <tr>
                  <th>Rule / assumed delay</th>
                  <th>Trades</th>
                  <th>Gross</th>
                  <th>Fees</th>
                  <th>Friction</th>
                  <th>Net</th>
                  <th>95% session mean interval</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Original fixed 60m</td>
                  <td>5</td>
                  <td colSpan={3}>Saved baseline below</td>
                  <td>{inr(Number(adaptive.report.fixed_baseline_ending_inr) - 1_000_000)}</td>
                  <td>Examined dates</td>
                </tr>
                <tr>
                  <td>NO TRADE</td>
                  <td>0</td>
                  <td>{inr(0)}</td>
                  <td>{inr(0)}</td>
                  <td>{inr(0)}</td>
                  <td>{inr(0)}</td>
                  <td>Zero reference return</td>
                </tr>
                {Object.entries(adaptive.report.variants).map(([key, row]) => (
                  <tr key={key}>
                    <td>
                      {row.variant.replaceAll("_", " ")} /{" "}
                      {row.assumed_post_completion_delay_seconds}s
                    </td>
                    <td>
                      {row.selected_opportunities} / {row.selected_sessions} sessions
                    </td>
                    <td>{inr(row.gross_reference_inr)}</td>
                    <td>{inr(row.fees_inr)}</td>
                    <td>{inr(row.assumed_friction_inr)}</td>
                    <td>{inr(row.net_reference_inr)}</td>
                    <td>
                      {row.session_bootstrap_95pct_mean_net_inr?.map(inr).join(" to ") ??
                        "Insufficient"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p>
            Paired volume gate minus price-only net:{" "}
            {Object.entries(adaptive.report.paired_volume_ablation)
              .map(
                ([delay, value]) =>
                  `${delay} ${inr(value.incremental_net_inr)} (session interval ${value.session_bootstrap_95pct_mean_incremental_inr?.map(inr).join(" to ") ??
                  "unavailable"
                  })`,
              )
              .join(" · ")}
            . All variants remain net negative.
          </p>
          <label htmlFor="adaptive-variant">Inspect adaptive rule</label>{" "}
          <select
            id="adaptive-variant"
            value={adaptiveKey}
            onChange={(event) => setAdaptiveKey(event.target.value)}
          >
            {Object.keys(adaptive.report.variants).map((key) => (
              <option key={key} value={key}>
                {key.replaceAll("_", " ")}
              </option>
            ))}
          </select>
          {adaptive.report.variants[adaptiveKey] && (
            <>
              <p>
                Ending virtual capital{" "}
                {inr(adaptive.report.variants[adaptiveKey].ending_virtual_capital_inr)} · max
                drawdown {inr(adaptive.report.variants[adaptiveKey].max_drawdown_inr)} · abstention
                slots {adaptive.report.variants[adaptiveKey].abstention_slots}. Reference closes are
                not fills.
              </p>
              <EquityCurve values={adaptive.report.variants[adaptiveKey].equity_curve} />
              <div className="paper-table-wrap">
                <table className="paper-table">
                  <thead>
                    <tr>
                      <th>Session</th>
                      <th>Stock</th>
                      <th>Decision / as-of</th>
                      <th>Reference entry / exit</th>
                      <th>Exit reason</th>
                      <th>Gross</th>
                      <th>Fees + friction</th>
                      <th>Net</th>
                    </tr>
                  </thead>
                  <tbody>
                    {adaptive.report.variants[adaptiveKey].trade_ledger.map((row, index) => (
                      <tr key={`${row.day}-${index}`}>
                        <td>{row.day}</td>
                        <td>{row.symbol}</td>
                        <td>
                          {row.entry_decision_time_ist} / {row.entry_asof_label_ist}
                        </td>
                        <td>
                          {row.entry_reference_label_ist} / {row.exit_reference_label_ist}
                        </td>
                        <td>{row.exit_reason}</td>
                        <td>{inr(row.gross_reference_inr)}</td>
                        <td>
                          {inr(
                            Number(row.fee_components_inr.total) +
                            Number(row.assumed_friction_inr),
                          )}
                        </td>
                        <td>{inr(row.net_reference_inr)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          <details>
            <summary>Inspect all 125 original stock rankings and selection reasons</summary>
            <div className="paper-table-wrap">
              <table className="paper-table">
                <thead>
                  <tr>
                    <th>Day</th>
                    <th>Rank</th>
                    <th>Stock</th>
                    <th>Breakout bps</th>
                    <th>15m bps</th>
                    <th>15m volume</th>
                    <th>Decision</th>
                  </tr>
                </thead>
                <tbody>
                  {adaptive.fixed_audit.rows.map((row) => (
                    <tr key={`${row.day}-${row.symbol}`}>
                      <td>{row.day}</td>
                      <td>{row.all_stock_strength_rank ?? "—"}</td>
                      <td>
                        {row.symbol} · {row.security_id}
                      </td>
                      <td>{row.breakout_bps ?? "—"}</td>
                      <td>{row.trailing_15m_bps ?? "—"}</td>
                      <td>{row.volume_15m ?? "—"}</td>
                      <td>{row.original_decision}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
          <p>
            Complete as-of candidate, HOLD, EXIT, REJECT and NO TRADE log retained privately with
            SHA-256 {adaptive.decision_log_sha256}. No model was fitted or promoted.
          </p>
        </section>
      )}

      {/* STATIC SIMULATION COMPARISON / TESTS REQUIREMENTS */}
      {!sim ? (
        <section className="panel">
          <h2>Simulation unavailable</h2>
          <p>
            The pinned RELIANCE archive and offline verifier must be available.
            No data is invented.
          </p>
        </section>
      ) : (
        <>
          <section className="panel">
            <div className="panel-head">
              <div>
                <div className="eyebrow">EXPLORATORY MECHANICS ONLY</div>
                <h2>Virtual account</h2>
              </div>
            </div>
            {crossStock && (
              <p>
                25-stock verified October archive · EXPLORATORY_ARCHIVE · artifact SHA-256{" "}
                {crossStock.sha256}. All five sessions were already inspected; this is not held-out
                evidence.
              </p>
            )}
            <div className="metric-grid three">
              <div className="metric">
                <div className="metric-label">STARTING CAPITAL</div>
                <div className="metric-value">{inr(sim.virtual_starting_capital_inr)}</div>
              </div>
              <div className="metric">
                <div className="metric-label">ENDING REFERENCE EQUITY</div>
                <div className="metric-value">{inr(sim.candidate.ending_virtual_capital_inr)}</div>
              </div>
              <div className="metric">
                <div className="metric-label">MAX DRAWDOWN</div>
                <div className="metric-value">{inr(sim.candidate.max_drawdown_inr)}</div>
              </div>
            </div>
            <p>
              {sim.fixed_rule}. Assumed round-trip friction {sim.friction_assumption_bps} bps plus
              published fee estimates. Bar labels and completion timing remain unverified.
            </p>
            <p>
              Available genuine instruments: {sim.available_symbols.join(", ")}. Missing archive
              for {sim.missing_symbols.length} of the frozen 25 names. No cross-stock evaluation or
              untouched holdout exists.
            </p>
            <p>
              Virtual positions open at session end: {sim.virtual_positions_end.length}. Paper and
              broker orders: {sim.paper_orders + sim.broker_orders}.
            </p>
            <EquityCurve values={sim.candidate.equity_curve} />
          </section>
          <section className="panel">
            <div className="panel-head">
              <div>
                <div className="eyebrow">SAME SESSIONS / NO TRADE INCLUDED</div>
                <h2>Reference comparison</h2>
              </div>
            </div>
            <div className="paper-table-wrap">
              <table className="paper-table">
                <thead>
                  <tr>
                    <th>Rule</th>
                    <th>Selections</th>
                    <th>End equity</th>
                    <th>Mean session net</th>
                    <th>Mean utilization</th>
                    <th>95% session interval</th>
                  </tr>
                </thead>
                <tbody>
                  {(
                    [
                      ["Opening range", sim.candidate],
                      ["15m momentum", sim.momentum],
                    ] as const
                  ).map(([name, row]) => (
                    <tr key={name}>
                      <td>{name}</td>
                      <td>
                        {row.selected_sessions} / {row.independent_sessions}
                      </td>
                      <td>{inr(row.ending_virtual_capital_inr)}</td>
                      <td>{inr(row.mean_session_net_inr)}</td>
                      <td>{(Number(row.mean_capital_utilization) * 100).toFixed(2)}%</td>
                      <td>
                        {row.session_bootstrap_95pct_mean_net_inr
                          ? row.session_bootstrap_95pct_mean_net_inr.map(inr).join(" to ")
                          : "Unavailable"}
                      </td>
                    </tr>
                  ))}
                  <tr>
                    <td>NO TRADE</td>
                    <td>0</td>
                    <td>{inr(sim.no_trade.ending_virtual_capital_inr)}</td>
                    <td>{inr(0)}</td>
                    <td>0%</td>
                    <td>0</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p>
              Paired opening-range minus momentum 95% session interval:{" "}
              {sim.paired_candidate_minus_momentum_95pct_inr?.map(inr).join(" to ") ??
                "Unavailable"}
              . Five already inspected sessions are insufficient for a performance claim.
            </p>
          </section>
          <section className="panel">
            <div className="panel-head">
              <div>
                <div className="eyebrow">REFERENCE PRICES / ZERO FILLS</div>
                <h2>Virtual trade ledger</h2>
              </div>
            </div>
            <div className="paper-table-wrap">
              <table className="paper-table">
                <thead>
                  <tr>
                    <th>Session</th>
                    <th>Instrument</th>
                    <th>Shares</th>
                    <th>09:59 close</th>
                    <th>10:59 close</th>
                    <th>Gross</th>
                    <th>Fees</th>
                    <th>Assumed friction</th>
                    <th>Net</th>
                  </tr>
                </thead>
                <tbody>
                  {sim.trade_ledger.length ? (
                    sim.trade_ledger.map((row) => (
                      <tr key={`${row.day}-${row.symbol}`}>
                        <td>{row.day}</td>
                        <td>{row.symbol}</td>
                        <td>{row.shares}</td>
                        <td>{inr(row.entry_close_reference_inr)}</td>
                        <td>{inr(row.exit_close_reference_inr)}</td>
                        <td>{inr(row.gross_reference_inr)}</td>
                        <td>{inr(row.fee_components_inr.total)}</td>
                        <td>{inr(row.assumed_friction_inr)}</td>
                        <td>{inr(row.net_reference_inr)}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={9}>No rule-selected reference opportunity.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <p>
              Fee detail:{" "}
              {sim.trade_ledger
                .map(
                  (row) =>
                    `${row.day}: ${Object.entries(row.fee_components_inr)
                      .filter(([key]) => key !== "total")
                      .map(([key, value]) => `${key} ${inr(value)}`)
                      .join(", ")}`,
                )
                .join("; ") || "No fee estimate"}
              .
            </p>
          </section>
          <section className="panel">
            <div className="panel-head">
              <div>
                <div className="eyebrow">DECISION AUDIT</div>
                <h2>Rejections and diagnostics</h2>
              </div>
            </div>
            <p>
              {Object.entries(sim.decision_counts)
                .map(([reason, count]) => `${reason}: ${count}`)
                .join(" · ")}
            </p>
            <p>
              Forced diagnostic selections: {sim.forced_diagnostics_excluded.length}. They are
              excluded from virtual equity, candidate statistics and the momentum comparison.
            </p>
            <div className="paper-table-wrap">
              <table className="paper-table">
                <thead>
                  <tr>
                    <th>Session</th>
                    <th>Diagnostic instrument</th>
                    <th>Gross reference</th>
                    <th>Net reference</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {sim.forced_diagnostics_excluded.map((row) => (
                    <tr key={row.day}>
                      <td>{row.day}</td>
                      <td>{row.symbol}</td>
                      <td>{inr(row.gross_reference_inr)}</td>
                      <td>{inr(row.net_reference_inr)}</td>
                      <td>EXCLUDED · NO SIGNAL</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p>
              Source:{" "}
              {Object.entries(sim.source_hashes)
                .map(([symbol, hashes]) => `${symbol} ${hashes.join(", ")}`)
                .join(" · ")}
              . QP01 writes {sim.qp01_writes}; paper orders {sim.paper_orders}; broker orders{" "}
              {sim.broker_orders}.
            </p>
          </section>
        </>
      )}
    </>
  );
}
