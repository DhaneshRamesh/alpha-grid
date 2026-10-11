from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from quant.domain import DepthEvent, DepthLevel, TradeEvent
from quant.storage.journal import (
    EventJournal,
    FsyncPolicy,
    JournalCorruptionError,
    JournalSettings,
)


def _depth(security_id: str = "1333", at: datetime | None = None) -> DepthEvent:
    return DepthEvent(
        "NSE",
        "NSE_EQ",
        security_id,
        None,
        at or datetime(2026, 10, 11, 3, 45, 0, 123456, UTC),
        (DepthLevel(Decimal("100.0500"), 10, 2),),
        (DepthLevel(Decimal("100.10"), 8, 1),),
        42,
    )


def _settings(root: Path, policy: FsyncPolicy = FsyncPolicy.ALWAYS) -> JournalSettings:
    return JournalSettings(root, "NSE-2026-10-11", policy, "0.3.0")


def test_append_read_round_trip_and_metadata(tmp_path: Path) -> None:
    with EventJournal(_settings(tmp_path), {("NSE_EQ", "1333"): "HDFCBANK"}) as journal:
        journal.append(_depth())
        journal.append(
            TradeEvent(
                "NSE",
                "NSE_EQ",
                "1333",
                None,
                datetime(2026, 10, 11, 3, 45, 1, tzinfo=UTC),
                Decimal("100.1250"),
                3,
                "t1",
                43,
            )
        )
        records = list(journal.records())
    assert [row["event_type"] for row in records] == ["depth", "trade"]
    assert records[0]["collector_version"] == "0.3.0"
    assert records[0]["session_id"] == "NSE-2026-10-11"
    assert records[0]["symbol"] == "HDFCBANK"
    assert records[0]["security_id"] == "1333"
    assert records[0]["receive_timestamp"] == "2026-10-11T03:45:00.123456+00:00"
    assert records[0]["event"]["bid_levels"] == [["100.0500", 10, 2]]
    assert records[1]["event"]["price"] == "100.1250"
    assert records[1]["event"]["trade_id"] == "t1"


def test_restart_recovery_and_append(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with EventJournal(settings) as journal:
        journal.append(_depth())
    with EventJournal(settings) as journal:
        assert journal.recovery.records == 1
        assert journal.recovery.truncated_bytes == 0
        journal.append(_depth("2885"))
        assert [row["security_id"] for row in journal.records()] == ["1333", "2885"]


def test_truncated_final_record_is_repaired_without_losing_prior_events(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    with EventJournal(settings) as journal:
        journal.append(_depth())
        journal.append(_depth("2885"))
    path = tmp_path / "session=NSE-2026-10-11/date=2026-10-11/events.qj"
    with path.open("r+b") as stream:
        stream.truncate(path.stat().st_size - 7)
    with EventJournal(settings) as journal:
        assert journal.recovery.records == 1
        assert journal.recovery.truncated_bytes > 0
        assert journal.recovery.repaired_files == 1
        journal.append(_depth("3045"))
        assert [row["security_id"] for row in journal.records()] == ["1333", "3045"]


def test_corrupted_final_checksum_recovers_but_middle_corruption_fails(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    with EventJournal(settings) as journal:
        journal.append(_depth())
        journal.append(_depth("2885"))
    path = tmp_path / "session=NSE-2026-10-11/date=2026-10-11/events.qj"
    data = bytearray(path.read_bytes())
    last_start = data.find(b"QJ01", 4)
    data[last_start + 8] ^= 0xFF
    path.write_bytes(data)
    with EventJournal(settings) as journal:
        assert journal.recovery.records == 1
        assert len(list(journal.records())) == 1
    with EventJournal(settings) as journal:
        journal.append(_depth("2885"))
    data = bytearray(path.read_bytes())
    data[8] ^= 0xFF
    path.write_bytes(data)
    with pytest.raises(JournalCorruptionError, match="non-tail"):
        EventJournal(settings)


def test_multiple_instruments_and_ist_date_partitions(tmp_path: Path) -> None:
    with EventJournal(_settings(tmp_path)) as journal:
        journal.append(_depth("1333", datetime(2026, 10, 10, 18, 29, tzinfo=UTC)))
        journal.append(_depth("2885", datetime(2026, 10, 10, 18, 30, tzinfo=UTC)))
        assert [row["security_id"] for row in journal.records()] == ["1333", "2885"]
    assert len(list(tmp_path.glob("session=*/date=*/events.qj"))) == 2


def test_deterministic_serialization_and_nanosecond_text(tmp_path: Path) -> None:
    class NanosecondDatetime(datetime):
        def isoformat(self, sep: str = "T", timespec: str = "auto") -> str:
            return super().isoformat(sep, timespec).replace("+00:00", "789+00:00")

    instant = NanosecondDatetime(2026, 10, 11, 3, 45, 0, 123456, UTC)
    event = _depth(at=instant)
    with EventJournal(_settings(tmp_path / "a")) as journal:
        journal.append(event)
    with EventJournal(_settings(tmp_path / "b")) as journal:
        journal.append(event)
    first = next((tmp_path / "a").glob("session=*/date=*/events.qj"))
    second = next((tmp_path / "b").glob("session=*/date=*/events.qj"))
    assert first.read_bytes() == second.read_bytes()
    with EventJournal(_settings(tmp_path / "a")) as journal:
        row = next(journal.records())
    assert row["receive_timestamp"] == "2026-10-11T03:45:00.123456789+00:00"
    assert (
        json.loads(first.read_bytes()[12:])["event"]["receive_timestamp"]
        == row["receive_timestamp"]
    )


def test_closed_journal_rejects_writes_and_session_lock(tmp_path: Path) -> None:
    settings = _settings(tmp_path, FsyncPolicy.ON_CLOSE)
    journal = EventJournal(settings)
    with pytest.raises(BlockingIOError):
        EventJournal(settings)
    journal.close()
    with pytest.raises(ValueError, match="closed"):
        journal.append(_depth())
    with EventJournal(settings) as reopened:
        reopened.append(_depth())


def test_fsync_policy_counts_and_env_validation(tmp_path: Path) -> None:
    always = EventJournal(_settings(tmp_path / "always"))
    always.append(_depth())
    always.append(_depth())
    assert always.fsync_count == 2
    always.close()

    on_close = EventJournal(_settings(tmp_path / "close", FsyncPolicy.ON_CLOSE))
    on_close.append(_depth())
    assert on_close.fsync_count == 0
    on_close.close()
    assert on_close.fsync_count == 1

    never = EventJournal(_settings(tmp_path / "never", FsyncPolicy.NEVER))
    never.append(_depth())
    never.close()
    assert never.fsync_count == 0

    with pytest.raises(ValueError, match="QUANT_JOURNAL_FSYNC"):
        JournalSettings.from_env({"QUANT_JOURNAL_FSYNC": "sometimes"})
    with pytest.raises(ValueError, match="session_id"):
        JournalSettings(tmp_path, "../escape")


def test_collector_journals_before_downstream_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import asyncio

    from quant.apps import collect_depth
    from quant.feeds.base import FeedCounters

    class FakeFeed:
        counters = FeedCounters(False, 0, 0, 0, 0, 0, 0)

        def __init__(self, _settings: object) -> None:
            pass

        def __aiter__(self) -> FakeFeed:
            return self

        async def __anext__(self) -> DepthEvent:
            if getattr(self, "done", False):
                raise StopAsyncIteration
            self.done = True
            return _depth()

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(collect_depth, "DhanDepthFeed", FakeFeed)
    monkeypatch.setenv("DHAN_CLIENT_ID", "test")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "not-a-real-token")
    monkeypatch.setenv("QUANT_DEPTH_INSTRUMENTS", "NSE_EQ:1333:HDFCBANK")
    monkeypatch.setenv("QUANT_JOURNAL_DIR", str(tmp_path))
    monkeypatch.setenv("QUANT_JOURNAL_SESSION_ID", "NSE-2026-10-11")
    asyncio.run(collect_depth.collect())
    assert len(capsys.readouterr().out.splitlines()) == 1
    with EventJournal(_settings(tmp_path)) as journal:
        assert next(journal.records())["symbol"] == "HDFCBANK"

    def fail_append(self: EventJournal, _event: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(EventJournal, "append", fail_append)
    with pytest.raises(OSError, match="disk full"):
        asyncio.run(collect_depth.collect())
    assert capsys.readouterr().out == ""
