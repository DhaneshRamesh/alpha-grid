"""Synthetic software fixtures only; never market performance evidence."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from quant.acquisition.calendar import IST
from quant.acquisition.contracts import CashInstrument, HistoricalBar
from quant.apps.cross_equity_adaptive import _save_once, replay


def test_all_stock_asof_coverage_and_later_reference_exit() -> None:
    instrument = CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE")
    start = datetime(2026, 10, 26, 9, 15, tzinfo=IST)
    bars = []
    for index in range(360):
        stamp = start + timedelta(minutes=index)
        close = 101 if 39 <= index < 56 else 99 if 56 <= index < 75 else 103 if index >= 75 else 100
        bars.append(HistoricalBar(
            instrument, 1, date(2026, 10, 26), int(stamp.timestamp()) * 1_000_000_000,
            close * 1_000_000_000, close * 1_000_000_000,
            close * 1_000_000_000, close * 1_000_000_000,
            2000 if index >= 39 else 1000, "DHAN_HISTORICAL", "fixture-only",
        ))
    universe = {"instruments": [
        {"symbol": "RELIANCE", "security_id": "2885"},
        *({"symbol": f"FIXTURE{i}", "security_id": str(10000 + i)}
          for i in range(24)),
    ]}
    result, decisions = replay(universe, {"RELIANCE": tuple(bars)},
                               delay_seconds=60, require_volume=False)
    assert len([d for d in decisions if d["time_ist"] == "10:00:00"
                and d["kind"] in {"CANDIDATE", "REJECT"}]) == 25
    trade = result["trade_ledger"][0]
    assert trade["entry_asof_label_ist"] == "09:58:00"
    assert trade["entry_reference_label_ist"] == "10:01:00"
    assert trade["exit_asof_label_ist"] == "10:13:00"
    assert trade["exit_reference_label_ist"] == "10:16:00"
    assert trade["exit_reason"] == "BREAKOUT_INVALIDATED"
    assert trade["actual_fill"] is False
    assert Decimal(trade["net_reference_inr"]) == (
        Decimal(trade["gross_reference_inr"])
        - Decimal(trade["fee_components_inr"]["total"])
        - Decimal(trade["assumed_friction_inr"])
    )
    assert result["qp01_writes"] == result["broker_orders"] == 0
    assert len(result["trade_ledger"]) == 1
    assert any(d["kind"] == "NO_TRADE" and d["reason"] == "ENTRY_CUTOFF_OR_RISK_LIMIT"
               for d in decisions if d["time_ist"] == "10:40:00")


def test_delay_sensitivity_changes_eligible_asof_without_same_bar_fill() -> None:
    instrument = CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE")
    start = datetime(2026, 10, 26, 9, 15, tzinfo=IST)
    bars = tuple(HistoricalBar(
        instrument, 1, date(2026, 10, 26),
        int((start + timedelta(minutes=index)).timestamp()) * 1_000_000_000,
        100_000_000_000, 100_000_000_000, 100_000_000_000,
        100_000_000_000, 1000, "DHAN_HISTORICAL", "fixture-only",
    ) for index in range(360))
    universe = {"instruments": [
        {"symbol": "RELIANCE", "security_id": "2885"},
        *({"symbol": f"FIXTURE{i}", "security_id": str(10000 + i)} for i in range(24)),
    ]}
    _, one_minute = replay(universe, {"RELIANCE": bars}, delay_seconds=60, require_volume=False)
    _, five_minutes = replay(universe, {"RELIANCE": bars}, delay_seconds=300, require_volume=False)
    first = next(d for d in one_minute if d.get("symbol") == "RELIANCE" and d["time_ist"] == "10:00:00")
    second = next(d for d in five_minutes if d.get("symbol") == "RELIANCE" and d["time_ist"] == "10:00:00")
    assert first["features"]["last_eligible_label_ist"] == "09:58:00"
    assert second["features"]["last_eligible_label_ist"] == "09:54:00"


def test_artifact_writer_refuses_replacement(tmp_path) -> None:
    path = tmp_path / "research.json"
    first = _save_once(path, {"result": "fixture"})
    assert len(first) == 64
    with pytest.raises(FileExistsError):
        _save_once(path, {"result": "changed"})
    assert path.read_text().strip() == '{"result":"fixture"}'
