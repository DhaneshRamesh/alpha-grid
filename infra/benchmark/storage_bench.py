"""Sequential append and durable fsync latency on the chosen journal filesystem."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

from infra.benchmark.common import distribution


def run_storage(
    directory: Path, *, samples: int = 1000, record_bytes: int = 4096
) -> dict[str, Any]:
    if samples < 1 or record_bytes < 1:
        raise ValueError("storage samples and record size must be positive")
    if not directory.is_dir():
        raise ValueError("storage directory must already exist")
    write_ms: list[float] = []
    fsync_ms: list[float] = []
    payload = b"J" * record_bytes
    descriptor, name = tempfile.mkstemp(prefix=".quant-benchmark-", dir=directory)
    try:
        for _ in range(samples):
            start = time.perf_counter_ns()
            offset = 0
            while offset < record_bytes:
                offset += os.write(descriptor, payload[offset:])
            write_ms.append((time.perf_counter_ns() - start) / 1_000_000)
            start = time.perf_counter_ns()
            os.fsync(descriptor)
            fsync_ms.append((time.perf_counter_ns() - start) / 1_000_000)
    finally:
        os.close(descriptor)
        os.unlink(name)
    filesystem = os.statvfs(directory)
    return {
        "samples": samples,
        "record_bytes": record_bytes,
        "write_ms": distribution(write_ms),
        "fsync_ms": distribution(fsync_ms),
        "free_bytes": filesystem.f_bavail * filesystem.f_frsize,
        "note": "One append and one fsync per record on the selected filesystem.",
    }
