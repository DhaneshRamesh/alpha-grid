import { amount, latency, price, time } from "./format";
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
  const paper: WirePaperSnapshot | null | undefined = snapshot?.paper;
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
              Start the local paper replay to see simulated decisions and
              accounting. There is no broker connection.
            </p>
          </div>
        </div>
        <div className="panel">
          <div className="empty">
            Paper engine not active. The DATA_ONLY cockpit remains available.
          </div>
        </div>
      </>
    );
  const p = paper.portfolio;
  const selected = paper.recent_decisions.filter(
    (d) => !instrumentKey || d.instrument.key === instrumentKey,
  );
  const actionable = selected
    .filter(
      (d) => d.candidate_action === "Buy" || d.candidate_action === "SellExit",
    )
    .slice(-8)
    .reverse();
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
  return (
    <>
      <div className="page-heading paper-heading">
        <div>
          <div className="eyebrow">PAPER / SYNTHETIC / REPLAY</div>
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
        </div>
      </div>
      <div className="paper-warning">
        SYNTHETIC REPLAY · UNCALIBRATED SIGNAL · PROVISIONAL FEES · REAL
        PROFITABILITY UNKNOWN
      </div>
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
          label="FEES / MAX DRAWDOWN"
          value={`${price(p.fees_paid_nanos)} / ${price(p.max_drawdown_nanos)}`}
          note="Modelled / marked equity"
        />
      </div>
      <div className="paper-grid">
        <Block kicker="PORTFOLIO / 01" title="Open paper positions">
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
        <Block kicker="EXPERIENCE MEMORY / 05" title="Decisions & outcomes">
          <div className="paper-summary">
            <span>{selected.length} decisions in bounded view</span>
            <span>{paper.recent_outcomes.length} completed paper outcomes</span>
          </div>
          {actionable.length ? (
            actionable.map((decision) => (
              <DecisionRow key={decision.decision_id} decision={decision} />
            ))
          ) : (
            <div className="empty">
              No qualifying hypothesis in the retained window.
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
