from __future__ import annotations

import io
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from quant.domain import FeedStatus
from quant.health import assess_feed_health
from quant.logging import JsonFormatter


def test_feed_health_uses_receive_time() -> None:
    now = datetime.now(UTC)
    assert (
        assess_feed_health("nse-depth", now, None, 5).status is FeedStatus.DISCONNECTED
    )
    assert (
        assess_feed_health("nse-depth", now, now - timedelta(seconds=5), 5).status
        is FeedStatus.HEALTHY
    )
    assert (
        assess_feed_health("nse-depth", now, now - timedelta(seconds=6), 5).status
        is FeedStatus.STALE
    )
    with pytest.raises(ValueError, match="after observed_at"):
        assess_feed_health("nse-depth", now, now + timedelta(seconds=1), 5)


def test_json_log_contains_context_and_utc_timestamp() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("quant.test.json")
    logger.addHandler(handler)
    try:
        logger.warning("feed_lag", extra={"context": {"seconds": 7}})
    finally:
        logger.removeHandler(handler)
    payload = json.loads(stream.getvalue())
    assert payload["level"] == "WARNING"
    assert payload["message"] == "feed_lag"
    assert payload["context"] == {"seconds": 7}
    assert payload["timestamp"].endswith("Z")
