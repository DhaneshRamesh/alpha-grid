from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from quant.acquisition.contracts import CashInstrument, HistoricalBar
from quant.acquisition.lake import (
    LakeManifest,
    stage_raw_file,
    verify_dataset,
    write_dataset,
)

INSTRUMENT = CashInstrument("NSE", "NSE_EQ", "1333", "RELIANCE", "INE002A01018")


def bar(day: date) -> HistoricalBar:
    return HistoricalBar(
        INSTRUMENT,
        None,
        day,
        None,
        100_000_000_000,
        101_000_000_000,
        99_000_000_000,
        100_500_000_000,
        500,
        "NSE_UDIFF_CM",
        "a" * 64,
        "DAILY",
        50_250_000_000_000,
        300,
    )


def write(rows: list[HistoricalBar], path: Path) -> LakeManifest:
    return write_dataset(
        iter(rows),
        path,
        dataset_id="test-dataset-v1",
        acquisition_mode="LOCAL_FILE",
        instrument_universe=("1333",),
        calendar_version="fixture-calendar",
        adjustment_policy="UNADJUSTED",
        code_commit="fixture-commit",
        batch_size=1,
        max_rows_per_file=2,
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )


def test_immutable_raw_and_multi_year_partition_determinism(tmp_path: Path) -> None:
    raw = tmp_path / "archive.zip"
    raw.write_bytes(b"synthetic archive")
    staged = stage_raw_file(raw, tmp_path / "raw", "NSE_UDIFF_CM")
    assert staged.read_bytes() == raw.read_bytes()
    assert stage_raw_file(raw, tmp_path / "raw", "NSE_UDIFF_CM") == staged
    staged.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        stage_raw_file(raw, tmp_path / "raw", "NSE_UDIFF_CM")

    rows = [bar(date(2023, 1, 2)), bar(date(2024, 1, 2)), bar(date(2025, 1, 2))]
    first = write(rows, tmp_path / "first")
    second = write(rows, tmp_path / "second")
    assert first.dataset_sha256 == second.dataset_sha256
    assert len(first.partitions) == 3
    assert verify_dataset(tmp_path / "first") == first
    table = pq.read_table(tmp_path / "first" / first.partitions[0].path)
    assert table.column("close_nanos").to_pylist() == [100_500_000_000]
    assert table.column("trading_date").to_pylist() == [date(2023, 1, 2)]
    partition = tmp_path / "first" / first.partitions[0].path
    with partition.open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(ValueError, match="checksum"):
        verify_dataset(tmp_path / "first")


def test_failed_publish_does_not_leave_partial_dataset(tmp_path: Path) -> None:
    rows = [bar(date(2025, 1, 2)), replace(bar(date(2025, 1, 2)), volume=501)]
    with pytest.raises(ValueError, match="sorted and deduplicated"):
        write(rows, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()
    assert not list(tmp_path.glob(".bad.*.partial"))


def test_interrupted_publication_retries_from_original_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "synthetic.raw"
    raw.write_bytes(b"synthetic archive\n\x00original bytes")
    raw_root = tmp_path / "raw"
    with monkeypatch.context() as fault:

        def interrupted_link(*_: object) -> None:
            raise OSError("interrupted raw publication")

        fault.setattr(os, "link", interrupted_link)
        with pytest.raises(OSError, match="interrupted raw publication"):
            stage_raw_file(raw, raw_root, "NSE_UDIFF_CM")
    assert not list(raw_root.rglob("*.partial"))
    staged = stage_raw_file(raw, raw_root, "NSE_UDIFF_CM")
    assert staged.read_bytes() == raw.read_bytes()

    output = tmp_path / "published"
    with monkeypatch.context() as fault:

        def interrupted_replace(*_: object) -> None:
            raise OSError("interrupted dataset publication")

        fault.setattr(os, "replace", interrupted_replace)
        with pytest.raises(OSError, match="interrupted dataset publication"):
            write([bar(date(2025, 1, 2))], output)
    assert not output.exists()
    assert not list(tmp_path.glob(".published.*.partial"))
    manifest = write([bar(date(2025, 1, 2))], output)
    assert verify_dataset(output) == manifest
