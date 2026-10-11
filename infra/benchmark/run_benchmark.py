"""Run read-only host probes or compare complete multi-day measurements."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from infra.benchmark.network_bench import (
    run_authenticated_api,
    run_egress_ipv4,
    run_feed,
    run_network,
    run_websocket,
)
from infra.benchmark.storage_bench import run_storage
from infra.benchmark.system_bench import run_system

IST = ZoneInfo("Asia/Kolkata")
WINDOWS = {
    "preopen": (time(8, 30), time(9, 15)),
    "open": (time(9, 15), time(9, 45)),
    "midday": (time(10, 0), time(14, 45)),
    "close": (time(14, 45), time(15, 30)),
}
WEIGHTS = {
    "collector": {
        "network_latency": 18,
        "network_jitter": 16,
        "cpu_consistency": 10,
        "storage_persistence": 20,
        "reliability": 18,
        "static_ip": 8,
        "monthly_cost": 10,
    },
    "executor": {
        "network_latency": 28,
        "network_jitter": 20,
        "cpu_consistency": 18,
        "storage_persistence": 7,
        "reliability": 17,
        "static_ip": 7,
        "monthly_cost": 3,
    },
}


def market_window(when: datetime) -> str:
    local = when.astimezone(IST)
    if local.weekday() >= 5:
        return "outside"
    current = local.time().replace(tzinfo=None)
    for name, (start, end) in WINDOWS.items():
        if start <= current < end:
            return name
    return "outside"


def _flatten(value: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, item in value.items():
        name = f"{prefix}__{key}" if prefix else key
        if isinstance(item, dict):
            output.update(_flatten(item, name))
        elif isinstance(item, (str, int, float, bool)) or item is None:
            output[name] = item
    return output


def _write_result(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix not in {".json", ".parquet"}:
        raise ValueError("output must end in .json or .parquet")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        if path.suffix == ".json":
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, sort_keys=True, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        else:
            os.close(descriptor)
            import pyarrow as pa
            import pyarrow.parquet as pq

            row = _flatten(payload)
            row["result_json"] = json.dumps(payload, sort_keys=True, allow_nan=False)
            pq.write_table(pa.Table.from_pylist([row]), temporary, compression="zstd")
            file_descriptor = os.open(temporary, os.O_RDONLY)
            try:
                os.fsync(file_descriptor)
            finally:
                os.close(file_descriptor)
        os.link(temporary, path)  # Atomic create; never overwrite an earlier run.
        directory_descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        os.unlink(temporary)


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = datetime.now(UTC)
    if platform.system() != "Linux":
        raise RuntimeError("benchmarks must run on a Linux candidate server")
    if args.monthly_cost is not None and args.monthly_cost < 0:
        raise ValueError("monthly cost must be nonnegative")
    result: dict[str, Any] = {
        "schema_version": 2,
        "benchmark_version": "0.2.0",
        "started_at": started.isoformat(),
        "market_date": started.astimezone(IST).date().isoformat(),
        "market_window": market_window(started),
        "candidate": {
            "provider": args.provider,
            "region": args.region,
            "plan": args.plan,
            "monthly_cost": args.monthly_cost,
            "currency": args.currency,
            "static_ip_supported": args.static_ip_supported,
        },
        "network": run_network(args.network_samples, args.timeout, args.interval),
        "system": run_system(duration_seconds=args.cpu_seconds),
        "storage": run_storage(args.journal_dir, samples=args.storage_samples),
        "egress_ipv4": run_egress_ipv4(args.expected_egress_ip, timeout=args.timeout),
    }
    token = os.environ.get("DHAN_ACCESS_TOKEN")
    if token:
        result["authenticated_api"] = run_authenticated_api(
            token,
            samples=args.api_samples,
            timeout=args.timeout,
            interval=args.api_interval,
        )
    if args.ws_url:
        result["websocket"] = run_websocket(args.ws_url, timeout=args.timeout)
    if args.feed_seconds:
        try:
            result["feed"] = asyncio.run(
                run_feed(args.feed_seconds, args.stall_seconds)
            )
        except Exception as exc:
            result["feed"] = {"error_type": type(exc).__name__}
    result["finished_at"] = datetime.now(UTC).isoformat()
    _write_result(args.output, result)
    return result


def _metric(record: Mapping[str, Any], *keys: str) -> float | None:
    item: Any = record
    for key in keys:
        if not isinstance(item, dict):
            return None
        item = item.get(key)
    if isinstance(item, (int, float)) and math.isfinite(item):
        return float(item)
    return None


def _quality(value: float, good: float, bad: float) -> float:
    return max(0.0, min(100.0, 100 * (bad - value) / (bad - good)))


def score_records(
    records: Sequence[Mapping[str, Any]],
    *,
    cost_range: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Withhold scores until every candidate has comparable repeated sessions."""
    if not records:
        return {"eligible": False, "reason": "no measurements"}
    days = {str(row.get("market_date")) for row in records}
    windows = {str(row.get("market_window")) for row in records}
    required_windows = {"open", "midday", "close"}
    if len(days) < 3 or any(
        not required_windows.issubset(
            {
                str(row.get("market_window"))
                for row in records
                if row.get("market_date") == day
            }
        )
        for day in days
    ):
        return {
            "eligible": False,
            "reason": "need open, midday and close on 3 trading sessions",
        }
    first = records[0].get("candidate")
    if not isinstance(first, dict):
        return {"eligible": False, "reason": "missing candidate metadata"}
    if first.get("static_ip_supported") is not True or any(
        row.get("egress_ipv4", {}).get("matches_reserved_ip") is not True
        for row in records
    ):
        return {"eligible": False, "reason": "reserved outbound IPv4 unverified"}
    cost = first.get("monthly_cost")
    if not isinstance(cost, (int, float)) or cost < 0:
        return {"eligible": False, "reason": "all-in monthly cost unavailable"}
    if any(row.get("candidate") != first for row in records):
        return {"eligible": False, "reason": "candidate metadata changed"}
    paths = {
        "tcp": ("network", "latency_ms", "tcp", "p99"),
        "tls": ("network", "latency_ms", "tls", "p99"),
        "api": ("authenticated_api", "latency_ms", "p99"),
        "https_p50": ("network", "latency_ms", "https", "p50"),
        "https_p99": ("network", "latency_ms", "https", "p99"),
        "ws": ("feed", "websocket_first_connect_ms"),
        "cpu": ("system", "scheduler_jitter_ms", "p99"),
        "fsync": ("storage", "fsync_ms", "p99"),
    }
    metrics: dict[str, float] = {}
    for name, path in paths.items():
        values = [_metric(row, *path) for row in records]
        if any(value is None for value in values):
            return {"eligible": False, "reason": f"missing {name} p99 data"}
        metrics[name] = max(value for value in values if value is not None)
    if any(
        (_metric(row, "feed", "events") or 0) < 30
        or (_metric(row, "network", "attempts") or 0) < 30
        or (_metric(row, "authenticated_api", "attempts") or 0) < 30
        for row in records
    ):
        return {"eligible": False, "reason": "insufficient per-window samples"}
    if any(row.get("feed", {}).get("missing_instruments") for row in records):
        return {"eligible": False, "reason": "feed missing configured instruments"}
    if any(
        row.get("storage", {}).get("interrupted_write_recovery", {}).get("passed")
        is not True
        for row in records
    ):
        return {"eligible": False, "reason": "journal recovery unverified"}
    if any(
        _metric(row, "system", "cpu_steal_fraction") is None
        or _metric(row, "system", "cgroup_throttle_events") is None
        or _metric(row, "system", "sustained_single_thread", "sha256_per_second", "p50")
        is None
        for row in records
    ):
        return {"eligible": False, "reason": "CPU contention measurements missing"}
    attempts = sum(_metric(row, "network", "attempts") or 0 for row in records)
    failures = sum(
        sum(row.get("network", {}).get("failures", {}).values()) for row in records
    )
    disconnects = sum(_metric(row, "feed", "disconnects") or 0 for row in records)
    stalls = sum(_metric(row, "feed", "stalls_over_threshold") or 0 for row in records)
    api_failures = sum(
        _metric(row, "authenticated_api", "failures") or 0 for row in records
    )
    malformed = sum(_metric(row, "feed", "malformed") or 0 for row in records)
    api_attempts = sum(
        _metric(row, "authenticated_api", "attempts") or 0 for row in records
    )
    feed_messages = sum(_metric(row, "feed", "messages") or 0 for row in records)
    throttle = max(
        _metric(row, "system", "cgroup_throttle_events") or 0 for row in records
    )
    steal = max(_metric(row, "system", "cpu_steal_fraction") or 0 for row in records)
    dimensions = {
        "network_latency": min(
            _quality(metrics["tcp"], 5, 100),
            _quality(metrics["tls"], 5, 150),
            _quality(metrics["api"], 10, 250),
            _quality(metrics["ws"], 20, 2000),
        ),
        "network_jitter": _quality(metrics["https_p99"] - metrics["https_p50"], 1, 200),
        "cpu_consistency": min(
            _quality(metrics["cpu"], 0.1, 20),
            _quality(steal, 0, 0.05),
            _quality(throttle, 0, 100),
        ),
        "storage_persistence": _quality(metrics["fsync"], 0.5, 50),
        "reliability": _quality(
            failures / max(1, attempts * 4)
            + api_failures / max(1, api_attempts)
            + disconnects / len(records)
            + stalls / max(1, len(records) * 100)
            + malformed / max(1, feed_messages),
            0,
            0.1,
        ),
        "static_ip": 100.0,
        "monthly_cost": (
            50.0
            if cost_range is None or cost_range[0] == cost_range[1]
            else _quality(float(cost), cost_range[0], cost_range[1])
        ),
    }
    scores = {
        role: round(
            sum(dimensions[key] * weight for key, weight in weights.items()) / 100, 2
        )
        for role, weights in WEIGHTS.items()
    }
    return {
        "eligible": True,
        "scores": scores,
        "dimensions": dimensions,
        "worst_window_p99": metrics,
        "days": sorted(days),
        "windows": sorted(windows),
    }


def compare(paths: Sequence[Path]) -> dict[str, Any]:
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for path in paths:
        if path.suffix != ".json":
            raise ValueError("comparison inputs must be JSON files")
        row = json.loads(path.read_text(encoding="utf-8"))
        candidate = row["candidate"]
        key = (candidate["provider"], candidate["region"], candidate["plan"])
        groups[key].append(row)
    currencies = {
        str(row["candidate"].get("currency"))
        for rows in groups.values()
        for row in rows
    }
    if len(currencies) > 1:
        return {
            "candidates": {
                "/".join(key): score_records(rows) for key, rows in groups.items()
            },
            "rankings": {role: [] for role in WEIGHTS},
            "reason": "convert all-in costs to one currency before comparison",
        }
    costs = [
        float(row["candidate"]["monthly_cost"])
        for rows in groups.values()
        for row in rows
        if isinstance(row["candidate"].get("monthly_cost"), (int, float))
    ]
    cost_range = (min(costs), max(costs)) if costs else None
    scored = {
        "/".join(key): score_records(rows, cost_range=cost_range)
        for key, rows in groups.items()
    }
    rankings = {
        role: sorted(
            (
                (name, value["scores"][role])
                for name, value in scored.items()
                if value["eligible"]
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        for role in WEIGHTS
    }
    return {
        "candidates": scored,
        "rankings": rankings,
        "provisional": True,
        "note": "Scores are research aids, not proof of live execution latency.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run")
    runner.add_argument("--provider", required=True)
    runner.add_argument("--region", required=True)
    runner.add_argument("--plan", required=True)
    runner.add_argument("--output", required=True, type=Path)
    runner.add_argument("--journal-dir", required=True, type=Path)
    runner.add_argument("--monthly-cost", type=float)
    runner.add_argument("--currency", default="USD")
    runner.add_argument("--static-ip-supported", action="store_true")
    runner.add_argument(
        "--expected-egress-ip", help="Reserved IPv4 expected as outbound source"
    )
    runner.add_argument("--api-samples", type=int, default=30)
    runner.add_argument("--api-interval", type=float, default=1.0)
    runner.add_argument("--network-samples", type=int, default=30)
    runner.add_argument("--storage-samples", type=int, default=1000)
    runner.add_argument("--cpu-seconds", type=float, default=30.0)
    runner.add_argument("--timeout", type=float, default=5.0)
    runner.add_argument("--interval", type=float, default=0.2)
    runner.add_argument("--ws-url", help="Public WSS URL with no credentials")
    runner.add_argument("--feed-seconds", type=float, default=0.0)
    runner.add_argument("--stall-seconds", type=float, default=5.0)
    comparison = sub.add_parser("compare")
    comparison.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        result = run(args)
        print(
            json.dumps({"output": str(args.output), "window": result["market_window"]})
        )
    else:
        print(json.dumps(compare(args.inputs), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
