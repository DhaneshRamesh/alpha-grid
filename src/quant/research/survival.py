"""Aalen-Johansen nonparametric competing-risk reference."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Cause = Literal["FAVORABLE", "ADVERSE", "CENSORED", "NEITHER_AT_HORIZON"]


@dataclass(frozen=True, slots=True)
class SurvivalObservation:
    elapsed_ns: int
    cause: Cause


@dataclass(frozen=True, slots=True)
class IncidencePoint:
    elapsed_ns: int
    at_risk: int
    favorable: float
    adverse: float
    neither: float
    censored_count: int


def cumulative_incidence(
    observations: list[SurvivalObservation], horizon_ns: int
) -> list[IncidencePoint]:
    """Censor at last observation; never count incomplete paths as neither."""
    if horizon_ns <= 0 or any(
        o.elapsed_ns < 0 or o.elapsed_ns > horizon_ns for o in observations
    ):
        raise ValueError("invalid horizon or event time")
    if not observations:
        return []
    if any(
        o.cause == "NEITHER_AT_HORIZON" and o.elapsed_ns != horizon_ns
        for o in observations
    ):
        raise ValueError("administrative no-hit requires complete horizon")
    remaining = len(observations)
    favor = adverse = 0.0
    survival = 1.0
    output = []
    for time in sorted({o.elapsed_ns for o in observations}):
        at_time = [o for o in observations if o.elapsed_ns == time]
        a = sum(o.cause == "FAVORABLE" for o in at_time)
        b = sum(o.cause == "ADVERSE" for o in at_time)
        c = sum(o.cause == "CENSORED" for o in at_time)
        no_hit = sum(o.cause == "NEITHER_AT_HORIZON" for o in at_time)
        favor += survival * a / remaining
        adverse += survival * b / remaining
        survival *= 1 - (a + b) / remaining
        output.append(IncidencePoint(time, remaining, favor, adverse, survival, c))
        remaining -= a + b + c + no_hit
    return output


def complete_case_brier(
    probabilities: list[float], outcomes: list[bool], censored: list[bool]
) -> float | None:
    eligible = [
        (p, y)
        for p, y, c in zip(probabilities, outcomes, censored, strict=True)
        if not c
    ]
    if not eligible or any(not 0 <= p <= 1 for p, _ in eligible):
        return None
    return sum((p - float(y)) ** 2 for p, y in eligible) / len(eligible)
