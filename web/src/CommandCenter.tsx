import { useEffect, useMemo, useState } from "react";
import type { WireEngineSnapshot, WireInstrumentSnapshot } from "./generated/engine-snapshot";
import type { RestSnapshot } from "./TradingWorkspace";
import { amount, dateTime, price, time } from "./format";
import StorageSessionPanel from "./StorageSessionPanel";

export const FOCUS = [
  "2885",   // RELIANCE
  "1333",   // HDFCBANK
  "4963",   // ICICIBANK
  "1594",   // INFY
  "11536",  // TCS
  "10604",  // BHARTIARTL
  "3045",   // SBIN
  "5900",   // AXISBANK
  "11483",  // LT
  "5097",   // ETERNAL
];

export type Position = {
  symbol: string;
  remaining_shares: number;
  ask_reference_inr: string;
  day?: string;
  decision_slot?: string;
  risk_bound_inr?: string;
  reduced?: boolean;
  sequence?: number;
};

export type Decision = {
  kind: string;
  symbol?: string;
  reason?: string;
  day?: string;
  decision_slot?: string;
  sequence?: number;
  observation_id?: string;
  hash?: string;
  observed_at_ns?: number | string;
};

export type LivePortfolio = {
  schema_version: string;
  state: string;
  feed_status: string;
  capital_start_inr?: string;
  cash_inr: string;
  equity_inr: string;
  equity_basis?: string;
  max_drawdown_inr: string;
  remaining_deployable_inr?: string;
  remaining_position_slots?: number;
  open_risk_bound_inr?: string;
  risk_limits?: {
    max_session_loss_inr: string;
    max_deployed_inr: string;
    max_instrument_inr?: string;
    max_spread_bps?: string;
    max_market_age_ns?: string;
    max_loss_per_decision_inr?: string;
  };
  positions?: Position[];
  decisions: Decision[];
  rejected_count: number;
  ledger_events: number;
  observed_quotes?: { symbol: string; receipt_to_worker_ms?: string }[];
};

export const money = (value?: string | number | null) =>
  value == null || !Number.isFinite(Number(value))
    ? "N/A"
    : `₹${Number(value).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export function fresh(received?: string | null, connected?: boolean): boolean {
  if (!received || !connected) return false;
  try {
    const age = BigInt(Date.now()) * 1_000_000n - BigInt(received);
    return age >= 0n && age <= 5_000_000_000n;
  } catch {
    return false;
  }
}

/**
 * Authoritative CAS Phase 1 eligibility from dated NSE MII security evidence (docs/cross-stock-universe-2026-11-02.json).
 * 1 = Derivative-enabled cash equity: continuous trading ends 15:15 IST, CAS runs 15:15–15:35 IST.
 * 0 = Non-CAS cash equity: continuous trading continues until 15:30 IST.
 * undefined / null = Applicability unverified; display UNKNOWN.
 */
export const CAS_ELIGIBILITY_MAP: Record<string, number> = {
  "1333": 1, // HDFCBANK
  "2885": 1, // RELIANCE
  "1594": 1, // INFY
  "4963": 1, // ICICIBANK
  "10604": 1, // BHARTIARTL
  "19585": 1, // BSE
  "11536": 1, // TCS
  "29124": 0, // PCJEWELLER (Non-CAS: continuous to 15:30)
  "1922": 1, // KOTAKBANK
  "5900": 1, // AXISBANK
  "5097": 1, // ETERNAL
  "3045": 1, // SBIN
  "317": 1, // BAJFINANCE
  "11483": 1, // LT
  "766325": 0, // MONEYVIEW (Non-CAS: continuous to 15:30)
  "2031": 1, // M&M
  "17433": 0, // NETWEB (Non-CAS: continuous to 15:30)
  "18520": 0, // CUPID (Non-CAS: continuous to 15:30)
  "13538": 1, // TECHM
  "438": 1, // BHEL
  "10940": 1, // DIVISLAB
  "4306": 1, // SHRIRAMFIN
  "7229": 1, // HCLTECH
  "31181": 1, // MCX
  "10999": 1, // MARUTI
};

export function getNseSessionStatus(now = new Date()) {
  const utcMs = now.getTime() + now.getTimezoneOffset() * 60000;
  const istDate = new Date(utcMs + 5.5 * 3600000);
  const day = istDate.getDay(); // 0 = Sun, 6 = Sat
  const hours = istDate.getHours();
  const minutes = istDate.getMinutes();
  const timeInMinutes = hours * 60 + minutes;
  const preOpenTime = 9 * 60; // 09:00 IST
  const openTime = 9 * 60 + 15; // 09:15 IST
  // Under August 2026 Phase 1 closing-auction rules:
  // Continuous trading ends at 15:15 IST for CAS equities, 15:30 IST for non-CAS equities
  const continuousCloseTime = 15 * 60 + 15; // 15:15 IST
  const nonCasCloseTime = 15 * 60 + 30; // 15:30 IST
  const casCloseTime = 15 * 60 + 35; // 15:35 IST

  const isWeekday = day >= 1 && day <= 5;

  if (isWeekday && timeInMinutes >= openTime && timeInMinutes < continuousCloseTime) {
    return { status: "OPEN", label: "NSE CONTINUOUS", sub: "Continuous trading session (09:15–15:15 IST)" };
  } else if (isWeekday && timeInMinutes >= continuousCloseTime && timeInMinutes < nonCasCloseTime) {
    return { status: "CAS", label: "NSE DUAL / CAS", sub: "CAS equities in auction (15:15–15:35); non-CAS continuous to 15:30" };
  } else if (isWeekday && timeInMinutes >= nonCasCloseTime && timeInMinutes <= casCloseTime) {
    return { status: "CAS", label: "NSE CAS AUCTION", sub: "Phase 1 closing auction concluding (15:15–15:35 IST)" };
  } else if (isWeekday && timeInMinutes >= preOpenTime && timeInMinutes < openTime) {
    return { status: "PRE_OPEN", label: "NSE PRE-OPEN", sub: "Pre-market session opening continuous at 09:15 IST" };
  } else {
    return { status: "CLOSED", label: "NSE CLOSED", sub: "Off-hours observation & simulation mode" };
  }
}

export function getInstrumentSessionStatus(casFlag?: number | null, now = new Date()) {
  const utcMs = now.getTime() + now.getTimezoneOffset() * 60000;
  const istDate = new Date(utcMs + 5.5 * 3600000);
  const day = istDate.getDay();
  const hours = istDate.getHours();
  const minutes = istDate.getMinutes();
  const timeInMinutes = hours * 60 + minutes;

  const preOpenTime = 9 * 60; // 09:00 IST
  const openTime = 9 * 60 + 15; // 09:15 IST
  const casStartTime = 15 * 60 + 15; // 15:15 IST
  const nonCasCloseTime = 15 * 60 + 30; // 15:30 IST
  const casCloseTime = 15 * 60 + 35; // 15:35 IST

  const isWeekday = day >= 1 && day <= 5;
  if (!isWeekday) {
    return { status: "CLOSED", label: "CLOSED", detail: "Weekend" };
  }

  if (timeInMinutes < preOpenTime || timeInMinutes > casCloseTime) {
    return { status: "CLOSED", label: "CLOSED", detail: "Off-hours" };
  }

  if (timeInMinutes >= preOpenTime && timeInMinutes < openTime) {
    return { status: "PRE_OPEN", label: "PRE-OPEN", detail: "Pre-market opening at 09:15 IST" };
  }

  // Continuous market for all instruments
  if (timeInMinutes >= openTime && timeInMinutes < casStartTime) {
    return { status: "OPEN", label: "CONTINUOUS", detail: "Continuous trading (09:15–15:15 IST)" };
  }

  // 15:15 to 15:30 IST: Split Phase
  if (timeInMinutes >= casStartTime && timeInMinutes < nonCasCloseTime) {
    if (casFlag === 1) {
      return { status: "CAS", label: "CAS AUCTION", detail: "August 2026 Phase 1 closing auction (15:15–15:35 IST)" };
    } else if (casFlag === 0) {
      return { status: "OPEN", label: "CONTINUOUS", detail: "Non-CAS continuous trading until 15:30 IST" };
    } else {
      return { status: "UNKNOWN", label: "UNKNOWN", detail: "CAS applicability unverified" };
    }
  }

  // 15:30 to 15:35 IST: Non-CAS closed, CAS auction continues
  if (timeInMinutes >= nonCasCloseTime && timeInMinutes <= casCloseTime) {
    if (casFlag === 1) {
      return { status: "CAS", label: "CAS AUCTION", detail: "August 2026 Phase 1 closing auction (ends 15:35 IST)" };
    } else if (casFlag === 0) {
      return { status: "CLOSED", label: "CLOSED", detail: "Non-CAS session closed at 15:30 IST" };
    } else {
      return { status: "UNKNOWN", label: "UNKNOWN", detail: "CAS applicability unverified" };
    }
  }

  return { status: "CLOSED", label: "CLOSED", detail: "Market closed" };
}

export function priceChangePct(
  priceNanos?: string | null,
  prevCloseNanos?: string | null,
  restChange?: number | null,
  restPrice?: number | null
): number | null {
  if (priceNanos && prevCloseNanos) {
    const p = Number(BigInt(priceNanos)) / 1e9;
    const base = Number(BigInt(prevCloseNanos)) / 1e9;
    if (base > 0) return ((p - base) / base) * 100;
  }
  if (restChange != null && restPrice != null && restPrice > 0) {
    const prev = restPrice - restChange;
    if (prev > 0) return (restChange / prev) * 100;
  }
  return null;
}

export function deltaText(
  priceNanos?: string | null,
  prevCloseNanos?: string | null,
  restChange?: number | null,
  restPrice?: number | null
): string {
  if (priceNanos && prevCloseNanos) {
    const diff = Number(BigInt(priceNanos) - BigInt(prevCloseNanos)) / 1e9;
    const base = Number(BigInt(prevCloseNanos)) / 1e9;
    const pct = base > 0 ? (diff / base) * 100 : 0;
    const sign = diff >= 0 ? "+" : "";
    return `${sign}₹${diff.toFixed(2)} (${sign}${pct.toFixed(2)}%)`;
  }
  if (restChange != null && restPrice != null) {
    const prev = restPrice - restChange;
    const pct = prev > 0 ? (restChange / prev) * 100 : 0;
    const sign = restChange >= 0 ? "+" : "";
    return `${sign}₹${restChange.toFixed(2)} (${sign}${pct.toFixed(2)}%)`;
  }
  return "N/A";
}

/* -------------------------------------------------------------------------
   SVG Sparkline Component
   Renders smooth rolling price movements without external chart libraries
   ------------------------------------------------------------------------- */
export function Sparkline({ points, width = 80, height = 26 }: { points: number[]; width?: number; height?: number }) {
  if (!points || points.length < 2) {
    return (
      <svg width={width} height={height} className="sparkline sparkline-empty" aria-hidden="true">
        <line x1={0} y1={height / 2} x2={width} y2={height / 2} stroke="#334155" strokeDasharray="2,2" strokeWidth={1} />
      </svg>
    );
  }
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const padding = 3;
  const usableHeight = height - padding * 2;

  const coords = points.map((p, i) => {
    const x = (i / (points.length - 1)) * width;
    const y = height - padding - ((p - min) / range) * usableHeight;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const pathD = `M ${coords.join(" L ")}`;
  const areaD = `M ${coords[0]} L ${coords.join(" L ")} L ${width},${height} L 0,${height} Z`;
  const isUp = points[points.length - 1] >= points[0];
  const strokeColor = isUp ? "#2DD4BF" : "#FB7185";

  return (
    <svg width={width} height={height} className="sparkline" viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <defs>
        <linearGradient id={`spark-grad-${strokeColor.replace("#", "")}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={strokeColor} stopOpacity="0.3" />
          <stop offset="100%" stopColor={strokeColor} stopOpacity="0.0" />
        </linearGradient>
      </defs>
      <path d={areaD} fill={`url(#spark-grad-${strokeColor.replace("#", "")})`} />
      <path d={pathD} fill="none" stroke={strokeColor} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/* -------------------------------------------------------------------------
   2D Market Radar Component
   Plots real price change % vs. quoted spread (bps) for the focus universe
   ------------------------------------------------------------------------- */
export function MarketRadar({
  rows,
  selectedId,
  onSelect,
}: {
  rows: Array<{
    id: string;
    symbol: string;
    priceNum: number | null;
    changePct: number | null;
    spreadBps: number | null;
    volume: number | null;
    live: boolean;
  }>;
  selectedId?: string;
  onSelect: (id: string) => void;
}) {
  const width = 500;
  const height = 280;
  const paddingX = 40;
  const paddingY = 30;
  const plotWidth = width - paddingX * 2;
  const plotHeight = height - paddingY * 2;

  // Domain: X is change % [-2.5%, +2.5%], Y is spread [0 bps to 25 bps]
  const minX = -2.5;
  const maxX = 2.5;
  const minY = 0;
  const maxY = 25;

  const validPoints = rows
    .map((r) => {
      if (r.changePct == null || r.spreadBps == null) return null;
      const clampedX = Math.max(minX, Math.min(maxX, r.changePct));
      const clampedY = Math.max(minY, Math.min(maxY, r.spreadBps));
      const cx = paddingX + ((clampedX - minX) / (maxX - minX)) * plotWidth;
      // Invert Y: 0 bps (tight spread) at top, 25 bps (wide spread) at bottom
      const cy = paddingY + ((clampedY - minY) / (maxY - minY)) * plotHeight;
      const rSize = r.volume ? Math.max(7, Math.min(18, 7 + Math.log10(r.volume + 1) * 2)) : 8;
      const isSelected = r.id === selectedId;
      const color = r.spreadBps <= 5 ? "#2DD4BF" : r.spreadBps <= 15 ? "#38BDF8" : "#FB7185";
      return { ...r, cx, cy, rSize, isSelected, color };
    })
    .filter(Boolean) as Array<typeof rows[0] & { cx: number; cy: number; rSize: number; isSelected: boolean; color: string }>;

  const unmapped = rows.filter((r) => r.changePct == null || r.spreadBps == null);

  return (
    <div className="radar-box" aria-label="Interactive 2D Market Radar">
      <div className="radar-header">
        <div>
          <div className="eyebrow">MARKET RADAR · 2D SCATTER</div>
          <h3>Liquidity vs. Relative Movement</h3>
        </div>
        <span className="radar-legend">
          <i style={{ background: "#2DD4BF" }} /> Tight &lt;5bps
          <i style={{ background: "#38BDF8" }} /> Medium
          <i style={{ background: "#FB7185" }} /> Wide &gt;15bps
        </span>
      </div>

      <svg viewBox={`0 0 ${width} ${height}`} className="radar-svg" role="img" aria-label="Market scatter plot">
        {/* Radar Concentric Rings for strategic gaming feel */}
        <circle cx={width / 2} cy={height / 2} r={60} fill="none" stroke="#1E3A5F" strokeDasharray="3 3" opacity={0.4} />
        <circle cx={width / 2} cy={height / 2} r={120} fill="none" stroke="#1E3A5F" strokeDasharray="3 3" opacity={0.3} />

        {/* Axes */}
        {/* Center line (0% change) */}
        <line x1={width / 2} y1={paddingY} x2={width / 2} y2={height - paddingY} stroke="#334155" strokeWidth={1.5} />
        {/* Horizontal grid lines */}
        {[0, 5, 10, 15, 20, 25].map((bps) => {
          const y = paddingY + ((bps - minY) / (maxY - minY)) * plotHeight;
          return (
            <g key={bps}>
              <line x1={paddingX} y1={y} x2={width - paddingX} y2={y} stroke="#1E293B" strokeWidth={1} strokeDasharray="2 2" />
              <text x={paddingX - 6} y={y + 3} fill="#64748B" fontSize="9" textAnchor="end" fontFamily="ui-monospace, monospace">
                {bps}b
              </text>
            </g>
          );
        })}
        {/* X labels */}
        {[-2, -1, 0, 1, 2].map((pct) => {
          const x = paddingX + ((pct - minX) / (maxX - minX)) * plotWidth;
          return (
            <text key={pct} x={x} y={height - paddingY + 14} fill="#64748B" fontSize="9" textAnchor="middle" fontFamily="ui-monospace, monospace">
              {pct > 0 ? `+${pct}%` : `${pct}%`}
            </text>
          );
        })}

        {/* Data points */}
        {validPoints.map((pt) => (
          <g
            key={pt.id}
            className={`radar-node ${pt.isSelected ? "selected" : ""}`}
            onClick={() => onSelect(pt.id)}
            style={{ cursor: "pointer" }}
            tabIndex={0}
            role="button"
            aria-label={`${pt.symbol}: change ${pt.changePct?.toFixed(2)}%, spread ${pt.spreadBps?.toFixed(1)} bps`}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") onSelect(pt.id);
            }}
          >
            {pt.isSelected && (
              <circle cx={pt.cx} cy={pt.cy} r={pt.rSize + 5} fill="none" stroke="#38BDF8" strokeWidth={2} className="pulse-ring" />
            )}
            <circle cx={pt.cx} cy={pt.cy} r={pt.rSize} fill={pt.color} fillOpacity={0.75} stroke={pt.color} strokeWidth={1.5} />
            <text
              x={pt.cx}
              y={pt.cy - pt.rSize - 3}
              fill="#E2E8F0"
              fontSize="10"
              fontWeight="700"
              textAnchor="middle"
              fontFamily="ui-monospace, monospace"
            >
              {pt.symbol}
            </text>
          </g>
        ))}
      </svg>

      {unmapped.length > 0 && (
        <div className="radar-unmapped">
          <small>Awaiting complete 2D depth for: {unmapped.map((u) => u.symbol).join(", ")} (not invented)</small>
        </div>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------
   Opportunity Leaderboard Component
   Displays engine candidates and recorded evidence with strict truthfulness
   ------------------------------------------------------------------------- */
export function OpportunityLeaderboard({
  rows,
  portfolio,
  rawPending,
}: {
  rows: Array<{
    id: string;
    symbol: string;
    item?: WireInstrumentSnapshot;
    saved?: { last_price: number };
    received?: string | null;
    live: boolean;
    state: string;
    decision?: Decision;
    spreadBps?: number | null;
  }>;
  portfolio: LivePortfolio | null;
  rawPending: boolean;
}) {
  const slotsRemaining = portfolio?.remaining_position_slots ?? 3;
  const deployable = portfolio?.remaining_deployable_inr ?? "15000";

  return (
    <div className="leaderboard-box" aria-label="Opportunity Leaderboard">
      <div className="leaderboard-header">
        <div>
          <div className="eyebrow">OPPORTUNITY LEADERBOARD</div>
          <h3>Engine Candidate Status</h3>
        </div>
        <span className="chip subtle">
          Slots: {slotsRemaining}/3 avail · Deployable: {money(deployable)}
        </span>
      </div>

      <p className="leaderboard-sub">
        {rawPending
          ? "No predictive rank invented. Genuine Full depth is visible, but the decision worker requires verification."
          : "Authoritative candidate evaluations recorded by the portfolio worker."}
      </p>

      <div className="leaderboard-list">
        {rows.slice(0, 7).map((row, idx) => {
          const statusBadge = row.state.includes("HOLD")
            ? "holding"
            : row.state.includes("REDUCE")
            ? "reducing"
            : row.state === "REJECTED"
            ? "rejected"
            : row.state.includes("WATCH")
            ? "watching"
            : "stale";

          return (
            <div className={`leaderboard-row ${statusBadge}`} key={row.id}>
              <div className="row-rank">#{idx + 1}</div>
              <div className="row-sym">
                <strong>{row.symbol}</strong>
                <small>{row.id}</small>
              </div>
              <div className="row-price">
                <span>{row.live ? price(row.item?.market?.price_nanos) : row.saved ? `₹${row.saved.last_price}` : "N/A"}</span>
                <small>{row.spreadBps != null ? `${row.spreadBps.toFixed(1)} bps` : "Spread N/A"}</small>
              </div>
              <div className="row-status">
                <span className={`status-badge ${statusBadge}`}>{row.state}</span>
                <small>
                  {row.decision?.reason ?? (rawPending ? "Validation pending; raw observation" : "No candidate rank")}
                </small>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------
   Active Missions (Virtual Positions) Component
   ------------------------------------------------------------------------- */
export function ActiveMissions({
  positions,
  onSimulator,
  rawPending,
  connected,
}: {
  positions: Position[];
  onSimulator: () => void;
  rawPending?: boolean;
  connected?: boolean;
}) {
  return (
    <section className="command-panel missions-panel" aria-label="Active Missions">
      <div className="panel-top">
        <div>
          <div className="eyebrow">ACTIVE MISSIONS · VIRTUAL PORTFOLIO</div>
          <h2>{`Open Positions (${positions.length} / 3)`}</h2>
        </div>
        <button type="button" className="command-link" onClick={onSimulator}>
          Open Virtual Ledger →
        </button>
      </div>

      {positions.length === 0 ? (
        <div className="mission-empty-state">
          <div className="shield-symbol" aria-hidden="true">🛡️</div>
          <div>
            <strong>
              {rawPending
                ? "Zero Virtual Positions · Decisions Paused"
                : connected === false
                ? "Zero Virtual Positions · Feed Inactive"
                : "Zero Virtual Positions · 100% Cash Allocation"}
            </strong>
            <p>
              {rawPending
                ? "Virtual portfolio decisions are paused while dated MII security evidence or preflight validation is unresolved. Zero positions reflects regulatory and integrity constraints, not an active market timing success."
                : connected === false
                ? "Feed gateway is disconnected. Virtual order evaluation is idle."
                : "No candidate instruments currently meet virtual entry rules. 3 position slots remain available."}
            </p>
          </div>
        </div>
      ) : (
        <div className="mission-list">
          {positions.map((pos) => (
            <div className="mission-card" key={`${pos.symbol}-${pos.sequence ?? pos.day}`}>
              <div className="mission-card-head">
                <strong>{pos.symbol}</strong>
                <span className={`chip ${pos.reduced ? "alert" : "favorable"}`}>
                  {pos.reduced ? "REDUCING" : "HOLDING"} · {pos.remaining_shares} shares
                </span>
              </div>
              <div className="mission-card-body">
                <div>
                  <small>Ask Ref Entry</small>
                  <b>{money(pos.ask_reference_inr)}</b>
                </div>
                <div>
                  <small>Open Risk Bound</small>
                  <b>{money(pos.risk_bound_inr)}</b>
                </div>
                <div>
                  <small>Decision Slot</small>
                  <span>{pos.decision_slot ?? pos.day ?? "time unrecorded"}</span>
                </div>
              </div>
              <small className="mission-note">Cost basis accounting; no executable fill or live marked P&amp;L.</small>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

/* -------------------------------------------------------------------------
   Risk Shield (Portfolio Defense) Component
   ------------------------------------------------------------------------- */
export function RiskShield({
  portfolio,
}: {
  portfolio: LivePortfolio | null;
}) {
  const riskLimit = portfolio?.risk_limits?.max_session_loss_inr ?? "500";
  const openRisk = portfolio?.open_risk_bound_inr ?? "0";
  const maxDeployed = portfolio?.risk_limits?.max_deployed_inr ?? "15000";
  const deployed = Number(portfolio?.cash_inr ? Number(portfolio.capital_start_inr ?? 20000) - Number(portfolio.cash_inr) : 0);
  const maxDrawdown = portfolio?.max_drawdown_inr ?? "0";

  const riskRatio =
    Number(riskLimit) > 0 ? Math.min(100, Math.max(0, (Number(openRisk) / Number(riskLimit)) * 100)) : 0;
  const deployRatio =
    Number(maxDeployed) > 0 ? Math.min(100, Math.max(0, (deployed / Number(maxDeployed)) * 100)) : 0;

  return (
    <section className="command-panel risk-panel" aria-label="Risk Shield">
      <div className="panel-top">
        <div>
          <div className="eyebrow">RISK SHIELD · PORTFOLIO DEFENSE</div>
          <h2>Shared Risk Envelopes</h2>
        </div>
        <span className="chip subtle">DEFENSE ACTIVE</span>
      </div>

      <div className="risk-metrics-grid">
        <div className="risk-metric-card">
          <div className="risk-line">
            <span>Session Loss Budget</span>
            <strong>{money(openRisk)} / {money(riskLimit)}</strong>
          </div>
          <div className="risk-meter" role="meter" aria-label="Session loss budget" aria-valuemin={0} aria-valuemax={100} aria-valuenow={riskRatio}>
            <span style={{ width: `${riskRatio}%`, background: riskRatio > 80 ? "#FB7185" : riskRatio > 50 ? "#FBBF24" : "#2DD4BF" }} />
          </div>
          <small>{riskRatio.toFixed(1)}% risk budget utilized</small>
        </div>

        <div className="risk-metric-card">
          <div className="risk-line">
            <span>Capital Deployed</span>
            <strong>{money(deployed)} / {money(maxDeployed)}</strong>
          </div>
          <div className="risk-meter" role="meter" aria-label="Capital deployed" aria-valuemin={0} aria-valuemax={100} aria-valuenow={deployRatio}>
            <span style={{ width: `${deployRatio}%`, background: "#38BDF8" }} />
          </div>
          <small>{money(portfolio?.remaining_deployable_inr ?? "15000")} remaining deployable</small>
        </div>
      </div>

      <div className="risk-rules-strip">
        <div><small>MAX PER INSTRUMENT</small><b>{money(portfolio?.risk_limits?.max_instrument_inr ?? "5000")}</b></div>
        <div><small>MAX SPREAD TOLERANCE</small><b>{portfolio?.risk_limits?.max_spread_bps ?? "25"} bps</b></div>
        <div><small>MAX RECORDED DRAWDOWN</small><b>{money(maxDrawdown)}</b></div>
        <div><small>MAX MARKET AGE</small><b>2.0s</b></div>
      </div>

      <div className="guardrail-badges">
        <span className="guard-tag favorable">✓ Circuit Bounds Verified</span>
        <span className="guard-tag favorable">✓ Top-of-Book Uncrossed</span>
        <span className="guard-tag alert">🔒 Broker Execution Blocked</span>
      </div>
    </section>
  );
}

/* -------------------------------------------------------------------------
   Decision Timeline Component
   ------------------------------------------------------------------------- */
export function DecisionTimeline({
  audit,
  decisions,
}: {
  audit: Array<{ timestamp_ns: string; kind: string; detail: string; instrument_key?: string | null }>;
  decisions: Decision[];
}) {
  const [filter, setFilter] = useState<"ALL" | "DECISIONS" | "FEED">("ALL");

  // Merge decisions and audit into a single chronological feed
  const events = useMemo(() => {
    const list: Array<{
      id: string;
      timeNs: string;
      kind: string;
      title: string;
      detail: string;
      type: "DECISION" | "FEED";
    }> = [];

    decisions.forEach((d, i) => {
      const timeVal = d.observed_at_ns ? String(d.observed_at_ns) : `${Date.now() * 1_000_000}`;
      list.push({
        id: `dec-${d.sequence ?? i}-${d.kind}`,
        timeNs: timeVal,
        kind: d.kind,
        title: `${d.kind} ${d.symbol ?? "PORTFOLIO"}`,
        detail: d.reason ?? "Recorded virtual ledger decision",
        type: "DECISION",
      });
    });

    audit.forEach((a, i) => {
      list.push({
        id: `aud-${a.timestamp_ns}-${i}`,
        timeNs: a.timestamp_ns,
        kind: a.kind,
        title: a.kind,
        detail: `${a.detail}${a.instrument_key ? ` · ${a.instrument_key}` : ""}`,
        type: "FEED",
      });
    });

    list.sort((a, b) => {
      try {
        const diff = BigInt(b.timeNs) - BigInt(a.timeNs);
        return diff > 0n ? 1 : diff < 0n ? -1 : 0;
      } catch {
        return 0;
      }
    });

    return list;
  }, [audit, decisions]);

  const filtered = useMemo(() => {
    if (filter === "DECISIONS") return events.filter((e) => e.type === "DECISION");
    if (filter === "FEED") return events.filter((e) => e.type === "FEED");
    return events;
  }, [events, filter]);

  return (
    <section className="command-panel timeline-panel" aria-label="Decision Timeline">
      <div className="panel-top">
        <div>
          <div className="eyebrow">CHRONOLOGICAL FEED · AUDIT &amp; DECISIONS</div>
          <h2>Event Timeline</h2>
        </div>
        <div className="timeline-filter-buttons">
          <button type="button" className={filter === "ALL" ? "active" : ""} onClick={() => setFilter("ALL")}>
            All
          </button>
          <button type="button" className={filter === "DECISIONS" ? "active" : ""} onClick={() => setFilter("DECISIONS")}>
            Decisions
          </button>
          <button type="button" className={filter === "FEED" ? "active" : ""} onClick={() => setFilter("FEED")}>
            Feed
          </button>
        </div>
      </div>

      {filtered.length === 0 ? (
        <p className="command-empty">No events matching the active filter.</p>
      ) : (
        <ol className="command-events timeline-list">
          {filtered.slice(0, 10).map((entry) => (
            <li key={entry.id} className={`timeline-entry ${entry.type.toLowerCase()}`}>
              <div className="timeline-entry-left">
                <span className={`kind-tag ${entry.kind.toLowerCase()}`}>{entry.kind}</span>
                <strong>{entry.title}</strong>
              </div>
              <span>{entry.detail}</span>
              <small>{dateTime(entry.timeNs)}</small>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

/* -------------------------------------------------------------------------
   Stock Detail Drawer Component
   Opens on card or radar click to reveal full L2 ladder and shortcuts
   ------------------------------------------------------------------------- */
export function StockDetailDrawer({
  item,
  restQuote,
  onClose,
  onInspectCandles,
  onInspectBook,
}: {
  item: WireInstrumentSnapshot | null;
  restQuote?: { last_price: number; net_change?: number; volume?: number | null };
  onClose: () => void;
  onInspectCandles: () => void;
  onInspectBook: () => void;
}) {
  if (!item && !restQuote) return null;
  const sym = item?.instrument.symbol ?? "SELECTED";
  const id = item?.instrument.security_id ?? "—";
  const depth = item?.depth;
  const metrics = depth?.book_metrics;
  const bids = depth?.bids ?? [];
  const asks = depth?.asks ?? [];
  const casFlag = CAS_ELIGIBILITY_MAP[id];
  const instSession = getInstrumentSessionStatus(casFlag);

  return (
    <div className="stock-detail-drawer" aria-label={`Tactical detail for ${sym}`}>
      <div className="drawer-header">
        <div>
          <div className="eyebrow">TACTICAL DETAIL · {item?.instrument.exchange_segment ?? "NSE_EQ"}</div>
          <h2>{sym} · {id}</h2>
        </div>
        <div className="drawer-actions">
          <button type="button" className="drawer-btn" onClick={onInspectCandles}>Candle Workstation (03) →</button>
          <button type="button" className="drawer-btn" onClick={onInspectBook}>L2 Order Book (04) →</button>
          <button type="button" className="drawer-close" onClick={onClose} aria-label="Close detail drawer">✕</button>
        </div>
      </div>

      <div className="drawer-grid">
        <div className="drawer-ladder-col">
          <h4>
            {depth?.source === "DEPTH_20"
              ? `20-Level Derived Book (DEPTH_20 · ASYNC STREAM · ${time(depth.receive_timestamp_ns)})`
              : depth?.source?.startsWith("STANDARD_FULL")
              ? `Five-Level Displayed Book (${depth.source})`
              : `Order Book (${depth?.source ?? "OBSERVATION"})`}
          </h4>
          {bids.length > 0 || asks.length > 0 ? (
            <div className="mini-ladder">
              <div className="mini-ladder-side bids">
                <b>BIDS</b>
                {bids.slice(0, 5).map((b, i) => (
                  <div key={i} className="ladder-row">
                    <span>₹{(Number(BigInt(b.price_nanos)) / 1e9).toFixed(2)}</span>
                    <small>{amount(b.displayed_quantity)} qty</small>
                  </div>
                ))}
              </div>
              <div className="mini-ladder-side asks">
                <b>ASKS</b>
                {asks.slice(0, 5).map((a, i) => (
                  <div key={i} className="ladder-row">
                    <span>₹{(Number(BigInt(a.price_nanos)) / 1e9).toFixed(2)}</span>
                    <small>{amount(a.displayed_quantity)} qty</small>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <p className="ladder-empty">No genuine 5-level Full depth packet received yet for this security.</p>
          )}
        </div>

        <div className="drawer-stats-col">
          <h4>Microstructure & Session Telemetry</h4>
          <div className="drawer-stats-grid">
            <div><small>SESSION PHASE</small><b className={instSession.status === "CAS" ? "text-amber" : instSession.status === "OPEN" ? "text-teal" : ""}>{instSession.label}</b></div>
            <div><small>CAS PHASE 1 STATUS</small><b>{casFlag === 1 ? "ELIGIBLE (15:15–15:35)" : casFlag === 0 ? "NON-CAS (CONT TO 15:30)" : "UNKNOWN"}</b></div>
            <div><small>QUOTED SPREAD</small><b>{metrics ? `${metrics.quoted_spread_bps.toFixed(1)} bps` : "N/A"}</b></div>
            <div><small>5-LEVEL IMBALANCE</small><b>{metrics ? metrics.five_level_imbalance.toFixed(3) : "N/A"}</b></div>
            <div><small>OFI PROXY</small><b>{metrics?.ofi_proxy_quantity ?? "N/A"}</b></div>
            <div><small>DEPLETION (B/A)</small><b>{metrics?.bid_depletion ?? "0"} / {metrics?.ask_depletion ?? "0"}</b></div>
            <div><small>REPLENISHMENT (B/A)</small><b>{metrics?.bid_replenishment ?? "0"} / {metrics?.ask_replenishment ?? "0"}</b></div>
            <div><small>LAST RECEIPT</small><b>{depth?.receive_timestamp_ns ? time(depth.receive_timestamp_ns) : "N/A"}</b></div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* =========================================================================
   MAIN COMMAND CENTER COMPONENT
   ========================================================================= */
export default function CommandCenter({
  snapshot,
  restSnapshot,
  feedState,
  standardSockets,
  depthSockets,
  onInspect,
  onSimulator,
}: {
  snapshot: WireEngineSnapshot | null;
  restSnapshot: RestSnapshot | null;
  feedState?: string;
  standardSockets: number;
  depthSockets: number;
  onInspect: (key: string) => void;
  onSimulator: () => void;
}) {
  const [portfolio, setPortfolio] = useState<LivePortfolio | null>(null);
  const [workerError, setWorkerError] = useState("");
  const [viewAll, setViewAll] = useState(false);
  const [selectedStockId, setSelectedStockId] = useState<string>("2885"); // Default RELIANCE
  const [tickHistory, setTickHistory] = useState<Record<string, number[]>>({});

  // Poll live portfolio simulation status
  useEffect(() => {
    let active = true;
    const read = () =>
      void fetch("/trade/simulation", { credentials: "same-origin", cache: "no-store" })
        .then((response) =>
          response.ok ? response.json() : Promise.reject(new Error(`HTTP_${response.status}`))
        )
        .then((value: LivePortfolio) => {
          if (!active) return;
          if (value.schema_version !== "alpha-grid-live-portfolio-simulation-v2")
            throw new Error("PORTFOLIO_V2_UNAVAILABLE");
          setPortfolio(value);
          setWorkerError("");
        })
        .catch((error) => {
          if (active) {
            setPortfolio(null);
            setWorkerError(error instanceof Error ? error.message : "WORKER_UNAVAILABLE");
          }
        });

    read();
    const timer = window.setInterval(read, 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  // Update tick history for sparklines when live prices change
  useEffect(() => {
    if (!snapshot?.instruments) return;
    setTickHistory((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const inst of snapshot.instruments) {
        const id = inst.instrument.security_id;
        const pNanos = inst.market?.price_nanos;
        if (!pNanos) continue;
        const pNum = Number(BigInt(pNanos)) / 1e9;
        const currentSeries = next[id] ?? [];
        if (currentSeries.length === 0 || currentSeries[currentSeries.length - 1] !== pNum) {
          next[id] = [...currentSeries.slice(-14), pNum];
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [snapshot?.instruments]);

  const positions = portfolio?.positions ?? [];
  const rawPending = snapshot?.no_trade_reasons.includes("RAW_CAPTURE_VALIDATION_PENDING") ?? true;
  const targetIds = viewAll && snapshot?.instruments?.length
    ? snapshot.instruments.map((entry) => entry.instrument.security_id)
    : FOCUS;

  const rows = useMemo(() => {
    return targetIds.map((id) => {
      const item = snapshot?.instruments.find((entry) => entry.instrument.security_id === id);
      const saved = restSnapshot?.quotes.find((entry) => entry.security_id === id);
      const symbol = item?.instrument.symbol ?? saved?.symbol ?? id;
      const received = item?.market?.source_receive_timestamp_ns;
      const live = fresh(received, snapshot?.feed_connected) && item?.market?.freshness === "FRESH";
      const depthLive = fresh(item?.depth?.receive_timestamp_ns, snapshot?.feed_connected);
      const stdFull = item?.depth?.source?.startsWith("STANDARD_FULL") ? item.depth : null;
      const depth20 = item?.depth?.source === "DEPTH_20" ? item.depth : null;
      const book = stdFull || (depthLive ? depth20 : null);
      const position = positions.find((entry) => entry.symbol === symbol);
      const decision = portfolio?.decisions?.findLast((entry) => entry.symbol === symbol);

      const state = position
        ? position.reduced
          ? "REDUCING"
          : "HOLDING"
        : !live
        ? "STALE"
        : rawPending
        ? "WATCHING · VALIDATION PENDING"
        : decision?.kind === "REJECT"
        ? "REJECTED"
        : "WATCHING";

      const priceNum = item?.market?.price_nanos
        ? Number(BigInt(item.market.price_nanos)) / 1e9
        : saved?.last_price ?? null;

      const changePct = priceChangePct(
        item?.market?.price_nanos,
        item?.previous_close_nanos,
        saved?.net_change,
        saved?.last_price
      );

      const spreadBps = book?.book_metrics
        ? book.book_metrics.quoted_spread_bps
        : book?.bids[0] && book?.asks[0]
        ? (((Number(book.asks[0].price_nanos) - Number(book.bids[0].price_nanos)) /
            ((Number(book.asks[0].price_nanos) + Number(book.bids[0].price_nanos)) / 2)) *
            10000)
        : null;

      const volume = item?.market?.cumulative_traded_volume
        ? Number(item.market.cumulative_traded_volume)
        : saved?.volume ?? null;

      return {
        id,
        item,
        saved,
        symbol,
        received,
        live,
        book,
        position,
        decision,
        state,
        priceNum,
        changePct,
        spreadBps,
        volume,
      };
    });
  }, [targetIds, snapshot, restSnapshot, positions, portfolio?.decisions, rawPending]);

  const freshCount = rows.filter((r) => r.live).length;
  const decisions = portfolio?.decisions.slice(-10).reverse() ?? [];
  const audit = snapshot?.audit.slice(-10).reverse() ?? [];
  const sessionStatus = getNseSessionStatus();

  const selectedRow = rows.find((r) => r.id === selectedStockId) ?? rows[0];

  return (
    <section className="command-center gamified-theme" aria-label="Command Center">
      <StorageSessionPanel onInspectStock={onInspect} />
      {/* -------------------------------------------------------------------
          A. GLOBAL SESSION HUD (Top Persistent Status Strip)
          ------------------------------------------------------------------- */}
      <div className="global-session-hud">
        <div className="hud-cell session-status">
          <div className="hud-label">NSE SESSION</div>
          <div className="hud-value session-badge">
            <span className={`pulse-dot ${sessionStatus.status.toLowerCase()}`} />
            <strong>{sessionStatus.label}</strong>
          </div>
          <small>{sessionStatus.sub}</small>
        </div>

        <div className="hud-cell">
          <div className="hud-label">FEED GATEWAY</div>
          <div className="hud-value">
            <strong className="text-cyan">{feedState ?? "LIVE CAPTURE"}</strong>
          </div>
          <small>{standardSockets} standard / {depthSockets} depth socket</small>
        </div>

        <div className="hud-cell">
          <div className="hud-label">OBSERVED UNIVERSE</div>
          <div className="hud-value">
            <strong>{freshCount} / {targetIds.length}</strong>
            <span className="hud-tag">FRESH</span>
          </div>
          <small>{snapshot?.instruments.length ?? 0} subscribed NSE equities</small>
        </div>

        <div className="hud-cell">
          <div className="hud-label">VIRTUAL CAPITAL</div>
          <div className="hud-value">
            <strong>{money(portfolio?.cash_inr ?? "20000")}</strong>
          </div>
          <small>Cost basis: {money(portfolio?.equity_inr ?? "20000")} (virtual only)</small>
        </div>

        <div className="hud-cell">
          <div className="hud-label">ACTIVE POSITIONS</div>
          <div className="hud-value">
            <strong>{positions.length} / 3</strong>
            <span className="hud-tag">{portfolio?.remaining_position_slots ?? 3} SLOTS</span>
          </div>
          <small>Max 3 concurrent positions</small>
        </div>

        <div className="hud-cell">
          <div className="hud-label">DECISION WORKER</div>
          <div className="hud-value">
            <strong className="text-amber">{portfolio?.state ?? (workerError || "STARTING")}</strong>
          </div>
          <small>{portfolio?.feed_status ?? "Validation pending; raw observation"}</small>
        </div>
      </div>

      {/* Main Command Header */}
      <div className="command-heading">
        <div>
          <div className="eyebrow">STRATEGY COMMAND CENTER · QUANTITATIVE REPLAY LAB</div>
          <h1>Command Center<span className="heading-dot">.</span></h1>
          <p>
            Real-time multi-stock microstructure telemetry and hash-chained virtual portfolio simulator.
            Live prices are genuine Dhan/NSE observations; virtual references are not executable broker fills.
          </p>
        </div>
        <div className="heading-badges">
          <span className="chip alert">DATA_ONLY · SIMULATION_ONLY · NO REAL ORDERS</span>
        </div>
      </div>

      {/* -------------------------------------------------------------------
          B & C. UPPER STRATEGY GRID: Market Radar (Left) + Leaderboard (Right)
          ------------------------------------------------------------------- */}
      <div className="strategy-grid">
        <MarketRadar
          rows={rows.slice(0, 10)}
          selectedId={selectedStockId}
          onSelect={(id) => {
            setSelectedStockId(id);
            const found = rows.find((r) => r.id === id);
            if (found?.item) onInspect(found.item.instrument.key);
          }}
        />

        <OpportunityLeaderboard
          rows={rows}
          portfolio={portfolio}
          rawPending={rawPending}
        />
      </div>

      {/* -------------------------------------------------------------------
          D. STOCK ARENA (Main Tactical 10-Stock Grid)
          ------------------------------------------------------------------- */}
      <div className="command-section-head">
        <div>
          <div className="eyebrow">STOCK ARENA · {viewAll ? "ALL 25 MONITORED" : "TOP 10 FOCUS"}</div>
          <h2>Tactical Instrument Radar</h2>
        </div>
        <div className="arena-controls">
          <button
            type="button"
            className={`command-link ${!viewAll ? "active" : ""}`}
            onClick={() => setViewAll(false)}
          >
            Focus 10
          </button>
          <button
            type="button"
            className={`command-link ${viewAll ? "active" : ""}`}
            onClick={() => setViewAll(true)}
          >
            All 25 Stocks
          </button>
          <span className="arena-hint">Click card to inspect L2 order book</span>
        </div>
      </div>

      <div className="stock-arena">
        {rows.map((row) => {
          const isSelected = row.id === selectedStockId;
          const casFlag = CAS_ELIGIBILITY_MAP[row.id];
          const instSession = getInstrumentSessionStatus(casFlag);
          const deltaStr = deltaText(
            row.item?.market?.price_nanos,
            row.item?.previous_close_nanos,
            row.saved?.net_change,
            row.saved?.last_price
          );
          const isPositive = deltaStr.startsWith("+");
          const isNegative = deltaStr.startsWith("-");

          return (
            <button
              type="button"
              className={`stock-card ${row.live ? "stock-live" : "stock-stale"} ${isSelected ? "selected-card" : ""}`}
              key={row.id}
              onClick={() => {
                setSelectedStockId(row.id);
                if (row.item) onInspect(row.item.instrument.key);
              }}
              disabled={!row.item && !row.saved}
            >
              <div className="stock-card-top">
                <div>
                  <strong>{row.symbol}</strong>
                  <small className="sec-id">{row.id} · NSE_EQ</small>
                </div>
                <div className="card-top-right">
                  {instSession.status === "CAS" && (
                    <span className="inst-session-tag cas" title={instSession.detail}>
                      CAS
                    </span>
                  )}
                  {instSession.status === "UNKNOWN" && (
                    <span className="inst-session-tag unknown" title={instSession.detail}>
                      UNKNOWN
                    </span>
                  )}
                  <span className={`live-tag ${row.live ? "live" : "offline"}`}>
                    <i className="pulse-dot" />
                    {row.live ? "LIVE" : "STALE"}
                  </span>
                </div>
              </div>

              <div className="stock-price-row">
                <div className="stock-price">
                  {row.live ? price(row.item?.market?.price_nanos) : row.saved ? `₹${row.saved.last_price.toFixed(2)}` : "N/A"}
                </div>
                <Sparkline points={tickHistory[row.id] ?? []} />
              </div>

              <div className={`stock-delta ${isPositive ? "positive" : isNegative ? "negative" : ""}`}>
                {deltaStr}
              </div>

              <div className="stock-source">
                {row.live
                  ? `LIVE_WS · ${time(row.received)}`
                  : row.saved
                  ? `REST_SNAPSHOT · ${restSnapshot?.received_at ?? "SAVED"}`
                  : "NO CURRENT OBSERVATION"}
              </div>

              <div className="stock-book">
                <span>MARKET BID / ASK</span>
                <b>
                  {row.item?.market?.best_bid_price_nanos && row.item?.market?.best_ask_price_nanos
                    ? `${price(row.item.market.best_bid_price_nanos)} / ${price(row.item.market.best_ask_price_nanos)}`
                    : row.book?.source?.startsWith("STANDARD_FULL") && row.book?.bids[0] && row.book?.asks[0]
                    ? `${price(row.book.bids[0].price_nanos)} / ${price(row.book.asks[0].price_nanos)}`
                    : "N/A"}
                </b>
                <span>SPREAD</span>
                <b className={row.spreadBps && row.spreadBps <= 5 ? "text-teal" : ""}>
                  {row.spreadBps != null ? `${row.spreadBps.toFixed(1)} bps` : "N/A"}
                </b>
                <span>IMBALANCE</span>
                <b>{row.book?.book_metrics ? row.book.book_metrics.five_level_imbalance.toFixed(2) : "N/A"}</b>
              </div>

              <div className="stock-card-foot">
                <span>{row.state}</span>
                <small>
                  {row.decision?.reason ?? (rawPending ? "Raw capture is not decision eligible" : "No recorded candidate")}
                </small>
              </div>
            </button>
          );
        })}
      </div>

      {/* Selected Stock Tactical Detail Drawer */}
      {selectedRow && (
        <StockDetailDrawer
          item={selectedRow.item ?? null}
          restQuote={selectedRow.saved}
          onClose={() => setSelectedStockId("")}
          onInspectCandles={() => selectedRow.item && onInspect(selectedRow.item.instrument.key)}
          onInspectBook={() => selectedRow.item && onInspect(selectedRow.item.instrument.key)}
        />
      )}

      {/* -------------------------------------------------------------------
          E, F & G. LOWER STRATEGY PANELS: Missions, Risk Shield & Timeline
          ------------------------------------------------------------------- */}
      <div className="command-columns lower-grid">
        <ActiveMissions
          positions={positions}
          onSimulator={onSimulator}
          rawPending={rawPending}
          connected={snapshot?.feed_connected ?? (feedState === "LIVE CAPTURE")}
        />
        <RiskShield portfolio={portfolio} />
        <DecisionTimeline audit={audit} decisions={decisions} />
      </div>
    </section>
  );
}
