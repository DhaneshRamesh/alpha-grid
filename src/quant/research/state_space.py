"""Small offline state estimators with explicit time and sampling assumptions."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class LocalLevelState:
    mean: float
    variance: float
    timestamp_ns: int


def kalman_local_level(
    previous: LocalLevelState,
    *,
    observation: float,
    timestamp_ns: int,
    process_var_per_second: float,
    observation_var: float,
) -> LocalLevelState:
    elapsed = (timestamp_ns - previous.timestamp_ns) / 1e9
    if (
        elapsed <= 0
        or min(process_var_per_second, observation_var, previous.variance) < 0
    ):
        raise ValueError("time must increase and variances must be nonnegative")
    predicted = previous.variance + process_var_per_second * elapsed
    gain = (
        predicted / (predicted + observation_var) if predicted + observation_var else 0
    )
    return LocalLevelState(
        previous.mean + gain * (observation - previous.mean),
        (1 - gain) * predicted,
        timestamp_ns,
    )


@dataclass(frozen=True, slots=True)
class DynamicRegressionState:
    coefficient: float
    variance: float
    timestamp_ns: int


def kalman_dynamic_regression(
    previous: DynamicRegressionState,
    *,
    x: float,
    y: float,
    timestamp_ns: int,
    process_var_per_second: float,
    observation_var: float,
) -> DynamicRegressionState:
    elapsed = (timestamp_ns - previous.timestamp_ns) / 1e9
    if (
        elapsed <= 0
        or min(previous.variance, process_var_per_second, observation_var) < 0
    ):
        raise ValueError("invalid dynamic regression time/variance")
    predicted = previous.variance + process_var_per_second * elapsed
    denominator = x * x * predicted + observation_var
    gain = predicted * x / denominator if denominator else 0
    return DynamicRegressionState(
        previous.coefficient + gain * (y - x * previous.coefficient),
        predicted * (1 - gain * x),
        timestamp_ns,
    )


@dataclass(frozen=True, slots=True)
class OUEstimate:
    phi: float
    mean_reversion_per_second: float
    mean_reversion_se_per_second: float
    equilibrium: float
    innovation_std: float
    sample_interval_seconds: float
    observations: int
    status: str = "TRAINED_UNVALIDATED"


def estimate_regular_ou(
    residuals: list[float],
    timestamps_ns: list[int],
    *,
    max_relative_interval_error: float = 0.01,
) -> OUEstimate:
    if len(residuals) < 5 or len(residuals) != len(timestamps_ns):
        raise ValueError("insufficient paired residuals")
    intervals = np.diff(np.asarray(timestamps_ns, dtype=np.float64)) / 1e9
    dt = float(np.median(intervals))
    if dt <= 0 or np.max(np.abs(intervals - dt)) > dt * max_relative_interval_error:
        raise ValueError("irregular quote samples require an irregular-time model")
    x = np.asarray(residuals[:-1], dtype=np.float64)
    y = np.asarray(residuals[1:], dtype=np.float64)
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("nonfinite residual")
    design = np.column_stack([np.ones(len(x)), x])
    intercept, phi = np.linalg.lstsq(design, y, rcond=None)[0]
    if not 0 < phi < 1:
        raise ValueError("sample does not identify stationary mean reversion")
    innovations = y - (intercept + phi * x)
    residual_variance = float(np.sum(innovations**2) / (len(x) - 2))
    centered_sum = float(np.sum((x - np.mean(x)) ** 2))
    if centered_sum <= 0:
        raise ValueError("constant residual cannot identify mean reversion")
    phi_se = math.sqrt(residual_variance / centered_sum)
    return OUEstimate(
        float(phi),
        -math.log(float(phi)) / dt,
        phi_se / (float(phi) * dt),
        float(intercept / (1 - phi)),
        float(np.std(innovations, ddof=2)),
        dt,
        len(residuals),
    )


def cusum_change_points(
    values: list[float], *, reference_mean: float, allowance: float, threshold: float
) -> tuple[int, ...]:
    if allowance < 0 or threshold <= 0:
        raise ValueError("invalid CUSUM settings")
    positive = negative = 0.0
    found = []
    for index, value in enumerate(values):
        positive = max(0.0, positive + value - reference_mean - allowance)
        negative = min(0.0, negative + value - reference_mean + allowance)
        if positive >= threshold or negative <= -threshold:
            found.append(index)
            positive = negative = 0.0
    return tuple(found)


def volatility_regime(
    past_returns: list[float], *, threshold: float, minimum_samples: int = 10
) -> str:
    if threshold <= 0 or minimum_samples < 2:
        raise ValueError("invalid regime design")
    if len(past_returns) < minimum_samples:
        return "UNKNOWN"
    sample = np.asarray(past_returns, dtype=np.float64)
    if not np.isfinite(sample).all():
        return "UNKNOWN"
    return "HIGH_VOL" if float(np.std(sample, ddof=1)) >= threshold else "LOW_VOL"
