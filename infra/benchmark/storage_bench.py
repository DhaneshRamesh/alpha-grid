"""Sequential append and durable fsync latency on the chosen journal filesystem."""

from __future__ import annotations

import os
import tempfile
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from infra.benchmark.common import distribution
from quant.domain import DepthEvent, DepthLevel
from quant.storage.journal import EventJournal, FsyncPolicy, JournalSettings


def run_storage(
    directory: Path, *, samples: int = 1000, record_bytes: int = 4096
) -> dict[str, Any]:
    if samples < 1 or record_bytes < 1:
        raise ValueError("storage samples and record size must be positive")
    if not directory.is_dir():
        raise ValueError("storage directory must already exist")
    write_ms: list[float] = []
    fsync_ms: list[float] = []
    observations: list[dict[str, Any]] = []
    payload = b"J" * record_bytes
    descriptor, name = tempfile.mkstemp(prefix=".quant-benchmark-", dir=directory)
    try:
        start_all = time.perf_counter_ns()
        for index in range(samples):
            start = time.perf_counter_ns()
            offset = 0
            while offset < record_bytes:
                offset += os.write(descriptor, payload[offset:])
            write_elapsed = (time.perf_counter_ns() - start) / 1_000_000
            write_ms.append(write_elapsed)
            start = time.perf_counter_ns()
            os.fsync(descriptor)
            fsync_elapsed = (time.perf_counter_ns() - start) / 1_000_000
            fsync_ms.append(fsync_elapsed)
            observations.append(
                {
                    "sample": index,
                    "at": datetime.now(UTC).isoformat(),
                    "write_ms": write_elapsed,
                    "fsync_ms": fsync_elapsed,
                }
            )
        duration_seconds = (time.perf_counter_ns() - start_all) / 1_000_000_000
    finally:
        os.close(descriptor)
        os.unlink(name)
    filesystem = os.statvfs(directory)
    return {
        "samples": samples,
        "record_bytes": record_bytes,
        "write_ms": distribution(write_ms),
        "fsync_ms": distribution(fsync_ms),
        "append_records_per_second": samples / duration_seconds,
        "append_bytes_per_second": samples * record_bytes / duration_seconds,
        "observations": observations,
        "interrupted_write_recovery": _probe_recovery(directory),
        "free_bytes": filesystem.f_bavail * filesystem.f_frsize,
        "note": "One append and one fsync per record on the selected filesystem.",
    }


def _probe_recovery(directory: Path) -> dict[str, Any]:
    """Damage only a private test journal, then verify intact records survive."""
    with tempfile.TemporaryDirectory(prefix=".quant-recovery-", dir=directory) as name:
        settings = JournalSettings(Path(name), "benchmark", FsyncPolicy.ALWAYS)
        timestamp = datetime.now(UTC)
        level = DepthLevel(Decimal("100.25"), 10, 1)
        event = DepthEvent("NSE", "NSE_EQ", "123", None, timestamp, (level,), (level,))
        with EventJournal(settings) as journal:
            journal.append(event)
            journal.append(event)
        path = next(settings.root.glob("session=*/date=*/events.qj"))
        with path.open("r+b") as stream:
            stream.truncate(path.stat().st_size - 8)
            stream.flush()
            os.fsync(stream.fileno())
        start = time.perf_counter_ns()
        with EventJournal(settings) as recovered:
            stats = recovered.recovery
            retained = sum(1 for _ in recovered.records())
        return {
            "passed": retained == 1
            and stats.records == 1
            and stats.truncated_bytes > 0,
            "retained_records": retained,
            "truncated_bytes": stats.truncated_bytes,
            "recovery_ms": (time.perf_counter_ns() - start) / 1_000_000,
        }
