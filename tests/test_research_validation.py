from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from quant.research.baselines import ConditionalBaseline
from quant.research.decision import CostScenario, GrossForecast, evaluate_utility
from quant.research.registry import ModelArtifact, ResearchRegistry
from quant.research.state_space import (
    DynamicRegressionState,
    LocalLevelState,
    cusum_change_points,
    estimate_regular_ou,
    kalman_dynamic_regression,
    kalman_local_level,
    volatility_regime,
)
from quant.research.uncertainty import bootstrap_session_net
from quant.research.walkforward import (
    ResearchIndex,
    make_folds,
    reject_holdout_contamination,
)


def test_training_gate_is_required() -> None:
    model = ConditionalBaseline("knn")
    with pytest.raises(ValueError, match="NOT_TRAINED"):
        model.fit([[0], [1], [2]], [0, 1, 2])


def test_session_folds_purge_horizon_and_holdout() -> None:
    rows = [ResearchIndex(f"s{i}", i * 100, 10, i) for i in range(5)]
    rows.append(ResearchIndex("s1", 195, 10, 99))
    fold = make_folds(rows, train_sessions=2, embargo_ns=0)[0]
    assert {r.session_id for r in fold.train} == {"s0", "s1"}
    assert all(r.source_record_index != 99 for r in fold.train)
    assert fold.purged_count == 1
    assert {r.session_id for r in fold.validation} == {"s2"}
    assert {r.session_id for r in fold.test} == {"s3"}
    expanded = make_folds(rows, train_sessions=2, mode="expanding")
    assert len(expanded[-1].train) > len(fold.train)
    with pytest.raises(ValueError, match="overlapping market sessions"):
        make_folds(
            [
                ResearchIndex("a", 0, 10, 1),
                ResearchIndex("a", 110, 10, 2),
                ResearchIndex("b", 100, 10, 3),
            ],
            train_sessions=1,
        )
    with pytest.raises(ValueError, match="holdout"):
        reject_holdout_contamination({"s0", "s3"}, {"s1"}, {"s3"})


def _artifact(status: str = "TRAINED_UNVALIDATED") -> ModelArtifact:
    return ModelArtifact(
        model_family="ridge",
        model_version="fixture-v1",
        training_dataset_sha256="a" * 64,
        validation_dataset_sha256="b" * 64,
        source_kind="REAL_VERIFIED",
        feature_schema_version="research-v1",
        trained_at_ns=1,
        train_sessions=("s1",),
        holdout_sessions=("s2",),
        hyperparameters={"ridge_penalty": 1.0},
        calibration_method=None,
        validation_results={"brier": 0.2},
        instruments=("NSE:NSE_EQ:1333",),
        horizons_ns=(5_000_000_000,),
        environment="offline_research",
        latency_ns=None,
        limitations=("known-answer test fixture",),
        status=status,  # type: ignore[arg-type]
    )


def test_registry_requires_independent_review_and_reversible_status(
    tmp_path: Path,
) -> None:
    registry = ResearchRegistry(tmp_path)
    artifact = _artifact()
    identity = registry.register(artifact)
    assert artifact.identity() == identity
    with pytest.raises(ValueError, match="evidence"):
        registry.transition(identity, "VALIDATED_RESEARCH")
    evidence = tmp_path / "independent.json"
    evidence.write_text(
        '{"dataset_sha256":"' + "b" * 64 + '",'
        '"source_kind":"REAL_VERIFIED",'
        '"holdout_sessions":["s2"],"validation_status":"PASS"}'
    )
    registry.transition(identity, "VALIDATED_RESEARCH", evidence_path=evidence)
    assert registry.status(identity) == "VALIDATED_RESEARCH"
    review = tmp_path / "review.json"
    review.write_text('{"review_type":"SHADOW","artifact_sha256":"' + identity + '"}')
    registry.transition(identity, "SHADOW_ELIGIBLE", evidence_path=review)
    registry.transition(identity, "TRAINED_UNVALIDATED")
    assert registry.status(identity) == "TRAINED_UNVALIDATED"
    with pytest.raises(ValueError, match="overlapping market sessions"):
        make_folds(
            [
                ResearchIndex("a", 0, 10, 1),
                ResearchIndex("a", 110, 10, 2),
                ResearchIndex("b", 100, 10, 3),
            ],
            train_sessions=1,
        )
    with pytest.raises(ValueError, match="holdout"):
        ResearchRegistry(tmp_path / "bad").register(
            replace(artifact, holdout_sessions=("s1",))
        )


def test_costs_uncertainty_and_risk_cause_abstention() -> None:
    costs = CostScenario(10, 10, 10, 10, 10, 10, 10)
    forecast = GrossForecast(100, -10, 10, "VALIDATED_RESEARCH", True)
    assert (
        evaluate_utility(forecast, costs, risk_approved=True).action
        == "RESEARCH_CANDIDATE"
    )
    assert evaluate_utility(forecast, costs, risk_approved=False).action == "NO_TRADE"
    assert (
        evaluate_utility(
            GrossForecast(None, None, None, "NOT_TRAINED", False),
            costs,
            risk_approved=True,
        ).action
        == "NO_TRADE"
    )
    higher = CostScenario(10, 100, 10, 10, 10, 10, 10)
    assert evaluate_utility(forecast, higher, risk_approved=True).action == "NO_TRADE"


def test_session_bootstrap_is_deterministic_and_requires_sessions() -> None:
    assert bootstrap_session_net({"one": 1.0}) is None
    values = {f"s{i}": float(i - 5) for i in range(10)}
    first = bootstrap_session_net(values, repetitions=200, seed=7)
    assert first == bootstrap_session_net(values, repetitions=200, seed=7)
    assert first is not None and first.independent_sessions == 10
    assert (
        first.confidence_low_nanos
        <= first.mean_net_nanos
        <= first.confidence_high_nanos
    )


def test_state_models_use_time_and_reject_irregular_ou() -> None:
    local = kalman_local_level(
        LocalLevelState(0, 1, 0),
        observation=2,
        timestamp_ns=1_000_000_000,
        process_var_per_second=1,
        observation_var=1,
    )
    assert 0 < local.mean < 2
    dynamic = kalman_dynamic_regression(
        DynamicRegressionState(0, 1, 0),
        x=1,
        y=2,
        timestamp_ns=1_000_000_000,
        process_var_per_second=1,
        observation_var=1,
    )
    assert dynamic.coefficient > 0
    with pytest.raises(ValueError, match="irregular"):
        estimate_regular_ou([1, 2, 3, 4, 5], [0, 1, 2, 4, 5])
    assert cusum_change_points(
        [0, 0, 10], reference_mean=0, allowance=0, threshold=5
    ) == (2,)
    assert volatility_regime([0.1], threshold=0.01) == "UNKNOWN"
