"""Software timing fixtures only; never research market observations."""

from __future__ import annotations

import numpy as np

from quant.apps.exploratory_dhan_timing import _rows


def test_future_price_changes_outcome_without_changing_decision_features() -> None:
    base = np.arange(375, dtype=np.float64)
    close = 10_000 + 10 * base
    high = close + 5
    low = close - 5
    volume = np.full(375, 100.0)
    arrays = {
        "2026-05-04": (close.copy(), high.copy(), low.copy(), volume.copy()),
        "2026-05-05": (close.copy(), high.copy(), low.copy(), volume.copy()),
    }
    x_before, y_before, _, days = _rows(arrays, 60, 30)
    assert days[0] == "2026-05-05"  # Previous session supplies relative volume.
    assert x_before[0, 4] == 1.0
    assert x_before[0, 5] == 375 - (15 + 2) - 1
    assert y_before[0] > 0

    changed = arrays["2026-05-05"]
    revised_close = changed[0].copy()
    revised_close[46] += 100
    arrays["2026-05-05"] = (
        revised_close,
        changed[1],
        changed[2],
        changed[3],
    )
    x_after, y_after, _, _ = _rows(arrays, 60, 30)
    np.testing.assert_array_equal(x_before[0], x_after[0])
    assert y_after[0] > y_before[0]
