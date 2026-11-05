"""Offline approval-gate fixtures; never connect to Dhan."""

import gzip
import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from quant.acquisition.dhan_instruments import MASTER_URL
from quant.apps.live_feed_scope import (
    approved_plan,
    ensure_local_read_only_scope,
    evaluated_gate,
    main,
)


def test_local_token_creates_one_revocable_25_stock_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    docs = tmp_path / "docs"
    docs.mkdir()
    universe = docs / "cross-stock-universe-2026-11-02.json"
    universe.write_text(json.dumps({"instruments": [
        {"symbol": f"S{i}", "security_id": str(i)} for i in range(25)]}))
    monkeypatch.setattr("quant.apps.live_feed_scope.load_credentials",
                        lambda *a: ("fixture.token", "123", datetime.now(UTC) + timedelta(hours=1)))
    monkeypatch.setattr("quant.apps.live_feed_credentials._check_profile",
                        lambda *a: "DHAN_TOKEN_AND_DATA_PLAN_ACTIVE")
    ensure_local_read_only_scope(tmp_path, private)
    path = private / "feed-scope.json"
    saved = json.loads(path.read_text())
    assert saved["authorized_security_ids"] == [str(i) for i in range(25)]
    assert saved["authorized_feeds"] == ["STANDARD_QUOTE"]
    assert saved["data_entitlement_evidence"] == "DHAN_V2_PROFILE_ACTIVE"
    assert path.stat().st_mode & 0o077 == 0
    saved["revoked_at"] = datetime.now(UTC).isoformat()
    path.write_text(json.dumps(saved))
    ensure_local_read_only_scope(tmp_path, private)
    assert json.loads(path.read_text())["revoked_at"] == saved["revoked_at"]


def test_missing_or_wrong_dated_scope_fails_before_clock_or_feed(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    with pytest.raises(ValueError, match="approval absent"):
        approved_plan(tmp_path, private)
    approval = private / "feed-scope.json"
    approval.write_text(
        json.dumps({"schema_version": "invalid", "authorization_confirmed": True})
    )
    approval.chmod(0o600)
    with pytest.raises(ValueError, match="scope unavailable"):
        approved_plan(tmp_path, private)


def test_dated_25_name_scope_passes_only_offline_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path
    private = root / "private"
    private.mkdir(mode=0o700)
    docs = root / "docs"
    docs.mkdir()
    day = "2026-11-03"
    now = datetime(2026, 11, 3, 5, 0, tzinfo=UTC)
    instruments = [
        {"symbol": f"FIXTURE{i}", "security_id": str(i), "isin": f"INE{i:09d}"}
        for i in range(1, 26)
    ]
    universe = docs / "cross-stock-universe-2026-11-02.json"
    universe.write_text(
        json.dumps(
            {
                "schema_version": "alpha-grid-cross-stock-universe-v1",
                "instruments": instruments,
            }
        )
    )
    master = root / "master.csv"
    master.write_text(
        "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_TRADING_SYMBOL,SEM_SERIES,SM_SYMBOL_NAME\n"
        + "".join(
            f"NSE,E,{row['security_id']},EQUITY,{row['symbol']},EQ,{row['symbol']}\n"
            for row in instruments
        )
    )
    master_manifest = root / "master.manifest.json"
    master_manifest.write_text(
        json.dumps(
            {
                "schema_version": "dhan-instrument-master-v1",
                "source_url": MASTER_URL,
                "sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
                "retrieved_at": now.isoformat(),
            }
        )
    )
    nse = root / "NSE_CM_security_03112026.csv.gz"
    nse.write_bytes(
        gzip.compress(
            (
                "FinInstrmId,TckrSymb,SctySrs,ISIN,ElgbltyNrmlMkt,ElgbltyClsgAuctnSsn\n"
                + "".join(
                    f"{row['security_id']},{row['symbol']},EQ,{row['isin']},1,1\n"
                    for row in instruments
                )
            ).encode()
        )
    )
    nse_manifest = root / "nse.manifest.json"
    nse_manifest.write_text(
        json.dumps(
            {
                "source_url": f"https://nsearchives.nseindia.com/content/cm/{nse.name}",
                "sha256": hashlib.sha256(nse.read_bytes()).hexdigest(),
                "retrieved_at_utc": now.isoformat(),
            }
        )
    )
    calendar = root / "calendar.json"
    calendar.write_text(
        json.dumps(
            {
                "version": "fixture-calendar",
                "scope": {"start": day, "end": "2026-11-04"},
                "special": [
                    {
                        "day": day,
                        "opens": "09:15:00",
                        "closes": "15:15:00",
                        "kind": "ORDINARY_OBSERVATION_ONLY",
                        "verified": False,
                    }
                ],
                "observation_evidence": {
                    "day": day,
                    "ordinary_session_verified": True,
                    "classification": "UNVERIFIED",
                    "checked_at": now.isoformat(),
                    "holiday_circular_url": "https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf",
                    "normal_hours_url": "https://www.nseindia.com/static/market-data/market-timings",
                    "cas_rule_url": "https://www.nseindia.com/static/products-services/closing-auction-session",
                },
            }
        )
    )
    approval = private / "feed-scope.json"
    approval.write_text(
        json.dumps(
            {
                "schema_version": "alpha-grid-approved-quote-scope-v1",
                "session_date_ist": day,
                "purpose": "SIMULATION_ONLY",
                "standard_connections": 1,
                "depth_connections": 0,
                "authorization_confirmed": True,
                "authorized_security_ids": [row["security_id"] for row in instruments],
                "authorized_feeds": ["STANDARD_QUOTE"],
                "expires_at_ist": "2026-11-03T15:14:00+05:30",
                "universe_sha256": hashlib.sha256(universe.read_bytes()).hexdigest(),
                "master": master.name,
                "master_manifest": master_manifest.name,
                "calendar": calendar.name,
                "nse_security": nse.name,
                "nse_manifest": nse_manifest.name,
            }
        )
    )
    approval.chmod(0o600)
    monkeypatch.setattr(
        "quant.apps.live_feed_scope.load_credentials",
        lambda *a, **k: ("fixture", "000000", now + timedelta(hours=8)),
    )
    monkeypatch.setattr(
        "quant.apps.live_feed_scope.subprocess.run",
        lambda *a, **k: SimpleNamespace(
            stdout="+0.010 +/- 0.020 time.google.com fixture"
        ),
    )
    plan = approved_plan(root, private, int(now.timestamp() * 1_000_000_000))
    assert len(plan[0].split(",")) == 25
    assert plan[1].endswith("standard-2026-11-03.qj")
    assert (private / "standard-2026-11-03.qj").exists()
    monkeypatch.setattr(
        "quant.apps.live_feed_scope.subprocess.run",
        lambda *a, **k: SimpleNamespace(
            stdout="+0.080 +/- 0.030 time.google.com fixture"
        ),
    )
    with pytest.raises(ValueError, match="100 ms"):
        approved_plan(root, private, int(now.timestamp() * 1_000_000_000))
    scope = json.loads(approval.read_text())
    scope["authorized_security_ids"] = scope["authorized_security_ids"][:-1]
    approval.write_text(json.dumps(scope))
    with pytest.raises(ValueError, match="approved instruments"):
        approved_plan(root, private, int(now.timestamp() * 1_000_000_000))
    scope["authorized_security_ids"].append("25")
    scope["expires_at_ist"] = "2026-11-03T09:59:00+05:30"
    approval.write_text(json.dumps(scope))
    with pytest.raises(ValueError, match="expired"):
        approved_plan(root, private, int(now.timestamp() * 1_000_000_000))
    instruments_dir = root / "data/instruments"
    instruments_dir.mkdir(parents=True)
    config_dir = root / "data/config"
    config_dir.mkdir(parents=True)
    for source, name in (
        (master, "dhan-scrip-master-2026-11-03.csv"),
        (master_manifest, "dhan-scrip-master-2026-11-03.manifest.json"),
        (nse, nse.name),
        (nse_manifest, "NSE_CM_security_03112026.manifest.json"),
    ):
        shutil.copyfile(source, instruments_dir / name)
    shutil.copyfile(calendar, config_dir / "calendar.json")
    scope = {
        "schema_version": "alpha-grid-market-data-access-v2",
        "purpose": "SIMULATION_ONLY",
        "segment": "NSE_EQ",
        "standard_connections": 1,
        "depth_connections": 0,
        "authorization_confirmed": True,
        "data_entitlement_confirmed": True,
        "auto_connect": True,
        "authorized_security_ids": [row["security_id"] for row in instruments],
        "authorized_feeds": ["STANDARD_QUOTE"],
        "universe_sha256": hashlib.sha256(universe.read_bytes()).hexdigest(),
        "duration": "PERSISTENT",
        "session_date_ist": None,
        "expires_at": None,
        "revoked_at": None,
    }
    approval.write_text(json.dumps(scope))
    monkeypatch.setattr(
        "sys.argv",
        [
            "live_feed_scope",
            "--root",
            str(root),
            "--private-dir",
            str(private),
            "--display-only",
        ],
    )
    assert main() == 0
    assert capsys.readouterr().out.strip().split(",") == [
        row["security_id"] for row in instruments
    ]
    monkeypatch.setattr(
        "quant.apps.live_feed_scope.subprocess.run",
        lambda *a, **k: SimpleNamespace(
            stdout="+0.010 +/- 0.020 time.google.com fixture"
        ),
    )
    eligible_plan = approved_plan(root, private, int(now.timestamp() * 1_000_000_000))
    assert eligible_plan[0].count(",") == 24
    assert eligible_plan[-4] == "STANDARD_QUOTE"
    assert eligible_plan[-2] == "RAW_CAPTURE_WITH_ELIGIBLE_PROJECTION"
    scope["authorized_feeds"] = ["STANDARD_FULL"]
    approval.write_text(json.dumps(scope))
    full_plan = approved_plan(root, private, int(now.timestamp() * 1_000_000_000))
    assert full_plan[-4] == "STANDARD_FULL"
    assert full_plan[-2] == "RAW_CAPTURE_WITH_ELIGIBLE_PROJECTION"
    scope["authorized_feeds"] = ["STANDARD_QUOTE"]
    approval.write_text(json.dumps(scope))
    binary = root / "rust/target/debug/quant-depth"
    binary.parent.mkdir(parents=True)
    binary.write_text("fixture")
    binary.chmod(0o700)
    scope["depth_connections"] = 1
    scope["authorized_feeds"] = ["STANDARD_QUOTE", "DEPTH_20"]
    approval.write_text(json.dumps(scope))
    assert approved_plan(root, private, int(now.timestamp() * 1_000_000_000))[-3] == "1"
    scope["depth_connections"] = 0
    scope["authorized_feeds"] = ["STANDARD_QUOTE"]
    approval.write_text(json.dumps(scope))
    scope["auto_connect"] = False
    approval.write_text(json.dumps(scope))
    with pytest.raises(ValueError, match="disabled"):
        approved_plan(root, private, int(now.timestamp() * 1_000_000_000))
    scope["auto_connect"] = True
    scope["expires_at"] = "2026-11-03T04:59:00+00:00"
    approval.write_text(json.dumps(scope))
    with pytest.raises(ValueError, match="expired"):
        approved_plan(root, private, int(now.timestamp() * 1_000_000_000))
    scope["expires_at"] = None
    approval.write_text(json.dumps(scope))
    (instruments_dir / nse.name).unlink()
    (config_dir / "calendar.json").unlink()
    monkeypatch.setattr(
        "quant.apps.live_feed_scope.subprocess.run",
        lambda *a, **k: SimpleNamespace(
            stdout="+0.080 +/- 0.030 time.google.com fixture"
        ),
    )
    raw_plan = approved_plan(root, private, int(now.timestamp() * 1_000_000_000))
    assert raw_plan[-2] == "RAW_CAPTURE_ONLY"
    validation = json.loads(
        (private / "raw-capture-validation-2026-11-03.jsonl")
        .read_text()
        .splitlines()[-1]
    )
    assert validation["clock_offset_seconds"] == 0.080
    assert validation["clock_uncertainty_seconds"] == 0.030
    assert "CLOCK_PRECISION_UNVERIFIED" in validation["unresolved"]
    assert "DATED_NSE_IDENTITY_OR_CAS_UNVERIFIED" in validation["unresolved"]
    assert validation["research_eligible"] is False
    assert validation["production_eligible"] is False
    copied_master = instruments_dir / "dhan-scrip-master-2026-11-03.csv"
    copied_master.write_text(copied_master.read_text().replace("FIXTURE1", "WRONG", 1))
    with pytest.raises(ValueError, match="provenance"):
        approved_plan(root, private, int(now.timestamp() * 1_000_000_000))
    shutil.copyfile(master, copied_master)
    disk_usage = shutil.disk_usage(private)
    monkeypatch.setattr(
        "quant.apps.dhan_quote_preflight.shutil.disk_usage",
        lambda *_: disk_usage._replace(free=0),
    )
    with pytest.raises(ValueError, match="capacity"):
        approved_plan(root, private, int(now.timestamp() * 1_000_000_000))
    monkeypatch.undo()
    monkeypatch.setattr(
        "quant.apps.live_feed_scope.load_credentials",
        lambda *a, **k: ("fixture", "000000", now + timedelta(hours=8)),
    )
    after_close = datetime(2026, 11, 3, 11, 0, tzinfo=UTC)
    assert evaluated_gate(root, private, int(after_close.timestamp() * 1_000_000_000))[
        1:
    ] == ("MARKET CLOSED", "AUTO_CONNECT_ARMED")
    scope["revoked_at"] = after_close.isoformat()
    approval.write_text(json.dumps(scope))
    assert evaluated_gate(root, private, int(after_close.timestamp() * 1_000_000_000))[
        1:
    ] == ("BLOCKED", "APPROVED_SCOPE_DISABLED_OR_EXPIRED")
