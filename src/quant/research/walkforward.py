"""Chronological session folds with horizon purging and embargo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ResearchIndex:
    session_id: str
    asof_ns: int
    horizon_ns: int
    source_record_index: int


@dataclass(frozen=True, slots=True)
class Fold:
    train: tuple[ResearchIndex, ...]
    validation: tuple[ResearchIndex, ...]
    test: tuple[ResearchIndex, ...]
    purged_count: int
    embargo_ns: int
    mode: str


def make_folds(
    rows: list[ResearchIndex],
    *,
    train_sessions: int,
    validation_sessions: int = 1,
    test_sessions: int = 1,
    embargo_ns: int = 0,
    mode: Literal["rolling", "expanding"] = "rolling",
) -> list[Fold]:
    if min(train_sessions, validation_sessions, test_sessions) < 1 or embargo_ns < 0:
        raise ValueError("positive fold sizes and nonnegative embargo required")
    starts: dict[str, int] = {}
    ends: dict[str, int] = {}
    for row in rows:
        if row.horizon_ns <= 0:
            raise ValueError("positive horizon required")
        starts[row.session_id] = min(
            starts.get(row.session_id, row.asof_ns), row.asof_ns
        )
        ends[row.session_id] = max(ends.get(row.session_id, row.asof_ns), row.asof_ns)
    sessions = sorted(starts, key=lambda session: (starts[session], session))
    if len({starts[session] for session in sessions}) != len(sessions):
        raise ValueError("sessions have overlapping start times; order ambiguous")
    if any(
        ends[left] >= starts[right]
        for left, right in zip(sessions, sessions[1:], strict=False)
    ):
        raise ValueError("overlapping market sessions cannot form independent folds")
    ordered = sorted(rows, key=lambda row: (row.asof_ns, row.source_record_index))
    folds = []
    needed = train_sessions + validation_sessions + test_sessions
    for stop in range(needed, len(sessions) + 1):
        train_stop = stop - validation_sessions - test_sessions
        train_start = 0 if mode == "expanding" else train_stop - train_sessions
        train_ids = set(sessions[train_start:train_stop])
        validation_ids = set(sessions[train_stop : stop - test_sessions])
        test_ids = set(sessions[stop - test_sessions : stop])
        validation_start = min(starts[session] for session in validation_ids)
        test_start = min(starts[session] for session in test_ids)
        train = tuple(
            row
            for row in ordered
            if row.session_id in train_ids
            and row.asof_ns + row.horizon_ns + embargo_ns < validation_start
        )
        validation = tuple(
            row
            for row in ordered
            if row.session_id in validation_ids
            and row.asof_ns + row.horizon_ns + embargo_ns < test_start
        )
        test = tuple(row for row in ordered if row.session_id in test_ids)
        purged = sum(row.session_id in train_ids for row in ordered) - len(train)
        purged += sum(row.session_id in validation_ids for row in ordered) - len(
            validation
        )
        folds.append(Fold(train, validation, test, purged, embargo_ns, mode))
    return folds


def reject_holdout_contamination(
    train_sessions: set[str], validation_sessions: set[str], holdout_sessions: set[str]
) -> None:
    if (
        train_sessions & validation_sessions
        or train_sessions & holdout_sessions
        or validation_sessions & holdout_sessions
    ):
        raise ValueError(
            "holdout or validation session used for fitting/model selection"
        )
