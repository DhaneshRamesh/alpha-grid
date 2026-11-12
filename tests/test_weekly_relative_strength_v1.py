"""Isolated software checks; synthetic bars are never research evidence."""

from datetime import date, timedelta, time
from decimal import Decimal

import pytest

from tools.evaluate_weekly_relative_strength_v1 import delivery_fees, result, select, summary
from tools.execute_full_3yr_acquisition import (
    HistoricalValuationIneligible, contemporaneous_price, unreconstructed_price_windows,
)


def test_delivery_fee_includes_dp_once_and_both_stt_legs():
    # ₹5,000 flat delivery round trip under the existing Rust rate/rounding contract.
    assert delivery_fees(Decimal("5000"), Decimal("5000")) == Decimal("26.19")


def test_official_action_factors_restore_split_and_bonus_prices():
    mcx = {"key": "31181-1-2025-07-23-2025-10-21", "symbol": "MCX", "security_id": "31181",
           "ca_status": "VERIFIED_BACK_ADJUSTED", "ca_factor": 5.0}
    cupid = {"key": "18520-1-2025-07-23-2025-10-21", "symbol": "CUPID", "security_id": "18520",
             "ca_status": "VERIFIED_BACK_ADJUSTED", "ca_factor": 5.0}
    assert contemporaneous_price(Decimal("639.95"), "MCX", "2025-09-03", mcx) == Decimal("3199.75")
    assert contemporaneous_price(Decimal("20.50"), "CUPID", "2025-09-08", cupid) == Decimal("102.50")
    with pytest.raises(HistoricalValuationIneligible, match="SOURCE_ADJUSTMENT_FACTOR_MISMATCH"):
        contemporaneous_price(Decimal("20.50"), "CUPID", "2025-09-08", {**cupid, "ca_factor": 4.0})
    with pytest.raises(HistoricalValuationIneligible, match="SOURCE_ACTION_CLASSIFICATION_MISMATCH"):
        contemporaneous_price(Decimal("20.50"), "CUPID", "2025-09-08",
                               {**cupid, "ca_status": "NO_KNOWN_BONUS_OR_SPLIT_RECORDED", "ca_factor": 1.0})
    with pytest.raises(HistoricalValuationIneligible, match="INDEPENDENT_ACTION_FACTOR_MISSING"):
        contemporaneous_price(Decimal("20.50"), "CUPID", "2023-11-03",
                               {**cupid, "key": "18520-1-2023-11-01-2024-01-30", "ca_factor": 100.0})


def test_action_spanning_window_remains_unresolved_on_both_sides():
    row = {"key": "1333-1-2025-07-23-2025-10-21", "symbol": "HDFCBANK",
           "security_id": "1333", "ca_status": "EX_DATE_SPANNED_WITHIN_WINDOW"}
    with pytest.raises(HistoricalValuationIneligible, match="ACTION_SPANS_SOURCE_WINDOW"):
        contemporaneous_price(Decimal("950"), "HDFCBANK", "2025-09-01", row)
    with pytest.raises(HistoricalValuationIneligible, match="ACTION_SPANS_SOURCE_WINDOW"):
        contemporaneous_price(Decimal("1900"), "HDFCBANK", "2025-08-25", row)


def test_restored_price_prevents_unaffordable_whole_share_and_false_charges():
    sessions = ["2025-01-31", "2025-02-03", "2025-02-04", "2025-02-05",
                "2025-02-06", "2025-02-07"]
    row = {"key": "317-1-2025-01-24-2025-04-24", "symbol": "BAJFINANCE", "security_id": "317",
           "ca_status": "VERIFIED_BACK_ADJUSTED", "ca_factor": 10.0}
    bars = {"BAJFINANCE": {sessions[1]: {time(9, 30): 733.65},
                           sessions[5]: {time(9, 30): 740.00}}}
    sources = {"BAJFINANCE": {d: {time(9, 30): row} for d in (sessions[1], sessions[5])}}
    out = result(bars, sessions, (0, "BAJFINANCE", "BAJFINANCE", "POSITIVE_BREADTH"),
                 "BAJFINANCE", 20, 20, sources)
    assert out["status"] == "NO_TRADE_UNAFFORDABLE"
    assert out["entry_price"] == Decimal("7336.50")
    assert out["quantity"] == 0
    assert out["gross"] == out["fees"] == out["net"] == 0
    unresolved = {"BAJFINANCE": {d: {time(9, 30): {**row, "ca_factor": 9.0}}
                                    for d in (sessions[1], sessions[5])}}
    out = result(bars, sessions, (0, "BAJFINANCE", "BAJFINANCE", "POSITIVE_BREADTH"),
                 "BAJFINANCE", 20, 20, unresolved)
    assert out["status"] == "NO_TRADE_UNRESOLVED"
    assert out["reason"] == "SOURCE_ADJUSTMENT_FACTOR_MISMATCH"
    assert out["net"] == 0
    report = summary(bars, sessions, [(0, "BAJFINANCE", "BAJFINANCE", "POSITIVE_BREADTH")],
                     20, 20, unresolved)
    assert report["n"] == 1
    assert report["valuation_status"] == {"NO_TRADE_UNRESOLVED": 1}
    assert report["gross_inr"] == report["fees_inr"] == report["net_inr"] == "0"


def test_uncorrected_economic_evaluators_flag_only_relevant_adjusted_windows():
    manifest = {"files": [
        {"key": "317-1-2025-01-24-2025-04-24", "symbol": "BAJFINANCE", "bars": 100,
         "ca_status": "VERIFIED_BACK_ADJUSTED"},
        {"key": "2885-1-2026-01-19-2026-04-19", "symbol": "RELIANCE", "bars": 100,
         "ca_status": "VERIFIED_BACK_ADJUSTED"},
        {"key": "1922-1-2025-01-24-2025-04-24", "symbol": "KOTAKBANK", "bars": 100,
         "ca_status": "VERIFIED_BACK_ADJUSTED"},
    ]}
    assert unreconstructed_price_windows(manifest, "2025-01-01", "2025-10-31",
                                         {"KOTAKBANK"}) == ["317-1-2025-01-24-2025-04-24"]


def test_weekly_selection_uses_only_prior_closes_and_censors_missing_path():
    days = []
    cursor = date(2024, 1, 1)
    while len(days) < 95:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    ids = {f"STOCK{i:02}": str(1000 + i) for i in range(20)}
    bars = {symbol: {} for symbol in ids}
    for i, symbol in enumerate(ids):
        for j, day in enumerate(days):
            bars[symbol][day] = {time(14, 29): float(100 + j * (i + 1) / 20),
                                 time(9, 30): float(100 + j * (i + 1) / 20)}
    picks, _, sessions = select(bars, ids)
    assert picks and all(pick[1] == "STOCK00" for pick in picks)
    later = picks[-1]
    bars["STOCK00"][sessions[later[0] + 5]][time(9, 30)] *= 10
    assert select(bars, ids)[0] == picks
    del bars["STOCK00"][sessions[later[0] + 5]][time(9, 30)]
    assert len(select(bars, ids)[0]) == len(picks) - 1
