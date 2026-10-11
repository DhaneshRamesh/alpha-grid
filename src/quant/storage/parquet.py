"""Replay the raw journal into atomically published, research-ready Parquet."""

from __future__ import annotations

import fcntl
import math
import os
import shutil
import time
import uuid
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from quant.storage.journal import (
    JournalRecord,
    JournalSettings,
    _mkdir_durable,
    iter_journal_records,
)

_IST = ZoneInfo("Asia/Kolkata")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_PRICE = pa.decimal128(38, 18)
_TIMESTAMP = pa.timestamp("ns", tz="UTC")


def _schema() -> pa.Schema:
    fields = [
        pa.field("schema_version", pa.int16(), nullable=False),
        pa.field("collector_version", pa.string(), nullable=False),
        pa.field("session_id", pa.string(), nullable=False),
        pa.field("event_type", pa.string(), nullable=False),
        pa.field("venue", pa.string(), nullable=False),
        pa.field("exchange_segment", pa.string(), nullable=False),
        pa.field("security_id", pa.string(), nullable=False),
        pa.field("symbol", pa.string()),
        pa.field("receive_timestamp", _TIMESTAMP, nullable=False),
        pa.field("exchange_timestamp", _TIMESTAMP),
        pa.field("sequence_number", pa.int64()),
        pa.field("bid_level_count", pa.int8(), nullable=False),
        pa.field("ask_level_count", pa.int8(), nullable=False),
        pa.field("journal_date", pa.string(), nullable=False),
        pa.field("journal_offset", pa.int64(), nullable=False),
    ]
    for side in ("bid", "ask"):
        for index in range(1, 21):
            suffix = f"{side}_{{}}_{index:02d}"
            fields.extend(
                [
                    pa.field(suffix.format("price"), _PRICE),
                    pa.field(suffix.format("qty"), pa.int64()),
                    pa.field(suffix.format("orders"), pa.int64()),
                ]
            )
    return pa.schema(fields, metadata={b"alpha_grid_schema": b"depth_v1"})


DEPTH_SCHEMA = _schema()


@dataclass(frozen=True, slots=True)
class ParquetSettings:
    journal: JournalSettings
    output_root: Path
    batch_size: int = 5_000
    batch_seconds: float = 900.0
    zstd_level: int = 3

    @property
    def dataset_path(self) -> Path:
        return self.output_root / f"session={self.journal.session_id}"

    def __post_init__(self) -> None:
        if self.batch_size < 1:
            raise ValueError("batch_size must be positive")
        if not math.isfinite(self.batch_seconds) or self.batch_seconds <= 0:
            raise ValueError("batch_seconds must be finite and positive")
        if not 1 <= self.zstd_level <= 22:
            raise ValueError("zstd_level must be between 1 and 22")

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> ParquetSettings:
        try:
            batch_size = int(environ.get("QUANT_PARQUET_BATCH_SIZE", "5000"))
            batch_seconds = float(environ.get("QUANT_PARQUET_BATCH_SECONDS", "900"))
            level = int(environ.get("QUANT_PARQUET_ZSTD_LEVEL", "3"))
        except ValueError as exc:
            raise ValueError("invalid Parquet batch or compression setting") from exc
        return cls(
            JournalSettings.from_env(environ),
            Path(environ.get("QUANT_PARQUET_DIR", "data/research_depth")),
            batch_size,
            batch_seconds,
            level,
        )


@dataclass(frozen=True, slots=True)
class ExportStats:
    depth_events: int
    skipped_events: int
    parquet_files: int
    compressed_bytes: int
    elapsed_seconds: float
    dataset_path: Path

    @property
    def events_per_second(self) -> float:
        return self.depth_events / self.elapsed_seconds if self.elapsed_seconds else 0.0

    @property
    def compressed_bytes_per_event(self) -> float:
        return self.compressed_bytes / self.depth_events if self.depth_events else 0.0


def _timestamp_ns(text: str) -> int:
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("journal timestamp must have a timezone")
    fraction = text.split(".", 1)[1] if "." in text else ""
    # Slice before the UTC offset; offset digits must not become fractional digits.
    digits = fraction[: len(fraction) - len(fraction.lstrip("0123456789"))]
    if len(digits) > 9:
        raise ValueError("journal timestamp exceeds nanosecond precision")
    nanoseconds = int(digits.ljust(9, "0")) if digits else 0
    delta = value.replace(microsecond=0).astimezone(UTC) - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + nanoseconds


def _row(record: JournalRecord) -> tuple[dict[str, Any], tuple[str, str, str]]:
    envelope = record.envelope
    event = envelope["event"]
    if not isinstance(event, dict) or envelope["event_type"] != "depth":
        raise ValueError("expected a depth journal record")
    receive_text = event["receive_timestamp"]
    receive_ns = _timestamp_ns(receive_text)
    received = datetime.fromisoformat(receive_text.replace("Z", "+00:00"))
    local = received.astimezone(_IST)
    journal_date = record.source_file.parent.name.removeprefix("date=")
    if journal_date != local.date().isoformat():
        raise ValueError("journal partition date differs from event receive date")
    partition = (
        local.date().isoformat(),
        f"{event['exchange_segment']}-{event['instrument_id']}",
        f"{local.hour:02d}",
    )
    row: dict[str, Any] = {
        "schema_version": envelope["schema_version"],
        "collector_version": envelope["collector_version"],
        "session_id": envelope["session_id"],
        "event_type": "depth",
        "venue": event["venue"],
        "exchange_segment": event["exchange_segment"],
        "security_id": event["instrument_id"],
        "symbol": envelope.get("symbol"),
        "receive_timestamp": receive_ns,
        "exchange_timestamp": (
            _timestamp_ns(event["exchange_timestamp"])
            if event["exchange_timestamp"] is not None
            else None
        ),
        "sequence_number": event["sequence_number"],
        "bid_level_count": len(event["bid_levels"]),
        "ask_level_count": len(event["ask_levels"]),
        "journal_date": journal_date,
        "journal_offset": record.offset,
    }
    for side in ("bid", "ask"):
        levels = event[f"{side}_levels"]
        if len(levels) > 20:
            raise ValueError("depth journal record has more than 20 levels")
        for index, level in enumerate(levels, 1):
            price, quantity, orders = level
            row[f"{side}_price_{index:02d}"] = Decimal(price)
            row[f"{side}_qty_{index:02d}"] = quantity
            row[f"{side}_orders_{index:02d}"] = orders
    return row, partition


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_tree(root: Path) -> None:
    for directory, _, _ in os.walk(root, topdown=False):
        _fsync_dir(Path(directory))


def _write_batch(
    root: Path,
    partition: tuple[str, str, str],
    index: int,
    rows: list[dict[str, Any]],
    zstd_level: int,
) -> int:
    date, instrument, hour = partition
    directory = (
        root
        / f"date={date}"
        / f"instrument={quote(instrument, safe='')}"
        / f"hour={hour}"
    )
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"part-{index:06d}.parquet"
    table = pa.Table.from_pylist(rows, schema=DEPTH_SCHEMA)
    pq.write_table(
        table,
        path,
        compression="zstd",
        compression_level=zstd_level,
        version="2.6",
        row_group_size=len(rows),
    )
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
    return path.stat().st_size


def export_journal(settings: ParquetSettings) -> ExportStats:
    """Rebuild a complete snapshot, then atomically publish one dataset path."""
    started = time.perf_counter()
    session_dir = settings.journal.root / f"session={settings.journal.session_id}"
    if not any(session_dir.glob("date=*/events.qj")):
        raise FileNotFoundError(
            f"no journal segments for {settings.journal.session_id}"
        )
    output = settings.dataset_path.absolute()
    if output.exists() and not output.is_symlink():
        raise ValueError("output_root must be absent or an exporter-managed symlink")
    generation_root = output.parent / f".{output.name}.generations"
    _mkdir_durable(generation_root)
    lock_fd = os.open(generation_root / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        generation = generation_root / f"gen-{uuid.uuid4().hex}"
        generation.mkdir()
        try:
            buffers: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(
                list
            )
            part_numbers: dict[tuple[str, str, str], int] = defaultdict(int)
            first_ns: dict[tuple[str, str, str], int] = {}
            depth_events = skipped = files = compressed = 0

            def flush(partition: tuple[str, str, str]) -> None:
                nonlocal files, compressed
                rows = buffers[partition]
                if not rows:
                    return
                compressed += _write_batch(
                    generation,
                    partition,
                    part_numbers[partition],
                    rows,
                    settings.zstd_level,
                )
                files += 1
                part_numbers[partition] += 1
                buffers[partition] = []
                first_ns.pop(partition, None)

            max_age_ns = int(settings.batch_seconds * 1_000_000_000)
            for record in iter_journal_records(settings.journal):
                if record.envelope.get("session_id") != settings.journal.session_id:
                    raise ValueError("journal session metadata mismatch")
                if record.envelope.get("schema_version") != 1:
                    raise ValueError("unsupported journal schema version")
                if record.envelope.get("event_type") == "trade":
                    skipped += 1
                    continue
                if record.envelope.get("event_type") != "depth":
                    raise ValueError("unsupported journal event type")
                event = record.envelope["event"]
                if not isinstance(event, dict):
                    raise ValueError("invalid journal depth event")
                if event["instrument_id"] != record.envelope.get(
                    "security_id"
                ) or event["receive_timestamp"] != record.envelope.get(
                    "receive_timestamp"
                ):
                    raise ValueError("journal event metadata mismatch")
                row, partition = _row(record)
                received_ns = row["receive_timestamp"]
                if (
                    partition in first_ns
                    and received_ns - first_ns[partition] >= max_age_ns
                ):
                    flush(partition)
                if not buffers[partition]:
                    first_ns[partition] = received_ns
                buffers[partition].append(row)
                depth_events += 1
                if len(buffers[partition]) >= settings.batch_size:
                    flush(partition)
            for partition in list(buffers):
                flush(partition)
            _fsync_tree(generation)
            _fsync_dir(generation_root)
            link = output.parent / f".{output.name}.next-{uuid.uuid4().hex}"
            try:
                os.symlink(generation, link)
                os.replace(link, output)
                _fsync_dir(output.parent)
            finally:
                link.unlink(missing_ok=True)
            return ExportStats(
                depth_events,
                skipped,
                files,
                compressed,
                time.perf_counter() - started,
                output,
            )
        except BaseException:
            # A failed conversion never alters the currently published dataset.
            if not output.is_symlink() or output.resolve() != generation:
                shutil.rmtree(generation)
            raise
    finally:
        os.close(lock_fd)
