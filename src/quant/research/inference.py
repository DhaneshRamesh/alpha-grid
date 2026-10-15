"""Python reference for the bounded Rust linear research adapter."""

from __future__ import annotations

import math
from typing import Any


def linear_score(artifact: dict[str, Any], request: dict[str, Any]) -> float:
    names = artifact["feature_names"]
    if (
        artifact["artifact_schema_version"] != 1
        or not 1 <= len(names) <= 6
        or len(set(names)) != len(names)
        or any(
            len(artifact[key]) != len(names) for key in ("centers", "scales", "weights")
        )
    ):
        raise ValueError("invalid bounded model artifact")
    if (
        artifact["model_version"] != request["model_version"]
        or artifact["feature_schema_version"] != request["feature_schema_version"]
        or artifact["horizon_ns"] != request["horizon_ns"]
        or request["instrument_key"] not in artifact["instruments"]
        or not artifact["valid_from_ns"]
        <= request["asof_receive_ns"]
        < artifact["valid_until_ns"]
    ):
        raise ValueError("model version, universe or validity mismatch")
    if artifact["status"] not in {
        "TRAINED_UNVALIDATED",
        "VALIDATED_RESEARCH",
        "SHADOW_ELIGIBLE",
    }:
        raise ValueError("unvalidated model cannot emit a prediction")
    score = artifact["intercept"]
    for name, center, scale, weight in zip(
        names, artifact["centers"], artifact["scales"], artifact["weights"], strict=True
    ):
        if name not in request["features"]:
            raise ValueError("missing feature")
        value = request["features"][name]
        if (
            not all(math.isfinite(v) for v in (value, center, scale, weight))
            or scale <= 0
        ):
            raise ValueError("nonfinite or invalid input")
        score += weight * (value - center) / scale
    if not math.isfinite(score):
        raise ValueError("nonfinite prediction")
    return float(score)
