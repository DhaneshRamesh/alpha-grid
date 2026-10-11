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

_LOGGER = logging.getLogger(__name__)


def _json_value(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


async def collect() -> None:
    feed = DhanDepthFeed(DhanDepthSettings.from_env(os.environ))
    try:
        async for event in feed:
            print(json.dumps(asdict(event), default=_json_value), flush=True)
    finally:
        await feed.aclose()
        _LOGGER.info(
            "depth_collector_stopped", extra={"context": asdict(feed.counters)}
        )


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


if __name__ == "__main__":
    main()
