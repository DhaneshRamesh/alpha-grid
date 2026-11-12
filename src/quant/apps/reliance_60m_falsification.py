"""Pinned, offline, exploratory RELIANCE first-signal falsification; never trades."""

from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from quant.acquisition.dhan import _private_directory
from quant.apps.descriptive_dhan_opportunities import (
    load_verified_bars,
    validated_sessions,
)
from quant.apps.exploratory_dhan_timing import _session_arrays
from quant.research.uncertainty import bootstrap_session_net
from quant.research.walkforward import ResearchIndex, make_folds

DELAYS = (60, 120, 300)
EVAL_START = "2026-08-03"
EVAL_END = "2026-10-01"
EMBARGO = "2026-07-31"
NS_DAY = 86_400_000_000_000
IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class Choice:
    day: str
    index: int
    gross: float
    high: float
    low: float
    secondary_30: float
    secondary_remaining: float


def fee_bps(notional: float) -> tuple[float, dict[str, float]]:
    """Dhan published intraday equity schedule; equal buy/sell notional estimate."""
    rupees = Decimal(str(notional))

    def paise(value: Decimal) -> float:
        return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    def whole(value: Decimal) -> float:
        return float(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    brokerage = 2 * paise(min(Decimal("20"), rupees * Decimal("0.0003")))
    transaction = paise(2 * rupees * Decimal("0.000030699"))
    sebi = paise(2 * rupees * Decimal("0.000001"))
    ipft = paise(2 * rupees * Decimal("0.000000001"))
    gst = paise(Decimal("0.18") * Decimal(str(brokerage + transaction + sebi + ipft)))
    stt = whole(rupees * Decimal("0.00025"))
    stamp = whole(rupees * Decimal("0.00003"))
    parts = {
        "brokerage": brokerage,
        "nse_transaction": transaction,
        "sebi": sebi,
        "ipft": ipft,
        "gst": gst,
        "stt": stt,
        "stamp": stamp,
    }
    return sum(parts.values()) / notional * 10_000, parts


def select(
    arrays: dict, delay: int
) -> tuple[dict[str, dict[str, Choice | None]], dict[str, int]]:
    history: list[np.ndarray] = []
    result: dict[str, dict[str, Choice | None]] = {}
    censored = {"momentum": 0, "volume": 0}
    for day, (close, high, low, volume) in sorted(arrays.items()):
        chosen: dict[str, Choice | None] = {
            "unconditional": None,
            "momentum": None,
            "volume": None,
        }
        n = len(close)
        for i in range(15, n):
            momentum_signal = close[i] > close[i - 15]
            bucket = volume[i - 4 : i + 1].sum()
            prior = history[-20:]
            confirmed = len(prior) == 20 and all(len(v) > i for v in prior)
            volume_signal = (
                momentum_signal
                and confirmed
                and bucket >= 2 * np.median([v[i - 4 : i + 1].sum() for v in prior])
            )
            candidates = (
                ("unconditional", "momentum", "volume")
                if volume_signal
                else (
                    ("unconditional", "momentum")
                    if momentum_signal
                    else ("unconditional",)
                )
            )
            entry_i = i + 1 + delay // 60
            exit_i = entry_i + 59
            if exit_i >= n:
                for key in candidates:
                    if chosen[key] is None:
                        censored[key] += 1
                continue
            entry = close[entry_i]
            choice = Choice(
                day,
                i,
                float((close[exit_i] / entry - 1) * 10_000),
                float((np.max(high[entry_i + 1 : exit_i + 1]) / entry - 1) * 10_000),
                float((np.min(low[entry_i + 1 : exit_i + 1]) / entry - 1) * 10_000),
                float((close[entry_i + 29] / entry - 1) * 10_000),
                float((close[-1] / entry - 1) * 10_000),
            )
            for key in candidates:
                if chosen[key] is None:
                    chosen[key] = choice
            if all(value is not None for value in chosen.values()):
                break
        result[day] = chosen
        history.append(volume)
    return result, censored


def score_probability(prob: float, actual: bool) -> tuple[float, float]:
    p = min(max(prob, 1e-12), 1 - 1e-12)
    y = float(actual)
    return (p - y) ** 2, -y * math.log(p) - (1 - y) * math.log(1 - p)


def summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"n": 0, "mean": None, "p05": None, "median": None, "p95": None}
    a = np.asarray(values)
    return {
        "n": len(values),
        "mean": float(a.mean()),
        "p05": float(np.quantile(a, 0.05)),
        "median": float(np.median(a)),
        "p95": float(np.quantile(a, 0.95)),
    }


def study() -> dict:
    bars, provenance = load_verified_bars()
    sessions = validated_sessions(bars)
    arrays = _session_arrays(sessions)
    ordered = sorted(d for d in sessions if d < EVAL_END)
    if EMBARGO not in ordered or sum(EVAL_START <= d < EVAL_END for d in ordered) != 42:
        raise ValueError("frozen chronology or embargo missing")
    fee_table = {
        str(int(n)): {
            "round_trip_bps": fee_bps(n)[0],
            "components_rupees": fee_bps(n)[1],
        }
        for n in (10_000.0, 100_000.0, 500_000.0)
    }
    cost = fee_bps(100_000.0)[0] + 40
    # One session row per date lets the existing splitter enforce label maturity
    # and a full calendar-day embargo even on dates without selected signals.
    index = [
        ResearchIndex(
            d,
            int(
                datetime.fromisoformat(d + "T09:15:00").replace(tzinfo=IST).timestamp()
                * 1e9
            ),
            60 * 60 * 1_000_000_000,
            k,
        )
        for k, d in enumerate(ordered)
    ]
    folds = make_folds(
        index,
        train_sessions=20,
        validation_sessions=1,
        test_sessions=1,
        embargo_ns=NS_DAY,
        mode="expanding",
    )
    fold_by_test = {f.test[0].session_id: f for f in folds}
    eval_days = [d for d in ordered if EVAL_START <= d < EVAL_END]
    results = {}
    for delay in DELAYS:
        selected, censored = select(arrays, delay)
        per_model: dict[str, dict] = {}
        for key in ("unconditional", "momentum", "volume"):
            net: dict[str, float] = {}
            chosen: list[Choice] = []
            scores: dict[str, list[float]] = {
                "favorable_brier": [],
                "favorable_log_loss": [],
                "adverse_brier": [],
                "adverse_log_loss": [],
                "favorable_predicted": [],
                "adverse_predicted": [],
                "favorable_unconditional_brier_on_same_days": [],
                "favorable_unconditional_log_loss_on_same_days": [],
                "adverse_unconditional_brier_on_same_days": [],
                "adverse_unconditional_log_loss_on_same_days": [],
            }
            for day in eval_days:
                fold = fold_by_test[day]
                allowed = {r.session_id for r in fold.train if r.session_id < EMBARGO}
                # For walk-forward, matured prior evaluation sessions may be fitted.
                allowed |= {
                    r.session_id for r in fold.train if EVAL_START <= r.session_id < day
                }
                train = [selected[d][key] for d in allowed]
                train = [x for x in train if x is not None]
                item = selected[day][key]
                # Unconditional is a no-feature daily long reference.
                if item is None:
                    net[day] = 0.0
                    continue
                chosen.append(item)
                net[day] = item.gross - cost
                for event, predicate in (
                    ("favorable", lambda x: x.gross > cost),
                    ("adverse", lambda x: x.gross < -cost),
                ):
                    prob = (1 + sum(predicate(x) for x in train)) / (2 + len(train))
                    brier, logloss = score_probability(prob, predicate(item))
                    unconditioned = [selected[d]["unconditional"] for d in allowed]
                    unconditioned = [x for x in unconditioned if x is not None]
                    base_prob = (1 + sum(predicate(x) for x in unconditioned)) / (
                        2 + len(unconditioned)
                    )
                    base_brier, base_logloss = score_probability(
                        base_prob, predicate(item)
                    )
                    scores[event + "_brier"].append(brier)
                    scores[event + "_log_loss"].append(logloss)
                    scores[event + "_predicted"].append(prob)
                    scores[event + "_unconditional_brier_on_same_days"].append(
                        base_brier
                    )
                    scores[event + "_unconditional_log_loss_on_same_days"].append(
                        base_logloss
                    )
            favorable = [x.gross for x in chosen if x.gross > cost]
            adverse = [x.gross for x in chosen if x.gross < -cost]
            boot = bootstrap_session_net(net, seed=20261102 + delay, min_sessions=10)
            per_model[key] = {
                "selected_sessions": len(chosen),
                "selected_opportunities": len(chosen),
                "gross_60_bps": summary([x.gross for x in chosen]),
                "favorable_60_bps": summary(favorable),
                "adverse_60_bps": summary(adverse),
                "favorable_rate": len(favorable) / len(chosen) if chosen else None,
                "adverse_rate": len(adverse) / len(chosen) if chosen else None,
                "favorable_high_range_bps": summary([x.high for x in chosen]),
                "adverse_low_range_bps": summary([x.low for x in chosen]),
                "gross_30_bps": summary([x.secondary_30 for x in chosen]),
                "gross_remaining_bps": summary([x.secondary_remaining for x in chosen]),
                "net_mean_session_bps": float(np.mean(list(net.values()))),
                "net_sensitivity_mean_session_bps": {
                    "published_fee_only": float(
                        np.mean([v + 40 if v else 0 for v in net.values()])
                    ),
                    "fee_plus_assumed_20_bps": float(
                        np.mean([v + 20 if v else 0 for v in net.values()])
                    ),
                    "fee_plus_assumed_40_bps": float(np.mean(list(net.values()))),
                },
                "net_bootstrap_95_bps": [
                    boot.confidence_low_nanos,
                    boot.confidence_high_nanos,
                ]
                if boot
                else None,
                "probability_scores": {
                    name: float(np.mean(v)) if v else None for name, v in scores.items()
                },
                "observed_favorable_rate": len(favorable) / len(chosen)
                if chosen
                else None,
                "observed_adverse_rate": len(adverse) / len(chosen) if chosen else None,
                "daily_net": net,
                "chronological_14_session_blocks": [
                    {
                        "first": block[0],
                        "last": block[-1],
                        "mean_net_bps": float(np.mean([net[d] for d in block])),
                    }
                    for block in (eval_days[i : i + 14] for i in (0, 14, 28))
                ],
            }
        a = per_model["momentum"]["daily_net"]
        b = per_model["volume"]["daily_net"]
        delta = {d: b[d] - a[d] for d in eval_days}
        contrast = bootstrap_session_net(delta, seed=20261202 + delay, min_sessions=10)
        simple = per_model["unconditional"]["daily_net"]
        delta_simple = {d: b[d] - simple[d] for d in eval_days}
        contrast_simple = bootstrap_session_net(
            delta_simple, seed=20261302 + delay, min_sessions=10
        )
        results[str(delay)] = {
            "censored_after_signal": censored,
            "candidates": per_model,
            "volume_minus_momentum_mean_session_bps": float(
                np.mean(list(delta.values()))
            ),
            "volume_minus_momentum_bootstrap_95_bps": [
                contrast.confidence_low_nanos,
                contrast.confidence_high_nanos,
            ]
            if contrast
            else None,
            "volume_minus_unconditional_mean_session_bps": float(
                np.mean(list(delta_simple.values()))
            ),
            "volume_minus_unconditional_bootstrap_95_bps": [
                contrast_simple.confidence_low_nanos,
                contrast_simple.confidence_high_nanos,
            ]
            if contrast_simple
            else None,
            "source_session_regimes": {
                "pre_aug3_fit": sum(d < EVAL_START for d in ordered),
                "aug_sep_eval": len(eval_days),
            },
        }
    return {
        "schema_version": "reliance-60m-first-signal-exploratory-v1",
        "status": "EXPLORATORY_NO_TRADE",
        "source": provenance,
        "bars": len(bars),
        "sessions": len(sessions),
        "evaluation_sessions": len(eval_days),
        "fee_table": fee_table,
        "assumed_base_friction_bps": 20,
        "assumed_conservative_friction_bps": 40,
        "primary_cost_bps": cost,
        "timing_assumptions_seconds_after_inferred_completion": DELAYS,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = study()
    _private_directory(args.output.parent)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "bars": report["bars"],
                "sessions": report["sessions"],
                "output": str(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
