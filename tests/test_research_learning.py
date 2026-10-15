from __future__ import annotations

from pathlib import Path

from quant.research.learning import transitions_from_paper


def test_historical_qp01_learning_rows_do_not_invent_propensities() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "docs/benchmarks/phase3a-isolated-2026-10-15/run1/risk_filtered.qp.gz"
    rows = transitions_from_paper(path)
    assert len(rows) == 240
    assert all(row.action_probability is None for row in rows)
    assert all(not row.offline_policy_evaluation_supported for row in rows)
    assert any(row.state["volume_interval_incomplete"] for row in rows)
    assert any(row.censored for row in rows)
