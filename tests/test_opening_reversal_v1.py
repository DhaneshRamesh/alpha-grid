"""Offline contract checks for the frozen exploratory opening-reversal evaluator."""

from datetime import time
from decimal import Decimal

from tools.evaluate_opening_reversal_v1 import complete_pairs, outcome, select_pairs, summarize


def test_selection_ignores_future_bars_and_censors_missing_reference_path():
    day = "2025-09-01"
    bars = {sym: {day: {}} for sym in (
        "HDFCBANK", "RELIANCE", "INFY", "ICICIBANK", "BHARTIARTL"
    )}
    for sym, close in (("HDFCBANK", 94), ("RELIANCE", 98), ("INFY", 100),
                       ("ICICIBANK", 101), ("BHARTIARTL", 100.5)):
        for m in range(9 * 60 + 15, 10 * 60):
            bars[sym][day][time(m // 60, m % 60)] = {
                "o": 100, "c": close if m == 9 * 60 + 59 else 100,
            }
    before, _ = select_pairs(bars)
    assert before == [(day, "HDFCBANK", "INFY")]
    bars["HDFCBANK"][day][time(10, 6)] = {"o": 500, "c": 500}
    bars["INFY"][day][time(12, 6)] = {"o": 1, "c": 1}
    assert select_pairs(bars)[0] == before
    assert complete_pairs(bars, before) == ([], 1)


def test_reference_net_deducts_published_fees_and_friction_once():
    day = "2025-05-02"
    bars = {"HDFCBANK": {day: {
        time(10, 6): {"o": 90}, time(12, 6): {"o": 91},
    }}}
    sources = {"HDFCBANK": {day: {
        "key": "1333-1-2025-04-24-2025-07-23", "symbol": "HDFCBANK", "security_id": "1333",
        "ca_status": "VERIFIED_BACK_ADJUSTED", "ca_factor": 2.0,
    }}}
    result = outcome(bars, (day, "HDFCBANK", "INFY"), "HDFCBANK", 10, 10, False, sources)
    assert result["status"] == "ELIGIBLE" and result["shares"] == 27
    assert result["buy"] == Decimal("4860")  # 27 whole shares at restored ₹180
    assert result["gross"] == Decimal("54")
    assert result["fees"] == Decimal("4.82")
    assert result["spread"] == result["residual"] == Decimal("4.86")
    assert result["net"] == result["gross"] - result["fees"] - Decimal("9.72")
    bad = {"HDFCBANK": {day: {**sources["HDFCBANK"][day], "ca_factor": 3.0}}}
    rejected = outcome(bars, (day, "HDFCBANK", "INFY"), "HDFCBANK", 10, 10, False, bad)
    assert rejected["status"] == "NO_TRADE_UNRESOLVED"
    assert rejected["shares"] == rejected["fees"] == rejected["net"] == 0
    report = summarize(bars, [(day, "HDFCBANK", "INFY")], 10, 10, False, bad)
    assert report["n"] == 1
    assert report["valuation_status"] == {"NO_TRADE_UNRESOLVED": 1}
    assert report["gross_inr"] == report["fees_inr"] == report["net_inr"] == "0"
