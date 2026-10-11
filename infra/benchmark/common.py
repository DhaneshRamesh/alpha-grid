"""Small, deterministic helpers shared by benchmark probes."""

from __future__ import annotations

import math
from collections.abc import Sequence


def distribution(values: Sequence[float]) -> dict[str, float | int] | None:
    """Return interpolated percentiles without discarding tail observations."""
    if not values:
        return None
    ordered = sorted(values)
    if any(not math.isfinite(value) for value in ordered):
        raise ValueError("benchmark samples must be finite")

    def percentile(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    return {
        "count": len(ordered),
        "p50": percentile(0.5),
        "p90": percentile(0.9),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "p99_9": percentile(0.999),
        "max": ordered[-1],
    }
