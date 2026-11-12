"""Offline Dhan OHLCV movement description; never emits causal observations."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import numpy.typing as npt

from quant.acquisition.contracts import CashInstrument, HistoricalBar
from quant.acquisition.dhan import (
    HistoricalRequest,
    _private_directory,
    response_path,
    verify_saved_response,
)

IST = ZoneInfo("Asia/Kolkata")
ROOT = Path("data/historical/raw-dhan-macos-2026-11-01")
INSTRUMENT = CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE")
WINDOWS = (
    (
        date(2026, 5, 1),
        date(2026, 7, 30),
        "62e76b108fad3fde0c49041e154796c5389f055040b1b229839d533664de512b",
    ),
    (
        date(2026, 7, 30),
        date(2026, 10, 26),
        "fbe4eac254fa7e55c4e167eae9fe9c9a36597f8073c58196b135586ca8455838",
    ),
    (
        date(2026, 10, 26),
        date(2026, 10, 31),
        "a4c61e3d5ad8ce3b9bf78317ac73ff15502bcefa52bd6a09c3771b866d2d8653",
    ),
)
HOLIDAYS = frozenset(
    date.fromisoformat(value)
    for value in (
        "2026-05-01",
        "2026-05-28",
        "2026-06-26",
        "2026-09-14",
        "2026-10-02",
        "2026-10-20",
    )
)
HORIZONS = (30, 60, "remaining_session")
COST_HURDLES_BPS = (25.0, 50.0)  # Assumptions, never observed execution costs.
MINUTE_NS = 60_000_000_000


def load_verified_bars(
    root: Path = ROOT,
) -> tuple[tuple[HistoricalBar, ...], list[dict[str, object]]]:
    """Pin each original response and reuse the shared saved-response verifier."""
    bars: list[HistoricalBar] = []
    provenance: list[dict[str, object]] = []
    for start, end, expected_sha in WINDOWS:
        request = HistoricalRequest(INSTRUMENT, start, end, 1)
        path = response_path(request, root)
        audit = verify_saved_response(request, path)
        if audit.sha256 != expected_sha or audit.retrieved_at is None:
            raise ValueError("pinned Dhan source provenance mismatch")
        metadata = json.loads(path.with_suffix(".meta.json").read_text())
        if metadata.get("http_status") != 200:
            raise ValueError("saved Dhan response is not HTTP 200")
        bars.extend(audit.bars)
        provenance.append(
            {
                "request_start": start.isoformat(),
                "request_end_exclusive": end.isoformat(),
                "sha256": audit.sha256,
                "retrieved_at": audit.retrieved_at.isoformat(),
                "rows": len(audit.bars),
                "original_bytes": path.stat().st_size,
            }
        )
    return tuple(bars), provenance


def validated_sessions(
    bars: tuple[HistoricalBar, ...],
) -> dict[str, tuple[HistoricalBar, ...]]:
    """Require intact source-minute sequences and the observed continuous span."""
    grouped: dict[str, list[HistoricalBar]] = defaultdict(list)
    for bar in bars:
        if (
            bar.instrument != INSTRUMENT
            or bar.interval_minutes != 1
            or bar.source != "DHAN_HISTORICAL"
            or bar.timestamp_semantics != "UNVERIFIED"
            or bar.source_timestamp_ns is None
        ):
            raise ValueError("unexpected source or asserted timing semantics")
        grouped[bar.trading_date.isoformat()].append(bar)
    expected: set[str] = set()
    day = date(2026, 5, 1)
    while day < date(2026, 10, 31):
        if day.weekday() < 5 and day not in HOLIDAYS:
            expected.add(day.isoformat())
        day += timedelta(days=1)
    if set(grouped) != expected:
        raise ValueError("Dhan date set differs from independent NSE holiday schedule")
    result = {}
    for label, values in sorted(grouped.items()):
        day = date.fromisoformat(label)
        # Exclude any auction extension: only the observed 09:15–15:29
        # pre-CAS or 09:15–15:14 post-CAS continuous span is described.
        count, last = (
            (375, time(15, 29)) if day < date(2026, 8, 3) else (360, time(15, 14))
        )
        stamps = [bar.source_timestamp_ns for bar in values]
        observed = [
            datetime.fromtimestamp(int(stamp) // 1_000_000_000, IST)
            for stamp in stamps
            if stamp is not None
        ]
        if (
            len(values) != count
            or len(observed) != count
            or observed[0].date() != day
            or observed[0].time() != time(9, 15)
            or observed[-1].time() != last
            or any(
                right - left != MINUTE_NS
                for left, right in zip(stamps, stamps[1:], strict=False)
                if left is not None and right is not None
            )
        ):
            raise ValueError(f"source session coverage invalid: {label}")
        result[label] = tuple(values)
    return result


def _paths(
    values: tuple[HistoricalBar, ...], horizon: int | str
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64], npt.NDArray[np.float64]]:
    close = np.asarray([bar.close_nanos for bar in values], dtype=np.float64)
    high = np.asarray([bar.high_nanos for bar in values], dtype=np.float64)
    low = np.asarray([bar.low_nanos for bar in values], dtype=np.float64)
    if horizon == "remaining_session":
        entry = close[:-1]
        exit_close = np.full(len(entry), close[-1])
        maximum = np.maximum.accumulate(high[1:][::-1])[::-1]
        minimum = np.minimum.accumulate(low[1:][::-1])[::-1]
    else:
        assert isinstance(horizon, int)
        entry, exit_close = close[:-horizon], close[horizon:]
        maximum = np.lib.stride_tricks.sliding_window_view(high[1:], horizon).max(
            axis=1
        )
        minimum = np.lib.stride_tricks.sliding_window_view(low[1:], horizon).min(axis=1)
    return (
        (exit_close / entry - 1) * 10_000,
        np.maximum(0, (maximum / entry - 1) * 10_000),
        np.minimum(0, (minimum / entry - 1) * 10_000),
    )


def describe(sessions: dict[str, tuple[HistoricalBar, ...]]) -> dict[str, object]:
    """Equal-session uncertainty on overlapping retrospective OHLCV paths."""
    result: dict[str, object] = {}
    for horizon in HORIZONS:
        paths = [_paths(values, horizon) for values in sessions.values()]
        signed = np.concatenate([item[0] for item in paths])
        favorable = np.concatenate([item[1] for item in paths])
        adverse = np.concatenate([item[2] for item in paths])
        hurdle_results: dict[str, object] = {}
        for hurdle in COST_HURDLES_BPS:
            per_session = np.asarray(
                [np.mean(np.abs(item[0]) > hurdle) for item in paths]
            )
            rng = np.random.default_rng(
                20261101 + int(horizon) if isinstance(horizon, int) else 20261101
            )
            indices = rng.integers(0, len(per_session), size=(5000, len(per_session)))
            bootstrap = per_session[indices].mean(axis=1)
            hurdle_results[str(int(hurdle))] = {
                "assumed_round_trip_cost_bps": hurdle,
                "session_mean_fraction_abs_close_move_exceeds_cost": float(
                    per_session.mean()
                ),
                "session_bootstrap_95pct_interval": [
                    float(value) for value in np.quantile(bootstrap, [0.025, 0.975])
                ],
                "interpretation": "gross path hurdle, not executable net P&L",
            }
        result[str(horizon)] = {
            "overlapping_windows": len(signed),
            "sessions": len(paths),
            "positive_fraction": float(np.mean(signed > 0)),
            "negative_fraction": float(np.mean(signed < 0)),
            "flat_fraction": float(np.mean(signed == 0)),
            "median_signed_close_move_bps": float(np.median(signed)),
            "median_absolute_close_move_bps": float(np.median(np.abs(signed))),
            "p90_absolute_close_move_bps": float(np.quantile(np.abs(signed), 0.9)),
            "mean_long_favorable_high_excursion_bps": float(np.mean(favorable)),
            "mean_long_adverse_low_excursion_bps": float(np.mean(adverse)),
            "cost_hurdles": hurdle_results,
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bars, provenance = load_verified_bars()
    sessions = validated_sessions(bars)
    report = {
        "schema_version": "dhan-descriptive-opportunities-v1",
        "status": "EXPLORATORY_NOT_CAUSALLY_ELIGIBLE",
        "instrument": "NSE_EQ:2885:RELIANCE",
        "date_range": ["2026-05-01", "2026-10-31"],
        "source_integrity": "VERIFIED_FOR_PINNED_RESPONSES",
        "historical_identity": "UNVERIFIED_FOR_FULL_RANGE",
        "session_dates": "MATCH_NSE_2026_EQUITY_WEEKDAYS_LESS_HOLIDAYS",
        "cas_applicability": "UNVERIFIED_FOR_RELIANCE_FULL_RANGE",
        "bar_timestamp_and_publication": "UNVERIFIED",
        "corporate_action_adjustment": "UNVERIFIED",
        "causal_eligibility": "BLOCKED",
        "production_eligibility": "BLOCKED",
        "observed_execution_costs": None,
        "source": provenance,
        "session_count": len(sessions),
        "bar_count": len(bars),
        "regime_session_counts": dict(
            Counter(
                "BEFORE_2026_08_03" if day < "2026-08-03" else "FROM_2026_08_03"
                for day in sessions
            )
        ),
        "regime_labels": "DATE_BUCKETS_ONLY_NOT_DATED_RELIANCE_CAS_EVIDENCE",
        "horizons": describe(sessions),
        "notes": [
            "Overlapping retrospective reference paths are not trades.",
            "High/low extremes are one-minute buckets, with unknown order.",
            "Cost hurdles are assumed; no spread, fee, slippage or fill observed.",
            "No model, signal, paper fill or order is produced.",
        ],
    }
    _private_directory(args.output.parent)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(report, output, sort_keys=True, indent=2)
        output.write("\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "status": report["status"],
                "sessions": len(sessions),
                "bars": len(bars),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
