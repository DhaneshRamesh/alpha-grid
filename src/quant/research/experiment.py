"""One predeclared chronological experiment; synthetic data stops at the gate."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

import pyarrow.parquet as pq

from quant.research.baselines import ConditionalBaseline, Method
from quant.research.dataset import FEATURE_NAMES, canonical
from quant.research.sufficiency import DEFAULT_POLICY, SufficiencyPolicy, assess
from quant.research.walkforward import (
    ResearchIndex,
    make_folds,
    reject_holdout_contamination,
)


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    model_family: Method
    features: tuple[str, ...]
    horizon_ns: int
    train_sessions: int
    validation_sessions: int = 1
    test_sessions: int = 1
    embargo_ns: int = 0
    fold_mode: Literal["rolling", "expanding"] = "rolling"
    k: int = 20
    bandwidth: float = 1.0
    ridge_penalty: float = 1.0


@dataclass(frozen=True, slots=True)
class ExperimentReport:
    status: str
    reasons: tuple[str, ...]
    dataset_sha256: str
    experiment_sha256: str
    train_rows: int
    validation_rows: int
    test_rows: int
    validation_mae: float | None
    heldout_mae: float | None
    missing_predictions: int
    limitations: tuple[str, ...]


def run_experiment(
    dataset_dir: Path,
    config: ExperimentConfig,
    *,
    policy: SufficiencyPolicy = DEFAULT_POLICY,
) -> ExperimentReport:
    if (
        not config.features
        or len(config.features) > 6
        or any(name not in FEATURE_NAMES for name in config.features)
    ):
        raise ValueError("predeclare 1-6 known causal features")
    if config.horizon_ns <= 0 or config.fold_mode not in {"rolling", "expanding"}:
        raise ValueError("invalid experiment horizon/fold mode")
    manifest = json.loads((dataset_dir / "manifest.json").read_text())
    features: list[dict[str, Any]] = pq.read_table(
        dataset_dir / "features.parquet"
    ).to_pylist()
    labels: list[dict[str, Any]] = pq.read_table(
        dataset_dir / "labels.parquet"
    ).to_pylist()
    if hashlib.sha256(canonical(features)).hexdigest() != manifest["feature_sha256"]:
        raise ValueError("feature Parquet lineage hash mismatch")
    if hashlib.sha256(canonical(labels)).hexdigest() != manifest["label_sha256"]:
        raise ValueError("label Parquet lineage hash mismatch")
    experiment_hash = hashlib.sha256(
        canonical(
            {
                "dataset_sha256": manifest["dataset_sha256"],
                "config": asdict(config),
                "sufficiency_policy": asdict(policy),
            }
        )
    ).hexdigest()
    eligibility = assess(
        features, labels, source_kind=manifest["source_kind"], policy=policy
    )
    blocked = ExperimentReport(
        "NOT_TRAINED",
        eligibility.reasons,
        manifest["dataset_sha256"],
        experiment_hash,
        0,
        0,
        0,
        None,
        None,
        0,
        ("No empirical result is claimed from synthetic or insufficient data",),
    )
    if eligibility.status != "ELIGIBLE_FOR_RESEARCH_TRAINING":
        return blocked
    by_decision = {row["decision_id"]: row for row in features}
    joined = [
        (by_decision[label["decision_id"]], label)
        for label in labels
        if label["horizon_ns"] == config.horizon_ns
        and label["decision_id"] in by_decision
        and label["forward_return"] is not None
        and label["horizon_covered"]
    ]
    joined = [
        (feature, label)
        for feature, label in joined
        if feature["freshness"] == "Fresh"
        and not feature["stale"]
        and feature["journal_healthy"]
        and feature["feed_connected"]
        and (
            "rolling_volume_activity" not in config.features
            or not feature["volume_interval_incomplete"]
        )
        and all(
            feature[name] is not None and math.isfinite(feature[name])
            for name in config.features
        )
    ]
    index = [
        ResearchIndex(
            feature["session_id"],
            feature["asof_receive_ns"],
            config.horizon_ns,
            feature["source_record_index"],
        )
        for feature, _ in joined
    ]
    folds = make_folds(
        index,
        train_sessions=config.train_sessions,
        validation_sessions=config.validation_sessions,
        test_sessions=config.test_sessions,
        embargo_ns=config.embargo_ns,
        mode=config.fold_mode,
    )
    if not folds:
        return blocked
    fold = folds[-1]  # final holdout is reserved; no hyperparameter search here
    train_ids = {row.session_id for row in fold.train}
    validation_ids = {row.session_id for row in fold.validation}
    test_ids = {row.session_id for row in fold.test}
    reject_holdout_contamination(train_ids, validation_ids, test_ids)
    included = {
        "train": {(r.session_id, r.source_record_index) for r in fold.train},
        "validation": {(r.session_id, r.source_record_index) for r in fold.validation},
        "test": {(r.session_id, r.source_record_index) for r in fold.test},
    }
    subsets = {
        name: [
            (f, label)
            for f, label in joined
            if (f["session_id"], f["source_record_index"]) in selected
        ]
        for name, selected in included.items()
    }
    if any(not subsets[name] for name in subsets):
        return blocked
    train_eligibility = assess(
        [f for f, _ in subsets["train"]],
        [label for _, label in subsets["train"]],
        source_kind=manifest["source_kind"],
        policy=policy,
    )
    if train_eligibility.status != "ELIGIBLE_FOR_RESEARCH_TRAINING":
        return replace(
            blocked, reasons=train_eligibility.reasons, train_rows=len(subsets["train"])
        )
    model = ConditionalBaseline(
        config.model_family,
        k=config.k,
        bandwidth=config.bandwidth,
        ridge_penalty=config.ridge_penalty,
    )
    train = subsets["train"]
    model.fit(
        [[f[name] for name in config.features] for f, _ in train],
        [label["forward_return"] for _, label in train],
        eligibility=train_eligibility,
    )
    missing = 0

    def mae(sample: list[tuple[dict[str, Any], dict[str, Any]]]) -> float | None:
        nonlocal missing
        errors = []
        for f, label in sample:
            result = model.predict([f[name] for name in config.features])
            if result is None:
                missing += 1
            else:
                errors.append(abs(result.mean - label["forward_return"]))
        return sum(errors) / len(errors) if errors else None

    validation_mae = mae(subsets["validation"])
    heldout_mae = mae(subsets["test"])
    return ExperimentReport(
        "TRAINED_UNVALIDATED",
        (),
        manifest["dataset_sha256"],
        experiment_hash,
        len(train),
        len(subsets["validation"]),
        len(subsets["test"]),
        validation_mae,
        heldout_mae,
        missing,
        (
            "One diagnostic holdout is not a validation certificate",
            "No multiple-hypothesis adjustment is applied",
        ),
    )
