"""Synthetic, offline tests for benchmark math, persistence, and ranking gates."""

from __future__ import annotations

import http.client
import json
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from infra.benchmark import network_bench
from infra.benchmark.common import distribution
from infra.benchmark.network_bench import run_websocket
from infra.benchmark.run_benchmark import (
    _write_result,
    compare,
    market_window,
    run,
    score_records,
)
from infra.benchmark.storage_bench import run_storage
from infra.benchmark.system_bench import _sustained_single_thread


def test_distribution_keeps_tail_and_empty() -> None:
    assert distribution([]) is None
    result = distribution([1.0, 2.0, 3.0, 4.0])
    assert result is not None
    assert result["count"] == 4
    assert result["p50"] == 2.5
    assert result["max"] == 4.0
    assert result["p99_9"] > result["p99"]


def test_storage_uses_temporary_file_and_reports_fsync(tmp_path: Path) -> None:
    result = run_storage(tmp_path, samples=8, record_bytes=128)
    assert result["write_ms"]["count"] == 8
    assert result["fsync_ms"]["count"] == 8
    assert result["append_bytes_per_second"] > 0
    assert result["interrupted_write_recovery"]["passed"] is True
    assert list(tmp_path.iterdir()) == []


def test_sustained_cpu_probe_reports_continuous_work() -> None:
    result = _sustained_single_thread(0.005, b"test")
    assert result["sha256_per_second"]["count"] >= 1
    assert result["duration_seconds"] >= 0.005


def test_market_windows_are_nse_local() -> None:
    assert market_window(datetime(2026, 10, 12, 3, 10, tzinfo=UTC)) == "preopen"
    assert market_window(datetime(2026, 10, 12, 3, 50, tzinfo=UTC)) == "open"
    assert market_window(datetime(2026, 10, 12, 6, 0, tzinfo=UTC)) == "midday"
    assert market_window(datetime(2026, 10, 12, 9, 45, tzinfo=UTC)) == "close"
    assert market_window(datetime(2026, 10, 11, 6, 0, tzinfo=UTC)) == "outside"


def test_websocket_rejects_credential_bearing_url() -> None:
    with pytest.raises(ValueError, match="without query"):
        run_websocket("wss://example.com/feed?access_token=secret")


def test_live_run_fails_before_probes_without_dhan_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "infra.benchmark.run_benchmark.platform.system", lambda: "Linux"
    )
    monkeypatch.delenv("DHAN_CLIENT_ID", raising=False)
    monkeypatch.delenv("DHAN_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("QUANT_DEPTH_INSTRUMENTS", raising=False)
    args = Namespace(
        monthly_cost=None,
        output=tmp_path / "result.json",
        journal_dir=tmp_path,
        expected_egress_ip=None,
        feed_seconds=180,
    )
    with pytest.raises(ValueError, match="Dhan client ID and access token"):
        run(args)
    assert not args.output.exists()


def test_packet_probe_tracks_instrument_interarrival() -> None:
    probe = network_bench._ConnectionProbe()
    now = datetime.now(UTC)
    probe.packet_received(("NSE_EQ", "123"), now)
    probe.packet_received(("NSE_EQ", "123"), now)
    probe.malformed(("NSE_EQ", "123"), "invalid_length", now)
    assert probe.packet_counts["NSE_EQ:123"] == 2
    assert len(probe.packet_interarrival_ms["NSE_EQ:123"]) == 1
    assert probe.malformed_counts["NSE_EQ:123"] == 1


def test_network_probe_reuses_https_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    connections: list[object] = []

    class FakeConnection:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            connections.append(self)
            self.connect_count = 0

        def close(self) -> None:
            pass

    monkeypatch.setattr(network_bench, "_TrackingHTTPSConnection", FakeConnection)
    monkeypatch.setattr(network_bench, "_probe_dns", lambda: 1.0)
    monkeypatch.setattr(network_bench, "_probe_tcp", lambda timeout: 2.0)
    monkeypatch.setattr(network_bench, "_probe_tls", lambda timeout, context: 3.0)

    def probe(connection: FakeConnection) -> tuple[float, int]:
        connection.connect_count = 1
        return 4.0, 404

    monkeypatch.setattr(network_bench, "_probe_https", probe)
    result = network_bench.run_network(samples=3, interval=0)
    assert len(connections) == 1
    assert result["latency_ms"]["https"]["count"] == 3
    assert result["https_status_codes"] == {"404": 3}
    assert result["https_new_connections"] == 1
    assert result["https_reused_requests"] == 2
    assert result["packet_loss"] is None


def test_network_probe_detects_reconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeConnection:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            self.connect_count = 0

        def close(self) -> None:
            pass

    def reconnect(connection: FakeConnection) -> tuple[float, int]:
        connection.connect_count += 1
        return 5.0, 200

    monkeypatch.setattr(network_bench, "_TrackingHTTPSConnection", FakeConnection)
    monkeypatch.setattr(network_bench, "_probe_dns", lambda: 1.0)
    monkeypatch.setattr(network_bench, "_probe_tcp", lambda timeout: 2.0)
    monkeypatch.setattr(network_bench, "_probe_tls", lambda timeout, context: 3.0)
    monkeypatch.setattr(network_bench, "_probe_https", reconnect)
    result = network_bench.run_network(samples=3, interval=0)
    assert result["https_new_connections"] == 3
    assert result["https_reused_requests"] == 0
    assert result["https_connection_reused"] is False


def test_authenticated_profile_discards_body_and_never_serializes_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, str, dict[str, str]]] = []

    class FakeResponse:
        status = 200

        def read(self) -> bytes:
            return b'{"private":"account"}'

    class FakeConnection:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            pass

        def request(self, method: str, path: str, headers: dict[str, str]) -> None:
            requests.append((method, path, headers))

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            pass

    monkeypatch.setattr(http.client, "HTTPSConnection", FakeConnection)
    monkeypatch.setattr(network_bench.time, "sleep", lambda seconds: None)
    result = network_bench.run_authenticated_api("top-secret", samples=2)
    assert result["latency_ms"]["count"] == 2
    assert all(
        method == "GET" and path == "/v2/profile" for method, path, _ in requests
    )
    assert "top-secret" not in json.dumps(result)
    assert "private" not in json.dumps(result)
    with pytest.raises(ValueError, match="at least 1s"):
        network_bench.run_authenticated_api("top-secret", interval=0.1)


def test_authenticated_profile_stops_after_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class FakeResponse:
        status = 401

        def read(self) -> bytes:
            return b"invalid token"

    class FakeConnection:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            pass

        def request(self, method: str, path: str, headers: dict[str, str]) -> None:
            nonlocal calls
            calls += 1

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            pass

    monkeypatch.setattr(http.client, "HTTPSConnection", FakeConnection)
    result = network_bench.run_authenticated_api("token", samples=30)
    assert calls == 1
    assert result["attempts"] == result["failures"] == 1
    assert "invalid token" not in json.dumps(result)


def test_egress_ip_must_match_reserved_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        status = 200

        def read(self, size: int) -> bytes:
            return b"203.0.113.10\n"

    class FakeConnection:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            pass

        def request(self, method: str, path: str) -> None:
            pass

        def getresponse(self) -> FakeResponse:
            return FakeResponse()

        def close(self) -> None:
            pass

    monkeypatch.setattr(http.client, "HTTPSConnection", FakeConnection)
    result = network_bench.run_egress_ipv4("203.0.113.10")
    assert result["matches_reserved_ip"] is True
    assert network_bench.run_egress_ipv4("203.0.113.11")["matches_reserved_ip"] is False


def _record(day: str, window: str) -> dict[str, object]:
    return {
        "market_date": day,
        "market_window": window,
        "candidate": {
            "provider": "test",
            "region": "Mumbai",
            "plan": "small",
            "monthly_cost": 45.0,
            "currency": "USD",
            "static_ip_supported": True,
        },
        "network": {
            "attempts": 30,
            "failures": {"dns": 0, "tcp": 0, "tls": 0, "https": 0},
            "latency_ms": {
                "tcp": {"p99": 15.0},
                "tls": {"p99": 20.0},
                "https": {"p50": 20.0, "p99": 40.0},
            },
        },
        "authenticated_api": {"attempts": 30, "latency_ms": {"p99": 40.0}},
        "egress_ipv4": {"matches_reserved_ip": True},
        "feed": {
            "interarrival_ms": {"p99": 20.0, "p99_9": 30.0},
            "websocket_first_connect_ms": 100.0,
            "events": 300,
            "disconnects": 0,
            "stalls_over_threshold": 0,
        },
        "system": {
            "scheduler_jitter_ms": {"p99": 1.0},
            "cpu_steal_fraction": 0.0,
            "cgroup_throttle_events": 0,
            "sustained_single_thread": {"sha256_per_second": {"p50": 1000.0}},
            "clock": {"ntp_synchronized": True, "ntp_offset_seconds": 0.001},
        },
        "storage": {
            "fsync_ms": {"p99": 2.0},
            "interrupted_write_recovery": {"passed": True},
        },
    }


def test_ranking_requires_three_sessions_and_measured_feed() -> None:
    rows = [_record("2026-10-12", "open")]
    assert score_records(rows)["eligible"] is False
    rows = [
        _record(day, window)
        for day in ("2026-10-12", "2026-10-13", "2026-10-14")
        for window in ("open", "midday", "close")
    ]
    scored = score_records(rows)
    assert scored["eligible"] is True
    assert set(scored["scores"]) == {"collector", "executor"}
    rows[0]["feed"] = {}  # type: ignore[index]
    assert score_records(rows)["eligible"] is False


def test_json_parquet_output_and_comparison(tmp_path: Path) -> None:
    row = _record("2026-10-12", "open")
    json_path = tmp_path / "probe.json"
    parquet_path = tmp_path / "probe.parquet"
    _write_result(json_path, row)
    _write_result(parquet_path, row)
    assert json.loads(json_path.read_text()) == row
    import pyarrow.parquet as pq

    assert json.loads(pq.read_table(parquet_path)["result_json"][0].as_py()) == row
    assert compare([json_path])["rankings"] == {"collector": [], "executor": []}
    with pytest.raises(FileExistsError):
        _write_result(json_path, row)
