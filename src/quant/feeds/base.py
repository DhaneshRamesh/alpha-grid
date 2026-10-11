"""Asynchronous normalized market-data source contract."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from quant.domain import DepthEvent, MarketEvent


class MarketDataFeed(Protocol):
    def __aiter__(self) -> AsyncIterator[MarketEvent]: ...


@dataclass(frozen=True, slots=True)
class FeedCounters:
    connected: bool
    connections: int
    reconnects: int
    messages: int
    depth_events: int
    malformed_messages: int
    disconnects: int


class MarketDepthFeed(MarketDataFeed, Protocol):
    """Broker-independent async source of complete depth snapshots."""

    def __aiter__(self) -> AsyncIterator[DepthEvent]: ...

    @property
    def counters(self) -> FeedCounters: ...

    async def aclose(self) -> None: ...


class FeedObserver(Protocol):
    """Optional broker-independent packet and connection quality callbacks."""

    def connection_changed(
        self, connected: bool, at: datetime, reason: str = "transport"
    ) -> None: ...

    def packet_received(self, key: tuple[str, str], at: datetime) -> None: ...

    def malformed(
        self, key: tuple[str, str] | None, reason: str, at: datetime
    ) -> None: ...
