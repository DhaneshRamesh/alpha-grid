"""Synthetic software fixtures only; never used as market research evidence."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from quant.acquisition.contracts import CashInstrument, HistoricalBar
from quant.apps.historical_simulator import published_intraday_fees, simulate


def _bars(day: int, *, breakout: bool) -> tuple[HistoricalBar, ...]:
    instrument = CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE")
    start = datetime(2026, 10, day, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
    result = []
    for index in range(105):
        close = 102 if breakout and index == 44 else 100
        if breakout and index == 104:
            close = 103
        stamp = start + timedelta(minutes=index)
        result.append(
            HistoricalBar(
                instrument,
                1,
                date(2026, 10, day),
                int(stamp.timestamp()) * 1_000_000_000,
                100 * 1_000_000_000,
                max(101, close) * 1_000_000_000,
                99 * 1_000_000_000,
                close * 1_000_000_000,
                1000,
                "DHAN_HISTORICAL",
                "fixture-only",
            )
        )
    return tuple(result)


def _universe() -> dict:
    return {
        "schema_version": "alpha-grid-cross-stock-universe-v1",
        "selection_asof": "2026-10-30",
        "instruments": [
            {"symbol": "RELIANCE", "security_id": "2885"},
            *(
                {"symbol": f"FIXTURE{index}", "security_id": str(10000 + index)}
                for index in range(24)
            ),
        ],
    }


def test_reference_simulator_separates_rule_trade_and_forced_diagnostic() -> None:
    report = simulate(
        {"RELIANCE": _bars(26, breakout=True) + _bars(27, breakout=False)}, _universe()
    )
    assert report["mode"] == "SIMULATION_ONLY"
    assert report["candidate"]["selected_sessions"] == 1
    assert report["momentum"]["selected_sessions"] == 1
    assert report["no_trade"]["selected_sessions"] == 0
    assert report["decision_counts"]["NO_GENUINE_ARCHIVE"] == 48
    assert len(report["forced_diagnostics_excluded"]) == 1
    trade = report["trade_ledger"][0]
    gross = Decimal(trade["gross_reference_inr"])
    fees = Decimal(trade["fee_components_inr"]["total"])
    friction = Decimal(trade["assumed_friction_inr"])
    net = Decimal(trade["net_reference_inr"])
    assert gross - fees - friction == net
    assert Decimal(trade["virtual_cash_after_reference_entry_inr"]) == (
        Decimal("1000000") - Decimal(trade["entry_notional_inr"])
    )
    assert trade["virtual_position_shares_at_reference_entry"] == trade["shares"]
    assert trade["virtual_position_shares_after_reference_exit"] == 0
    assert (
        trade["virtual_cash_after_reference_exit_inr"]
        == report["candidate"]["ending_virtual_capital_inr"]
    )
    assert (
        Decimal(report["candidate"]["ending_virtual_capital_inr"])
        == Decimal("1000000") + net
    )
    assert report["candidate"]["session_bootstrap_95pct_mean_net_inr"] is None
    assert report["virtual_positions_end"] == []
    assert (report["paper_orders"], report["qp01_writes"], report["broker_orders"]) == (
        0,
        0,
        0,
    )


def test_fee_components_use_distinct_buy_and_sell_reference_values() -> None:
    fees = published_intraday_fees(Decimal("99856.40"), Decimal("99719.60"))
    assert fees["brokerage"] == "40.00"
    assert fees["stt"] == "25"
    assert fees["stamp"] == "3"
    assert Decimal(fees["total"]) == sum(
        (Decimal(value) for key, value in fees.items() if key != "total"), Decimal(0)
    )


def test_source_order_violation_fails_closed() -> None:
    bars = _bars(26, breakout=True)
    with pytest.raises(ValueError, match="source identity, order"):
        simulate({"RELIANCE": (bars[1], bars[0])}, _universe())


def test_one_ranked_opportunity_across_two_instruments() -> None:
    universe = _universe()
    universe["instruments"][1] = {"symbol": "HDFCBANK", "security_id": "1333"}
    hdfc = CashInstrument("NSE", "NSE_EQ", "1333", "HDFCBANK")
    hdfc_bars = tuple(
        replace(
            bar,
            instrument=hdfc,
            close_nanos=(104 if index == 44 else 103 if index == 104 else 100)
            * 1_000_000_000,
            high_nanos=(104 if index == 44 else 103 if index == 104 else 101)
            * 1_000_000_000,
        )
        for index, bar in enumerate(_bars(26, breakout=False))
    )
    report = simulate(
        {"RELIANCE": _bars(26, breakout=True), "HDFCBANK": hdfc_bars}, universe
    )
    assert report["candidate"]["selected_sessions"] == 1
    assert len(report["trade_ledger"]) == 1
    assert report["trade_ledger"][0]["symbol"] == "HDFCBANK"
    assert report["trade_ledger"][0]["actual_fill"] is False
    assert Decimal(report["trade_ledger"][0]["entry_notional_inr"]) <= Decimal("100000")
    assert report["decision_counts"]["REJECTED_LOWER_BREAKOUT_RANK"] == 1
    assert report["decision_counts"]["NO_GENUINE_ARCHIVE"] == 23
    assert report["virtual_positions_end"] == []


def test_source_hash_can_change_only_between_sessions() -> None:
    first = _bars(26, breakout=True)
    second = tuple(
        replace(bar, source_sha256="second-fixture")
        for bar in _bars(27, breakout=False)
    )
    report = simulate({"RELIANCE": first + second}, _universe())
    assert report["source_hashes"]["RELIANCE"] == ["fixture-only", "second-fixture"]
    mixed_day = first[:1] + tuple(
        replace(bar, source_sha256="second-fixture") for bar in first[1:]
    )
    with pytest.raises(ValueError, match="provenance invalid"):
        simulate({"RELIANCE": mixed_day}, _universe())
