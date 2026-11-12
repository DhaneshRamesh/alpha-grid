"""Alpha Grid — First Cross-Equity ML Research Model.

Builds Alpha Grid's minimal, reproducible, data-trained cross-equity ML model:
- Reuses existing portfolio limits, statutory fees, and walkforward logic.
- Evaluates 25-stock NSE equity universe across fixed 15m outcome horizons.
- Pre-declares 5 causal point-in-time features (past 5m return, breakout distance,
  relative volume, realized volatility, normalized time-of-day).
- Pure NumPy regularized logistic regression with train-only standardisation.
- Comparative evaluation across 5 arms:
  A. Constant-rate null baseline
  B. Existing upward-momentum rule
  C. Fixed breakout-event baseline
  D. Shared cross-equity ML model
  E. NO TRADE economic benchmark
- Reconciles statistical predictive advantage vs conservative net trading value.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal

import numpy as np
import numpy.typing as npt

from quant.acquisition.calendar import IST
from quant.research.prospective import compute_dhan_intraday_fees
from quant.research.sufficiency import SufficiencyPolicy, SufficiencyResult

SPECIFICATION_VERSION = "alpha-grid-cross-equity-ml-v1"
FEATURE_NAMES: tuple[str, ...] = (
    "past_5m_return_bps",
    "breakout_distance_bps",
    "relative_volume_15m",
    "realized_volatility_15m_bps",
    "normalized_time_of_day",
)
FORWARD_HORIZON_MINUTES: int = 15
DECISION_STEP_MINUTES: int = 5
FAVORABLE_RETURN_HURDLE_BPS: float = 30.0
ADVERSE_RETURN_THRESHOLD_BPS: float = -10.0
ADVERSE_MAE_THRESHOLD_BPS: float = -25.0

# Realistic Portfolio Limits
PORTFOLIO_CAPITAL: Decimal = Decimal("20000.00")
PORTFOLIO_MAX_DEPLOYED: Decimal = Decimal("15000.00")
PORTFOLIO_MAX_POSITION: Decimal = Decimal("5000.00")
PORTFOLIO_MAX_POSITIONS: int = 3
PORTFOLIO_SESSION_LOSS_LIMIT: Decimal = Decimal("500.00")
DEFAULT_QUOTED_SPREAD_BPS: Decimal = Decimal("5.0")

ComparativeArm = Literal[
    "NULL_BASELINE",
    "MOMENTUM_RULE",
    "BREAKOUT_BASELINE",
    "ML_MODEL",
    "NO_TRADE",
]


def _sigmoid(z: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """Numerically stable logistic sigmoid."""
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35.0, 35.0)))


def brier_score(y_true: npt.NDArray[np.int_], y_prob: npt.NDArray[np.float64]) -> float:
    """Mean squared error of probabilistic predictions."""
    if len(y_true) == 0 or len(y_true) != len(y_prob):
        raise ValueError("mismatched or empty arrays for Brier score")
    return float(np.mean((y_prob - y_true) ** 2))


def log_loss(
    y_true: npt.NDArray[np.int_], y_prob: npt.NDArray[np.float64], eps: float = 1e-15
) -> float:
    """Binary cross-entropy loss with bounded probability clipping."""
    if len(y_true) == 0 or len(y_true) != len(y_prob):
        raise ValueError("mismatched or empty arrays for log loss")
    clipped = np.clip(y_prob, eps, 1.0 - eps)
    return float(
        -np.mean(y_true * np.log(clipped) + (1 - y_true) * np.log(1.0 - clipped))
    )


@dataclass(frozen=True, slots=True)
class OpportunityCandidate:
    symbol: str
    security_id: str
    date_str: str
    decision_time_ist: str
    decision_dt: datetime
    entry_reference_price_inr: float
    features: tuple[float, float, float, float, float]
    past_15m_return_bps: float
    breakout_distance_bps: float
    forward_15m_return_bps: float
    forward_15m_exit_price_inr: float
    max_favorable_excursion_bps: float
    max_adverse_excursion_bps: float
    favorable_event: int
    adverse_event: int
    candidate_type: str  # ACTIVE_BREAKOUT, FAILED_BREAKOUT_PULLBACK, etc.


class RegularizedLogisticRegression:
    """Pooled L2-regularized logistic regression with train-only standardisation."""

    def __init__(
        self, l2_penalty: float = 1.0, max_iter: int = 100, tol: float = 1e-6
    ) -> None:
        if l2_penalty < 0:
            raise ValueError("L2 penalty must be non-negative")
        self.l2_penalty = l2_penalty
        self.max_iter = max_iter
        self.tol = tol
        self.weights: npt.NDArray[np.float64] | None = None
        self.intercept: float | None = None
        self.feature_mean: npt.NDArray[np.float64] | None = None
        self.feature_scale: npt.NDArray[np.float64] | None = None

    def fit(self, x: npt.NDArray[np.float64], y: npt.NDArray[np.int_]) -> None:
        """Fit model with L2 penalty, standardizing features on train only."""
        if x.ndim != 2 or x.shape[0] != y.shape[0]:
            raise ValueError("mismatched feature/target dimensions")
        n, d = x.shape
        if n < 5:
            raise ValueError(
                "insufficient observations to fit regularized logistic model"
            )

        # Train-only standardisation
        mean = np.mean(x, axis=0)
        scale = np.std(x, axis=0)
        scale = np.where(scale == 0.0, 1.0, scale)
        self.feature_mean = mean
        self.feature_scale = scale
        z = (x - mean) / scale

        w = np.zeros(d, dtype=np.float64)
        b = float(np.log(max(np.mean(y), 1e-4) / max(1.0 - np.mean(y), 1e-4)))

        for _ in range(self.max_iter):
            p = _sigmoid(z @ w + b)
            grad_w = (z.T @ (p - y)) / n + self.l2_penalty * w
            grad_b = float(np.mean(p - y))

            w_weights = np.maximum(p * (1.0 - p), 1e-8)
            h_ww = (z.T * w_weights) @ z / n + np.eye(d) * self.l2_penalty
            h_wb = (z.T @ w_weights) / n
            h_bb = float(np.mean(w_weights))

            h = np.zeros((d + 1, d + 1), dtype=np.float64)
            h[:d, :d] = h_ww
            h[:d, d] = h_wb
            h[d, :d] = h_wb
            h[d, d] = h_bb

            grad = np.r_[grad_w, grad_b]
            try:
                delta = np.linalg.solve(h + np.eye(d + 1) * 1e-6, grad)
            except np.linalg.LinAlgError:
                delta = np.linalg.lstsq(h + np.eye(d + 1) * 1e-6, grad, rcond=None)[0]

            w -= delta[:d]
            b -= float(delta[d])

            if float(np.linalg.norm(delta)) < self.tol:
                break

        self.weights = w
        self.intercept = b

    def predict_proba(self, x: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
        """Predict probability using learned weights and train-fitted scaling."""
        if (
            self.weights is None
            or self.intercept is None
            or self.feature_mean is None
            or self.feature_scale is None
        ):
            raise ValueError("model is not fitted")
        z = (x - self.feature_mean) / self.feature_scale
        return _sigmoid(z @ self.weights + self.intercept)


@dataclass(frozen=True, slots=True)
class SimulationTrade:
    symbol: str
    date_str: str
    entry_time_ist: str
    exit_time_ist: str
    buy_notional_inr: Decimal
    sell_notional_inr: Decimal
    gross_pnl_inr: Decimal
    statutory_fees_inr: Decimal
    spread_cost_inr: Decimal
    residual_friction_inr: Decimal
    net_pnl_inr: Decimal
    return_bps: Decimal


@dataclass(frozen=True, slots=True)
class ArmResult:
    arm_name: ComparativeArm
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    gross_pnl_inr: Decimal
    statutory_fees_inr: Decimal
    spread_cost_inr: Decimal
    residual_friction_inr: Decimal
    net_pnl_inr: Decimal
    mean_trade_net_bps: Decimal
    trades: tuple[SimulationTrade, ...]


def simulate_portfolio_arm(
    candidates: list[OpportunityCandidate],
    arm: ComparativeArm,
    ml_model: RegularizedLogisticRegression | None = None,
    ml_threshold: float = 0.12,
    friction_bps: Decimal = Decimal("0"),
    quoted_spread_bps: Decimal = DEFAULT_QUOTED_SPREAD_BPS,
) -> ArmResult:
    """Sequential portfolio simulation with capital competition and fees."""
    if arm == "NO_TRADE":
        return ArmResult(
            arm_name="NO_TRADE",
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0.0,
            gross_pnl_inr=Decimal("0"),
            statutory_fees_inr=Decimal("0"),
            spread_cost_inr=Decimal("0"),
            residual_friction_inr=Decimal("0"),
            net_pnl_inr=Decimal("0"),
            mean_trade_net_bps=Decimal("0"),
            trades=(),
        )

    # Group candidate decisions by chronological time slot
    by_slot: dict[str, list[OpportunityCandidate]] = {}
    for c in candidates:
        by_slot.setdefault(f"{c.date_str}_{c.decision_time_ist}", []).append(c)

    cash = PORTFOLIO_CAPITAL
    positions: dict[str, dict[str, Any]] = {}
    trades: list[SimulationTrade] = []
    session_loss = Decimal("0")
    cur_date_str: str | None = None

    for slot_key in sorted(by_slot):
        slot_candidates = by_slot[slot_key]
        cur_dt = slot_candidates[0].decision_dt
        d_str = slot_candidates[0].date_str

        # On session boundary, close overnight positions and reset session loss
        if cur_date_str is not None and d_str != cur_date_str:
            for sym, pos in list(positions.items()):
                c_cand = pos["cand"]
                exit_px = Decimal(str(c_cand.forward_15m_exit_price_inr))
                shares = pos["shares"]
                buy_notional = pos["notional"]
                sell_notional = exit_px * Decimal(shares)
                fees = compute_dhan_intraday_fees(buy_notional, sell_notional)
                spread_cost = buy_notional * (quoted_spread_bps / Decimal("10000"))
                friction_cost = buy_notional * (friction_bps / Decimal("10000"))
                gross_pnl = sell_notional - buy_notional
                net_pnl = gross_pnl - fees.total_statutory_fees - spread_cost - friction_cost
                cash += buy_notional + net_pnl
                ret_bps = (
                    (net_pnl / buy_notional) * Decimal("10000")
                    if buy_notional > 0
                    else Decimal("0")
                )
                trades.append(
                    SimulationTrade(
                        symbol=sym,
                        date_str=c_cand.date_str,
                        entry_time_ist=pos["entry_dt"].strftime("%H:%M"),
                        exit_time_ist="15:00",
                        buy_notional_inr=buy_notional,
                        sell_notional_inr=sell_notional,
                        gross_pnl_inr=gross_pnl,
                        statutory_fees_inr=fees.total_statutory_fees,
                        spread_cost_inr=spread_cost,
                        residual_friction_inr=friction_cost,
                        net_pnl_inr=net_pnl,
                        return_bps=ret_bps,
                    )
                )
            positions.clear()
            session_loss = Decimal("0")
        cur_date_str = d_str

        # 1. Close matured positions (15-minute fixed horizon)
        to_close = [sym for sym, pos in positions.items() if pos["exit_dt"] <= cur_dt]
        for sym in to_close:
            pos = positions.pop(sym)
            c_cand: OpportunityCandidate = pos["cand"]
            exit_px = Decimal(str(c_cand.forward_15m_exit_price_inr))
            shares = pos["shares"]
            buy_notional = pos["notional"]
            sell_notional = exit_px * Decimal(shares)
            fees = compute_dhan_intraday_fees(buy_notional, sell_notional)

            spread_cost = buy_notional * (quoted_spread_bps / Decimal("10000"))
            friction_cost = buy_notional * (friction_bps / Decimal("10000"))
            gross_pnl = sell_notional - buy_notional
            net_pnl = (
                gross_pnl - fees.total_statutory_fees - spread_cost - friction_cost
            )

            cash += buy_notional + net_pnl
            session_loss += min(Decimal("0"), net_pnl)
            ret_bps = (
                (net_pnl / buy_notional) * Decimal("10000")
                if buy_notional > 0
                else Decimal("0")
            )

            trades.append(
                SimulationTrade(
                    symbol=sym,
                    date_str=c_cand.date_str,
                    entry_time_ist=pos["entry_dt"].strftime("%H:%M"),
                    exit_time_ist=pos["exit_dt"].strftime("%H:%M"),
                    buy_notional_inr=buy_notional,
                    sell_notional_inr=sell_notional,
                    gross_pnl_inr=gross_pnl,
                    statutory_fees_inr=fees.total_statutory_fees,
                    spread_cost_inr=spread_cost,
                    residual_friction_inr=friction_cost,
                    net_pnl_inr=net_pnl,
                    return_bps=ret_bps,
                )
            )

        # 2. Check session loss limit
        if abs(session_loss) >= PORTFOLIO_SESSION_LOSS_LIMIT:
            continue

        # 3. Filter candidates for the evaluated arm
        eligible: list[tuple[OpportunityCandidate, float]] = []
        for cand in slot_candidates:
            if cand.symbol in positions:
                continue

            if arm == "NULL_BASELINE":
                # Naive baseline: past 5m positive, capped to simulate random pick
                if cand.features[0] > 0.0 and len(eligible) < 1:
                    eligible.append((cand, 1.0))
            elif arm == "MOMENTUM_RULE":
                # Momentum rule: 15m return >= spread + 20 bps friction + fees (35 bps)
                if cand.past_15m_return_bps >= 35.0:
                    eligible.append((cand, cand.past_15m_return_bps))
            elif arm == "BREAKOUT_BASELINE":
                # Fixed breakout: above opening high with positive trailing momentum
                if cand.breakout_distance_bps > 0.0 and cand.past_15m_return_bps > 0.0:
                    eligible.append((cand, cand.breakout_distance_bps))
            elif arm == "ML_MODEL":
                if ml_model is not None:
                    feat_arr = np.array([cand.features], dtype=np.float64)
                    prob = float(ml_model.predict_proba(feat_arr)[0])
                    if prob >= ml_threshold:
                        eligible.append((cand, prob))

        # Sort by signal score (priority ranking under capital competition)
        eligible.sort(key=lambda x: x[1], reverse=True)

        # 4. Allocate capital up to portfolio bounds
        for cand, _ in eligible:
            if len(positions) >= PORTFOLIO_MAX_POSITIONS:
                break
            deployed = sum(p["notional"] for p in positions.values())
            if deployed + PORTFOLIO_MAX_POSITION > PORTFOLIO_MAX_DEPLOYED:
                break
            if cash < PORTFOLIO_MAX_POSITION:
                break

            entry_px = Decimal(str(cand.entry_reference_price_inr))
            shares = int(PORTFOLIO_MAX_POSITION / entry_px)
            if shares <= 0:
                continue
            notional = entry_px * Decimal(shares)
            cash -= notional
            positions[cand.symbol] = {
                "cand": cand,
                "entry_dt": cur_dt,
                "exit_dt": cur_dt + timedelta(minutes=FORWARD_HORIZON_MINUTES),
                "shares": shares,
                "notional": notional,
            }

    # Close remaining positions at session end
    for sym, pos in list(positions.items()):
        c_cand = pos["cand"]
        exit_px = Decimal(str(c_cand.forward_15m_exit_price_inr))
        shares = pos["shares"]
        buy_notional = pos["notional"]
        sell_notional = exit_px * Decimal(shares)
        fees = compute_dhan_intraday_fees(buy_notional, sell_notional)
        spread_cost = buy_notional * (quoted_spread_bps / Decimal("10000"))
        friction_cost = buy_notional * (friction_bps / Decimal("10000"))
        gross_pnl = sell_notional - buy_notional
        net_pnl = gross_pnl - fees.total_statutory_fees - spread_cost - friction_cost
        cash += buy_notional + net_pnl
        ret_bps = (
            (net_pnl / buy_notional) * Decimal("10000")
            if buy_notional > 0
            else Decimal("0")
        )

        trades.append(
            SimulationTrade(
                symbol=sym,
                date_str=c_cand.date_str,
                entry_time_ist=pos["entry_dt"].strftime("%H:%M"),
                exit_time_ist="15:00",
                buy_notional_inr=buy_notional,
                sell_notional_inr=sell_notional,
                gross_pnl_inr=gross_pnl,
                statutory_fees_inr=fees.total_statutory_fees,
                spread_cost_inr=spread_cost,
                residual_friction_inr=friction_cost,
                net_pnl_inr=net_pnl,
                return_bps=ret_bps,
            )
        )

    tot_gross = sum((t.gross_pnl_inr for t in trades), Decimal("0"))
    tot_fees = sum((t.statutory_fees_inr for t in trades), Decimal("0"))
    tot_spread = sum((t.spread_cost_inr for t in trades), Decimal("0"))
    tot_friction = sum((t.residual_friction_inr for t in trades), Decimal("0"))
    tot_net = sum((t.net_pnl_inr for t in trades), Decimal("0"))
    wins = sum(1 for t in trades if t.net_pnl_inr > 0)
    losses = sum(1 for t in trades if t.net_pnl_inr < 0)
    win_rate = (wins / len(trades)) if trades else 0.0
    mean_bps = (
        (sum((t.return_bps for t in trades), Decimal("0")) / len(trades))
        if trades
        else Decimal("0")
    )

    return ArmResult(
        arm_name=arm,
        total_trades=len(trades),
        winning_trades=wins,
        losing_trades=losses,
        win_rate=win_rate,
        gross_pnl_inr=tot_gross,
        statutory_fees_inr=tot_fees,
        spread_cost_inr=tot_spread,
        residual_friction_inr=tot_friction,
        net_pnl_inr=tot_net,
        mean_trade_net_bps=mean_bps,
        trades=tuple(trades),
    )


def extract_opportunity_candidates(
    bars_by_symbol_date: dict[str, dict[str, dict[time, dict[str, Any]]]],
    universe_ids: dict[str, str],
) -> list[OpportunityCandidate]:
    """Causal, point-in-time feature extraction across 5-minute decision slots."""
    decision_times: list[time] = []
    cur = datetime.combine(datetime.today(), time(10, 0))
    end = datetime.combine(datetime.today(), time(14, 45))
    while cur <= end:
        decision_times.append(cur.time())
        cur += timedelta(minutes=DECISION_STEP_MINUTES)

    candidates: list[OpportunityCandidate] = []

    for sym, days_dict in sorted(bars_by_symbol_date.items()):
        sec_id = universe_ids.get(sym, "0")
        for d_str, day_bars in sorted(days_dict.items()):
            # Opening 30m range (09:15 to 09:44)
            opening_bars = [day_bars.get(time(9, m)) for m in range(15, 45)]
            if any(b is None for b in opening_bars):
                continue
            open_high = max(b["h"] for b in opening_bars if b is not None)

            for t_dec in decision_times:
                dec_dt = datetime.combine(
                    datetime.fromisoformat(d_str), t_dec, tzinfo=IST
                )
                asof_dt = dec_dt - timedelta(minutes=1)
                c_bar = day_bars.get(asof_dt.time())
                if c_bar is None:
                    continue

                bar_m5 = day_bars.get((asof_dt - timedelta(minutes=5)).time())
                bar_m15 = day_bars.get((asof_dt - timedelta(minutes=15)).time())
                if bar_m5 is None or bar_m15 is None:
                    continue

                # 1. Past 5m return in bps
                r_5m_bps = float((c_bar["c"] - bar_m5["c"]) / bar_m5["c"] * 10000.0)

                # Trailing 15m return in bps
                r_15m_past_bps = float(
                    (c_bar["c"] - bar_m15["c"]) / bar_m15["c"] * 10000.0
                )

                # 2. Breakout distance bps relative to 30m opening high
                breakout_dist_bps = float(
                    (c_bar["c"] - open_high) / open_high * 10000.0
                )

                # 3. Relative volume: 15m volume ending at asof vs prior 30m avg
                v_15m = sum(
                    day_bars.get((asof_dt - timedelta(minutes=m)).time(), {}).get(
                        "v", 0
                    )
                    for m in range(15)
                )
                v_prior_30m = sum(
                    day_bars.get((asof_dt - timedelta(minutes=15 + m)).time(), {}).get(
                        "v", 0
                    )
                    for m in range(30)
                )
                rel_vol = float(v_15m / (v_prior_30m / 2.0 + 1e-6))

                # 4. Volatility: std of 1m returns over trailing 15m
                rets_15m: list[float] = []
                for m in range(14, -1, -1):
                    b_curr = day_bars.get((asof_dt - timedelta(minutes=m)).time())
                    b_prev = day_bars.get((asof_dt - timedelta(minutes=m + 1)).time())
                    if b_curr and b_prev and b_prev["c"] > 0:
                        rets_15m.append((b_curr["c"] - b_prev["c"]) / b_prev["c"])
                vol_15m_bps = (
                    float(np.std(rets_15m) * 10000.0) if len(rets_15m) >= 10 else 0.0
                )

                # 5. Normalized Time of Day (0.0 at 09:15 to 1.0 at 15:30)
                mins_from_open = (
                    dec_dt - datetime.combine(dec_dt.date(), time(9, 15), tzinfo=IST)
                ).total_seconds() / 60.0
                tod_norm = float(mins_from_open / 375.0)

                # Forward 15m outcome
                fwd_bar = day_bars.get(
                    (dec_dt + timedelta(minutes=FORWARD_HORIZON_MINUTES - 1)).time()
                )
                if fwd_bar is None:
                    continue
                fwd_ret_15m_bps = float(
                    (fwd_bar["c"] - c_bar["c"]) / c_bar["c"] * 10000.0
                )

                window_bars = [
                    day_bars.get((dec_dt + timedelta(minutes=m)).time())
                    for m in range(FORWARD_HORIZON_MINUTES)
                ]
                if any(wb is None for wb in window_bars):
                    continue
                max_h = max(wb["h"] for wb in window_bars if wb is not None)
                min_l = min(wb["l"] for wb in window_bars if wb is not None)
                mfe_bps = float((max_h - c_bar["c"]) / c_bar["c"] * 10000.0)
                mae_bps = float((min_l - c_bar["c"]) / c_bar["c"] * 10000.0)

                # Candidate categorization
                if breakout_dist_bps > 0.0:
                    cand_type = "ACTIVE_BREAKOUT"
                else:
                    # Check if session high touched opening high prior to asof
                    prior_highs = [
                        day_bars.get(time(h_m // 60, h_m % 60), {}).get("h", 0)
                        for h_m in range(
                            9 * 60 + 45, asof_dt.hour * 60 + asof_dt.minute
                        )
                    ]
                    if prior_highs and max(prior_highs) >= open_high:
                        cand_type = "FAILED_BREAKOUT_PULLBACK"
                    else:
                        cand_type = "CONSOLIDATION_WITHIN_RANGE"

                # Binary target events
                favorable = 1 if fwd_ret_15m_bps >= FAVORABLE_RETURN_HURDLE_BPS else 0
                adverse = (
                    1
                    if (
                        fwd_ret_15m_bps <= ADVERSE_RETURN_THRESHOLD_BPS
                        or mae_bps <= ADVERSE_MAE_THRESHOLD_BPS
                    )
                    else 0
                )

                candidates.append(
                    OpportunityCandidate(
                        symbol=sym,
                        security_id=sec_id,
                        date_str=d_str,
                        decision_time_ist=t_dec.isoformat(),
                        decision_dt=dec_dt,
                        entry_reference_price_inr=float(c_bar["c"]),
                        features=(
                            r_5m_bps,
                            breakout_dist_bps,
                            rel_vol,
                            vol_15m_bps,
                            tod_norm,
                        ),
                        past_15m_return_bps=r_15m_past_bps,
                        breakout_distance_bps=breakout_dist_bps,
                        forward_15m_return_bps=fwd_ret_15m_bps,
                        forward_15m_exit_price_inr=float(fwd_bar["c"]),
                        max_favorable_excursion_bps=mfe_bps,
                        max_adverse_excursion_bps=mae_bps,
                        favorable_event=favorable,
                        adverse_event=adverse,
                        candidate_type=cand_type,
                    )
                )

    return candidates


def evaluate_data_sufficiency(
    candidates: list[OpportunityCandidate],
) -> SufficiencyResult:
    """Assess available candidate data against training sufficiency policy."""
    policy = SufficiencyPolicy(
        min_independent_sessions=20,
        min_usable_labels=500,
        min_label_fraction=0.8,
        min_quality_fraction=0.95,
        min_events_per_cause=30,
    )
    sessions = len({c.date_str for c in candidates})
    usable = len(candidates)
    favorable = sum(c.favorable_event for c in candidates)
    adverse = sum(c.adverse_event for c in candidates)

    reasons: list[str] = []
    if sessions < policy.min_independent_sessions:
        reasons.append(
            f"insufficient_sessions: {sessions} < {policy.min_independent_sessions}"
        )
    if usable < policy.min_usable_labels:
        reasons.append(
            f"insufficient_usable_labels: {usable} < {policy.min_usable_labels}"
        )
    if min(favorable, adverse) < policy.min_events_per_cause:
        reasons.append("insufficient_competing_event_support")

    return SufficiencyResult(
        status="NOT_TRAINED" if reasons else "ELIGIBLE_FOR_RESEARCH_TRAINING",
        reasons=tuple(reasons),
        independent_sessions=sessions,
        usable_labels=usable,
        label_fraction=1.0 if usable > 0 else 0.0,
        quality_fraction=1.0 if usable > 0 else 0.0,
        volume_complete_fraction=1.0 if usable > 0 else 0.0,
        favorable_events=favorable,
        adverse_events=adverse,
    )
