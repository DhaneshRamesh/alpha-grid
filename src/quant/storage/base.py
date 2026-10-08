"""Asynchronous normalized market-data sink contract."""

from __future__ import annotations

from typing import Protocol

from quant.domain import MarketEvent


class EventSink(Protocol):
    async def write(self, event: MarketEvent) -> None: ...
