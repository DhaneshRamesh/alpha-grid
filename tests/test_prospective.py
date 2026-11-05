"""Tests for prospective statistical edge measurement protocol."""

from decimal import Decimal
import pytest

from quant.research.prospective import (
    compute_dhan_intraday_fees,
    ExecutionAttribution,
    ProspectiveSpecification,
)


def test_dhan_intraday_fee_scaling() -> None:
    # 1. Micro position: ₹5,000 buy and ₹5,000 sell
    f5k = compute_dhan_intraday_fees(Decimal("5000"), Decimal("5000"))
    assert f5k.brokerage_buy == Decimal("1.50")
    assert f5k.brokerage_sell == Decimal("1.50")
    assert f5k.total_brokerage == Decimal("3.00")
    assert f5k.stt_sell == Decimal("1")  # 5000 * 0.00025 = 1.25 -> 1
    assert f5k.nse_transaction == Decimal("0.31")  # 10000 * 0.000030699 = 0.30699 -> 0.31
    assert f5k.sebi_turnover == Decimal("0.01")  # 10000 * 0.000001 = 0.01
    assert f5k.gst == Decimal("0.60")  # 18% of (3.00 + 0.31 + 0.01) = 0.5976 -> 0.60
    assert f5k.stamp_duty_buy == Decimal("0")  # 5000 * 0.00003 = 0.15 -> 0
    assert f5k.total_statutory_fees == Decimal("4.92")
    assert f5k.fee_burden_bps == Decimal("9.84")

    # 2. Portfolio deployment ceiling: ₹15,000 buy and ₹15,000 sell
    f15k = compute_dhan_intraday_fees(Decimal("15000"), Decimal("15000"))
    assert f15k.brokerage_buy == Decimal("4.50")
    assert f15k.brokerage_sell == Decimal("4.50")
    assert f15k.total_brokerage == Decimal("9.00")
    assert f15k.stt_sell == Decimal("4")  # 15000 * 0.00025 = 3.75 -> 4
    assert f15k.nse_transaction == Decimal("0.92")  # 30000 * 0.000030699 = 0.92097 -> 0.92
    assert f15k.sebi_turnover == Decimal("0.03")  # 30000 * 0.000001 = 0.03
    assert f15k.gst == Decimal("1.79")  # 18% of (9.00 + 0.92 + 0.03) = 1.791 -> 1.79
    assert f15k.stamp_duty_buy == Decimal("0")  # 15000 * 0.00003 = 0.45 -> 0
    assert f15k.total_statutory_fees == Decimal("15.74")
    assert f15k.fee_burden_bps == Decimal("10.49")

    # 3. Reference research notional: ₹100,000 buy and ₹100,000 sell
    f100k = compute_dhan_intraday_fees(Decimal("100000"), Decimal("100000"))
    # Brokerage capped at ₹20 per leg
    assert f100k.brokerage_buy == Decimal("20.00")
    assert f100k.brokerage_sell == Decimal("20.00")
    assert f100k.total_brokerage == Decimal("40.00")
    assert f100k.stt_sell == Decimal("25")  # 100000 * 0.00025 = 25
    assert f100k.nse_transaction == Decimal("6.14")  # 200000 * 0.000030699 = 6.1398 -> 6.14
    assert f100k.sebi_turnover == Decimal("0.20")  # 200000 * 0.000001 = 0.20
    assert f100k.gst == Decimal("8.34")  # 18% of (40.00 + 6.14 + 0.20) = 8.3412 -> 8.34
    assert f100k.stamp_duty_buy == Decimal("3")  # 100000 * 0.00003 = 3
    assert f100k.total_statutory_fees == Decimal("82.68")
    assert f100k.fee_burden_bps == Decimal("8.27")


def test_spread_and_touch_accounting_no_double_counting() -> None:
    # RELIANCE fixture:
    # Entry: Bid=1314.50, Ask=1314.70 (Spread=0.20, Mid=1314.60)
    # Exit: Bid=1320.00, Ask=1320.20 (Spread=0.20, Mid=1320.10)
    # Shares: 75
    att = ExecutionAttribution.from_touch_execution(
        symbol="RELIANCE",
        shares=75,
        entry_bid=Decimal("1314.50"),
        entry_ask=Decimal("1314.70"),
        exit_bid=Decimal("1320.00"),
        exit_ask=Decimal("1320.20"),
        residual_friction_bps=Decimal("5"),  # 5 bps pure residual impact/slippage
    )

    # Buy = 1314.70 * 75 = 98,602.50
    # Sell = 1320.00 * 75 = 99,000.00
    # Touch Gross = 99,000.00 - 98,602.50 = +397.50
    assert att.touch_to_touch_gross == Decimal("397.50")

    # Mid Buy = 1314.60 * 75 = 98,595.00
    # Mid Sell = 1320.10 * 75 = 99,007.50
    # Mid Gross = 99,007.50 - 98,595.00 = +412.50
    assert att.mid_to_mid_gross == Decimal("412.50")

    # Total Quoted Spread paid = (0.10 + 0.10) * 75 = 0.20 * 75 = 15.00
    assert att.observed_spread_cost == Decimal("15.00")

    # Critical Proof: Touch Gross exactly equals Mid Gross minus Observed Spread Cost
    assert att.touch_to_touch_gross == att.mid_to_mid_gross - att.observed_spread_cost

    # Statutory fees on turnover (98,602.50 + 99,000.00 = 197,602.50)
    assert att.statutory_fees == Decimal("82.60")

    # Residual friction cost: 98,602.50 * 5 / 10000 = 49.30
    assert att.residual_friction_cost == Decimal("49.30")

    # Conservative net: 397.50 - 82.60 - 49.30 = +265.60
    assert att.conservative_net_pnl == Decimal("265.60")


def test_prospective_specification_predeclared_arms() -> None:
    spec = ProspectiveSpecification()
    assert spec.min_sessions_required == 30
    assert "NO_TRADE" in spec.arms
    assert "SIMPLE_ORB" in spec.arms
    assert "MATCHED_RANDOM" in spec.arms
    assert "PRICE_VOLUME" in spec.arms
    assert Decimal("40") in spec.sensitivity_friction_bps
    assert Decimal("0") in spec.sensitivity_friction_bps
    d = spec.to_dict()
    assert d["primary_metric"] == "session_bootstrap_95pct_mean_daily_net_low_nanos"
