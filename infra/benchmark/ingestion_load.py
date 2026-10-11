"""Offline 20-level feed-to-journal capacity and durability benchmark.

The bounded queue models an explicit feed handoff; the deployed collector is
currently synchronous. A rejected enqueue is fatal and visible. Only records
covered by a successful fsync count as durable.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import platform
import queue
import random
import resource
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from infra.benchmark.common import distribution
from quant.apps.collect_depth import _json_value
from quant.domain import DepthEvent, DepthLevel, Instrument
from quant.health.monitor import FeedHealthMonitor, HealthThresholds
from quant.storage.journal import EventJournal, FsyncPolicy, JournalSettings

RATES = (100, 500, 1_000, 5_000, 10_000)
POLICIES = ("every_event", "periodic", "group_bytes")
BENCHMARK_VERSION = "ingestion-0.5.0"


@dataclass(frozen=True, slots=True)
class LoadSettings:
    rate: int
    duration_seconds: float
    policy: str
    queue_limit: int = 2_000
    instrument_count: int = 20
    sync_interval_ms: float = 50.0
    group_bytes: int = 262_144
    pipeline_mode: str = "collector"

    def __post_init__(self) -> None:
        if (
            self.rate < 1
            or self.duration_seconds <= 0
            or self.policy not in POLICIES
            or self.queue_limit < 1
            or not 1 <= self.instrument_count <= 50
            or self.sync_interval_ms <= 0
            or self.group_bytes < 1
            or self.pipeline_mode not in {"collector", "journal_only"}
        ):
            raise ValueError("invalid ingestion load settings")


_STEP = Decimal("0.05")
_BIDS = tuple(
    DepthLevel(Decimal("2500.00") - _STEP * index, 100 + index * 7, 2 + index)
    for index in range(20)
)
_ASKS = tuple(
    DepthLevel(Decimal("2500.05") + _STEP * index, 95 + index * 5, 2 + index)
    for index in range(20)
)


def _event(index: int, instrument_count: int) -> DepthEvent:
    """Round-robin full snapshots; sequence is monotone per instrument."""
    return DepthEvent(
        "NSE",
        "NSE_EQ",
        str(10_000 + index % instrument_count),
        None,
        datetime.now(UTC),
        tuple(
            DepthLevel(level.price, level.quantity, level.order_count)
            for level in _BIDS
        ),
        tuple(
            DepthLevel(level.price, level.quantity, level.order_count)
            for level in _ASKS
        ),
        index // instrument_count + 1,
    )


def _peak_rss_bytes() -> int:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if platform.system() == "Darwin" else value * 1024


def _current_rss_bytes() -> int | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def run_case(root: Path, settings: LoadSettings) -> dict[str, Any]:
    """Run one rate/policy on a new session; preserve an explicit overload status."""
    root.mkdir(parents=True, exist_ok=True)
    session = f"load-{settings.policy}-{settings.rate}-{time.time_ns()}"
    policy = (
        FsyncPolicy.ALWAYS if settings.policy == "every_event" else FsyncPolicy.ON_CLOSE
    )
    journal_settings = JournalSettings(root, session, policy)
    items: queue.Queue[tuple[DepthEvent, int]] = queue.Queue(settings.queue_limit)
    producer_done = threading.Event()
    failures: list[str] = []
    write_ms: list[float] = []
    fsync_ms: list[float] = []
    durable_ms: list[float] = []
    queue_depths: list[float] = []
    metrics: dict[str, int] = {
        "journal_appended": 0,
        "durable": 0,
        "max_uncommitted_events": 0,
        "fsync_count": 0,
        "journal_bytes": 0,
        "downstream_published": 0,
        "published_before_durable": 0,
    }
    interval_ns = int(settings.sync_interval_ms * 1_000_000)

    def consume() -> None:
        pending: list[int] = []
        pending_bytes = 0
        first_pending_ns = 0
        downstream = None
        try:
            with EventJournal(journal_settings) as journal:
                monitor = None
                if settings.pipeline_mode == "collector":
                    monitor = FeedHealthMonitor(
                        tuple(
                            Instrument(
                                "NSE", "NSE_EQ", str(10_000 + index), f"S{index}"
                            )
                            for index in range(settings.instrument_count)
                        ),
                        HealthThresholds(),
                        session_id=session,
                    )
                    monitor.connection_changed(True, datetime.now(UTC), "synthetic")
                    downstream = open(os.devnull, "w", encoding="utf-8")

                def commit() -> None:
                    nonlocal pending_bytes, first_pending_ns
                    if not pending:
                        return
                    elapsed = journal.sync()
                    committed_ns = time.perf_counter_ns()
                    fsync_ms.append(elapsed / 1_000_000)
                    durable_ms.extend(
                        (committed_ns - arrived_ns) / 1_000_000
                        for arrived_ns in pending
                    )
                    metrics["durable"] += len(pending)
                    pending.clear()
                    pending_bytes = 0
                    first_pending_ns = 0

                while not producer_done.is_set() or not items.empty():
                    try:
                        event, arrived_ns = items.get(timeout=0.005)
                    except queue.Empty:
                        if (
                            pending
                            and time.perf_counter_ns() - first_pending_ns >= interval_ns
                        ):
                            commit()
                        continue
                    before_bytes = journal.appended_bytes
                    before_syncs = journal.fsync_count
                    started_ns = time.perf_counter_ns()
                    journal.append(event)
                    written_ns = time.perf_counter_ns()
                    if (
                        settings.policy != "every_event"
                        and journal.fsync_count > before_syncs
                        and journal.last_fsync_ns is not None
                    ):
                        fsync_ms.append(journal.last_fsync_ns / 1_000_000)
                    write_ms.append((written_ns - started_ns) / 1_000_000)
                    metrics["journal_appended"] += 1
                    if settings.policy == "every_event":
                        if journal.last_fsync_ns is not None:
                            fsync_ms.append(journal.last_fsync_ns / 1_000_000)
                        metrics["durable"] += 1
                        durable_ms.append((written_ns - arrived_ns) / 1_000_000)
                    else:
                        pending.append(arrived_ns)
                        pending_bytes += journal.appended_bytes - before_bytes
                        if first_pending_ns == 0:
                            first_pending_ns = arrived_ns
                        metrics["max_uncommitted_events"] = max(
                            metrics["max_uncommitted_events"], len(pending)
                        )
                    if monitor is not None and downstream is not None:
                        monitor.observe_event(event)
                        downstream.write(
                            json.dumps(asdict(event), default=_json_value) + "\n"
                        )
                        downstream.flush()
                        metrics["downstream_published"] += 1
                        metrics["published_before_durable"] += int(bool(pending))
                    if pending and (
                        (
                            settings.policy == "group_bytes"
                            and pending_bytes >= settings.group_bytes
                        )
                        or time.perf_counter_ns() - first_pending_ns >= interval_ns
                    ):
                        commit()
                commit()
                metrics["fsync_count"] = journal.fsync_count
                metrics["journal_bytes"] = journal.appended_bytes
        except Exception as exc:
            failures.append(
                f"{type(exc).__name__}:{exc.errno}"
                if isinstance(exc, OSError)
                else type(exc).__name__
            )
        finally:
            if downstream is not None:
                downstream.close()

    started_at = datetime.now(UTC)
    rss_before = _peak_rss_bytes()
    current_rss_before = _current_rss_bytes()
    cpu_before = time.process_time_ns()
    worker = threading.Thread(target=consume, name="journal-writer")
    worker.start()
    start_ns = time.perf_counter_ns()
    deadline_ns = start_ns + int(settings.duration_seconds * 1_000_000_000)
    accepted = rejected = max_depth = attempted = max_not_durable = 0
    max_schedule_lag_ns = 0
    queue_depth_at_input_end = 0
    producer_end_ns = start_ns
    next_index = 0
    try:
        while True:
            scheduled_ns = start_ns + next_index * 1_000_000_000 // settings.rate
            if scheduled_ns >= deadline_ns or failures:
                break
            remaining = scheduled_ns - time.perf_counter_ns()
            if remaining > 0:
                time.sleep(remaining / 1_000_000_000)
            max_schedule_lag_ns = max(
                max_schedule_lag_ns, time.perf_counter_ns() - scheduled_ns
            )
            event = _event(next_index, settings.instrument_count)
            attempted += 1
            try:
                items.put_nowait((event, time.perf_counter_ns()))
            except queue.Full:
                rejected += 1
                failures.append("QueueFull")
                break
            accepted += 1
            max_not_durable = max(max_not_durable, accepted - metrics["durable"])
            depth = items.qsize()
            max_depth = max(max_depth, depth)
            queue_depths.append(float(depth))
            next_index += 1
    finally:
        queue_depth_at_input_end = items.qsize()
        producer_end_ns = time.perf_counter_ns()
        producer_done.set()
        worker.join()
    end_ns = time.perf_counter_ns()
    wall_seconds = (end_ns - start_ns) / 1_000_000_000
    cpu_seconds = (time.process_time_ns() - cpu_before) / 1_000_000_000
    peak_rss = _peak_rss_bytes()
    current_rss_after = _current_rss_bytes()
    pending_at_end = accepted - metrics["durable"]
    journal_files = list((root / f"session={session}").glob("date=*/events.qj"))
    disk_bytes = sum(path.stat().st_size for path in journal_files)
    status = "failed" if failures or pending_at_end else "complete"
    recovery_start_ns = time.perf_counter_ns()
    try:
        with EventJournal(JournalSettings(root, session, FsyncPolicy.NEVER)) as check:
            recovery_records = check.recovery.records
            recovered_tail_bytes = check.recovery.truncated_bytes
    except OSError as exc:
        recovery_records = None
        recovered_tail_bytes = None
        failures.append(f"Recovery{type(exc).__name__}")
        status = "failed"
    if status == "complete" and recovery_records != metrics["durable"]:
        failures.append("DurableRecoveryMismatch")
        status = "failed"
    recovery_ms = (time.perf_counter_ns() - recovery_start_ns) / 1_000_000
    return {
        "schema_version": 1,
        "benchmark_version": BENCHMARK_VERSION,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "settings": {
            "rate": settings.rate,
            "duration_seconds": settings.duration_seconds,
            "policy": settings.policy,
            "queue_limit": settings.queue_limit,
            "instrument_count": settings.instrument_count,
            "sync_interval_ms": settings.sync_interval_ms,
            "group_bytes": settings.group_bytes,
            "pipeline_mode": settings.pipeline_mode,
        },
        "session_id": session,
        "status": status,
        "failure_types": failures,
        "scheduled_events": math.ceil(settings.duration_seconds * settings.rate),
        "attempted_events": attempted,
        "unproduced_due_to_failure": max(
            0, math.ceil(settings.duration_seconds * settings.rate) - attempted
        ),
        "accepted_in_memory": accepted,
        "journal_appended": metrics["journal_appended"],
        "downstream_published": metrics["downstream_published"],
        "published_before_durable": metrics["published_before_durable"],
        "durable_events": metrics["durable"],
        "accepted_but_not_durable": pending_at_end,
        "rejected_events": rejected,
        "dropped_events": rejected,
        "max_queue_depth": max_depth,
        "queue_depth_at_input_end": queue_depth_at_input_end,
        "queue_depth": distribution(queue_depths),
        "final_queue_depth": items.qsize(),
        "max_uncommitted_events": metrics["max_uncommitted_events"],
        "max_accepted_not_durable_events": max_not_durable,
        "elapsed_seconds": wall_seconds,
        "producer_elapsed_seconds": (producer_end_ns - start_ns) / 1_000_000_000,
        "max_schedule_lag_ms": max_schedule_lag_ns / 1_000_000,
        "actual_source_rate": accepted / ((producer_end_ns - start_ns) / 1_000_000_000),
        "drain_seconds": (end_ns - producer_end_ns) / 1_000_000_000,
        "actual_accept_rate": accepted / wall_seconds,
        "journal_append_rate": metrics["journal_appended"] / wall_seconds,
        "durable_rate": metrics["durable"] / wall_seconds,
        "process_cpu_percent_one_core": 100 * cpu_seconds / wall_seconds,
        "peak_rss_bytes": peak_rss,
        "peak_rss_increase_bytes": max(0, peak_rss - rss_before),
        "rss_before_bytes": current_rss_before,
        "rss_after_bytes": current_rss_after,
        "write_ms": distribution(write_ms),
        "fsync_ms": distribution(fsync_ms),
        "event_to_durable_ms": distribution(durable_ms),
        "raw_write_ms": write_ms,
        "raw_fsync_ms": fsync_ms,
        "raw_event_to_durable_ms": durable_ms,
        "fsync_count": metrics["fsync_count"],
        "journal_bytes": metrics["journal_bytes"],
        "disk_bytes": disk_bytes,
        "recovery_records": recovery_records,
        "recovery_truncated_bytes": recovered_tail_bytes,
        "recovery_ms": recovery_ms,
        "disk_bytes_per_million_events": (
            disk_bytes * 1_000_000 / metrics["journal_appended"]
            if metrics["journal_appended"]
            else None
        ),
        "configured_max_uncommitted_ms": (
            0.0 if settings.policy == "every_event" else settings.sync_interval_ms
        ),
        "max_observed_event_to_durable_ms": max(durable_ms, default=0.0),
        "note": "Configured interval is a target, not a hard bound during OS stalls.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration-seconds", type=float, default=10.0)
    parser.add_argument("--rates", type=int, nargs="+", default=list(RATES))
    parser.add_argument("--queue-limit", type=int, default=2_000)
    parser.add_argument("--instrument-count", type=int, default=20)
    parser.add_argument("--sync-interval-ms", type=float, default=50.0)
    parser.add_argument("--group-bytes", type=int, default=262_144)
    parser.add_argument(
        "--pipeline-mode", choices=("collector", "journal_only"), default="collector"
    )
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261011)
    parser.add_argument("--provider", default="local")
    parser.add_argument("--region", default="local")
    parser.add_argument("--plan", default="unknown")
    args = parser.parse_args()
    # State calculations remain enabled; suppress initial synthetic status logs.
    logging.getLogger("quant.health.monitor").setLevel(logging.CRITICAL)
    trial_file = args.output.with_suffix(args.output.suffix + ".trials.jsonl")
    if args.output.exists() or trial_file.exists():
        parser.error("output already exists")
    if args.repetitions < 1:
        parser.error("repetitions must be positive")
    cases = [
        (trial, policy, rate)
        for trial in range(args.repetitions)
        for policy in POLICIES
        for rate in args.rates
    ]
    random.Random(args.seed).shuffle(cases)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with trial_file.open("x", encoding="utf-8") as raw:
        for trial, policy, rate in cases:
            result = run_case(
                args.root,
                LoadSettings(
                    rate,
                    args.duration_seconds,
                    policy,
                    args.queue_limit,
                    args.instrument_count,
                    args.sync_interval_ms,
                    args.group_bytes,
                    args.pipeline_mode,
                ),
            )
            result["trial"] = trial
            result["candidate"] = {
                "provider": args.provider,
                "region": args.region,
                "plan": args.plan,
            }
            raw.write(json.dumps(result, sort_keys=True, allow_nan=False) + "\n")
            raw.flush()
            os.fsync(raw.fileno())
            # Keep the multi-case index small; exact samples are in the JSONL file.
            results.append(
                {
                    key: value
                    for key, value in result.items()
                    if not key.startswith("raw_")
                }
            )
    output = {
        "benchmark_version": BENCHMARK_VERSION,
        "host": platform.node(),
        "python": platform.python_version(),
        "candidate": {
            "provider": args.provider,
            "region": args.region,
            "plan": args.plan,
        },
        "seed": args.seed,
        "results": results,
    }
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(output, stream, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    if any(row["status"] != "complete" for row in results):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
