"""Unit tests for the First Cross-Equity ML Research Model."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import Decimal

import numpy as np
import pytest

from quant.acquisition.calendar import IST
from quant.research.cross_equity_model import (
    OpportunityCandidate,
    RegularizedLogisticRegression,
    brier_score,
    evaluate_data_sufficiency,
    extract_opportunity_candidates,
    log_loss,
    simulate_portfolio_arm,
)


def test_logistic_regression_fit_and_predict_convergence() -> None:
    rng = np.random.default_rng(42)
    x_train = rng.normal(size=(200, 5))
    true_w = np.array([0.6, -0.4, 0.5, -0.7, 0.2])
    true_z = x_train @ true_w - 0.5
    true_p = 1.0 / (1.0 + np.exp(-true_z))
    y_train = (rng.uniform(size=200) < true_p).astype(np.int_)

    model = RegularizedLogisticRegression(l2_penalty=0.5)
    with pytest.raises(ValueError, match="model is not fitted"):
        model.predict_proba(x_train[:5])

    model.fit(x_train, y_train)
    assert model.weights is not None
    assert model.intercept is not None
    assert len(model.weights) == 5

    preds = model.predict_proba(x_train)
    assert preds.shape == (200,)
    assert np.all((preds >= 0.0) & (preds <= 1.0))

    # Test positive correlation between predicted probs and true log-odds
    corr = np.corrcoef(preds, true_z)[0, 1]
    assert corr > 0.65


def test_brier_score_and_log_loss_properties() -> None:
    y_true = np.array([1, 0, 1, 0], dtype=np.int_)
    # Perfect predictions
    p_perf = np.array([1.0, 0.0, 1.0, 0.0])
    assert brier_score(y_true, p_perf) == pytest.approx(0.0)
    assert log_loss(y_true, p_perf) < 1e-10

    # Inverted predictions
    p_bad = np.array([0.0, 1.0, 0.0, 1.0])
    assert brier_score(y_true, p_bad) == pytest.approx(1.0)
    assert log_loss(y_true, p_bad) > 30.0

    # Constant base rate 0.5
    p_half = np.array([0.5, 0.5, 0.5, 0.5])
    assert brier_score(y_true, p_half) == pytest.approx(0.25)
    assert log_loss(y_true, p_half) == pytest.approx(float(np.log(2)))


def test_candidate_feature_extraction_and_types() -> None:
    # Build synthetic 1-minute bars for a single symbol
    day_str = "2026-10-26"
    bars_dict: dict[time, dict[str, float | int]] = {}
    base_dt = datetime.combine(datetime.fromisoformat(day_str), time(9, 15), tzinfo=IST)

    # 375 minutes of bars
    for m in range(375):
        dt = base_dt + timedelta(minutes=m)
        t_obj = dt.time()
        # Opening 30 min (9:15 to 9:44): range 100 to 105
        # Post 9:45: stock breaks out to 110, then pulls back to 104 at 10:30
        if m < 30:
            b_open, b_high, b_low, b_close, b_vol = 100.0, 105.0, 100.0, 102.0, 1000
        elif 30 <= m < 50:
            b_open, b_high, b_low, b_close, b_vol = 102.0, 110.0, 101.0, 108.0, 2000
        else:
            b_open, b_high, b_low, b_close, b_vol = 104.0, 105.0, 103.0, 104.0, 1500
        bars_dict[t_obj] = {
            "o": b_open, "h": b_high, "l": b_low, "c": b_close, "v": b_vol
        }

    nested_bars = {"TEST_SYM": {day_str: bars_dict}}
    candidates = extract_opportunity_candidates(nested_bars, {"TEST_SYM": "999"})

    assert len(candidates) > 0
    # At 10:00: price 108 > open_high 105 -> ACTIVE_BREAKOUT
    c_1000 = next(c for c in candidates if c.decision_time_ist == "10:00:00")
    assert c_1000.candidate_type == "ACTIVE_BREAKOUT"
    assert c_1000.breakout_distance_bps > 0.0

    # At 11:00: price 104 <= open_high 105, touched 110 -> FAILED_BREAKOUT_PULLBACK
    c_1100 = next(c for c in candidates if c.decision_time_ist == "11:00:00")
    assert c_1100.candidate_type == "FAILED_BREAKOUT_PULLBACK"
    assert c_1100.breakout_distance_bps <= 0.0


def test_portfolio_simulation_respects_limits_and_statutory_costs() -> None:
    # Create 5 synthetic candidates at the same decision slot
    dt = datetime(2026, 10, 26, 10, 0, tzinfo=IST)
    cands: list[OpportunityCandidate] = []
    for i in range(5):
        cands.append(
            OpportunityCandidate(
                symbol=f"SYM_{i}",
                security_id=str(100 + i),
                date_str="2026-10-26",
                decision_time_ist="10:00:00",
                decision_dt=dt,
                entry_reference_price_inr=100.0,
                features=(10.0, 5.0, 1.2, 5.0, 0.1),
                past_15m_return_bps=40.0,  # All qualify for MOMENTUM_RULE
                breakout_distance_bps=10.0,
                forward_15m_return_bps=50.0,  # +50 bps gross profit
                forward_15m_exit_price_inr=100.50,
                max_favorable_excursion_bps=60.0,
                max_adverse_excursion_bps=-5.0,
                favorable_event=1,
                adverse_event=0,
                candidate_type="ACTIVE_BREAKOUT",
            )
        )

    # 1. NO_TRADE returns 0 trades
    res_no_trade = simulate_portfolio_arm(cands, "NO_TRADE")
    assert res_no_trade.total_trades == 0
    assert res_no_trade.net_pnl_inr == Decimal("0")

    # 2. MOMENTUM_RULE must cap at 3 positions (PORTFOLIO_MAX_POSITIONS = 3)
    res_mom = simulate_portfolio_arm(cands, "MOMENTUM_RULE")
    assert res_mom.total_trades == 3
    assert len(res_mom.trades) == 3

    # Capital deployment: 3 * 50 shares * 100 = ₹15,000 (PORTFOLIO_MAX_DEPLOYED)
    total_deployed = sum(t.buy_notional_inr for t in res_mom.trades)
    assert total_deployed == Decimal("15000.00")

    # Statutory fees and spread must be strictly positive
    assert res_mom.statutory_fees_inr > Decimal("0")
    assert res_mom.spread_cost_inr > Decimal("0")
    # Gross P&L: 3 * (100.50 - 100.00) * 50 = ₹75.00
    assert res_mom.gross_pnl_inr == Decimal("75.00")
    # Net P&L = Gross - fees - spread
    costs = res_mom.statutory_fees_inr + res_mom.spread_cost_inr
    assert res_mom.net_pnl_inr == (res_mom.gross_pnl_inr - costs)


def test_sufficiency_evaluator_fails_closed_on_five_sessions() -> None:
    # 5 sessions < 20 required sessions
    dt = datetime(2026, 10, 26, 10, 0, tzinfo=IST)
    cands: list[OpportunityCandidate] = []
    for day_idx in range(5):
        cands.append(
            OpportunityCandidate(
                symbol="RELIANCE",
                security_id="2885",
                date_str=f"2026-10-2{day_idx + 6}",
                decision_time_ist="10:00:00",
                decision_dt=dt + timedelta(days=day_idx),
                entry_reference_price_inr=100.0,
                features=(0.0, 0.0, 1.0, 5.0, 0.1),
                past_15m_return_bps=0.0,
                breakout_distance_bps=0.0,
                forward_15m_return_bps=0.0,
                forward_15m_exit_price_inr=100.0,
                max_favorable_excursion_bps=0.0,
                max_adverse_excursion_bps=0.0,
                favorable_event=1,
                adverse_event=1,
                candidate_type="CONSOLIDATION_WITHIN_RANGE",
            )
        )

    res = evaluate_data_sufficiency(cands)
    assert res.status == "NOT_TRAINED"
    assert any("insufficient_sessions: 5 < 20" in r for r in res.reasons)
    assert any("insufficient_usable_labels" in r for r in res.reasons)
