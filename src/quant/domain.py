"""Broker-independent, immutable normalized market-data records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


def _nonempty(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be empty")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _positive_price(value: Decimal) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError("price must be finite and positive")


@dataclass(frozen=True, slots=True)
class Instrument:
    venue: str
    exchange_segment: str
    instrument_id: str
    symbol: str

    def __post_init__(self) -> None:
        for name in ("venue", "exchange_segment", "instrument_id", "symbol"):
            _nonempty(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class DepthLevel:
    price: Decimal
    quantity: int
    order_count: int | None = None

    def __post_init__(self) -> None:
        if not self.price.is_finite() or self.price < 0:
            raise ValueError("depth price must be finite and nonnegative")
        if self.quantity < 0:
            raise ValueError("quantity must be nonnegative")
        if self.order_count is not None and self.order_count < 0:
            raise ValueError("order_count must be nonnegative")


@dataclass(frozen=True, slots=True)
class DepthEvent:
    venue: str
    exchange_segment: str
    instrument_id: str
    exchange_timestamp: datetime | None
    receive_timestamp: datetime
    bid_levels: tuple[DepthLevel, ...]
    ask_levels: tuple[DepthLevel, ...]
    sequence_number: int | None = None

    def __post_init__(self) -> None:
        for name in ("venue", "exchange_segment", "instrument_id"):
            _nonempty(getattr(self, name), name)
        _aware(self.receive_timestamp, "receive_timestamp")
        if self.exchange_timestamp is not None:
            _aware(self.exchange_timestamp, "exchange_timestamp")
        if not isinstance(self.bid_levels, tuple) or not isinstance(
            self.ask_levels, tuple
        ):
            raise TypeError("depth levels must be tuples")
        if self.sequence_number is not None and self.sequence_number < 0:
            raise ValueError("sequence_number must be nonnegative")


@dataclass(frozen=True, slots=True)
class TradeEvent:
    venue: str
    exchange_segment: str
    instrument_id: str
    exchange_timestamp: datetime | None
    receive_timestamp: datetime
    price: Decimal
    quantity: int
    trade_id: str | None = None
    sequence_number: int | None = None

    def __post_init__(self) -> None:
        for name in ("venue", "exchange_segment", "instrument_id"):
            _nonempty(getattr(self, name), name)
        _aware(self.receive_timestamp, "receive_timestamp")
        if self.exchange_timestamp is not None:
            _aware(self.exchange_timestamp, "exchange_timestamp")
        _positive_price(self.price)
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.sequence_number is not None and self.sequence_number < 0:
            raise ValueError("sequence_number must be nonnegative")


class FeedStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    STALE = "stale"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True, slots=True)
class FeedHealth:
    feed_name: str
    status: FeedStatus
    observed_at: datetime
    last_event_at: datetime | None = None

    def __post_init__(self) -> None:
        _nonempty(self.feed_name, "feed_name")
        _aware(self.observed_at, "observed_at")
        if self.last_event_at is not None:
            _aware(self.last_event_at, "last_event_at")


MarketEvent = DepthEvent | TradeEvent
