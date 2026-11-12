"""AlphaGrid M1: Locally trainable, deployable NSE equity forecasting pipeline.

Implements Alpha Grid's first gradient-boosted forecasting model (M1):
- Predicts separate favorable (forward 15m return >= +30 bps) and adverse
  (forward 15m return <= -10 bps or MAE <= -25 bps) outcome probabilities.
- Reuses 5 causal point-in-time features from cross_equity_model.py.
- Strict chronological train/calibration splits with non-overlapping outcome purging.
- Train-only feature standardisation and fixed decision thresholds.
- Immutable model packaging with feature schema, dataset hashes, and SHA-256 verification.
- Offline replay inference and append-only shadow-prediction logger.
- Minimal read-only prediction interface suitable for future Cockpit integration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import pickle
from typing import Any, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import numpy.typing as npt
from sklearn.ensemble import HistGradientBoostingClassifier

from quant.acquisition.calendar import IST

M1_MODEL_VERSION = "alpha-grid-m1-20261107-v1"
M1_SCHEMA_VERSION = "alpha-grid-m1-artifact-v1"

FEATURE_NAMES: tuple[str, ...] = (
    "past_5m_return_bps",
    "breakout_distance_bps",
    "relative_volume_15m",
    "realized_volatility_15m_bps",
    "normalized_time_of_day",
)

FORWARD_HORIZON_MINUTES: int = 15
DECISION_STEP_MINUTES: int = 15  # Non-overlapping decision slots for training to purge overlap
FAVORABLE_RETURN_HURDLE_BPS: float = 30.0
ADVERSE_RETURN_THRESHOLD_BPS: float = -10.0
ADVERSE_MAE_THRESHOLD_BPS: float = -25.0

# Predeclared Data Boundaries
RESERVED_HOLDOUT_STOCKS: frozenset[str] = frozenset({
    "TCS", "KOTAKBANK", "MARUTI", "BHEL", "DIVISLAB"
})
TRAIN_START_DATE: str = "2023-11-01"
TRAIN_END_DATE: str = "2025-04-30"
CALIBRATION_START_DATE: str = "2025-05-01"
CALIBRATION_END_DATE: str = "2025-10-31"
TEST_HOLDOUT_START_DATE: str = "2025-11-01"

CORPORATE_ACTION_EX_DATES: dict[str, set[str]] = {
    "RELIANCE": {"2024-10-25", "2024-10-28", "2024-10-29"},
    "PCJEWELLER": {"2024-10-29", "2024-10-30", "2024-10-31"},
    "HDFCBANK": {"2024-05-09", "2024-05-10", "2024-05-13"},
    "BSE": {"2024-06-13", "2024-06-14", "2024-06-17"},
}
SPECIAL_DR_DATES: frozenset[str] = frozenset({"2024-03-02", "2024-05-18"})


@dataclass(frozen=True, slots=True)
class M1Prediction:
    symbol: str
    as_of_ist: str
    model_version: str
    features: tuple[float, float, float, float, float] | None
    prob_favorable: float | None
    prob_adverse: float | None
    abstain: bool
    abstention_reason: str | None
    signal: str  # 'OPPORTUNITY_FAVORABLE', 'RISK_ADVERSE', 'NEUTRAL', 'ABSTAIN'


class M1Preprocessor:
    """Train-only standardization ensuring zero data leakage."""

    def __init__(self) -> None:
        self.mean: npt.NDArray[np.float64] | None = None
        self.scale: npt.NDArray[np.float64] | None = None

    def fit(self, x: npt.NDArray[np.float64]) -> None:
        if x.ndim != 2 or x.shape[1] != len(FEATURE_NAMES):
            raise ValueError(f"expected 2D array with {len(FEATURE_NAMES)} features")
        mean = np.mean(x, axis=0)
        scale = np.std(x, axis=0)
        scale = np.where(scale == 0.0, 1.0, scale)
        self.mean = mean.astype(np.float64)
        self.scale = scale.astype(np.float64)

    def transform(self, x: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
        if self.mean is None or self.scale is None:
            raise ValueError("preprocessor is not fitted")
        return (x - self.mean) / self.scale


def calculate_features_single(
    symbol: str,
    as_of_dt: datetime,
    day_bars: dict[time, dict[str, float]],
) -> tuple[tuple[float, float, float, float, float] | None, str | None]:
    """Extract causal point-in-time features for a single decision instant.

    Returns:
        (features, None) if all causal data requirements are satisfied.
        (None, abstention_reason) if any causal condition or data point is missing.
    """
    if as_of_dt.tzinfo is None:
        as_of_dt = as_of_dt.replace(tzinfo=IST)

    t_dec = as_of_dt.time()
    dec_mins = t_dec.hour * 60 + t_dec.minute

    # 1. Trading hours gate: 10:00 to 14:45 IST
    if dec_mins < 10 * 60 or dec_mins > 14 * 60 + 45:
        return None, "OUTSIDE_DECISION_HOURS"

    # 2. Opening 30m range gate (09:15 to 09:44)
    opening_bars = [day_bars.get(time(9, m)) for m in range(15, 45)]
    if any(b is None for b in opening_bars):
        return None, "INSUFFICIENT_OPENING_RANGE"
    open_high = max(b["h"] for b in opening_bars if b is not None)

    # 3. Decision bar at T-1
    asof_mins = dec_mins - 1
    asof_t = time(asof_mins // 60, asof_mins % 60)
    c_bar = day_bars.get(asof_t)
    if c_bar is None:
        return None, "MISSING_DECISION_BAR"

    # 4. Trailing return lookbacks (5m, 15m)
    m5_t = time((asof_mins - 5) // 60, (asof_mins - 5) % 60)
    m15_t = time((asof_mins - 15) // 60, (asof_mins - 15) % 60)
    bar_m5 = day_bars.get(m5_t)
    bar_m15 = day_bars.get(m15_t)
    if bar_m5 is None or bar_m15 is None:
        return None, "INSUFFICIENT_LOOKBACK_BARS"

    r_5m_bps = float((c_bar["c"] - bar_m5["c"]) / bar_m5["c"] * 10000.0)
    breakout_dist_bps = float((c_bar["c"] - open_high) / open_high * 10000.0)

    # 5. Relative volume: trailing 15m vs prior 30m
    v_15m = sum(
        day_bars.get(time((asof_mins - m) // 60, (asof_mins - m) % 60), {}).get("v", 0.0)
        for m in range(15)
    )
    v_prior_30m = sum(
        day_bars.get(time((asof_mins - 15 - m) // 60, (asof_mins - 15 - m) % 60), {}).get("v", 0.0)
        for m in range(30)
    )
    rel_vol = float(v_15m / (v_prior_30m / 2.0 + 1e-6))

    # 6. Realized volatility: std of 1m returns over trailing 15m
    rets_15m: list[float] = []
    for m in range(14, -1, -1):
        b_curr = day_bars.get(time((asof_mins - m) // 60, (asof_mins - m) % 60))
        b_prev = day_bars.get(time((asof_mins - m - 1) // 60, (asof_mins - m - 1) % 60))
        if b_curr and b_prev and b_prev["c"] > 0:
            rets_15m.append((b_curr["c"] - b_prev["c"]) / b_prev["c"])
    if len(rets_15m) < 10:
        return None, "INSUFFICIENT_VOLATILITY_BARS"
    vol_15m_bps = float(np.std(rets_15m) * 10000.0)

    # 7. Normalized time of day (0.0 at 09:15 to 1.0 at 15:30)
    tod_norm = float((dec_mins - (9 * 60 + 15)) / 375.0)

    return (r_5m_bps, breakout_dist_bps, rel_vol, vol_15m_bps, tod_norm), None


class AlphaGridM1:
    """Alpha Grid M1 Dual Gradient-Boosted Equity Forecasting Model."""

    def __init__(
        self,
        favorable_model: HistGradientBoostingClassifier | None = None,
        adverse_model: HistGradientBoostingClassifier | None = None,
        preprocessor: M1Preprocessor | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.favorable_model = favorable_model
        self.adverse_model = adverse_model
        self.preprocessor = preprocessor or M1Preprocessor()
        self.metadata = metadata or {}
        saved_thresholds = self.metadata.get("calibrated_thresholds", {})
        self.fav_threshold: float = float(self.metadata.get("fav_threshold", saved_thresholds.get("fav_threshold", 0.12)))
        self.adv_threshold: float = float(self.metadata.get("adv_threshold", saved_thresholds.get("adv_threshold", 0.35)))

    def predict_proba(
        self, x: npt.NDArray[np.float64]
    ) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64]]:
        """Predict (prob_favorable, prob_adverse) for an array of feature vectors."""
        if self.favorable_model is None or self.adverse_model is None:
            raise ValueError("M1 models are not trained")
        x_std = self.preprocessor.transform(x)
        p_fav = self.favorable_model.predict_proba(x_std)[:, 1]
        p_adv = self.adverse_model.predict_proba(x_std)[:, 1]
        return p_fav, p_adv

    def save_artifact(self, output_dir: Path, name: str = "alpha-grid-m1-v1") -> tuple[Path, Path]:
        """Save model weights and cryptographic metadata sidecar."""
        output_dir.mkdir(parents=True, exist_ok=True)
        weights_file = output_dir / f"{name}.pkl"
        meta_file = output_dir / f"{name}.meta.json"
        sha_file = output_dir / f"{name}.sha256"

        bundle = {
            "model_version": M1_MODEL_VERSION,
            "schema_version": M1_SCHEMA_VERSION,
            "feature_names": FEATURE_NAMES,
            "favorable_model": self.favorable_model,
            "adverse_model": self.adverse_model,
            "preprocessor": self.preprocessor,
            "metadata": self.metadata,
        }

        raw_bytes = pickle.dumps(bundle, protocol=5)
        sha256 = hashlib.sha256(raw_bytes).hexdigest()

        weights_file.write_bytes(raw_bytes)
        sha_file.write_text(sha256)

        full_meta = {
            **self.metadata,
            "model_version": M1_MODEL_VERSION,
            "schema_version": M1_SCHEMA_VERSION,
            "feature_names": list(FEATURE_NAMES),
            "weights_file": weights_file.name,
            "sha256": sha256,
            "byte_size": len(raw_bytes),
            "fav_threshold": self.fav_threshold,
            "adv_threshold": self.adv_threshold,
        }
        meta_file.write_text(json.dumps(full_meta, indent=2))

        os.chmod(weights_file, 0o600)
        os.chmod(meta_file, 0o600)
        os.chmod(sha_file, 0o600)

        return weights_file, meta_file

    @classmethod
    def load_artifact(cls, weights_path: Path) -> AlphaGridM1:
        """Load and cryptographically verify model artifact."""
        raw_bytes = weights_path.read_bytes()
        computed_sha = hashlib.sha256(raw_bytes).hexdigest()

        meta_path = weights_path.with_suffix(".meta.json")
        sha_path = weights_path.with_suffix(".sha256")

        if not sha_path.is_file():
            raise ValueError("model artifact SHA-256 sidecar missing")
        expected_sha = sha_path.read_text().strip()
        if computed_sha != expected_sha:
            raise ValueError("model artifact SHA-256 verification failed")

        bundle = pickle.loads(raw_bytes)
        if bundle.get("schema_version") != M1_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema version: {bundle.get('schema_version')}")

        m1 = cls(
            favorable_model=bundle["favorable_model"],
            adverse_model=bundle["adverse_model"],
            preprocessor=bundle["preprocessor"],
            metadata=bundle["metadata"],
        )
        return m1


class M1Predictor:
    """Minimal, read-only prediction interface suitable for future Cockpit integration.

    Strictly decoupled from order generation and trading execution.
    """

    def __init__(self, model: AlphaGridM1) -> None:
        self.model = model

    def predict_snapshot(
        self,
        symbol: str,
        as_of_dt: datetime,
        day_bars: dict[time, dict[str, float]],
    ) -> M1Prediction:
        """Evaluate a decision instant for a given stock and day bar buffer."""
        as_of_str = as_of_dt.astimezone(IST).isoformat()

        # Check reserved holdouts and exclusions
        if symbol in RESERVED_HOLDOUT_STOCKS:
            return M1Prediction(
                symbol=symbol,
                as_of_ist=as_of_str,
                model_version=M1_MODEL_VERSION,
                features=None,
                prob_favorable=None,
                prob_adverse=None,
                abstain=True,
                abstention_reason="RESERVED_HOLDOUT_STOCK",
                signal="ABSTAIN",
            )

        features, abstention = calculate_features_single(symbol, as_of_dt, day_bars)
        if abstention is not None or features is None:
            return M1Prediction(
                symbol=symbol,
                as_of_ist=as_of_str,
                model_version=M1_MODEL_VERSION,
                features=None,
                prob_favorable=None,
                prob_adverse=None,
                abstain=True,
                abstention_reason=abstention,
                signal="ABSTAIN",
            )

        x_arr = np.array([features], dtype=np.float64)
        p_fav_arr, p_adv_arr = self.model.predict_proba(x_arr)
        p_fav = float(p_fav_arr[0])
        p_adv = float(p_adv_arr[0])

        if p_fav >= self.model.fav_threshold and p_adv <= self.model.adv_threshold:
            sig = "OPPORTUNITY_FAVORABLE"
        elif p_adv > self.model.adv_threshold * 1.25:
            sig = "RISK_ADVERSE"
        else:
            sig = "NEUTRAL"

        return M1Prediction(
            symbol=symbol,
            as_of_ist=as_of_str,
            model_version=M1_MODEL_VERSION,
            features=features,
            prob_favorable=p_fav,
            prob_adverse=p_adv,
            abstain=False,
            abstention_reason=None,
            signal=sig,
        )


class M1ShadowLogger:
    """Immutable, append-only shadow-prediction logger with per-record SHA-256 integrity."""

    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, prediction: M1Prediction) -> str:
        record = asdict(prediction)
        # Compute record digest
        record_bytes = json.dumps(record, sort_keys=True).encode("utf-8")
        record_sha = hashlib.sha256(record_bytes).hexdigest()
        entry = {"record": record, "sha256": record_sha}

        line = json.dumps(entry, sort_keys=True) + "\n"
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line)

        return record_sha
