"""Conservative offline opportunity utility against the zero-reward abstention."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class GrossForecast:
    expected_gross_nanos: int | None
    downside_tail_nanos: int | None
    uncertainty_nanos: int | None
    status: str
    calibrated: bool


@dataclass(frozen=True, slots=True)
class CostScenario:
    broker_exchange_regulatory_nanos: int
    spread_crossing_nanos: int
    slippage_nanos: int
    impact_nanos: int
    liquidity_allowance_nanos: int
    adverse_movement_nanos: int
    fill_uncertainty_nanos: int
    schedule_provisional: bool = True

    def total(self) -> int:
        values = (
            self.broker_exchange_regulatory_nanos,
            self.spread_crossing_nanos,
            self.slippage_nanos,
            self.impact_nanos,
            self.liquidity_allowance_nanos,
            self.adverse_movement_nanos,
            self.fill_uncertainty_nanos,
        )
        if any(value < 0 for value in values):
            raise ValueError("costs must be nonnegative")
        return sum(values)


@dataclass(frozen=True, slots=True)
class UtilityDecision:
    action: Literal["NO_TRADE", "RESEARCH_CANDIDATE"]
    conservative_utility_nanos: int | None
    reason: str


def evaluate_utility(
    forecast: GrossForecast,
    cost: CostScenario,
    *,
    risk_approved: bool,
    downside_weight: float = 1.0,
    uncertainty_weight: float = 1.0,
) -> UtilityDecision:
    if downside_weight < 0 or uncertainty_weight < 0:
        raise ValueError("penalty weights must be nonnegative")
    total_cost = cost.total()
    if not risk_approved:
        return UtilityDecision("NO_TRADE", None, "deterministic_risk_gate")
    if (
        forecast.status not in {"VALIDATED_RESEARCH", "SHADOW_ELIGIBLE"}
        or not forecast.calibrated
        or forecast.expected_gross_nanos is None
        or forecast.downside_tail_nanos is None
        or forecast.uncertainty_nanos is None
    ):
        return UtilityDecision("NO_TRADE", None, "unvalidated_or_missing_forecast")
    conservative = round(
        forecast.expected_gross_nanos
        - total_cost
        - downside_weight * max(0, -forecast.downside_tail_nanos)
        - uncertainty_weight * max(0, forecast.uncertainty_nanos)
    )
    if conservative <= 0:
        return UtilityDecision("NO_TRADE", conservative, "cost_or_risk_exceeds_edge")
    return UtilityDecision(
        "RESEARCH_CANDIDATE", conservative, "positive_modelled_utility"
    )
