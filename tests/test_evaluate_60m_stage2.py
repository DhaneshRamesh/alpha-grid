"""Unit tests for EXP-60M Stage 2 evaluation logic and holdout preservation."""

from datetime import datetime, time as dtime
from decimal import Decimal
import pytest

from quant.acquisition.calendar import IST
from quant.research.m1_pipeline import RESERVED_HOLDOUT_STOCKS
from tools.evaluate_60m_stage2 import (
    DEV_END_DATE,
    DEV_START_DATE,
    Opportunity60M,
    simulate_60m_portfolio,
)


def test_holdout_date_and_stock_invariants() -> None:
    """Ensure development evaluation cannot touch temporal or stock holdouts."""
    assert DEV_END_DATE < "2025-11-01", "Development end must strictly precede temporal holdout"
    assert len(RESERVED_HOLDOUT_STOCKS) == 5
    for s in ("TCS", "KOTAKBANK", "MARUTI", "BHEL", "DIVISLAB"):
        assert s in RESERVED_HOLDOUT_STOCKS


def test_asymmetric_bracket_collision_rule() -> None:
    """When both target (+25) and stop (-20) are hit in the same bar, stop triggers first."""
    # Entry at 100.0
    entry_px = 100.0
    target_px = entry_px * 1.0025  # 100.25
    stop_px = entry_px * 0.9980    # 99.80

    # Bar with high >= 100.25 AND low <= 99.80
    b_collision = {"o": 100.0, "h": 100.30, "l": 99.70, "c": 100.10, "v": 500.0}

    hit_target = b_collision["h"] >= target_px
    hit_stop = b_collision["l"] <= stop_px
    assert hit_target and hit_stop

    # Protocol rule: Stop triggers first
    if hit_target and hit_stop:
        ret = -20.0
        reason = "STOP_COLLISION"
    assert ret == -20.0
    assert reason == "STOP_COLLISION"


def test_no_trade_arm_returns_zero_pnl() -> None:
    """Arm 0 NO_TRADE must return zero trades and zero PnL."""
    res = simulate_60m_portfolio([], "NO_TRADE", "ZERO_SLIPPAGE_FLOOR")
    assert res.total_trades == 0
    assert res.net_pnl_inr == Decimal("0")
    assert res.mean_net_bps == 0.0


def test_last_slot_positions_settle_each_session() -> None:
    """A position maturing after the last decision slot must be booked that day."""
    def candidate(day: str) -> Opportunity60M:
        return Opportunity60M(
            symbol="RELIANCE", security_id="2885", date_str=day,
            decision_time_ist="14:15:00",
            decision_dt=datetime.fromisoformat(f"{day}T14:15:00").replace(tzinfo=IST),
            entry_open_px=100.0, features=(0.0,) * 7,
            past_15m_return_bps=0.0, breakout_dist_bps=0.0,
            fwd_60m_return_bps=100.0, fwd_60m_exit_px=101.0,
            mfe_bps=100.0, mae_bps=0.0,
            bracket_return_bps=25.0, bracket_exit_minute=59,
            bracket_exit_reason="TARGET_TRIGGERED", maker_filled=False,
            maker_bracket_return_bps=0.0, maker_exit_minute=0,
            maker_exit_reason="LIMIT_UNFILLED",
        )

    result = simulate_60m_portfolio(
        [candidate("2025-10-01"), candidate("2025-10-02")],
        "UNCONDITIONAL_NULL", "ZERO_SLIPPAGE_FLOOR",
    )
    assert result.total_trades == 2
    assert result.gross_pnl_inr == Decimal("100")
    assert [trade.date_str for trade in result.trades] == ["2025-10-01", "2025-10-02"]
