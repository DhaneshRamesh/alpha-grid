"""Synthetic software checks only; these arrays are never research evidence."""

from datetime import date, timedelta

import numpy as np

from quant.apps.reliance_60m_falsification import fee_bps, select


def test_volume_confirmation_uses_prior_20_sessions_and_future_is_outcome_only() -> (
    None
):
    arrays = {}
    for k in range(21):
        close = 10000 + 10 * np.arange(375, dtype=float)
        volume = np.full(375, 100.0)
        if k == 20:
            volume[11:16] = 300.0
        day = (date(2026, 5, 1) + timedelta(days=k)).isoformat()
        arrays[day] = (close, close + 5, close - 5, volume)
    selected, _ = select(arrays, 60)
    days = sorted(arrays)
    assert all(selected[d]["volume"] is None for d in days[:20])
    original = selected[days[20]]["volume"]
    assert original is not None and original.index == 15

    close, high, low, volume = arrays[days[20]]
    revised = close.copy()
    revised[76] += 100
    arrays[days[20]] = (revised, high, low, volume)
    later, _ = select(arrays, 60)
    changed = later[days[20]]["volume"]
    assert changed is not None and changed.index == original.index
    assert changed.gross > original.gross


def test_published_fee_rounding_is_half_up() -> None:
    _, components = fee_bps(10_000)
    assert components["stt"] == 3.0
