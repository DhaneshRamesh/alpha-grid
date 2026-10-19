"""Fixed three-class fit and paired session scoring for the prospective experiment."""

import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from quant.acquisition.contracts import CashInstrument, HistoricalBar
from quant.apps import prospective_15m
from quant.apps.noise_reduction import Example
from quant.apps.prospective_15m import (
    _fit,
    _labels,
    _probabilities,
    _session_scores,
)


def test_strict_tail_classes_and_multinomial_probabilities() -> None:
    returns = np.array([-0.003, -0.00206, 0, 0.00206, 0.003])
    assert _labels(returns).tolist() == [0, 1, 1, 1, 2]
    x = np.column_stack((np.linspace(-3, 3, 90), np.zeros(90), np.ones(90)))
    y = np.array([0] * 30 + [1] * 30 + [2] * 30, dtype=np.int64)
    model = _fit(x, y)
    predicted = _probabilities(x, model)
    assert np.allclose(predicted.sum(axis=1), 1)
    assert predicted.shape == (90, 3)
    assert predicted[0, 0] > predicted[0, 2]
    assert predicted[-1, 2] > predicted[-1, 0]


def test_session_brier_convention_and_bootstrap_are_replayable() -> None:
    rows = [
        Example(day, i, i + 2, i + 3, i + 18, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0, ret)
        for day in ("2026-11-02", "2026-11-03")
        for i, ret in enumerate((-0.003, 0.0, 0.003))
    ]
    null = np.full((6, 3), 1 / 3)
    model = np.eye(3)[[0, 1, 2, 0, 1, 2]] * 0.8 + 0.2 / 3
    first = _session_scores(rows, model, null)
    second = _session_scores(rows, model, null)
    assert first == second
    assert np.isclose(first["null_session_mean_brier"], 2 / 3)
    assert first["model_session_mean_brier"] < first["null_session_mean_brier"]
    assert first["conclusion"] == "IMPROVEMENT_SUPPORTED"


def test_future_evaluation_replays_frozen_model_without_fitting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instrument = CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE")

    def bars_for_days(days: list[date]) -> list[HistoricalBar]:
        bars = []
        for day in days:
            opening = datetime.combine(day, time(9, 15), ZoneInfo("Asia/Kolkata"))
            for minute in range(360):
                stamp = int((opening + timedelta(minutes=minute)).timestamp() * 1e9)
                bars.append(
                    HistoricalBar(
                        instrument,
                        1,
                        day,
                        stamp,
                        100_000_000_000,
                        100_000_000_000,
                        100_000_000_000,
                        100_000_000_000,
                        100,
                        "SYNTHETIC_TEST",
                        "test",
                    )
                )
        return bars

    train_days = [date(2026, 4, 1) + timedelta(days=i) for i in range(11)]
    november_days = [
        date(2026, 11, 1) + timedelta(days=i)
        for i in range(25)
        if (date(2026, 11, 1) + timedelta(days=i)).weekday() < 5
    ][:15]
    training = bars_for_days(train_days)
    november = bars_for_days(november_days)
    monkeypatch.setattr(
        prospective_15m, "_development", lambda _: (training, [], {"training": "abc"})
    )
    monkeypatch.setattr(
        prospective_15m,
        "verify_saved_response",
        lambda *_: SimpleNamespace(
            retrieved_at=datetime(2026, 12, 1, tzinfo=UTC),
            bars=november,
            sha256="november-test",
        ),
    )
    monkeypatch.setattr(
        prospective_15m,
        "_fit",
        lambda *_: pytest.fail("evaluation must not refit"),
    )
    frozen = {
        "spec": prospective_15m.SPEC,
        "spec_sha256": prospective_15m._sha(
            prospective_15m._canonical(prospective_15m.SPEC)
        ),
        "code_sha256": prospective_15m._code_hashes(),
        "source_sha256": {"training": "abc"},
        "git_commit_at_freeze": "test",
        "model": {
            "beta": [[0.0] * 4, [0.0] * 4],
            "center": [0.0] * 3,
            "scale": [1.0] * 3,
        },
        "null_probabilities": [0.2, 0.6, 0.2],
        "training_class_mean_return_bps": [-25.0, 0.0, 25.0],
    }
    frozen_path = tmp_path / "frozen.json"
    frozen_path.write_bytes(prospective_15m._canonical(frozen))
    calendar_path = tmp_path / "calendar.json"
    calendar_path.write_text(
        json.dumps(
            {
                "verified": True,
                "source": "synthetic test dates",
                "sessions": [day.isoformat() for day in november_days],
            }
        )
    )
    report, predictions = prospective_15m.evaluate(tmp_path, frozen_path, calendar_path)
    assert report["examples_overlapping"] == 15 * 312
    assert report["sessions"] == 15
    assert len(predictions) == 15 * 312
    assert all(
        record["available_ns"] < record["label_start_ns"] < record["label_end_ns"]
        for record in predictions
    )
    assert report["score"]["conclusion"] == "INCONCLUSIVE_OR_WORSE"
    calendar_path.write_text(
        json.dumps(
            {
                "verified": True,
                "source": "synthetic test dates",
                "sessions": [day.isoformat() for day in november_days[:-1]]
                + ["2026-11-30"],
            }
        )
    )
    with pytest.raises(ValueError, match="observed sessions differ"):
        prospective_15m.evaluate(tmp_path, frozen_path, calendar_path)
