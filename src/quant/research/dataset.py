"""Causal QP01 feature extraction joined to future QJ01 prices offline."""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_right
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from quant.research.io import read_market_export, read_paper
from quant.research.labels import (
    PriceObservation,
    instrument_key,
    label_path,
    market_observation,
)
from quant.research.sufficiency import assess

SCHEMA_VERSION = "research-v1"
DEFAULT_HORIZONS_NS = (5, 15, 30, 60, 180)
FEATURE_NAMES = (
    "relative_spread",
    "microprice_displacement",
    "bid_ask_imbalance",
    "snapshot_ofi_quantity",
    "latest_price_return",
    "rolling_return_volatility",
    "rolling_volume_activity",
    "price_volume_correlation",
    "bid_level_weighted_depth",
    "ask_level_weighted_depth",
)


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    schema_version: str
    market_sha256: str
    paper_sha256: str
    feature_sha256: str
    label_sha256: str
    dataset_sha256: str
    source_kind: str
    horizons_ns: tuple[int, ...]
    max_gap_ns: int
    feature_rows: int
    label_rows: int
    independent_sessions: int
    status: str
    sufficiency_reasons: tuple[str, ...]
    label_fraction: float
    quality_fraction: float
    volume_complete_fraction: float
    horizon_coverage_fraction: float
    favorable_events: int
    adverse_events: int


def _feature_row(record: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    decision = record["event"]["Decision"]
    context = decision["context"]
    asof = context["source_receive_timestamp_ns"]
    index = context["source_record_index"]
    if index != record["source_record_index"]:
        raise ValueError("paper source index mismatch")
    if context["session_id"] != market["session_id"]:
        raise ValueError("paper source session mismatch")
    qj_event = market["event"]
    if "Depth" not in qj_event:
        raise ValueError("paper decision source is not a depth record")
    key = instrument_key(context["instrument"])
    if instrument_key(qj_event["Depth"]["instrument"]) != key:
        raise ValueError("paper instrument does not match market source")
    if qj_event["Depth"]["receive_timestamp_ns"] != asof:
        raise ValueError("paper as-of time does not match market source")
    for name in ("market", "features", "health"):
        snap = context[name]
        if snap["processing_timestamp_ns"] > asof:
            raise ValueError(f"future {name} snapshot in paper decision")
        if instrument_key(snap["instrument"]) != key:
            raise ValueError(f"cross-instrument {name} snapshot")
    depth_time = context["market"].get("depth_receive_timestamp_ns")
    if depth_time is not None and depth_time > asof:
        raise ValueError("future book in paper decision")
    features = context["features"]
    quality = features["quality"]
    snapshot = context["market"]
    row: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "instrument_key": key,
        "security_id": context["instrument"]["security_id"],
        "session_id": context["session_id"],
        "asof_receive_ns": asof,
        "source_record_index": index,
        "decision_id": decision["decision_id"],
        "feature_schema_version": features["schema_version"],
        "model_version": decision["model"]["model_version"],
        "experiment_version": decision["policy_version"],
        "price_nanos": snapshot["price_nanos"],
        "price_provenance": "quote_sampled_ltp_or_depth_snapshot",
        "best_bid_nanos": (snapshot.get("best_bid") or {}).get("price_nanos"),
        "best_ask_nanos": (snapshot.get("best_ask") or {}).get("price_nanos"),
        "best_bid_qty": (snapshot.get("best_bid") or {}).get("quantity"),
        "best_ask_qty": (snapshot.get("best_ask") or {}).get("quantity"),
        "absolute_spread_nanos": features["absolute_spread_nanos"],
        "microprice_nanos": features["microprice_nanos"],
        "freshness": features["freshness"],
        "source_time_unverified": quality["source_time_unverified"],
        "volume_interval_incomplete": quality["volume_interval_incomplete"],
        "cross_feed_uncertain": quality["cross_feed_uncertain"],
        "stale": quality["stale"],
        "journal_healthy": context["journal_healthy"],
        "feed_connected": context["feed_connected"],
        "candidate_action": decision["candidate"]["action"],
        "actual_paper_action": decision["action"],
        "candidate_horizon_ns": decision["candidate"]["horizon_ns"],
        "fee_schedule_version": (decision["cost"] or {}).get("fee_schedule_version"),
        "estimated_cost_nanos": (decision["cost"] or {}).get("expected_total_nanos"),
        "cost_provisional": (decision["cost"] or {}).get("provisional"),
        "risk_approved_for_paper": decision["risk"]["approved_for_paper"],
        "outcome_provenance": "observed_market_path_only",
        "behavior_action_probability": None,
    }
    row.update({name: features.get(name) for name in FEATURE_NAMES})
    mid = features.get("midprice_nanos")
    micro = features.get("microprice_nanos")
    row["microprice_displacement"] = (micro / mid - 1) if mid and micro else None
    return row


def build_dataset(
    *,
    market_jsonl: Path,
    paper_qp: Path,
    output_dir: Path,
    horizons_seconds: tuple[int, ...] = DEFAULT_HORIZONS_NS,
    profit_barrier_nanos: int = 100_000_000,
    loss_barrier_nanos: int = 100_000_000,
    max_gap_seconds: int = 5,
    source_kind: str = "UNVERIFIED",
) -> DatasetManifest:
    """Reject bad joins; write immutable feature/label Parquet and hash lineage."""
    if output_dir.exists():
        raise FileExistsError("research output directory must be new")
    if not horizons_seconds or any(h <= 0 for h in horizons_seconds):
        raise ValueError("positive horizons required")
    if max_gap_seconds <= 0:
        raise ValueError("max_gap_seconds must be positive")
    market_rows = list(read_market_export(market_jsonl))
    indexed = {row["source_record_index"]: row["record"] for row in market_rows}
    observations: dict[tuple[str, str], list[PriceObservation]] = defaultdict(list)
    disconnects: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row in market_rows:
        record = row["record"]
        observation = market_observation(row)
        if observation is not None:
            observations[(observation.session_id, observation.instrument_key)].append(
                observation
            )
        event = record["event"]
        if "Session" in event and "Disconnected" in str(event["Session"]["kind"]):
            value = event["Session"]
            stamp = value["metadata"]["receive_timestamp_ns"]
            inst = value.get("instrument")
            keys = (
                [instrument_key(inst)]
                if inst
                else [
                    key
                    for session, key in observations
                    if session == record["session_id"]
                ]
            )
            for key in keys:
                disconnects[(record["session_id"], key)].append(stamp)
    feature_rows: list[dict[str, Any]] = []
    label_rows: list[dict[str, Any]] = []
    for points in observations.values():
        points.sort(key=lambda point: (point.receive_ns, point.source_index))
    timestamps = {
        key: [point.receive_ns for point in points]
        for key, points in observations.items()
    }
    for record in read_paper(paper_qp):
        if "Decision" not in record["event"]:
            continue
        index = record["source_record_index"]
        if index not in indexed:
            raise ValueError("paper record references absent market source")
        row = _feature_row(record, indexed[index])
        feature_rows.append(row)
        session_instrument_key = (row["session_id"], row["instrument_key"])
        all_points = observations[session_instrument_key]
        all_times = timestamps[session_instrument_key]
        start = bisect_right(all_times, row["asof_receive_ns"])
        stop = bisect_right(
            all_times,
            row["asof_receive_ns"]
            + max(horizons_seconds) * 1_000_000_000
            + max_gap_seconds * 1_000_000_000,
        )
        paths = all_points[start:stop]
        cutoff = next(
            (
                time
                for time in disconnects[session_instrument_key]
                if time > row["asof_receive_ns"]
            ),
            None,
        )
        for seconds in horizons_seconds:
            price = row["price_nanos"]
            label = (
                label_path(
                    asof_index=index,
                    asof_ns=row["asof_receive_ns"],
                    entry_nanos=price,
                    observations=paths,
                    horizon_ns=seconds * 1_000_000_000,
                    profit_barrier_nanos=profit_barrier_nanos,
                    loss_barrier_nanos=loss_barrier_nanos,
                    max_gap_ns=max_gap_seconds * 1_000_000_000,
                    session_end_ns=cutoff,
                )
                if price is not None
                else None
            )
            label_rows.append(
                {
                    "decision_id": row["decision_id"],
                    "instrument_key": row["instrument_key"],
                    "session_id": row["session_id"],
                    "asof_receive_ns": row["asof_receive_ns"],
                    "source_record_index": index,
                    "horizon_ns": seconds * 1_000_000_000,
                    "label_available": label.available if label else False,
                    "horizon_covered": label.horizon_covered if label else False,
                    "neither_observed": (
                        label.horizon_covered and label.first_event is None
                    )
                    if label
                    else False,
                    "right_censored": label.censored if label else True,
                    "ordering_ambiguous": label.ambiguous if label else False,
                    "first_event": label.first_event if label else None,
                    "first_event_ns": label.first_event_ns if label else None,
                    "forward_return": label.forward_return if label else None,
                    "mfe_nanos": label.mfe_nanos if label else None,
                    "mae_nanos": label.mae_nanos if label else None,
                    "observation_count": label.observation_count if label else 0,
                    "censor_reason": label.censor_reason
                    if label
                    else "missing_entry_price",
                    "outcome_provenance": label.provenance if label else "unavailable",
                }
            )
    if not feature_rows:
        raise ValueError("no decision records")
    feature_hash = hashlib.sha256(canonical(feature_rows)).hexdigest()
    label_hash = hashlib.sha256(canonical(label_rows)).hexdigest()
    market_hash, paper_hash = digest_file(market_jsonl), digest_file(paper_qp)
    dataset_hash = hashlib.sha256(
        canonical(
            {
                "schema": SCHEMA_VERSION,
                "market": market_hash,
                "paper": paper_hash,
                "features": feature_hash,
                "labels": label_hash,
                "horizons": horizons_seconds,
                "max_gap_seconds": max_gap_seconds,
                "profit_barrier_nanos": profit_barrier_nanos,
                "loss_barrier_nanos": loss_barrier_nanos,
            }
        )
    ).hexdigest()
    sufficiency = assess(feature_rows, label_rows, source_kind=source_kind)
    manifest = DatasetManifest(
        SCHEMA_VERSION,
        market_hash,
        paper_hash,
        feature_hash,
        label_hash,
        dataset_hash,
        source_kind,
        tuple(h * 1_000_000_000 for h in horizons_seconds),
        max_gap_seconds * 1_000_000_000,
        len(feature_rows),
        len(label_rows),
        len({r["session_id"] for r in feature_rows}),
        sufficiency.status,
        sufficiency.reasons,
        sufficiency.label_fraction,
        sufficiency.quality_fraction,
        sufficiency.volume_complete_fraction,
        sum(bool(row["horizon_covered"]) for row in label_rows) / len(label_rows),
        sufficiency.favorable_events,
        sufficiency.adverse_events,
    )
    output_dir.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist(feature_rows),
        output_dir / "features.parquet",
        compression="zstd",
    )
    pq.write_table(
        pa.Table.from_pylist(label_rows),
        output_dir / "labels.parquet",
        compression="zstd",
    )
    (output_dir / "manifest.json").write_bytes(canonical(asdict(manifest)) + b"\n")
    return manifest
