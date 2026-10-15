"""Disabled future live-capture profile and explicit storage arithmetic."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LiveCaptureProfile:
    enabled: bool = False
    mode: str = "DATA_ONLY"
    security_ids: tuple[str, ...] = ()
    standard_feed: bool = True
    depth_20_feed: bool = True
    independent_session_monitoring: bool = True
    journal_format: str = "QJ01"
    record_receive_clock: bool = True
    record_source_clock_when_supplied: bool = True
    record_reconnects_and_gaps: bool = True

    def validate_pilot(self) -> None:
        if self.enabled:
            raise ValueError("live capture activation is not permitted in this phase")
        if self.mode != "DATA_ONLY" or not 5 <= len(self.security_ids) <= 10:
            raise ValueError(
                "future pilot requires DATA_ONLY and 5-10 selected securities"
            )
        if len(set(self.security_ids)) != len(self.security_ids):
            raise ValueError("duplicate security IDs")
        if (
            not all(
                (
                    self.standard_feed,
                    self.depth_20_feed,
                    self.independent_session_monitoring,
                    self.record_receive_clock,
                    self.record_source_clock_when_supplied,
                    self.record_reconnects_and_gaps,
                )
            )
            or self.journal_format != "QJ01"
        ):
            raise ValueError("pilot observability and journal requirements missing")


@dataclass(frozen=True, slots=True)
class CapacityEstimate:
    instruments: int
    assumed_peak_events_per_second_per_instrument: float
    assumed_bytes_per_event: int
    session_hours: float
    retention_sessions: int
    raw_events_per_session: int
    raw_bytes_per_session: int
    retained_bytes_with_copies: int
    assumptions_only: bool = True


def estimate_capacity(
    *,
    instruments: int,
    peak_events_per_second_per_instrument: float,
    bytes_per_event: int,
    session_hours: float = 6.25,
    retention_sessions: int = 20,
    copies: int = 2,
) -> CapacityEstimate:
    """Arithmetic, never a live-feed measurement or production readiness claim."""
    if not 5 <= instruments <= 10:
        raise ValueError("pilot size must be 5-10 instruments")
    if (
        min(
            peak_events_per_second_per_instrument,
            bytes_per_event,
            session_hours,
            retention_sessions,
            copies,
        )
        <= 0
    ):
        raise ValueError("positive rate, size, duration and retention required")
    if not math.isfinite(peak_events_per_second_per_instrument) or not math.isfinite(
        session_hours
    ):
        raise ValueError("finite rate and duration required")
    events = int(
        instruments * peak_events_per_second_per_instrument * session_hours * 3600
    )
    raw = events * bytes_per_event
    return CapacityEstimate(
        instruments,
        peak_events_per_second_per_instrument,
        bytes_per_event,
        session_hours,
        retention_sessions,
        events,
        raw,
        raw * retention_sessions * copies,
    )
