"""Assess receive-side freshness without assuming a feed vendor."""

from __future__ import annotations

from datetime import datetime

from quant.domain import FeedHealth, FeedStatus, _aware


def assess_feed_health(
    feed_name: str,
    observed_at: datetime,
    last_event_at: datetime | None,
    stale_after_seconds: float,
) -> FeedHealth:
    if not 0 < stale_after_seconds < float("inf"):
        raise ValueError("stale_after_seconds must be finite and positive")
    _aware(observed_at, "observed_at")
    if last_event_at is None:
        status = FeedStatus.DISCONNECTED
    else:
        _aware(last_event_at, "last_event_at")
        age = (observed_at - last_event_at).total_seconds()
        if age < 0:
            raise ValueError("last_event_at must not be after observed_at")
        status = FeedStatus.STALE if age > stale_after_seconds else FeedStatus.HEALTHY
    return FeedHealth(feed_name, status, observed_at, last_event_at)
