"""Immutable raw files and streaming OHLCV Parquet/Zstd dataset publication."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from quant.acquisition.contracts import SCHEMA_VERSION, HistoricalBar
from quant.acquisition.nse import file_sha256

OHLCV_SCHEMA = pa.schema(
    [
        pa.field("venue", pa.string(), nullable=False),
        pa.field("segment", pa.string(), nullable=False),
        pa.field("security_id", pa.string(), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("isin", pa.string()),
        pa.field("trading_date", pa.date32(), nullable=False),
        pa.field("interval_minutes", pa.int16()),
        pa.field("source_timestamp_ns", pa.int64()),
        pa.field("timestamp_semantics", pa.string(), nullable=False),
        pa.field("open_nanos", pa.int64(), nullable=False),
        pa.field("high_nanos", pa.int64(), nullable=False),
        pa.field("low_nanos", pa.int64(), nullable=False),
        pa.field("close_nanos", pa.int64(), nullable=False),
        pa.field("volume", pa.int64(), nullable=False),
        pa.field("turnover_nanos", pa.decimal128(38, 0)),
        pa.field("delivery_quantity", pa.int64()),
        pa.field("source", pa.string(), nullable=False),
        pa.field("source_sha256", pa.string(), nullable=False),
    ],
    metadata={b"alpha_grid_schema": SCHEMA_VERSION.encode()},
)


@dataclass(frozen=True, slots=True)
class PartitionInfo:
    path: str
    rows: int
    bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class LakeManifest:
    schema_version: str
    dataset_id: str
    dataset_sha256: str
    sources: tuple[str, ...]
    acquisition_mode: str
    instrument_universe: tuple[str, ...]
    first_date: str
    last_date: str
    timezone: str
    calendar_version: str
    adjustment_policy: str
    source_checksums: tuple[str, ...]
    quality_exclusions: tuple[str, ...]
    code_commit: str
    created_at: str
    row_count: int
    partitions: tuple[PartitionInfo, ...]


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def stage_raw_file(source_file: Path, raw_root: Path, source: str) -> Path:
    """Content-address original bytes; verify an existing copy on retry."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", source):
        raise ValueError("invalid source label")
    digest = file_sha256(source_file)
    target = raw_root / source / digest
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if file_sha256(target) != digest:
            raise ValueError("immutable raw source checksum mismatch")
        return target
    temporary = target.parent / f".{digest}.{uuid.uuid4().hex}.partial"
    try:
        with source_file.open("rb") as reader, temporary.open("xb") as writer:
            shutil.copyfileobj(reader, writer, 1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
        if file_sha256(temporary) != digest:
            raise ValueError("raw source changed during copy")
        os.link(temporary, target)
        _fsync_dir(target.parent)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _partition(bar: HistoricalBar) -> str:
    sid = bar.instrument.security_id
    source = bar.source
    if not re.fullmatch(r"[A-Za-z0-9_-]+", sid) or not re.fullmatch(
        r"[A-Za-z0-9_-]+", source
    ):
        raise ValueError("unsafe partition identity")
    interval = "daily" if bar.interval_minutes is None else f"{bar.interval_minutes}m"
    return (
        f"normalized/source={source}/interval={interval}/year={bar.trading_date.year:04d}/"
        f"month={bar.trading_date.month:02d}/security_id={sid}"
    )


def _row(bar: HistoricalBar) -> dict[str, Any]:
    return {
        "venue": bar.instrument.venue,
        "segment": bar.instrument.segment,
        "security_id": bar.instrument.security_id,
        "symbol": bar.instrument.symbol,
        "isin": bar.instrument.isin,
        "trading_date": bar.trading_date,
        "interval_minutes": bar.interval_minutes,
        "source_timestamp_ns": bar.source_timestamp_ns,
        "timestamp_semantics": bar.timestamp_semantics,
        "open_nanos": bar.open_nanos,
        "high_nanos": bar.high_nanos,
        "low_nanos": bar.low_nanos,
        "close_nanos": bar.close_nanos,
        "volume": bar.volume,
        "turnover_nanos": Decimal(bar.turnover_nanos)
        if bar.turnover_nanos is not None
        else None,
        "delivery_quantity": bar.delivery_quantity,
        "source": bar.source,
        "source_sha256": bar.source_sha256,
    }


def write_dataset(
    bars: Iterable[HistoricalBar],
    output_dir: Path,
    *,
    dataset_id: str,
    acquisition_mode: str,
    instrument_universe: tuple[str, ...],
    calendar_version: str,
    adjustment_policy: str,
    code_commit: str,
    quality_exclusions: tuple[str, ...] = (),
    batch_size: int = 8192,
    max_rows_per_file: int = 131072,
    created_at: datetime | None = None,
) -> LakeManifest:
    """Accept partition/date-sorted input; memory bounded by batch_size."""
    if output_dir.exists() or batch_size <= 0 or max_rows_per_file < batch_size:
        raise ValueError("new output and valid batch/file sizes required")
    if (
        not dataset_id
        or not calendar_version
        or not code_commit
        or not adjustment_policy
    ):
        raise ValueError("complete dataset lineage required")
    staging = output_dir.parent / f".{output_dir.name}.{uuid.uuid4().hex}.partial"
    staging.mkdir(parents=True)
    writer: pq.ParquetWriter | None = None
    buffer: list[dict[str, Any]] = []
    partition = ""
    partition_rows = 0
    file_rows = 0
    file_number = 0
    current_file: Path | None = None
    files: list[PartitionInfo] = []
    sources: set[str] = set()
    source_hashes: set[str] = set()
    first_date = last_date = None
    total = 0
    previous_order: tuple[str, str, int] | None = None

    def flush() -> None:
        nonlocal buffer
        if buffer and writer is not None:
            writer.write_table(pa.Table.from_pylist(buffer, schema=OHLCV_SCHEMA))
            buffer = []

    def close_file() -> None:
        nonlocal writer, current_file, file_rows
        flush()
        if writer is not None and current_file is not None:
            writer.close()
            with current_file.open("rb") as stream:
                os.fsync(stream.fileno())
            files.append(
                PartitionInfo(
                    str(current_file.relative_to(staging)),
                    file_rows,
                    current_file.stat().st_size,
                    file_sha256(current_file),
                )
            )
            writer = None
            current_file = None
            file_rows = 0

    try:
        for bar in bars:
            if bar.instrument.security_id not in instrument_universe:
                raise ValueError("bar outside frozen universe")
            key = _partition(bar)
            stamp = bar.source_timestamp_ns or bar.trading_date.toordinal()
            order = (key, bar.trading_date.isoformat(), stamp)
            if previous_order is not None and order <= previous_order:
                raise ValueError("input must be strictly sorted and deduplicated")
            previous_order = order
            if key != partition:
                close_file()
                partition = key
                partition_rows = 0
                file_number = 0
            if writer is None or file_rows >= max_rows_per_file:
                close_file()
                path = staging / key
                path.mkdir(parents=True, exist_ok=True)
                current_file = path / f"part-{file_number:05d}.parquet"
                writer = pq.ParquetWriter(
                    current_file, OHLCV_SCHEMA, compression="zstd"
                )
                file_number += 1
            buffer.append(_row(bar))
            file_rows += 1
            partition_rows += 1
            total += 1
            first_date = (
                min(first_date, bar.trading_date) if first_date else bar.trading_date
            )
            last_date = (
                max(last_date, bar.trading_date) if last_date else bar.trading_date
            )
            sources.add(bar.source)
            source_hashes.add(bar.source_sha256)
            if len(buffer) >= batch_size:
                flush()
        close_file()
        if total == 0 or first_date is None or last_date is None:
            raise ValueError("empty dataset")
        core: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "dataset_id": dataset_id,
            "sources": sorted(sources),
            "acquisition_mode": acquisition_mode,
            "instrument_universe": instrument_universe,
            "first_date": first_date.isoformat(),
            "last_date": last_date.isoformat(),
            "timezone": "Asia/Kolkata",
            "calendar_version": calendar_version,
            "adjustment_policy": adjustment_policy,
            "source_checksums": sorted(source_hashes),
            "quality_exclusions": quality_exclusions,
            "code_commit": code_commit,
            "row_count": total,
            "partitions": [asdict(item) for item in files],
        }
        digest = hashlib.sha256(_canonical(core)).hexdigest()
        manifest = LakeManifest(
            SCHEMA_VERSION,
            dataset_id,
            digest,
            tuple(sorted(sources)),
            acquisition_mode,
            instrument_universe,
            first_date.isoformat(),
            last_date.isoformat(),
            "Asia/Kolkata",
            calendar_version,
            adjustment_policy,
            tuple(sorted(source_hashes)),
            quality_exclusions,
            code_commit,
            (created_at or datetime.now(UTC)).astimezone(UTC).isoformat(),
            total,
            tuple(files),
        )
        manifest_path = staging / "manifest.json"
        with manifest_path.open("xb") as stream:
            stream.write(_canonical(asdict(manifest)) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_dir(staging)
        os.replace(staging, output_dir)
        _fsync_dir(output_dir.parent)
        return manifest
    except BaseException:
        if writer is not None:
            writer.close()
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_dataset(output_dir: Path) -> LakeManifest:
    data = json.loads((output_dir / "manifest.json").read_bytes())
    partitions = tuple(PartitionInfo(**item) for item in data.pop("partitions"))
    for name in (
        "sources", "instrument_universe", "source_checksums", "quality_exclusions"
    ):
        data[name] = tuple(data[name])
    manifest = LakeManifest(partitions=partitions, **data)
    for item in manifest.partitions:
        path = output_dir / item.path
        if file_sha256(path) != item.sha256 or path.stat().st_size != item.bytes:
            raise ValueError("Parquet partition checksum mismatch")
    core = asdict(manifest)
    core.pop("dataset_sha256")
    core.pop("created_at")
    if hashlib.sha256(_canonical(core)).hexdigest() != manifest.dataset_sha256:
        raise ValueError("dataset manifest checksum mismatch")
    return manifest
