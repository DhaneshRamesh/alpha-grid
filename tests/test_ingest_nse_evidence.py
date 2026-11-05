"""Tests for src/quant/apps/ingest_nse_evidence.py."""

from __future__ import annotations

import csv
import gzip
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from quant.apps.dhan_quote_preflight import verify_dated_nse_security
from quant.apps.ingest_nse_evidence import ingest_nse_security, update_calendar_session


@pytest.fixture
def sample_universe(tmp_path: Path) -> tuple[Path, dict[str, tuple[str, str]]]:
    instruments = [
        {"symbol": f"SYM{i}", "security_id": str(1000 + i), "isin": f"INE{i:09d}"}
        for i in range(25)
    ]
    u_path = tmp_path / "universe.json"
    u_path.write_text(
        json.dumps({
            "schema_version": "alpha-grid-cross-stock-universe-v1",
            "instruments": instruments,
        }),
        encoding="utf-8",
    )
    identities = {row["symbol"]: (row["security_id"], row["isin"]) for row in instruments}
    return u_path, identities


@pytest.fixture
def valid_nse_gz(tmp_path: Path, sample_universe: tuple[Path, dict[str, tuple[str, str]]]) -> Path:
    _, identities = sample_universe
    csv_path = tmp_path / "raw.csv.gz"
    with gzip.open(csv_path, "wt", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "FinInstrmId",
                "TckrSymb",
                "SctySrs",
                "ISIN",
                "ElgbltyNrmlMkt",
                "ElgbltyClsgAuctnSsn",
            ],
        )
        writer.writeheader()
        for sym, (sec_id, isin) in identities.items():
            writer.writerow({
                "FinInstrmId": sec_id,
                "TckrSymb": sym,
                "SctySrs": "EQ",
                "ISIN": isin,
                "ElgbltyNrmlMkt": "1",
                "ElgbltyClsgAuctnSsn": "1",
            })
    return csv_path


def test_ingest_nse_security_success(
    tmp_path: Path,
    sample_universe: tuple[Path, dict[str, tuple[str, str]]],
    valid_nse_gz: Path,
) -> None:
    u_path, identities = sample_universe
    dest_dir = tmp_path / "instruments"
    calendar_path = tmp_path / "calendar.json"
    calendar_path.write_text(
        json.dumps({"version": "v1", "special": []}),
        encoding="utf-8",
    )

    day = "2026-11-04"
    now_utc = datetime(2026, 11, 4, 6, 0, 0, tzinfo=UTC)

    target_gz, target_manifest = ingest_nse_security(
        valid_nse_gz,
        day,
        universe_path=u_path,
        dest_dir=dest_dir,
        calendar_path=calendar_path,
        update_calendar=True,
        now_utc=now_utc,
    )

    assert target_gz.name == "NSE_CM_security_04112026.csv.gz"
    assert target_manifest.name == "NSE_CM_security_04112026.manifest.json"
    assert target_gz.exists()
    assert target_manifest.exists()

    manifest = json.loads(target_manifest.read_text(encoding="utf-8"))
    assert manifest["verified_universe_count"] == 25
    assert manifest["sha256"] != ""

    # Check preflight verification passes on the ingested output
    cas_flags = verify_dated_nse_security(
        target_gz,
        target_manifest,
        day,
        now=now_utc,
        identities=identities,
    )
    assert len(cas_flags) == 25

    # Check calendar was updated
    cal = json.loads(calendar_path.read_text(encoding="utf-8"))
    assert any(s["day"] == day and s["kind"] == "ORDINARY_OBSERVATION_ONLY" for s in cal["special"])
    assert cal["observation_evidence"]["day"] == day


def test_ingest_nse_security_missing_symbol(
    tmp_path: Path,
    sample_universe: tuple[Path, dict[str, tuple[str, str]]],
) -> None:
    u_path, _ = sample_universe
    csv_path = tmp_path / "incomplete.csv.gz"
    # Write only 1 row instead of 25
    with gzip.open(csv_path, "wt", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "FinInstrmId",
                "TckrSymb",
                "SctySrs",
                "ISIN",
                "ElgbltyNrmlMkt",
                "ElgbltyClsgAuctnSsn",
            ],
        )
        writer.writeheader()
        writer.writerow({
            "FinInstrmId": "1000",
            "TckrSymb": "SYM0",
            "SctySrs": "EQ",
            "ISIN": "INE000000000",
            "ElgbltyNrmlMkt": "1",
            "ElgbltyClsgAuctnSsn": "1",
        })

    with pytest.raises(ValueError, match="matches"):
        ingest_nse_security(
            csv_path,
            "2026-11-04",
            universe_path=u_path,
            dest_dir=tmp_path / "dest",
            calendar_path=None,
        )
