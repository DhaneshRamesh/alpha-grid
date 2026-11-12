from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time
from pathlib import Path

import pytest

from quant.acquisition.calendar import NseCalendar, Session
from quant.acquisition.contracts import CashInstrument, HistoricalBar
from quant.acquisition.forward_observations import (
    MINUTE_NS,
    TimingEvidence,
    _validated_bars,
    generate,
    observations,
    outcomes,
    summarize,
)
from quant.acquisition.lake import (
    LakeManifest,
    RawProvenance,
    verify_dataset,
    write_dataset,
)

DAY = date(2026, 8, 4)
INSTRUMENT = CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE")
SESSION = Session(DAY, time(9, 15), time(15, 15), "CAS_CONTINUOUS_TRADING")
CALENDAR = NseCalendar("verified-fixture", special={DAY: SESSION})
START = SESSION.start_ns()


def bar(index: int, *, close: int = 100_000_000_000) -> HistoricalBar:
    return HistoricalBar(
        INSTRUMENT,
        1,
        DAY,
        START + index * MINUTE_NS,
        100_000_000_000,
        max(close, 100_500_000_000),
        min(close, 99_500_000_000),
        close,
        100 + index,
        "SYNTHETIC_TEST",
        "a" * 64,
        "BAR_START_VERIFIED",
    )


def source_manifest(count: int) -> LakeManifest:
    return LakeManifest(
        "ohlcv-v1",
        "fixture",
        "b" * 64,
        ("SYNTHETIC_TEST",),
        "SYNTHETIC_FIXTURE",
        ("2885",),
        DAY.isoformat(),
        DAY.isoformat(),
        "Asia/Kolkata",
        CALENDAR.version,
        "UNADJUSTED",
        ("a" * 64,),
        (),
        "fixture",
        "2026-08-04T00:00:00+00:00",
        count,
        (),
    )


def test_decision_boundary_and_no_future_features() -> None:
    bars = [bar(i) for i in range(20)]
    original = observations(bars, CALENDAR)
    assert original[0]["eligibility"] == "INELIGIBLE_NO_CONTIGUOUS_PRIOR"
    assert original[1]["decision_timestamp_ns"] == START + 2 * MINUTE_NS
    assert original[1]["latest_bar_start_ns"] == START + MINUTE_NS
    assert original[1]["features"]["prior_close_return"] == 0
    changed = bars.copy()
    changed[3] = replace(changed[3], high_nanos=105_000_000_000)
    assert observations(changed, CALENDAR)[:3] == original[:3]
    assert not any(
        "future" in key or "outcome" in key for key in original[1]["features"]
    )
    forward = outcomes(bars, original, CALENDAR, as_of_ns=START + 7 * MINUTE_NS)
    first_five = next(
        row
        for row in forward
        if row["observation_id"] == original[1]["observation_id"]
        and row["horizon_minutes"] == 5
    )
    assert first_five["maturity_timestamp_ns"] == START + 7 * MINUTE_NS
    assert first_five["state"] == "MATURED"
    assert first_five["time_to_favorable_extreme_minutes"] == 1
    assert first_five["extreme_time_precision"] == "ONE_MINUTE_BUCKET_NOT_TICK_TIME"
    assert (
        next(
            row
            for row in forward
            if row["observation_id"] == original[1]["observation_id"]
            and row["horizon_minutes"] == 15
        )["state"]
        == "PENDING"
    )


def test_gap_excludes_next_decision_and_censors_forward_path() -> None:
    bars = [bar(i) for i in range(20) if i != 5]
    observed = observations(bars, CALENDAR)
    assert observed[5]["eligibility"] == "INELIGIBLE_NO_CONTIGUOUS_PRIOR"
    forward = outcomes(bars, observed, CALENDAR, as_of_ns=START + 25 * MINUTE_NS)
    row = next(
        item
        for item in forward
        if item["observation_id"] == observed[2]["observation_id"]
        and item["horizon_minutes"] == 5
    )
    assert row["state"] == "CENSORED_MISSING_CANDLE"
    assert row["signed_return"] is None
    assert all(
        item["observation_id"] != observed[5]["observation_id"] for item in forward
    )
    # A future gap cannot be reported before its missing candle would mature.
    pending = outcomes(bars, observed, CALENDAR, as_of_ns=START + 4 * MINUTE_NS)
    assert (
        next(
            item
            for item in pending
            if item["observation_id"] == observed[2]["observation_id"]
            and item["horizon_minutes"] == 5
        )["state"]
        == "PENDING"
    )


def test_session_end_and_closing_auction_boundary() -> None:
    bars = [bar(358), bar(359)]
    observed = observations(bars, CALENDAR)
    forward = outcomes(bars, observed, CALENDAR, as_of_ns=START + 400 * MINUTE_NS)
    assert {row["state"] for row in forward} == {"CENSORED_SESSION_END"}
    assert all(row["signed_return"] is None for row in forward)
    with pytest.raises(ValueError, match="outside verified continuous session"):
        _validated_bars([bar(360)], CALENDAR, source_manifest(1))
    provisional = NseCalendar("provisional")
    with pytest.raises(ValueError, match="explicit verified session"):
        _validated_bars([bar(0)], provisional, source_manifest(1))
    wrong_regime = NseCalendar(
        "wrong-regime", special={DAY: Session(DAY, time(9, 15), time(15, 30))}
    )
    with pytest.raises(ValueError, match="post-August closing-auction"):
        _validated_bars([bar(0)], wrong_regime, source_manifest(1))
    tomorrow = date(2026, 8, 5)
    next_session = Session(
        tomorrow, time(9, 15), time(15, 15), "CAS_CONTINUOUS_TRADING"
    )
    calendar = NseCalendar("two-days", special={DAY: SESSION, tomorrow: next_session})
    bars.append(
        replace(
            bar(0), trading_date=tomorrow, source_timestamp_ns=next_session.start_ns()
        )
    )
    observed = observations(bars, calendar)
    assert observed[-1]["eligibility"] == "INELIGIBLE_NO_CONTIGUOUS_PRIOR"
    assert {
        row["state"]
        for row in outcomes(bars, observed, calendar, as_of_ns=next_session.end_ns())
    } == {"CENSORED_SESSION_END"}


def test_multiple_instruments_regroup_without_repairing_anomalies() -> None:
    second = CashInstrument("NSE", "NSE_EQ", "2886", "OTHER")
    rows = [
        bar(0),
        replace(bar(0), instrument=second),
        bar(1),
        replace(bar(1), instrument=second),
    ]
    manifest = replace(source_manifest(4), instrument_universe=("2885", "2886"))
    grouped = _validated_bars(rows, CALENDAR, manifest)
    assert [item.instrument.security_id for item in grouped] == [
        "2885",
        "2885",
        "2886",
        "2886",
    ]
    observed = observations(grouped, CALENDAR)
    assert [item["eligibility"] for item in observed] == [
        "INELIGIBLE_NO_CONTIGUOUS_PRIOR",
        "ELIGIBLE",
        "INELIGIBLE_NO_CONTIGUOUS_PRIOR",
        "ELIGIBLE",
    ]
    with pytest.raises(ValueError, match="strictly ordered"):
        _validated_bars(rows + [bar(0)], CALENDAR, manifest)


def test_extremes_use_candle_ranges_and_first_tie_bucket() -> None:
    bars = [bar(i) for i in range(18)]
    bars[2] = replace(bars[2], high_nanos=102_000_000_000, low_nanos=98_000_000_000)
    bars[3] = replace(bars[3], high_nanos=102_000_000_000)
    bars[6] = replace(bars[6], close_nanos=101_000_000_000, high_nanos=101_000_000_000)
    observed = observations(bars, CALENDAR)
    row = next(
        item
        for item in outcomes(bars, observed, CALENDAR, as_of_ns=START + 20 * MINUTE_NS)
        if item["observation_id"] == observed[1]["observation_id"]
        and item["horizon_minutes"] == 5
    )
    assert row["signed_return"] == pytest.approx(0.01)
    assert row["favorable_excursion"] == pytest.approx(0.02)
    assert row["adverse_excursion"] == pytest.approx(-0.02)
    assert row["time_to_favorable_extreme_minutes"] == 1
    assert row["time_to_adverse_extreme_minutes"] == 1
    assert summarize([row])["5"]["positive_count"] == 1


def test_30_60_and_remaining_session_use_one_maturity_boundary() -> None:
    bars = [bar(i) for i in range(360)]
    bars[10] = replace(bars[10], high_nanos=102_000_000_000)
    bars[11] = replace(bars[11], high_nanos=102_000_000_000)
    bars[20] = replace(bars[20], low_nanos=98_000_000_000)
    bars[31] = replace(
        bars[31], close_nanos=101_000_000_000, high_nanos=101_000_000_000
    )
    observed = observations(bars, CALENDAR)
    partial = outcomes(
        bars[:62], observed[:62], CALENDAR, as_of_ns=START + 62 * MINUTE_NS
    )
    selected = {
        row["horizon"]: row
        for row in partial
        if row["observation_id"] == observed[1]["observation_id"]
    }
    assert {"5", "15", "30", "60", "remaining_session"} == set(selected)
    for horizon in (30, 60):
        row = selected[str(horizon)]
        assert row["state"] == "MATURED"
        assert row["horizon_minutes"] == horizon
        assert row["maturity_timestamp_ns"] == START + (horizon + 2) * MINUTE_NS
        assert row["favorable_excursion"] == pytest.approx(0.02)
        assert row["adverse_excursion"] == pytest.approx(-0.02)
        assert row["time_to_favorable_extreme_minutes"] == 9
        assert row["time_to_adverse_extreme_minutes"] == 19
    assert selected["30"]["signed_return"] == pytest.approx(0.01)
    remaining = selected["remaining_session"]
    assert remaining["horizon_minutes"] == 358
    assert remaining["maturity_timestamp_ns"] == SESSION.end_ns()
    assert remaining["state"] == "PENDING"
    assert remaining["signed_return"] is None

    complete = outcomes(bars, observed, CALENDAR, as_of_ns=SESSION.end_ns())
    remaining = next(
        row
        for row in complete
        if row["observation_id"] == observed[1]["observation_id"]
        and row["horizon"] == "remaining_session"
    )
    assert remaining["state"] == "MATURED"
    assert remaining["signed_return"] == 0
    at_close = next(
        row
        for row in complete
        if row["observation_id"] == observed[-1]["observation_id"]
        and row["horizon"] == "remaining_session"
    )
    assert at_close["horizon_minutes"] == 0
    assert at_close["state"] == "CENSORED_SESSION_END"
    assert at_close["signed_return"] is None


def test_remaining_session_gap_is_censored_only_after_gap_matures() -> None:
    bars = [bar(i) for i in range(360) if i != 50]
    observed = observations(bars, CALENDAR)

    def first_remaining(as_of_ns: int) -> dict[str, object]:
        return next(
            row
            for row in outcomes(bars, observed, CALENDAR, as_of_ns=as_of_ns)
            if row["observation_id"] == observed[1]["observation_id"]
            and row["horizon"] == "remaining_session"
        )

    assert first_remaining(START + 40 * MINUTE_NS)["state"] == "PENDING"
    censored = first_remaining(SESSION.end_ns())
    assert censored["state"] == "CENSORED_MISSING_CANDLE"
    assert censored["favorable_excursion"] is None
    assert censored["time_to_adverse_extreme_minutes"] is None


def test_remaining_session_uses_verified_non_cas_close() -> None:
    session = Session(DAY, time(9, 15), time(15, 30), "NON_CAS_CONTINUOUS_TRADING")
    calendar = NseCalendar("verified-non-cas", special={DAY: session})
    bars = [bar(i) for i in range(375)]
    _validated_bars(
        bars,
        calendar,
        replace(source_manifest(len(bars)), calendar_version=calendar.version),
    )
    observed = observations(bars, calendar)
    remaining = next(
        row
        for row in outcomes(bars, observed[:2], calendar, as_of_ns=session.end_ns())
        if row["horizon"] == "remaining_session"
    )
    assert remaining["horizon_minutes"] == 373
    assert remaining["maturity_timestamp_ns"] == session.end_ns()
    assert remaining["state"] == "MATURED"
    with pytest.raises(ValueError, match="outside verified continuous session"):
        _validated_bars([bar(375)], calendar, source_manifest(1))


def test_synthetic_lake_is_rejected_after_native_publication(
    tmp_path: Path,
) -> None:
    lake = tmp_path / "lake"
    manifest = write_dataset(
        (bar(i, close=100_000_000_000 + (i % 3 - 1) * 100_000_000) for i in range(20)),
        lake,
        dataset_id="fixture",
        acquisition_mode="SYNTHETIC_FIXTURE",
        instrument_universe=("2885",),
        calendar_version=CALENDAR.version,
        adjustment_policy="UNADJUSTED",
        code_commit="fixture",
        created_at=datetime(2026, 8, 4, tzinfo=UTC),
    )
    evidence = TimingEvidence(
        manifest.dataset_sha256, CALENDAR.version, "synthetic fixture", True, True
    )
    cutoff = START + 20 * MINUTE_NS
    assert verify_dataset(lake) == manifest
    with pytest.raises(ValueError, match="Dhan original-response lineage"):
        generate(
            lake,
            tmp_path / "raw",
            tmp_path / "blocked",
            CALENDAR,
            evidence,
            as_of_ns=cutoff,
        )
    assert not (tmp_path / "blocked").exists()
    with pytest.raises(ValueError, match="unverified 1-minute"):
        _validated_bars(
            [replace(bar(0), timestamp_semantics="UNVERIFIED")], CALENDAR, manifest
        )


def test_dhan_label_cannot_replace_missing_original_bytes(tmp_path: Path) -> None:
    lake = tmp_path / "lake"
    manifest = write_dataset(
        [replace(bar(0), source="DHAN_HISTORICAL")],
        lake,
        dataset_id="synthetic-lineage-check",
        acquisition_mode="DHAN_SAVED_RESPONSE",
        instrument_universe=("2885",),
        calendar_version=CALENDAR.version,
        adjustment_policy="UNVERIFIED",
        code_commit="synthetic-test",
        source_provenance=(
            RawProvenance(
                "a" * 64,
                "https://api.dhan.co/v2/charts/intraday",
                "2026-08-04T00:00:00+00:00",
                "DHAN_CHARTS_V2",
            ),
        ),
    )
    evidence = TimingEvidence(
        manifest.dataset_sha256, CALENDAR.version, "test", True, True
    )
    with pytest.raises(ValueError, match="original Dhan response bytes missing"):
        generate(
            lake,
            tmp_path / "raw",
            tmp_path / "blocked",
            CALENDAR,
            evidence,
            as_of_ns=START + MINUTE_NS,
        )
    assert not (tmp_path / "blocked").exists()
