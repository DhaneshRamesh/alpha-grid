"""Training eligibility is evidence based and deliberately conservative."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SufficiencyPolicy:
    min_independent_sessions: int = 20
    min_usable_labels: int = 500
    min_label_fraction: float = 0.8
    min_quality_fraction: float = 0.95
    require_complete_volume: bool = False
    min_events_per_cause: int = 30


@dataclass(frozen=True, slots=True)
class SufficiencyResult:
    status: str
    reasons: tuple[str, ...]
    independent_sessions: int
    usable_labels: int
    label_fraction: float
    quality_fraction: float
    volume_complete_fraction: float
    favorable_events: int
    adverse_events: int


DEFAULT_POLICY = SufficiencyPolicy()


def assess(
    features: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    *,
    source_kind: str,
    data_modality: str = "DEPTH_EVENTS",
    policy: SufficiencyPolicy = DEFAULT_POLICY,
) -> SufficiencyResult:
    if (
        not 0 <= policy.min_label_fraction <= 1
        or not 0 <= policy.min_quality_fraction <= 1
    ):
        raise ValueError("fractions must be in [0, 1]")
    sessions = len({row["session_id"] for row in features})
    usable = sum(
        bool(row.get("label_available")) and not row.get("ordering_ambiguous")
        for row in labels
    )
    label_fraction = usable / len(labels) if labels else 0.0
    favorable_events = sum(
        bool(row.get("label_available")) and row.get("first_event") == "FAVORABLE"
        for row in labels
    )
    adverse_events = sum(
        bool(row.get("label_available")) and row.get("first_event") == "ADVERSE"
        for row in labels
    )
    quality_count = sum(
        bool(
            row.get("freshness") == "Fresh"
            and not row.get("stale")
            and row.get("journal_healthy")
            and row.get("feed_connected")
        )
        for row in features
    )
    quality_fraction = quality_count / len(features) if features else 0.0
    volume_complete_fraction = (
        sum(not row.get("volume_interval_incomplete") for row in features)
        / len(features)
        if features
        else 0.0
    )
    reasons = []
    if data_modality != "DEPTH_EVENTS":
        reasons.append("depth_models_require_recorded_depth_events")
    if source_kind != "REAL_VERIFIED":
        reasons.append("source_is_not_verified_real_market_data")
    if sessions < policy.min_independent_sessions:
        reasons.append("insufficient_independent_sessions")
    if usable < policy.min_usable_labels:
        reasons.append("insufficient_usable_labels")
    if min(favorable_events, adverse_events) < policy.min_events_per_cause:
        reasons.append("insufficient_competing_event_support")
    if label_fraction < policy.min_label_fraction:
        reasons.append("incomplete_forward_coverage")
    if quality_fraction < policy.min_quality_fraction:
        reasons.append("insufficient_data_quality")
    if (
        policy.require_complete_volume
        and volume_complete_fraction < policy.min_quality_fraction
    ):
        reasons.append("insufficient_volume_quality")
    return SufficiencyResult(
        "NOT_TRAINED" if reasons else "ELIGIBLE_FOR_RESEARCH_TRAINING",
        tuple(reasons),
        sessions,
        usable,
        label_fraction,
        quality_fraction,
        volume_complete_fraction,
        favorable_events,
        adverse_events,
    )
