"""Observed forward paths: trajectory labels, never executable fill claims."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PriceObservation:
    session_id: str
    instrument_key: str
    source_index: int
    receive_ns: int
    price_nanos: int
    source_kind: str


@dataclass(frozen=True, slots=True)
class HorizonLabel:
    horizon_ns: int
    available: bool
    censored: bool
    ambiguous: bool
    first_event: str | None
    first_event_ns: int | None
    forward_return: float | None
    mfe_nanos: int | None
    mae_nanos: int | None
    observation_count: int
    censor_reason: str | None
    provenance: str


def instrument_key(instrument: dict[str, Any]) -> str:
    return ":".join(
        str(instrument[field]) for field in ("venue", "exchange_segment", "security_id")
    )


def market_observation(row: dict[str, Any]) -> PriceObservation | None:
    record = row["record"]
    event: dict[str, Any] = record["event"]
    kind, value = next(iter(event.items()))
    if kind == "Quote":
        value = value["price"]
    if kind in {"Price", "Quote"}:
        stamp = value["metadata"]["receive_timestamp_ns"]
    else:
        return None
    price = value.get("price_nanos")
    if not isinstance(price, int) or price <= 0:
        return None
    return PriceObservation(
        record["session_id"],
        instrument_key(value["instrument"]),
        row["source_record_index"],
        stamp,
        price,
        kind,
    )


def label_path(
    *,
    asof_index: int,
    asof_ns: int,
    entry_nanos: int,
    observations: list[PriceObservation],
    horizon_ns: int,
    profit_barrier_nanos: int,
    loss_barrier_nanos: int,
    max_gap_ns: int,
    session_end_ns: int | None = None,
) -> HorizonLabel:
    if horizon_ns <= 0 or profit_barrier_nanos <= 0 or loss_barrier_nanos <= 0:
        raise ValueError("horizon and barriers must be positive")
    end_ns = asof_ns + horizon_ns
    path = [
        p
        for p in observations
        if p.source_index > asof_index and asof_ns < p.receive_ns <= end_ns
    ]
    path.sort(key=lambda p: (p.receive_ns, p.source_index))
    previous_ns = asof_ns
    first: str | None = None
    first_ns: int | None = None
    ambiguous = False
    censored_reason: str | None = None
    mfe = mae = 0
    for point in path:
        if session_end_ns is not None and point.receive_ns >= session_end_ns:
            censored_reason = "session_end"
            break
        if point.receive_ns - previous_ns > max_gap_ns:
            censored_reason = "observation_gap"
            break
        delta = point.price_nanos - entry_nanos
        mfe = max(mfe, delta)
        mae = min(mae, delta)
        if first is None:
            hit_profit = delta >= profit_barrier_nanos
            hit_loss = delta <= -loss_barrier_nanos
            if hit_profit and hit_loss:
                ambiguous = True
            elif hit_profit or hit_loss:
                first = "FAVORABLE" if hit_profit else "ADVERSE"
                first_ns = point.receive_ns
        elif first_ns == point.receive_ns:
            if (first == "FAVORABLE" and delta <= -loss_barrier_nanos) or (
                first == "ADVERSE" and delta >= profit_barrier_nanos
            ):
                ambiguous = True
        previous_ns = point.receive_ns
    future = sorted(
        (
            p
            for p in observations
            if p.source_index > asof_index and p.receive_ns >= end_ns
        ),
        key=lambda p: (p.receive_ns, p.source_index),
    )
    witness = future[0] if future else None
    coverage = (
        witness is not None
        and witness.receive_ns - previous_ns <= max_gap_ns
        and (session_end_ns is None or session_end_ns >= end_ns)
        and censored_reason is None
    )
    if session_end_ns is not None and session_end_ns < end_ns:
        censored_reason = censored_reason or "session_end"
    elif not coverage:
        censored_reason = censored_reason or "recording_end_or_gap"
    available = not ambiguous and (first is not None or coverage)
    censored = not available
    last = path[-1] if path else None
    return HorizonLabel(
        horizon_ns,
        available,
        censored,
        ambiguous,
        first,
        first_ns,
        (last.price_nanos / entry_nanos - 1) if last and coverage else None,
        mfe if path else None,
        mae if path else None,
        len(path),
        censored_reason if censored else None,
        "observed source prices; no verified execution",
    )
