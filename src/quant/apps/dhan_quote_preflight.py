"""Offline, fail-closed evidence check for one read-only Dhan quote session.

No broker credentials or network operations enter this process.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import shutil
import sys
from datetime import UTC, datetime, time
from pathlib import Path

from quant.acquisition.calendar import IST, load_calendar
from quant.acquisition.dhan import (
    _private_directory,
    _verify_windows_private_acl,
    _write_private_bytes,
)
from quant.acquisition.dhan_instruments import verify_master_identities

MAX_JOURNAL_BYTES = 512 * 1024 * 1024
MAX_SEGMENTED_JOURNAL_BYTES = 4 * 1024 * 1024 * 1024
MIN_FREE_BYTES = 1024 * 1024 * 1024
PILOT_IDS = {
    "HDFCBANK": ("1333", "INE040A01034"),
    "RELIANCE": ("2885", "INE002A01018"),
    "INFY": ("1594", "INE009A01021"),
}


def verify_dated_nse_security(
    path: Path,
    manifest: Path,
    day: str,
    now: datetime,
    identities: dict[str, tuple[str, str]] = PILOT_IDS,
) -> dict[str, str]:
    if (
        path.name
        != f"NSE_CM_security_{datetime.fromisoformat(day).strftime('%d%m%Y')}.csv.gz"
    ):
        raise ValueError("dated NSE security filename mismatch")
    metadata = json.loads(manifest.read_text())
    retrieved = datetime.fromisoformat(metadata["retrieved_at_utc"])
    if (
        retrieved.tzinfo is None
        or retrieved > now
        or retrieved.astimezone(IST).date().isoformat() != day
    ):
        raise ValueError("NSE security retrieval not dated to session")
    if (
        metadata.get("source_url")
        != f"https://nsearchives.nseindia.com/content/cm/{path.name}"
    ):
        raise ValueError("NSE security source mismatch")
    if hashlib.sha256(path.read_bytes()).hexdigest() != metadata.get("sha256"):
        raise ValueError("NSE security bytes mismatch")
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    result = {}
    for symbol, (security_id, isin) in identities.items():
        matches = [
            row
            for row in rows
            if row.get("FinInstrmId") == security_id
            and row.get("TckrSymb") == symbol
            and row.get("SctySrs") == "EQ"
            and row.get("ISIN") == isin
        ]
        if (
            len(matches) != 1
            or matches[0].get("ElgbltyNrmlMkt") != "1"
            or matches[0].get("ElgbltyClsgAuctnSsn") not in {"0", "1"}
        ):
            raise ValueError("dated NSE identity or CAS unavailable")
        result[symbol] = matches[0]["ElgbltyClsgAuctnSsn"]
    return result


def check(
    *,
    journal: Path,
    master: Path,
    manifest: Path,
    calendar_path: Path,
    now_ns: int,
    pilot_three: bool = False,
    nse_security: Path | None = None,
    nse_manifest: Path | None = None,
    universe_path: Path | None = None,
) -> dict[str, int | str]:
    if now_ns <= 0:
        raise ValueError("clock unavailable")
    now = datetime.fromtimestamp(now_ns / 1_000_000_000, UTC)
    if universe_path is not None:
        universe = json.loads(universe_path.read_text())
        instruments = universe.get("instruments", [])
        if (
            universe.get("schema_version") != "alpha-grid-cross-stock-universe-v1"
            or len(instruments) != 25
        ):
            raise ValueError("fixed 25-name universe required")
        identities = {
            row["symbol"]: (row["security_id"], row["isin"]) for row in instruments
        }
        if len(identities) != 25:
            raise ValueError("duplicate universe symbol")
    else:
        identities = PILOT_IDS if pilot_three else {"RELIANCE": PILOT_IDS["RELIANCE"]}
    for identity in verify_master_identities(
        master, manifest,
        [(symbol, security_id) for symbol, (security_id, _) in identities.items()],
        now=now,
    ):
        if identity.instrument.segment != "NSE_EQ":
            raise ValueError("identity scope mismatch")
    calendar = load_calendar(calendar_path)
    day = now.astimezone(IST).date()
    if pilot_three or universe_path is not None:
        if nse_security is None or nse_manifest is None:
            raise ValueError("dated NSE source required for three-ID pilot")
        verify_dated_nse_security(
            nse_security, nse_manifest, day.isoformat(), now, identities
        )
    session = calendar.session(day)
    if (
        session is None
        or calendar.special.get(day) != session
        or session.verified
        or session.kind != "ORDINARY_OBSERVATION_ONLY"
        or session.opens != time(9, 15)
        or session.closes != time(15, 15)
    ):
        raise ValueError("dated ordinary observation session required")
    evidence = json.loads(calendar_path.read_text()).get("observation_evidence", {})
    if (
        evidence.get("day") != day.isoformat()
        or evidence.get("ordinary_session_verified") is not True
        or evidence.get("classification") != "UNVERIFIED"
        or evidence.get("holiday_circular_url")
        != "https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf"
        or evidence.get("normal_hours_url")
        != "https://www.nseindia.com/static/market-data/market-timings"
        or evidence.get("cas_rule_url")
        != "https://www.nseindia.com/static/products-services/closing-auction-session"
    ):
        raise ValueError("ordinary session provenance unavailable")
    checked_at = datetime.fromisoformat(evidence["checked_at"])
    if (
        checked_at.tzinfo is None
        or checked_at.astimezone(IST).date() != day
        or checked_at > now
    ):
        raise ValueError("ordinary session evidence not current")
    start_ns, end_ns = session.start_ns(), session.end_ns()
    if not start_ns <= now_ns < end_ns:
        raise ValueError("outside verified continuous session")
    _private_directory(journal.parent)
    if journal.is_symlink():
        raise PermissionError("journal path cannot be a symlink")
    if journal.is_dir():
        _private_directory(journal)
        total_size = sum(f.stat().st_size for f in journal.glob("*.qj") if f.is_file())
        if total_size > MAX_SEGMENTED_JOURNAL_BYTES:
            raise ValueError("segmented journal byte cap reached")
    elif journal.is_file():
        if sys.platform == "win32":
            _verify_windows_private_acl(journal)
        elif journal.stat().st_mode & 0o077:
            raise PermissionError("journal file is not private")
        if journal.stat().st_size > MAX_JOURNAL_BYTES:
            raise ValueError("journal byte cap reached")
    else:
        if journal.suffix == ".qj":
            _write_private_bytes(journal, b"")
            if sys.platform == "win32":
                _verify_windows_private_acl(journal)
            elif journal.stat().st_mode & 0o077:
                raise PermissionError("journal file is not private")
        else:
            journal.mkdir(mode=0o700, exist_ok=True)
            _private_directory(journal)
    if shutil.disk_usage(journal.parent).free < MIN_FREE_BYTES:
        raise ValueError("insufficient private journal capacity")
    return {
        "start_ns": start_ns,
        "end_ns": end_ns,
        "calendar_version": calendar.version,
        "classification": "UNVERIFIED",
    }


def check_raw_capture(
    *, journal: Path, master: Path, manifest: Path, universe_path: Path, now_ns: int
) -> dict:
    """Admit quarantined bytes only; this does not establish research eligibility."""
    if now_ns <= 0:
        raise ValueError("clock unavailable")
    now = datetime.fromtimestamp(now_ns / 1_000_000_000, UTC)
    local = now.astimezone(IST)
    if local.weekday() >= 5 or not time(9, 15) <= local.time() < time(15, 14):
        raise ValueError("outside candidate continuous-session capture window")
    universe = json.loads(universe_path.read_text())
    rows = universe.get("instruments", [])
    if (
        universe.get("schema_version") != "alpha-grid-cross-stock-universe-v1"
        or len(rows) != 25
    ):
        raise ValueError("fixed 25-name universe required")
    ids = [row["security_id"] for row in rows]
    if len(set(ids)) != 25:
        raise ValueError("duplicate universe security ID")
    identity = verify_master_identities(
        master, manifest,
        [(row["symbol"], row["security_id"]) for row in rows],
        now=now,
    )
    if any(item.instrument.segment != "NSE_EQ" for item in identity):
        raise ValueError("Dhan subscription mapping invalid")
    _private_directory(journal.parent)
    if journal.is_symlink():
        raise PermissionError("journal path cannot be a symlink")
    if journal.is_dir():
        _private_directory(journal)
        total_size = sum(f.stat().st_size for f in journal.glob("*.qj") if f.is_file())
        if total_size > MAX_SEGMENTED_JOURNAL_BYTES:
            raise ValueError("segmented journal byte cap reached")
    elif journal.is_file():
        if sys.platform == "win32":
            _verify_windows_private_acl(journal)
        elif journal.stat().st_mode & 0o077:
            raise PermissionError("journal file is not private")
        if journal.stat().st_size > MAX_JOURNAL_BYTES:
            raise ValueError("journal byte cap reached")
    else:
        if journal.suffix == ".qj":
            _write_private_bytes(journal, b"")
            if sys.platform == "win32":
                _verify_windows_private_acl(journal)
            elif journal.stat().st_mode & 0o077:
                raise PermissionError("journal file is not private")
        else:
            journal.mkdir(mode=0o700, exist_ok=True)
            _private_directory(journal)
    if shutil.disk_usage(journal.parent).free < MIN_FREE_BYTES:
        raise ValueError("insufficient private journal capacity")
    start = datetime.combine(local.date(), time(9, 15), IST)
    end = datetime.combine(local.date(), time(15, 15), IST)
    return {
        "start_ns": int(start.timestamp() * 1_000_000_000),
        "end_ns": int(end.timestamp() * 1_000_000_000),
        "calendar_version": "RAW_CAPTURE_UNVERIFIED",
        "classification": "UNVERIFIED",
        "dhan_master_sha256": identity[0].master_sha256,
        "dhan_master_retrieved_at": identity[0].retrieved_at.isoformat(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("journal", "master", "manifest", "calendar"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--now-ns", type=int, required=True)
    parser.add_argument("--pilot-three", action="store_true")
    parser.add_argument("--nse-security", type=Path)
    parser.add_argument("--nse-manifest", type=Path)
    parser.add_argument("--universe", type=Path)
    parser.add_argument("--raw-capture-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.raw_capture_only:
            if args.universe is None:
                raise ValueError("raw capture requires exact frozen universe")
            result = check_raw_capture(
                journal=args.journal,
                master=args.master,
                manifest=args.manifest,
                universe_path=args.universe,
                now_ns=args.now_ns,
            )
        else:
            result = check(
                journal=args.journal,
                master=args.master,
                manifest=args.manifest,
                calendar_path=args.calendar,
                now_ns=args.now_ns,
                pilot_three=args.pilot_three,
                nse_security=args.nse_security,
                nse_manifest=args.nse_manifest,
                universe_path=args.universe,
            )
    except (OSError, ValueError, PermissionError, KeyError, TypeError):
        print('{"status":"BLOCKED"}')
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
