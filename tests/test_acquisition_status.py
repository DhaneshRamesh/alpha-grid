from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from quant.acquisition.contracts import CashInstrument, HistoricalBar
from quant.acquisition.lake import write_dataset
from quant.acquisition.quality import QualityIssue, QualityReport
from quant.acquisition.status import acquisition_status, write_status


def test_status_default_and_verified_local_manifest(tmp_path: Path) -> None:
    empty = acquisition_status()
    assert empty["access_status"] == "UNVERIFIED"
    assert empty["checksum_verified"] is False
    bar = HistoricalBar(
        CashInstrument("NSE", "NSE_EQ", "1333", "RELIANCE"),
        None,
        date(2025, 1, 2),
        None,
        100_000_000_000,
        101_000_000_000,
        99_000_000_000,
        100_000_000_000,
        500,
        "NSE_UDIFF_CM",
        "a" * 64,
        "DAILY",
    )
    output = tmp_path / "lake"
    write_dataset(
        [bar],
        output,
        dataset_id="fixture",
        acquisition_mode="LOCAL_FILE",
        instrument_universe=("1333",),
        calendar_version="fixture",
        adjustment_policy="UNADJUSTED",
        code_commit="fixture",
    )
    quality = QualityReport(
        1,
        1,
        0,
        (QualityIssue("PRICE_GAP", "1333", date(2025, 1, 2), "unknown", "WARNING"),),
    )
    status = acquisition_status(dataset_dir=output, quality=quality)
    assert status["checksum_verified"] is True
    assert status["instrument_count"] == 1
    assert status["access_status"] == "UNVERIFIED"
    assert status["research_eligibility"] == "NOT_TRAINED"
    path = tmp_path / "status.json"
    write_status(path, status)
    assert "access_token" not in path.read_text()
    partition = next(output.rglob("*.parquet"))
    with partition.open("ab") as stream:
        stream.write(b"bad")
    with pytest.raises(ValueError, match="checksum"):
        acquisition_status(dataset_dir=output)
