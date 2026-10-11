"""Run the Dhan depth collector and emit normalized JSON Lines to stdout."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from quant.config import AppConfig
from quant.feeds.dhan_depth import (
    DhanDepthAuthenticationError,
    DhanDepthFeed,
    DhanDepthSettings,
)
from quant.health.monitor import (
    FeedHealthMonitor,
    HealthThresholds,
    write_health_status,
)
from quant.logging import configure_logging
from quant.storage.journal import EventJournal, JournalSettings

_LOGGER = logging.getLogger(__name__)


def _json_value(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


async def collect() -> None:
    settings = DhanDepthSettings.from_env(os.environ)
    thresholds = HealthThresholds.from_env(os.environ)
    status_path = Path(
        os.environ.get("QUANT_HEALTH_STATUS_PATH", "data/feed_health.json")
    )
    journal_settings = JournalSettings.from_env(os.environ)
    monitor = FeedHealthMonitor(
        settings.instruments, thresholds, session_id=journal_settings.session_id
    )
    symbols = {
        (item.exchange_segment, item.instrument_id): item.symbol
        for item in settings.instruments
    }
    with EventJournal(journal_settings, symbols) as journal:
        if journal.recovery.truncated_bytes:
            _LOGGER.warning(
                "journal_tail_recovered",
                extra={"context": asdict(journal.recovery)},
            )
        feed = DhanDepthFeed(settings, observer=monitor)

        async def publish_health() -> None:
            while True:
                try:
                    write_health_status(status_path, monitor.snapshot())
                except OSError as exc:
                    _LOGGER.critical(
                        "health_status_write_failed",
                        extra={"context": {"error_type": type(exc).__name__}},
                    )
                    raise
                await asyncio.sleep(thresholds.poll_seconds)

        async def consume_events() -> None:
            async for event in feed:
                try:
                    journal.append(event)
                except OSError as exc:
                    receive_timestamp = event.receive_timestamp.isoformat()
                    _LOGGER.critical(
                        "market_event_journal_failed",
                        extra={
                            "context": {
                                "session_id": journal_settings.session_id,
                                "exchange_segment": event.exchange_segment,
                                "instrument_id": event.instrument_id,
                                "symbol": symbols.get(
                                    (event.exchange_segment, event.instrument_id)
                                ),
                                "receive_timestamp": receive_timestamp,
                                "error_type": type(exc).__name__,
                            }
                        },
                    )
                    raise
                monitor.observe_event(event)
                print(json.dumps(asdict(event), default=_json_value), flush=True)

        publisher = asyncio.create_task(publish_health())
        consumer = asyncio.create_task(consume_events())
        try:
            done, _ = await asyncio.wait(
                (publisher, consumer), return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
        finally:
            publisher.cancel()
            consumer.cancel()
            await asyncio.gather(publisher, consumer, return_exceptions=True)
            await feed.aclose()
            monitor.connection_changed(False, datetime.now(UTC), "collector_stopped")
            write_health_status(status_path, monitor.snapshot())
    _LOGGER.info(
        "journal_closed",
        extra={
            "context": {
                "records": journal.appended_records,
                "bytes": journal.appended_bytes,
                "fsyncs": journal.fsync_count,
            }
        },
    )
    _LOGGER.info("depth_collector_stopped", extra={"context": asdict(feed.counters)})


def main() -> None:
    config = AppConfig.from_env()
    configure_logging(config.logging_level)
    try:
        asyncio.run(collect())
    except KeyboardInterrupt:
        pass
    except DhanDepthAuthenticationError as exc:
        _LOGGER.error(
            "depth_authentication_failed", extra={"context": {"reason": str(exc)}}
        )
        raise SystemExit(2) from None
    except ValueError as exc:
        _LOGGER.error(
            "depth_configuration_invalid", extra={"context": {"reason": str(exc)}}
        )
        raise SystemExit(2) from None
    except OSError as exc:
        _LOGGER.error(
            "collector_storage_failed", extra={"context": {"reason": str(exc)}}
        )
        raise SystemExit(3) from None


if __name__ == "__main__":
    main()
