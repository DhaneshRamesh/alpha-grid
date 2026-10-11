"""Dhan 20-level WebSocket packets to broker-independent depth events."""

from __future__ import annotations

import asyncio
import json
import logging
import struct
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol, cast
from urllib.parse import urlencode

from quant.domain import DepthEvent, DepthLevel, Instrument
from quant.feeds.base import FeedCounters

_LOGGER = logging.getLogger(__name__)
_SOCKET_LOGGER = logging.getLogger(f"{__name__}.websocket")
# The authenticated WebSocket URL contains a token; suppress transport logs.
_SOCKET_LOGGER.setLevel(logging.WARNING)
_SOCKET_LOGGER.addHandler(logging.NullHandler())
_SOCKET_LOGGER.propagate = False
_SOCKET_LOGGER.disabled = True
_HEADER = struct.Struct("<hBBiI")
_DEPTH_PACKET_LENGTH = 12 + 20 * 16
_SEGMENTS = {1: "NSE_EQ", 2: "NSE_FNO"}
_SEGMENT_CODES = {name: code for code, name in _SEGMENTS.items()}
_AUTH_FAILURE_CODES = {806, 807, 808, 809}


@dataclass(frozen=True, slots=True)
class DhanDepthSettings:
    client_id: str = field(repr=False)
    access_token: str = field(repr=False)
    instruments: tuple[Instrument, ...]
    reconnect_initial_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0
    pair_max_age_seconds: float = 2.0

    def __post_init__(self) -> None:
        if not self.client_id.strip() or not self.access_token.strip():
            raise ValueError("Dhan client ID and access token are required")
        if not 1 <= len(self.instruments) <= 50:
            raise ValueError("Dhan 20-level depth requires 1 to 50 instruments")
        keys: set[tuple[str, str]] = set()
        for instrument in self.instruments:
            if (
                instrument.venue != "NSE"
                or instrument.exchange_segment not in _SEGMENT_CODES
            ):
                raise ValueError("Dhan depth supports NSE_EQ and NSE_FNO only")
            if (
                not instrument.instrument_id.isascii()
                or not instrument.instrument_id.isdigit()
            ):
                raise ValueError("Dhan security IDs must be decimal strings")
            if len(instrument.instrument_id) > 10:
                raise ValueError("Dhan security IDs must fit the wire format")
            security_id = int(instrument.instrument_id)
            if (
                str(security_id) != instrument.instrument_id
                or not 1 <= security_id <= 2_147_483_647
            ):
                raise ValueError("Dhan security IDs must fit the wire format")
            key = (instrument.exchange_segment, instrument.instrument_id)
            if key in keys:
                raise ValueError("Dhan depth instruments must be unique")
            keys.add(key)
        if not 0 < self.reconnect_initial_seconds <= self.reconnect_max_seconds:
            raise ValueError("reconnect delays must be positive and ordered")
        if self.reconnect_max_seconds == float("inf"):
            raise ValueError("reconnect delay must be finite")
        if not 0 < self.pair_max_age_seconds < float("inf"):
            raise ValueError("pair_max_age_seconds must be finite and positive")

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> DhanDepthSettings:
        raw_instruments = environ.get("QUANT_DEPTH_INSTRUMENTS", "")
        instruments: list[Instrument] = []
        for item in raw_instruments.split(","):
            if not item.strip():
                continue
            parts = [part.strip() for part in item.split(":")]
            if len(parts) != 3:
                raise ValueError("invalid QUANT_DEPTH_INSTRUMENTS entry")
            segment, security_id, symbol = parts
            instruments.append(Instrument("NSE", segment, security_id, symbol))
        try:
            initial = float(environ.get("QUANT_DEPTH_RECONNECT_INITIAL_SECONDS", "1"))
            maximum = float(environ.get("QUANT_DEPTH_RECONNECT_MAX_SECONDS", "30"))
            pair_max_age = float(environ.get("QUANT_DEPTH_PAIR_MAX_AGE_SECONDS", "2"))
        except ValueError as exc:
            raise ValueError("depth timing settings must be numeric") from exc
        return cls(
            client_id=environ.get("DHAN_CLIENT_ID", ""),
            access_token=environ.get("DHAN_ACCESS_TOKEN", ""),
            instruments=tuple(instruments),
            reconnect_initial_seconds=initial,
            reconnect_max_seconds=maximum,
            pair_max_age_seconds=pair_max_age,
        )


class DhanDepthAuthenticationError(RuntimeError):
    """A server rejection that requires credentials or entitlement to change."""


class _ServerDisconnect(Exception):
    def __init__(self, code: int) -> None:
        self.code = code
        super().__init__(f"Dhan disconnect code {code}")


class _Socket(Protocol):
    async def recv(self) -> bytes | str: ...

    async def send(self, message: str) -> None: ...

    async def close(self) -> None: ...


class _DepthTransport(Protocol):
    async def connect(self) -> None: ...

    async def recv(self) -> bytes | str: ...

    def decode(self, frame: bytes) -> object: ...

    async def close(self) -> None: ...


class _OfficialDecoder(Protocol):
    depth_20_feed_wss: str
    loop: asyncio.AbstractEventLoop

    def process_data(self, frame: bytes) -> object: ...


class _SdkDepthTransport:
    """Use the official decoder with a direct socket for immediate receipt timing."""

    def __init__(self, settings: DhanDepthSettings) -> None:
        self._settings = settings
        self._decoder: _OfficialDecoder | None = None
        self._socket: _Socket | None = None

    async def connect(self) -> None:
        from dhanhq import DhanContext, FullDepth
        from websockets import connect

        running_loop = asyncio.get_running_loop()
        try:
            decoder = cast(
                _OfficialDecoder,
                FullDepth(
                    DhanContext(self._settings.client_id, self._settings.access_token),
                    [
                        (_SEGMENT_CODES[item.exchange_segment], item.instrument_id)
                        for item in self._settings.instruments
                    ],
                    depth_level=20,
                ),
            )
        finally:
            # FullDepth creates and selects a separate loop for its synchronous API.
            asyncio.set_event_loop(running_loop)
        decoder.loop.close()
        self._decoder = decoder
        query = urlencode(
            {
                "token": self._settings.access_token,
                "clientId": self._settings.client_id,
                "authType": "2",
            }
        )
        url = f"{decoder.depth_20_feed_wss}?{query}"
        self._socket = cast(
            _Socket,
            await connect(
                url,
                open_timeout=10,
                ping_interval=20,
                ping_timeout=20,
                logger=_SOCKET_LOGGER,
            ),
        )
        subscription = {
            "RequestCode": 23,
            "InstrumentCount": len(self._settings.instruments),
            "InstrumentList": [
                {
                    "ExchangeSegment": item.exchange_segment,
                    "SecurityId": item.instrument_id,
                }
                for item in self._settings.instruments
            ],
        }
        await self._socket.send(json.dumps(subscription))

    async def recv(self) -> bytes | str:
        if self._socket is None:
            raise RuntimeError("Dhan depth socket is not connected")
        return await self._socket.recv()

    def decode(self, frame: bytes) -> object:
        if self._decoder is None:
            raise RuntimeError("Dhan depth decoder is not initialized")
        return self._decoder.process_data(frame)

    async def close(self) -> None:
        if self._socket is not None:
            await self._socket.close()
            self._socket = None


@dataclass(frozen=True, slots=True)
class _Side:
    levels: tuple[DepthLevel, ...]
    received_at: datetime
    exchange_timestamp: datetime | None
    sequence_number: int


class DhanDepthFeed:
    """Reconnectable, async source of normalized Dhan 20-level snapshots."""

    def __init__(
        self,
        settings: DhanDepthSettings,
        *,
        transport_factory: Callable[[], _DepthTransport] | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._factory = transport_factory or (lambda: _SdkDepthTransport(settings))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._subscribed = {
            (item.exchange_segment, item.instrument_id) for item in settings.instruments
        }
        self._pending: dict[tuple[str, str], dict[str, _Side]] = {}
        self._active: _DepthTransport | None = None
        self._running = False
        self._stopping = False
        self._connected = False
        self._connections = 0
        self._reconnects = 0
        self._messages = 0
        self._depth_events = 0
        self._malformed_messages = 0
        self._disconnects = 0

    @property
    def counters(self) -> FeedCounters:
        return FeedCounters(
            connected=self._connected,
            connections=self._connections,
            reconnects=self._reconnects,
            messages=self._messages,
            depth_events=self._depth_events,
            malformed_messages=self._malformed_messages,
            disconnects=self._disconnects,
        )

    def __aiter__(self) -> AsyncIterator[DepthEvent]:
        return self._events()

    async def aclose(self) -> None:
        self._stopping = True
        if self._active is not None:
            await self._active.close()

    async def _events(self) -> AsyncIterator[DepthEvent]:
        if self._running:
            raise RuntimeError("a depth feed supports one active consumer")
        self._running = True
        delay = self._settings.reconnect_initial_seconds
        try:
            while not self._stopping:
                transport = self._factory()
                self._active = transport
                try:
                    await transport.connect()
                    self._connected = True
                    self._connections += 1
                    _LOGGER.info(
                        "depth_connected",
                        extra={"context": {"connections": self._connections}},
                    )
                    while not self._stopping:
                        packet = await transport.recv()
                        received_at = self._clock()
                        for event in self._consume(packet, received_at, transport):
                            self._depth_events += 1
                            delay = self._settings.reconnect_initial_seconds
                            yield event
                except _ServerDisconnect as exc:
                    self._disconnects += 1
                    _LOGGER.warning(
                        "depth_disconnected",
                        extra={"context": {"code": exc.code}},
                    )
                    if exc.code in _AUTH_FAILURE_CODES:
                        raise DhanDepthAuthenticationError(
                            f"Dhan depth rejected the session with code {exc.code}"
                        ) from None
                except ModuleNotFoundError:
                    raise RuntimeError("install the dhan optional dependency") from None
                except Exception as exc:
                    if not self._stopping:
                        if self._connected:
                            self._disconnects += 1
                        _LOGGER.warning(
                            "depth_connection_failed",
                            extra={"context": {"error_type": type(exc).__name__}},
                        )
                finally:
                    self._connected = False
                    self._pending.clear()
                    try:
                        await transport.close()
                    except Exception as exc:
                        _LOGGER.warning(
                            "depth_close_failed",
                            extra={"context": {"error_type": type(exc).__name__}},
                        )
                    self._active = None
                if self._stopping:
                    break
                self._reconnects += 1
                _LOGGER.info(
                    "depth_reconnect_scheduled",
                    extra={"context": {"attempt": self._reconnects, "delay": delay}},
                )
                await self._sleep(delay)
                delay = min(delay * 2, self._settings.reconnect_max_seconds)
        finally:
            self._running = False

    def _consume(
        self, packet: bytes | str, received_at: datetime, transport: _DepthTransport
    ) -> Iterator[DepthEvent]:
        if not isinstance(packet, bytes):
            self._malformed("non_binary_packet")
            return
        offset = 0
        while offset < len(packet):
            if len(packet) - offset < _HEADER.size:
                self._malformed("short_header")
                break
            length, code, segment_code, security_id, sequence = _HEADER.unpack_from(
                packet, offset
            )
            if length < _HEADER.size or length > len(packet) - offset:
                self._malformed("invalid_length")
                break
            frame = packet[offset : offset + length]
            offset += length
            self._messages += 1
            if code == 50:
                raise _ServerDisconnect(sequence)
            if code not in (41, 51):
                continue
            if length != _DEPTH_PACKET_LENGTH or segment_code not in _SEGMENTS:
                self._malformed("invalid_depth_frame")
                continue
            segment = _SEGMENTS[segment_code]
            key = (segment, str(security_id))
            if key not in self._subscribed:
                self._malformed("unknown_instrument")
                continue
            try:
                decoded = transport.decode(frame)
                side = self._parse_side(
                    decoded, code, segment_code, security_id, sequence, received_at
                )
            except (
                ValueError,
                TypeError,
                KeyError,
                OverflowError,
                struct.error,
            ) as exc:
                self._malformed(type(exc).__name__)
                continue
            except Exception as exc:
                self._malformed(type(exc).__name__)
                continue
            event = self._pair(key, "Bid" if code == 41 else "Ask", side)
            if event is not None:
                yield event

    def _parse_side(
        self,
        decoded: object,
        code: int,
        segment_code: int,
        security_id: int,
        sequence: int,
        received_at: datetime,
    ) -> _Side:
        if not isinstance(decoded, Mapping):
            raise ValueError("missing decoded packet")
        if (
            decoded.get("type") != ("Bid" if code == 41 else "Ask")
            or decoded.get("exchange_segment") != segment_code
            or decoded.get("security_id") != security_id
        ):
            raise ValueError("decoded header mismatch")
        raw_levels = decoded.get("depth")
        if not isinstance(raw_levels, list) or len(raw_levels) != 20:
            raise ValueError("depth side must contain exactly 20 levels")
        levels: list[DepthLevel] = []
        for row in raw_levels:
            if not isinstance(row, Mapping):
                raise ValueError("invalid depth level")
            price = Decimal(str(row["price"]))
            quantity = row["quantity"]
            orders = row["orders"]
            if (
                isinstance(quantity, bool)
                or not isinstance(quantity, int)
                or isinstance(orders, bool)
                or not isinstance(orders, int)
            ):
                raise ValueError("invalid depth counts")
            if price == 0 and (quantity != 0 or orders != 0):
                raise ValueError("zero-price level must be empty")
            levels.append(DepthLevel(price, quantity, orders))
        levels.sort(
            key=lambda level: (
                level.price == 0,
                -level.price if code == 41 else level.price,
            )
        )
        return _Side(
            levels=tuple(levels),
            received_at=received_at,
            exchange_timestamp=_exchange_timestamp(decoded.get("exchange_timestamp")),
            sequence_number=sequence,
        )

    def _pair(self, key: tuple[str, str], name: str, side: _Side) -> DepthEvent | None:
        pending = self._pending.setdefault(key, {})
        opposite = pending.get("Ask" if name == "Bid" else "Bid")
        if opposite is not None:
            age = abs((side.received_at - opposite.received_at).total_seconds())
            if age > self._settings.pair_max_age_seconds:
                pending.clear()
        pending[name] = side
        if "Bid" not in pending or "Ask" not in pending:
            return None
        bid = pending["Bid"]
        ask = pending["Ask"]
        del self._pending[key]
        timestamps = [
            timestamp
            for timestamp in (bid.exchange_timestamp, ask.exchange_timestamp)
            if timestamp is not None
        ]
        return DepthEvent(
            venue="NSE",
            exchange_segment=key[0],
            instrument_id=key[1],
            exchange_timestamp=max(timestamps) if timestamps else None,
            receive_timestamp=max(bid.received_at, ask.received_at),
            bid_levels=bid.levels,
            ask_levels=ask.levels,
            sequence_number=(
                bid.sequence_number
                if bid.sequence_number == ask.sequence_number
                else None
            ),
        )

    def _malformed(self, reason: str) -> None:
        self._malformed_messages += 1
        self._pending.clear()
        _LOGGER.warning("depth_packet_dropped", extra={"context": {"reason": reason}})


def _exchange_timestamp(raw: object) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        timestamp = raw
    elif isinstance(raw, str):
        timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    elif isinstance(raw, int | float) and not isinstance(raw, bool):
        epoch = raw / 1000 if raw >= 1_000_000_000_000 else raw
        timestamp = datetime.fromtimestamp(epoch, UTC)
    else:
        raise ValueError("unsupported exchange timestamp")
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("exchange timestamp must be timezone-aware")
    return timestamp
