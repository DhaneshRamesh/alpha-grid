"""Sanitized, read-only acquisition status prepared offline for the cockpit."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from quant.acquisition.lake import verify_dataset
from quant.acquisition.nse import file_sha256
from quant.acquisition.quality import QualityReport


def acquisition_status(
    *,
    dataset_dir: Path | None = None,
    quality: QualityReport | None = None,
    raw_root: Path | None = None,
    configured_sources: tuple[str, ...] = (
        "DHAN_HISTORICAL_DISABLED",
        "NSE_LOCAL_IMPORT",
    ),
) -> dict[str, Any]:
    """Access/entitlement stays UNVERIFIED without independently checked evidence."""
    result: dict[str, Any] = {
        "schema_version": "acquisition-status-v1",
        "status": "WAITING_FOR_REAL_SOURCE",
        "sources_configured": list(configured_sources),
        "access_status": "UNVERIFIED",
        "source_files_available": 0,
        "genuine_source_files_available": 0,
        "source_verification_status": "UNVERIFIED",
        "calendar_completeness": "UNVERIFIED",
        "mapping_uncertainty": "UNVERIFIED",
        "first_date": None,
        "last_date": None,
        "instrument_count": 0,
        "independent_sessions": 0,
        "missing_bars": None,
        "quality_warning_count": None,
        "partition_count": 0,
        "partition_bytes": 0,
        "checksum_verified": False,
        "collection_mode": "DATA_ONLY_OFFLINE",
        "research_eligibility": "NOT_TRAINED",
        "historical_dataset_eligibility": "BLOCKED",
        "training_blocked": True,
        "blocked_model_families": ["DEPTH", "ORDER_FLOW", "HAWKES", "EXECUTION"],
        "block_reasons": ["no_verified_real_market_dataset"],
    }
    if dataset_dir is not None:
        manifest = verify_dataset(dataset_dir)
        observed_ids: set[str] = set()
        for part in manifest.partitions:
            column = pq.read_table(dataset_dir / part.path, columns=["security_id"])
            observed_ids.update(column.column("security_id").to_pylist())
        available = 0
        if raw_root is not None:
            for provenance in manifest.source_provenance:
                raw = raw_root / f"NSE_{provenance.format_version}" / provenance.sha256
                if raw.is_file():
                    if file_sha256(raw) != provenance.sha256:
                        raise ValueError("original source checksum mismatch")
                    available += 1
        result.update(
            status="SOURCE_AUDIT_REQUIRED",
            first_date=manifest.first_date,
            last_date=manifest.last_date,
            instrument_count=len(observed_ids),
            source_files_available=available,
            partition_count=len(manifest.partitions),
            partition_bytes=sum(item.bytes for item in manifest.partitions),
            checksum_verified=True,
            collection_mode=manifest.acquisition_mode,
            block_reasons=[
                "original_source_not_independently_verified",
                "calendar_and_mapping_evidence_unverified",
                "historical_candles_do_not_supply_depth_or_recorded_decisions",
                "independent_statistical_validation_not_performed",
            ],
        )
        if not manifest.source_provenance:
            result["block_reasons"].append("source_provenance_missing")
        elif available != len(manifest.source_provenance):
            result["block_reasons"].append("original_source_file_missing")
    if quality is not None:
        result.update(
            independent_sessions=quality.independent_sessions,
            missing_bars=quality.missing_bars,
            quality_warning_count=len(quality.issues),
        )
        if quality.hard_failures:
            result["block_reasons"].append("data_quality_hard_failures")
    return result


def write_status(path: Path, status: dict[str, Any]) -> None:
    if status.get("schema_version") != "acquisition-status-v1":
        raise ValueError("invalid acquisition status schema")
    write_json_atomic(path, status)


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    """Publish reproducible sidecars without exposing a partially written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.partial"
    try:
        with temporary.open("xb") as stream:
            stream.write(
                (
                    json.dumps(
                        value, sort_keys=True, separators=(",", ":"), default=str
                    )
                    + "\n"
                ).encode()
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
