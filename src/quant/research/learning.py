"""Offline adaptive-learning rows; unsupported counterfactuals stay unknown."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quant.research.io import read_paper
from quant.research.labels import instrument_key


@dataclass(frozen=True, slots=True)
class LearningTransition:
    decision_id: str
    session_id: str
    instrument_key: str
    source_record_index: int
    state: dict[str, float | int | str | bool | None]
    action: str
    action_probability: float | None
    outcome_net_nanos: int | None
    fill_ids: tuple[str, ...]
    fill_assumptions: tuple[str, ...]
    risk_intervention: bool
    data_quality_ok: bool
    censored: bool
    next_state: dict[str, float | int | str | bool | None] | None
    offline_policy_evaluation_supported: bool = False


def transitions_from_paper(path: Path) -> list[LearningTransition]:
    decisions: list[dict[str, Any]] = []
    outcomes: dict[str, dict[str, Any]] = {}
    fills: dict[str, list[dict[str, Any]]] = {}
    for record in read_paper(path):
        event = record["event"]
        if "Decision" in event:
            decisions.append(event["Decision"])
        elif "Outcome" in event:
            outcomes[event["Outcome"]["decision_id"]] = event["Outcome"]["attribution"]
        elif "Fill" in event:
            fill = event["Fill"]
            fills.setdefault(fill["decision_id"], []).append(fill)
    rows: list[LearningTransition] = []
    next_by_key: dict[
        tuple[str, str], tuple[int, dict[str, float | int | str | bool | None]]
    ] = {}
    decisions.sort(
        key=lambda decision: decision["context"]["source_record_index"], reverse=True
    )
    for decision in decisions:
        context = decision["context"]
        features = context["features"]
        quality = features["quality"]
        key = instrument_key(context["instrument"])
        state: dict[str, float | int | str | bool | None] = {
            "asof_receive_ns": context["source_receive_timestamp_ns"],
            "relative_spread": features["relative_spread"],
            "bid_ask_imbalance": features["bid_ask_imbalance"],
            "snapshot_ofi_quantity": features["snapshot_ofi_quantity"],
            "freshness": features["freshness"],
            "volume_interval_incomplete": quality["volume_interval_incomplete"],
        }
        next_entry = next_by_key.get((context["session_id"], key))
        next_state = (
            next_entry[1]
            if next_entry is not None and next_entry[0] > context["source_record_index"]
            else None
        )
        next_by_key[(context["session_id"], key)] = (
            context["source_record_index"],
            state,
        )
        decision_fills = fills.get(decision["decision_id"], [])
        outcome = outcomes.get(decision["decision_id"])
        rows.append(
            LearningTransition(
                decision_id=decision["decision_id"],
                session_id=context["session_id"],
                instrument_key=key,
                source_record_index=context["source_record_index"],
                state=state,
                action=decision["action"],
                action_probability=None,
                outcome_net_nanos=outcome.get("observed_net_pnl_nanos")
                if outcome
                else None,
                fill_ids=tuple(fill["fill_id"] for fill in decision_fills),
                fill_assumptions=tuple(
                    assumption
                    for fill in decision_fills
                    for assumption in fill["assumptions"]
                ),
                risk_intervention=not decision["risk"]["approved_for_paper"],
                data_quality_ok=(
                    features["freshness"] == "Fresh"
                    and not quality["stale"]
                    and context["journal_healthy"]
                ),
                censored=outcome is None,
                next_state=next_state,
            )
        )
    rows.reverse()
    return rows
