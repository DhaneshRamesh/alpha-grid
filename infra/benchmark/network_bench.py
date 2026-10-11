"""Read-only DNS, TCP, TLS, HTTP, WebSocket, and Dhan feed probes."""

from __future__ import annotations

import asyncio
import http.client
import os
import socket
import ssl
import time
from collections.abc import AsyncGenerator, Callable, Mapping
from datetime import datetime
from typing import Any, cast
from urllib.parse import urlsplit

from infra.benchmark.common import distribution

HOST = "api.dhan.co"
PORT = 443


class _ConnectionProbe:
    def __init__(self) -> None:
        self.started_ns = time.perf_counter_ns()
        self.first_connect_ms: float | None = None

    def connection_changed(
        self, connected: bool, at: datetime, reason: str = "transport"
    ) -> None:
        if connected and self.first_connect_ms is None:
            self.first_connect_ms = (
                time.perf_counter_ns() - self.started_ns
            ) / 1_000_000

    def packet_received(self, key: tuple[str, str], at: datetime) -> None:
        pass

    def malformed(self, key: tuple[str, str] | None, reason: str, at: datetime) -> None:
        pass


def _timed_call(action: Callable[[], object]) -> float:
    start = time.perf_counter_ns()
    action()
    return (time.perf_counter_ns() - start) / 1_000_000


def _probe_dns() -> float:
    return _timed_call(lambda: socket.getaddrinfo(HOST, PORT, type=socket.SOCK_STREAM))


def _probe_tcp(timeout: float) -> float:
    start = time.perf_counter_ns()
    with socket.create_connection((HOST, PORT), timeout=timeout):
        return (time.perf_counter_ns() - start) / 1_000_000


def _probe_tls(timeout: float, context: ssl.SSLContext) -> float:
    with socket.create_connection((HOST, PORT), timeout=timeout) as raw:
        raw.settimeout(timeout)
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
    samples: int = 30, timeout: float = 5.0, interval: float = 0.2
) -> dict[str, Any]:
    """Each phase is isolated; HTTPS reuses one connection per probe run."""
    if samples < 1 or timeout <= 0 or interval < 0:
        raise ValueError("invalid network probe settings")
    data: dict[str, list[float]] = {name: [] for name in ("dns", "tcp", "tls", "https")}
    failures: dict[str, int] = {name: 0 for name in data}
    status_codes: dict[str, int] = {}
    context = ssl.create_default_context()
    connection: http.client.HTTPSConnection | None = None
    try:
        for _ in range(samples):
            for name, action in (
                ("dns", _probe_dns),
                ("tcp", lambda: _probe_tcp(timeout)),
                ("tls", lambda: _probe_tls(timeout, context)),
            ):
                try:
                    data[name].append(action())
                except (OSError, TimeoutError):
                    failures[name] += 1
            try:
                if connection is None:
                    connection = http.client.HTTPSConnection(
                        HOST, PORT, timeout=timeout
                    )
                elapsed, status = _probe_https(connection)
                data["https"].append(elapsed)
                status_codes[str(status)] = status_codes.get(str(status), 0) + 1
            except (OSError, TimeoutError, http.client.HTTPException):
                failures["https"] += 1
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
        "https_connection_reused": True,
        "attempts": samples,
        "latency_ms": {name: distribution(values) for name, values in data.items()},
        "failures": failures,
        "https_status_codes": status_codes,
        "packet_loss": None,  # TCP failures are not a packet-loss measurement.
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
    failures = 0
    for _ in range(samples):
        try:
            start = time.perf_counter_ns()
            with connect(url, open_timeout=timeout, close_timeout=timeout):
                measurements.append((time.perf_counter_ns() - start) / 1_000_000)
        except Exception:
            failures += 1
    return {
        "attempts": samples,
        "latency_ms": distribution(measurements),
        "failures": failures,
        "target_host": parts.hostname,
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
    delays: list[float] = []
    interarrival: list[float] = []
    per_instrument: dict[str, dict[str, Any]] = {}
    last_received: dict[str, datetime] = {}
    stalls = 0
    deadline = time.monotonic() + duration_seconds
    iterator = cast(AsyncGenerator[DepthEvent, None], feed.__aiter__())
    try:
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                event = await asyncio.wait_for(anext(iterator), timeout=remaining)
            except TimeoutError:
                break
            key = f"{event.exchange_segment}:{event.instrument_id}"
            item = per_instrument.setdefault(
                key, {"events": 0, "interarrival_ms": [], "delay_ms": [], "stalls": 0}
            )
            item["events"] += 1
            if key in last_received:
                gap = (event.receive_timestamp - last_received[key]).total_seconds()
                interarrival.append(gap * 1_000)
                item["interarrival_ms"].append(gap * 1_000)
                stalls += gap >= stall_seconds
                item["stalls"] += gap >= stall_seconds
            last_received[key] = event.receive_timestamp
            if event.exchange_timestamp is not None:
                delay = (
                    event.receive_timestamp - event.exchange_timestamp
                ).total_seconds() * 1_000
                delays.append(delay)
                item["delay_ms"].append(delay)
    finally:
        await feed.aclose()
        await iterator.aclose()
    counters = feed.counters
    configured = {
        f"{item.exchange_segment}:{item.instrument_id}" for item in settings.instruments
    }
    instruments = {
        key: {
            "events": item["events"],
            "interarrival_ms": distribution(item["interarrival_ms"]),
            "exchange_to_receive_ms": distribution(item["delay_ms"]),
            "stalls_over_threshold": item["stalls"],
        }
        for key, item in per_instrument.items()
    }
    return {
        "duration_seconds": duration_seconds,
        "events": counters.depth_events,
        "messages": counters.messages,
        "websocket_first_connect_ms": probe.first_connect_ms,
        "disconnects": counters.disconnects,
        "reconnects": counters.reconnects,
        "malformed": counters.malformed_messages,
        "interarrival_ms": distribution(interarrival),
        "exchange_to_receive_ms": distribution(delays),
        "exchange_timestamp_coverage": len(delays) / counters.depth_events
        if counters.depth_events
        else None,
        "stalls_over_threshold": stalls,
        "per_instrument": instruments,
        "missing_instruments": sorted(configured - instruments.keys()),
        "stall_threshold_seconds": stall_seconds,
        "packet_loss": None,
        "note": "Packet loss requires a verified contiguous upstream sequence.",
    }
