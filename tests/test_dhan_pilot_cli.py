from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq
import pytest

from quant.acquisition.contracts import CashInstrument
from quant.acquisition.dhan import plan_requests, run_backfill
from quant.acquisition.dhan_instruments import MASTER_URL, verify_master_identity
from quant.acquisition.lake import verify_dataset
from quant.apps.acquisition import run


def master_fixture(tmp_path: Path) -> tuple[Path, Path]:
    master = tmp_path / "master.csv"
    master.write_text(
        "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,"
        "SEM_INSTRUMENT_NAME,SEM_TRADING_SYMBOL,SEM_SERIES,SM_SYMBOL_NAME\n"
        "NSE,E,2885,EQUITY,RELIANCE,EQ,RELIANCE INDUSTRIES LTD\n"
    )
    manifest = tmp_path / "master.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "dhan-instrument-master-v1",
                "source_url": MASTER_URL,
                "retrieved_at": datetime.now(UTC).isoformat(),
                "sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
            }
        )
    )
    return master, manifest


def arguments(master: Path, manifest: Path) -> list[str]:
    return [
        "--security-id",
        "2885",
        "--symbol",
        "RELIANCE",
        "--start",
        "2025-01-02",
        "--end",
        "2025-01-03",
        "--master",
        str(master),
        "--master-manifest",
        str(manifest),
    ]


def test_public_master_identity_and_staleness(tmp_path: Path) -> None:
    master, manifest = master_fixture(tmp_path)
    identity = verify_master_identity(
        master, manifest, symbol="RELIANCE", security_id="2885"
    )
    assert identity.instrument.security_id == "2885"
    with pytest.raises(ValueError, match="not uniquely"):
        verify_master_identity(master, manifest, symbol="RELIANCE", security_id="1333")
    with pytest.raises(ValueError, match="stale"):
        verify_master_identity(
            master,
            manifest,
            symbol="RELIANCE",
            security_id="2885",
            now=datetime.now(UTC) + timedelta(days=8),
        )
    master.write_text(master.read_text() + "BSE,E,1,EQUITY,X,EQ,X\n")
    with pytest.raises(ValueError, match="provenance"):
        verify_master_identity(master, manifest, symbol="RELIANCE", security_id="2885")


def test_pilot_guard_and_no_network_dry_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    master, manifest = master_fixture(tmp_path)
    args = arguments(master, manifest)
    monkeypatch.delenv("DHAN_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(
        "quant.apps.acquisition.run_backfill",
        lambda *a, **k: pytest.fail("network path reached"),
    )
    dry = run(["dry-run", *args])
    assert dry["mode"] == "DRY_RUN_NO_NETWORK"
    assert dry["request_count"] == 1
    assert dry["instrument_evidence"]["security_id"] == "2885"
    assert run(["verify-config", *args])["token_present"] is False
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "never-print-this")
    config = run(["verify-config", *args])
    assert config["token_present"] is True
    assert "never-print-this" not in json.dumps(config)
    monkeypatch.delenv("DHAN_ACCESS_TOKEN")
    with pytest.raises(ValueError, match="one-minute"):
        run(["verify-config", *args, "--daily"])
    with pytest.raises(PermissionError, match="explicit authorization"):
        run(["pilot", *args])
    with pytest.raises(ValueError, match="DHAN_ACCESS_TOKEN"):
        run(
            [
                "pilot",
                *args,
                "--subscription-confirmed",
                "--pilot-authorized",
                "--execute-read-only",
            ]
        )
    assert not (tmp_path / "raw").exists()
    with pytest.raises(ValueError, match="evidenced calendar and registry"):
        run(
            [
                "verify",
                *args,
                "--calendar",
                str(tmp_path / "calendar.json"),
                "--registry",
                str(tmp_path / "registry.json"),
            ]
        )


def test_diagnose_saved_bytes_is_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    master, manifest = master_fixture(tmp_path)
    raw = tmp_path / "response.json"
    raw.write_bytes(
        b'{"timestamp":[1740000000000],"open":[],"high":[],"low":[],"close":[],"volume":[]}'
    )
    monkeypatch.setattr(
        "quant.apps.acquisition.run_backfill",
        lambda *a, **k: pytest.fail("network path reached"),
    )
    result = run(
        ["diagnose", *arguments(master, manifest), "--response-file", str(raw)]
    )
    assert result["timestamp_units"] == ["milliseconds"]
    assert result["array_lengths"]["open"] == 0


def test_recover_quarantined_response_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    master, manifest = master_fixture(tmp_path)
    stamp = int(
        datetime(2025, 1, 2, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()
    )
    raw = json.dumps(
        {
            "timestamp": [float(stamp)],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.5],
            "volume": [123.0],
        }
    ).encode()
    request = plan_requests(
        (CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE"),),
        date(2025, 1, 2),
        date(2025, 1, 3),
        1,
    )
    run_backfill(
        request,
        output_dir=tmp_path / "source",
        enabled=True,
        token="fixture-only",
        transport=FakeTransport(raw),
        sleep=lambda _: None,
    )
    evidence = next((tmp_path / "source" / ".quarantine").glob("*.response"))
    receipt = json.loads(evidence.with_suffix(".meta.json").read_text())
    monkeypatch.setattr(
        "quant.apps.acquisition.run_backfill",
        lambda *a, **k: pytest.fail("network path reached"),
    )
    args = [
        "recover",
        *arguments(master, manifest),
        "--response-file",
        str(evidence),
        "--output-dir",
        str(tmp_path / "recovered"),
    ]
    result = run(args)
    assert result["mode"] == "RECOVER_QUARANTINED_NO_NETWORK"
    assert result["rows"] == 1
    assert result["response_sha256"] == hashlib.sha256(raw).hexdigest()
    checkpoint = Path(result["saved_response"])
    assert checkpoint.read_bytes() == raw
    assert (
        json.loads(checkpoint.with_suffix(".meta.json").read_text())["received_at"]
        == receipt["received_at"]
    )
    assert run(args)["response_sha256"] == result["response_sha256"]


class FakeTransport:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    def post_json(self, url: str, payload: dict[str, object], token: str) -> bytes:
        assert url == "https://api.dhan.co/v2/charts/intraday"
        return self.raw


def test_mocked_saved_response_verify_and_import(tmp_path: Path) -> None:
    master, manifest = master_fixture(tmp_path)
    raw_dir = tmp_path / "responses"
    source_dir = tmp_path / "sources"
    output = tmp_path / "lake" / "pilot"
    stamp = int(
        datetime(2025, 1, 2, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()
    )
    response = json.dumps(
        {
            "timestamp": [stamp, stamp + 60],
            "open": [100, 101],
            "high": [101, 102],
            "low": [99, 100],
            "close": [100, 101],
            "volume": [100, 200],
        }
    ).encode()
    request = plan_requests(
        (CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE"),),
        date(2025, 1, 2),
        date(2025, 1, 3),
        1,
    )
    paths = run_backfill(
        request,
        output_dir=raw_dir,
        enabled=True,
        token="fixture-only",
        transport=FakeTransport(response),
        sleep=lambda _: None,
    )
    assert (
        json.loads(paths[0].with_suffix(".meta.json").read_text())["method"] == "POST"
    )
    assert "fixture-only" not in paths[0].with_suffix(".meta.json").read_text()
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "version": "fixture",
                "records": [
                    {
                        "instrument": {
                            "venue": "NSE",
                            "segment": "NSE_EQ",
                            "security_id": "2885",
                            "symbol": "RELIANCE",
                            "isin": None,
                        },
                        "name": "Fixture",
                        "effective_from": "2025-01-01",
                        "effective_to": None,
                        "listing_status": "LISTED",
                        "source_manifest_version": "fixture",
                    }
                ],
            }
        )
    )
    calendar = tmp_path / "calendar.json"
    calendar.write_text(
        json.dumps(
            {
                "version": "fixture",
                "holidays": [],
                "special": [
                    {
                        "day": "2025-01-02",
                        "opens": "09:15:00",
                        "closes": "09:17:00",
                        "kind": "FIXTURE",
                    }
                ],
            }
        )
    )
    common = [
        *arguments(master, manifest),
        "--output-dir",
        str(raw_dir),
        "--raw-root",
        str(source_dir),
        "--output",
        str(output),
        "--registry",
        str(registry),
        "--calendar",
        str(calendar),
    ]
    verified = run(["verify", *common])
    assert verified["rows"] == 2
    assert verified["retrieval_times_recorded"] is True
    assert verified["timestamp_semantics"] == "UNVERIFIED"
    result = run(["import", *common, "--code-commit", "fixture"])
    lake = verify_dataset(output)
    assert result["dataset_sha256"] == lake.dataset_sha256
    assert lake.row_count == 2
    table = pq.read_table(next(output.rglob("*.parquet")))
    assert table.column("volume").to_pylist() == [100, 200]
    status = json.loads(
        (output.parent / "dhan-historical-pilot-status.json").read_text()
    )
    assert status["source_files_available"] == 1
    assert status["genuine_source_files_available"] == 0
    assert status["training_blocked"] is True
    reported = run(["report", *common])
    assert reported["research_eligibility"] == "NOT_TRAINED"
    assert reported["independent_sessions"] == 1
    with pytest.raises(ValueError, match="immutable output"):
        run(["import", *common, "--code-commit", "fixture"])
    registry.write_text(json.dumps({"version": "unverified", "records": []}))
    calendar.write_text(
        json.dumps(
            {
                "version": "provisional",
                "special": [
                    {
                        "day": "2025-01-02",
                        "opens": "09:15:00",
                        "closes": "09:17:00",
                        "verified": False,
                    }
                ],
            }
        )
    )
    blocked = run(["verify", *common])
    groups = {item["code"]: item for item in blocked["quality_issue_summary"]}
    assert blocked["hard_failures"] == 3
    assert groups["IDENTITY_UNVERIFIED"]["issue_entries"] == 3
    assert groups["IDENTITY_UNVERIFIED"]["records_in_affected_sessions"] == 2
    assert groups["IDENTITY_UNVERIFIED"]["affected_sessions"] == [
        {"security_id": "2885", "date": "2025-01-02"}
    ]
    assert groups["CALENDAR_APPLICABILITY_UNVERIFIED"]["issue_entries"] == 1
    paths[0].with_suffix(".sha256").write_text("bad")
    with pytest.raises(ValueError, match="checksum"):
        run(["verify", *common])
    paths[0].with_suffix(".sha256").write_text(hashlib.sha256(response).hexdigest())
    metadata_path = paths[0].with_suffix(".meta.json")
    metadata = json.loads(metadata_path.read_text())
    metadata["request"]["securityId"] = "1333"
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="provenance"):
        run(["verify", *common])
