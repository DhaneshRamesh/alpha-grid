"""Session-block uncertainty; no independent-tick fiction."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class BootstrapSummary:
    independent_sessions: int
    mean_net_nanos: float
    confidence_low_nanos: float
    confidence_high_nanos: float
    expected_shortfall_nanos: float
    max_drawdown_nanos: float
    seed: int
    repetitions: int


def _drawdown(values: np.ndarray[tuple[int], np.dtype[np.float64]]) -> float:
    equity = np.r_[0.0, np.cumsum(values)]
    peak = np.maximum.accumulate(equity)
    return float(np.max(peak - equity))


def bootstrap_session_net(
    session_net_nanos: dict[str, float],
    *,
    repetitions: int = 2000,
    seed: int = 1,
    min_sessions: int = 10,
) -> BootstrapSummary | None:
    if repetitions < 100 or min_sessions < 2:
        raise ValueError("insufficient bootstrap design")
    if len(session_net_nanos) < min_sessions:
        return None
    values = np.asarray(
        [session_net_nanos[key] for key in sorted(session_net_nanos)], dtype=np.float64
    )
    if not np.isfinite(values).all():
        raise ValueError("invalid net outcomes")
    rng = np.random.default_rng(seed)
    sampled = values[rng.integers(0, len(values), size=(repetitions, len(values)))]
    means = np.mean(sampled, axis=1)
    session_es = float(
        np.mean(np.sort(values)[: max(1, int(np.ceil(len(values) * 0.05)))])
    )
    return BootstrapSummary(
        len(values),
        float(np.mean(values)),
        float(np.quantile(means, 0.025)),
        float(np.quantile(means, 0.975)),
        session_es,
        _drawdown(values),
        seed,
        repetitions,
    )
