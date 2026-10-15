from __future__ import annotations

from dataclasses import replace
from datetime import date, time, timedelta
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from quant.acquisition.calendar import NseCalendar, Session
from quant.acquisition.contracts import CashInstrument, HistoricalBar
from quant.acquisition.lake import LakeManifest
from quant.acquisition.ohlcv_research import (
    CandleResearchPolicy,
    iter_research_rows,
    verify_research_dataset,
    write_research_rows,
)
from quant.research.sufficiency import assess

INST = CashInstrument("NSE", "NSE_EQ", "1333", "RELIANCE")
DAY = date(2025, 1, 2)
START = Session(DAY, time(9, 15), time(9, 25), "TEST").start_ns()


def minute(index: int) -> HistoricalBar:
    return HistoricalBar(
        INST,
        1,
        DAY,
        START + index * 60_000_000_000,
        100_000_000_000,
        100_500_000_000,
        99_500_000_000,
        100_000_000_000,
        100 + index,
        "DHAN_HISTORICAL",
        "a" * 64,
        "BAR_START_VERIFIED",
    )


def test_only_completed_past_bars_enter_features_and_intrabar_is_ambiguous(
    tmp_path: Path,
) -> None:
    policy = CandleResearchPolicy(
        history_bars=2,
        horizon_bars=2,
        profit_barrier_nanos=1_000_000_000,
        stop_barrier_nanos=1_000_000_000,
    )
    bars = [minute(i) for i in range(6)]
    bars[2] = replace(bars[2], high_nanos=102_000_000_000, low_nanos=98_000_000_000)
    rows = list(iter_research_rows(bars, policy))
    assert len(rows) == 5
    feature, label = rows[0]
    assert feature.asof_ns == START + 2 * 60_000_000_000
    assert feature.past_mean_volume == 100.5
    assert feature.latest_close_nanos == 100_000_000_000
    assert label.first_event == "AMBIGUOUS"
    assert label.ambiguous and not label.right_censored
    changed = bars.copy()
    changed[2] = replace(
        changed[2], high_nanos=101_000_000_000, low_nanos=99_000_000_000
    )
    assert list(iter_research_rows(changed, policy))[0][0] == feature
    assert rows[-1][1].right_censored
    source = LakeManifest(
        "ohlcv-v1",
        "source",
        "a" * 64,
        ("DHAN_HISTORICAL",),
        "SYNTHETIC_FIXTURE",
        ("1333",),
        DAY.isoformat(),
        DAY.isoformat(),
        "Asia/Kolkata",
        "test",
        "UNADJUSTED",
        ("a" * 64,),
        (),
        "fixture",
        "2025-01-01T00:00:00+00:00",
        6,
        (),
    )
    manifest = write_research_rows(
        iter(rows),
        tmp_path / "research",
        source_manifest=source,
        policy=policy,
        dataset_id="synthetic-research",
        batch_size=2,
    )
    assert manifest["status"] == "NOT_TRAINED"
    assert "DEPTH" in manifest["blocked_model_families"]
    assert verify_research_dataset(tmp_path / "research") == manifest
    saved = pq.read_table(tmp_path / "research/features.parquet").to_pylist()
    assert saved[0]["asof_ns"] == feature.asof_ns
    assert "high_nanos" not in saved[0]


def test_unverified_intraday_timing_and_daily_publication_blocked() -> None:
    policy = CandleResearchPolicy(history_bars=1, horizon_bars=1)
    with pytest.raises(ValueError, match="semantics"):
        list(
            iter_research_rows(
                [replace(minute(0), timestamp_semantics="UNVERIFIED")], policy
            )
        )
    daily = replace(
        minute(0),
        interval_minutes=None,
        source_timestamp_ns=None,
        timestamp_semantics="DAILY",
    )
    with pytest.raises(ValueError, match="publication"):
        list(iter_research_rows([daily], policy))
    calendar = NseCalendar(
        "test", special={DAY: Session(DAY, time(9, 15), time(9, 25), "TEST")}
    )
    with pytest.raises(ValueError, match="post-close"):
        list(
            iter_research_rows(
                [daily], policy, daily_available_at=lambda _: START, calendar=calendar
            )
        )


def test_ohlcv_never_satisfies_depth_sufficiency() -> None:
    result = assess([], [], source_kind="REAL_VERIFIED", data_modality="OHLCV")
    assert "depth_models_require_recorded_depth_events" in result.reasons
    assert result.status == "NOT_TRAINED"


def test_daily_path_needs_verified_availability_and_calendar_continuity() -> None:
    policy = CandleResearchPolicy(history_bars=1, horizon_bars=1)
    days = [DAY, DAY + timedelta(days=1), DAY + timedelta(days=5)]
    daily = [
        replace(
            minute(0),
            trading_date=day,
            interval_minutes=None,
            source_timestamp_ns=None,
            timestamp_semantics="DAILY",
        )
        for day in days
    ]
    calendar = NseCalendar("fixture")

    def published(bar: HistoricalBar) -> int:
        session = calendar.session(bar.trading_date)
        assert session is not None
        return session.end_ns() + 60_000_000_000

    rows = list(
        iter_research_rows(
            daily, policy, daily_available_at=published, calendar=calendar
        )
    )
    assert len(rows) == 3
    assert not rows[0][1].right_censored
    assert rows[1][1].right_censored  # Monday's bar is missing
