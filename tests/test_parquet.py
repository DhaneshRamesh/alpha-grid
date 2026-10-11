from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant.domain import DepthEvent, DepthLevel
from quant.storage import parquet as converter
from quant.storage.journal import (
    EventJournal,
    FsyncPolicy,
    JournalCorruptionError,
    JournalSettings,
)
from quant.storage.parquet import DEPTH_SCHEMA, ParquetSettings, export_journal


class NanoDatetime(datetime):
    def isoformat(self, sep: str = "T", timespec: str = "auto") -> str:
        return super().isoformat(sep, timespec).replace("+00:00", "789+00:00")


def _event(security_id: str, at: datetime, sequence: int) -> DepthEvent:
    bids = tuple(
        DepthLevel(
            Decimal("100.050000000123456789") - Decimal(index) / 20, index + 1, index
        )
        for index in range(20)
    )
    asks = tuple(
        DepthLevel(Decimal("100.10") + Decimal(index) / 20, index + 2, None)
        for index in range(20)
    )
    return DepthEvent(
        "NSE",
        "NSE_EQ",
        security_id,
        at - timedelta(microseconds=100),
        at,
        bids,
        asks,
        sequence,
    )


def _settings(tmp_path: Path, *, batch_size: int = 100) -> ParquetSettings:
    journal = JournalSettings(tmp_path / "journal", "NSE-2026-10-11", FsyncPolicy.NEVER)
    return ParquetSettings(journal, tmp_path / "research", batch_size, 3600)


def test_arrow_polars_round_trip_twenty_levels_and_nanoseconds(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    at = NanoDatetime(2026, 10, 11, 3, 45, 0, 123456, UTC)
    event = _event("1333", at, 7)
    with EventJournal(settings.journal, {("NSE_EQ", "1333"): "HDFCBANK"}) as journal:
        journal.append(event)
    result = export_journal(settings)
    assert result.depth_events == 1
    assert result.parquet_files == 1
    file = next(result.dataset_path.rglob("*.parquet"))
    arrow = pq.read_table(file)
    assert arrow.schema.equals(DEPTH_SCHEMA, check_metadata=True)
    row = arrow.to_pylist()[0]
    assert row["security_id"] == "1333"
    assert row["symbol"] == "HDFCBANK"
    assert row["sequence_number"] == 7
    assert row["bid_level_count"] == row["ask_level_count"] == 20
    for index in range(20):
        suffix = f"{index + 1:02d}"
        assert row[f"bid_price_{suffix}"] == event.bid_levels[index].price
        assert row[f"bid_qty_{suffix}"] == event.bid_levels[index].quantity
        assert row[f"bid_orders_{suffix}"] == event.bid_levels[index].order_count
        assert row[f"ask_price_{suffix}"] == event.ask_levels[index].price
        assert row[f"ask_qty_{suffix}"] == event.ask_levels[index].quantity
        assert row[f"ask_orders_{suffix}"] is None
    assert (
        arrow["receive_timestamp"].cast(pa.int64())[0].as_py() % 1_000_000_000
        == 123456789
    )
    assert (
        arrow["exchange_timestamp"].cast(pa.int64())[0].as_py() % 1_000_000_000
        == 123356789
    )
    frame = pl.read_parquet(file)
    assert frame.height == 1
    assert frame["bid_price_01"][0] == event.bid_levels[0].price
    assert frame["receive_timestamp"].dtype == pl.Datetime("ns", "UTC")
    assert pq.read_metadata(file).row_group(0).column(0).compression == "ZSTD"


def test_partition_batching_and_replay_have_no_duplicate_final_rows(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, batch_size=3)
    start = datetime(2026, 10, 11, 3, 45, tzinfo=UTC)
    with EventJournal(settings.journal) as journal:
        for index in range(8):
            journal.append(_event("1333", start + timedelta(seconds=index), index))
        journal.append(_event("2885", start, 99))
        journal.append(_event("1333", start + timedelta(hours=1), 100))
    first = export_journal(settings)
    assert first.depth_events == 10
    assert first.parquet_files == 5  # 3+3+2, plus two other partitions
    first_files = sorted(first.dataset_path.rglob("*.parquet"))
    assert len(first_files) == 5
    first_generation = first.dataset_path.resolve()
    second = export_journal(settings)
    assert second.depth_events == 10
    assert (
        sum(
            pq.read_metadata(path).num_rows
            for path in second.dataset_path.rglob("*.parquet")
        )
        == 10
    )
    assert "instrument=NSE_EQ-1333" in str(first_files[0]) or any(
        "instrument=NSE_EQ-1333" in str(path) for path in first_files
    )
    assert first_generation != second.dataset_path.resolve()


def test_failed_conversion_keeps_previous_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    at = datetime(2026, 10, 11, 3, 45, tzinfo=UTC)
    with EventJournal(settings.journal) as journal:
        journal.append(_event("1333", at, 1))
    export_journal(settings)
    published = settings.dataset_path.resolve()
    original_write = converter._write_batch

    def fail_write(*_args: object, **_kwargs: object) -> int:
        raise OSError("conversion failed")

    monkeypatch.setattr(converter, "_write_batch", fail_write)
    with pytest.raises(OSError, match="conversion failed"):
        export_journal(settings)
    assert settings.dataset_path.resolve() == published
    assert (
        sum(
            pq.read_metadata(path).num_rows
            for path in settings.dataset_path.rglob("*.parquet")
        )
        == 1
    )
    monkeypatch.setattr(converter, "_write_batch", original_write)
    retry = export_journal(settings)
    assert retry.depth_events == 1
    assert (
        sum(
            pq.read_metadata(path).num_rows
            for path in settings.dataset_path.rglob("*.parquet")
        )
        == 1
    )


def test_batch_age_threshold_and_incomplete_levels(tmp_path: Path) -> None:
    settings = ParquetSettings(
        _settings(tmp_path).journal, tmp_path / "research", 100, 1
    )
    at = datetime(2026, 10, 11, 3, 45, tzinfo=UTC)
    with EventJournal(settings.journal) as journal:
        journal.append(DepthEvent("NSE", "NSE_EQ", "1333", None, at, (), (), None))
        journal.append(_event("1333", at + timedelta(seconds=2), 2))
    result = export_journal(settings)
    assert result.parquet_files == 2
    rows = [
        row
        for path in sorted(result.dataset_path.rglob("*.parquet"))
        for row in pq.read_table(path).to_pylist()
    ]
    assert rows[0]["bid_level_count"] == 0
    assert rows[0]["bid_price_01"] is None
    assert rows[1]["bid_level_count"] == 20


def test_exporting_another_session_keeps_prior_session(tmp_path: Path) -> None:
    first = _settings(tmp_path)
    second = ParquetSettings(
        JournalSettings(tmp_path / "journal", "NSE-2026-10-12", FsyncPolicy.NEVER),
        first.output_root,
    )
    with EventJournal(first.journal) as journal:
        journal.append(_event("1333", datetime(2026, 10, 11, 3, 45, tzinfo=UTC), 1))
    with EventJournal(second.journal) as journal:
        journal.append(_event("2885", datetime(2026, 10, 12, 3, 45, tzinfo=UTC), 2))
    export_journal(first)
    export_journal(second)
    assert first.dataset_path.is_symlink()
    assert second.dataset_path.is_symlink()
    assert (
        sum(pq.read_metadata(p).num_rows for p in first.dataset_path.rglob("*.parquet"))
        == 1
    )
    assert (
        sum(
            pq.read_metadata(p).num_rows for p in second.dataset_path.rglob("*.parquet")
        )
        == 1
    )


def test_incomplete_journal_tail_keeps_published_dataset(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with EventJournal(settings.journal) as journal:
        journal.append(_event("1333", datetime(2026, 10, 11, 3, 45, tzinfo=UTC), 1))
    export_journal(settings)
    published = settings.dataset_path.resolve()
    source = next(settings.journal.root.rglob("events.qj"))
    with source.open("ab") as stream:
        stream.write(b"QJ")
    with pytest.raises(JournalCorruptionError, match="incomplete"):
        export_journal(settings)
    assert settings.dataset_path.resolve() == published


def test_missing_session_does_not_publish_empty_dataset(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(FileNotFoundError, match="no journal segments"):
        export_journal(settings)
    assert not settings.dataset_path.exists()
