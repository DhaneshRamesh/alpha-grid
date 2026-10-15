"""Read-only coverage of one-day local NSE datasets."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from quant.acquisition.calendar import NseCalendar
from quant.acquisition.lake import verify_dataset
from quant.acquisition.nse import file_sha256
from quant.acquisition.registry import InstrumentRegistry, UniverseVersion


def session_coverage(
    *,
    lake_root: Path,
    raw_root: Path,
    calendar: NseCalendar,
    registry: InstrumentRegistry,
    universe: UniverseVersion,
    start: date,
    end: date,
) -> dict[str, Any]:
    """IMPORTED means checksummed local import, not independently genuine data."""
    if start >= end:
        raise ValueError("coverage start must precede end")
    imported: dict[date, str] = {}
    unverified: dict[date, str] = {}
    if lake_root.exists():
        for path in sorted(lake_root.iterdir()):
            if not path.is_dir() or not (path / "manifest.json").is_file():
                continue
            manifest = verify_dataset(path)
            if manifest.first_date != manifest.last_date:
                raise ValueError("coverage requires one session per dataset")
            day = date.fromisoformat(manifest.first_date)
            if not start <= day < end:
                continue
            if day in imported or day in unverified:
                raise ValueError(f"duplicate daily import: {day}")
            if {item.sha256 for item in manifest.source_provenance} != set(
                manifest.source_checksums
            ):
                unverified[day] = "source_provenance_missing"
                continue
            originals_present = all(
                (raw_root / f"NSE_{source.format_version}" / source.sha256).is_file()
                and file_sha256(
                    raw_root / f"NSE_{source.format_version}" / source.sha256
                )
                == source.sha256
                for source in manifest.source_provenance
            )
            if originals_present:
                imported[day] = manifest.dataset_sha256
            else:
                unverified[day] = "original_source_missing_or_mismatched"
    result: dict[str, list[str]] = {
        "imported": [],
        "missing": [],
        "ineligible": [],
        "unverified": [],
    }
    for session in calendar.sessions(start, end):
        day = session.day
        label = day.isoformat()
        if universe.selection_asof > day:
            result["ineligible"].append(label)
            continue
        try:
            for sid in universe.security_ids:
                registry.by_security_id(sid, day)
        except ValueError:
            result["ineligible"].append(label)
        else:
            if day in imported:
                result["imported"].append(label)
            elif day in unverified:
                result["unverified"].append(label)
            else:
                result["missing"].append(label)
    return {
        "schema_version": "nse-session-coverage-v1",
        "calendar_completeness": "UNVERIFIED",
        "source_authenticity": "UNVERIFIED",
        "counts": {name: len(days) for name, days in result.items()},
        "sessions": result,
        "imported_dataset_sha256": {
            day.isoformat(): digest for day, digest in imported.items()
        },
    }
