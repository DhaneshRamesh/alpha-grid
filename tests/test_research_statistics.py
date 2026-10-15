from __future__ import annotations

import numpy as np
import pytest

from quant.research.baselines import ConditionalBaseline
from quant.research.sufficiency import SufficiencyPolicy, assess
from quant.research.survival import (
    SurvivalObservation,
    complete_case_brier,
    cumulative_incidence,
)


def test_synthetic_data_never_passes_sufficiency() -> None:
    features = [
        {
            "session_id": "s",
            "freshness": "Fresh",
            "stale": False,
            "journal_healthy": True,
            "feed_connected": True,
        }
    ]
    labels = [{"label_available": True, "ordering_ambiguous": False}]
    result = assess(
        features, labels, source_kind="SYNTHETIC", policy=SufficiencyPolicy(1, 1, 0, 0)
    )
    assert result.status == "NOT_TRAINED"
    assert "source_is_not_verified_real_market_data" in result.reasons
    assert assess([], [], source_kind="REAL_VERIFIED").status == "NOT_TRAINED"


@pytest.mark.parametrize("method", ["empirical", "knn", "kernel", "ridge"])
def test_known_answer_baselines_and_missing_features(method: str) -> None:
    x = np.array([[0.0], [1.0], [2.0], [3.0]])
    y = np.array([0.0, 1.0, 2.0, 3.0])
    model = ConditionalBaseline(method, k=4, bandwidth=10.0)
    assert model.predict([1.0]) is None
    model.fit(x, y, favorable=[0, 0, 1, 1], known_answer_fixture=True)
    result = model.predict([1.0])
    assert result is not None
    assert 0 <= result.mean <= 3
    assert result.status == "TEST_FIXTURE_UNVALIDATED"
    assert model.predict([float("nan")]) is None
    if method == "ridge":
        assert result.q50 is None and result.favorable_probability is None
    else:
        assert result.q05 is not None and result.q95 is not None
        assert result.q05 <= result.q50 <= result.q95


def test_training_only_scaler_and_deterministic_ridge() -> None:
    x = [[0.0], [1.0], [2.0]]
    y = [0.0, 2.0, 4.0]
    left = ConditionalBaseline("ridge", ridge_penalty=0)
    right = ConditionalBaseline("ridge", ridge_penalty=0)
    left.fit(x, y, known_answer_fixture=True)
    right.fit(x, y, known_answer_fixture=True)
    assert left.predict([3.0]) == right.predict([3.0])
    assert left.predict([3.0]).mean == pytest.approx(6.0)  # type: ignore[union-attr]


def test_competing_risk_risk_sets_and_censoring() -> None:
    events = [
        SurvivalObservation(1, "FAVORABLE"),
        SurvivalObservation(1, "ADVERSE"),
        SurvivalObservation(2, "CENSORED"),
        SurvivalObservation(3, "FAVORABLE"),
    ]
    curve = cumulative_incidence(events, 5)
    assert curve[0].at_risk == 4
    assert curve[0].favorable == pytest.approx(0.25)
    assert curve[0].adverse == pytest.approx(0.25)
    assert curve[1].censored_count == 1 and curve[1].at_risk == 2
    assert curve[2].favorable == pytest.approx(0.75)
    assert curve[2].adverse == pytest.approx(0.25)
    assert curve[2].neither == pytest.approx(0)
    assert not cumulative_incidence([], 5)
    with pytest.raises(ValueError):
        cumulative_incidence(events, 2)


def test_brier_excludes_censored_unknowns() -> None:
    assert complete_case_brier(
        [0.8, 0.2], [True, True], [False, True]
    ) == pytest.approx(0.04)
    assert complete_case_brier([0.9], [True], [True]) is None
