from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, time
from pathlib import Path

import pytest

from quant.acquisition.calendar import NseCalendar, Session, load_calendar
from quant.acquisition.contracts import CashInstrument, HistoricalBar
from quant.acquisition.quality import audit_bars, reconcile_daily
from quant.acquisition.registry import (
    CorporateAction,
    InstrumentRegistry,
    MappingRecord,
    UniverseVersion,
    load_registry,
)

OLD = CashInstrument("NSE", "NSE_EQ", "100", "OLD", "INE000A01001")
NEW = CashInstrument("NSE", "NSE_EQ", "100", "NEW", "INE000A01001")


def test_pilot_evidence_is_scoped_to_dated_nse_files() -> None:
    root = Path(__file__).resolve().parents[1] / "data/config"
    mapping = load_registry(root / "registry.json")
    calendar = load_calendar(root / "calendar.json")
    assert [record.effective_from for record in mapping.records] == [
        date(2026, 10, 15),
        date(2026, 10, 16),
    ]
    for day in range(12, 17):
        current = date(2026, 10, day)
        session = calendar.session(current)
        assert session is not None
        assert session.closes == time(15, 15)
        assert session.verified == (day >= 15)
        if day >= 15:
            assert (
                mapping.by_security_id("2885", current).instrument.symbol == "RELIANCE"
            )
        else:
            with pytest.raises(ValueError, match="historical security ID missing"):
                mapping.by_security_id("2885", current)


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


def test_two_instruments_on_one_day_are_one_session() -> None:
    day = date(2025, 1, 2)
    other = CashInstrument("NSE", "NSE_EQ", "200", "OTHER")
    mapping = InstrumentRegistry(
        registry().records
        + (MappingRecord(other, "Other", day, None, "LISTED", "master-2025"),),
        "two-instrument-fixture",
    )
    report = audit_bars(
        [daily(day), daily(day, other)],
        calendar=NseCalendar("fixture"),
        registry=mapping,
        universe=("100", "200"),
        start=day,
        end=date(2025, 1, 3),
        interval_minutes=None,
    )
    assert report.rows == 2
    assert report.independent_sessions == 1


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
    assert report.hard_failures == 1
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
    assert any(
        issue.code == "SESSION_COUNT_SHORTFALL"
        and issue.detail.startswith("observed 1 of 3")
        and issue.severity == "ERROR"
        for issue in unknown.issues
    )


def test_provisional_session_is_a_hard_research_hold() -> None:
    day = date(2025, 1, 2)
    session = Session(day, time(9, 15), time(9, 16), "CAS_CTS", False)
    row = replace(
        daily(day),
        interval_minutes=1,
        source_timestamp_ns=session.start_ns(),
        timestamp_semantics="UNVERIFIED",
    )
    report = audit_bars(
        [row],
        calendar=NseCalendar("provisional", special={day: session}),
        registry=registry(),
        universe=("100",),
        start=day,
        end=date(2025, 1, 3),
        interval_minutes=1,
    )
    assert report.hard_failures == 1
    assert any(
        issue.code == "CALENDAR_APPLICABILITY_UNVERIFIED" for issue in report.issues
    )


def test_scoped_calendar_rejects_unsupported_dates(tmp_path: Path) -> None:
    path = tmp_path / "calendar.json"
    path.write_text(
        json.dumps(
            {
                "version": "scoped",
                "scope": {"start": "2025-01-02", "end": "2025-01-03"},
                "normal_open": "09:15:00",
                "normal_close": "15:30:00",
                "holidays": [],
                "special": [
                    {
                        "day": "2025-01-02",
                        "opens": "09:15:00",
                        "closes": "15:15:00",
                        "kind": "CAS_CONTINUOUS_TRADING",
                        "verified": False,
                    }
                ],
            }
        )
    )
    calendar = load_calendar(path)
    session = calendar.session(date(2025, 1, 2))
    assert session is not None and not session.verified
    with pytest.raises(ValueError, match="outside evidenced calendar scope"):
        calendar.session(date(2025, 1, 3))


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
