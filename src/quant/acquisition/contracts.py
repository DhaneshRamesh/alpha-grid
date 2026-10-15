"""Versioned, source-neutral historical candle contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

SCHEMA_VERSION = "ohlcv-v1"
PRICE_SCALE = 1_000_000_000
IST = "Asia/Kolkata"


@dataclass(frozen=True, slots=True)
class CashInstrument:
    venue: str
    segment: str
    security_id: str
    symbol: str
    isin: str | None = None

    def __post_init__(self) -> None:
        if self.venue != "NSE" or self.segment != "NSE_EQ":
            raise ValueError("only NSE cash equities are supported")
        if not self.security_id or not self.symbol:
            raise ValueError("security ID and symbol are required")


@dataclass(frozen=True, slots=True)
class HistoricalBar:
    instrument: CashInstrument
    interval_minutes: int | None  # None means exchange daily bar
    trading_date: date
    bar_start_ns: int | None  # UTC epoch; daily bars have no intraday start
    open_nanos: int
    high_nanos: int
    low_nanos: int
    close_nanos: int
    volume: int
    source: str
    source_sha256: str
    turnover_nanos: int | None = None
    delivery_quantity: int | None = None

    def __post_init__(self) -> None:
        if self.interval_minutes is not None and (
            self.interval_minutes <= 0 or self.bar_start_ns is None
        ):
            raise ValueError("intraday bar requires interval and start time")
        if self.interval_minutes is None and self.bar_start_ns is not None:
            raise ValueError("daily bar has no intraday start time")
        if min(self.open_nanos, self.high_nanos, self.low_nanos, self.close_nanos) <= 0:
            raise ValueError("prices must be positive")
        if self.high_nanos < max(self.open_nanos, self.close_nanos, self.low_nanos):
            raise ValueError("high is below OHLC")
        if self.low_nanos > min(self.open_nanos, self.close_nanos):
            raise ValueError("low is above OHLC")
        if self.volume < 0 or (
            self.delivery_quantity is not None and self.delivery_quantity < 0
        ):
            raise ValueError("negative quantity")

    @property
    def key(self) -> tuple[str, str, int | None, int | None]:
        return (
            self.source,
            self.instrument.security_id,
            self.interval_minutes,
            self.bar_start_ns
            if self.bar_start_ns is not None
            else self.trading_date.toordinal(),
        )
