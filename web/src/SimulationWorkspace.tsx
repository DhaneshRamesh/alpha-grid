import type { HistoricalCache } from "./TradingWorkspace";
import { useEffect, useState } from "react";

type LiveSimulation = {
  schema_version: "alpha-grid-live-simulation-v1" | "alpha-grid-live-portfolio-simulation-v2";
  state: string;
  feed_status: string;
  universe_count: number;
  cash_inr: string;
  equity_inr: string;
  max_drawdown_inr: string;
  position: { symbol: string; shares: number; ask_reference: string } | null;
  positions?: { symbol: string; remaining_shares: number; ask_reference_inr: string }[];
  remaining_deployable_inr?: string;
  capital_buffer_at_full_deployment_inr?: string;
  reserved_cash_inr?: string;
  remaining_position_slots?: number;
  equity_basis?: string;
  trades: { day: string; symbol: string; net_reference?: string; net_reference_inr?: string;
    fees: { total: string }; assumed_friction?: string; residual_friction_inr?: string }[];
  decisions: { kind: string; symbol?: string; reason?: string; day?: string }[];
  observed_quotes: { symbol: string; bid_nanos: string; ask_nanos: string; receive_ns: string;
    book_source?: string; receipt_to_worker_ms?: string; raw_frame_sha256?: string;
    rest_snapshot_sha256?: string | null }[];
  equity_curve: { day: string; virtual_equity_inr?: string;
    virtual_book_equity_inr?: string; session_net_inr?: string }[];
  rejected_count: number;
  ledger_events: number;
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
  day: string; symbol: string; entry_decision_time_ist: string;
  entry_asof_label_ist: string; entry_reference_label_ist: string;
  exit_decision_time_ist: string; exit_reason: string;
  exit_reference_label_ist: string; gross_reference_inr: string;
  fee_components_inr: { total: string }; assumed_friction_inr: string;
  net_reference_inr: string; ranked_candidates: string[];
};
type AdaptiveVariant = {
  variant: string; assumed_post_completion_delay_seconds: number;
  selected_opportunities: number; selected_sessions: number;
  ending_virtual_capital_inr: string; net_reference_inr: string;
  gross_reference_inr: string; fees_inr: string; assumed_friction_inr: string;
  max_drawdown_inr: string; abstention_slots: number;
  session_bootstrap_95pct_mean_net_inr: [string, string] | null;
  equity_curve: Summary["equity_curve"]; trade_ledger: AdaptiveTrade[];
};
type AdaptiveResearch = {
  source: "EXPLORATORY_ARCHIVE"; sha256: string; decision_log_sha256: string;
  report: { schema_version: string; bars: number; ml_challenger: null;
    specification: Record<string, string>; variants: Record<string, AdaptiveVariant>;
    paired_volume_ablation: Record<string, { incremental_net_inr: string;
      session_bootstrap_95pct_mean_incremental_inr: [string, string] | null }>;
    fixed_baseline_ending_inr: string; momentum_baseline_ending_inr: string;
    no_trade_ending_inr: string };
  fixed_audit: { coverage: { sessions: number; instruments: number; evaluated_rows: number;
    complete_past_rows: number; missing_future_exit_rows: number };
    future_exit_availability_used_in_original_eligibility: boolean;
    rows: { day: string; symbol: string; security_id: string;
      all_stock_strength_rank?: number; breakout_rank?: number;
      breakout_bps?: string; trailing_15m_bps?: string; volume_15m?: number;
      past_complete: boolean; original_decision: string }[];
    selected_paths: { day: string; symbol: string;
      first_breakout_invalidation_label_ist: string | null }[] };
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
  `₹${Number(value).toLocaleString("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`;

function EquityCurve({ values }: { values: Summary["equity_curve"] }) {
  if (!values.length) return null;
  const y = values.map((point) => Number(point.virtual_equity_inr));
  const low = Math.min(...y);
  const high = Math.max(...y);
  const span = Math.max(high - low, 1);
  const points = y
    .map(
      (value, index) =>
        `${20 + (index * 560) / Math.max(y.length - 1, 1)},${110 - ((value - low) / span) * 90}`,
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
  const [crossStock, setCrossStock] = useState<{ source: "EXPLORATORY_ARCHIVE"; sha256: string; simulation: SimulationResult } | null>(null);
  const [adaptive, setAdaptive] = useState<AdaptiveResearch | null>(null);
  const [adaptiveKey, setAdaptiveKey] = useState("price_volume_60s");
  const sim = crossStock?.simulation ?? historical?.simulation;
  const [live, setLive] = useState<LiveSimulation | null>(null);
  const [stopRequested, setStopRequested] = useState(false);
  const [stopError, setStopError] = useState("");
  useEffect(() => {
    let active = true;
    const read = () => {
      void fetch("/trade/simulation", { credentials: "same-origin", cache: "no-store" })
        .then((response) => response.ok ? response.json() : null)
        .then((value: LiveSimulation | null) => {
          if (active) setLive(value?.schema_version === "alpha-grid-live-simulation-v1" ||
            value?.schema_version === "alpha-grid-live-portfolio-simulation-v2" ? value : null);
        })
        .catch(() => { if (active) setLive(null); });
    };
    read();
    const timer = window.setInterval(read, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  useEffect(() => {
    let active = true;
    void fetch("/trade/cross-equity-adaptive", { credentials: "same-origin", cache: "no-store" })
      .then((response) => response.ok ? response.json() : null)
      .then((value: AdaptiveResearch | null) => {
        if (active && value?.source === "EXPLORATORY_ARCHIVE" &&
            value.report.schema_version === "alpha-grid-cross-equity-adaptive-v2") setAdaptive(value);
      }).catch(() => undefined);
    return () => { active = false; };
  }, []);
  useEffect(() => {
    let active = true;
    const read = () => {
      void fetch("/trade/cross-stock-simulation", { credentials: "same-origin", cache: "no-store" })
        .then((response) => response.ok ? response.json() : null)
        .then((value) => { if (active) setCrossStock(value?.simulation?.schema_version === "alpha-grid-simulation-v1" ? value : null); })
        .catch(() => { if (active) setCrossStock(null); });
    };
    read();
    const timer = window.setInterval(read, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  const stop = () => {
    setStopError("");
    void fetch("/trade/simulation/stop", { method: "POST", credentials: "same-origin" })
      .then((response) => {
        if (response.ok) setStopRequested(true);
        else setStopError(`Stop request rejected (${response.status}).`);
      })
      .catch(() => setStopError("Stop service unavailable."));
  };
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
      <section className="panel" aria-label="Persistent live simulation">
        <div className="panel-head">
          <div>
            <div className="eyebrow">VERSIONED / SIMULATION_ONLY</div>
            <h2>Persistent market worker</h2>
          </div>
          <button type="button" onClick={stop} disabled={stopRequested || !live}>
            {stopRequested ? "Stopping…" : "Stop local session"}
          </button>
        </div>
        {stopError && <p role="status">{stopError}</p>}
        {live ? <>
          <p>Worker: {live.state} · Feed: {live.feed_status} · Strategy: {live.schema_version} · Fixed universe: {live.universe_count} · Durable events: {live.ledger_events}</p>
          <div className="metric-grid three">
            <div className="metric"><div className="metric-label">VIRTUAL CASH</div><div className="metric-value">{inr(live.cash_inr)}</div></div>
            <div className="metric"><div className="metric-label">VIRTUAL EQUITY</div><div className="metric-value">{inr(live.equity_inr)}</div></div>
            <div className="metric"><div className="metric-label">MAX DRAWDOWN</div><div className="metric-value">{inr(live.max_drawdown_inr)}</div></div>
          </div>
          <p>Positions: {live.positions ? live.positions.map((row) => `${row.symbol} ${row.remaining_shares} shares`).join(" · ") || "None" : live.position ? `${live.position.symbol} ${live.position.shares} shares at ask reference ${inr(live.position.ask_reference)}` : "None"}. Closed reference disposals: {live.trades.length}. Rejected decisions: {live.rejected_count}. No actual fills or broker orders.</p>
          {live.positions && <p>Shared risk budget: {inr(live.remaining_deployable_inr ?? "0")} deployable · {live.remaining_position_slots ?? 0} position slots · {inr(live.capital_buffer_at_full_deployment_inr ?? "0")} capital outside the deployment limit · {inr(live.reserved_cash_inr ?? "0")} pending-order reserve. Equity basis: {live.equity_basis}; stale positions are not marked as live P&amp;L.</p>}
          <EquityCurve values={(live.equity_curve ?? []).map((row) => ({day: row.day,
            virtual_equity_inr: row.virtual_book_equity_inr ?? row.virtual_equity_inr ?? "0",
            session_net_inr: row.session_net_inr ?? "0"}))} />
          <div className="paper-table-wrap"><table className="paper-table"><thead><tr><th>Observed instrument</th><th>Bid quote</th><th>Ask quote</th><th>Book source</th><th>Receipt to worker</th><th>Source hashes</th></tr></thead><tbody>
            {(live.observed_quotes ?? []).map((row) => <tr key={row.symbol}><td>{row.symbol}</td><td>{inr(Number(row.bid_nanos) / 1e9)}</td><td>{inr(Number(row.ask_nanos) / 1e9)}</td><td>{row.book_source ?? "LIVE_WS"}</td><td>{row.receipt_to_worker_ms ? `${Number(row.receipt_to_worker_ms).toFixed(1)} ms` : "N/A"}</td><td>{row.raw_frame_sha256?.slice(0, 12) ?? "N/A"}{row.rest_snapshot_sha256 ? ` / ${row.rest_snapshot_sha256.slice(0, 12)}` : ""}</td></tr>)}
          </tbody></table></div>
          <div className="paper-table-wrap"><table className="paper-table"><thead><tr><th>Day</th><th>Symbol</th><th>Net reference</th><th>Fees</th><th>Assumed friction</th></tr></thead><tbody>
            {live.trades.map((row, index) => <tr key={`${row.day}-${row.symbol}-${index}`}><td>{row.day}</td><td>{row.symbol}</td><td>{inr(row.net_reference_inr ?? row.net_reference ?? "0")}</td><td>{inr(row.fees.total)}</td><td>{inr(row.residual_friction_inr ?? row.assumed_friction ?? "0")}</td></tr>)}
          </tbody></table></div>
          <p>Recent decisions: {live.decisions.slice(-5).map((row) => `${row.kind}${row.symbol ? ` ${row.symbol}` : ""}${row.reason ? `: ${row.reason}` : ""}`).join(" · ") || "None"}</p>
        </> : <p>Persistent worker unavailable. Saved historical replay remains separate.</p>}
      </section>
      {adaptive && <section className="panel" aria-label="Adaptive cross-equity research">
        <div className="panel-head"><div><div className="eyebrow">EXPLORATORY_ARCHIVE · NO TRADE</div>
          <h2>Cross-equity adaptive reference replay</h2></div></div>
        <p>Archived single-position research version {adaptive.report.schema_version} · {adaptive.report.bars.toLocaleString("en-IN")} verified Dhan bars · five already examined sessions. The separate live portfolio worker supports concurrent positions under shared limits. Historical candle availability and actual fills are unverified. ML challenger: none.</p>
        <p>Original fixed rule: {adaptive.fixed_audit.coverage.evaluated_rows} of 125 stock-days evaluated; {adaptive.fixed_audit.coverage.complete_past_rows} had complete past inputs. The original selector also checked future exit-bar availability and used the same 09:59 close as trigger and entry reference.</p>
        <p>{adaptive.report.specification.slots}. {adaptive.report.specification.input_cutoff}. {adaptive.report.specification.exits}. {adaptive.report.specification.risk}.</p>
        <div className="paper-table-wrap"><table className="paper-table"><thead><tr><th>Rule / assumed delay</th><th>Trades</th><th>Gross</th><th>Fees</th><th>Friction</th><th>Net</th><th>95% session mean interval</th></tr></thead><tbody>
          <tr><td>Original fixed 60m</td><td>5</td><td colSpan={3}>Saved baseline below</td><td>{inr(Number(adaptive.report.fixed_baseline_ending_inr) - 1_000_000)}</td><td>Examined dates</td></tr>
          <tr><td>NO TRADE</td><td>0</td><td>{inr(0)}</td><td>{inr(0)}</td><td>{inr(0)}</td><td>{inr(0)}</td><td>Zero reference return</td></tr>
          {Object.entries(adaptive.report.variants).map(([key, row]) => <tr key={key}>
            <td>{row.variant.replaceAll("_", " ")} / {row.assumed_post_completion_delay_seconds}s</td>
            <td>{row.selected_opportunities} / {row.selected_sessions} sessions</td>
            <td>{inr(row.gross_reference_inr)}</td><td>{inr(row.fees_inr)}</td>
            <td>{inr(row.assumed_friction_inr)}</td><td>{inr(row.net_reference_inr)}</td>
            <td>{row.session_bootstrap_95pct_mean_net_inr?.map(inr).join(" to ") ?? "Insufficient"}</td>
          </tr>)}
        </tbody></table></div>
        <p>Paired volume gate minus price-only net: {Object.entries(adaptive.report.paired_volume_ablation).map(([delay, value]) => `${delay} ${inr(value.incremental_net_inr)} (session interval ${value.session_bootstrap_95pct_mean_incremental_inr?.map(inr).join(" to ") ?? "unavailable"})`).join(" · ")}. All variants remain net negative.</p>
        <label htmlFor="adaptive-variant">Inspect adaptive rule</label>{" "}
        <select id="adaptive-variant" value={adaptiveKey} onChange={(event) => setAdaptiveKey(event.target.value)}>
          {Object.keys(adaptive.report.variants).map((key) => <option key={key} value={key}>{key.replaceAll("_", " ")}</option>)}
        </select>
        {adaptive.report.variants[adaptiveKey] && <>
          <p>Ending virtual capital {inr(adaptive.report.variants[adaptiveKey].ending_virtual_capital_inr)} · max drawdown {inr(adaptive.report.variants[adaptiveKey].max_drawdown_inr)} · abstention slots {adaptive.report.variants[adaptiveKey].abstention_slots}. Reference closes are not fills.</p>
          <EquityCurve values={adaptive.report.variants[adaptiveKey].equity_curve} />
          <div className="paper-table-wrap"><table className="paper-table"><thead><tr><th>Session</th><th>Stock</th><th>Decision / as-of</th><th>Reference entry / exit</th><th>Exit reason</th><th>Gross</th><th>Fees + friction</th><th>Net</th></tr></thead><tbody>
            {adaptive.report.variants[adaptiveKey].trade_ledger.map((row, index) => <tr key={`${row.day}-${index}`}>
              <td>{row.day}</td><td>{row.symbol}</td><td>{row.entry_decision_time_ist} / {row.entry_asof_label_ist}</td>
              <td>{row.entry_reference_label_ist} / {row.exit_reference_label_ist}</td><td>{row.exit_reason}</td>
              <td>{inr(row.gross_reference_inr)}</td><td>{inr(Number(row.fee_components_inr.total) + Number(row.assumed_friction_inr))}</td><td>{inr(row.net_reference_inr)}</td>
            </tr>)}
          </tbody></table></div>
        </>}
        <details><summary>Inspect all 125 original stock rankings and selection reasons</summary>
          <div className="paper-table-wrap"><table className="paper-table"><thead><tr><th>Day</th><th>Rank</th><th>Stock</th><th>Breakout bps</th><th>15m bps</th><th>15m volume</th><th>Decision</th></tr></thead><tbody>
            {adaptive.fixed_audit.rows.map((row) => <tr key={`${row.day}-${row.symbol}`}><td>{row.day}</td><td>{row.all_stock_strength_rank ?? "—"}</td><td>{row.symbol} · {row.security_id}</td><td>{row.breakout_bps ?? "—"}</td><td>{row.trailing_15m_bps ?? "—"}</td><td>{row.volume_15m ?? "—"}</td><td>{row.original_decision}</td></tr>)}
          </tbody></table></div></details>
        <p>Complete as-of candidate, HOLD, EXIT, REJECT and NO TRADE log retained privately with SHA-256 {adaptive.decision_log_sha256}. No model was fitted or promoted.</p>
      </section>}
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
            {crossStock && <p>25-stock verified October archive · EXPLORATORY_ARCHIVE · artifact SHA-256 {crossStock.sha256}. All five sessions were already inspected; this is not held-out evidence.</p>}
            <div className="metric-grid three">
              <div className="metric">
                <div className="metric-label">STARTING CAPITAL</div>
                <div className="metric-value">
                  {inr(sim.virtual_starting_capital_inr)}
                </div>
              </div>
              <div className="metric">
                <div className="metric-label">ENDING REFERENCE EQUITY</div>
                <div className="metric-value">
                  {inr(sim.candidate.ending_virtual_capital_inr)}
                </div>
              </div>
              <div className="metric">
                <div className="metric-label">MAX DRAWDOWN</div>
                <div className="metric-value">
                  {inr(sim.candidate.max_drawdown_inr)}
                </div>
              </div>
            </div>
            <p>
              {sim.fixed_rule}. Assumed round-trip friction{" "}
              {sim.friction_assumption_bps} bps plus published fee estimates.
              Bar labels and completion timing remain unverified.
            </p>
            <p>
              Available genuine instruments: {sim.available_symbols.join(", ")}.
              Missing archive for {sim.missing_symbols.length} of the frozen 25
              names. No cross-stock evaluation or untouched holdout exists.
            </p>
            <p>
              Virtual positions open at session end:{" "}
              {sim.virtual_positions_end.length}. Paper and broker orders:{" "}
              {sim.paper_orders + sim.broker_orders}.
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
                      <td>
                        {(Number(row.mean_capital_utilization) * 100).toFixed(
                          2,
                        )}
                        %
                      </td>
                      <td>
                        {row.session_bootstrap_95pct_mean_net_inr
                          ? row.session_bootstrap_95pct_mean_net_inr
                              .map(inr)
                              .join(" to ")
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
              {sim.paired_candidate_minus_momentum_95pct_inr
                ?.map(inr)
                .join(" to ") ?? "Unavailable"}
              . Five already inspected sessions are insufficient for a
              performance claim.
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
                      <td colSpan={9}>
                        No rule-selected reference opportunity.
                      </td>
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
              Forced diagnostic selections:{" "}
              {sim.forced_diagnostics_excluded.length}. They are excluded from
              virtual equity, candidate statistics and the momentum comparison.
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
              . QP01 writes {sim.qp01_writes}; paper orders {sim.paper_orders};
              broker orders {sim.broker_orders}.
            </p>
          </section>
        </>
      )}
    </>
  );
}
