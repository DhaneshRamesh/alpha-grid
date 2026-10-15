"""Sanitized, read-only acquisition status prepared offline for the cockpit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from quant.acquisition.lake import verify_dataset
from quant.acquisition.quality import QualityReport


def acquisition_status(
    *,
    dataset_dir: Path | None = None,
    quality: QualityReport | None = None,
    configured_sources: tuple[str, ...] = (
        "DHAN_HISTORICAL_DISABLED",
        "NSE_LOCAL_IMPORT",
    ),
) -> dict[str, Any]:
    """Access/entitlement stays UNVERIFIED without independently checked evidence."""
    result: dict[str, Any] = {
        "schema_version": "acquisition-status-v1",
        "sources_configured": list(configured_sources),
        "access_status": "UNVERIFIED",
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
        "blocked_model_families": ["DEPTH", "ORDER_FLOW", "HAWKES", "EXECUTION"],
        "block_reasons": ["no_verified_real_market_dataset"],
    }
    if dataset_dir is not None:
        manifest = verify_dataset(dataset_dir)
        result.update(
            first_date=manifest.first_date,
            last_date=manifest.last_date,
            instrument_count=len(manifest.instrument_universe),
            partition_count=len(manifest.partitions),
            partition_bytes=sum(item.bytes for item in manifest.partitions),
            checksum_verified=True,
            collection_mode=manifest.acquisition_mode,
            block_reasons=[
                "historical_candles_do_not_supply_depth_or_recorded_decisions",
                "independent_statistical_validation_not_performed",
            ],
        )
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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, sort_keys=True, separators=(",", ":")) + "\n")
