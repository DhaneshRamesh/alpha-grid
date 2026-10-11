from __future__ import annotations

import asyncio
import json
import struct
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from quant.domain import DepthEvent, Instrument
from quant.feeds.dhan_depth import (
    DhanDepthAuthenticationError,
    DhanDepthFeed,
    DhanDepthSettings,
    _SdkDepthTransport,
)


def _packet(
    code: int,
    *,
    security_id: int = 1333,
    segment: int = 1,
    sequence: int = 42,
    levels: int = 20,
) -> bytes:
    body = b"".join(
        struct.pack("<dII", 100.0 + index * 0.05, 10 + index, 2)
        for index in range(levels)
    )
    return (
        struct.pack("<hBBiI", 12 + len(body), code, segment, security_id, sequence)
        + body
    )


def _settings(*instruments: Instrument) -> DhanDepthSettings:
    selected = instruments or (Instrument("NSE", "NSE_EQ", "1333", "HDFCBANK"),)
    return DhanDepthSettings("client", "secret-token", selected)


class FakeTransport:
    def __init__(
        self,
        messages: list[bytes | str | Exception],
        *,
        connect_error: Exception | None = None,
        exchange_timestamp: datetime | None = None,
        decode_override: Callable[[bytes], object] | None = None,
    ) -> None:
        self.messages = messages
        self.connect_error = connect_error
        self.exchange_timestamp = exchange_timestamp
        self.decode_override = decode_override
        self.closed = False

    async def connect(self) -> None:
        if self.connect_error is not None:
            raise self.connect_error

    async def recv(self) -> bytes | str:
        message = self.messages.pop(0)
        if isinstance(message, Exception):
            raise message
        return message

    def decode(self, frame: bytes) -> object:
        if self.decode_override is not None:
            return self.decode_override(frame)
        _, code, segment, security_id, _ = struct.unpack_from("<hBBiI", frame)
        depth = [
            {"price": price, "quantity": quantity, "orders": orders}
            for price, quantity, orders in struct.iter_unpack("<dII", frame[12:])
        ]
        result: dict[str, Any] = {
            "type": "Bid" if code == 41 else "Ask",
            "exchange_segment": segment,
            "security_id": security_id,
            "depth": depth,
        }
        if self.exchange_timestamp is not None:
            result["exchange_timestamp"] = self.exchange_timestamp
        return result

    async def close(self) -> None:
        self.closed = True


async def _first(feed: DhanDepthFeed) -> DepthEvent:
    iterator = feed.__aiter__()
    try:
        return await anext(iterator)
    finally:
        await feed.aclose()
        await iterator.aclose()


def test_example_config_has_twenty_verified_instruments() -> None:
    example = Path(__file__).resolve().parents[1] / ".env.example"
    values = dict(
        line.split("=", 1)
        for line in example.read_text().splitlines()
        if line and not line.startswith("#")
    )
    values["DHAN_CLIENT_ID"] = "client"
    values["DHAN_ACCESS_TOKEN"] = "secret"
    settings = DhanDepthSettings.from_env(values)
    assert len(settings.instruments) == 20
    assert settings.instruments[3].instrument_id == "1333"
    assert settings.pair_max_age_seconds == 2
    assert "secret" not in repr(settings)


def test_configuration_supports_fifty_but_rejects_more() -> None:
    instruments = tuple(
        Instrument("NSE", "NSE_EQ", str(index), f"SYMBOL{index}")
        for index in range(1, 52)
    )
    assert len(_settings(*instruments[:50]).instruments) == 50
    with pytest.raises(ValueError, match="1 to 50"):
        _settings(*instruments)
    with pytest.raises(ValueError, match="unique"):
        _settings(instruments[0], instruments[0])
    with pytest.raises(ValueError, match="wire format"):
        _settings(Instrument("NSE", "NSE_EQ", "0001", "BAD"))


def test_bid_ask_packets_normalize_to_twenty_levels_and_receive_time() -> None:
    received_at = datetime(2026, 10, 11, 9, 15, tzinfo=UTC)
    exchange_at = received_at - timedelta(milliseconds=50)
    transport = FakeTransport(
        [_packet(41), _packet(51)], exchange_timestamp=exchange_at
    )
    feed = DhanDepthFeed(
        _settings(), transport_factory=lambda: transport, clock=lambda: received_at
    )
    event = asyncio.run(_first(feed))
    assert event.venue == "NSE"
    assert event.exchange_segment == "NSE_EQ"
    assert event.instrument_id == "1333"
    assert event.exchange_timestamp == exchange_at
    assert event.receive_timestamp == received_at
    assert event.sequence_number == 42
    assert len(event.bid_levels) == len(event.ask_levels) == 20
    assert event.bid_levels[0].price == Decimal("100.95")
    assert event.ask_levels[0].price == Decimal("100.0")
    assert event.bid_levels[0].order_count == 2
    assert feed.counters.messages == 2
    assert feed.counters.depth_events == 1
    assert transport.closed


def test_interleaved_instruments_and_coalesced_frames_do_not_cross_pair() -> None:
    combined = b"".join(
        [
            _packet(41, security_id=1333),
            _packet(41, security_id=2885),
            _packet(51, security_id=2885),
            _packet(51, security_id=1333),
        ]
    )
    transport = FakeTransport([combined])
    feed = DhanDepthFeed(
        _settings(
            Instrument("NSE", "NSE_EQ", "1333", "HDFCBANK"),
            Instrument("NSE", "NSE_EQ", "2885", "RELIANCE"),
        ),
        transport_factory=lambda: transport,
    )

    async def read_two() -> list[str]:
        iterator = feed.__aiter__()
        try:
            return [(await anext(iterator)).instrument_id for _ in range(2)]
        finally:
            await feed.aclose()
            await iterator.aclose()

    assert asyncio.run(read_two()) == ["2885", "1333"]
    assert feed.counters.messages == 4


def test_derivatives_segment_is_normalized_without_sdk_codes() -> None:
    transport = FakeTransport(
        [
            _packet(41, security_id=49081, segment=2),
            _packet(51, security_id=49081, segment=2),
        ]
    )
    feed = DhanDepthFeed(
        _settings(Instrument("NSE", "NSE_FNO", "49081", "EXAMPLE_FUT")),
        transport_factory=lambda: transport,
    )
    event = asyncio.run(_first(feed))
    assert event.exchange_segment == "NSE_FNO"
    assert event.instrument_id == "49081"


def test_malformed_packets_are_dropped_without_stale_side_pairing() -> None:
    transport = FakeTransport([_packet(41), b"bad", _packet(51), _packet(41)])
    feed = DhanDepthFeed(_settings(), transport_factory=lambda: transport)
    event = asyncio.run(_first(feed))
    assert len(event.bid_levels) == 20
    assert feed.counters.malformed_messages == 1
    assert feed.counters.messages == 3


def test_short_depth_side_is_rejected_and_following_snapshot_survives() -> None:
    transport = FakeTransport([_packet(41, levels=19), _packet(41), _packet(51)])
    feed = DhanDepthFeed(_settings(), transport_factory=lambda: transport)
    asyncio.run(_first(feed))
    assert feed.counters.malformed_messages == 1
    assert feed.counters.depth_events == 1


def test_invalid_decoded_levels_are_dropped_without_stopping_feed() -> None:
    valid_decoder = FakeTransport([])
    calls = 0

    def decode(frame: bytes) -> object:
        nonlocal calls
        calls += 1
        result = valid_decoder.decode(frame)
        if calls == 1 and isinstance(result, dict):
            result["depth"][0]["price"] = "not-a-price"
        return result

    transport = FakeTransport(
        [_packet(41), _packet(41), _packet(51)], decode_override=decode
    )
    feed = DhanDepthFeed(_settings(), transport_factory=lambda: transport)
    asyncio.run(_first(feed))
    assert feed.counters.malformed_messages == 1
    assert feed.counters.depth_events == 1


def test_empty_wire_level_remains_at_end_of_both_sides() -> None:
    valid_decoder = FakeTransport([])

    def decode(frame: bytes) -> object:
        result = valid_decoder.decode(frame)
        assert isinstance(result, dict)
        result["depth"][10] = {"price": 0.0, "quantity": 0, "orders": 0}
        return result

    transport = FakeTransport([_packet(41), _packet(51)], decode_override=decode)
    feed = DhanDepthFeed(_settings(), transport_factory=lambda: transport)
    event = asyncio.run(_first(feed))
    assert event.bid_levels[-1].price == 0
    assert event.ask_levels[-1].price == 0
    assert len(event.bid_levels) == len(event.ask_levels) == 20


def test_completion_timestamp_comes_from_later_packet() -> None:
    first_at = datetime(2026, 10, 11, 9, 15, tzinfo=UTC)
    times = iter([first_at, first_at + timedelta(milliseconds=7)])
    transport = FakeTransport([_packet(41), _packet(51)])
    feed = DhanDepthFeed(
        _settings(), transport_factory=lambda: transport, clock=lambda: next(times)
    )
    assert asyncio.run(_first(feed)).receive_timestamp == first_at + timedelta(
        milliseconds=7
    )


def test_stale_side_is_not_combined_with_a_later_side() -> None:
    start = datetime(2026, 10, 11, 9, 15, tzinfo=UTC)
    times = iter([start, start + timedelta(seconds=3), start + timedelta(seconds=3)])
    transport = FakeTransport([_packet(41), _packet(51), _packet(41)])
    feed = DhanDepthFeed(
        _settings(), transport_factory=lambda: transport, clock=lambda: next(times)
    )
    event = asyncio.run(_first(feed))
    assert event.receive_timestamp == start + timedelta(seconds=3)
    assert feed.counters.depth_events == 1


def test_disconnect_reconnects_and_clears_partial_book() -> None:
    first = FakeTransport([_packet(41), struct.pack("<hBBiI", 12, 50, 1, 0, 805)])
    second = FakeTransport([_packet(51), _packet(41)])
    transports = iter([first, second])
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    feed = DhanDepthFeed(
        _settings(), transport_factory=lambda: next(transports), sleep=sleep
    )
    asyncio.run(_first(feed))
    assert delays == [1.0]
    assert feed.counters.connections == 2
    assert feed.counters.reconnects == 1
    assert feed.counters.disconnects == 1
    assert first.closed and second.closed


def test_socket_read_failure_is_counted_as_disconnect_and_retried() -> None:
    first = FakeTransport([ConnectionError("socket closed")])
    second = FakeTransport([_packet(41), _packet(51)])
    transports = iter([first, second])

    async def sleep(_delay: float) -> None:
        pass

    feed = DhanDepthFeed(
        _settings(), transport_factory=lambda: next(transports), sleep=sleep
    )
    asyncio.run(_first(feed))
    assert feed.counters.connections == 2
    assert feed.counters.disconnects == 1
    assert feed.counters.reconnects == 1


def test_complete_snapshot_before_coalesced_disconnect_is_emitted() -> None:
    disconnect = struct.pack("<hBBiI", 12, 50, 1, 0, 805)
    first = FakeTransport([_packet(41) + _packet(51) + disconnect])
    second = FakeTransport([_packet(41) + _packet(51)])
    transports = iter([first, second])

    async def sleep(_delay: float) -> None:
        pass

    feed = DhanDepthFeed(
        _settings(), transport_factory=lambda: next(transports), sleep=sleep
    )

    async def read_two() -> list[int]:
        iterator = feed.__aiter__()
        try:
            return [len((await anext(iterator)).bid_levels) for _ in range(2)]
        finally:
            await feed.aclose()
            await iterator.aclose()

    assert asyncio.run(read_two()) == [20, 20]
    assert feed.counters.disconnects == 1


def test_connection_failures_use_capped_backoff_without_logging_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    failed = [
        FakeTransport([], connect_error=RuntimeError("secret-token must not appear"))
        for _ in range(4)
    ]
    success = FakeTransport([_packet(41), _packet(51)])
    transports = iter([*failed, success])
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    settings = DhanDepthSettings(
        "client",
        "secret-token",
        (Instrument("NSE", "NSE_EQ", "1333", "HDFCBANK"),),
        reconnect_initial_seconds=1,
        reconnect_max_seconds=4,
    )
    feed = DhanDepthFeed(
        settings, transport_factory=lambda: next(transports), sleep=sleep
    )
    asyncio.run(_first(feed))
    assert delays == [1, 2, 4, 4]
    assert feed.counters.reconnects == 4
    assert "secret-token" not in caplog.text


def test_auth_rejection_stops_reconnects() -> None:
    transport = FakeTransport([struct.pack("<hBBiI", 12, 50, 1, 0, 807)])
    feed = DhanDepthFeed(_settings(), transport_factory=lambda: transport)

    async def read() -> None:
        iterator = feed.__aiter__()
        try:
            await anext(iterator)
        finally:
            await iterator.aclose()

    with pytest.raises(DhanDepthAuthenticationError, match="807"):
        asyncio.run(read())
    assert feed.counters.disconnects == 1
    assert feed.counters.reconnects == 0


def test_official_decoder_reads_mock_binary_packet_without_account() -> None:
    dhanhq = pytest.importorskip("dhanhq")
    decoder = dhanhq.FullDepth.__new__(dhanhq.FullDepth)
    decoder.depth_level = 20
    decoded = decoder.process_data(_packet(41))
    assert decoded["type"] == "Bid"
    assert len(decoded["depth"]) == 20


def test_official_transport_subscribes_without_live_network(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pytest.importorskip("dhanhq")
    websockets = pytest.importorskip("websockets")
    sent: list[str] = []
    urls: list[str] = []

    class MockSocket:
        async def send(self, message: str) -> None:
            sent.append(message)

        async def recv(self) -> bytes:
            return _packet(41)

        async def close(self) -> None:
            pass

    async def connect(url: str, **_kwargs: object) -> MockSocket:
        logger = _kwargs["logger"]
        assert hasattr(logger, "warning")
        logger.warning("secret-token in upstream debug text")
        urls.append(url)
        return MockSocket()

    monkeypatch.setattr(websockets, "connect", connect)
    transport = _SdkDepthTransport(_settings())

    async def run() -> object:
        await transport.connect()
        try:
            return transport.decode(await transport.recv())
        finally:
            await transport.close()

    decoded = asyncio.run(run())
    assert isinstance(decoded, dict)
    assert len(decoded["depth"]) == 20
    assert len(urls) == 1
    assert "token=secret-token" in urls[0]
    assert sent == [
        '{"RequestCode": 23, "InstrumentCount": 1, "InstrumentList": '
        '[{"ExchangeSegment": "NSE_EQ", "SecurityId": "1333"}]}'
    ]
    assert "secret-token" not in caplog.text


def test_official_transport_subscribes_fifty_in_one_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("dhanhq")
    websockets = pytest.importorskip("websockets")
    sent: list[str] = []

    class MockSocket:
        async def send(self, message: str) -> None:
            sent.append(message)

        async def close(self) -> None:
            pass

    async def connect(_url: str, **_kwargs: object) -> MockSocket:
        return MockSocket()

    monkeypatch.setattr(websockets, "connect", connect)
    settings = _settings(
        *(
            Instrument("NSE", "NSE_EQ", str(index), f"SYMBOL{index}")
            for index in range(1, 51)
        )
    )
    transport = _SdkDepthTransport(settings)

    async def run() -> None:
        await transport.connect()
        await transport.close()

    asyncio.run(run())
    assert len(sent) == 1
    subscription = json.loads(sent[0])
    assert subscription["InstrumentCount"] == 50
    assert len(subscription["InstrumentList"]) == 50


def test_feed_observer_receives_packet_and_malformed_instrument_metrics() -> None:
    from quant.health.monitor import FeedHealthMonitor, HealthThresholds

    received_at = datetime(2026, 10, 11, 9, 15, tzinfo=UTC)
    settings = _settings()
    monitor = FeedHealthMonitor(
        settings.instruments,
        HealthThresholds(),
        session_id="test-session",
        clock=lambda: received_at,
    )
    transport = FakeTransport([_packet(41, levels=19), _packet(41), _packet(51)])
    feed = DhanDepthFeed(
        settings,
        transport_factory=lambda: transport,
        clock=lambda: received_at,
        observer=monitor,
    )
    event = asyncio.run(_first(feed))
    monitor.observe_event(event, received_at)
    instrument = monitor.snapshot(received_at).instruments[0]
    assert instrument.packets_received == 3
    assert instrument.events_received == 1
    assert instrument.malformed_messages == 1
    assert instrument.last_packet_receive_at == received_at
    assert instrument.status.value == "disconnected"
