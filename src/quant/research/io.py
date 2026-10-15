"""Strict read-only QP01 and exported QJ01 readers."""

from __future__ import annotations

import gzip
import json
import struct
import zlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO, cast


def _open(path: Path) -> BinaryIO:
    return cast(
        BinaryIO, gzip.open(path, "rb") if path.suffix == ".gz" else path.open("rb")
    )


def read_paper(path: Path) -> Iterator[dict[str, Any]]:
    """Verify every frame. A truncated/corrupt source is never silently skipped."""
    with _open(path) as stream:
        while header := stream.read(12):
            if len(header) != 12 or header[:4] != b"QP01":
                raise ValueError("invalid or truncated QP01 header")
            length, checksum = struct.unpack("<II", header[4:])
            if not 0 < length <= 2 * 1024 * 1024:
                raise ValueError("invalid QP01 frame length")
            payload = stream.read(length)
            if len(payload) != length or zlib.crc32(payload) != checksum:
                raise ValueError("truncated or corrupt QP01 frame")
            record: dict[str, Any] = json.loads(payload)
            if record.get("schema_version") != 1:
                raise ValueError("unsupported QP01 schema")
            yield record


def read_market_export(path: Path) -> Iterator[dict[str, Any]]:
    """Read the output of the pinned Rust QJ01 exporter, preserving order."""
    with _open(path) as stream:
        for expected, raw in enumerate(stream, 1):
            value: dict[str, Any] = json.loads(raw)
            if value.get("source_record_index") != expected:
                raise ValueError("QJ01 export has missing or reordered records")
            if not isinstance(value.get("record"), dict):
                raise ValueError("invalid QJ01 export")
            yield value
