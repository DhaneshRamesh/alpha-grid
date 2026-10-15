"""Separate, candle-only, causal research rows; no QJ/QP or execution claims."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from quant.acquisition.calendar import NseCalendar
from quant.acquisition.contracts import HistoricalBar
from quant.acquisition.lake import LakeManifest
from quant.acquisition.nse import file_sha256

RESEARCH_SCHEMA_VERSION = "ohlcv-research-v1"


@dataclass(frozen=True, slots=True)
class CandleResearchPolicy:
    history_bars: int = 3
    horizon_bars: int = 3
    profit_barrier_nanos: int = 1_000_000_000
    stop_barrier_nanos: int = 1_000_000_000
    publication_lag_ns: int = 0

    def __post_init__(self) -> None:
        if (
            min(
                self.history_bars,
                self.horizon_bars,
                self.profit_barrier_nanos,
                self.stop_barrier_nanos,
            )
            <= 0
            or self.publication_lag_ns < 0
        ):
            raise ValueError(
                "positive history, horizon, barriers and nonnegative lag required"
            )


@dataclass(frozen=True, slots=True)
class CandleFeature:
    security_id: str
    trading_date: str
    asof_ns: int
    latest_source_timestamp_ns: int | None
    latest_close_nanos: int
    prior_close_return: float | None
    past_mean_volume: float
    history_bars: int
    provenance: str = "completed_ohlcv_only"


@dataclass(frozen=True, slots=True)
class CandleLabel:
    security_id: str
    trading_date: str
    asof_ns: int
    horizon_bars: int
    first_event: str | None
    ambiguous: bool
    right_censored: bool
    forward_close_return: float | None
    observed_future_bars: int
    provenance: str = "future_ohlcv_range_only_no_fill_claim"


def _available_at(
    bar: HistoricalBar,
    policy: CandleResearchPolicy,
    daily_available_at: Callable[[HistoricalBar], int] | None,
) -> int:
    if bar.interval_minutes is None:
        if daily_available_at is None:
            raise ValueError("daily bars require verified publication timestamps")
        result = daily_available_at(bar)
        if result <= 0:
            raise ValueError("invalid daily publication timestamp")
        return result
    if (
        bar.timestamp_semantics != "BAR_START_VERIFIED"
        or bar.source_timestamp_ns is None
    ):
        raise ValueError("intraday candle start semantics must be verified")
    interval_ns = bar.interval_minutes * 60 * 1_000_000_000
    if policy.publication_lag_ns >= interval_ns:
        raise ValueError("publication lag must be shorter than candle interval")
    return bar.source_timestamp_ns + interval_ns + policy.publication_lag_ns


def _label(
    decision: HistoricalBar,
    future: list[HistoricalBar],
    asof_ns: int,
    policy: CandleResearchPolicy,
    calendar: NseCalendar | None,
) -> CandleLabel:
    complete = len(future) == policy.horizon_bars
    if decision.interval_minutes is not None:
        if decision.source_timestamp_ns is None:
            raise ValueError("intraday decision missing timestamp")
        step = decision.interval_minutes * 60 * 1_000_000_000
        previous = decision.source_timestamp_ns
        for bar in future:
            if (
                bar.trading_date != decision.trading_date
                or bar.source_timestamp_ns != previous + step
            ):
                complete = False
                break
            previous = bar.source_timestamp_ns
    elif calendar is not None and future:
        next_session = calendar.session(future[0].trading_date)
        if next_session is None or asof_ns >= next_session.start_ns():
            complete = False
        expected = [
            session.day
            for session in calendar.sessions(
                decision.trading_date, future[-1].trading_date + timedelta(days=1)
            )
        ]
        if expected[: len(future) + 1] != [decision.trading_date] + [
            bar.trading_date for bar in future
        ]:
            complete = False
    # A missing bar or session boundary censors the path. Do not skip gaps.
    first: str | None = None
    ambiguous = False
    if complete:
        for bar in future:
            hit_profit = (
                bar.high_nanos >= decision.close_nanos + policy.profit_barrier_nanos
            )
            hit_stop = bar.low_nanos <= decision.close_nanos - policy.stop_barrier_nanos
            if hit_profit and hit_stop:
                first = "AMBIGUOUS"
                ambiguous = True
                break
            if hit_profit or hit_stop:
                first = "FAVORABLE" if hit_profit else "ADVERSE"
                break
    return CandleLabel(
        decision.instrument.security_id,
        decision.trading_date.isoformat(),
        asof_ns,
        policy.horizon_bars,
        first if complete else None,
        ambiguous,
        not complete or ambiguous,
        future[-1].close_nanos / decision.close_nanos - 1
        if complete and not ambiguous
        else None,
        len(future),
    )


def iter_research_rows(
    bars: Iterable[HistoricalBar],
    policy: CandleResearchPolicy,
    *,
    daily_available_at: Callable[[HistoricalBar], int] | None = None,
    calendar: NseCalendar | None = None,
) -> Iterator[tuple[CandleFeature, CandleLabel]]:
    """Hold only history + horizon bars, then emit an as-of feature and label."""
    past: deque[HistoricalBar] = deque(maxlen=policy.history_bars)
    pending: deque[tuple[CandleFeature, HistoricalBar, list[HistoricalBar]]] = deque()
    previous: HistoricalBar | None = None
    previous_availability = -1

    for bar in bars:
        availability = _available_at(bar, policy, daily_available_at)
        if bar.interval_minutes is None:
            session = calendar.session(bar.trading_date) if calendar else None
            if session is None or availability < session.end_ns():
                raise ValueError(
                    "daily bar needs verified calendar and post-close availability"
                )
        if previous is not None:
            old_stamp = (
                previous.source_timestamp_ns or previous.trading_date.toordinal()
            )
            new_stamp = bar.source_timestamp_ns or bar.trading_date.toordinal()
            if bar.instrument.security_id != previous.instrument.security_id:
                while pending:
                    feature, decision, future = pending.popleft()
                    yield (
                        feature,
                        _label(decision, future, feature.asof_ns, policy, calendar),
                    )
                past.clear()
                previous_availability = -1
            elif new_stamp <= old_stamp or availability <= previous_availability:
                raise ValueError(
                    "research bars must be ordered by source and availability"
                )
        for _, _, future in pending:
            future.append(bar)
        while pending and len(pending[0][2]) >= policy.horizon_bars:
            feature, decision, future = pending.popleft()
            yield feature, _label(decision, future, feature.asof_ns, policy, calendar)
        past.append(bar)
        if len(past) == policy.history_bars:
            prior_return = (
                bar.close_nanos / past[-2].close_nanos - 1 if len(past) > 1 else None
            )
            feature = CandleFeature(
                bar.instrument.security_id,
                bar.trading_date.isoformat(),
                availability,
                bar.source_timestamp_ns,
                bar.close_nanos,
                prior_return,
                sum(item.volume for item in past) / len(past),
                len(past),
            )
            pending.append((feature, bar, []))
        previous, previous_availability = bar, availability
    while pending:
        feature, decision, future = pending.popleft()
        yield feature, _label(decision, future, feature.asof_ns, policy, calendar)


FEATURE_SCHEMA = pa.schema(
    [
        pa.field("security_id", pa.string()),
        pa.field("trading_date", pa.string()),
        pa.field("asof_ns", pa.int64()),
        pa.field("latest_source_timestamp_ns", pa.int64()),
        pa.field("latest_close_nanos", pa.int64()),
        pa.field("prior_close_return", pa.float64()),
        pa.field("past_mean_volume", pa.float64()),
        pa.field("history_bars", pa.int16()),
        pa.field("provenance", pa.string()),
    ]
)
LABEL_SCHEMA = pa.schema(
    [
        pa.field("security_id", pa.string()),
        pa.field("trading_date", pa.string()),
        pa.field("asof_ns", pa.int64()),
        pa.field("horizon_bars", pa.int16()),
        pa.field("first_event", pa.string()),
        pa.field("ambiguous", pa.bool_()),
        pa.field("right_censored", pa.bool_()),
        pa.field("forward_close_return", pa.float64()),
        pa.field("observed_future_bars", pa.int16()),
        pa.field("provenance", pa.string()),
    ]
)


def write_research_rows(
    rows: Iterable[tuple[CandleFeature, CandleLabel]],
    output_dir: Path,
    *,
    source_manifest: LakeManifest,
    policy: CandleResearchPolicy,
    dataset_id: str,
    batch_size: int = 8192,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    if output_dir.exists() or batch_size <= 0 or not dataset_id:
        raise ValueError("new output, dataset ID and positive batch size required")
    staging = output_dir.parent / f".{output_dir.name}.{uuid.uuid4().hex}.partial"
    staging.mkdir(parents=True)
    features = pq.ParquetWriter(
        staging / "features.parquet", FEATURE_SCHEMA, compression="zstd"
    )
    labels = pq.ParquetWriter(
        staging / "labels.parquet", LABEL_SCHEMA, compression="zstd"
    )
    feature_batch: list[dict[str, Any]] = []
    label_batch: list[dict[str, Any]] = []
    count = 0
    sessions: set[tuple[str, str]] = set()
    instruments: set[str] = set()
    censored = ambiguous = favorable = adverse = 0

    def flush() -> None:
        if feature_batch:
            features.write_table(
                pa.Table.from_pylist(feature_batch, schema=FEATURE_SCHEMA)
            )
            labels.write_table(pa.Table.from_pylist(label_batch, schema=LABEL_SCHEMA))
            feature_batch.clear()
            label_batch.clear()

    try:
        for feature, label in rows:
            if (feature.security_id, feature.asof_ns) != (
                label.security_id,
                label.asof_ns,
            ):
                raise ValueError("feature/label as-of mismatch")
            feature_batch.append(asdict(feature))
            label_batch.append(asdict(label))
            count += 1
            sessions.add((feature.security_id, feature.trading_date))
            instruments.add(feature.security_id)
            censored += label.right_censored
            ambiguous += label.ambiguous
            favorable += label.first_event == "FAVORABLE"
            adverse += label.first_event == "ADVERSE"
            if len(feature_batch) >= batch_size:
                flush()
        flush()
        features.close()
        labels.close()
        for name in ("features.parquet", "labels.parquet"):
            with (staging / name).open("rb") as stream:
                os.fsync(stream.fileno())
        if not count:
            raise ValueError("no research rows")
        manifest: dict[str, Any] = {
            "schema_version": RESEARCH_SCHEMA_VERSION,
            "dataset_id": dataset_id,
            "source_dataset_sha256": source_manifest.dataset_sha256,
            "sources": list(source_manifest.sources),
            "acquisition_mode": source_manifest.acquisition_mode,
            "instrument_universe": list(source_manifest.instrument_universe),
            "first_date": source_manifest.first_date,
            "last_date": source_manifest.last_date,
            "timezone": source_manifest.timezone,
            "calendar_version": source_manifest.calendar_version,
            "adjustment_policy": source_manifest.adjustment_policy,
            "source_checksums": list(source_manifest.source_checksums),
            "quality_exclusions": list(source_manifest.quality_exclusions),
            "code_commit": source_manifest.code_commit,
            "policy": asdict(policy),
            "feature_rows": count,
            "label_rows": count,
            "instrument_count": len(instruments),
            "independent_sessions": len(sessions),
            "censored_labels": censored,
            "ambiguous_labels": ambiguous,
            "favorable_events": favorable,
            "adverse_events": adverse,
            "effective_sample_size_estimate": (count - censored) // policy.horizon_bars,
            "market_regime_coverage": "UNKNOWN",
            "train_validation_test_separation": "NOT_PERFORMED",
            "corporate_action_uncertainty": "UNKNOWN",
            "feature_sha256": file_sha256(staging / "features.parquet"),
            "label_sha256": file_sha256(staging / "labels.parquet"),
            "status": "NOT_TRAINED",
            "blocked_model_families": ["DEPTH", "ORDER_FLOW", "HAWKES", "EXECUTION"],
            "reason": "historical_ohlcv_cannot_replace_recorded_depth_and_decisions",
        }
        manifest["dataset_sha256"] = hashlib.sha256(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        manifest["created_at"] = (
            (created_at or datetime.now(UTC)).astimezone(UTC).isoformat()
        )
        with (staging / "manifest.json").open("xb") as output_stream:
            output_stream.write(json.dumps(manifest, sort_keys=True).encode() + b"\n")
            output_stream.flush()
            os.fsync(output_stream.fileno())
        os.replace(staging, output_dir)
        return manifest
    except BaseException:
        features.close()
        labels.close()
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_research_dataset(output_dir: Path) -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads((output_dir / "manifest.json").read_bytes())
    for name, filename in (
        ("feature", "features.parquet"),
        ("label", "labels.parquet"),
    ):
        if file_sha256(output_dir / filename) != manifest[f"{name}_sha256"]:
            raise ValueError("research Parquet checksum mismatch")
    core = {
        key: value
        for key, value in manifest.items()
        if key not in {"dataset_sha256", "created_at"}
    }
    digest = hashlib.sha256(
        json.dumps(core, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if digest != manifest["dataset_sha256"]:
        raise ValueError("research dataset manifest checksum mismatch")
    return manifest
