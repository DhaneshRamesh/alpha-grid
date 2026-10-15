from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from quant.acquisition.calendar import NseCalendar
from quant.acquisition.contracts import CashInstrument, HistoricalBar
from quant.acquisition.coverage import session_coverage
from quant.acquisition.lake import RawProvenance, stage_raw_file, write_dataset
from quant.acquisition.nse import file_sha256
from quant.acquisition.registry import (
    InstrumentRegistry,
    MappingRecord,
    UniverseVersion,
)


def test_incremental_coverage_and_duplicate_dates(tmp_path: Path) -> None:
    root = tmp_path / "lake"
    root.mkdir()
    raw_root = tmp_path / "raw"
    original = tmp_path / "archive.csv"
    original.write_text("synthetic fixture only")
    digest = file_sha256(original)
    stage_raw_file(original, raw_root, "NSE_UDIFF_CM")
    instrument = CashInstrument("NSE", "NSE_EQ", "100", "TEST")
    first = date(2025, 1, 2)
    registry = InstrumentRegistry(
        (MappingRecord(instrument, "Test", first, None, "LISTED", "fixture"),),
        "fixture",
    )
    universe = UniverseVersion("fixture", first, ("100",), "fixture")
    bar = HistoricalBar(
        instrument,
        None,
        first,
        None,
        100_000_000_000,
        101_000_000_000,
        99_000_000_000,
        100_000_000_000,
        5,
        "NSE_UDIFF_CM",
        digest,
        "DAILY",
    )
    provenance = (
        RawProvenance(
            digest,
            "https://example.test/archive",
            "2025-01-03T00:00:00+05:30",
            "UDIFF_CM",
        ),
    )

    def publish(name: str, source_provenance: tuple[RawProvenance, ...]) -> None:
        write_dataset(
            [bar],
            root / name,
            dataset_id=name,
            acquisition_mode="LOCAL_FILE",
            instrument_universe=("100",),
            calendar_version="fixture",
            adjustment_policy="UNADJUSTED",
            code_commit="fixture",
            source_provenance=source_provenance,
        )

    publish("first", provenance)
    calendar = NseCalendar("fixture")
    report = session_coverage(
        lake_root=root,
        raw_root=raw_root,
        calendar=calendar,
        registry=registry,
        universe=universe,
        start=first,
        end=date(2025, 1, 7),
    )
    assert report["sessions"]["imported"] == ["2025-01-02"]
    assert report["sessions"]["missing"] == ["2025-01-03", "2025-01-06"]
    assert report["source_authenticity"] == "UNVERIFIED"
    (raw_root / "NSE_UDIFF_CM" / digest).unlink()
    report = session_coverage(
        lake_root=root,
        raw_root=raw_root,
        calendar=calendar,
        registry=registry,
        universe=universe,
        start=first,
        end=date(2025, 1, 7),
    )
    assert report["sessions"]["unverified"] == ["2025-01-02"]
    publish("duplicate", provenance)
    with pytest.raises(ValueError, match="duplicate daily import"):
        session_coverage(
            lake_root=root,
            raw_root=raw_root,
            calendar=calendar,
            registry=registry,
            universe=universe,
            start=first,
            end=date(2025, 1, 7),
        )


def test_missing_registry_makes_session_ineligible(tmp_path: Path) -> None:
    day = date(2025, 1, 2)
    report = session_coverage(
        lake_root=tmp_path / "empty",
        raw_root=tmp_path / "raw",
        calendar=NseCalendar("fixture"),
        registry=InstrumentRegistry((), "fixture"),
        universe=UniverseVersion("fixture", day, ("100",), "fixture"),
        start=day,
        end=date(2025, 1, 3),
    )
    assert report["sessions"]["ineligible"] == ["2025-01-02"]
