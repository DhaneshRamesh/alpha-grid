from __future__ import annotations

import json
from pathlib import Path

import pytest

from quant.research.inference import linear_score

FIXTURES = (
    Path(__file__).resolve().parents[1] / "rust/crates/quant-inference/tests/fixtures"
)


def test_shared_python_rust_inference_fixture() -> None:
    artifact = json.loads((FIXTURES / "linear.json").read_text())
    request = json.loads((FIXTURES / "input.json").read_text())
    assert artifact["status"] == "TRAINED_UNVALIDATED"
    assert linear_score(artifact, request) == 2.5
    del request["features"]["imbalance"]
    with pytest.raises(ValueError, match="missing feature"):
        linear_score(artifact, request)
