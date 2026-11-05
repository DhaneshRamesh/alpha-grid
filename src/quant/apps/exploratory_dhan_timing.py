"""Offline timing-sensitivity study on pinned Dhan bars; no causal or trade output."""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import cast

import numpy as np
import numpy.typing as npt

from quant.acquisition.contracts import HistoricalBar
from quant.acquisition.dhan import _private_directory
from quant.apps.descriptive_dhan_opportunities import (
    ROOT,
    load_verified_bars,
    validated_sessions,
)

DELAYS_SECONDS = (60, 120, 300)
HORIZONS = (30, 60, "remaining_session")
FEATURES = (
    "return_1_bps",
    "return_5_bps",
    "return_15_bps",
    "volatility_15_bps",
    "prior_session_relative_volume",
    "remaining_minutes_at_decision",
)
TRAIN_END = "2026-08-03"  # 63 May--July sessions; never fit on later days.
VALIDATION_END = "2026-10-01"  # August--September, already exploratory.
EMBARGO_DAY = "2026-07-31"  # One complete session between fit and evaluation.
RIDGE_PENALTY = 1.0
PRIMARY_COST_BPS = 50.0
SENSITIVITY_COST_BPS = 25.0
BOOTSTRAP_SEED = 20261102
FloatArray = npt.NDArray[np.float64]
SessionArrays = tuple[FloatArray, FloatArray, FloatArray, FloatArray]


def _float_array(values: list[int]) -> FloatArray:
    return np.asarray(values, dtype=np.float64)


def _session_arrays(
    sessions: dict[str, tuple[HistoricalBar, ...]],
) -> dict[str, SessionArrays]:
    result: dict[str, SessionArrays] = {}
    for day, bars in sessions.items():
        result[day] = (
            _float_array([bar.close_nanos for bar in bars]),
            _float_array([bar.high_nanos for bar in bars]),
            _float_array([bar.low_nanos for bar in bars]),
            _float_array([bar.volume for bar in bars]),
        )
    return result


def _rows(
    arrays: dict[str, SessionArrays],
    delay: int,
    horizon: int | str,
) -> tuple[FloatArray, FloatArray, FloatArray, list[str]]:
    """Features end at bar i; price references begin after assumed decision time."""
    prior_volume_sum = np.zeros(375, dtype=np.float64)
    prior_volume_count = np.zeros(375, dtype=np.float64)
    all_x: list[list[float]] = []
    all_y: list[float] = []
    all_adverse: list[tuple[float, float]] = []
    all_days: list[str] = []
    train_arrays = [(day, arrays[day]) for day in sorted(arrays) if day < TRAIN_END]
    fixed_sum = np.zeros(375, dtype=np.float64)
    fixed_count = np.zeros(375, dtype=np.float64)
    for day, (_, _, _, volume) in train_arrays:
        if day == EMBARGO_DAY:
            continue
        fixed_sum[: len(volume)] += volume
        fixed_count[: len(volume)] += 1
    for day, (close, high, low, volume) in sorted(arrays.items()):
        if day >= VALIDATION_END:
            continue  # October was inspected descriptively; no fitting or score.
        reference_sum = prior_volume_sum if day < TRAIN_END else fixed_sum
        reference_count = prior_volume_count if day < TRAIN_END else fixed_count
        n = len(close)
        for i in range(15, n):
            decision_boundary = i + 1 + delay // 60
            entry_index = decision_boundary  # first full future bar close, +1 minute
            exit_index = (
                decision_boundary + horizon - 1 if isinstance(horizon, int) else n - 1
            )
            if (
                exit_index >= n
                or exit_index <= entry_index
                or reference_count[i] == 0
                or reference_sum[i] <= 0
            ):
                continue
            recent = np.diff(close[i - 15 : i + 1]) / close[i - 15 : i] * 10_000
            feature = [
                (close[i] / close[i - 1] - 1) * 10_000,
                (close[i] / close[i - 5] - 1) * 10_000,
                (close[i] / close[i - 15] - 1) * 10_000,
                float(np.std(recent)),
                volume[i] / (reference_sum[i] / reference_count[i]),
                n - decision_boundary - 1,
            ]
            entry = close[entry_index]
            y = (close[exit_index] / entry - 1) * 10_000
            # Direction-specific adverse ranges are selected at evaluation time.
            long_adverse = (
                np.min(low[entry_index + 1 : exit_index + 1]) / entry - 1
            ) * 10_000
            short_adverse = (
                entry / np.max(high[entry_index + 1 : exit_index + 1]) - 1
            ) * 10_000
            all_x.append(feature)
            all_y.append(y)
            all_adverse.append((float(long_adverse), float(short_adverse)))
            all_days.append(day)
        if day < TRAIN_END:
            prior_volume_sum[:n] += volume
            prior_volume_count[:n] += 1
    return (
        np.asarray(all_x, dtype=np.float64),
        np.asarray(all_y, dtype=np.float64),
        np.asarray(all_adverse, dtype=np.float64),
        all_days,
    )


def _fit_ridge(
    train_x: npt.NDArray[np.float64], train_y: npt.NDArray[np.float64]
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    center = train_x.mean(axis=0)
    scale = train_x.std(axis=0)
    scale[scale == 0] = 1.0
    design = np.column_stack((np.ones(len(train_x)), (train_x - center) / scale))
    penalty = np.eye(design.shape[1]) * RIDGE_PENALTY
    penalty[0, 0] = 0
    beta = np.linalg.solve(design.T @ design + penalty, design.T @ train_y)
    return center, scale, beta


def _predict(
    x: npt.NDArray[np.float64],
    center: npt.NDArray[np.float64],
    scale: npt.NDArray[np.float64],
    beta: npt.NDArray[np.float64],
) -> npt.NDArray[np.float64]:
    return np.column_stack((np.ones(len(x)), (x - center) / scale)) @ beta


def _calibration(
    training_scores: npt.NDArray[np.float64],
    training_y: npt.NDArray[np.float64],
    evaluation_scores: npt.NDArray[np.float64],
    evaluation_y: npt.NDArray[np.float64],
) -> dict[str, object]:
    edges = np.quantile(training_scores, [0.2, 0.4, 0.6, 0.8])
    train_bin = np.searchsorted(edges, training_scores, side="right")
    eval_bin = np.searchsorted(edges, evaluation_scores, side="right")
    probability = np.asarray(
        [
            (1 + np.count_nonzero((train_bin == b) & (training_y > 0)))
            / (2 + np.count_nonzero(train_bin == b))
            for b in range(5)
        ]
    )
    predicted = probability[eval_bin]
    actual = (evaluation_y > 0).astype(np.float64)
    baseline = (1 + np.count_nonzero(training_y > 0)) / (2 + len(training_y))
    bins = [
        {
            "count": int(np.count_nonzero(eval_bin == b)),
            "predicted_positive_fraction": float(probability[b]),
            "observed_positive_fraction": (
                float(np.mean(actual[eval_bin == b])) if np.any(eval_bin == b) else None
            ),
        }
        for b in range(5)
    ]
    return {
        "brier": float(np.mean((predicted - actual) ** 2)),
        "unconditional_brier": float(np.mean((baseline - actual) ** 2)),
        "bins": bins,
        "calibration_fit": "train-only empirical score quintiles, Laplace smoothing",
    }


def _economic(
    score: npt.NDArray[np.float64],
    y: npt.NDArray[np.float64],
    adverse: npt.NDArray[np.float64],
    days: list[str],
    cost: float,
    unconditional_mean: float,
    seed: int,
) -> dict[str, object]:
    action = np.where(score > cost, 1, np.where(score < -cost, -1, 0))
    baseline_action = (
        1 if unconditional_mean > cost else -1 if unconditional_mean < -cost else 0
    )
    net = np.where(action != 0, action * y - cost, 0.0)
    baseline = np.where(baseline_action != 0, baseline_action * y - cost, 0.0)
    per_day: dict[str, list[float]] = defaultdict(list)
    per_day_baseline: dict[str, list[float]] = defaultdict(list)
    for day, value, simple in zip(days, net, baseline, strict=True):
        per_day[day].append(float(value))
        per_day_baseline[day].append(float(simple))
    day_values = np.asarray([np.mean(per_day[d]) for d in sorted(per_day)])
    day_baseline = np.asarray([np.mean(per_day_baseline[d]) for d in sorted(per_day)])
    contrast = day_values - day_baseline
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(contrast), size=(2000, len(contrast)))
    interval = np.quantile(contrast[draws].mean(axis=1), [0.025, 0.975])
    traded = action != 0
    adverse_for_action = np.where(action > 0, adverse[:, 0], adverse[:, 1])
    ordered_days = sorted(per_day)
    blocks = [ordered_days[i : i + 14] for i in range(0, len(ordered_days), 14)]
    return {
        "assumed_round_trip_cost_bps": cost,
        "reference_decisions": len(y),
        "selected_decisions": int(np.count_nonzero(traded)),
        "selection_fraction": float(np.mean(traded)),
        "model_mean_session_reference_net_bps": float(np.mean(day_values)),
        "no_condition_mean_session_reference_net_bps": float(np.mean(day_baseline)),
        "no_trade_mean_session_reference_net_bps": 0.0,
        "model_minus_simple_session_bootstrap_95pct_bps": [float(v) for v in interval],
        "negative_session_fraction": float(np.mean(day_values < 0)),
        "worst_session_mean_reference_net_bps": float(np.min(day_values)),
        "mean_selected_adverse_range_bps": (
            float(np.mean(adverse_for_action[traded])) if np.any(traded) else None
        ),
        "chronological_14_session_blocks": [
            {
                "first_session": block[0],
                "last_session": block[-1],
                "sessions": len(block),
                "mean_session_reference_net_bps": float(
                    np.mean([np.mean(per_day[d]) for d in block])
                ),
            }
            for block in blocks
        ],
        "interpretation": "hypothetical close references, not fills or executable P&L",
    }


def study(root: Path = ROOT) -> dict[str, object]:
    bars, sources = load_verified_bars(root)
    sessions = validated_sessions(bars)
    arrays = _session_arrays(sessions)
    if EMBARGO_DAY not in arrays:
        raise ValueError("predeclared July 31 embargo session missing")
    results: dict[str, object] = {}
    for delay in DELAYS_SECONDS:
        horizon_results: dict[str, object] = {}
        for horizon in HORIZONS:
            x, y, adverse, days = _rows(arrays, delay, horizon)
            train = np.asarray([d < TRAIN_END and d != EMBARGO_DAY for d in days])
            evaluation = np.asarray([TRAIN_END <= d < VALIDATION_END for d in days])
            if np.count_nonzero(train) < 100 or np.count_nonzero(evaluation) < 100:
                raise ValueError("insufficient chronological exploratory coverage")
            center, scale, beta = _fit_ridge(x[train], y[train])
            train_score = _predict(x[train], center, scale, beta)
            score = _predict(x[evaluation], center, scale, beta)
            eval_days = [
                d for d, included in zip(days, evaluation, strict=True) if included
            ]
            primary = _economic(
                score,
                y[evaluation],
                adverse[evaluation],
                eval_days,
                PRIMARY_COST_BPS,
                float(np.mean(y[train])),
                BOOTSTRAP_SEED + delay,
            )
            sensitivity = _economic(
                score,
                y[evaluation],
                adverse[evaluation],
                eval_days,
                SENSITIVITY_COST_BPS,
                float(np.mean(y[train])),
                BOOTSTRAP_SEED + delay + 1,
            )
            horizon_results[str(horizon)] = {
                "train_rows": int(np.count_nonzero(train)),
                "train_sessions": len(
                    set(d for d in days if d < TRAIN_END and d != EMBARGO_DAY)
                ),
                "evaluation_rows": int(np.count_nonzero(evaluation)),
                "evaluation_sessions": len(set(eval_days)),
                "ridge_penalty": RIDGE_PENALTY,
                "fitted_exploratory_model": {
                    "feature_center": center.tolist(),
                    "feature_scale": scale.tolist(),
                    "coefficients_intercept_first": beta.tolist(),
                    "status": "EXPLORATORY_ONLY_NO_PROMOTION",
                },
                "evaluation_mae_bps": float(np.mean(np.abs(score - y[evaluation]))),
                "simple_mean_mae_bps": float(
                    np.mean(np.abs(np.mean(y[train]) - y[evaluation]))
                ),
                "positive_probability_calibration": _calibration(
                    train_score, y[train], score, y[evaluation]
                ),
                "primary_50bps": primary,
                "sensitivity_25bps": sensitivity,
                "incremental_economic_value_demonstrated": bool(
                    cast(int, primary["selected_decisions"]) > 0
                    and cast(
                        list[float],
                        primary["model_minus_simple_session_bootstrap_95pct_bps"],
                    )[0]
                    > 0
                    and cast(float, primary["model_mean_session_reference_net_bps"]) > 0
                ),
            }
        results[str(delay)] = horizon_results
    return {
        "schema_version": "exploratory-dhan-timing-v1",
        "status": "EXPLORATORY_ASSUMED_TIMING_NOT_CAUSALLY_ELIGIBLE",
        "source": sources,
        "source_bars": len(bars),
        "source_sessions": len(sessions),
        "instrument": "NSE_EQ:2885:RELIANCE",
        "source_integrity": "VERIFIED_FOR_PINNED_RESPONSES",
        "historical_identity": "UNVERIFIED_FOR_FULL_RANGE",
        "cas_applicability": "UNVERIFIED_FOR_RELIANCE_FULL_RANGE",
        "corporate_action_adjustment": "UNVERIFIED",
        "bar_label_hypothesis": "START_OF_MINUTE_UNVERIFIED",
        "assumed_post_close_availability_seconds": list(DELAYS_SECONDS),
        "decision_rule": (
            "bar label + 60 seconds + assumed delay; features through that bar only; "
            "entry reference is first full bar close after decision; outcomes are "
            "decision+30m, decision+60m, or final observed continuous close"
        ),
        "features": list(FEATURES),
        "train_sessions": (
            "May-July 2026 except July 31 one-session embargo; "
            "prior-session relative volume only"
        ),
        "evaluation_sessions": "August-September 2026, chronological untouched by fit",
        "excluded": (
            "October inspected descriptively; November frozen experiment; "
            "December unseen"
        ),
        "costs": "50 and 25 bps assumed round trip, not observed execution costs",
        "model": (
            "train-standardized ridge regression, fixed penalty 1; "
            "unconditional mean and no-trade baselines"
        ),
        "causal_eligibility": "BLOCKED",
        "model_promotion": "BLOCKED",
        "paper_order_connection": "NONE",
        "trading": "NO_TRADE",
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = study()
    _private_directory(args.output.parent)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {"output": str(args.output), "status": result["status"]}, sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
