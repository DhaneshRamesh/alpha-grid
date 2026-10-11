"""Read-only DNS, TCP, TLS, HTTP, WebSocket, and Dhan feed probes."""

from __future__ import annotations

import asyncio
import http.client
import ipaddress
import os
import socket
import ssl
import time
from collections.abc import AsyncGenerator, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlsplit

from infra.benchmark.common import distribution

HOST = "api.dhan.co"
PORT = 443


class _TrackingHTTPSConnection(http.client.HTTPSConnection):
    """Count actual TLS connections, including transparent reconnects."""

    def __init__(self, host: str, port: int, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self.connect_count = 0

    def connect(self) -> None:
        super().connect()
        self.connect_count += 1


class _ConnectionProbe:
    def __init__(self) -> None:
        self.started_ns = time.perf_counter_ns()
        self.first_connect_ms: float | None = None
        self.connect_ms: list[float] = []
        self.connection_events: list[dict[str, Any]] = []
        self._disconnected_ns: int | None = None
        self.packet_counts: dict[str, int] = {}
        self.malformed_counts: dict[str, int] = {}
        self.packet_interarrival_ms: dict[str, list[float]] = {}
        self.packet_observations: list[dict[str, Any]] = []
        self._last_packet_ns: dict[str, int] = {}

    def connection_changed(
        self, connected: bool, at: datetime, reason: str = "transport"
    ) -> None:
        self.connection_events.append(
            {
                "at": datetime.now(UTC).isoformat(),
                "connected": connected,
                "reason": reason,
            }
        )
        if connected and self.first_connect_ms is None:
            self.first_connect_ms = (
                time.perf_counter_ns() - self.started_ns
            ) / 1_000_000
        if connected:
            origin = self._disconnected_ns or self.started_ns
            self.connect_ms.append((time.perf_counter_ns() - origin) / 1_000_000)
            self._disconnected_ns = None
        else:
            self._disconnected_ns = time.perf_counter_ns()

    def packet_received(self, key: tuple[str, str], at: datetime) -> None:
        name = f"{key[0]}:{key[1]}"
        now = time.perf_counter_ns()
        self.packet_counts[name] = self.packet_counts.get(name, 0) + 1
        last = self._last_packet_ns.get(name)
        if last is not None:
            self.packet_interarrival_ms.setdefault(name, []).append(
                (now - last) / 1_000_000
            )
        self._last_packet_ns[name] = now
        self.packet_observations.append(
            {
                "instrument": name,
                "at": at.isoformat(),
                "elapsed_from_start_ms": (now - self.started_ns) / 1_000_000,
            }
        )

    def malformed(self, key: tuple[str, str] | None, reason: str, at: datetime) -> None:
        name = f"{key[0]}:{key[1]}" if key is not None else "unknown"
        self.malformed_counts[name] = self.malformed_counts.get(name, 0) + 1


def _timed_call(action: Callable[[], object]) -> float:
    start = time.perf_counter_ns()
    action()
    return (time.perf_counter_ns() - start) / 1_000_000


def _probe_dns() -> float:
    return _timed_call(lambda: socket.getaddrinfo(HOST, PORT, type=socket.SOCK_STREAM))


def _probe_tcp(timeout: float) -> float:
    family, kind, proto, _, address = socket.getaddrinfo(
        HOST, PORT, type=socket.SOCK_STREAM
    )[0]
    with socket.socket(family, kind, proto) as raw:
        raw.settimeout(timeout)
        start = time.perf_counter_ns()
        raw.connect(address)
        return (time.perf_counter_ns() - start) / 1_000_000


def _probe_tls(timeout: float, context: ssl.SSLContext) -> float:
    family, kind, proto, _, address = socket.getaddrinfo(
        HOST, PORT, type=socket.SOCK_STREAM
    )[0]
    with socket.socket(family, kind, proto) as raw:
        raw.settimeout(timeout)
        raw.connect(address)
        start = time.perf_counter_ns()
        with context.wrap_socket(raw, server_hostname=HOST):
            return (time.perf_counter_ns() - start) / 1_000_000


def _probe_https(connection: http.client.HTTPSConnection) -> tuple[float, int]:
    start = time.perf_counter_ns()
    connection.request("GET", "/v2/")  # Public, read-only path; status need not be 200.
    response = connection.getresponse()
    response.read()
    return (time.perf_counter_ns() - start) / 1_000_000, response.status


def run_network(
    samples: int = 30, timeout: float = 5.0, interval: float = 1.0
) -> dict[str, Any]:
    """Time phase-specific probes; report whether HTTPS actually reused a socket."""
    if samples < 1 or timeout <= 0 or interval < 0:
        raise ValueError("invalid network probe settings")
    data: dict[str, list[float]] = {name: [] for name in ("dns", "tcp", "tls", "https")}
    failures: dict[str, int] = {name: 0 for name in data}
    status_codes: dict[str, int] = {}
    observations: list[dict[str, Any]] = []
    context = ssl.create_default_context()
    connection: _TrackingHTTPSConnection | None = None
    https_connections = 0
    reused_requests = 0
    try:
        for index in range(samples):
            for name, action in (
                ("dns", _probe_dns),
                ("tcp", lambda: _probe_tcp(timeout)),
                ("tls", lambda: _probe_tls(timeout, context)),
            ):
                try:
                    elapsed = action()
                    data[name].append(elapsed)
                    observations.append(
                        {
                            "sample": index,
                            "phase": name,
                            "at": datetime.now(UTC).isoformat(),
                            "elapsed_ms": elapsed,
                        }
                    )
                except (OSError, TimeoutError):
                    failures[name] += 1
                    observations.append(
                        {
                            "sample": index,
                            "phase": name,
                            "at": datetime.now(UTC).isoformat(),
                            "error": "transport",
                        }
                    )
            try:
                if connection is None:
                    connection = _TrackingHTTPSConnection(HOST, PORT, timeout=timeout)
                before_connects = connection.connect_count
                elapsed, status = _probe_https(connection)
                new_connections = connection.connect_count - before_connects
                https_connections += new_connections
                reused = new_connections == 0 and before_connects > 0
                reused_requests += int(reused)
                data["https"].append(elapsed)
                status_codes[str(status)] = status_codes.get(str(status), 0) + 1
                observations.append(
                    {
                        "sample": index,
                        "phase": "https",
                        "at": datetime.now(UTC).isoformat(),
                        "elapsed_ms": elapsed,
                        "status": status,
                        "new_connections": new_connections,
                        "reused_connection": reused,
                    }
                )
            except (OSError, TimeoutError, http.client.HTTPException):
                failures["https"] += 1
                observations.append(
                    {
                        "sample": index,
                        "phase": "https",
                        "at": datetime.now(UTC).isoformat(),
                        "error": "transport",
                    }
                )
                if connection is not None:
                    connection.close()
                connection = None
            if interval:
                time.sleep(interval)
    finally:
        if connection is not None:
            connection.close()
    return {
        "target": f"{HOST}:{PORT}",
        "https_path": "/v2/",
        "https_connection_reused": reused_requests > 0,
        "https_reused_requests": reused_requests,
        "https_new_connections": https_connections,
        "attempts": samples,
        "latency_ms": {name: distribution(values) for name, values in data.items()},
        "failures": failures,
        "https_status_codes": status_codes,
        "packet_loss": None,  # TCP failures are not a packet-loss measurement.
        "observations": observations,
    }


def run_authenticated_api(
    token: str, *, samples: int = 30, timeout: float = 5.0, interval: float = 1.0
) -> dict[str, Any]:
    """Rate-limited, read-only profile GET; discard sensitive response bodies."""
    if not token or samples < 1 or timeout <= 0 or interval < 1.0:
        raise ValueError("profile probe needs a token and at least 1s spacing")
    connection: http.client.HTTPSConnection | None = None
    observations: list[dict[str, Any]] = []
    latencies: list[float] = []
    failures = 0
    try:
        for index in range(samples):
            try:
                if connection is None:
                    connection = http.client.HTTPSConnection(
                        HOST, PORT, timeout=timeout
                    )
                start = time.perf_counter_ns()
                connection.request(
                    "GET", "/v2/profile", headers={"access-token": token}
                )
                response = connection.getresponse()
                response.read()
                elapsed = (time.perf_counter_ns() - start) / 1_000_000
                observations.append(
                    {
                        "sample": index,
                        "at": datetime.now(UTC).isoformat(),
                        "elapsed_ms": elapsed,
                        "status": response.status,
                    }
                )
                if response.status == 200:
                    latencies.append(elapsed)
                else:
                    failures += 1
                    if response.status in {401, 403, 429}:
                        break
            except (OSError, TimeoutError, http.client.HTTPException):
                failures += 1
                observations.append(
                    {
                        "sample": index,
                        "at": datetime.now(UTC).isoformat(),
                        "error": "transport",
                    }
                )
                if connection is not None:
                    connection.close()
                connection = None
            if index + 1 < samples:
                time.sleep(interval)
    finally:
        if connection is not None:
            connection.close()
    return {
        "path": "/v2/profile",
        "attempts": len(observations),
        "requested_samples": samples,
        "latency_ms": distribution(latencies),
        "failures": failures,
        "observations": observations,
    }


def run_egress_ipv4(
    expected: str | None = None, *, timeout: float = 5.0
) -> dict[str, Any]:
    """Observe outward IPv4 through two independent HTTPS reflectors."""
    if expected is not None:
        expected = str(ipaddress.IPv4Address(expected))
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    addresses: list[str] = []
    failures = 0
    for host in ("checkip.amazonaws.com", "api.ipify.org"):
        connection = http.client.HTTPSConnection(host, 443, timeout=timeout)
        try:
            connection.request("GET", "/")
            response = connection.getresponse()
            body = response.read(64).decode("ascii").strip()
            if response.status != 200:
                raise ValueError("reflector returned non-200")
            addresses.append(str(ipaddress.IPv4Address(body)))
        except (
            OSError,
            TimeoutError,
            ValueError,
            UnicodeError,
            http.client.HTTPException,
        ):
            failures += 1
        finally:
            connection.close()
    consistent = len(addresses) == 2 and addresses[0] == addresses[1]
    return {
        "observed_ipv4": addresses[0] if consistent else None,
        "expected_ipv4": expected,
        "consistent": consistent,
        "matches_reserved_ip": consistent
        and expected is not None
        and addresses[0] == expected,
        "failures": failures,
    }


def run_websocket(url: str, samples: int = 5, timeout: float = 5.0) -> dict[str, Any]:
    """Optional public WSS handshake only; reject credential-bearing URLs."""
    from websockets.sync.client import connect

    parts = urlsplit(url)
    if parts.scheme != "wss" or parts.query or parts.username or parts.password:
        raise ValueError("WebSocket URL must be wss without query or userinfo")
    if samples < 1 or timeout <= 0:
        raise ValueError("invalid WebSocket probe settings")
    measurements: list[float] = []
    observations: list[dict[str, Any]] = []
    failures = 0
    for index in range(samples):
        try:
            start = time.perf_counter_ns()
            with connect(url, open_timeout=timeout, close_timeout=timeout):
                elapsed = (time.perf_counter_ns() - start) / 1_000_000
                measurements.append(elapsed)
                observations.append(
                    {
                        "sample": index,
                        "at": datetime.now(UTC).isoformat(),
                        "elapsed_ms": elapsed,
                    }
                )
        except Exception:
            failures += 1
            observations.append(
                {
                    "sample": index,
                    "at": datetime.now(UTC).isoformat(),
                    "error": "transport",
                }
            )
    return {
        "attempts": samples,
        "latency_ms": distribution(measurements),
        "failures": failures,
        "target_host": parts.hostname,
        "observations": observations,
    }


async def run_feed(
    duration_seconds: float,
    stall_seconds: float,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Optional read-only Dhan depth sample using the existing feed adapter."""
    from quant.domain import DepthEvent
    from quant.feeds.dhan_depth import DhanDepthFeed, DhanDepthSettings

    if duration_seconds <= 0 or stall_seconds <= 0:
        raise ValueError("feed durations must be positive")
    settings = DhanDepthSettings.from_env(os.environ if environ is None else environ)
    probe = _ConnectionProbe()
    feed = DhanDepthFeed(settings, observer=probe)
    interarrival: list[float] = []
    per_instrument: dict[str, dict[str, Any]] = {}
    last_received: dict[str, int] = {}
    stalls = 0
    deadline = time.monotonic() + duration_seconds
    started_ns = time.perf_counter_ns()
    observations: list[dict[str, Any]] = []
    exchange_timestamp_count = 0
    sequence_count = 0
    full_book_count = 0
    iterator = cast(AsyncGenerator[DepthEvent, None], feed.__aiter__())
    try:
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                event = await asyncio.wait_for(anext(iterator), timeout=remaining)
            except TimeoutError:
                break
            key = f"{event.exchange_segment}:{event.instrument_id}"
            received_ns = time.perf_counter_ns()
            observations.append(
                {
                    "instrument": key,
                    "at": datetime.now(UTC).isoformat(),
                    "elapsed_from_start_ms": (received_ns - started_ns) / 1_000_000,
                    "bid_levels": len(event.bid_levels),
                    "ask_levels": len(event.ask_levels),
                    "exchange_timestamp_present": event.exchange_timestamp is not None,
                    "sequence_present": event.sequence_number is not None,
                }
            )
            exchange_timestamp_count += event.exchange_timestamp is not None
            sequence_count += event.sequence_number is not None
            full_book_count += (
                len(event.bid_levels) == 20 and len(event.ask_levels) == 20
            )
            item = per_instrument.setdefault(
                key, {"events": 0, "interarrival_ms": [], "stalls": 0}
            )
            item["events"] += 1
            if key in last_received:
                gap = (received_ns - last_received[key]) / 1_000_000_000
                interarrival.append(gap * 1_000)
                item["interarrival_ms"].append(gap * 1_000)
                stalls += gap >= stall_seconds
                item["stalls"] += gap >= stall_seconds
            last_received[key] = received_ns
    finally:
        await feed.aclose()
        await iterator.aclose()
    end_ns = time.perf_counter_ns()
    for key, last_ns in last_received.items():
        gap = (end_ns - last_ns) / 1_000_000_000
        per_instrument[key]["tail_gap_ms"] = gap * 1_000
        if gap >= stall_seconds:
            stalls += 1
            per_instrument[key]["stalls"] += 1
    counters = feed.counters
    configured = {
        f"{item.exchange_segment}:{item.instrument_id}" for item in settings.instruments
    }
    instruments = {
        key: {
            "events": item["events"],
            "events_per_second": item["events"] / duration_seconds,
            "packets": probe.packet_counts.get(key, 0),
            "packets_per_second": probe.packet_counts.get(key, 0) / duration_seconds,
            "packet_interarrival_ms": distribution(
                probe.packet_interarrival_ms.get(key, [])
            ),
            "malformed_packets": probe.malformed_counts.get(key, 0),
            "interarrival_ms": distribution(item["interarrival_ms"]),
            "stalls_over_threshold": item["stalls"],
            "tail_gap_ms": item.get("tail_gap_ms"),
        }
        for key, item in per_instrument.items()
    }
    return {
        "duration_seconds": duration_seconds,
        "events": counters.depth_events,
        "messages": counters.messages,
        "configured_instruments": sorted(configured),
        "packet_observations": probe.packet_observations,
        "exchange_timestamp_coverage": (
            exchange_timestamp_count / counters.depth_events
            if counters.depth_events
            else None
        ),
        "sequence_coverage": (
            sequence_count / counters.depth_events if counters.depth_events else None
        ),
        "full_20x20_book_events": full_book_count,
        "book_semantics": (
            "Each source side packet contains 20 aggregate price levels; normalized "
            "events pair bid and ask sides. Individual add/cancel/trade events "
            "cannot be reconstructed from these aggregates."
        ),
        "websocket_first_connect_ms": probe.first_connect_ms,
        "websocket_connect_ms": distribution(probe.connect_ms),
        "connection_events": probe.connection_events,
        "disconnects": counters.disconnects,
        "reconnects": counters.reconnects,
        "malformed": counters.malformed_messages,
        "interarrival_ms": distribution(interarrival),
        "websocket_packet_interarrival_ms": distribution(
            [
                value
                for values in probe.packet_interarrival_ms.values()
                for value in values
            ]
        ),
        "stalls_over_threshold": stalls,
        "per_instrument": instruments,
        "missing_instruments": sorted(configured - instruments.keys()),
        "stall_threshold_seconds": stall_seconds,
        "packet_loss": None,
        "observations": observations,
        "note": (
            "Interarrival uses a monotonic clock. Packet loss requires a verified "
            "contiguous upstream sequence. Exchange time is not network latency."
        ),
    }
