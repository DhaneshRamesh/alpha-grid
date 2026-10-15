"""Transparent low-dimensional reference estimators. Offline only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

from quant.research.sufficiency import SufficiencyResult

Method = Literal["empirical", "knn", "kernel", "ridge"]


@dataclass(frozen=True, slots=True)
class Prediction:
    mean: float
    q05: float | None
    q50: float | None
    q95: float | None
    favorable_probability: float | None
    support: int
    status: str = "TRAINED_UNVALIDATED"


class ConditionalBaseline:
    """Scale on train only; no fitted probability is called calibrated."""

    def __init__(
        self,
        method: Method,
        *,
        k: int = 20,
        bandwidth: float = 1.0,
        ridge_penalty: float = 1.0,
    ) -> None:
        if k < 1 or bandwidth <= 0 or ridge_penalty < 0:
            raise ValueError("invalid hyperparameters")
        self.method = method
        self.k = k
        self.bandwidth = bandwidth
        self.ridge_penalty = ridge_penalty
        self._fixture = False
        self._x: npt.NDArray[np.float64] | None = None
        self._y: npt.NDArray[np.float64] | None = None
        self._event: npt.NDArray[np.float64] | None = None
        self._center: npt.NDArray[np.float64] | None = None
        self._scale: npt.NDArray[np.float64] | None = None
        self._beta: npt.NDArray[np.float64] | None = None

    def fit(
        self,
        x: npt.ArrayLike,
        y: npt.ArrayLike,
        *,
        favorable: npt.ArrayLike | None = None,
        eligibility: SufficiencyResult | None = None,
        known_answer_fixture: bool = False,
    ) -> None:
        if not known_answer_fixture and (
            eligibility is None
            or eligibility.status != "ELIGIBLE_FOR_RESEARCH_TRAINING"
        ):
            raise ValueError("NOT_TRAINED: dataset sufficiency gate did not pass")
        self._fixture = known_answer_fixture
        x_arr = np.asarray(x, dtype=np.float64)
        y_arr = np.asarray(y, dtype=np.float64)
        if x_arr.ndim != 2 or not 1 <= x_arr.shape[1] <= 6:
            raise ValueError("use 1-6 predeclared features")
        if y_arr.shape != (x_arr.shape[0],) or x_arr.shape[0] < 3:
            raise ValueError("insufficient or mismatched observations")
        if not np.isfinite(x_arr).all() or not np.isfinite(y_arr).all():
            raise ValueError("missing or nonfinite training feature/target")
        center = np.mean(x_arr, axis=0)
        scale = np.std(x_arr, axis=0)
        scale[scale == 0] = 1
        self._center, self._scale = center, scale
        self._x = (x_arr - center) / scale
        self._y = y_arr
        if favorable is not None:
            event = np.asarray(favorable, dtype=np.float64)
            if event.shape != y_arr.shape or not np.isin(event, [0, 1]).all():
                raise ValueError("favorable indicators must be binary")
            self._event = event
        if self.method == "ridge":
            design = np.column_stack([np.ones(len(y_arr)), self._x])
            penalty = np.eye(design.shape[1]) * self.ridge_penalty
            penalty[0, 0] = 0
            self._beta = np.linalg.solve(design.T @ design + penalty, design.T @ y_arr)

    def predict(self, x: npt.ArrayLike) -> Prediction | None:
        if (
            self._x is None
            or self._y is None
            or self._center is None
            or self._scale is None
        ):
            return None
        query = np.asarray(x, dtype=np.float64)
        if query.shape != self._center.shape or not np.isfinite(query).all():
            return None
        z = (query - self._center) / self._scale
        distance = np.linalg.norm(self._x - z, axis=1)
        if self.method == "ridge":
            assert self._beta is not None
            return Prediction(
                float(np.r_[1, z] @ self._beta),
                None,
                None,
                None,
                None,
                len(self._y),
                "TEST_FIXTURE_UNVALIDATED" if self._fixture else "TRAINED_UNVALIDATED",
            )
        if self.method == "empirical":
            selected = np.flatnonzero(distance <= self.bandwidth)
            weights = np.ones(len(selected))
        elif self.method == "knn":
            selected = np.argsort(distance, kind="stable")[: self.k]
            weights = np.ones(len(selected))
        else:
            selected = np.arange(len(self._y))
            weights = np.exp(-0.5 * (distance / self.bandwidth) ** 2)
        if not len(selected) or np.sum(weights) <= 1e-12:
            return None
        values = self._y[selected]
        order = np.argsort(values, kind="stable")
        sorted_values = values[order]
        cumulative = np.cumsum(weights[order]) / np.sum(weights)
        quantiles = tuple(
            float(sorted_values[np.searchsorted(cumulative, q)])
            for q in (0.05, 0.5, 0.95)
        )
        event = (
            float(np.average(self._event[selected], weights=weights))
            if self._event is not None
            else None
        )
        return Prediction(
            float(np.average(values, weights=weights)),
            quantiles[0],
            quantiles[1],
            quantiles[2],
            event,
            len(selected),
            "TEST_FIXTURE_UNVALIDATED" if self._fixture else "TRAINED_UNVALIDATED",
        )
