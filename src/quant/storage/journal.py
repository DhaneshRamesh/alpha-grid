"""Single-writer, append-only journal for normalized market events."""

from __future__ import annotations

import fcntl
import json
import os
import re
import struct
import zlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from quant import __version__
from quant.domain import DepthEvent, MarketEvent, TradeEvent

_HEADER = struct.Struct(">4sII")  # magic, payload length, CRC32
_MAGIC = b"QJ01"
_MAX_RECORD_BYTES = 1_048_576
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_IST = ZoneInfo("Asia/Kolkata")


class FsyncPolicy(StrEnum):
    ALWAYS = "always"
    ON_CLOSE = "on_close"
    NEVER = "never"


@dataclass(frozen=True, slots=True)
class JournalSettings:
    root: Path
    session_id: str
    fsync_policy: FsyncPolicy = FsyncPolicy.ALWAYS
    collector_version: str = __version__

    def __post_init__(self) -> None:
        if not _SESSION_RE.fullmatch(self.session_id):
            raise ValueError("session_id must be a safe, nonempty path component")
        if not self.collector_version.strip():
            raise ValueError("collector_version must not be empty")
        if not isinstance(self.fsync_policy, FsyncPolicy):
            raise ValueError("invalid fsync_policy")

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> JournalSettings:
        session = environ.get("QUANT_JOURNAL_SESSION_ID") or (
            f"NSE-{datetime.now(_IST).date().isoformat()}"
        )
        try:
            policy = FsyncPolicy(environ.get("QUANT_JOURNAL_FSYNC", "always"))
        except ValueError as exc:
            raise ValueError(
                "QUANT_JOURNAL_FSYNC must be always, on_close, or never"
            ) from exc
        return cls(
            Path(environ.get("QUANT_JOURNAL_DIR", "data/journal")),
            session,
            policy,
        )


@dataclass(frozen=True, slots=True)
class RecoveryStats:
    records: int = 0
    truncated_bytes: int = 0
    repaired_files: int = 0


@dataclass(frozen=True, slots=True)
class JournalRecord:
    source_file: Path
    offset: int
    envelope: dict[str, Any]


class JournalCorruptionError(OSError):
    """Non-tail corruption; the journal is left untouched for investigation."""


def iter_journal_records(settings: JournalSettings) -> Iterator[JournalRecord]:
    """Read validated records without taking the writer's session lock."""
    session_dir = settings.root / f"session={settings.session_id}"
    files = [
        (path, path.stat().st_size)
        for path in sorted(session_dir.glob("date=*/events.qj"))
    ]
    for path, snapshot_size in files:
        with path.open("rb") as stream:
            while stream.tell() < snapshot_size:
                offset = stream.tell()
                header = stream.read(min(_HEADER.size, snapshot_size - offset))
                if len(header) != _HEADER.size:
                    raise JournalCorruptionError(
                        f"incomplete record at {path}:{offset}"
                    )
                magic, length, checksum = _HEADER.unpack(header)
                if magic != _MAGIC or not 0 < length <= _MAX_RECORD_BYTES:
                    raise JournalCorruptionError(
                        f"invalid record header at {path}:{offset}"
                    )
                if stream.tell() + length > snapshot_size:
                    raise JournalCorruptionError(
                        f"incomplete record at {path}:{offset}"
                    )
                payload = stream.read(length)
                if len(payload) != length or zlib.crc32(payload) != checksum:
                    raise JournalCorruptionError(
                        f"invalid record body at {path}:{offset}"
                    )
                try:
                    value = json.loads(payload)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise JournalCorruptionError(
                        f"invalid JSON record at {path}:{offset}"
                    ) from exc
                if not isinstance(value, dict):
                    raise JournalCorruptionError(
                        f"invalid record type at {path}:{offset}"
                    )
                yield JournalRecord(path, offset, value)


def _timestamp(value: datetime) -> str:
    # datetime subclasses with nanosecond precision can emit nine digits here.
    return value.isoformat()


def _mkdir_durable(path: Path) -> None:
    if path.is_dir():
        return
    _mkdir_durable(path.parent)
    try:
        path.mkdir()
    except FileExistsError:
        return
    parent_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _payload(
    event: MarketEvent, settings: JournalSettings, symbol: str | None
) -> bytes:
    body: dict[str, Any] = {
        "venue": event.venue,
        "exchange_segment": event.exchange_segment,
        "instrument_id": event.instrument_id,
        "exchange_timestamp": (
            _timestamp(event.exchange_timestamp)
            if event.exchange_timestamp is not None
            else None
        ),
        "receive_timestamp": _timestamp(event.receive_timestamp),
        "sequence_number": event.sequence_number,
    }
    if isinstance(event, DepthEvent):
        event_type = "depth"
        body["bid_levels"] = [
            [str(level.price), level.quantity, level.order_count]
            for level in event.bid_levels
        ]
        body["ask_levels"] = [
            [str(level.price), level.quantity, level.order_count]
            for level in event.ask_levels
        ]
    elif isinstance(event, TradeEvent):
        event_type = "trade"
        body.update(
            price=str(event.price), quantity=event.quantity, trade_id=event.trade_id
        )
    else:
        raise TypeError("unsupported market event")
    envelope = {
        "collector_version": settings.collector_version,
        "event": body,
        "event_type": event_type,
        "receive_timestamp": body["receive_timestamp"],
        "schema_version": 1,
        "security_id": event.instrument_id,
        "session_id": settings.session_id,
        "symbol": symbol,
    }
    return json.dumps(
        envelope,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _scan(path: Path, *, repair: bool) -> tuple[int, int]:
    """Return valid record count and discarded tail bytes."""
    mode = "r+b" if repair else "rb"
    with path.open(mode) as stream:
        size = path.stat().st_size
        valid_end = 0
        count = 0
        while valid_end < size:
            stream.seek(valid_end)
            header = stream.read(_HEADER.size)
            if len(header) < _HEADER.size:
                break
            magic, length, checksum = _HEADER.unpack(header)
            if magic != _MAGIC or not 0 < length <= _MAX_RECORD_BYTES:
                raise JournalCorruptionError(
                    f"invalid record header at {path}:{valid_end}"
                )
            end = valid_end + _HEADER.size + length
            if end > size:
                break
            payload = stream.read(length)
            if zlib.crc32(payload) != checksum:
                if end != size:
                    raise JournalCorruptionError(
                        f"non-tail checksum error at {path}:{valid_end}"
                    )
                break
            try:
                json.loads(payload)
            except (ValueError, UnicodeDecodeError) as exc:
                raise JournalCorruptionError(
                    f"invalid JSON record at {path}:{valid_end}"
                ) from exc
            valid_end = end
            count += 1
        lost = size - valid_end
        if lost and repair:
            stream.truncate(valid_end)
            stream.flush()
            os.fsync(stream.fileno())
        return count, lost


class EventJournal:
    """One process owns a session; append raises on any storage failure."""

    def __init__(
        self,
        settings: JournalSettings,
        symbols: Mapping[tuple[str, str], str] | None = None,
    ) -> None:
        self.settings = settings
        self._symbols = dict(symbols or {})
        self._session_dir = settings.root / f"session={settings.session_id}"
        _mkdir_durable(self._session_dir)
        self._lock_fd = os.open(
            self._session_dir / ".lock", os.O_CREAT | os.O_RDWR, 0o600
        )
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self._lock_fd)
            raise
        self._fd: int | None = None
        self._path: Path | None = None
        self._closed = False
        self.appended_records = 0
        self.appended_bytes = 0
        self.fsync_count = 0
        try:
            records = lost = repaired = 0
            for path in sorted(self._session_dir.glob("date=*/events.qj")):
                found, discarded = _scan(path, repair=True)
                records += found
                lost += discarded
                repaired += int(discarded > 0)
            self.recovery = RecoveryStats(records, lost, repaired)
        except BaseException:
            os.close(self._lock_fd)
            raise

    def append(self, event: MarketEvent) -> None:
        if self._closed:
            raise ValueError("journal is closed")
        payload = _payload(
            event,
            self.settings,
            self._symbols.get((event.exchange_segment, event.instrument_id)),
        )
        if len(payload) > _MAX_RECORD_BYTES:
            raise ValueError("journal record exceeds maximum size")
        date = event.receive_timestamp.astimezone(_IST).date().isoformat()
        path = self._session_dir / f"date={date}" / "events.qj"
        record = _HEADER.pack(_MAGIC, len(payload), zlib.crc32(payload)) + payload
        view = memoryview(record)
        try:
            if path != self._path:
                self._switch(path)
            assert self._fd is not None
            while view:
                written = os.write(self._fd, view)
                if written <= 0:
                    raise OSError("journal write made no progress")
                view = view[written:]
            if self.settings.fsync_policy is FsyncPolicy.ALWAYS:
                os.fsync(self._fd)
                self.fsync_count += 1
            self.appended_records += 1
            self.appended_bytes += len(record)
        except BaseException:
            # A partial write is repaired only after restart; do not append again.
            self.close()
            raise

    async def write(self, event: MarketEvent) -> None:
        self.append(event)

    def _switch(self, path: Path) -> None:
        self._close_segment()
        _mkdir_durable(path.parent)
        created = not path.exists()
        fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_RDWR, 0o600)
        self._fd, self._path = fd, path
        if created:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        # Files from earlier dates were recovered at startup. Check any existing
        # current-date file before appending (including a newly switched segment).
        if not created:
            _, discarded = _scan(path, repair=True)
            if discarded:
                self.recovery = RecoveryStats(
                    self.recovery.records,
                    self.recovery.truncated_bytes + discarded,
                    self.recovery.repaired_files + 1,
                )

    def _close_segment(self) -> None:
        if self._fd is not None:
            try:
                if self.settings.fsync_policy is FsyncPolicy.ON_CLOSE:
                    os.fsync(self._fd)
                    self.fsync_count += 1
            finally:
                os.close(self._fd)
                self._fd = None
                self._path = None

    def close(self) -> None:
        if not self._closed:
            try:
                self._close_segment()
            finally:
                self._closed = True
                os.close(self._lock_fd)

    def records(self) -> Iterator[dict[str, Any]]:
        for record in iter_journal_records(self.settings):
            yield record.envelope

    def __enter__(self) -> EventJournal:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
