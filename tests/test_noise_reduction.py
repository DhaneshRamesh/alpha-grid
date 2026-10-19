"""Causal timing and training-only volume context for the provisional experiment."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from quant.acquisition.contracts import CashInstrument, HistoricalBar
from quant.apps.noise_reduction import _examples, _fit_logistic, _predict


def _synthetic_sessions(count: int) -> list[HistoricalBar]:
    instrument = CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE")
    bars = []
    for session in range(count):
        day = date(2026, 4, 1) + timedelta(days=session)
        opening = datetime.combine(day, time(9, 15), ZoneInfo("Asia/Kolkata"))
        for minute in range(360):
            price = 100_000_000_000 + session * 10_000_000 + minute * 100_000
            bars.append(
                HistoricalBar(
                    instrument=instrument,
                    interval_minutes=1,
                    trading_date=day,
                    source_timestamp_ns=int(
                        (opening + timedelta(minutes=minute)).timestamp() * 1e9
                    ),
                    open_nanos=price,
                    high_nanos=price + 100_000,
                    low_nanos=price - 100_000,
                    close_nanos=price,
                    volume=100 + minute + session,
                    source="SYNTHETIC_TEST",
                    source_sha256="test",
                )
            )
    return bars


def test_features_available_before_future_label_and_do_not_cross_session() -> None:
    rows = _examples(_synthetic_sessions(11), through_august=False)
    assert len(rows) == 322
    assert len({row.session for row in rows}) == 1
    assert all(
        row.available_ns - row.stamp_ns == 120_000_000_000
        and row.label_start_ns - row.stamp_ns == 180_000_000_000
        and row.label_end_ns - row.stamp_ns == 480_000_000_000
        and row.available_ns < row.label_start_ns < row.label_end_ns
        and row.positive == 1
        for row in rows
    )
    assert rows[0].raw[1:] == rows[0].denoised[1:]
    assert rows[-1].label_end_ns - rows[-1].stamp_ns == 480_000_000_000


def test_later_session_cannot_change_prior_volume_features() -> None:
    bars = _synthetic_sessions(12)
    original = _examples(bars, through_august=False)[:322]
    changed = list(bars)
    changed[-360:] = [
        HistoricalBar(
            instrument=bar.instrument,
            interval_minutes=bar.interval_minutes,
            trading_date=bar.trading_date,
            source_timestamp_ns=bar.source_timestamp_ns,
            open_nanos=bar.open_nanos,
            high_nanos=bar.high_nanos,
            low_nanos=bar.low_nanos,
            close_nanos=bar.close_nanos,
            volume=bar.volume * 1000,
            source=bar.source,
            source_sha256=bar.source_sha256,
        )
        for bar in changed[-360:]
    ]
    assert _examples(changed, through_august=False)[:322] == original
    x = np.asarray([[float(i)] for i in range(20)], dtype=np.float64)
    y = np.asarray([int(i >= 10) for i in range(20)], dtype=np.float64)
    predictions = _predict(x, _fit_logistic(x, y))
    assert np.isfinite(predictions).all()
    assert 0 < predictions[0] < predictions[-1] < 1


def test_fifteen_minute_target_keeps_causal_features_and_common_close() -> None:
    bars = _synthetic_sessions(11)
    five = _examples(bars, through_august=False)
    fifteen = _examples(bars, through_august=False, target_end=18)
    assert len(fifteen) == 312
    assert [row.raw for row in fifteen] == [row.raw for row in five[:312]]
    assert all(
        row.available_ns - row.stamp_ns == 120_000_000_000
        and row.label_start_ns - row.stamp_ns == 180_000_000_000
        and row.label_end_ns - row.stamp_ns == 1_080_000_000_000
        and row.available_ns < row.label_start_ns
        for row in fifteen
    )
    assert fifteen[-1].label_end_ns == bars[-1].source_timestamp_ns
