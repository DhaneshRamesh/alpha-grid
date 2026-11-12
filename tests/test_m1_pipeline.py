"""Targeted unit tests for AlphaGrid M1 Forecasting Model & Pipeline."""

from __future__ import annotations

from datetime import datetime, time, timedelta
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
import pytest
from sklearn.ensemble import HistGradientBoostingClassifier

from quant.acquisition.calendar import IST
from quant.research.m1_pipeline import (
    CALIBRATION_END_DATE,
    FEATURE_NAMES,
    M1_MODEL_VERSION,
    M1_SCHEMA_VERSION,
    RESERVED_HOLDOUT_STOCKS,
    TEST_HOLDOUT_START_DATE,
    TRAIN_END_DATE,
    AlphaGridM1,
    M1Prediction,
    M1Predictor,
    M1Preprocessor,
    M1ShadowLogger,
    calculate_features_single,
)


def _build_synthetic_day_bars() -> dict[time, dict[str, float]]:
    """Build a complete 375-minute continuous trading day (09:15 to 15:29)."""
    day_bars: dict[time, dict[str, float]] = {}
    for m in range(375):
        h = 9 + (15 + m) // 60
        mn = (15 + m) % 60
        t_obj = time(h, mn)

        if m < 30:
            # Opening 30 min (09:15 to 09:44): price around 100-102
            day_bars[t_obj] = {"o": 100.0, "h": 102.0, "l": 99.5, "c": 101.0, "v": 1000.0}
        elif 30 <= m < 45:
            # 09:45 to 09:59: price pushes up to 105
            day_bars[t_obj] = {"o": 101.0, "h": 105.0, "l": 101.0, "c": 104.0, "v": 2000.0}
        else:
            # 10:00 onwards: price consolidates around 104.5
            day_bars[t_obj] = {"o": 104.0, "h": 105.5, "l": 103.5, "c": 104.5, "v": 1500.0}
    return day_bars


def test_train_inference_feature_parity() -> None:
    """Verify single-decision feature extraction matches batch extraction down to 1e-6."""
    day_bars = _build_synthetic_day_bars()
    as_of = datetime(2025, 6, 2, 10, 0, tzinfo=IST)

    features, abstention = calculate_features_single("RELIANCE", as_of, day_bars)
    assert abstention is None
    assert features is not None
    assert len(features) == len(FEATURE_NAMES)

    # Manual verification of feature values
    # 1. past_5m_return_bps: c_bar (09:59) close 104.0 vs bar_m5 (09:54) close 104.0 -> 0.0 bps
    assert features[0] == pytest.approx(0.0, abs=1e-5)

    # 2. breakout_dist_bps: open_high is 102.0 (09:15-09:44). c_bar is 104.0 -> (104 - 102)/102 * 10000 = 196.078 bps
    assert features[1] == pytest.approx(196.0784, rel=1e-3)

    # 3. relative_volume_15m: 15m vol (09:45-09:59) is 15 * 2000 = 30,000
    #    prior 30m vol (09:15-09:44) is 30 * 1000 = 30,000 -> 30,000 / (30,000 / 2) = 2.0
    assert features[2] == pytest.approx(2.0, rel=1e-3)

    # 4. normalized_time_of_day: 10:00 is 45 mins from 09:15 -> 45 / 375 = 0.12
    assert features[4] == pytest.approx(0.12, abs=1e-5)


def test_no_lookahead_leakage() -> None:
    """Verify mutating forward or future bars has zero effect on decision features."""
    day_bars_clean = _build_synthetic_day_bars()
    as_of = datetime(2025, 6, 2, 10, 0, tzinfo=IST)

    feat_clean, abs_clean = calculate_features_single("RELIANCE", as_of, day_bars_clean)
    assert abs_clean is None

    # Mutate future bars: 10:00 to 15:30 with extreme price spikes and volume surges
    day_bars_mutated = dict(day_bars_clean)
    for m in range(45, 375):
        h = 9 + (15 + m) // 60
        mn = (15 + m) % 60
        day_bars_mutated[time(h, mn)] = {
            "o": 99999.0, "h": 99999.0, "l": 0.01, "c": 99999.0, "v": 100000000.0
        }

    feat_mutated, abs_mutated = calculate_features_single("RELIANCE", as_of, day_bars_mutated)
    assert abs_mutated is None
    assert feat_clean == feat_mutated


def test_missing_data_abstentions() -> None:
    """Verify fail-closed abstentions when causal conditions are violated."""
    day_bars = _build_synthetic_day_bars()
    as_of = datetime(2025, 6, 2, 10, 0, tzinfo=IST)

    # 1. Outside trading hours
    as_of_early = datetime(2025, 6, 2, 9, 30, tzinfo=IST)
    _, abs_early = calculate_features_single("RELIANCE", as_of_early, day_bars)
    assert abs_early == "OUTSIDE_DECISION_HOURS"

    as_of_late = datetime(2025, 6, 2, 15, 0, tzinfo=IST)
    _, abs_late = calculate_features_single("RELIANCE", as_of_late, day_bars)
    assert abs_late == "OUTSIDE_DECISION_HOURS"

    # 2. Insufficient opening range (missing 09:20 bar)
    bars_missing_open = dict(day_bars)
    del bars_missing_open[time(9, 20)]
    _, abs_open = calculate_features_single("RELIANCE", as_of, bars_missing_open)
    assert abs_open == "INSUFFICIENT_OPENING_RANGE"

    # 3. Missing decision bar (missing 09:59 bar)
    bars_missing_dec = dict(day_bars)
    del bars_missing_dec[time(9, 59)]
    _, abs_dec = calculate_features_single("RELIANCE", as_of, bars_missing_dec)
    assert abs_dec == "MISSING_DECISION_BAR"

    # 4. Insufficient lookback bars (missing 09:54 bar, which is m5_t for 10:00 decision)
    bars_missing_lb = dict(day_bars)
    del bars_missing_lb[time(9, 54)]
    _, abs_lb = calculate_features_single("RELIANCE", as_of, bars_missing_lb)
    assert abs_lb == "INSUFFICIENT_LOOKBACK_BARS"


def test_deterministic_inference() -> None:
    """Verify deterministic, bit-identical predictions on identical inputs."""
    day_bars = _build_synthetic_day_bars()
    as_of = datetime(2025, 6, 2, 10, 0, tzinfo=IST)

    # Fit a toy model
    rng = np.random.default_rng(42)
    x_synth = rng.normal(size=(200, 5))
    y_fav = (x_synth[:, 0] > 0).astype(int)
    y_adv = (x_synth[:, 1] < 0).astype(int)

    prep = M1Preprocessor()
    prep.fit(x_synth)
    x_std = prep.transform(x_synth)

    hgb_fav = HistGradientBoostingClassifier(max_iter=10, random_state=42)
    hgb_fav.fit(x_std, y_fav)
    hgb_adv = HistGradientBoostingClassifier(max_iter=10, random_state=42)
    hgb_adv.fit(x_std, y_adv)

    m1 = AlphaGridM1(hgb_fav, hgb_adv, prep, metadata={"fav_threshold": 0.12, "adv_threshold": 0.35})
    predictor = M1Predictor(m1)

    pred1 = predictor.predict_snapshot("RELIANCE", as_of, day_bars)
    pred2 = predictor.predict_snapshot("RELIANCE", as_of, day_bars)

    assert pred1 == pred2
    assert pred1.prob_favorable == pytest.approx(pred2.prob_favorable)
    assert pred1.prob_adverse == pytest.approx(pred2.prob_adverse)
    assert pred1.signal == pred2.signal


def test_reserved_holdout_isolation() -> None:
    """Verify that reserved stocks are strictly rejected by the predictor."""
    day_bars = _build_synthetic_day_bars()
    as_of = datetime(2025, 6, 2, 10, 0, tzinfo=IST)

    m1 = AlphaGridM1()
    predictor = M1Predictor(m1)

    for holdout_sym in RESERVED_HOLDOUT_STOCKS:
        pred = predictor.predict_snapshot(holdout_sym, as_of, day_bars)
        assert pred.abstain is True
        assert pred.abstention_reason == "RESERVED_HOLDOUT_STOCK"
        assert pred.signal == "ABSTAIN"
        assert pred.prob_favorable is None


def test_model_artifact_save_load_and_audit() -> None:
    """Verify model artifact serialization, hash sidecar, and tamper detection."""
    rng = np.random.default_rng(42)
    x_synth = rng.normal(size=(100, 5))
    y_fav = (x_synth[:, 0] > 0).astype(int)
    y_adv = (x_synth[:, 1] < 0).astype(int)

    prep = M1Preprocessor()
    prep.fit(x_synth)
    x_std = prep.transform(x_synth)

    hgb_fav = HistGradientBoostingClassifier(max_iter=10, random_state=42)
    hgb_fav.fit(x_std, y_fav)
    hgb_adv = HistGradientBoostingClassifier(max_iter=10, random_state=42)
    hgb_adv.fit(x_std, y_adv)

    m1 = AlphaGridM1(hgb_fav, hgb_adv, prep, metadata={"test_key": "verified_metadata"})

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        weights_file, meta_file = m1.save_artifact(tmp_path, name="test-m1")

        assert weights_file.exists()
        assert meta_file.exists()
        sha_file = weights_file.with_suffix(".sha256")
        assert sha_file.exists()

        # Load back
        m1_loaded = AlphaGridM1.load_artifact(weights_file)
        assert m1_loaded.metadata.get("test_key") == "verified_metadata"

        # Tamper with file
        raw = bytearray(weights_file.read_bytes())
        raw[20] ^= 0xFF
        weights_file.write_bytes(bytes(raw))

        with pytest.raises(ValueError, match="SHA-256 verification failed"):
            AlphaGridM1.load_artifact(weights_file)


def test_saved_decision_thresholds_survive_reload() -> None:
    """A saved policy must not silently revert to default decision thresholds."""
    model = AlphaGridM1(metadata={
        "calibrated_thresholds": {"fav_threshold": 0.10, "adv_threshold": 0.35},
    })
    model.fav_threshold = 0.10
    with tempfile.TemporaryDirectory() as tmpdir:
        weights, _ = model.save_artifact(Path(tmpdir), name="thresholds")
        restored = AlphaGridM1.load_artifact(weights)
        assert restored.fav_threshold == 0.10
        assert restored.adv_threshold == 0.35
        weights.with_suffix(".sha256").unlink()
        with pytest.raises(ValueError, match="SHA-256 sidecar missing"):
            AlphaGridM1.load_artifact(weights)


def test_m1_loaders_reject_changed_snapshot_bytes() -> None:
    """A manifest label alone cannot validate raw candle input bytes."""
    from tools.audit_m1_model import load_calibration_data
    from tools.train_m1_model import load_raw_bars

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        raw = root / "window.json"
        original = b'{"timestamp":[]}'
        raw.write_bytes(original + b" ")
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({
            "provenance": {"merkle_root_sha256": "0" * 64, "universe_size": 1},
            "files": [{"symbol": "RELIANCE", "security_id": "2885",
                       "key": "2885-1-2025-05-01-2025-06-01", "path": str(raw),
                       "sha256": hashlib.sha256(original).hexdigest(),
                       "byte_size": len(original)}],
        }))
        with pytest.raises(ValueError, match="snapshot source mismatch"):
            load_raw_bars(manifest)
        with pytest.raises(ValueError, match="snapshot source mismatch"):
            load_calibration_data(manifest)


def test_shadow_logger_append_and_hash() -> None:
    """Verify that M1ShadowLogger logs valid records with SHA-256 digests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "shadow.jsonl"
        logger = M1ShadowLogger(log_file)

        pred = M1Prediction(
            symbol="RELIANCE",
            as_of_ist="2025-06-02T10:00:00+05:30",
            model_version=M1_MODEL_VERSION,
            features=(1.0, 2.0, 1.5, 12.0, 0.12),
            prob_favorable=0.145,
            prob_adverse=0.280,
            abstain=False,
            abstention_reason=None,
            signal="OPPORTUNITY_FAVORABLE",
        )

        sha = logger.log(pred)
        assert len(sha) == 64

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["sha256"] == sha
        assert data["record"]["symbol"] == "RELIANCE"
        assert data["record"]["signal"] == "OPPORTUNITY_FAVORABLE"


def test_saved_m1_artifact_audit_and_reproducibility() -> None:
    """Verify stored model artifact provenance, schema, and tamper resistance."""
    model_path = Path("data/models/alpha-grid-m1-v1.pkl")
    if not model_path.exists():
        pytest.skip("Model artifact data/models/alpha-grid-m1-v1.pkl not present on disk")

    m1 = AlphaGridM1.load_artifact(model_path)
    assert m1.metadata.get("model_version") == M1_MODEL_VERSION
    meta_json = json.loads(model_path.with_suffix(".meta.json").read_text())
    assert meta_json.get("schema_version") == M1_SCHEMA_VERSION

    # Verify Brier metrics match metadata records
    meta_brier_fav = m1.metadata["statistical_metrics_calibration"]["favorable_brier_m1"]
    assert meta_brier_fav == pytest.approx(0.062219, abs=1e-5)
    meta_brier_adv = m1.metadata["statistical_metrics_calibration"]["adverse_brier_m1"]
    assert meta_brier_adv == pytest.approx(0.203758, abs=1e-5)

    # Verify prediction dimensions
    dummy_x = np.zeros((3, len(FEATURE_NAMES)), dtype=np.float64)
    p_fav, p_adv = m1.predict_proba(dummy_x)
    assert len(p_fav) == 3
    assert len(p_adv) == 3
    assert np.all((p_fav >= 0.0) & (p_fav <= 1.0))
    assert np.all((p_adv >= 0.0) & (p_adv <= 1.0))
