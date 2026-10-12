"""Offline full-depth ingestion, durability, and fail-closed tests."""

from __future__ import annotations

import errno
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from infra.benchmark.ingestion_load import LoadSettings, _event, run_case

from quant.storage.journal import EventJournal, FsyncPolicy, JournalSettings


def test_full_depth_order_and_durability_strategies(tmp_path: Path) -> None:
    for policy in ("every_event", "periodic", "group_bytes"):
        result = run_case(
            tmp_path,
            LoadSettings(100, 0.08, policy, queue_limit=100, group_bytes=1_000),
        )
        assert result["status"] == "complete"
        assert result["accepted_in_memory"] == result["durable_events"]
        assert result["persisted_to_kernel"] == result["durable_events"]
        assert result["recovery_records"] == result["durable_events"]
        assert result["recovery_truncated_bytes"] == 0
        assert result["dropped_events"] == 0
        assert result["event_to_durable_ms"]["count"] > 0
        assert result["fsync_ms"]["count"] == result["fsync_count"]
        assert result["disk_bytes_per_million_events"] > 0
        settings = JournalSettings(tmp_path, result["session_id"], FsyncPolicy.NEVER)
        with EventJournal(settings) as journal:
            records = list(journal.records())
        assert len(records) == result["durable_events"]
        assert [row["event"]["sequence_number"] for row in records] == [1] * len(
            records
        )
        assert all(
            len(row["event"]["bid_levels"]) == 20
            and len(row["event"]["ask_levels"]) == 20
            for row in records
        )


def test_synthetic_sequence_is_per_instrument() -> None:
    events = [_event(index, 2) for index in range(6)]
    assert [(event.instrument_id, event.sequence_number) for event in events] == [
        ("10000", 1),
        ("10001", 1),
        ("10000", 2),
        ("10001", 2),
        ("10000", 3),
        ("10001", 3),
    ]


@pytest.mark.parametrize("code", [errno.EIO, errno.ENOSPC])
def test_disk_failure_is_visible_and_not_counted_durable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    def fail(_journal: EventJournal, _event: object) -> None:
        raise OSError(code, "injected disk failure")

    monkeypatch.setattr(EventJournal, "append", fail)
    result = run_case(tmp_path, LoadSettings(500, 0.02, "every_event"))
    assert result["status"] == "failed"
    assert f"OSError:{code}" in result["failure_types"]
    assert result["durable_events"] == 0
    assert result["accepted_but_not_durable"] > 0


def test_slow_fsync_and_bounded_queue_fail_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_sync = EventJournal.sync

    def slow_sync(journal: EventJournal) -> int:
        time.sleep(0.02)
        return real_sync(journal)

    monkeypatch.setattr(EventJournal, "sync", slow_sync)
    result = run_case(
        tmp_path, LoadSettings(10_000, 0.05, "every_event", queue_limit=2)
    )
    assert result["status"] == "failed"
    assert "QueueFull" in result["failure_types"]
    assert result["rejected_events"] == result["dropped_events"] == 1
    assert result["max_queue_depth"] <= 2


def test_abrupt_process_exit_recovers_complete_prefix(tmp_path: Path) -> None:
    script = """
import os, sys
from pathlib import Path
from infra.benchmark.ingestion_load import _event
from quant.storage.journal import EventJournal, FsyncPolicy, JournalSettings
s = JournalSettings(Path(sys.argv[1]), 'abrupt-test', FsyncPolicy.ALWAYS)
with EventJournal(s) as j:
    j.append(_event(0, 2))
    j.append(_event(1, 2))
    p = next(Path(sys.argv[1]).glob('session=*/date=*/events.qj'))
    with p.open('ab') as f:
        f.write(b'QJ01\\x00\\x00')
        f.flush()
    os._exit(17)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], check=False
    )
    assert completed.returncode == 17
    with EventJournal(JournalSettings(tmp_path, "abrupt-test")) as recovered:
        assert recovered.recovery.records == 2
        assert recovered.recovery.truncated_bytes == 6
        assert [row["security_id"] for row in recovered.records()] == ["10000", "10001"]


def test_journal_sync_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = EventJournal(JournalSettings(tmp_path, "sync-failure", FsyncPolicy.NEVER))
    journal.append(_event(0, 1))

    def fail_fsync(_descriptor: int) -> None:
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr("quant.storage.journal.os.fsync", fail_fsync)
    with pytest.raises(OSError, match="disk full"):
        journal.sync()
    with pytest.raises(ValueError, match="closed"):
        journal.append(_event(1, 1))
    # The kernel may have persisted the record, but it was never acknowledged.
    assert journal.written_records == 1
    assert journal.fsync_count == 0


def test_group_commit_sync_failure_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_sync(_journal: EventJournal) -> int:
        raise OSError(errno.EIO, "injected sync failure")

    monkeypatch.setattr(EventJournal, "sync", fail_sync)
    result = run_case(
        tmp_path,
        LoadSettings(100, 0.02, "group_bytes", group_bytes=1),
    )
    assert result["status"] == "failed"
    assert f"OSError:{errno.EIO}" in result["failure_types"]
    assert result["durable_events"] == 0
    assert result["accepted_but_not_durable"] > 0


def test_cli_keeps_raw_trials_separate_from_summary(tmp_path: Path) -> None:
    output = tmp_path / "results.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "infra.benchmark.ingestion_load",
            "--root",
            str(tmp_path / "journal"),
            "--output",
            str(output),
            "--rates",
            "100",
            "--duration-seconds",
            "0.02",
            "--repetitions",
            "1",
            "--pipeline-mode",
            "journal_only",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(output.read_text())
    trials = [
        json.loads(line)
        for line in output.with_suffix(".json.trials.jsonl").read_text().splitlines()
    ]
    assert len(trials) == len(summary["results"]) == 3
    assert all("raw_event_to_durable_ms" in row for row in trials)
    assert all("raw_event_to_durable_ms" not in row for row in summary["results"])
