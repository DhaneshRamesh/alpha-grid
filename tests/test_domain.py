from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from quant.domain import DepthEvent, DepthLevel, TradeEvent


def test_depth_preserves_exchange_and_receive_metadata() -> None:
    exchange_at = datetime(2026, 1, 2, 9, 15, tzinfo=UTC)
    received_at = datetime(2026, 1, 2, 9, 15, 1, tzinfo=UTC)
    bid = DepthLevel(Decimal("100.05"), 10, 2)
    event = DepthEvent(
        venue="NSE",
        exchange_segment="NSE_EQ",
        instrument_id="12345",
        exchange_timestamp=exchange_at,
        receive_timestamp=received_at,
        bid_levels=(bid,),
        ask_levels=(DepthLevel(Decimal("100.10"), 8),),
        sequence_number=42,
    )
    assert (
        event.venue,
        event.exchange_segment,
        event.instrument_id,
        event.exchange_timestamp,
        event.receive_timestamp,
        event.bid_levels,
        event.ask_levels,
        event.sequence_number,
    ) == (
        "NSE",
        "NSE_EQ",
        "12345",
        exchange_at,
        received_at,
        (bid,),
        (DepthLevel(Decimal("100.10"), 8),),
        42,
    )
    with pytest.raises(FrozenInstanceError):
        event.venue = "OTHER"  # type: ignore[misc]


def test_depth_allows_missing_exchange_timestamp() -> None:
    event = DepthEvent("NSE", "NSE_EQ", "1", None, datetime.now(UTC), (), ())
    assert event.exchange_timestamp is None


def test_events_reject_naive_timestamps_and_mutable_levels() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="timezone-aware"):
        DepthEvent("NSE", "NSE_EQ", "1", None, datetime.now(), (), ())
    with pytest.raises(TypeError, match="tuples"):
        DepthEvent("NSE", "NSE_EQ", "1", None, now, [], ())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timezone-aware"):
        TradeEvent("NSE", "NSE_EQ", "1", datetime.now(), now, Decimal("1"), 1)


def test_trade_requires_positive_finite_price_and_quantity() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="price"):
        TradeEvent("NSE", "NSE_EQ", "1", None, now, Decimal("NaN"), 1)
    with pytest.raises(ValueError, match="quantity"):
        TradeEvent("NSE", "NSE_EQ", "1", None, now, Decimal("1"), 0)


def test_depth_level_accepts_empty_wire_placeholder_only_as_nonnegative_price() -> None:
    assert DepthLevel(Decimal("0"), 0, 0).price == 0
    with pytest.raises(ValueError, match="depth price"):
        DepthLevel(Decimal("-1"), 0)
