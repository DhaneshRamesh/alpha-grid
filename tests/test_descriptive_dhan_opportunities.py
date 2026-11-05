"""Boundary checks for retrospective, non-executable Dhan path descriptions."""

from datetime import date

import numpy as np

from quant.acquisition.contracts import HistoricalBar
from quant.apps.descriptive_dhan_opportunities import INSTRUMENT, _paths


def test_paths_end_at_requested_horizon_and_remain_inside_session() -> None:
    day = date(2026, 5, 4)
    start_ns = 1_778_011_100_000_000_000  # Synthetic timestamp; no Dhan input.
    bars = tuple(
        HistoricalBar(
            INSTRUMENT,
            1,
            day,
            start_ns + minute * 60_000_000_000,
            (100 + minute) * 1_000_000_000,
            (100 + minute) * 1_000_000_000,
            (100 + minute) * 1_000_000_000,
            (100 + minute) * 1_000_000_000,
            1,
            "DHAN_HISTORICAL",
            "fixture-only",
        )
        for minute in range(65)
    )
    thirty, favorable, adverse = _paths(bars, 30)
    sixty, _, _ = _paths(bars, 60)
    remaining, _, _ = _paths(bars, "remaining_session")
    assert len(thirty) == 35
    assert len(sixty) == 5
    assert len(remaining) == 64
    np.testing.assert_allclose(thirty[0], (130 / 100 - 1) * 10_000)
    np.testing.assert_allclose(thirty[-1], (164 / 134 - 1) * 10_000)
    np.testing.assert_allclose(remaining[-1], (164 / 163 - 1) * 10_000)
    np.testing.assert_allclose(favorable, thirty)
    np.testing.assert_allclose(adverse, 0)
