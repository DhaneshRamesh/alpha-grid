"""Frozen, offline RELIANCE 15-minute experiment; provisional research only."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import numpy.typing as npt

from quant.acquisition.contracts import CashInstrument, HistoricalBar
from quant.acquisition.dhan import HistoricalRequest, verify_saved_response
from quant.apps import noise_reduction

SPEC: dict[str, Any] = {
    "version": "reliance-15m-prospective-v1",
    "instrument": "NSE_EQ:2885:RELIANCE",
    "train": ["2026-04-01", "2026-08-01"],
    "development_only": ["2026-08-01", "2026-09-01"],
    "excluded_prior_holdout": ["2026-09-01", "2026-11-01"],
    "prospective": ["2026-11-01", "2026-12-01"],
    "continuous_ist": ["09:15", "15:15"],
    "feature_availability_minutes": 2,
    "reference_offsets_minutes": [3, 18],
    "features": ["normalized_r1", "normalized_r5", "log1p_relative_volume"],
    "volatility_lookback": 30,
    "volume_baseline_prior_sessions": 10,
    "preprocessing": "train_mean_and_population_std; zero_std_to_one",
    "classes": [
        "return_bps < -20.6",
        "-20.6 <= return_bps <= 20.6",
        "return_bps > 20.6",
    ],
    "model": "multinomial_softmax_reference_class_1",
    "l2_sum_nll_coefficient": 10.0,
    "penalty": "0.5*lambda*sum(non_intercept_coefficients_squared)",
    "null": "April-July training class frequencies",
    "brier": "sum_of_three_squared_probability_errors_per_example; equal_session_mean",
    "bootstrap": {"seed": 20261019, "repetitions": 5000, "percentiles": [2.5, 97.5]},
    "pass_rule": "model_minus_null_upper_95pct_bootstrap_bound < 0",
    "cost_bps": [15.6, 20.6, 30.6],
    "notional_inr": 5000,
    "economic_magnitude": "training_class_mean_reference_return_bps_only",
}

_MINUTE_NS = 60_000_000_000
_IST = ZoneInfo("Asia/Kolkata")
_NOVEMBER = HistoricalRequest(
    CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE"),
    date(2026, 11, 1),
    date(2026, 12, 1),
    1,
)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n"
    ).encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _code_hashes() -> dict[str, str]:
    return {
        path.name: _sha(path.read_bytes())
        for path in (Path(__file__), Path(noise_reduction.__file__))
    }


def _labels(returns: npt.NDArray[np.float64]) -> npt.NDArray[np.int64]:
    bps = returns * 10_000
    return np.asarray(
        np.where(bps < -20.6, 0, np.where(bps > 20.6, 2, 1)), dtype=np.int64
    )


def _softmax(logits: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    exp = np.exp(logits - np.max(logits, axis=1, keepdims=True))
    return np.asarray(exp / np.sum(exp, axis=1, keepdims=True), dtype=np.float64)


def _probabilities(
    x: npt.NDArray[np.float64], model: dict[str, Any]
) -> npt.NDArray[np.float64]:
    center = np.asarray(model["center"], dtype=np.float64)
    scale = np.asarray(model["scale"], dtype=np.float64)
    beta = np.asarray(model["beta"], dtype=np.float64)
    design = np.column_stack((np.ones(len(x)), (x - center) / scale))
    logits = np.column_stack((design @ beta[0], np.zeros(len(x)), design @ beta[1]))
    return _softmax(logits)


def _fit(x: npt.NDArray[np.float64], y: npt.NDArray[np.int64]) -> dict[str, Any]:
    if x.ndim != 2 or x.shape[1] != 3 or len(x) != len(y) or set(y) != {0, 1, 2}:
        raise ValueError("training requires three features and all three classes")
    center = np.mean(x, axis=0)
    scale = np.std(x, axis=0)
    scale[scale == 0] = 1
    design = np.column_stack((np.ones(len(x)), (x - center) / scale))
    beta = np.zeros((2, design.shape[1]), dtype=np.float64)
    penalty = SPEC["l2_sum_nll_coefficient"]
    regularizer = np.diag([0.0] + [penalty] * x.shape[1])
    row = np.arange(len(y))

    def objective(candidate: npt.NDArray[np.float64]) -> float:
        logits = np.column_stack(
            (design @ candidate[0], np.zeros(len(y)), design @ candidate[1])
        )
        prob = _softmax(logits)
        return float(
            -np.sum(np.log(np.maximum(prob[row, y], 1e-300)))
            + 0.5 * penalty * np.sum(candidate[:, 1:] ** 2)
        )

    for _ in range(50):
        p = _softmax(
            np.column_stack((design @ beta[0], np.zeros(len(y)), design @ beta[1]))
        )
        p0, p2 = p[:, 0], p[:, 2]
        g0 = design.T @ (p0 - (y == 0)) + regularizer @ beta[0]
        g2 = design.T @ (p2 - (y == 2)) + regularizer @ beta[1]
        h00 = design.T @ (design * (p0 * (1 - p0))[:, None]) + regularizer
        h22 = design.T @ (design * (p2 * (1 - p2))[:, None]) + regularizer
        h02 = design.T @ (design * (-p0 * p2)[:, None])
        hessian = np.block([[h00, h02], [h02.T, h22]])
        step = np.linalg.solve(hessian, np.concatenate((g0, g2))).reshape(beta.shape)
        if float(np.max(np.abs(step))) < 1e-10:
            break
        loss = objective(beta)
        for power in range(21):
            candidate = beta - step / (2**power)
            if objective(candidate) < loss:
                beta = candidate
                break
        else:
            raise ValueError("multinomial logistic line search failed")
    else:
        raise ValueError("multinomial logistic fit did not converge")
    return {"beta": beta.tolist(), "center": center.tolist(), "scale": scale.tolist()}


def _development(
    raw_root: Path,
) -> tuple[list[HistoricalBar], list[noise_reduction.Example], dict[str, str]]:
    bars, hashes = noise_reduction._load(raw_root)
    development = [bar for bar in bars if bar.trading_date < date(2026, 9, 1)]
    rows = noise_reduction._examples(development, through_august=False, target_end=18)
    train = [row for row in rows if row.session < "2026-08-01"]
    august = [row for row in rows if row.session >= "2026-08-01"]
    if (
        len({row.session for row in train}) != 73
        or len({row.session for row in august}) != 21
        or len(train) != 73 * 312
        or len(august) != 21 * 312
        or max(row.label_end_ns for row in train)
        >= min(row.available_ns for row in august)
    ):
        raise ValueError("frozen development coverage or split changed")
    return development, train, hashes


def freeze(raw_root: Path) -> dict[str, Any]:
    _, train, hashes = _development(raw_root)
    x = noise_reduction._matrix(train, "raw")
    returns = np.asarray([row.forward_return for row in train], dtype=np.float64)
    y = _labels(returns)
    counts = np.bincount(y, minlength=3)
    if np.any(counts == 0):
        raise ValueError("missing training outcome class")
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return {
        "status": "FROZEN_PROVISIONAL_NO_TRADING_OR_PROMOTION",
        "spec": SPEC,
        "spec_sha256": _sha(_canonical(SPEC)),
        "code_sha256": _code_hashes(),
        "git_commit_at_freeze": commit,
        "source_sha256": hashes,
        "train_examples": len(train),
        "train_sessions": 73,
        "august_diagnostic_sessions_not_fitted": 21,
        "model": _fit(x, y),
        "null_probabilities": (counts / len(y)).tolist(),
        "training_class_mean_return_bps": [
            float(np.mean(returns[y == k]) * 10_000) for k in range(3)
        ],
    }


def _session_scores(
    rows: list[noise_reduction.Example],
    probability: npt.NDArray[np.float64],
    null: npt.NDArray[np.float64],
) -> dict[str, Any]:
    y = _labels(np.asarray([row.forward_return for row in rows], dtype=np.float64))
    truth = np.eye(3, dtype=np.float64)[y]
    model_error = np.sum((probability - truth) ** 2, axis=1)
    null_error = np.sum((null - truth) ** 2, axis=1)
    by_session: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        by_session[row.session].append(index)
    sessions = {
        day: {
            "model_brier": float(np.mean(model_error[indices])),
            "null_brier": float(np.mean(null_error[indices])),
            "examples": len(indices),
        }
        for day, indices in sorted(by_session.items())
    }
    differences = np.asarray(
        [value["model_brier"] - value["null_brier"] for value in sessions.values()],
        dtype=np.float64,
    )
    rng = np.random.default_rng(SPEC["bootstrap"]["seed"])
    boot = np.mean(
        differences[
            rng.integers(
                0,
                len(differences),
                size=(SPEC["bootstrap"]["repetitions"], len(differences)),
            )
        ],
        axis=1,
    )
    interval = [float(np.quantile(boot, q)) for q in (0.025, 0.975)]
    mean = float(np.mean(differences))
    return {
        "class_counts_adverse_neutral_favorable": np.bincount(y, minlength=3).tolist(),
        "model_session_mean_brier": float(
            np.mean([v["model_brier"] for v in sessions.values()])
        ),
        "null_session_mean_brier": float(
            np.mean([v["null_brier"] for v in sessions.values()])
        ),
        "model_minus_null_session_mean": mean,
        "paired_session_bootstrap_95pct": interval,
        "conclusion": "IMPROVEMENT_SUPPORTED"
        if mean < 0 and interval[1] < 0
        else "INCONCLUSIVE_OR_WORSE",
        "by_session": sessions,
    }


def evaluate(
    raw_root: Path, frozen_path: Path, calendar_path: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    frozen_bytes = frozen_path.read_bytes()
    frozen: dict[str, Any] = json.loads(frozen_bytes)
    if (
        frozen.get("spec") != SPEC
        or frozen.get("spec_sha256") != _sha(_canonical(SPEC))
        or frozen.get("code_sha256") != _code_hashes()
    ):
        raise ValueError("frozen experiment specification or code changed")
    development, _, hashes = _development(raw_root)
    if hashes != frozen.get("source_sha256"):
        raise ValueError("frozen training source hashes changed")
    calendar_bytes = calendar_path.read_bytes()
    calendar: dict[str, Any] = json.loads(calendar_bytes)
    expected = calendar.get("sessions")
    if (
        calendar.get("verified") is not True
        or not calendar.get("source")
        or not isinstance(expected, list)
        or len(expected) < 15
        or any(
            not isinstance(day, str) or not day.startswith("2026-11-")
            for day in expected
        )
        or len(set(expected)) != len(expected)
    ):
        raise ValueError("verified November session-date list required")
    november_path = raw_root / "2885-1-2026-11-01-2026-12-01.json"
    audit = verify_saved_response(_NOVEMBER, november_path)
    if audit.retrieved_at is None or audit.retrieved_at < datetime(
        2026, 12, 1, tzinfo=_IST
    ):
        raise ValueError("complete November response must be retrieved after month end")
    november_bars = list(audit.bars)
    observed = {bar.trading_date.isoformat() for bar in november_bars}
    if observed != set(expected):
        raise ValueError("November observed sessions differ from verified calendar")
    training_bars = [bar for bar in development if bar.trading_date < date(2026, 8, 1)]
    rows = noise_reduction._examples(
        training_bars + november_bars, through_august=False, target_end=18
    )
    november = [row for row in rows if row.session.startswith("2026-11-")]
    if len(november) != 312 * len(expected):
        raise ValueError("November common continuous sessions are incomplete")
    if max(row.label_end_ns for row in rows if row.session < "2026-08-01") >= min(
        row.available_ns for row in november
    ):
        raise ValueError("chronological outcome split overlaps")
    x = noise_reduction._matrix(november, "raw")
    probability = _probabilities(x, frozen["model"])
    null = np.broadcast_to(
        np.asarray(frozen["null_probabilities"], dtype=np.float64), probability.shape
    )
    predictions = [
        {
            "session": row.session,
            "stamp_ns": row.stamp_ns,
            "available_ns": row.available_ns,
            "label_start_ns": row.label_start_ns,
            "label_end_ns": row.label_end_ns,
            "model_probabilities_adverse_neutral_favorable": probability[i].tolist(),
            "null_probabilities_adverse_neutral_favorable": null[i].tolist(),
        }
        for i, row in enumerate(november)
    ]
    conditional_means = np.asarray(
        frozen["training_class_mean_return_bps"], dtype=np.float64
    )
    expected_gross = probability @ conditional_means
    report = {
        "status": "PROVISIONAL_REFERENCE_PRICES_NOT_EXECUTABLE_NO_TRADING_OR_PROMOTION",
        "frozen_model_sha256": _sha(frozen_bytes),
        "spec_sha256": frozen["spec_sha256"],
        "code_sha256": frozen["code_sha256"],
        "git_commit_at_freeze": frozen["git_commit_at_freeze"],
        "source_sha256": {**hashes, november_path.name: audit.sha256},
        "november_retrieved_at": audit.retrieved_at.isoformat(),
        "calendar_sha256": _sha(calendar_bytes),
        "sessions": len(expected),
        "examples_overlapping": len(november),
        "market_structure_note": "August 3 change; common 09:15-15:15 window only",
        "score": _session_scores(november, probability, null),
        "economic_assessment": {
            "basis": "frozen April-July class means; reference prices, no fills or P&L",
            "mean_expected_gross_reference_bps": float(np.mean(expected_gross)),
            "mean_expected_net_reference_bps_by_cost": {
                str(cost): float(np.mean(expected_gross - cost))
                for cost in SPEC["cost_bps"]
            },
        },
    }
    return report, predictions


def _inside_experiments(path: Path) -> None:
    root = Path("data/experiments").resolve()
    if root not in path.resolve().parents:
        raise ValueError("experiment artifacts must be under data/experiments/")


def _write_new(path: Path, data: bytes) -> None:
    _inside_experiments(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("freeze", "evaluate"))
    parser.add_argument("--raw-root", type=Path, default=Path("data/historical/raw"))
    parser.add_argument(
        "--frozen",
        type=Path,
        default=Path("data/experiments/reliance-15m-v1/frozen-model.json"),
    )
    parser.add_argument("--calendar", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.phase == "freeze":
        artifact = freeze(args.raw_root)
        encoded = _canonical(artifact)
        _write_new(args.frozen, encoded)
        print(
            json.dumps(
                {"frozen": str(args.frozen), "sha256": _sha(encoded)}, sort_keys=True
            )
        )
        return
    if args.calendar is None or args.output_dir is None:
        raise ValueError("evaluation requires --calendar and --output-dir")
    _inside_experiments(args.output_dir / "report.json")
    report, predictions = evaluate(args.raw_root, args.frozen, args.calendar)
    serialized = b"".join(_canonical(record) for record in predictions)
    report["predictions_sha256"] = _sha(serialized)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    _write_new(args.output_dir / "predictions.jsonl", serialized)
    _write_new(args.output_dir / "report.json", _canonical(report))
    print(
        json.dumps(
            {"report": str(args.output_dir / "report.json"), "score": report["score"]},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
