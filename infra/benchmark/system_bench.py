"""Linux host, scheduling, contention, and sustained single-thread probes."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import resource
import subprocess
import time
from pathlib import Path
from typing import Any

from infra.benchmark.common import distribution


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return None


def _fields(text: str | None) -> dict[str, str]:
    if text is None:
        return {}
    return dict(line.split(":", 1) for line in text.splitlines() if ":" in line)


def _cpu_times() -> tuple[int, int] | None:
    text = _read("/proc/stat")
    if text is None:
        return None
    values = [int(value) for value in text.splitlines()[0].split()[1:]]
    return sum(values), values[7] if len(values) > 7 else 0


def _cgroup_throttle() -> int | None:
    text = _read("/sys/fs/cgroup/cpu.stat")
    if text is None:
        return None
    match = re.search(r"^nr_throttled (\d+)$", text, re.MULTILINE)
    return int(match.group(1)) if match else None


def _clock_status() -> dict[str, Any]:
    status: dict[str, Any] = {"ntp_synchronized": None, "ntp_offset_seconds": None}
    try:
        result = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            status["ntp_synchronized"] = result.stdout.strip() == "yes"
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        result = subprocess.run(
            ["chronyc", "tracking"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if result.returncode == 0:
            match = re.search(
                r"System time\s*:\s*([+-]?[\d.]+) seconds (fast|slow)",
                result.stdout,
            )
            if match:
                offset = float(match.group(1))
                status["ntp_offset_seconds"] = (
                    offset if match.group(2) == "fast" else -offset
                )
    except (OSError, subprocess.TimeoutExpired):
        pass
    return status


def run_system(
    *, duration_seconds: float = 10.0, scheduler_period_ms: float = 10.0
) -> dict[str, Any]:
    if duration_seconds <= 0 or scheduler_period_ms <= 0:
        raise ValueError("system probe durations must be positive")
    cpu_info = _fields(_read("/proc/cpuinfo"))
    memory = _fields(_read("/proc/meminfo"))
    before_cpu = _cpu_times()
    before_throttle = _cgroup_throttle()
    before_usage = resource.getrusage(resource.RUSAGE_SELF)
    start = time.perf_counter()
    deadline = start + duration_seconds
    next_tick = start
    jitter_ms: list[float] = []
    throughput: list[float] = []
    clock_mhz: list[float] = []
    payload = b"quant-benchmark" * 256
    while time.perf_counter() < deadline:
        next_tick += scheduler_period_ms / 1000
        time.sleep(max(0.0, next_tick - time.perf_counter()))
        tick = time.perf_counter()
        jitter_ms.append(max(0.0, tick - next_tick) * 1000)
        work_start = time.perf_counter()
        for _ in range(1000):
            hashlib.sha256(payload).digest()
        elapsed = time.perf_counter() - work_start
        throughput.append(1000 / elapsed)
        if len(throughput) % 100 == 0:
            observed = _fields(_read("/proc/cpuinfo")).get("cpu MHz")
            if observed is not None:
                clock_mhz.append(float(observed))
        if next_tick < time.perf_counter():
            next_tick = time.perf_counter()
    after_usage = resource.getrusage(resource.RUSAGE_SELF)
    after_cpu = _cpu_times()
    after_throttle = _cgroup_throttle()
    steal_fraction = None
    if before_cpu is not None and after_cpu is not None:
        total = after_cpu[0] - before_cpu[0]
        if total > 0:
            steal_fraction = (after_cpu[1] - before_cpu[1]) / total
    uptime = _read("/proc/uptime")
    return {
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "cpu_model": cpu_info.get("model name", "").strip() or None,
        "cpu_mhz": float(cpu_info["cpu MHz"]) if "cpu MHz" in cpu_info else None,
        "reported_clock_mhz": distribution(clock_mhz),
        "hypervisor_visible": "hypervisor" in cpu_info.get("flags", ""),
        "logical_cpus": os.cpu_count(),
        "load_average": os.getloadavg(),
        "mem_available_kb": int(memory["MemAvailable"].strip().split()[0])
        if "MemAvailable" in memory
        else None,
        "uptime_seconds": float(uptime.split()[0]) if uptime else None,
        "cpu_steal_fraction": steal_fraction,
        "cgroup_throttle_events": (
            after_throttle - before_throttle
            if before_throttle is not None and after_throttle is not None
            else None
        ),
        "voluntary_context_switches": after_usage.ru_nvcsw - before_usage.ru_nvcsw,
        "involuntary_context_switches": after_usage.ru_nivcsw - before_usage.ru_nivcsw,
        "scheduler_jitter_ms": distribution(jitter_ms),
        "sha256_per_second": distribution(throughput),
        "clock": _clock_status(),
        "duration_seconds": time.perf_counter() - start,
    }
