"""Alpha Grid — EXP-60M-BRACKETED-OPPORTUNITY-V1 Stage 2 Evaluator.

Implements only Stage 2 of the frozen 60-minute opportunity protocol:
1. Point-in-time causal feature and 60-minute outcome extraction on verified development data.
2. Bar T Open reference prices; actual availability and fills are unverified.
3. Predeclared comparative arms:
   - Arm 0: NO_TRADE
   - Arm 1: UNCONDITIONAL_NULL (fixed 60m exit)
   - Arm 2: MOMENTUM_FIXED_60M (fixed 60m exit)
   - Arm 3: BREAKOUT_FIXED_60M (fixed 60m exit)
   - Arm 4: BREAKOUT_BRACKET_ASYMMETRIC (+25 bps target, -20 bps stop, 60m timeout)
   - Arm 5: MAKER_BRACKET_PASSIVE (resting bid limit, saving spread, bracket exit)
4. Rigorous fee schedule (compute_dhan_intraday_fees), spread accounting, and friction ladder.
5. Calendar session-clustered block bootstrap (B = 2,000 resamples).
6. Strict preservation of temporal holdout (>= 2025-11-01) and 5 reserved stocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import numpy as np

from quant.acquisition.calendar import IST
from quant.research.m1_pipeline import (
    CALIBRATION_END_DATE,
    CALIBRATION_START_DATE,
    CORPORATE_ACTION_EX_DATES,
    RESERVED_HOLDOUT_STOCKS,
    SPECIAL_DR_DATES,
)
from quant.research.prospective import compute_dhan_intraday_fees
from tools.execute_full_3yr_acquisition import unreconstructed_price_windows

DEV_START_DATE = "2023-11-01"
DEV_END_DATE = "2025-10-31"  # Strictly before temporal holdout 2025-11-01

PORTFOLIO_CAPITAL = Decimal("100000")
PORTFOLIO_MAX_DEPLOYED = Decimal("15000")
PORTFOLIO_MAX_POSITION = Decimal("5000")
PORTFOLIO_MAX_POSITIONS = 3
PORTFOLIO_SESSION_LOSS_LIMIT = Decimal("1500")


@dataclass(frozen=True, slots=True)
class Opportunity60M:
    symbol: str
    security_id: str
    date_str: str
    decision_time_ist: str
    decision_dt: datetime
    entry_open_px: float
    features: tuple[float, float, float, float, float, float, float]
    # Features: (r_5m, r_15m, breakout_dist, rel_vol, vol_15m, tod_norm, or_width)
    past_15m_return_bps: float
    breakout_dist_bps: float
    # Fixed 60m outcome
    fwd_60m_return_bps: float
    fwd_60m_exit_px: float
    mfe_bps: float
    mae_bps: float
    # Asymmetric bracket outcome (+25 bps target, -20 bps stop, 60m timeout)
    bracket_return_bps: float
    bracket_exit_minute: int
    bracket_exit_reason: str
    # Maker bracket outcome (resting bid at -2.5 bps)
    maker_filled: bool
    maker_bracket_return_bps: float
    maker_exit_minute: int
    maker_exit_reason: str


@dataclass(frozen=True, slots=True)
class SimulatedTrade60M:
    symbol: str
    date_str: str
    entry_time_ist: str
    exit_time_ist: str
    entry_px: Decimal
    exit_px: Decimal
    shares: int
    buy_notional_inr: Decimal
    sell_notional_inr: Decimal
    gross_pnl_inr: Decimal
    gross_return_bps: Decimal
    statutory_fees_inr: Decimal
    spread_cost_inr: Decimal
    residual_friction_inr: Decimal
    net_pnl_inr: Decimal
    net_return_bps: Decimal
    exit_reason: str


@dataclass(frozen=True, slots=True)
class ArmEvaluationResult:
    arm_name: str
    scenario_name: str
    total_trades: int
    win_rate: float
    gross_pnl_inr: Decimal
    statutory_fees_inr: Decimal
    spread_cost_inr: Decimal
    friction_cost_inr: Decimal
    net_pnl_inr: Decimal
    mean_gross_bps: float
    mean_net_bps: float
    profit_factor: float
    max_session_loss_inr: Decimal
    bootstrap_net_bps_mean: float
    bootstrap_net_bps_se: float
    bootstrap_net_bps_95_ci: tuple[float, float]
    trades: tuple[SimulatedTrade60M, ...]


def load_development_bars(
    manifest_path: Path,
) -> tuple[dict[str, dict[str, dict[dtime, dict[str, float]]]], dict[str, str], int]:
    """Load continuous bars for active modeling stocks across development window."""
    manifest = json.loads(manifest_path.read_text())
    universe_ids: dict[str, str] = {}
    bars_by_symbol_date: dict[str, dict[str, dict[dtime, dict[str, float]]]] = {}
    verified_files = 0

    for w in manifest["files"]:
        sym = w["symbol"]
        sid = w["security_id"]
        universe_ids[sym] = sid

        if sym in RESERVED_HOLDOUT_STOCKS:
            continue

        parts = w["key"].split("-")
        start_d = "-".join(parts[2:5])
        end_d = "-".join(parts[5:8])
        if end_d > "2025-11-01":
            # Do not read even a response that straddles the temporal holdout.
            continue

        p = Path(w["path"])
        if not p.exists():
            alt = Path("/Volumes/Danny's HDD/AlphaGrid-Plain/historical/raw-dhan") / p.name
            if alt.exists():
                p = alt
        raw_bytes = p.read_bytes()
        if len(raw_bytes) != w["byte_size"] or hashlib.sha256(raw_bytes).hexdigest() != w["sha256"]:
            raise ValueError(f"snapshot integrity violation on {w['key']}")

        raw = json.loads(raw_bytes)
        stamps = raw.get("timestamp") or []
        opens = raw.get("open") or []
        highs = raw.get("high") or []
        lows = raw.get("low") or []
        closes = raw.get("close") or []
        volumes = raw.get("volume") or []

        for ts, o, h, l, c, v in zip(stamps, opens, highs, lows, closes, volumes):
            dt = datetime.fromtimestamp(ts, tz=IST)
            d_str = dt.strftime("%Y-%m-%d")
            if not (DEV_START_DATE <= d_str <= DEV_END_DATE):
                continue
            if d_str in SPECIAL_DR_DATES or d_str in CORPORATE_ACTION_EX_DATES.get(sym, set()):
                continue
            t = dt.time()
            # Retain only regular continuous trading session
            if t < dtime(9, 15) or t > dtime(15, 30):
                continue

            bars_by_symbol_date.setdefault(sym, {}).setdefault(d_str, {})[t] = {
                "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": float(v)
            }
        verified_files += 1

    return bars_by_symbol_date, universe_ids, verified_files


def extract_60m_candidates(
    bars_by_symbol_date: dict[str, dict[str, dict[dtime, dict[str, float]]]],
    universe_ids: dict[str, str],
) -> list[Opportunity60M]:
    """Causal feature extraction and point-in-time 60m outcome computation."""
    # 18 decision slots from 10:00 to 14:15 IST (window matures by 15:15 CAS boundary)
    decision_minutes = [
        h * 60 + m
        for h in range(10, 15)
        for m in (0, 15, 30, 45)
        if h * 60 + m <= 14 * 60 + 15
    ]

    candidates: list[Opportunity60M] = []

    for sym, days_dict in sorted(bars_by_symbol_date.items()):
        sec_id = universe_ids.get(sym, "0")
        for d_str, day_bars in sorted(days_dict.items()):
            # Opening 30-minute range (09:15 to 09:44)
            opening_bars = [day_bars.get(dtime(9, m)) for m in range(15, 45)]
            if any(b is None for b in opening_bars):
                continue
            open_high = max(b["h"] for b in opening_bars if b is not None)
            open_low = min(b["l"] for b in opening_bars if b is not None)
            open_first = day_bars.get(dtime(9, 15))
            if open_first is None or open_first["o"] <= 0:
                continue
            or_width_bps = float((open_high - open_low) / open_first["o"] * 10000.0)

            for dec_mins in decision_minutes:
                t_dec = dtime(dec_mins // 60, dec_mins % 60)
                dec_dt = datetime.combine(datetime.fromisoformat(d_str), t_dec, tzinfo=IST)

                # Point-in-time features computed at T-1 close
                asof_mins = dec_mins - 1
                asof_t = dtime(asof_mins // 60, asof_mins % 60)
                c_bar = day_bars.get(asof_t)
                open_bar = day_bars.get(t_dec)
                if c_bar is None or open_bar is None:
                    continue

                bar_m5 = day_bars.get(dtime((asof_mins - 5) // 60, (asof_mins - 5) % 60))
                bar_m15 = day_bars.get(dtime((asof_mins - 15) // 60, (asof_mins - 15) % 60))
                if bar_m5 is None or bar_m15 is None or bar_m5["c"] <= 0 or bar_m15["c"] <= 0:
                    continue

                # Causal features
                r_5m_bps = float((c_bar["c"] - bar_m5["c"]) / bar_m5["c"] * 10000.0)
                r_15m_past_bps = float((c_bar["c"] - bar_m15["c"]) / bar_m15["c"] * 10000.0)
                breakout_dist_bps = float((c_bar["c"] - open_high) / open_high * 10000.0)

                v_15m = sum(day_bars.get(dtime((asof_mins - m) // 60, (asof_mins - m) % 60), {}).get("v", 0.0) for m in range(15))
                v_prior_30m = sum(day_bars.get(dtime((asof_mins - 15 - m) // 60, (asof_mins - 15 - m) % 60), {}).get("v", 0.0) for m in range(30))
                rel_vol = float(v_15m / (v_prior_30m / 2.0 + 1e-6))

                rets_15m = []
                for m in range(14, -1, -1):
                    b_curr = day_bars.get(dtime((asof_mins - m) // 60, (asof_mins - m) % 60))
                    b_prev = day_bars.get(dtime((asof_mins - m - 1) // 60, (asof_mins - m - 1) % 60))
                    if b_curr and b_prev and b_prev["c"] > 0:
                        rets_15m.append((b_curr["c"] - b_prev["c"]) / b_prev["c"])
                if len(rets_15m) < 10:
                    continue
                vol_15m_bps = float(np.std(rets_15m) * 10000.0)
                tod_norm = float((dec_mins - (9 * 60 + 15)) / 375.0)

                # 60-minute forward window bars: [T, T+59]
                fwd_bars = [day_bars.get(dtime((dec_mins + m) // 60, (dec_mins + m) % 60)) for m in range(60)]
                if any(b is None for b in fwd_bars):
                    # Incomplete 60-minute forward horizon -> exclude
                    continue

                # Bar T Open reference price; historical OHLCV cannot prove a fill.
                entry_px = open_bar["o"]
                if entry_px <= 0:
                    continue

                # Fixed 60m exit: Bar T+59 Close
                b_59 = fwd_bars[59]
                fwd_ret_60m_bps = float((b_59["c"] - entry_px) / entry_px * 10000.0)

                # Intra-window excursions (hindsight upper and lower bounds)
                max_h = max(b["h"] for b in fwd_bars)
                min_l = min(b["l"] for b in fwd_bars)
                mfe_bps = float((max_h - entry_px) / entry_px * 10000.0)
                mae_bps = float((min_l - entry_px) / entry_px * 10000.0)

                # OHLCV touch-based bracket reference (+25/-20 bps), not an observed fill.
                target_px = entry_px * (1.0 + 25.0 / 10000.0)
                stop_px = entry_px * (1.0 - 20.0 / 10000.0)
                bracket_ret = fwd_ret_60m_bps
                bracket_m = 59
                bracket_reason = "TIMEOUT_EXIT"

                for m, b in enumerate(fwd_bars):
                    hit_target = (b["h"] >= target_px)
                    hit_stop = (b["l"] <= stop_px)

                    if hit_target and hit_stop:
                        # Conservative collision handling: Stop triggers first
                        bracket_ret = -20.0
                        bracket_m = m
                        bracket_reason = "STOP_COLLISION"
                        break
                    elif hit_stop:
                        bracket_ret = -20.0
                        bracket_m = m
                        bracket_reason = "STOP_TRIGGERED"
                        break
                    elif hit_target:
                        bracket_ret = +25.0
                        bracket_m = m
                        bracket_reason = "TARGET_TRIGGERED"
                        break

                # Optimistic limit-touch proxy; OHLCV cannot establish queue execution.
                limit_bid_px = entry_px * (1.0 - 2.5 / 10000.0)
                b_0 = fwd_bars[0]
                maker_filled = (b_0["l"] <= limit_bid_px and b_0["v"] >= 100.0)

                if maker_filled:
                    m_target_px = limit_bid_px * (1.0 + 25.0 / 10000.0)
                    m_stop_px = limit_bid_px * (1.0 - 20.0 / 10000.0)
                    m_ret = float((b_59["c"] - limit_bid_px) / limit_bid_px * 10000.0)
                    m_m = 59
                    m_reason = "TIMEOUT_EXIT"

                    for m, b in enumerate(fwd_bars):
                        hit_t = (b["h"] >= m_target_px)
                        hit_s = (b["l"] <= m_stop_px)
                        if hit_t and hit_s:
                            m_ret = -20.0
                            m_m = m
                            m_reason = "STOP_COLLISION"
                            break
                        elif hit_s:
                            m_ret = -20.0
                            m_m = m
                            m_reason = "STOP_TRIGGERED"
                            break
                        elif hit_t:
                            m_ret = +25.0
                            m_m = m
                            m_reason = "TARGET_TRIGGERED"
                            break
                else:
                    m_ret = 0.0
                    m_m = 0
                    m_reason = "LIMIT_UNFILLED"

                features = (r_5m_bps, r_15m_past_bps, breakout_dist_bps, rel_vol, vol_15m_bps, tod_norm, or_width_bps)
                candidates.append(
                    Opportunity60M(
                        symbol=sym,
                        security_id=sec_id,
                        date_str=d_str,
                        decision_time_ist=t_dec.strftime("%H:%M:%S"),
                        decision_dt=dec_dt,
                        entry_open_px=entry_px,
                        features=features,
                        past_15m_return_bps=r_15m_past_bps,
                        breakout_dist_bps=breakout_dist_bps,
                        fwd_60m_return_bps=fwd_ret_60m_bps,
                        fwd_60m_exit_px=float(b_59["c"]),
                        mfe_bps=mfe_bps,
                        mae_bps=mae_bps,
                        bracket_return_bps=bracket_ret,
                        bracket_exit_minute=bracket_m,
                        bracket_exit_reason=bracket_reason,
                        maker_filled=maker_filled,
                        maker_bracket_return_bps=m_ret,
                        maker_exit_minute=m_m,
                        maker_exit_reason=m_reason,
                    )
                )

    return candidates


def simulate_60m_portfolio(
    candidates: list[Opportunity60M],
    arm_name: str,
    friction_scenario: str,
) -> ArmEvaluationResult:
    """Execute chronological simulation under portfolio bounds and realistic costs."""
    # Define friction parameters by scenario
    if friction_scenario == "ZERO_SLIPPAGE_FLOOR":
        quoted_spread_bps = Decimal("5.0") if "MAKER" not in arm_name else Decimal("0.0")
        residual_friction_bps = Decimal("0.0")
    elif friction_scenario == "BASE_REALISTIC":
        quoted_spread_bps = Decimal("5.0") if "MAKER" not in arm_name else Decimal("0.0")
        residual_friction_bps = Decimal("5.0") if "MAKER" not in arm_name else Decimal("2.5")
    elif friction_scenario == "CONSERVATIVE_STRESS":
        quoted_spread_bps = Decimal("10.0") if "MAKER" not in arm_name else Decimal("5.0")
        residual_friction_bps = Decimal("10.0") if "MAKER" not in arm_name else Decimal("5.0")
    else:
        raise ValueError(f"Unknown friction scenario: {friction_scenario}")

    if arm_name == "NO_TRADE":
        return ArmEvaluationResult(
            arm_name=arm_name,
            scenario_name=friction_scenario,
            total_trades=0,
            win_rate=0.0,
            gross_pnl_inr=Decimal("0"),
            statutory_fees_inr=Decimal("0"),
            spread_cost_inr=Decimal("0"),
            friction_cost_inr=Decimal("0"),
            net_pnl_inr=Decimal("0"),
            mean_gross_bps=0.0,
            mean_net_bps=0.0,
            profit_factor=0.0,
            max_session_loss_inr=Decimal("0"),
            bootstrap_net_bps_mean=0.0,
            bootstrap_net_bps_se=0.0,
            bootstrap_net_bps_95_ci=(0.0, 0.0),
            trades=(),
        )

    by_slot: dict[str, list[Opportunity60M]] = {}
    for c in candidates:
        by_slot.setdefault(f"{c.date_str}_{c.decision_time_ist}", []).append(c)
    for day in {c.date_str for c in candidates}:
        by_slot.setdefault(f"{day}_15:15:00", [])

    cash = PORTFOLIO_CAPITAL
    positions: dict[str, dict[str, Any]] = {}
    trades: list[SimulatedTrade60M] = []
    session_loss = Decimal("0")
    max_session_loss = Decimal("0")
    cur_date_str: str | None = None

    for slot_key in sorted(by_slot):
        slot_cands = by_slot[slot_key]
        d_str, slot_time = slot_key.split("_", 1)
        cur_dt = datetime.fromisoformat(f"{d_str}T{slot_time}").replace(tzinfo=IST)

        # Every position must settle at the prior session's 15:15 slot.
        if cur_date_str is not None and d_str != cur_date_str:
            if positions:
                raise ValueError("unsettled positions at session boundary")
            session_loss = Decimal("0")
        cur_date_str = d_str

        # 1. Close matured positions
        to_close = [sym for sym, pos in positions.items() if pos["exit_dt"] <= cur_dt]
        for sym in to_close:
            pos = positions.pop(sym)
            c = pos["cand"]
            buy_notional = pos["notional"]
            gross_ret_bps = Decimal(str(pos["gross_ret_bps"]))
            gross_pnl = buy_notional * (gross_ret_bps / Decimal("10000"))
            sell_notional = buy_notional + gross_pnl
            exit_px = pos["entry_px"] * (Decimal("1") + gross_ret_bps / Decimal("10000"))

            fees = compute_dhan_intraday_fees(buy_notional, sell_notional)
            spread_cost = buy_notional * (quoted_spread_bps / Decimal("10000"))
            friction_cost = buy_notional * (residual_friction_bps / Decimal("10000"))
            net_pnl = gross_pnl - fees.total_statutory_fees - spread_cost - friction_cost
            net_ret_bps = (net_pnl / buy_notional) * Decimal("10000") if buy_notional > 0 else Decimal("0")

            cash += buy_notional + net_pnl
            session_loss += min(Decimal("0"), net_pnl)
            max_session_loss = min(max_session_loss, session_loss)

            trades.append(
                SimulatedTrade60M(
                    symbol=sym,
                    date_str=c.date_str,
                    entry_time_ist=pos["entry_dt"].strftime("%H:%M"),
                    exit_time_ist=pos["exit_dt"].strftime("%H:%M"),
                    entry_px=pos["entry_px"],
                    exit_px=exit_px,
                    shares=pos["shares"],
                    buy_notional_inr=buy_notional,
                    sell_notional_inr=sell_notional,
                    gross_pnl_inr=gross_pnl,
                    gross_return_bps=gross_ret_bps,
                    statutory_fees_inr=fees.total_statutory_fees,
                    spread_cost_inr=spread_cost,
                    residual_friction_inr=friction_cost,
                    net_pnl_inr=net_pnl,
                    net_return_bps=net_ret_bps,
                    exit_reason=pos["exit_reason"],
                )
            )

        # 2. Risk check: Session loss limit
        if abs(session_loss) >= PORTFOLIO_SESSION_LOSS_LIMIT:
            continue

        # 3. Filter candidates for the evaluated arm
        eligible: list[Opportunity60M] = []
        for cand in slot_cands:
            if cand.symbol in positions:
                continue

            if arm_name == "UNCONDITIONAL_NULL":
                eligible.append(cand)
            elif arm_name == "MOMENTUM_FIXED_60M":
                if cand.past_15m_return_bps >= 35.0:
                    eligible.append(cand)
            elif arm_name in ("BREAKOUT_FIXED_60M", "BREAKOUT_BRACKET_ASYMMETRIC"):
                if cand.breakout_dist_bps > 0.0 and cand.past_15m_return_bps > 0.0:
                    eligible.append(cand)
            elif arm_name == "MAKER_BRACKET_PASSIVE":
                if cand.breakout_dist_bps > 0.0 and cand.past_15m_return_bps > 0.0 and cand.maker_filled:
                    eligible.append(cand)

        # 4. Allocate capital up to portfolio limits
        for cand in eligible:
            if len(positions) >= PORTFOLIO_MAX_POSITIONS:
                break
            deployed = sum(p["notional"] for p in positions.values())
            if deployed + PORTFOLIO_MAX_POSITION > PORTFOLIO_MAX_DEPLOYED:
                break
            if cash < PORTFOLIO_MAX_POSITION:
                break

            entry_px = Decimal(str(cand.entry_open_px)) if "MAKER" not in arm_name else Decimal(str(cand.entry_open_px * (1.0 - 2.5 / 10000.0)))
            shares = int(PORTFOLIO_MAX_POSITION / entry_px)
            if shares <= 0:
                continue
            notional = entry_px * Decimal(shares)
            cash -= notional

            if arm_name in ("UNCONDITIONAL_NULL", "MOMENTUM_FIXED_60M", "BREAKOUT_FIXED_60M"):
                gross_ret = cand.fwd_60m_return_bps
                exit_mins = 60
                exit_reason = "TIMEOUT_60M"
            elif arm_name == "BREAKOUT_BRACKET_ASYMMETRIC":
                gross_ret = cand.bracket_return_bps
                exit_mins = cand.bracket_exit_minute + 1
                exit_reason = cand.bracket_exit_reason
            elif arm_name == "MAKER_BRACKET_PASSIVE":
                gross_ret = cand.maker_bracket_return_bps
                exit_mins = cand.maker_exit_minute + 1
                exit_reason = cand.maker_exit_reason
            else:
                raise ValueError(arm_name)

            positions[cand.symbol] = {
                "cand": cand,
                "entry_dt": cur_dt,
                "exit_dt": cur_dt + timedelta(minutes=exit_mins),
                "entry_px": entry_px,
                "shares": shares,
                "notional": notional,
                "gross_ret_bps": gross_ret,
                "exit_reason": exit_reason,
            }

    # Close remaining positions at session end
    for sym, pos in list(positions.items()):
        c = pos["cand"]
        buy_notional = pos["notional"]
        gross_ret_bps = Decimal(str(pos["gross_ret_bps"]))
        gross_pnl = buy_notional * (gross_ret_bps / Decimal("10000"))
        sell_notional = buy_notional + gross_pnl
        exit_px = pos["entry_px"] * (Decimal("1") + gross_ret_bps / Decimal("10000"))

        fees = compute_dhan_intraday_fees(buy_notional, sell_notional)
        spread_cost = buy_notional * (quoted_spread_bps / Decimal("10000"))
        friction_cost = buy_notional * (residual_friction_bps / Decimal("10000"))
        net_pnl = gross_pnl - fees.total_statutory_fees - spread_cost - friction_cost
        net_ret_bps = (net_pnl / buy_notional) * Decimal("10000") if buy_notional > 0 else Decimal("0")

        cash += buy_notional + net_pnl
        trades.append(
            SimulatedTrade60M(
                symbol=sym,
                date_str=c.date_str,
                entry_time_ist=pos["entry_dt"].strftime("%H:%M"),
                exit_time_ist="15:15",
                entry_px=pos["entry_px"],
                exit_px=exit_px,
                shares=pos["shares"],
                buy_notional_inr=buy_notional,
                sell_notional_inr=sell_notional,
                gross_pnl_inr=gross_pnl,
                gross_return_bps=gross_ret_bps,
                statutory_fees_inr=fees.total_statutory_fees,
                spread_cost_inr=spread_cost,
                residual_friction_inr=friction_cost,
                net_pnl_inr=net_pnl,
                net_return_bps=net_ret_bps,
                exit_reason="SESSION_CLOSE",
            )
        )

    # Calculate summary metrics
    n_trades = len(trades)
    if n_trades == 0:
        return ArmEvaluationResult(
            arm_name=arm_name,
            scenario_name=friction_scenario,
            total_trades=0,
            win_rate=0.0,
            gross_pnl_inr=Decimal("0"),
            statutory_fees_inr=Decimal("0"),
            spread_cost_inr=Decimal("0"),
            friction_cost_inr=Decimal("0"),
            net_pnl_inr=Decimal("0"),
            mean_gross_bps=0.0,
            mean_net_bps=0.0,
            profit_factor=0.0,
            max_session_loss_inr=Decimal("0"),
            bootstrap_net_bps_mean=0.0,
            bootstrap_net_bps_se=0.0,
            bootstrap_net_bps_95_ci=(0.0, 0.0),
            trades=(),
        )

    win_trades = sum(1 for t in trades if t.net_pnl_inr > 0)
    win_rate = float(win_trades / n_trades)
    tot_gross = sum((t.gross_pnl_inr for t in trades), Decimal("0"))
    tot_fees = sum((t.statutory_fees_inr for t in trades), Decimal("0"))
    tot_spread = sum((t.spread_cost_inr for t in trades), Decimal("0"))
    tot_friction = sum((t.residual_friction_inr for t in trades), Decimal("0"))
    tot_net = sum((t.net_pnl_inr for t in trades), Decimal("0"))
    mean_gross_bps = float(np.mean([float(t.gross_return_bps) for t in trades]))
    mean_net_bps = float(np.mean([float(t.net_return_bps) for t in trades]))

    gross_gains = sum((t.gross_pnl_inr for t in trades if t.gross_pnl_inr > 0), Decimal("0"))
    gross_losses = abs(sum((t.gross_pnl_inr for t in trades if t.gross_pnl_inr < 0), Decimal("0")))
    pf = float(gross_gains / gross_losses) if gross_losses > 0 else 999.0

    # Session-clustered block bootstrap on mean net bps (2,000 resamples)
    sessions_dict: dict[str, list[float]] = {}
    for t in trades:
        sessions_dict.setdefault(t.date_str, []).append(float(t.net_return_bps))
    session_keys = np.array(sorted(sessions_dict.keys()))
    n_sessions = len(session_keys)

    rng = np.random.default_rng(20261107)
    boot_means = np.zeros(2000, dtype=np.float64)
    for b in range(2000):
        sampled = rng.choice(session_keys, size=n_sessions, replace=True)
        sample_vals = []
        for s in sampled:
            sample_vals.extend(sessions_dict[s])
        boot_means[b] = np.mean(sample_vals)

    b_mean = float(np.mean(boot_means))
    b_se = float(np.std(boot_means))
    ci_low, ci_high = np.percentile(boot_means, [2.5, 97.5])

    return ArmEvaluationResult(
        arm_name=arm_name,
        scenario_name=friction_scenario,
        total_trades=n_trades,
        win_rate=win_rate,
        gross_pnl_inr=tot_gross,
        statutory_fees_inr=tot_fees,
        spread_cost_inr=tot_spread,
        friction_cost_inr=tot_friction,
        net_pnl_inr=tot_net,
        mean_gross_bps=mean_gross_bps,
        mean_net_bps=mean_net_bps,
        profit_factor=pf,
        max_session_loss_inr=max_session_loss,
        bootstrap_net_bps_mean=b_mean,
        bootstrap_net_bps_se=b_se,
        bootstrap_net_bps_95_ci=(float(ci_low), float(ci_high)),
        trades=tuple(trades),
    )


def main() -> None:
    print("=" * 80)
    print("ALPHA GRID EXP-60M-BRACKETED-OPPORTUNITY-V1: STAGE 2 EVALUATION")
    print("=" * 80)

    manifest_path = Path("data/research_snapshot_manifest_plain_v2.json")
    if not manifest_path.exists():
        manifest_path = Path("data/research_snapshot_manifest_v1.json")
    if not manifest_path.exists():
        print("Error: manifest not found", file=sys.stderr)
        sys.exit(1)

    t0 = time.time()
    bars_by_symbol_date, universe_ids, verified_files = load_development_bars(manifest_path)
    t1 = time.time()
    print(f"\n1. DATASET INTEGRITY & SPLIT PROVENANCE:")
    print(f"   Verified Development Files: {verified_files} (SHA-256 and byte counts verified)")
    print(f"   Active Modeling Symbols:    {len(bars_by_symbol_date)} (Reserved 5 holdouts strictly excluded)")
    print(f"   Development Period:         {DEV_START_DATE} to {DEV_END_DATE} (Pre-Nov 2025 only)")
    print(f"   Temporal Holdout:           Nov 2025 to Oct 2026 (STRICTLY LOCKED / UNINSPECTED)")
    print(f"   Loading Time:               {t1 - t0:.2f}s")

    print("\n2. EXTRACTING POINT-IN-TIME 60M CANDIDATES & EXCURSIONS...")
    candidates = extract_60m_candidates(bars_by_symbol_date, universe_ids)
    print(f"   Total Eligible 60m Slots:   {len(candidates):,} across {len({c.date_str for c in candidates})} sessions")

    # Split into Train split (2023-11-01 to 2025-04-30) and Calibration split (2025-05-01 to 2025-10-31)
    train_cands = [c for c in candidates if c.date_str < CALIBRATION_START_DATE]
    cal_cands = [c for c in candidates if CALIBRATION_START_DATE <= c.date_str <= CALIBRATION_END_DATE]
    print(f"   - Split 1 (Train Period):   {len(train_cands):,} candidates")
    print(f"   - Split 2 (Calibration):    {len(cal_cands):,} candidates")

    # 3. Opportunity Excursion Distribution & Realizability Analysis
    print("\n" + "=" * 80)
    print("3. EXCURSION DYNAMICS: HINDSIGHT MFE/MAE VS REFERENCE EXITS")
    print("=" * 80)

    for name, c_list in [("Split 1 (Train Period)", train_cands), ("Split 2 (Calibration Period)", cal_cands), ("Full Development Period", candidates)]:
        breakouts = [c for c in c_list if c.breakout_dist_bps > 0 and c.past_15m_return_bps > 0]
        n_bo = len(breakouts)
        if n_bo == 0:
            continue
        mfe_vals = [c.mfe_bps for c in breakouts]
        mae_vals = [c.mae_bps for c in breakouts]
        fwd60_vals = [c.fwd_60m_return_bps for c in breakouts]
        brk_vals = [c.bracket_return_bps for c in breakouts]

        # Trigger rates for bracket
        target_hits = sum(1 for c in breakouts if c.bracket_exit_reason == "TARGET_TRIGGERED")
        stop_hits = sum(1 for c in breakouts if c.bracket_exit_reason in ("STOP_TRIGGERED", "STOP_COLLISION"))
        timeout_hits = sum(1 for c in breakouts if c.bracket_exit_reason == "TIMEOUT_EXIT")

        print(f"\n   [{name}] Active Breakout Candidates (N = {n_bo:,}):")
        print(f"   Hindsight MFE:           Mean={np.mean(mfe_vals):+6.2f} bps | Median={np.median(mfe_vals):+6.2f} bps | Std={np.std(mfe_vals):5.1f}")
        print(f"   Hindsight MAE:           Mean={np.mean(mae_vals):+6.2f} bps | Median={np.median(mae_vals):+6.2f} bps | Std={np.std(mae_vals):5.1f}")
        print(f"   Fixed 60m reference:    Mean={np.mean(fwd60_vals):+6.2f} bps | Win Rate (>0)={np.mean(np.array(fwd60_vals)>0)*100:4.1f}%")
        print(f"   Bracket touch reference (+25/-20): Mean={np.mean(brk_vals):+6.2f} bps | Target Hits={target_hits/n_bo*100:4.1f}% | Stop Hits={stop_hits/n_bo*100:4.1f}% | Timeouts={timeout_hits/n_bo*100:4.1f}%")

    affected = unreconstructed_price_windows(json.loads(manifest_path.read_text()),
                                             DEV_START_DATE, DEV_END_DATE,
                                             set(RESERVED_HOLDOUT_STOCKS))
    if affected:
        print(f"ECONOMIC_INELIGIBLE_UNRECONSTRUCTED_PRICE_BASIS: {len(affected)} source windows")
        return

    # 4. Comparative Arm Evaluation Across Friction Scenarios
    arms_to_eval = [
        "NO_TRADE",
        "UNCONDITIONAL_NULL",
        "MOMENTUM_FIXED_60M",
        "BREAKOUT_FIXED_60M",
        "BREAKOUT_BRACKET_ASYMMETRIC",
        "MAKER_BRACKET_PASSIVE",
    ]

    scenarios = [
        ("ZERO_SLIPPAGE_FLOOR", "Zero Slippage Floor (15.5 bps Taker / 10.5 bps Maker)"),
        ("BASE_REALISTIC", "Base Realistic Slippage (20.5 bps Taker / 13.0 bps Maker)"),
        ("CONSERVATIVE_STRESS", "Conservative Stress (30.5 bps Taker / 20.5 bps Maker)"),
    ]

    print("\n" + "=" * 80)
    print("4. PORTFOLIO REFERENCE RESULTS: INSPECTED CALIBRATION/DEVELOPMENT SPLIT")
    print("Limits: ₹15k max deployed, ₹5k/pos, max 3 positions, ₹1,500 daily loss limit")
    print("=" * 80)

    for sc_id, sc_label in scenarios:
        print(f"\n   --- Scenario: {sc_label} ---")
        print(f"   {'Arm':<28} {'Trades':<7} {'Win%':<7} {'Gross PnL':<11} {'Fees':<9} {'Spread':<9} {'Net PnL':<12} {'Mean Net':<10} {'ProfitFactor':<12} {'95% Bootstrap CI'}")
        print("   " + "-" * 125)
        for arm in arms_to_eval:
            res = simulate_60m_portfolio(cal_cands, arm, sc_id)
            ci_str = f"[{res.bootstrap_net_bps_95_ci[0]:+.2f}, {res.bootstrap_net_bps_95_ci[1]:+.2f}]"
            print(f"   {res.arm_name:<28} {res.total_trades:<7} {res.win_rate*100:<6.1f}% ₹{res.gross_pnl_inr:<10.2f} ₹{res.statutory_fees_inr:<8.2f} ₹{res.spread_cost_inr:<8.2f} ₹{res.net_pnl_inr:<11.2f} {res.mean_net_bps:<10.2f} {res.profit_factor:<12.2f} {ci_str}")

    print("\n" + "=" * 80)
    print("5. PORTFOLIO SIMULATION RESULTS: FULL 2-YEAR DEVELOPMENT WINDOW (2023-11 to 2025-10)")
    print("=" * 80)

    for sc_id, sc_label in scenarios:
        print(f"\n   --- Scenario: {sc_label} ---")
        print(f"   {'Arm':<28} {'Trades':<7} {'Win%':<7} {'Gross PnL':<11} {'Fees':<9} {'Spread':<9} {'Net PnL':<12} {'Mean Net':<10} {'ProfitFactor':<12} {'95% Bootstrap CI'}")
        print("   " + "-" * 125)
        for arm in arms_to_eval:
            res = simulate_60m_portfolio(candidates, arm, sc_id)
            ci_str = f"[{res.bootstrap_net_bps_95_ci[0]:+.2f}, {res.bootstrap_net_bps_95_ci[1]:+.2f}]"
            print(f"   {res.arm_name:<28} {res.total_trades:<7} {res.win_rate*100:<6.1f}% ₹{res.gross_pnl_inr:<10.2f} ₹{res.statutory_fees_inr:<8.2f} ₹{res.spread_cost_inr:<8.2f} ₹{res.net_pnl_inr:<11.2f} {res.mean_net_bps:<10.2f} {res.profit_factor:<12.2f} {ci_str}")

    print("\n" + "=" * 80)
    print("6. PREDECLARED STOP CRITERIA & ML VIABILITY VERDICT")
    print("=" * 80)

    # Test against primary acceptance criteria under Base Realistic Slippage
    res_bracket_cal = simulate_60m_portfolio(cal_cands, "BREAKOUT_BRACKET_ASYMMETRIC", "BASE_REALISTIC")
    res_maker_cal = simulate_60m_portfolio(cal_cands, "MAKER_BRACKET_PASSIVE", "BASE_REALISTIC")
    res_floor_cal = simulate_60m_portfolio(cal_cands, "BREAKOUT_BRACKET_ASYMMETRIC", "ZERO_SLIPPAGE_FLOOR")

    survives_base = (res_bracket_cal.bootstrap_net_bps_95_ci[0] > 0.0 or res_maker_cal.bootstrap_net_bps_95_ci[0] > 0.0)
    survives_floor = (res_floor_cal.mean_net_bps > 0.0)

    print(f"   Primary Economic Null Rejected (95% CI > 0 under Base Friction): {survives_base}")
    print(f"   Friction Floor Survival (> 0 net under Zero Slippage Floor):       {survives_floor}")
    print(f"   Arm 3 (Bracket Taker) Net bps: Mean = {res_bracket_cal.mean_net_bps:+.2f} bps, 95% CI = [{res_bracket_cal.bootstrap_net_bps_95_ci[0]:+.2f}, {res_bracket_cal.bootstrap_net_bps_95_ci[1]:+.2f}] bps")
    print(f"   Arm 4 (Maker Bracket) Net bps: Mean = {res_maker_cal.mean_net_bps:+.2f} bps, 95% CI = [{res_maker_cal.bootstrap_net_bps_95_ci[0]:+.2f}, {res_maker_cal.bootstrap_net_bps_95_ci[1]:+.2f}] bps")

    if not survives_floor and not survives_base:
        print("\n   PREDECLARED STOP RULE TRIGGERED:")
        print("   These tested OHLCV reference arms fail the assumed fee and spread hurdle.")
        print("   Passive limit touches do not establish queue fills or cash-equity-wide impossibility.")
        print("   VERDICT: STOP THE TESTED ARMS; NO TRADING OR HOLDOUT RELEASE.")
    else:
        print("\n   PREDECLARED ACCEPTANCE RULE MET:")
        print("   VERDICT: GO FOR STAGE 3 ML MODELING.")
    print("=" * 80)


if __name__ == "__main__":
    main()
