import { useState } from "react";
import { amount, dateTime, latency, price, time } from "./format";
import type {
  WireEngineSnapshot,
  WirePaperDecision,
  WirePaperSnapshot,
} from "./generated/engine-snapshot";
import type { Connection } from "./stream";

function Card({
  label,
  value,
  note,
  tone = "",
}: {
  label: string;
  value: string;
  note?: string;
  tone?: string;
}) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className={`metric-value ${tone}`}>{value}</div>
      {note && <div className="metric-note">{note}</div>}
    </div>
  );
}
function Block({
  kicker,
  title,
  children,
}: {
  kicker: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="panel paper-panel">
      <div className="panel-head">
        <div>
          <div className="eyebrow">{kicker}</div>
          <h2>{title}</h2>
        </div>
      </div>
      {children}
    </section>
  );
}
function actionName(value: string): string {
  return value === "SellExit"
    ? "SELL / EXIT"
    : value === "NoTrade"
      ? "NO TRADE"
      : value.toUpperCase();
}
function DecisionRow({ decision }: { decision: WirePaperDecision }) {
  return (
    <div className="paper-decision">
      <div className="paper-decision-top">
        <span
          className={`paper-action ${decision.action === "Buy" ? "buy" : decision.action === "SellExit" ? "sell" : "muted"}`}
        >
          {actionName(decision.action)}
        </span>
        <strong>
          {decision.instrument.symbol ?? decision.instrument.security_id}
        </strong>
        <small>{time(decision.source_receive_timestamp_ns)}</small>
      </div>
      <div className="paper-decision-reason">
        {decision.reason.replaceAll(/([a-z])([A-Z])/g, "$1 $2")}
      </div>
      <div className="paper-evidence">{decision.evidence.join(" · ")}</div>
      <div className="paper-tags">
        <span>
          MODEL{" "}
          {decision.model_status
            .replaceAll(/([a-z])([A-Z])/g, "$1 $2")
            .toUpperCase()}
        </span>
        <span>
          {decision.risk_approved
            ? "RISK APPROVED FOR PAPER"
            : `RISK ${decision.risk_reasons.join(", ") || "NO ACTION"}`}
        </span>
        {decision.cross_feed_uncertain && <span>CROSS FEED UNCERTAIN</span>}
        {!decision.volume_complete && <span>VOLUME INCOMPLETE</span>}
      </div>
      <div className="paper-decision-grid">
        <div>
          Expected gross <b>{price(decision.expected_gross_edge_nanos)}</b>
        </div>
        <div>
          Expected net <b>{price(decision.expected_net_edge_nanos)}</b>
        </div>
        <div>
          Estimated cost <b>{price(decision.estimated_cost_nanos)}</b>
        </div>
        <div>
          Adverse cost <b>{price(decision.adverse_cost_nanos)}</b>
        </div>
        <div>
          Quantity <b>{amount(decision.quantity)}</b>
        </div>
        <div>
          Expires <b>{time(decision.expires_at_ns)}</b>
        </div>
      </div>
    </div>
  );
}

export default function PaperWorkspace({
  snapshot,
  connection,
  instrumentKey,
}: {
  snapshot: WireEngineSnapshot | null;
  connection: Connection;
  instrumentKey: string | null;
}) {
  const [approvalFeedback, setApprovalFeedback] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const paper: WirePaperSnapshot | null | undefined = snapshot?.paper;
  async function changeApproval(securityId: string, approved: boolean) {
    setBusyId(securityId);
    try {
      const response = await fetch("/api/paper/approval", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ security_id: securityId, approved }),
      });
      setApprovalFeedback(
        response.ok
          ? "Approval recorded in the QP01 paper ledger. Live trading remains disabled."
          : `Approval was not accepted (${response.status}).`,
      );
    } catch {
      setApprovalFeedback(
        "Approval service unavailable; no change was recorded.",
      );
    } finally {
      setBusyId(null);
    }
  }
  if (!paper)
    return (
      <>
        <div className="page-heading">
          <div>
            <div className="eyebrow">RESEARCH WORKSPACE / 08</div>
            <h1>
              Paper research<span className="heading-dot">.</span>
            </h1>
            <p>
              Decisions and simulated accounting appear only after verified Dhan
              observations and an eligible research policy are available.
            </p>
          </div>
        </div>
        <div className="panel">
          <div className="empty">
            Paper engine not active. DATA UNAVAILABLE / NO TRADE; no validated
            stock candidates or decision records are available.
          </div>
          {snapshot?.instruments.map(({ instrument }) => {
            const approved = snapshot.approved_security_ids.includes(
              instrument.security_id,
            );
            return (
              <div className="paper-decision" key={instrument.key}>
                <strong>{instrument.symbol ?? instrument.security_id}</strong>{" "}
                <span>
                  {approved
                    ? "Approved for future paper research"
                    : "Not approved"}
                </span>{" "}
                <button
                  disabled={
                    connection !== "CONNECTED" ||
                    busyId === instrument.security_id
                  }
                  onClick={() =>
                    void changeApproval(instrument.security_id, !approved)
                  }
                >
                  {approved ? "Revoke approval" : "Approve stock"}
                </button>
              </div>
            );
          })}
          {approvalFeedback && <p role="status">{approvalFeedback}</p>}
        </div>
      </>
    );
  const p = paper.portfolio;
  const selected = paper.recent_decisions.filter(
    (d) => !instrumentKey || d.instrument.key === instrumentKey,
  );
  const latest = selected.at(-1);
  const noTradeCount = selected.filter((d) => d.action === "NoTrade").length;
  const unrealized = p.positions.every((v) => v.unrealized_pnl_nanos != null)
    ? p.positions
        .reduce((sum, v) => sum + BigInt(v.unrealized_pnl_nanos ?? "0"), 0n)
        .toString()
    : null;
  const openOrders = paper.orders.filter(
    (o) =>
      ![
        "Filled",
        "Cancelled",
        "Expired",
        "Rejected",
        "UnknownUnresolved",
      ].includes(o.status),
  );
  const scanner = paper.scanner ?? [];
  const grouped = [
    ...new Map(
      scanner.map((row) => [row.instrument.key, row.instrument]),
    ).values(),
  ];
  const bestEdge = (key: string) =>
    scanner
      .filter(
        (row) =>
          row.instrument.key === key && row.reason === "HypothesisSignal",
      )
      .reduce<bigint | null>((best, row) => {
        const edge =
          row.robust_net_edge_nanos == null
            ? null
            : BigInt(row.robust_net_edge_nanos);
        return edge == null ? best : best == null || edge > best ? edge : best;
      }, null);
  grouped.sort((a, b) => {
    const left = bestEdge(a.key);
    const right = bestEdge(b.key);
    if (left == null || right == null)
      return left == null
        ? right == null
          ? a.key.localeCompare(b.key)
          : 1
        : -1;
    return left === right ? a.key.localeCompare(b.key) : left > right ? -1 : 1;
  });
  async function haltPaper() {
    try {
      const response = await fetch("/api/demo/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "halt_paper" }),
      });
      setApprovalFeedback(
        response.ok
          ? "Paper halt queued for the QP01 ledger; await HALTED status before relying on it."
          : `Paper halt was not accepted (${response.status}).`,
      );
    } catch {
      setApprovalFeedback(
        "Paper halt service unavailable; status is unchanged.",
      );
    }
  }
  return (
    <>
      <div className="page-heading paper-heading">
        <div>
          <div className="eyebrow">PAPER RESEARCH / SIMULATED FILLS</div>
          <h1>
            Decision &amp; experience<span className="heading-dot">.</span>
          </h1>
          <p>
            Research hypotheses, conservative simulated fills and after-cost
            outcomes. No real orders or validated alpha.
          </p>
        </div>
        <div className="paper-mode">
          <span>RESEARCH STATUS</span>
          <strong>{p.halted ? "HALTED" : "PAPER ONLY"}</strong>
          <small>
            {paper.ledger_healthy
              ? "Paper ledger durable"
              : "Paper ledger failure"}
          </small>
          {paper.mode === "PAPER_REPLAY_APPROVED_RESEARCH" && !p.halted && (
            <button onClick={() => void haltPaper()}>Halt paper entries</button>
          )}
        </div>
      </div>
      <div className="paper-warning">
        PAPER FILLS SIMULATED · MODEL ELIGIBILITY UNVERIFIED · PROVISIONAL FEES
        · REAL PROFITABILITY UNKNOWN
      </div>
      <Block
        kicker="STOCK SCANNER / APPROVAL"
        title="Approved research candidates"
      >
        <div className="paper-foot">
          {paper.mode}. Ranked only when a calibrated, cost-adjusted horizon has
          positive robust edge. Source times come from QJ01 receive timestamps;
          model as-of time is unavailable. Candle prices are never treated as
          attainable fills.
        </div>
        {grouped.length ? (
          grouped.map((instrument) => {
            const approved =
              snapshot?.approved_security_ids.includes(
                instrument.security_id,
              ) ?? false;
            return (
              <div className="paper-decision" key={instrument.key}>
                <div className="paper-decision-top">
                  <strong>{instrument.symbol ?? instrument.security_id}</strong>
                  <span>
                    {approved
                      ? "APPROVED FOR PAPER EVALUATION"
                      : "APPROVAL REQUIRED"}
                  </span>
                  {paper.mode === "PAPER_REPLAY_APPROVED_RESEARCH" && (
                    <button
                      disabled={busyId === instrument.security_id}
                      onClick={() =>
                        void changeApproval(instrument.security_id, !approved)
                      }
                    >
                      {approved ? "Revoke approval" : "Approve stock"}
                    </button>
                  )}
                </div>
                <div className="paper-table-wrap">
                  <table className="paper-table">
                    <thead>
                      <tr>
                        <th>Horizon</th>
                        <th>Decision</th>
                        <th>Robust net edge</th>
                        <th>Adverse cost</th>
                        <th>Uncertainty</th>
                        <th>Source time</th>
                      </tr>
                    </thead>
                    <tbody>
                      {scanner
                        .filter((row) => row.instrument.key === instrument.key)
                        .map((row) => (
                          <tr key={row.horizon}>
                            <td>{row.horizon}</td>
                            <td>
                              {row.reason === "HypothesisSignal"
                                ? "PRE-RISK PAPER CANDIDATE"
                                : `NO TRADE · ${row.reason}`}
                            </td>
                            <td>{price(row.robust_net_edge_nanos)}</td>
                            <td>{price(row.adverse_cost_nanos)}</td>
                            <td>{price(row.uncertainty_nanos)}</td>
                            <td>{dateTime(row.source_receive_timestamp_ns)}</td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })
        ) : (
          <div className="empty">
            No validated scanner observations yet. NO TRADE is the default.
          </div>
        )}
        {approvalFeedback && (
          <div className="paper-foot" role="status">
            {approvalFeedback}
          </div>
        )}
      </Block>
      <div className="metric-grid paper-metrics">
        <Card
          label="STARTING CAPITAL"
          value={price(p.starting_capital_nanos)}
          note="Hypothetical INR cash"
        />
        <Card
          label="AVAILABLE PAPER CASH"
          value={price(p.available_cash_nanos)}
          note={`${price(p.reserved_cash_nanos)} reserved`}
        />
        <Card
          label="REALIZED PAPER P&L"
          value={price(p.realized_pnl_nanos)}
          tone={BigInt(p.realized_pnl_nanos) < 0n ? "negative" : ""}
          note="After modelled fill fees"
        />
        <Card
          label="UNREALIZED PAPER P&L"
          value={price(unrealized)}
          tone={unrealized && BigInt(unrealized) < 0n ? "negative" : ""}
          note="Bid liquidation, estimated exit fees"
        />
        <Card
          label="SIMULATED EXPOSURE"
          value={price(p.exposure_nanos)}
          note={`${p.positions.length} open positions`}
        />
        <Card
          label="RISK UTILIZATION"
          value={`${Number((BigInt(p.exposure_nanos) * 10_000n) / BigInt(paper.risk_limits.max_deployed_nanos)) / 100}%`}
          note="Of existing maximum deployed capital"
        />
        <Card
          label="FEES / MAX DRAWDOWN"
          value={`${price(p.fees_paid_nanos)} / ${price(p.max_drawdown_nanos)}`}
          note="Modelled / marked equity"
        />
      </div>
      <div className="paper-grid">
        <Block kicker="TRADES & POSITIONS / 01" title="Open paper positions">
          {p.positions.length ? (
            <div className="paper-table-wrap">
              <table className="paper-table">
                <thead>
                  <tr>
                    <th>Instrument</th>
                    <th>Qty</th>
                    <th>Cost basis</th>
                    <th>Bid mark</th>
                    <th>Unrealized</th>
                  </tr>
                </thead>
                <tbody>
                  {p.positions.map((pos) => (
                    <tr key={pos.instrument.key}>
                      <td>
                        <strong>
                          {pos.instrument.symbol ?? pos.instrument.security_id}
                        </strong>
                      </td>
                      <td>{amount(pos.quantity)}</td>
                      <td>{price(pos.cost_basis_nanos)}</td>
                      <td>{price(pos.last_bid_mark_nanos)}</td>
                      <td>{price(pos.unrealized_pnl_nanos)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">
              No open paper position. Abstention is a valid decision.
            </div>
          )}
          <div className="paper-foot">
            Unrealized marks assume sale at the current displayed bid and
            estimated delivery exit charges. A disconnect never closes a
            position.
          </div>
        </Block>
        <Block kicker="DECISION INTELLIGENCE / 02" title="Current evaluation">
          {latest ? (
            <DecisionRow decision={latest} />
          ) : (
            <div className="empty">Awaiting an eligible book event.</div>
          )}
          <div className="paper-foot">
            {noTradeCount} NO TRADE records retained for this instrument.{" "}
            {paper.policy_version}; {paper.fee_schedule_version}.
          </div>
        </Block>
      </div>
      <div className="paper-grid">
        <Block kicker="PAPER OMS / 03" title="Order lifecycle">
          <div className="paper-summary">
            <span>{openOrders.length} working</span>
            <span>
              {paper.recent_fills.length} recent partial or complete fills
            </span>
          </div>
          {paper.orders.length ? (
            <div className="paper-table-wrap">
              <table className="paper-table">
                <thead>
                  <tr>
                    <th>Side</th>
                    <th>Instrument</th>
                    <th>State</th>
                    <th>Filled / total</th>
                    <th>Age / duration</th>
                  </tr>
                </thead>
                <tbody>
                  {paper.orders.slice(0, 12).map((order) => (
                    <tr key={order.order_id}>
                      <td>{order.side.toUpperCase()}</td>
                      <td>
                        {order.instrument.symbol ??
                          order.instrument.security_id}
                      </td>
                      <td>
                        <span className="paper-state">
                          {order.status.replaceAll(/([a-z])([A-Z])/g, "$1 $2")}
                        </span>
                      </td>
                      <td>
                        {amount(order.filled_quantity)} /{" "}
                        {amount(order.quantity)}
                      </td>
                      <td>
                        {snapshot?.latest_data_timestamp_ns
                          ? latency(
                              (
                                BigInt(
                                  [
                                    "Filled",
                                    "Cancelled",
                                    "Expired",
                                    "Rejected",
                                    "UnknownUnresolved",
                                  ].includes(order.status)
                                    ? order.last_transition_ns
                                    : snapshot.latest_data_timestamp_ns,
                                ) - BigInt(order.created_at_ns)
                              ).toString(),
                            )
                          : "N/A"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">No paper orders submitted.</div>
          )}
          <div className="paper-foot">
            Base fills wait one event, cross the displayed spread and use at
            most 10% of the current top-level quantity. Queue position is
            unknown.
          </div>
          {paper.recent_fills.length > 0 && (
            <div className="paper-table-wrap">
              <table className="paper-table">
                <thead>
                  <tr>
                    <th>Simulated paper fill</th>
                    <th>Price</th>
                    <th>Actual modelled fee</th>
                    <th>Assumptions</th>
                  </tr>
                </thead>
                <tbody>
                  {paper.recent_fills
                    .slice(-10)
                    .reverse()
                    .map((fill) => (
                      <tr key={fill.fill_id}>
                        <td>
                          {fill.side}{" "}
                          {fill.instrument.symbol ??
                            fill.instrument.security_id}
                        </td>
                        <td>{price(fill.price_nanos)}</td>
                        <td>{price(fill.fees_nanos)}</td>
                        <td>{fill.assumptions.join("; ")}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
        </Block>
        <Block kicker="RISK CONTROL / 04" title="Hard limits">
          <div className="paper-risk">
            <div>
              <span>Engine state</span>
              <b>
                {p.halted
                  ? `HALTED · ${p.halt_reason ?? "UNKNOWN"}`
                  : "ELIGIBLE FOR PAPER CHECKS"}
              </b>
            </div>
            <div>
              <span>Max capital deployed</span>
              <b>{price(paper.risk_limits.max_deployed_nanos)}</b>
            </div>
            <div>
              <span>Per-instrument notional</span>
              <b>{price(paper.risk_limits.max_instrument_notional_nanos)}</b>
            </div>
            <div>
              <span>Max concurrent positions</span>
              <b>{paper.risk_limits.max_positions}</b>
            </div>
            <div>
              <span>Loss / decision</span>
              <b>{price(paper.risk_limits.max_loss_per_decision_nanos)}</b>
            </div>
            <div>
              <span>Session loss</span>
              <b>{price(paper.risk_limits.max_session_loss_nanos)}</b>
            </div>
            <div>
              <span>Remaining loss allowance</span>
              <b>{price(p.remaining_session_loss_nanos)}</b>
            </div>
            <div>
              <span>Spread / slippage ceilings</span>
              <b>
                {paper.risk_limits.max_spread_bps} /{" "}
                {paper.risk_limits.max_slippage_bps} bps
              </b>
            </div>
            <div>
              <span>Maximum data age</span>
              <b>{latency(paper.risk_limits.max_market_age_ns)}</b>
            </div>
          </div>
          <div className="paper-foot">
            No leverage or short sales. The hypothesis cannot override a failed
            rule.
          </div>
        </Block>
      </div>
      <div className="paper-grid">
        <Block kicker="EVIDENCE / AUDIT / 05" title="Decisions & abstentions">
          <div className="paper-summary">
            <span>{selected.length} decisions in bounded view</span>
            <span>{paper.recent_outcomes.length} completed paper outcomes</span>
          </div>
          {selected.length ? (
            [...selected]
              .reverse()
              .map((decision) => (
                <DecisionRow key={decision.decision_id} decision={decision} />
              ))
          ) : (
            <div className="empty">
              No paper decisions in the retained window.
            </div>
          )}
          <div className="paper-foot">
            Prediction error remains N/A while the return model is untrained. A
            profitable paper path does not validate the hypothesis.
          </div>
        </Block>
        <Block
          kicker="OUTCOME ATTRIBUTION / 06"
          title="After-cost paper results"
        >
          {paper.recent_outcomes.length ? (
            [...paper.recent_outcomes]
              .reverse()
              .slice(0, 10)
              .map((outcome) => (
                <div className="paper-outcome" key={outcome.decision_id}>
                  <div>
                    <strong>{price(outcome.net_pnl_nanos)}</strong>
                    <span>{outcome.categories.join(", ")}</span>
                  </div>
                  <small>
                    MFE {price(outcome.max_favorable_excursion_nanos)} · MAE{" "}
                    {price(outcome.max_adverse_excursion_nanos)} · Held{" "}
                    {latency(outcome.holding_time_ns)}
                  </small>
                  <p>{outcome.explanation}</p>
                </div>
              ))
          ) : (
            <div className="empty">
              No position has completed a simulated exit.
            </div>
          )}
          <div className="paper-foot">
            Model performance by regime: N/A. No regime model or independent
            out-of-sample validation exists.
          </div>
        </Block>
      </div>
      <Block kicker="SYSTEM RELIABILITY / 07" title="Research integrity">
        <div className="paper-reliability">
          <span>UI {connection}</span>
          <span>
            Market journal {snapshot?.journal_blocked ? "BLOCKED" : "READABLE"}
          </span>
          <span>
            Paper ledger {paper.ledger_healthy ? "HEALTHY" : "FAILED"}
          </span>
          <span>
            Recovered tail {amount(paper.ledger_recovered_tail_bytes)} bytes
          </span>
          <span>Source record {amount(paper.last_source_record_index)}</span>
          <span>Fee schedule PROVISIONAL</span>
        </div>
        <div className="paper-foot">
          The QJ01 market journal and QP01 paper ledger are separate.
          Presentation updates may coalesce; paper transitions are fsynced
          before publication.
        </div>
      </Block>
    </>
  );
}
