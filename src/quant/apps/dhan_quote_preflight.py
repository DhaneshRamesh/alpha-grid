"""Offline, fail-closed evidence check for one read-only Dhan quote session.

No broker credentials or network operations enter this process.
"""

from __future__ import annotations

import argparse
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
from quant.acquisition.dhan_instruments import verify_master_identity

MAX_JOURNAL_BYTES = 64 * 1024 * 1024
MIN_FREE_BYTES = 2 * MAX_JOURNAL_BYTES


def check(
    *,
    journal: Path,
    master: Path,
    manifest: Path,
    calendar_path: Path,
    now_ns: int,
) -> dict[str, int | str]:
    if now_ns <= 0:
        raise ValueError("clock unavailable")
    now = datetime.fromtimestamp(now_ns / 1_000_000_000, UTC)
    identity = verify_master_identity(
        master, manifest, symbol="RELIANCE", security_id="2885", now=now
    )
    if identity.instrument.segment != "NSE_EQ":
        raise ValueError("identity scope mismatch")
    calendar = load_calendar(calendar_path)
    day = now.astimezone(IST).date()
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
    if journal.is_symlink() or journal.is_dir():
        raise PermissionError("journal path is not a regular private file")
    if not journal.exists():
        _write_private_bytes(journal, b"")
    if sys.platform == "win32":
        _verify_windows_private_acl(journal)
    elif journal.stat().st_mode & 0o077:
        raise PermissionError("journal file is not private")
    if journal.stat().st_size > MAX_JOURNAL_BYTES:
        raise ValueError("journal byte cap reached")
    if shutil.disk_usage(journal.parent).free < MIN_FREE_BYTES:
        raise ValueError("insufficient private journal capacity")
    return {
        "start_ns": start_ns,
        "end_ns": end_ns,
        "calendar_version": calendar.version,
        "classification": "UNVERIFIED",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    for name in ("journal", "master", "manifest", "calendar"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--now-ns", type=int, required=True)
    args = parser.parse_args()
    try:
        result = check(
            journal=args.journal,
            master=args.master,
            manifest=args.manifest,
            calendar_path=args.calendar,
            now_ns=args.now_ns,
        )
    except (OSError, ValueError, PermissionError, KeyError, TypeError):
        print('{"status":"BLOCKED"}')
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
