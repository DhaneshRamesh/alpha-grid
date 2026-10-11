"""Synthetic, offline tests for benchmark math, persistence, and ranking gates."""

from __future__ import annotations

import json
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
    score_records,
)
from infra.benchmark.storage_bench import run_storage


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
    assert list(tmp_path.iterdir()) == []


def test_market_windows_are_nse_local() -> None:
    assert market_window(datetime(2026, 10, 12, 3, 10, tzinfo=UTC)) == "preopen"
    assert market_window(datetime(2026, 10, 12, 3, 50, tzinfo=UTC)) == "open"
    assert market_window(datetime(2026, 10, 12, 6, 0, tzinfo=UTC)) == "midday"
    assert market_window(datetime(2026, 10, 12, 9, 45, tzinfo=UTC)) == "close"
    assert market_window(datetime(2026, 10, 11, 6, 0, tzinfo=UTC)) == "outside"


def test_websocket_rejects_credential_bearing_url() -> None:
    with pytest.raises(ValueError, match="without query"):
        run_websocket("wss://example.com/feed?access_token=secret")


def test_network_probe_reuses_https_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    connections: list[object] = []

    class FakeConnection:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            connections.append(self)

        def close(self) -> None:
            pass

    monkeypatch.setattr(network_bench.http.client, "HTTPSConnection", FakeConnection)
    monkeypatch.setattr(network_bench, "_probe_dns", lambda: 1.0)
    monkeypatch.setattr(network_bench, "_probe_tcp", lambda timeout: 2.0)
    monkeypatch.setattr(network_bench, "_probe_tls", lambda timeout, context: 3.0)
    monkeypatch.setattr(network_bench, "_probe_https", lambda connection: (4.0, 404))
    result = network_bench.run_network(samples=3, interval=0)
    assert len(connections) == 1
    assert result["latency_ms"]["https"]["count"] == 3
    assert result["https_status_codes"] == {"404": 3}
    assert result["packet_loss"] is None


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
                "https": {"p99": 40.0},
            },
        },
        "feed": {
            "exchange_to_receive_ms": {"p99": 20.0, "p99_9": 30.0},
            "websocket_first_connect_ms": 100.0,
            "events": 300,
            "disconnects": 0,
            "stalls_over_threshold": 0,
        },
        "system": {
            "scheduler_jitter_ms": {"p99": 1.0},
            "clock": {"ntp_synchronized": True, "ntp_offset_seconds": 0.001},
        },
        "storage": {"fsync_ms": {"p99": 2.0}},
    }


def test_ranking_requires_two_days_all_windows_and_measured_feed() -> None:
    rows = [_record("2026-10-12", "open")]
    assert score_records(rows)["eligible"] is False
    rows = [
        _record(day, window)
        for day in ("2026-10-12", "2026-10-13")
        for window in ("preopen", "open", "midday", "close")
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
