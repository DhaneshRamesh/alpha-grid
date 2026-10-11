from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quant.domain import DepthEvent, DepthLevel, FeedStatus, Instrument
from quant.health.monitor import (
    FeedHealthMonitor,
    HealthThresholds,
    write_health_status,
)

_AT = datetime(2026, 10, 11, 11, 2, tzinfo=UTC)
_INSTRUMENTS = (
    Instrument("NSE", "NSE_EQ", "2885", "RELIANCE"),
    Instrument("NSE", "NSE_EQ", "1333", "HDFCBANK"),
)


class Clock:
    def __init__(self, at: datetime = _AT) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def advance(self, seconds: float) -> datetime:
        self.at += timedelta(seconds=seconds)
        return self.at


def _event(
    at: datetime,
    sequence: int | None,
    *,
    security_id: str = "2885",
    exchange_timestamp: datetime | None = None,
) -> DepthEvent:
    return DepthEvent(
        "NSE",
        "NSE_EQ",
        security_id,
        exchange_timestamp,
        at,
        (DepthLevel(Decimal("100.05"), 10, 2),),
        (DepthLevel(Decimal("100.10"), 8, 1),),
        sequence,
    )


def _monitor(clock: Clock, **thresholds: object) -> FeedHealthMonitor:
    return FeedHealthMonitor(
        _INSTRUMENTS,
        HealthThresholds(**thresholds),  # type: ignore[arg-type]
        session_id="NSE-2026-10-11",
        clock=clock,
    )


def test_frozen_and_duplicate_feed_logs_precise_gap(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = Clock()
    monitor = _monitor(clock)
    with caplog.at_level(logging.INFO, logger="quant.health.monitor"):
        monitor.connection_changed(True, clock.at)
        monitor.packet_received(("NSE_EQ", "2885"), clock.at)
        monitor.observe_event(_event(clock.at, 1))
        assert monitor.snapshot().instruments[0].status is FeedStatus.HEALTHY
        clock.advance(3)
        assert monitor.snapshot().instruments[0].status is FeedStatus.DEGRADED
        clock.advance(3)
        stale = monitor.snapshot().instruments[0]
        assert stale.status is FeedStatus.STALE
        assert stale.stale_duration_seconds == 1
        monitor.observe_event(_event(clock.at, 1))
        assert monitor.snapshot().instruments[0].status is FeedStatus.STALE
        assert monitor.snapshot().instruments[0].duplicate_events == 1
        clock.at = datetime(2026, 10, 11, 11, 5, tzinfo=UTC)
        monitor.observe_event(_event(clock.at, 2))
        recovered = monitor.snapshot().instruments[0]
        assert recovered.status is FeedStatus.DEGRADED  # duplicate issue hold
        assert recovered.last_unique_event_at == clock.at
    closed = [r.context for r in caplog.records if r.message == "feed_gap_closed"]
    opened = [
        r.context
        for r in caplog.records
        if r.message == "feed_gap_opened" and r.context["symbol"] == "RELIANCE"
    ]
    assert len(closed) == 1
    assert closed[0]["gap_id"] == opened[0]["gap_id"]
    assert closed[0]["symbol"] == "RELIANCE"
    assert closed[0]["gap_start_at"] == _AT.isoformat()
    assert closed[0]["gap_end_at"] == clock.at.isoformat()
    assert closed[0]["gap_duration_seconds"] == 180


def test_delayed_packets_and_exchange_timestamp_age() -> None:
    clock = Clock()
    monitor = _monitor(clock)
    monitor.connection_changed(True, clock.at)
    monitor.observe_event(
        _event(clock.at, 10, exchange_timestamp=clock.at - timedelta(seconds=12))
    )
    health = monitor.snapshot().instruments[0]
    assert health.status is FeedStatus.STALE
    assert health.exchange_lag_seconds == 12
    assert health.exchange_timestamp_age_seconds == 12
    assert health.delayed_events == 1
    clock.advance(1)
    assert monitor.snapshot().instruments[0].exchange_timestamp_age_seconds == 13


def test_receive_and_exchange_timestamp_regressions(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = Clock()
    monitor = _monitor(clock)
    monitor.connection_changed(True, clock.at)
    monitor.observe_event(_event(clock.at, 1, exchange_timestamp=clock.at))
    clock.advance(1)
    regressed = _event(
        clock.at - timedelta(seconds=2),
        2,
        exchange_timestamp=clock.at - timedelta(seconds=2),
    )
    with caplog.at_level(logging.WARNING, logger="quant.health.monitor"):
        monitor.observe_event(regressed)
    health = monitor.snapshot().instruments[0]
    assert health.timestamp_regressions == 2
    assert health.status is FeedStatus.DEGRADED
    fields = [
        r.context["field"]
        for r in caplog.records
        if r.message == "feed_timestamp_regression"
    ]
    assert fields == ["receive_timestamp", "exchange_timestamp"]


def test_sequence_gap_is_counted_and_strict_mode_degrades(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = Clock()
    monitor = _monitor(clock, sequence_gaps_degrade=True)
    monitor.connection_changed(True, clock.at)
    monitor.observe_event(_event(clock.at, 10))
    clock.advance(1)
    with caplog.at_level(logging.INFO, logger="quant.health.monitor"):
        monitor.observe_event(_event(clock.at, 14))
    health = monitor.snapshot().instruments[0]
    assert health.sequence_gap_count == 1
    assert health.missing_sequence_numbers == 3
    assert health.status is FeedStatus.DEGRADED
    gap = next(r.context for r in caplog.records if r.message == "feed_sequence_gap")
    assert gap["previous_sequence"] == 10
    assert gap["current_sequence"] == 14
    assert gap["symbol"] == "RELIANCE"
    assert gap["gap_start_at"] == _AT.isoformat()
    assert gap["gap_end_at"] == clock.at.isoformat()

    observe_only = _monitor(clock)
    observe_only.connection_changed(True, clock.at)
    observe_only.observe_event(_event(clock.at, 10))
    clock.advance(1)
    observe_only.observe_event(_event(clock.at, 14))
    assert observe_only.snapshot().instruments[0].sequence_gap_count == 1
    assert observe_only.snapshot().instruments[0].status is FeedStatus.HEALTHY


def test_same_sequence_with_different_book_is_identity_conflict() -> None:
    clock = Clock()
    monitor = _monitor(clock)
    monitor.connection_changed(True, clock.at)
    original = _event(clock.at, 10)
    monitor.observe_event(original)
    clock.advance(1)
    changed = replace(
        _event(clock.at, 10),
        bid_levels=(DepthLevel(Decimal("100.00"), 10, 2),),
    )
    monitor.observe_event(changed)
    health = monitor.snapshot().instruments[0]
    assert health.identity_conflicts == 1
    assert health.duplicate_events == 0
    assert health.status is FeedStatus.DEGRADED


def test_event_rate_and_duplicate_without_sequence_when_exchange_time_exists() -> None:
    clock = Clock()
    monitor = _monitor(clock, rate_window_seconds=10)
    monitor.connection_changed(True, clock.at)
    monitor.observe_event(_event(clock.at, None, exchange_timestamp=clock.at))
    clock.advance(1)
    monitor.observe_event(_event(clock.at, None, exchange_timestamp=_AT))
    health = monitor.snapshot().instruments[0]
    assert health.events_received == 2
    assert health.unique_events == 1
    assert health.duplicate_events == 1
    assert health.events_per_second == 2
    clock.advance(11)
    assert monitor.snapshot().instruments[0].events_per_second == 0


def test_sequence_gap_baseline_resets_after_unnumbered_event_and_reconnect() -> None:
    clock = Clock()
    monitor = _monitor(clock, sequence_gaps_degrade=True)
    monitor.connection_changed(True, clock.at)
    monitor.observe_event(_event(clock.at, 10))
    clock.advance(1)
    monitor.observe_event(_event(clock.at, None))
    clock.advance(1)
    monitor.observe_event(_event(clock.at, 20))
    assert monitor.snapshot().instruments[0].sequence_gap_count == 0
    monitor.connection_changed(False, clock.at)
    monitor.connection_changed(True, clock.at)
    clock.advance(1)
    monitor.observe_event(_event(clock.at, 1))
    health = monitor.snapshot().instruments[0]
    assert health.sequence_gap_count == 0
    assert health.sequence_regressions == 0
    assert health.reconnects == 1


def test_connection_loss_recovery_missing_and_malformed() -> None:
    clock = Clock()
    monitor = _monitor(clock)
    monitor.connection_changed(True, clock.at)
    monitor.packet_received(("NSE_EQ", "2885"), clock.at)
    monitor.observe_event(_event(clock.at, 1))
    monitor.malformed(("NSE_EQ", "2885"), "bad_depth", clock.at)
    monitor.malformed(None, "short_header", clock.at)
    clock.advance(6)
    snapshot = monitor.snapshot()
    assert snapshot.missing_instruments == ("HDFCBANK",)
    assert snapshot.instruments[0].packets_received == 1
    assert snapshot.instruments[0].malformed_messages == 1
    assert snapshot.unattributed_malformed_messages == 1
    monitor.connection_changed(False, clock.at, "socket_closed")
    assert monitor.snapshot().status is FeedStatus.DISCONNECTED
    clock.advance(1)
    monitor.connection_changed(True, clock.at, "connected")
    assert monitor.snapshot().instruments[0].reconnects == 1
    assert monitor.snapshot().instruments[0].status is FeedStatus.STALE
    monitor.observe_event(_event(clock.at, 2))
    assert monitor.snapshot().instruments[0].connected
    assert monitor.snapshot().instruments[0].reconnects == 1


def test_status_file_and_threshold_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from quant.apps import feed_health

    now = datetime.now(UTC)
    clock = Clock(now)
    monitor = _monitor(clock)
    monitor.connection_changed(True, now)
    monitor.observe_event(_event(now, 1))
    path = tmp_path / "health.json"
    write_health_status(path, monitor.snapshot())
    data = json.loads(path.read_text())
    assert data["instruments"][0]["symbol"] == "RELIANCE"
    assert data["instruments"][0]["status"] == "healthy"
    monkeypatch.setenv("QUANT_HEALTH_STATUS_PATH", str(path))
    monkeypatch.setattr("sys.argv", ["feed_health", "--instrument", "RELIANCE"])
    feed_health.main()
    selected = json.loads(capsys.readouterr().out)
    assert len(selected["instruments"]) == 1
    assert selected["status"] == "healthy"
    assert selected["overall_status"] == "degraded"
    stale = replace(monitor.snapshot(), observed_at=now - timedelta(minutes=5))
    write_health_status(path, stale)
    with pytest.raises(SystemExit) as exc:
        feed_health.main()
    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().out)["status_file_stale"]
    with pytest.raises(ValueError, match="degraded threshold"):
        HealthThresholds(degraded_after_seconds=6, stale_after_seconds=5)
    with pytest.raises(ValueError, match="QUANT_HEALTH"):
        HealthThresholds.from_env({"QUANT_HEALTH_POLL_SECONDS": "bad"})
    assert HealthThresholds.from_env(
        {"QUANT_HEALTH_SEQUENCE_GAPS_DEGRADE": "true"}
    ).sequence_gaps_degrade


def test_collector_updates_status_while_feed_is_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from quant.apps import collect_depth
    from quant.feeds.base import FeedCounters

    class FrozenFeed:
        counters = FeedCounters(False, 1, 0, 2, 1, 0, 0)

        def __init__(self, _settings: object, *, observer: FeedHealthMonitor) -> None:
            self.observer = observer

        def __aiter__(self) -> object:
            return self._events()

        async def _events(self) -> object:
            now = datetime.now(UTC)
            self.observer.connection_changed(True, now)
            yield _event(now, 1)
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            self.observer.connection_changed(False, datetime.now(UTC))

    status_path = tmp_path / "status.json"
    monkeypatch.setattr(collect_depth, "DhanDepthFeed", FrozenFeed)
    monkeypatch.setenv("DHAN_CLIENT_ID", "test")
    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "not-a-real-token")
    monkeypatch.setenv("QUANT_DEPTH_INSTRUMENTS", "NSE_EQ:2885:RELIANCE")
    monkeypatch.setenv("QUANT_JOURNAL_DIR", str(tmp_path / "journal"))
    monkeypatch.setenv("QUANT_HEALTH_STATUS_PATH", str(status_path))
    monkeypatch.setenv("QUANT_HEALTH_DEGRADED_AFTER_SECONDS", "0.01")
    monkeypatch.setenv("QUANT_HEALTH_STALE_AFTER_SECONDS", "0.03")
    monkeypatch.setenv("QUANT_HEALTH_POLL_SECONDS", "0.01")
    monkeypatch.setenv("QUANT_JOURNAL_FSYNC", "never")

    async def run() -> dict[str, object]:
        task = asyncio.create_task(collect_depth.collect())
        try:
            for _ in range(100):
                await asyncio.sleep(0.01)
                if status_path.exists():
                    data: dict[str, object] = json.loads(status_path.read_text())
                    if data["status"] == "stale":
                        return data
            raise AssertionError("health status did not become stale")
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    data = asyncio.run(run())
    instruments = data["instruments"]
    assert isinstance(instruments, list)
    assert instruments[0]["symbol"] == "RELIANCE"
    assert instruments[0]["status"] == "stale"
