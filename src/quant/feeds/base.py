"""Asynchronous normalized market-data source contract."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from quant.domain import MarketEvent


class MarketDataFeed(Protocol):
    def __aiter__(self) -> AsyncIterator[MarketEvent]: ...
