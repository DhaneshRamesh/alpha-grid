"""Offline, candle-only forward observations. No execution or model semantics."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from quant.acquisition.calendar import NseCalendar
from quant.acquisition.contracts import CashInstrument, HistoricalBar
from quant.acquisition.lake import LakeManifest, verify_dataset
from quant.acquisition.nse import file_sha256

MINUTE_NS = 60_000_000_000
HORIZONS = (5, 15, 30, 60)
REMAINING_SESSION = "remaining_session"
SCHEMA = "forward-observations-v2"
CAS_START = date(2026, 8, 3)


@dataclass(frozen=True, slots=True)
class TimingEvidence:
    source_dataset_sha256: str
    calendar_version: str
    source: str
    bar_start_verified: bool
    complete_at_bar_end_verified: bool

    def validate(self, manifest: LakeManifest, calendar: NseCalendar) -> None:
        if (
            not self.source
            or self.source_dataset_sha256 != manifest.dataset_sha256
            or self.calendar_version != calendar.version
            or not self.bar_start_verified
            or not self.complete_at_bar_end_verified
        ):
            raise ValueError("verified, dataset-bound candle timing evidence required")


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _validated_bars(
    bars: Iterable[HistoricalBar], calendar: NseCalendar, manifest: LakeManifest
) -> list[HistoricalBar]:
    result = list(bars)
    if not result:
        raise ValueError("empty verified candle dataset")
    previous: dict[str, tuple[date, int]] = {}
    for bar in result:
        stamp = bar.source_timestamp_ns
        if (
            bar.interval_minutes != 1
            or stamp is None
            or bar.timestamp_semantics != "BAR_START_VERIFIED"
            or bar.instrument.security_id not in manifest.instrument_universe
            or bar.source_sha256 not in manifest.source_checksums
        ):
            raise ValueError("unverified 1-minute candle or source lineage")
        # The default weekday schedule is only provisional. Require a dated,
        # explicitly verified session for every bar, including CAS applicability.
        session = calendar.session(bar.trading_date)
        if (
            session is None
            or not session.verified
            or bar.trading_date not in calendar.special
        ):
            raise ValueError("explicit verified session required for every candle")
        if bar.trading_date >= CAS_START:
            expected_closes = {
                "CAS_CONTINUOUS_TRADING": time(15, 15),
                "NON_CAS_CONTINUOUS_TRADING": time(15, 30),
            }
            if (
                session.kind not in expected_closes
                or session.closes != expected_closes[session.kind]
            ):
                raise ValueError(
                    "dated post-August closing-auction applicability required"
                )
        if stamp not in session.expected_starts(1):
            raise ValueError("candle outside verified continuous session")
        security_id = bar.instrument.security_id
        key = (bar.trading_date, stamp)
        if security_id in previous and key <= previous[security_id]:
            raise ValueError(
                "candles must be strictly ordered and unique per instrument"
            )
        previous[security_id] = key
    # Partitions can interleave instruments; validated per-instrument order is
    # regrouped deterministically without repairing a regression or duplicate.
    return sorted(
        result,
        key=lambda bar: (
            bar.instrument.security_id,
            bar.trading_date,
            bar.source_timestamp_ns or 0,
        ),
    )


def observations(
    bars: Iterable[HistoricalBar], calendar: NseCalendar
) -> list[dict[str, Any]]:
    """Build as-of rows using only the current completed bar and its predecessor."""
    rows: list[dict[str, Any]] = []
    prior: HistoricalBar | None = None
    for bar in bars:
        stamp = bar.source_timestamp_ns
        assert stamp is not None
        session = calendar.session(bar.trading_date)
        assert session is not None
        contiguous = (
            prior is not None
            and prior.instrument.security_id == bar.instrument.security_id
            and prior.trading_date == bar.trading_date
            and prior.source_timestamp_ns == stamp - MINUTE_NS
        )
        state = "ELIGIBLE" if contiguous else "INELIGIBLE_NO_CONTIGUOUS_PRIOR"
        asof_ns = stamp + MINUTE_NS
        observation_id = f"{bar.instrument.security_id}:{bar.trading_date}:{asof_ns}"
        rows.append(
            {
                "schema_version": SCHEMA,
                "observation_id": observation_id,
                "instrument": {
                    "venue": bar.instrument.venue,
                    "segment": bar.instrument.segment,
                    "security_id": bar.instrument.security_id,
                    "symbol": bar.instrument.symbol,
                },
                "session": bar.trading_date.isoformat(),
                "session_kind": session.kind,
                "decision_timestamp_ns": asof_ns,
                "latest_bar_start_ns": stamp,
                "eligibility": state,
                "source": {"name": bar.source, "sha256": bar.source_sha256},
                "features": {
                    "last_completed_close_nanos": bar.close_nanos,
                    "last_completed_volume": bar.volume,
                    "prior_close_return": (
                        bar.close_nanos / prior.close_nanos - 1
                        if contiguous and prior is not None
                        else None
                    ),
                },
            }
        )
        prior = bar
    return rows


def outcomes(
    bars: Iterable[HistoricalBar],
    observed: Iterable[dict[str, Any]],
    calendar: NseCalendar,
    *,
    as_of_ns: int,
) -> list[dict[str, Any]]:
    """Mature independent forward rows only after every needed candle is complete."""
    candles = list(bars)
    rows: list[dict[str, Any]] = []
    for index, observation in enumerate(observed):
        if observation["eligibility"] != "ELIGIBLE":
            continue
        decision = candles[index]
        stamp = decision.source_timestamp_ns
        assert stamp is not None
        session = calendar.session(decision.trading_date)
        assert session is not None
        for horizon in (*HORIZONS, REMAINING_SESSION):
            if horizon == REMAINING_SESSION:
                horizon_minutes = (
                    session.end_ns() - observation["decision_timestamp_ns"]
                ) // MINUTE_NS
            else:
                assert isinstance(horizon, int)
                horizon_minutes = horizon
            maturity = (
                observation["decision_timestamp_ns"] + horizon_minutes * MINUTE_NS
            )
            state = "PENDING" if as_of_ns < maturity else "MATURED"
            window: list[HistoricalBar] = []
            if horizon_minutes <= 0 or maturity > session.end_ns():
                state = "CENSORED_SESSION_END"
            else:
                for offset in range(1, horizon_minutes + 1):
                    candidate_index = index + offset
                    expected = stamp + offset * MINUTE_NS
                    if expected + MINUTE_NS > as_of_ns:
                        state = "PENDING"
                        break
                    if candidate_index >= len(candles):
                        state = "CENSORED_MISSING_CANDLE"
                        break
                    candidate = candles[candidate_index]
                    if (
                        candidate.instrument.security_id
                        != decision.instrument.security_id
                        or candidate.trading_date != decision.trading_date
                        or candidate.source_timestamp_ns != expected
                    ):
                        state = "CENSORED_MISSING_CANDLE"
                        break
                    window.append(candidate)
            row: dict[str, Any] = {
                "schema_version": SCHEMA,
                "observation_id": observation["observation_id"],
                "horizon": str(horizon),
                "horizon_minutes": horizon_minutes,
                "maturity_timestamp_ns": maturity,
                "state": state,
                "signed_return": None,
                "favorable_excursion": None,
                "adverse_excursion": None,
                "time_to_favorable_extreme_minutes": None,
                "time_to_adverse_extreme_minutes": None,
                "extreme_time_precision": "ONE_MINUTE_BUCKET_NOT_TICK_TIME",
                "provenance": "future_candle_ranges_no_execution_or_fill_claim",
            }
            if state == "MATURED" and len(window) == horizon_minutes:
                highest = max(item.high_nanos for item in window)
                lowest = min(item.low_nanos for item in window)
                entry = decision.close_nanos
                favorable = max(0.0, highest / entry - 1)
                adverse = min(0.0, lowest / entry - 1)
                row.update(
                    signed_return=window[-1].close_nanos / entry - 1,
                    favorable_excursion=favorable,
                    adverse_excursion=adverse,
                    time_to_favorable_extreme_minutes=next(
                        offset
                        for offset, item in enumerate(window, 1)
                        if item.high_nanos == highest
                    )
                    if favorable
                    else None,
                    time_to_adverse_extreme_minutes=next(
                        offset
                        for offset, item in enumerate(window, 1)
                        if item.low_nanos == lowest
                    )
                    if adverse
                    else None,
                )
            rows.append(row)
    return rows


def summarize(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    records = list(rows)
    result: dict[str, Any] = {}
    for horizon in (*HORIZONS, REMAINING_SESSION):
        selected = [row for row in records if row["horizon"] == str(horizon)]
        matured = [row for row in selected if row["state"] == "MATURED"]
        positive = sorted(
            row["signed_return"] for row in matured if row["signed_return"] > 0
        )
        negative = sorted(
            row["signed_return"] for row in matured if row["signed_return"] < 0
        )
        result[str(horizon)] = {
            "states": dict(sorted(Counter(row["state"] for row in selected).items())),
            "positive_count": len(positive),
            "negative_count": len(negative),
            "flat_count": len(matured) - len(positive) - len(negative),
            "positive_median": positive[(len(positive) - 1) // 2] if positive else None,
            "negative_median": negative[(len(negative) - 1) // 2] if negative else None,
            "mean_favorable_excursion": (
                sum(row["favorable_excursion"] for row in matured) / len(matured)
                if matured
                else None
            ),
            "mean_adverse_excursion": (
                sum(row["adverse_excursion"] for row in matured) / len(matured)
                if matured
                else None
            ),
        }
    return result


def _read_lake(root: Path, manifest: LakeManifest) -> list[HistoricalBar]:
    bars: list[HistoricalBar] = []
    for part in manifest.partitions:
        for row in pq.read_table(root / part.path).to_pylist():
            bars.append(
                HistoricalBar(
                    CashInstrument(
                        row["venue"],
                        row["segment"],
                        row["security_id"],
                        row["symbol"],
                        row["isin"],
                    ),
                    row["interval_minutes"],
                    row["trading_date"],
                    row["source_timestamp_ns"],
                    row["open_nanos"],
                    row["high_nanos"],
                    row["low_nanos"],
                    row["close_nanos"],
                    row["volume"],
                    row["source"],
                    row["source_sha256"],
                    row["timestamp_semantics"],
                    row["turnover_nanos"],
                    row["delivery_quantity"],
                )
            )
    return bars


def generate(
    lake_dir: Path,
    raw_root: Path,
    output_dir: Path,
    calendar: NseCalendar,
    evidence: TimingEvidence,
    *,
    as_of_ns: int,
) -> dict[str, Any]:
    """Verify immutable source, then publish independently hashed offline files."""
    if output_dir.exists() or as_of_ns <= 0:
        raise ValueError("new output directory and positive as-of timestamp required")
    manifest = verify_dataset(lake_dir)
    if (
        manifest.acquisition_mode != "DHAN_SAVED_RESPONSE"
        or set(manifest.sources) != {"DHAN_HISTORICAL"}
        or not manifest.source_provenance
        or {item.sha256 for item in manifest.source_provenance}
        != set(manifest.source_checksums)
    ):
        raise ValueError("verified Dhan original-response lineage required")
    for item in manifest.source_provenance:
        try:
            retrieved = datetime.fromisoformat(item.acquired_at)
        except ValueError as exc:
            raise ValueError("Dhan retrieval timestamp missing or invalid") from exc
        if (
            item.format_version != "DHAN_CHARTS_V2"
            or not item.source_url.startswith("https://api.dhan.co/")
            or re.fullmatch(r"[0-9a-f]{64}", item.sha256) is None
            or retrieved.tzinfo is None
        ):
            raise ValueError("Dhan request and retrieval metadata required")
        original = raw_root / "DHAN_HISTORICAL" / item.sha256
        if not original.is_file() or file_sha256(original) != item.sha256:
            raise ValueError("original Dhan response bytes missing or changed")
    evidence.validate(manifest, calendar)
    if manifest.calendar_version != calendar.version:
        raise ValueError("source lake and session calendar versions differ")
    bars = _read_lake(lake_dir, manifest)
    if any(bar.source != "DHAN_HISTORICAL" for bar in bars):
        raise ValueError("Dhan candles only")
    bars = _validated_bars(bars, calendar, manifest)
    if len(bars) != manifest.row_count:
        raise ValueError("source lake row count mismatch")
    bars = [
        bar
        for bar in bars
        if bar.source_timestamp_ns is not None
        and bar.source_timestamp_ns + MINUTE_NS <= as_of_ns
    ]
    if not bars:
        raise ValueError("no completed candles at requested as-of timestamp")
    observed = observations(bars, calendar)
    forward = outcomes(bars, observed, calendar, as_of_ns=as_of_ns)
    report = {
        "schema_version": SCHEMA,
        "source_dataset_sha256": manifest.dataset_sha256,
        "calendar_version": calendar.version,
        "timing_evidence": asdict(evidence),
        "as_of_ns": as_of_ns,
        "observations": len(observed),
        "eligible": sum(row["eligibility"] == "ELIGIBLE" for row in observed),
        "exclusions": dict(
            sorted(
                Counter(
                    row["eligibility"]
                    for row in observed
                    if row["eligibility"] != "ELIGIBLE"
                ).items()
            )
        ),
        "movement_distribution": summarize(forward),
        "status": "EXPLORATORY_CANDLE_OBSERVATIONS_NO_TRAINING_OR_FILL_CLAIM",
    }
    staging = output_dir.parent / f".{output_dir.name}.{uuid.uuid4().hex}.partial"
    staging.mkdir(parents=True)
    try:
        for name, rows in (
            ("observations.jsonl", observed),
            ("outcomes.jsonl", forward),
        ):
            with (staging / name).open("xb") as stream:
                for row in rows:
                    stream.write(_canonical(row))
                stream.flush()
                os.fsync(stream.fileno())
        (staging / "summary.json").write_bytes(_canonical(report))
        checksums = {
            name: file_sha256(staging / name)
            for name in ("observations.jsonl", "outcomes.jsonl", "summary.json")
        }
        (staging / "manifest.json").write_bytes(
            _canonical(
                {
                    "schema_version": SCHEMA,
                    "source_dataset_sha256": manifest.dataset_sha256,
                    "files": checksums,
                }
            )
        )
        os.replace(staging, output_dir)
        return report
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
