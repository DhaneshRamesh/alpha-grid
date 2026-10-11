"""Run the Dhan depth collector and emit normalized JSON Lines to stdout."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal

from quant.config import AppConfig
from quant.feeds.dhan_depth import (
    DhanDepthAuthenticationError,
    DhanDepthFeed,
    DhanDepthSettings,
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
    symbols = {
        (item.exchange_segment, item.instrument_id): item.symbol
        for item in settings.instruments
    }
    with EventJournal(JournalSettings.from_env(os.environ), symbols) as journal:
        if journal.recovery.truncated_bytes:
            _LOGGER.warning(
                "journal_tail_recovered",
                extra={"context": asdict(journal.recovery)},
            )
        feed = DhanDepthFeed(settings)
        try:
            async for event in feed:
                journal.append(event)
                print(json.dumps(asdict(event), default=_json_value), flush=True)
        finally:
            await feed.aclose()
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
        _LOGGER.error("depth_journal_failed", extra={"context": {"reason": str(exc)}})
        raise SystemExit(3) from None


if __name__ == "__main__":
    main()
