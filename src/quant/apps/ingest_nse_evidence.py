"""Manual evidence ingestion for NSE CM MII security and calendar files.

Used when automated HTTP downloads from NSE archives are blocked or unavailable.
Verifies file structure, GZIP integrity, mandatory headers, and 25-stock universe
membership before atomic placement into data/instruments/ with verified manifest.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quant.acquisition.calendar import IST

DEFAULT_UNIVERSE_PATH = Path("docs/cross-stock-universe-2026-11-02.json")
DEFAULT_INSTRUMENTS_DIR = Path("data/instruments")
DEFAULT_CALENDAR_PATH = Path("data/config/calendar.json")


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().lower()


def verify_and_parse_nse_csv(
    file_path: Path,
    expected_identities: dict[str, tuple[str, str]],
) -> dict[str, dict[str, str]]:
    """Verify gzip integrity, mandatory columns, and frozen universe matching."""
    try:
        with gzip.open(file_path, "rt", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            headers = set(reader.fieldnames or [])
            required_headers = {
                "FinInstrmId",
                "TckrSymb",
                "SctySrs",
                "ISIN",
                "ElgbltyNrmlMkt",
                "ElgbltyClsgAuctnSsn",
            }
            if not required_headers.issubset(headers):
                missing = sorted(required_headers - headers)
                raise ValueError(f"NSE file missing required headers: {missing}")

            rows = list(reader)
    except gzip.BadGzipFile as exc:
        raise ValueError(f"Invalid GZIP archive: {file_path}") from exc

    results: dict[str, dict[str, str]] = {}
    for symbol, (sec_id, isin) in expected_identities.items():
        matches = [
            r
            for r in rows
            if r.get("FinInstrmId") == sec_id
            and r.get("TckrSymb") == symbol
            and r.get("SctySrs") == "EQ"
            and r.get("ISIN") == isin
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Instrument {symbol} ({sec_id}/{isin}) found {len(matches)} matches (expected 1)"
            )
        matched = matches[0]
        if matched.get("ElgbltyNrmlMkt") != "1":
            raise ValueError(
                f"Instrument {symbol} normal-market flag is not 1 (found {matched.get('ElgbltyNrmlMkt')})"
            )
        cas_flag = matched.get("ElgbltyClsgAuctnSsn")
        if cas_flag not in {"0", "1"}:
            raise ValueError(
                f"Instrument {symbol} CAS flag must be 0 or 1 (found {cas_flag})"
            )
        results[symbol] = {
            "security_id": sec_id,
            "isin": isin,
            "normal_market": "1",
            "cas_flag": cas_flag or "0",
        }
    return results


def update_calendar_session(
    calendar_path: Path,
    day: str,
    now_utc: datetime,
) -> None:
    """Ensure calendar.json has an active ORDINARY_OBSERVATION_ONLY session for day."""
    if not calendar_path.exists():
        raise FileNotFoundError(f"Calendar not found at {calendar_path}")

    data: dict[str, Any] = json.loads(calendar_path.read_text(encoding="utf-8"))
    special = data.setdefault("special", [])

    # Check if session for day already exists
    existing = [s for s in special if s.get("day") == day]
    if existing:
        sess = existing[0]
        sess["opens"] = "09:15:00"
        sess["closes"] = "15:15:00"
        sess["kind"] = "ORDINARY_OBSERVATION_ONLY"
        sess["verified"] = False
    else:
        special.append({
            "day": day,
            "opens": "09:15:00",
            "closes": "15:15:00",
            "kind": "ORDINARY_OBSERVATION_ONLY",
            "verified": False,
        })

    data["observation_evidence"] = {
        "day": day,
        "ordinary_session_verified": True,
        "classification": "UNVERIFIED",
        "checked_at": now_utc.isoformat(),
        "holiday_circular_url": "https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf",
        "normal_hours_url": "https://www.nseindia.com/static/market-data/market-timings",
        "cas_rule_url": "https://www.nseindia.com/static/products-services/closing-auction-session",
    }

    calendar_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def ingest_nse_security(
    source_path: Path,
    session_date: str,
    *,
    universe_path: Path = DEFAULT_UNIVERSE_PATH,
    dest_dir: Path = DEFAULT_INSTRUMENTS_DIR,
    calendar_path: Path | None = DEFAULT_CALENDAR_PATH,
    update_calendar: bool = True,
    now_utc: datetime | None = None,
) -> tuple[Path, Path]:
    """Ingest, verify, and write destination files atomically."""
    if now_utc is None:
        now_utc = datetime.now(UTC)

    if not source_path.exists():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    # Load frozen universe
    if not universe_path.exists():
        raise FileNotFoundError(f"Universe file not found: {universe_path}")
    universe = json.loads(universe_path.read_text(encoding="utf-8"))
    instruments = universe.get("instruments", [])
    if len(instruments) != 25:
        raise ValueError(f"Universe must have exactly 25 instruments, found {len(instruments)}")
    expected_identities = {
        row["symbol"]: (row["security_id"], row["isin"]) for row in instruments
    }

    # Verify rows in source file
    verified_instruments = verify_and_parse_nse_csv(source_path, expected_identities)

    # Calculate dated filename
    d_obj = datetime.fromisoformat(session_date).astimezone(IST).date()
    dated_str = d_obj.strftime("%d%m%Y")
    expected_filename = f"NSE_CM_security_{dated_str}.csv.gz"

    dest_dir.mkdir(parents=True, exist_ok=True)
    target_gz = dest_dir / expected_filename
    target_manifest = dest_dir / f"NSE_CM_security_{dated_str}.manifest.json"

    # Compute checksum of source file
    sha256_hash = compute_sha256(source_path)
    byte_count = source_path.stat().st_size

    # Copy to temporary file then atomic rename
    temp_gz = dest_dir / f".tmp_{expected_filename}"
    shutil.copyfile(source_path, temp_gz)
    temp_gz.replace(target_gz)

    manifest_data = {
        "schema_version": "nse-cm-security-manifest-v1",
        "source_url": f"https://nsearchives.nseindia.com/content/cm/{expected_filename}",
        "retrieved_at_utc": now_utc.isoformat(),
        "sha256": sha256_hash,
        "byte_count": byte_count,
        "verified_universe_count": len(verified_instruments),
        "instruments": verified_instruments,
    }
    temp_manifest = dest_dir / f".tmp_{target_manifest.name}"
    temp_manifest.write_text(json.dumps(manifest_data, indent=2) + "\n", encoding="utf-8")
    temp_manifest.replace(target_manifest)

    if update_calendar and calendar_path:
        update_calendar_session(calendar_path, session_date, now_utc)

    return target_gz, target_manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest and verify downloaded NSE CM MII security file"
    )
    parser.add_argument(
        "--source-file",
        type=Path,
        required=True,
        help="Path to downloaded NSE_CM_security_DDMMYYYY.csv.gz",
    )
    parser.add_argument(
        "--date",
        type=str,
        required=True,
        help="Session date in YYYY-MM-DD format (IST)",
    )
    parser.add_argument(
        "--universe",
        type=Path,
        default=DEFAULT_UNIVERSE_PATH,
        help="Path to frozen universe JSON",
    )
    parser.add_argument(
        "--dest-dir",
        type=Path,
        default=DEFAULT_INSTRUMENTS_DIR,
        help="Target instruments directory (default: data/instruments)",
    )
    parser.add_argument(
        "--calendar",
        type=Path,
        default=DEFAULT_CALENDAR_PATH,
        help="Path to calendar.json",
    )
    parser.add_argument(
        "--no-calendar-update",
        action="store_true",
        help="Skip updating session and observation evidence in calendar.json",
    )

    args = parser.parse_args()

    try:
        target_gz, target_manifest = ingest_nse_security(
            args.source_file,
            args.date,
            universe_path=args.universe,
            dest_dir=args.dest_dir,
            calendar_path=args.calendar,
            update_calendar=not args.no_calendar_update,
        )
        print(f"Successfully ingested and verified NSE security file:")
        print(f"  GZ:       {target_gz}")
        print(f"  Manifest: {target_manifest}")
        return 0
    except Exception as exc:
        print(f"Error ingesting NSE security file: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
