"""Software-only startup checks; fixtures are never Dhan market evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest

from quant.acquisition.calendar import load_calendar
from quant.acquisition.dhan_instruments import MASTER_URL
from quant.apps.dhan_quote_preflight import check


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
