"""One frozen, provisional RELIANCE OHLCV comparison; never registers a model."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from quant.acquisition.contracts import CashInstrument, HistoricalBar
from quant.acquisition.dhan import (
    HistoricalRequest,
    iter_saved_backfill,
    response_path,
    verify_saved_response,
)

WINDOWS = (
    ("2026-04-01", "2026-06-30"),
    ("2026-06-30", "2026-09-28"),
    ("2026-09-28", "2026-10-12"),
    ("2026-10-12", "2026-10-17"),
)
HALF_LIFE_MINUTES = 5
VOLATILITY_LOOKBACK = 30
TARGET_REFERENCE_START = 3
TARGET_REFERENCE_END = 8
L2_PENALTY = 10.0
BOOTSTRAP_SEED = 20261019
BOOTSTRAP_REPETITIONS = 5000
MIN_VOLUME_BASELINE_SESSIONS = 10
EPSILON = 1e-12


@dataclass(frozen=True, slots=True)
class Example:
    session: str
    stamp_ns: int
    available_ns: int
    label_start_ns: int
    label_end_ns: int
    raw: tuple[float, float, float]
    denoised: tuple[float, float, float]
    positive: int
    forward_return: float


def _requests() -> tuple[HistoricalRequest, ...]:
    instrument = CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE")
    return tuple(
        HistoricalRequest(
            instrument, date.fromisoformat(start), date.fromisoformat(end), 1
        )
        for start, end in WINDOWS
    )


def _stamp(bar: HistoricalBar) -> int:
    if bar.source_timestamp_ns is None:
        raise ValueError("minute candle has no source timestamp")
    return bar.source_timestamp_ns


def _load(raw_root: Path) -> tuple[list[HistoricalBar], dict[str, str]]:
    requests = _requests()
    paths = [response_path(request, raw_root) for request in requests]
    audits = [
        verify_saved_response(request, path)
        for request, path in zip(requests, paths, strict=True)
    ]
    if any(audit.retrieved_at is None for audit in audits):
        raise ValueError("source retrieval provenance required")
    bars = list(iter_saved_backfill(requests, paths))
    return bars, {
        path.name: audit.sha256 for path, audit in zip(paths, audits, strict=True)
    }


def _examples(bars: list[HistoricalBar], *, through_august: bool) -> list[Example]:
    sessions: dict[date, list[HistoricalBar]] = defaultdict(list)
    for bar in bars:
        if through_august and bar.trading_date >= date(2026, 9, 1):
            continue
        sessions[bar.trading_date].append(bar)
    # Training examples use only preceding sessions' time-of-day volume.
    # Freeze the baseline at July 31 for all August and holdout predictions.
    prior_volume_sum = np.zeros(360, dtype=np.float64)
    prior_sessions = 0
    frozen_baseline: npt.NDArray[np.float64] | None = None
    result: list[Example] = []
    minute_ns = 60_000_000_000
    alpha = 1 - 2 ** (-1 / HALF_LIFE_MINUTES)
    for day, whole_session in sorted(sessions.items()):
        if day >= date(2026, 8, 1) and frozen_baseline is None:
            if prior_sessions < MIN_VOLUME_BASELINE_SESSIONS:
                raise ValueError("insufficient training sessions for volume baseline")
            frozen_baseline = prior_volume_sum / prior_sessions
        # Only common, ordinary continuous-session bars are admissible.
        market = [
            bar
            for bar in whole_session
            if 9 * 60 + 15 <= (_stamp(bar) // minute_ns + 330) % 1440 < 15 * 60 + 15
        ]
        if len(market) != 360 or any(
            _stamp(right) - _stamp(left) != minute_ns
            for left, right in zip(market, market[1:], strict=False)
        ):
            raise ValueError(f"incomplete common continuous session: {day}")
        if (_stamp(market[0]) // minute_ns + 330) % 1440 != 9 * 60 + 15:
            raise ValueError(f"unexpected exchange open: {day}")
        closes = np.asarray([bar.close_nanos for bar in market], dtype=np.float64)
        volumes = np.asarray([bar.volume for bar in market], dtype=np.float64)
        returns = np.zeros(360, dtype=np.float64)
        returns[1:] = np.log(closes[1:] / closes[:-1])
        filtered = np.zeros(360, dtype=np.float64)
        for index in range(1, 360):
            filtered[index] = alpha * returns[index] + (1 - alpha) * filtered[index - 1]
        baseline = (
            frozen_baseline
            if frozen_baseline is not None
            else prior_volume_sum / prior_sessions
            if prior_sessions
            else None
        )
        if baseline is not None and prior_sessions >= MIN_VOLUME_BASELINE_SESSIONS:
            for index in range(VOLATILITY_LOOKBACK, 360 - TARGET_REFERENCE_END):
                sigma = max(
                    float(np.sqrt(np.mean(returns[index - 29 : index + 1] ** 2))),
                    1e-6,
                )
                r1 = float(returns[index] / sigma)
                r5 = float(
                    np.log(closes[index] / closes[index - 5]) / (sigma * math.sqrt(5))
                )
                relative_volume = float(
                    np.log1p(volumes[index] / max(baseline[index], 1.0))
                )
                smoothed_r1 = float(filtered[index] / sigma)
                stamped = _stamp(market[index])
                start = _stamp(market[index + TARGET_REFERENCE_START])
                end = _stamp(market[index + TARGET_REFERENCE_END])
                available = stamped + 2 * minute_ns
                if not available < start < end:
                    raise ValueError("feature availability reaches future outcome")
                forward_return = float(closes[index + 8] / closes[index + 3] - 1)
                result.append(
                    Example(
                        day.isoformat(),
                        stamped,
                        available,
                        start,
                        end,
                        (r1, r5, relative_volume),
                        (smoothed_r1, r5, relative_volume),
                        int(forward_return > 0),
                        forward_return,
                    )
                )
        if day < date(2026, 8, 1):
            prior_volume_sum += volumes
            prior_sessions += 1
    return result


def _fit_logistic(
    x: npt.NDArray[np.float64], y: npt.NDArray[np.float64]
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    center = np.mean(x, axis=0)
    scale = np.std(x, axis=0)
    scale[scale == 0] = 1
    design = np.column_stack([np.ones(len(x)), (x - center) / scale])
    beta = np.zeros(design.shape[1], dtype=np.float64)
    frequency = np.clip(np.mean(y), EPSILON, 1 - EPSILON)
    beta[0] = math.log(frequency / (1 - frequency))
    penalty = np.diag([0.0] + [L2_PENALTY] * x.shape[1])
    for _ in range(50):
        p = 1 / (1 + np.exp(-np.clip(design @ beta, -35, 35)))
        weight = p * (1 - p)
        step = np.linalg.solve(
            design.T @ (weight[:, None] * design) + penalty,
            design.T @ (p - y) + penalty @ beta,
        )
        beta -= step
        if float(np.max(np.abs(step))) < 1e-10:
            break
    else:
        raise ValueError("regularized logistic fit did not converge")
    return beta, center, scale


def _predict(
    x: npt.NDArray[np.float64],
    model: tuple[
        npt.NDArray[np.float64], npt.NDArray[np.float64], npt.NDArray[np.float64]
    ],
) -> npt.NDArray[np.float64]:
    beta, center, scale = model
    z = beta[0] + ((x - center) / scale) @ beta[1:]
    return np.asarray(1 / (1 + np.exp(-np.clip(z, -35, 35))), dtype=np.float64)


def _matrix(rows: list[Example], kind: str) -> npt.NDArray[np.float64]:
    return np.asarray([getattr(row, kind) for row in rows], dtype=np.float64)


def _scores(
    rows: list[Example], predictions: dict[str, npt.NDArray[np.float64]]
) -> dict[str, Any]:
    y = np.asarray([row.positive for row in rows], dtype=np.float64)
    by_session: dict[str, dict[str, float]] = {}
    overall: dict[str, dict[str, float]] = {}
    reliability: dict[str, list[dict[str, float | int]]] = {}
    for name, probability in predictions.items():
        clipped = np.clip(probability, EPSILON, 1 - EPSILON)
        brier = (probability - y) ** 2
        log_loss = -(y * np.log(clipped) + (1 - y) * np.log1p(-clipped))
        overall[name] = {
            "brier": float(np.mean(brier)),
            "log_loss": float(np.mean(log_loss)),
            "mean_probability": float(np.mean(probability)),
            "positive_fraction": float(np.mean(y)),
        }
        for day in sorted({row.session for row in rows}):
            indices = [i for i, row in enumerate(rows) if row.session == day]
            by_session.setdefault(day, {})[f"{name}_brier"] = float(
                np.mean(brier[indices])
            )
            by_session[day][f"{name}_log_loss"] = float(np.mean(log_loss[indices]))
        reliability[name] = []
        for lower in np.arange(0, 1, 0.02):
            selected = (probability >= lower) & (probability < lower + 0.02)
            if np.any(selected):
                reliability[name].append(
                    {
                        "lower": round(float(lower), 2),
                        "count": int(np.sum(selected)),
                        "mean_probability": float(np.mean(probability[selected])),
                        "observed_positive_fraction": float(np.mean(y[selected])),
                    }
                )
    differences = np.asarray(
        [values["A_brier"] - values["B_brier"] for values in by_session.values()],
        dtype=np.float64,
    )
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    boot = np.mean(
        differences[
            rng.integers(
                0, len(differences), size=(BOOTSTRAP_REPETITIONS, len(differences))
            )
        ],
        axis=1,
    )
    return {
        "overall": overall,
        "reliability": reliability,
        "by_session": by_session,
        "paired_A_minus_B_brier": {
            "mean": float(np.mean(differences)),
            "median": float(np.median(differences)),
            "B_better_sessions": int(np.sum(differences > 0)),
            "sessions": len(differences),
            "bootstrap_95pct": [
                float(np.quantile(boot, 0.025)),
                float(np.quantile(boot, 0.975)),
            ],
            "seed": BOOTSTRAP_SEED,
            "repetitions": BOOTSTRAP_REPETITIONS,
        },
    }


def _cost_sensitivity() -> dict[str, Any]:
    """Illustrative one-buy/one-sell costs; no spread or fill observations."""
    notional = 5_000.0
    turnover = 2 * notional
    brokerage = 2 * min(20.0, notional * 0.0003)
    exchange = turnover * 0.000030699
    sebi = turnover * 0.000001
    stt = notional * 0.00025
    stamp = notional * 0.00003
    ipft = turnover * 0.000000001
    gst = 0.18 * (brokerage + exchange + sebi + ipft)
    other = exchange + sebi + stt + stamp + ipft + gst
    scenarios = []
    for assumed_bps in (5, 10, 20):
        spread_slippage = notional * assumed_bps / 10_000
        total = brokerage + other + spread_slippage
        scenarios.append(
            {
                "assumed_roundtrip_spread_slippage_bps": assumed_bps,
                "assumed_spread_slippage_rupees": spread_slippage,
                "total_rupees": total,
                "break_even_reference_move_bps": total / notional * 10_000,
            }
        )
    return {
        "status": "ILLUSTRATIVE_ONLY_NO_FILL_OR_PNL_CLAIM",
        "source": "https://dhan.co/pricing/",
        "pricing_checked_date": "2026-10-19",
        "one_way_notional_rupees": notional,
        "roundtrip_brokerage_rupees": brokerage,
        "exchange_transaction_rupees": exchange,
        "stt_rupees": stt,
        "stamp_rupees": stamp,
        "sebi_rupees": sebi,
        "ipft_rupees": ipft,
        "gst_rupees": gst,
        "roundtrip_nonbrokerage_charges_rupees": other,
        "scenarios": scenarios,
        "limitation": "Unrounded rate illustration; actual charges and fills differ",
    }


def run(phase: str, raw_root: Path) -> dict[str, Any]:
    """Diagnostics never score September/October; final uses frozen fit once."""
    if phase not in {"diagnostics", "final"}:
        raise ValueError("phase must be diagnostics or final")
    bars, hashes = _load(raw_root)
    rows = _examples(bars, through_august=phase == "diagnostics")
    train = [row for row in rows if row.session < "2026-08-01"]
    august = [row for row in rows if "2026-08-01" <= row.session < "2026-09-01"]
    holdout = [row for row in rows if row.session >= "2026-09-01"]
    if (
        len({row.session for row in train}) != 73
        or len({row.session for row in august}) != 21
        or (phase == "final" and len({row.session for row in holdout}) != 32)
    ):
        raise ValueError("frozen session split changed")
    if max(row.label_end_ns for row in train) >= min(
        row.available_ns for row in august
    ) or (
        phase == "final"
        and max(row.label_end_ns for row in august)
        >= min(row.available_ns for row in holdout)
    ):
        raise ValueError("overlapping labels at chronological split")
    y_train = np.asarray([row.positive for row in train], dtype=np.float64)
    models = {
        kind: _fit_logistic(_matrix(train, kind), y_train)
        for kind in ("raw", "denoised")
    }
    null = float(np.mean(y_train))

    def evaluate(sample: list[Example]) -> dict[str, Any]:
        return _scores(
            sample,
            {
                "null": np.full(len(sample), null),
                "A": _predict(_matrix(sample, "raw"), models["raw"]),
                "B": _predict(_matrix(sample, "denoised"), models["denoised"]),
            },
        )

    code_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    report: dict[str, Any] = {
        "status": "PROVISIONAL_EXPLORATORY_ONLY_NOT_APPROVED_FOR_MODEL_PROMOTION",
        "phase": phase,
        "source_sha256": hashes,
        "code_sha256": code_hash,
        "git_commit": git_sha,
        "config": {
            "half_life_minutes": HALF_LIFE_MINUTES,
            "volatility_lookback": VOLATILITY_LOOKBACK,
            "target_reference_offsets_minutes": [
                TARGET_REFERENCE_START,
                TARGET_REFERENCE_END,
            ],
            "availability_delay_minutes": 2,
            "l2_penalty": L2_PENALTY,
            "volume_baseline_min_prior_train_sessions": MIN_VOLUME_BASELINE_SESSIONS,
            "continuous_IST": ["09:15", "15:15"],
            "split_boundary_overlapping_labels": 0,
            "feature_order": [
                "normalized_r1_or_ewma_r1",
                "normalized_r5",
                "log1p_relative_volume",
            ],
        },
        "samples": {
            "train": len(train),
            "august": len(august),
            "holdout": len(holdout),
        },
        "sessions": {
            "train_model_fit": 73,
            "train_volume_warmup": 10,
            "august": 21,
            "holdout": 32 if phase == "final" else 0,
        },
        "training_positive_fraction": null,
        "cost_sensitivity": _cost_sensitivity(),
        "august_diagnostics": evaluate(august),
    }
    if phase == "final":
        report["untouched_holdout"] = evaluate(holdout)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("diagnostics", "final"))
    parser.add_argument("--raw-root", type=Path, default=Path("data/historical/raw"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    experiment_root = Path("data/experiments").resolve()
    if args.output is not None and experiment_root not in args.output.resolve().parents:
        raise ValueError("experimental report must be under data/experiments/")
    report = run(args.phase, args.raw_root)
    serialized = (
        json.dumps(report, sort_keys=True, allow_nan=False, separators=(",", ":"))
        + "\n"
    )
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as stream:
            stream.write(serialized)
    print(serialized, end="")


if __name__ == "__main__":
    main()
