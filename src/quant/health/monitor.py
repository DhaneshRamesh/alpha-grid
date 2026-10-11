"""Per-instrument market-data quality tracking and structured incident logs."""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quant.domain import (
    DepthEvent,
    FeedStatus,
    Instrument,
    MarketEvent,
    TradeEvent,
    _aware,
)

_LOGGER = logging.getLogger(__name__)
InstrumentKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class HealthThresholds:
    degraded_after_seconds: float = 2.0
    stale_after_seconds: float = 5.0
    missing_after_seconds: float = 5.0
    max_exchange_lag_seconds: float = 2.0
    stale_exchange_lag_seconds: float = 10.0
    rate_window_seconds: float = 60.0
    min_events_per_second: float = 0.0
    issue_hold_seconds: float = 300.0
    timestamp_regression_tolerance_seconds: float = 0.0
    future_timestamp_tolerance_seconds: float = 1.0
    poll_seconds: float = 1.0
    status_max_age_seconds: float = 3.0
    duplicate_history_size: int = 256
    sequence_gaps_degrade: bool = False

    def __post_init__(self) -> None:
        positive = (
            self.degraded_after_seconds,
            self.stale_after_seconds,
            self.missing_after_seconds,
            self.max_exchange_lag_seconds,
            self.stale_exchange_lag_seconds,
            self.rate_window_seconds,
            self.poll_seconds,
            self.status_max_age_seconds,
        )
        if any(not math.isfinite(value) or value <= 0 for value in positive):
            raise ValueError("health timing thresholds must be finite and positive")
        if self.degraded_after_seconds >= self.stale_after_seconds:
            raise ValueError("degraded threshold must precede stale threshold")
        if self.max_exchange_lag_seconds >= self.stale_exchange_lag_seconds:
            raise ValueError("exchange lag warning must precede stale threshold")
        if self.status_max_age_seconds <= self.poll_seconds:
            raise ValueError("status max age must exceed poll interval")
        nonnegative = (
            self.min_events_per_second,
            self.timestamp_regression_tolerance_seconds,
            self.future_timestamp_tolerance_seconds,
            self.issue_hold_seconds,
        )
        if any(not math.isfinite(value) or value < 0 for value in nonnegative):
            raise ValueError("health nonnegative thresholds are invalid")
        if self.duplicate_history_size < 1:
            raise ValueError("duplicate_history_size must be positive")

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> HealthThresholds:
        names = {
            "degraded_after_seconds": "QUANT_HEALTH_DEGRADED_AFTER_SECONDS",
            "stale_after_seconds": "QUANT_HEALTH_STALE_AFTER_SECONDS",
            "missing_after_seconds": "QUANT_HEALTH_MISSING_AFTER_SECONDS",
            "max_exchange_lag_seconds": "QUANT_HEALTH_MAX_EXCHANGE_LAG_SECONDS",
            "stale_exchange_lag_seconds": "QUANT_HEALTH_STALE_EXCHANGE_LAG_SECONDS",
            "rate_window_seconds": "QUANT_HEALTH_RATE_WINDOW_SECONDS",
            "min_events_per_second": "QUANT_HEALTH_MIN_EVENTS_PER_SECOND",
            "issue_hold_seconds": "QUANT_HEALTH_ISSUE_HOLD_SECONDS",
            "timestamp_regression_tolerance_seconds": (
                "QUANT_HEALTH_TIMESTAMP_REGRESSION_TOLERANCE_SECONDS"
            ),
            "future_timestamp_tolerance_seconds": (
                "QUANT_HEALTH_FUTURE_TIMESTAMP_TOLERANCE_SECONDS"
            ),
            "poll_seconds": "QUANT_HEALTH_POLL_SECONDS",
            "status_max_age_seconds": "QUANT_HEALTH_STATUS_MAX_AGE_SECONDS",
            "duplicate_history_size": "QUANT_HEALTH_DUPLICATE_HISTORY_SIZE",
            "sequence_gaps_degrade": "QUANT_HEALTH_SEQUENCE_GAPS_DEGRADE",
        }
        defaults = cls()
        values: dict[str, Any] = {}
        try:
            for field_name, env_name in names.items():
                default = getattr(defaults, field_name)
                raw = environ.get(env_name, str(default))
                if isinstance(default, bool):
                    if raw.lower() not in {"true", "false", "1", "0"}:
                        raise ValueError("invalid boolean health setting")
                    values[field_name] = raw.lower() in {"true", "1"}
                else:
                    values[field_name] = (
                        int(raw) if isinstance(default, int) else float(raw)
                    )
        except ValueError as exc:
            raise ValueError("invalid QUANT_HEALTH threshold") from exc
        return cls(**values)


@dataclass(frozen=True, slots=True)
class InstrumentHealth:
    venue: str
    exchange_segment: str
    instrument_id: str
    symbol: str
    status: FeedStatus
    connected: bool
    missing: bool
    last_packet_receive_at: datetime | None
    last_receive_at: datetime | None
    last_unique_event_at: datetime | None
    last_exchange_timestamp: datetime | None
    exchange_timestamp_age_seconds: float | None
    exchange_lag_seconds: float | None
    packets_received: int
    events_received: int
    unique_events: int
    events_per_second: float
    sequence_gap_count: int
    missing_sequence_numbers: int
    sequence_regressions: int
    malformed_messages: int
    reconnects: int
    duplicate_events: int
    identity_conflicts: int
    timestamp_regressions: int
    delayed_events: int
    stale_duration_seconds: float
    last_sequence_number: int | None
    gap_opened_at: datetime | None


@dataclass(frozen=True, slots=True)
class FeedHealthSnapshot:
    observed_at: datetime
    status: FeedStatus
    connected: bool
    instruments: tuple[InstrumentHealth, ...]
    unattributed_malformed_messages: int
    missing_instruments: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class _InstrumentState:
    instrument: Instrument
    status: FeedStatus = FeedStatus.DISCONNECTED
    connected: bool = False
    connected_at: datetime | None = None
    seen_since_connect: bool = False
    missing: bool = False
    last_packet_receive_at: datetime | None = None
    last_receive_at: datetime | None = None
    last_unique_event_at: datetime | None = None
    last_exchange_timestamp: datetime | None = None
    last_exchange_lag_seconds: float | None = None
    packets_received: int = 0
    events_received: int = 0
    unique_events: int = 0
    sequence_gap_count: int = 0
    missing_sequence_numbers: int = 0
    sequence_regressions: int = 0
    malformed_messages: int = 0
    reconnects: int = 0
    duplicate_events: int = 0
    identity_conflicts: int = 0
    timestamp_regressions: int = 0
    delayed_events: int = 0
    last_sequence_number: int | None = None
    last_issue_at: datetime | None = None
    gap_opened_at: datetime | None = None
    recent_events: deque[datetime] = field(default_factory=deque)
    recent_identities: OrderedDict[tuple[str, str, object], object] = field(
        default_factory=OrderedDict
    )


class FeedHealthMonitor:
    """Collect quality metrics; emit incidents only on changes or violations."""

    def __init__(
        self,
        instruments: tuple[Instrument, ...],
        thresholds: HealthThresholds,
        *,
        session_id: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not instruments:
            raise ValueError("health monitor requires configured instruments")
        self.thresholds = thresholds
        self.session_id = session_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._states: dict[InstrumentKey, _InstrumentState] = {}
        for instrument in instruments:
            key = (instrument.exchange_segment, instrument.instrument_id)
            if key in self._states:
                raise ValueError("health monitor instruments must be unique")
            self._states[key] = _InstrumentState(instrument)
        self.connected = False
        self._ever_connected = False
        self.unattributed_malformed_messages = 0
        self._last_unattributed_issue_at: datetime | None = None

    def _now(self, at: datetime | None = None) -> datetime:
        value = at if at is not None else self._clock()
        _aware(value, "health observation time")
        return value

    def connection_changed(
        self, connected: bool, at: datetime | None = None, reason: str = "transport"
    ) -> None:
        now = self._now(at)
        if self.connected == connected:
            return
        reconnect = connected and self._ever_connected
        self.connected = connected
        self._ever_connected |= connected
        for state in self._states.values():
            state.connected = connected
            if connected:
                state.connected_at = now
                state.seen_since_connect = False
                state.missing = False
                state.last_sequence_number = None
                state.recent_identities.clear()
                if reconnect:
                    state.reconnects += 1
            self._refresh(state, now)
        _LOGGER.log(
            logging.INFO if connected else logging.WARNING,
            "feed_connection_changed",
            extra={
                "context": {
                    "session_id": self.session_id,
                    "connected": connected,
                    "reconnect": reconnect,
                    "reason": reason,
                    "observed_at": now.isoformat(),
                }
            },
        )

    def packet_received(self, key: InstrumentKey, at: datetime) -> None:
        now = self._now(at)
        state = self._states.get(key)
        if state is not None:
            state.packets_received += 1
            state.last_packet_receive_at = now

    def malformed(
        self, key: InstrumentKey | None, reason: str, at: datetime | None = None
    ) -> None:
        now = self._now(at)
        state = self._states.get(key) if key is not None else None
        if state is None:
            self.unattributed_malformed_messages += 1
            self._last_unattributed_issue_at = now
        else:
            state.malformed_messages += 1
            state.last_issue_at = now
            self._refresh(state, now)
        context = (
            self._context(state, now)
            if state is not None
            else {"session_id": self.session_id, "observed_at": now.isoformat()}
        )
        _LOGGER.warning(
            "feed_malformed_message",
            extra={"context": {**context, "reason": reason}},
        )

    def observe_event(self, event: MarketEvent, at: datetime | None = None) -> None:
        now = self._now(at)
        key = (event.exchange_segment, event.instrument_id)
        state = self._states.get(key)
        if state is None:
            raise ValueError("event instrument is not configured")
        state.events_received += 1
        state.seen_since_connect = True
        state.recent_events.append(now)
        self._prune_rate(state, now)
        identity = self._identity(event)
        duplicate = False
        if identity is not None:
            payload = self._content(event)
            previous = state.recent_identities.get(identity)
            if previous == payload:
                duplicate = True
                state.duplicate_events += 1
                self._issue(
                    state,
                    now,
                    "feed_duplicate_event",
                    {
                        "sequence_number": event.sequence_number,
                        "exchange_timestamp": (
                            event.exchange_timestamp.isoformat()
                            if event.exchange_timestamp is not None
                            else None
                        ),
                    },
                )
            else:
                if previous is not None and identity[1] == "sequence":
                    state.identity_conflicts += 1
                    self._issue(
                        state,
                        now,
                        "feed_sequence_identity_conflict",
                        {"sequence_number": event.sequence_number},
                    )
                state.recent_identities[identity] = payload
                state.recent_identities.move_to_end(identity)
                if (
                    len(state.recent_identities)
                    > self.thresholds.duplicate_history_size
                ):
                    state.recent_identities.popitem(last=False)
        previous_receive = state.last_receive_at
        if (
            previous_receive is not None
            and (previous_receive - event.receive_timestamp).total_seconds()
            > self.thresholds.timestamp_regression_tolerance_seconds
        ):
            state.timestamp_regressions += 1
            self._issue(
                state,
                now,
                "feed_timestamp_regression",
                {
                    "field": "receive_timestamp",
                    "previous": previous_receive.isoformat(),
                    "current": event.receive_timestamp.isoformat(),
                },
            )
        if (
            state.last_exchange_timestamp is not None
            and event.exchange_timestamp is not None
        ):
            if (
                state.last_exchange_timestamp - event.exchange_timestamp
            ).total_seconds() > self.thresholds.timestamp_regression_tolerance_seconds:
                state.timestamp_regressions += 1
                self._issue(
                    state,
                    now,
                    "feed_timestamp_regression",
                    {
                        "field": "exchange_timestamp",
                        "previous": state.last_exchange_timestamp.isoformat(),
                        "current": event.exchange_timestamp.isoformat(),
                    },
                )
        previous_sequence = state.last_sequence_number
        if event.sequence_number is not None and previous_sequence is not None:
            if event.sequence_number > previous_sequence + 1:
                missing = event.sequence_number - previous_sequence - 1
                state.sequence_gap_count += 1
                state.missing_sequence_numbers += missing
                self._issue(
                    state,
                    now,
                    "feed_sequence_gap",
                    {
                        "previous_sequence": previous_sequence,
                        "current_sequence": event.sequence_number,
                        "missing_sequence_numbers": missing,
                        "gap_start_at": (
                            state.last_unique_event_at.isoformat()
                            if state.last_unique_event_at is not None
                            else None
                        ),
                        "gap_end_at": event.receive_timestamp.isoformat(),
                        "treated_as_quality_issue": (
                            self.thresholds.sequence_gaps_degrade
                        ),
                    },
                    degrade=self.thresholds.sequence_gaps_degrade,
                )
            elif event.sequence_number < previous_sequence:
                state.sequence_regressions += 1
                self._issue(
                    state,
                    now,
                    "feed_sequence_regression",
                    {
                        "previous_sequence": previous_sequence,
                        "current_sequence": event.sequence_number,
                    },
                )
        if event.exchange_timestamp is not None:
            lag = (event.receive_timestamp - event.exchange_timestamp).total_seconds()
            state.last_exchange_lag_seconds = lag
            if lag > self.thresholds.max_exchange_lag_seconds:
                state.delayed_events += 1
                self._issue(
                    state,
                    now,
                    "feed_delayed_event",
                    {
                        "exchange_lag_seconds": lag,
                        "exchange_timestamp": event.exchange_timestamp.isoformat(),
                        "receive_timestamp": event.receive_timestamp.isoformat(),
                    },
                )
            elif lag < -self.thresholds.future_timestamp_tolerance_seconds:
                state.timestamp_regressions += 1
                self._issue(
                    state,
                    now,
                    "feed_future_exchange_timestamp",
                    {
                        "exchange_lag_seconds": lag,
                    },
                )
            if (
                state.last_exchange_timestamp is None
                or event.exchange_timestamp > state.last_exchange_timestamp
            ):
                state.last_exchange_timestamp = event.exchange_timestamp
        else:
            state.last_exchange_lag_seconds = None
        if (
            state.last_receive_at is None
            or event.receive_timestamp > state.last_receive_at
        ):
            state.last_receive_at = event.receive_timestamp
        if event.sequence_number is None:
            state.last_sequence_number = None
        elif (
            state.last_sequence_number is None
            or event.sequence_number > state.last_sequence_number
        ):
            state.last_sequence_number = event.sequence_number
        if not duplicate:
            state.unique_events += 1
            state.last_unique_event_at = event.receive_timestamp
        self._refresh(state, now)
        if (
            not duplicate
            and state.gap_opened_at is not None
            and state.status in (FeedStatus.HEALTHY, FeedStatus.DEGRADED)
        ):
            self._close_gap(state, event.receive_timestamp, now)

    def snapshot(self, at: datetime | None = None) -> FeedHealthSnapshot:
        now = self._now(at)
        instruments = tuple(
            self._snapshot_state(state, now) for state in self._states.values()
        )
        rank = {
            FeedStatus.HEALTHY: 0,
            FeedStatus.DEGRADED: 1,
            FeedStatus.STALE: 2,
            FeedStatus.DISCONNECTED: 3,
        }
        overall = max((item.status for item in instruments), key=rank.__getitem__)
        if (
            self._last_unattributed_issue_at is not None
            and (now - self._last_unattributed_issue_at).total_seconds()
            <= self.thresholds.issue_hold_seconds
            and overall is FeedStatus.HEALTHY
        ):
            overall = FeedStatus.DEGRADED
        return FeedHealthSnapshot(
            now,
            overall,
            self.connected,
            instruments,
            self.unattributed_malformed_messages,
            tuple(item.symbol for item in instruments if item.missing),
        )

    def _snapshot_state(
        self, state: _InstrumentState, now: datetime
    ) -> InstrumentHealth:
        self._prune_rate(state, now)
        self._refresh(state, now)
        connected_at = state.connected_at
        elapsed = (
            (now - connected_at).total_seconds() if connected_at is not None else 0.0
        )
        window = min(self.thresholds.rate_window_seconds, max(elapsed, 1.0))
        age = (
            max(0.0, (now - state.last_exchange_timestamp).total_seconds())
            if state.last_exchange_timestamp is not None
            else None
        )
        freshness_at = state.last_unique_event_at or connected_at
        stale_duration = (
            max(
                0.0,
                (now - freshness_at).total_seconds()
                - self.thresholds.stale_after_seconds,
            )
            if freshness_at is not None
            else 0.0
        )
        return InstrumentHealth(
            state.instrument.venue,
            state.instrument.exchange_segment,
            state.instrument.instrument_id,
            state.instrument.symbol,
            state.status,
            state.connected,
            state.missing,
            state.last_packet_receive_at,
            state.last_receive_at,
            state.last_unique_event_at,
            state.last_exchange_timestamp,
            age,
            state.last_exchange_lag_seconds,
            state.packets_received,
            state.events_received,
            state.unique_events,
            len(state.recent_events) / window,
            state.sequence_gap_count,
            state.missing_sequence_numbers,
            state.sequence_regressions,
            state.malformed_messages,
            state.reconnects,
            state.duplicate_events,
            state.identity_conflicts,
            state.timestamp_regressions,
            state.delayed_events,
            stale_duration,
            state.last_sequence_number,
            state.gap_opened_at,
        )

    def _prune_rate(self, state: _InstrumentState, now: datetime) -> None:
        while (
            state.recent_events
            and (now - state.recent_events[0]).total_seconds()
            > self.thresholds.rate_window_seconds
        ):
            state.recent_events.popleft()

    def _refresh(self, state: _InstrumentState, now: datetime) -> None:
        previous = state.status
        previous_missing = state.missing
        connected_at = state.connected_at
        state.missing = bool(
            state.connected
            and not state.seen_since_connect
            and connected_at is not None
            and (now - connected_at).total_seconds()
            >= self.thresholds.missing_after_seconds
        )
        if state.missing and not previous_missing:
            _LOGGER.warning(
                "feed_instrument_missing", extra={"context": self._context(state, now)}
            )
        if previous_missing and not state.missing:
            _LOGGER.info(
                "feed_instrument_restored", extra={"context": self._context(state, now)}
            )
        freshness_at = state.last_unique_event_at or connected_at
        age = (now - freshness_at).total_seconds() if freshness_at else None
        if not state.connected:
            current = FeedStatus.DISCONNECTED
        elif state.missing or (
            age is not None and age >= self.thresholds.stale_after_seconds
        ):
            current = FeedStatus.STALE
        elif (
            state.last_exchange_lag_seconds is not None
            and state.last_exchange_lag_seconds
            >= self.thresholds.stale_exchange_lag_seconds
        ):
            current = FeedStatus.STALE
        elif (
            age is None
            or age >= self.thresholds.degraded_after_seconds
            or state.last_unique_event_at is None
            or not state.seen_since_connect
            or (
                state.last_issue_at is not None
                and (now - state.last_issue_at).total_seconds()
                <= self.thresholds.issue_hold_seconds
            )
            or (
                connected_at is not None
                and (now - connected_at).total_seconds()
                >= self.thresholds.rate_window_seconds
                and len(state.recent_events) / self.thresholds.rate_window_seconds
                < self.thresholds.min_events_per_second
            )
        ):
            current = FeedStatus.DEGRADED
        else:
            current = FeedStatus.HEALTHY
        state.status = current
        if (
            current in (FeedStatus.STALE, FeedStatus.DISCONNECTED)
            and state.gap_opened_at is None
        ):
            state.gap_opened_at = state.last_unique_event_at or connected_at or now
            _LOGGER.warning(
                "feed_gap_opened",
                extra={
                    "context": {
                        **self._context(state, now),
                        "gap_start_at": state.gap_opened_at.isoformat(),
                        "gap_id": self._gap_id(state, state.gap_opened_at),
                    }
                },
            )
        if current != previous:
            _LOGGER.log(
                logging.INFO if current is FeedStatus.HEALTHY else logging.WARNING,
                "feed_health_state_changed",
                extra={
                    "context": {
                        **self._context(state, now),
                        "previous_status": previous.value,
                        "status": current.value,
                    }
                },
            )

    def _close_gap(
        self, state: _InstrumentState, end_at: datetime, observed_at: datetime
    ) -> None:
        start = state.gap_opened_at
        if start is None:
            return
        _LOGGER.warning(
            "feed_gap_closed",
            extra={
                "context": {
                    **self._context(state, observed_at),
                    "gap_start_at": start.isoformat(),
                    "gap_end_at": end_at.isoformat(),
                    "gap_duration_seconds": max(0.0, (end_at - start).total_seconds()),
                    "gap_id": self._gap_id(state, start),
                }
            },
        )
        state.gap_opened_at = None

    def _issue(
        self,
        state: _InstrumentState,
        now: datetime,
        event: str,
        detail: dict[str, Any],
        *,
        degrade: bool = True,
    ) -> None:
        if degrade:
            state.last_issue_at = now
        _LOGGER.log(
            logging.WARNING if degrade else logging.INFO,
            event,
            extra={"context": {**self._context(state, now), **detail}},
        )

    def _context(self, state: _InstrumentState, now: datetime) -> dict[str, Any]:
        instrument = state.instrument
        return {
            "session_id": self.session_id,
            "venue": instrument.venue,
            "exchange_segment": instrument.exchange_segment,
            "instrument_id": instrument.instrument_id,
            "symbol": instrument.symbol,
            "observed_at": now.isoformat(),
        }

    def _gap_id(self, state: _InstrumentState, start: datetime) -> str:
        return (
            f"{self.session_id}:{state.instrument.exchange_segment}:"
            f"{state.instrument.instrument_id}:{start.isoformat()}"
        )

    @staticmethod
    def _identity(event: MarketEvent) -> tuple[str, str, object] | None:
        kind = "depth" if isinstance(event, DepthEvent) else "trade"
        if event.sequence_number is not None:
            return kind, "sequence", event.sequence_number
        if event.exchange_timestamp is not None:
            return kind, "exchange_timestamp", event.exchange_timestamp
        return None

    @staticmethod
    def _content(event: MarketEvent) -> object:
        if isinstance(event, DepthEvent):
            return event.bid_levels, event.ask_levels
        assert isinstance(event, TradeEvent)
        return event.price, event.quantity, event.trade_id


def write_health_status(path: Path, snapshot: FeedHealthSnapshot) -> None:
    """Atomically publish a CLI-readable status document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(snapshot.to_dict(), default=_json_value, sort_keys=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _json_value(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, FeedStatus):
        return value.value
    raise TypeError(f"unsupported health JSON value: {type(value).__name__}")
