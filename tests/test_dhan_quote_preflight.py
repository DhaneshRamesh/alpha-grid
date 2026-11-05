"""Software-only startup checks; fixtures are never Dhan market evidence."""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest

from quant.acquisition.calendar import load_calendar
from quant.acquisition.dhan_instruments import MASTER_URL
from quant.apps.dhan_quote_preflight import check, verify_dated_nse_security


def test_three_id_nse_source_requires_dated_bytes_and_cas(tmp_path: Path) -> None:
    now = datetime(2026, 11, 2, 7, 0, tzinfo=UTC)
    source = tmp_path / "NSE_CM_security_02112026.csv.gz"
    rows = (
        "FinInstrmId,TckrSymb,SctySrs,ISIN,ElgbltyNrmlMkt,ElgbltyClsgAuctnSsn\n"
        "1333,HDFCBANK,EQ,INE040A01034,1,1\n"
        "2885,RELIANCE,EQ,INE002A01018,1,1\n"
        "1594,INFY,EQ,INE009A01021,1,1\n"
    )
    source.write_bytes(gzip.compress(rows.encode()))
    manifest = tmp_path / "source.json"
    info = {
        "source_url": f"https://nsearchives.nseindia.com/content/cm/{source.name}",
        "retrieved_at_utc": now.isoformat(),
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    manifest.write_text(json.dumps(info))
    assert verify_dated_nse_security(source, manifest, "2026-11-02", now) == {
        "HDFCBANK": "1",
        "RELIANCE": "1",
        "INFY": "1",
    }
    source.write_bytes(source.read_bytes() + b"x")
    with pytest.raises(ValueError, match="bytes mismatch"):
        verify_dated_nse_security(source, manifest, "2026-11-02", now)


def test_scoped_private_quote_preflight_and_unverified_session_rejection(
    tmp_path: Path,
) -> None:
    day = date(2026, 10, 27)
    now = datetime.combine(day, time(10, 0), UTC) + timedelta(hours=-5, minutes=-30)
    # The timestamp represents 10:00 IST for this isolated fixture.
    master = tmp_path / "master.csv"
    master.write_text(
        "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,"
        "SEM_TRADING_SYMBOL,SEM_SERIES,SM_SYMBOL_NAME\n"
        "NSE,E,2885,EQUITY,RELIANCE,EQ,Reliance Industries\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "master.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "dhan-instrument-master-v1",
                "source_url": MASTER_URL,
                "sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
                "retrieved_at": now.isoformat(),
            }
        )
    )
    calendar = tmp_path / "calendar.json"
    session = {
        "day": day.isoformat(),
        "opens": "09:15:00",
        "closes": "15:15:00",
        "kind": "ORDINARY_OBSERVATION_ONLY",
        "verified": False,
    }
    evidence = {
        "day": day.isoformat(),
        "ordinary_session_verified": True,
        "classification": "UNVERIFIED",
        "checked_at": now.isoformat(),
        "holiday_circular_url": "https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf",
        "normal_hours_url": "https://www.nseindia.com/static/market-data/market-timings",
        "cas_rule_url": "https://www.nseindia.com/static/products-services/closing-auction-session",
    }
    calendar.write_text(
        json.dumps(
            {
                "version": "fixture-calendar",
                "scope": {
                    "start": day.isoformat(),
                    "end": (day + timedelta(days=1)).isoformat(),
                },
                "special": [session],
                "observation_evidence": evidence,
            }
        )
    )
    journal = tmp_path / "private" / "quote.qj"
    now_ns = int(now.timestamp()) * 1_000_000_000
    result = check(
        journal=journal,
        master=master,
        manifest=manifest,
        calendar_path=calendar,
        now_ns=now_ns,
    )
    assert result["start_ns"] <= now_ns < result["end_ns"]
    assert result["classification"] == "UNVERIFIED"
    assert load_calendar(calendar).session(day).verified is False
    assert journal.read_bytes() == b""
    session["kind"] = "CAS_CONTINUOUS_TRADING"
    calendar.write_text(
        json.dumps(
            {
                "version": "fixture-calendar",
                "scope": {
                    "start": day.isoformat(),
                    "end": (day + timedelta(days=1)).isoformat(),
                },
                "special": [session],
                "observation_evidence": evidence,
            }
        )
    )
    with pytest.raises(ValueError, match="dated ordinary observation session"):
        check(
            journal=journal,
            master=master,
            manifest=manifest,
            calendar_path=calendar,
            now_ns=now_ns,
        )
    session["kind"] = "ORDINARY_OBSERVATION_ONLY"
    session["verified"] = True
    calendar.write_text(
        json.dumps(
            {
                "version": "fixture-calendar",
                "scope": {
                    "start": day.isoformat(),
                    "end": (day + timedelta(days=1)).isoformat(),
                },
                "special": [session],
                "observation_evidence": evidence,
            }
        )
    )
    with pytest.raises(ValueError, match="dated ordinary observation session"):
        check(
            journal=journal,
            master=master,
            manifest=manifest,
            calendar_path=calendar,
            now_ns=now_ns,
        )
    session["verified"] = False
    evidence["classification"] = "CAS_VERIFIED"
    calendar.write_text(
        json.dumps(
            {
                "version": "fixture-calendar",
                "scope": {
                    "start": day.isoformat(),
                    "end": (day + timedelta(days=1)).isoformat(),
                },
                "special": [session],
                "observation_evidence": evidence,
            }
        )
    )
    with pytest.raises(ValueError, match="ordinary session provenance"):
        check(
            journal=journal,
            master=master,
            manifest=manifest,
            calendar_path=calendar,
            now_ns=now_ns,
        )

    # Segmented directory preflight support
    segmented_dir = tmp_path / "private" / "segmented_quotes"
    segmented_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    segmented_dir.chmod(0o700)
    (segmented_dir / "0000000000000000.qj").write_bytes(b"dummy")
    evidence["classification"] = "UNVERIFIED"
    calendar.write_text(
        json.dumps(
            {
                "version": "fixture-calendar",
                "scope": {
                    "start": day.isoformat(),
                    "end": (day + timedelta(days=1)).isoformat(),
                },
                "special": [
                    {
                        "day": day.isoformat(),
                        "opens": "09:15:00",
                        "closes": "15:15:00",
                        "kind": "ORDINARY_OBSERVATION_ONLY",
                        "verified": False,
                    }
                ],
                "observation_evidence": evidence,
            }
        )
    )
    result_seg = check(
        journal=segmented_dir,
        master=master,
        manifest=manifest,
        calendar_path=calendar,
        now_ns=now_ns,
    )
    assert result_seg["classification"] == "UNVERIFIED"

    # Non-existent segmented journal path is created as a private directory
    nonexistent_seg = tmp_path / "private" / "raw-standard-new-day"
    assert not nonexistent_seg.exists()
    result_new_seg = check(
        journal=nonexistent_seg,
        master=master,
        manifest=manifest,
        calendar_path=calendar,
        now_ns=now_ns,
    )
    assert nonexistent_seg.is_dir()
    assert (nonexistent_seg.stat().st_mode & 0o077) == 0
    assert result_new_seg["classification"] == "UNVERIFIED"
