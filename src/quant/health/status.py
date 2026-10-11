"""Assess receive-side freshness without assuming a feed vendor."""

from __future__ import annotations

from datetime import datetime

from quant.domain import FeedHealth, FeedStatus, _aware


def assess_feed_health(
    feed_name: str,
    observed_at: datetime,
    last_event_at: datetime | None,
    stale_after_seconds: float,
    degraded_after_seconds: float | None = None,
) -> FeedHealth:
    if not 0 < stale_after_seconds < float("inf"):
        raise ValueError("stale_after_seconds must be finite and positive")
    if degraded_after_seconds is not None and not (
        0 < degraded_after_seconds < stale_after_seconds
    ):
        raise ValueError("degraded_after_seconds must precede stale threshold")
    _aware(observed_at, "observed_at")
    if last_event_at is None:
        status = FeedStatus.DISCONNECTED
    else:
        _aware(last_event_at, "last_event_at")
        age = (observed_at - last_event_at).total_seconds()
        if age < 0:
            raise ValueError("last_event_at must not be after observed_at")
        if age > stale_after_seconds:
            status = FeedStatus.STALE
        elif degraded_after_seconds is not None and age > degraded_after_seconds:
            status = FeedStatus.DEGRADED
        else:
            status = FeedStatus.HEALTHY
    return FeedHealth(feed_name, status, observed_at, last_event_at)
