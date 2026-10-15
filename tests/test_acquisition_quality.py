from __future__ import annotations

from dataclasses import replace
from datetime import date, time

import pytest

from quant.acquisition.calendar import NseCalendar, Session
from quant.acquisition.contracts import CashInstrument, HistoricalBar
from quant.acquisition.quality import audit_bars, reconcile_daily
from quant.acquisition.registry import (
    CorporateAction,
    InstrumentRegistry,
    MappingRecord,
    UniverseVersion,
)

OLD = CashInstrument("NSE", "NSE_EQ", "100", "OLD", "INE000A01001")
NEW = CashInstrument("NSE", "NSE_EQ", "100", "NEW", "INE000A01001")


def registry() -> InstrumentRegistry:
    return InstrumentRegistry(
        (
            MappingRecord(
                OLD,
                "Example Ltd",
                date(2024, 1, 1),
                date(2025, 1, 2),
                "LISTED",
                "master-2024",
                (CorporateAction(date(2025, 1, 2), "SPLIT", "notice-1"),),
            ),
            MappingRecord(
                NEW, "Example Ltd", date(2025, 1, 2), None, "LISTED", "master-2025"
            ),
        ),
        "registry-v1",
    )


def daily(day: date, instrument: CashInstrument = NEW) -> HistoricalBar:
    return HistoricalBar(
        instrument,
        None,
        day,
        None,
        100_000_000_000,
        101_000_000_000,
        99_000_000_000,
        100_000_000_000,
        100,
        "NSE_UDIFF_CM",
        "a" * 64,
        "DAILY",
    )


def test_effective_date_identity_and_ambiguity() -> None:
    mapping = registry()
    assert mapping.resolve("OLD", OLD.isin, date(2025, 1, 1)) == OLD
    assert mapping.resolve("NEW", NEW.isin, date(2025, 1, 2)) == NEW
    with pytest.raises(ValueError, match="missing or ambiguous"):
        mapping.resolve("NEW", NEW.isin, date(2025, 1, 1))
    ambiguous = InstrumentRegistry(
        mapping.records
        + (MappingRecord(NEW, "Other", date(2025, 1, 2), None, "LISTED", "duplicate"),),
        "bad",
    )
    with pytest.raises(ValueError, match="ambiguous"):
        ambiguous.resolve("NEW", NEW.isin, date(2025, 1, 3))
    universe = UniverseVersion(
        "u1", date(2024, 12, 31), ("100",), "predeclared liquid universe"
    )
    assert universe.security_ids == ("100",)


def test_calendar_holiday_special_and_gap_classification() -> None:
    special = Session(date(2025, 1, 4), time(18), time(18, 2), "SPECIAL")
    calendar = NseCalendar(
        "test-calendar", frozenset({date(2025, 1, 3)}), {special.day: special}
    )
    assert [s.day for s in calendar.sessions(date(2025, 1, 2), date(2025, 1, 6))] == [
        date(2025, 1, 2),
        date(2025, 1, 4),
    ]
    bar = daily(date(2025, 1, 2))
    report = audit_bars(
        [bar],
        calendar=calendar,
        registry=registry(),
        universe=("100",),
        start=date(2025, 1, 2),
        end=date(2025, 1, 6),
        interval_minutes=None,
    )
    assert report.independent_sessions == 1
    assert any(
        issue.code == "MISSING_SESSION" and issue.detail == "UNKNOWN"
        for issue in report.issues
    )
    confirmed = audit_bars(
        [bar],
        calendar=calendar,
        registry=registry(),
        universe=("100",),
        start=date(2025, 1, 2),
        end=date(2025, 1, 6),
        interval_minutes=None,
        missing_evidence={("100", date(2025, 1, 4)): "PROVIDER_OUTAGE"},
    )
    assert any(issue.detail == "PROVIDER_OUTAGE" for issue in confirmed.issues)


def test_intraday_missing_bars_only_with_verified_timestamp() -> None:
    day = date(2025, 1, 2)
    special = Session(day, time(9, 15), time(9, 18), "TEST")
    calendar = NseCalendar("test", special={day: special})
    row = replace(
        daily(day),
        interval_minutes=1,
        source_timestamp_ns=special.start_ns(),
        timestamp_semantics="BAR_START_VERIFIED",
    )
    report = audit_bars(
        [row],
        calendar=calendar,
        registry=registry(),
        universe=("100",),
        start=day,
        end=date(2025, 1, 3),
        interval_minutes=1,
    )
    assert report.missing_bars == 2
    unknown = audit_bars(
        [replace(row, timestamp_semantics="UNVERIFIED")],
        calendar=calendar,
        registry=registry(),
        universe=("100",),
        start=day,
        end=date(2025, 1, 3),
        interval_minutes=1,
    )
    assert unknown.missing_bars == 0
    assert any(issue.code == "TIMESTAMP_SEMANTICS_UNKNOWN" for issue in unknown.issues)


def test_invalid_ohlc_duplicates_regressions_overlap_and_gap() -> None:
    day = date(2025, 1, 2)
    with pytest.raises(ValueError, match="high"):
        replace(daily(day), high_nanos=99_000_000_000)
    with pytest.raises(ValueError, match="negative"):
        replace(daily(day), volume=-1)
    calendar = NseCalendar("test")
    first = daily(day)
    second = daily(day)
    report = audit_bars(
        [first, second],
        calendar=calendar,
        registry=registry(),
        universe=("100",),
        start=day,
        end=date(2025, 1, 3),
        interval_minutes=None,
    )
    codes = {issue.code for issue in report.issues}
    assert "DUPLICATE_TIMESTAMP" in codes and "TIMESTAMP_REGRESSION" in codes
    assert reconcile_daily(first, replace(second, close_nanos=100_500_000_000)) == (
        "close_nanos",
    )
