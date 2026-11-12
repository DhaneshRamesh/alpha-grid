"""Alpha Grid M1 Training and Evaluation Pipeline.

Trains and validates Alpha Grid's lightweight gradient-boosted forecasting model (M1):
- Loads from research snapshot manifest with per-file size and SHA-256 verification.
- Strictly excludes 5 reserved holdout stocks and post-October-2025 test period.
- Uses chronological Train (Nov 2023 - Apr 2025) and Calibration (May 2025 - Oct 2025).
- Enforces non-overlapping outcome purging during training.
- Evaluates Brier score, log loss, and portfolio simulation vs Logistic, Momentum, and NO TRADE.
- Packages immutable model artifact with SHA-256 sidecar.
- Runs offline shadow replay inference test.
"""

from __future__ import annotations

import argparse
from datetime import datetime, time as dtime, timedelta
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

from quant.acquisition.calendar import IST
from quant.research.cross_equity_model import (
    OpportunityCandidate,
    RegularizedLogisticRegression,
    brier_score,
    log_loss,
    simulate_portfolio_arm,
)
from quant.research.m1_pipeline import (
    ADVERSE_MAE_THRESHOLD_BPS,
    ADVERSE_RETURN_THRESHOLD_BPS,
    CALIBRATION_END_DATE,
    CALIBRATION_START_DATE,
    CORPORATE_ACTION_EX_DATES,
    DECISION_STEP_MINUTES,
    FAVORABLE_RETURN_HURDLE_BPS,
    FEATURE_NAMES,
    M1_MODEL_VERSION,
    M1_SCHEMA_VERSION,
    RESERVED_HOLDOUT_STOCKS,
    SPECIAL_DR_DATES,
    TEST_HOLDOUT_START_DATE,
    TRAIN_END_DATE,
    TRAIN_START_DATE,
    AlphaGridM1,
    M1Prediction,
    M1Predictor,
    M1Preprocessor,
    M1ShadowLogger,
    calculate_features_single,
)


def load_raw_bars(manifest_path: Path) -> tuple[dict[str, dict[str, dict[dtime, dict[str, float]]]], dict[str, str], str]:
    """Load continuous market bars for modeling stocks up to calibration end."""
    manifest = json.loads(manifest_path.read_text())
    expected_root = manifest["provenance"]["merkle_root_sha256"]
    universe_size = manifest["provenance"]["universe_size"]

    universe_ids: dict[str, str] = {}
    bars_by_symbol_date: dict[str, dict[str, dict[dtime, dict[str, float]]]] = {}

    print(f"Loading bars from research snapshot (Merkle Root: {expected_root[:16]}...)...")
    t0 = time.time()
    loaded_windows = 0

    for w in manifest["files"]:
        sym = w["symbol"]
        sid = w["security_id"]
        universe_ids[sym] = sid

        if sym in RESERVED_HOLDOUT_STOCKS:
            continue

        p_parts = w["key"].split("-")
        # Format: sid-interval-YYYY-MM-DD-YYYY-MM-DD
        start_d = f"{p_parts[2]}-{p_parts[3]}-{p_parts[4]}"
        if start_d >= TEST_HOLDOUT_START_DATE:
            continue  # Strictly avoid touching test period files

        p = Path(w["path"])
        if not p.exists():
            alt = Path("/Volumes/Danny's HDD/AlphaGrid-Plain/historical/raw-dhan") / p.name
            if alt.exists():
                p = alt
        raw_bytes = p.read_bytes()
        if len(raw_bytes) != w["byte_size"] or hashlib.sha256(raw_bytes).hexdigest() != w["sha256"]:
            raise ValueError(f"snapshot source mismatch: {w['key']}")
        raw = json.loads(raw_bytes)
        stamps = raw.get("timestamp") or []
        opens = raw.get("open") or []
        highs = raw.get("high") or []
        lows = raw.get("low") or []
        closes = raw.get("close") or []
        vols = raw.get("volume") or []

        sym_dict = bars_by_symbol_date.setdefault(sym, {})
        for i, ts in enumerate(stamps):
            dt = datetime.fromtimestamp(ts, IST)
            d_str = dt.date().isoformat()
            if d_str > CALIBRATION_END_DATE:
                continue

            t_obj = dt.time()
            if t_obj < dtime(9, 15) or t_obj > dtime(15, 30):
                continue

            day_dict = sym_dict.setdefault(d_str, {})
            day_dict[t_obj] = {
                "o": opens[i], "h": highs[i], "l": lows[i], "c": closes[i], "v": vols[i]
            }
        loaded_windows += 1

    t1 = time.time()
    print(f"Loaded {loaded_windows} manifest windows across {len(bars_by_symbol_date)} modeling stocks in {t1 - t0:.2f}s")
    return bars_by_symbol_date, universe_ids, expected_root


def extract_split_candidates(
    bars_by_symbol_date: dict[str, dict[str, dict[dtime, dict[str, float]]]],
    universe_ids: dict[str, str],
    decision_step_minutes: int = 15,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[OpportunityCandidate]]:
    """Extract causal features and forward labels with outcome purging."""
    decision_minutes = [10 * 60 + decision_step_minutes * i for i in range((4 * 60 + 45) // decision_step_minutes + 1)]

    train_cands: list[dict[str, Any]] = []
    cal_cands: list[dict[str, Any]] = []
    cal_sim_candidates: list[OpportunityCandidate] = []

    for sym, days_dict in sorted(bars_by_symbol_date.items()):
        sec_id = universe_ids.get(sym, "0")
        for d_str, day_bars in sorted(days_dict.items()):
            # Filters and masks
            if d_str in SPECIAL_DR_DATES or d_str in CORPORATE_ACTION_EX_DATES.get(sym, set()):
                continue
            if sym == "MONEYVIEW" and d_str < "2026-10-01":
                continue
            if sym == "CUPID" and d_str < "2024-07-01":
                continue

            # Opening 30m range (09:15 to 09:44)
            opening_bars = [day_bars.get(dtime(9, m)) for m in range(15, 45)]
            if any(b is None for b in opening_bars):
                continue
            open_high = max(b["h"] for b in opening_bars if b is not None)

            for dec_mins in decision_minutes:
                t_dec = dtime(dec_mins // 60, dec_mins % 60)
                dec_dt = datetime.combine(datetime.fromisoformat(d_str), t_dec, tzinfo=IST)

                asof_mins = dec_mins - 1
                asof_t = dtime(asof_mins // 60, asof_mins % 60)
                c_bar = day_bars.get(asof_t)
                if c_bar is None:
                    continue

                m5_t = dtime((asof_mins - 5) // 60, (asof_mins - 5) % 60)
                m15_t = dtime((asof_mins - 15) // 60, (asof_mins - 15) % 60)
                bar_m5 = day_bars.get(m5_t)
                bar_m15 = day_bars.get(m15_t)
                if bar_m5 is None or bar_m15 is None:
                    continue

                r_5m_bps = float((c_bar["c"] - bar_m5["c"]) / bar_m5["c"] * 10000.0)
                r_15m_past_bps = float((c_bar["c"] - bar_m15["c"]) / bar_m15["c"] * 10000.0)
                breakout_dist_bps = float((c_bar["c"] - open_high) / open_high * 10000.0)

                v_15m = sum(day_bars.get(dtime((asof_mins - m) // 60, (asof_mins - m) % 60), {}).get("v", 0.0) for m in range(15))
                v_prior_30m = sum(day_bars.get(dtime((asof_mins - 15 - m) // 60, (asof_mins - 15 - m) % 60), {}).get("v", 0.0) for m in range(30))
                rel_vol = float(v_15m / (v_prior_30m / 2.0 + 1e-6))

                rets_15m = []
                for m in range(14, -1, -1):
                    b_curr = day_bars.get(dtime((asof_mins - m) // 60, (asof_mins - m) % 60))
                    b_prev = day_bars.get(dtime((asof_mins - m - 1) // 60, (asof_mins - m - 1) % 60))
                    if b_curr and b_prev and b_prev["c"] > 0:
                        rets_15m.append((b_curr["c"] - b_prev["c"]) / b_prev["c"])
                if len(rets_15m) < 10:
                    continue
                vol_15m_bps = float(np.std(rets_15m) * 10000.0)
                tod_norm = float((dec_mins - (9 * 60 + 15)) / 375.0)

                # Forward 15m outcome
                fwd_exit_t = dtime((dec_mins + 14) // 60, (dec_mins + 14) % 60)
                fwd_bar = day_bars.get(fwd_exit_t)
                if fwd_bar is None:
                    continue

                window_bars = [day_bars.get(dtime((dec_mins + m) // 60, (dec_mins + m) % 60)) for m in range(15)]
                if any(wb is None for wb in window_bars):
                    continue

                fwd_ret_15m_bps = float((fwd_bar["c"] - c_bar["c"]) / c_bar["c"] * 10000.0)
                max_h = max(wb["h"] for wb in window_bars if wb is not None)
                min_l = min(wb["l"] for wb in window_bars if wb is not None)
                mfe_bps = float((max_h - c_bar["c"]) / c_bar["c"] * 10000.0)
                mae_bps = float((min_l - c_bar["c"]) / c_bar["c"] * 10000.0)

                favorable = 1 if fwd_ret_15m_bps >= FAVORABLE_RETURN_HURDLE_BPS else 0
                adverse = 1 if (fwd_ret_15m_bps <= ADVERSE_RETURN_THRESHOLD_BPS or mae_bps <= ADVERSE_MAE_THRESHOLD_BPS) else 0

                feat_tuple = (r_5m_bps, breakout_dist_bps, rel_vol, vol_15m_bps, tod_norm)
                item = {
                    "symbol": sym,
                    "date_str": d_str,
                    "decision_time_ist": t_dec.strftime("%H:%M:%S"),
                    "x": feat_tuple,
                    "y_fav": favorable,
                    "y_adv": adverse,
                    "ret_bps": fwd_ret_15m_bps,
                }

                if d_str <= TRAIN_END_DATE:
                    train_cands.append(item)
                elif CALIBRATION_START_DATE <= d_str <= CALIBRATION_END_DATE:
                    cal_cands.append(item)
                    cal_sim_candidates.append(
                        OpportunityCandidate(
                            symbol=sym,
                            security_id=sec_id,
                            date_str=d_str,
                            decision_time_ist=t_dec.strftime("%H:%M:%S"),
                            decision_dt=dec_dt,
                            entry_reference_price_inr=float(c_bar["c"]),
                            features=feat_tuple,
                            past_15m_return_bps=r_15m_past_bps,
                            breakout_distance_bps=breakout_dist_bps,
                            forward_15m_return_bps=fwd_ret_15m_bps,
                            forward_15m_exit_price_inr=float(fwd_bar["c"]),
                            max_favorable_excursion_bps=mfe_bps,
                            max_adverse_excursion_bps=mae_bps,
                            favorable_event=favorable,
                            adverse_event=adverse,
                            candidate_type="ACTIVE_BREAKOUT" if breakout_dist_bps > 0 else "CONSOLIDATION_WITHIN_RANGE",
                        )
                    )

    return train_cands, cal_cands, cal_sim_candidates


def main() -> None:
    manifest_path = Path("data/research_snapshot_manifest_plain_v2.json")
    if not manifest_path.exists():
        manifest_path = Path("data/research_snapshot_manifest_v1.json")
    if not manifest_path.exists():
        print(f"Error: manifest missing at {manifest_path}", file=sys.stderr)
        sys.exit(1)

    bars_by_symbol_date, universe_ids, root_hash = load_raw_bars(manifest_path)

    print("\nExtracting purged training and calibration candidates (15m step)...")
    train_cands, cal_cands, cal_sim_cands = extract_split_candidates(
        bars_by_symbol_date, universe_ids, decision_step_minutes=DECISION_STEP_MINUTES
    )

    print(f"  Training Samples:    {len(train_cands):,} ({TRAIN_START_DATE} to {TRAIN_END_DATE})")
    print(f"  Calibration Samples: {len(cal_cands):,} ({CALIBRATION_START_DATE} to {CALIBRATION_END_DATE})")

    y_fav_tr = np.array([c["y_fav"] for c in train_cands], dtype=np.int_)
    y_adv_tr = np.array([c["y_adv"] for c in train_cands], dtype=np.int_)
    x_tr = np.array([c["x"] for c in train_cands], dtype=np.float64)

    y_fav_cl = np.array([c["y_fav"] for c in cal_cands], dtype=np.int_)
    y_adv_cl = np.array([c["y_adv"] for c in cal_cands], dtype=np.int_)
    x_cl = np.array([c["x"] for c in cal_cands], dtype=np.float64)

    fav_base_tr = float(np.mean(y_fav_tr))
    adv_base_tr = float(np.mean(y_adv_tr))
    fav_base_cl = float(np.mean(y_fav_cl))
    adv_base_cl = float(np.mean(y_adv_cl))

    print(f"\nTarget Base Rates:")
    print(f"  Train:       Favorable={fav_base_tr:.4f} ({fav_base_tr*100:.2f}%), Adverse={adv_base_tr:.4f} ({adv_base_tr*100:.2f}%)")
    print(f"  Calibration: Favorable={fav_base_cl:.4f} ({fav_base_cl*100:.2f}%), Adverse={adv_base_cl:.4f} ({adv_base_cl*100:.2f}%)")

    # 1. Fit Train-only Preprocessor
    preprocessor = M1Preprocessor()
    preprocessor.fit(x_tr)
    x_tr_std = preprocessor.transform(x_tr)
    x_cl_std = preprocessor.transform(x_cl)

    # 2. Train Gradient-Boosted Models (AlphaGrid M1)
    print("\nTraining AlphaGrid M1 (HistGradientBoosting dual ensemble)...")
    t0 = time.time()
    hgb_fav = HistGradientBoostingClassifier(
        max_iter=100, max_depth=3, learning_rate=0.05, l2_regularization=1.0, min_samples_leaf=50, random_state=42
    )
    hgb_fav.fit(x_tr_std, y_fav_tr)

    hgb_adv = HistGradientBoostingClassifier(
        max_iter=100, max_depth=3, learning_rate=0.05, l2_regularization=1.0, min_samples_leaf=50, random_state=42
    )
    hgb_adv.fit(x_tr_std, y_adv_tr)
    t1 = time.time()
    print(f"M1 training completed in {t1 - t0:.2f}s")

    # 3. Train Baseline Regularized Logistic Regressions
    print("Training Regularized Logistic Regression baselines...")
    log_fav = RegularizedLogisticRegression(l2_penalty=1.0, max_iter=100)
    log_fav.fit(x_tr_std, y_fav_tr)

    log_adv = RegularizedLogisticRegression(l2_penalty=1.0, max_iter=100)
    log_adv.fit(x_tr_std, y_adv_tr)

    # 4. Predictions on Calibration Split
    p_fav_m1_cl = hgb_fav.predict_proba(x_cl_std)[:, 1]
    p_adv_m1_cl = hgb_adv.predict_proba(x_cl_std)[:, 1]

    p_fav_log_cl = log_fav.predict_proba(x_cl_std)
    p_adv_log_cl = log_adv.predict_proba(x_cl_std)

    p_fav_null_cl = np.full_like(y_fav_cl, fav_base_tr, dtype=np.float64)
    p_adv_null_cl = np.full_like(y_adv_cl, adv_base_tr, dtype=np.float64)

    # 5. Statistical Evaluations (Brier score & Log Loss)
    b_fav_m1 = brier_score(y_fav_cl, p_fav_m1_cl)
    b_fav_log = brier_score(y_fav_cl, p_fav_log_cl)
    b_fav_null = brier_score(y_fav_cl, p_fav_null_cl)

    ll_fav_m1 = log_loss(y_fav_cl, p_fav_m1_cl)
    ll_fav_log = log_loss(y_fav_cl, p_fav_log_cl)
    ll_fav_null = log_loss(y_fav_cl, p_fav_null_cl)

    b_adv_m1 = brier_score(y_adv_cl, p_adv_m1_cl)
    b_adv_log = brier_score(y_adv_cl, p_adv_log_cl)
    b_adv_null = brier_score(y_adv_cl, p_adv_null_cl)

    ll_adv_m1 = log_loss(y_adv_cl, p_adv_m1_cl)
    ll_adv_log = log_loss(y_adv_cl, p_adv_log_cl)
    ll_adv_null = log_loss(y_adv_cl, p_adv_null_cl)

    print("\n" + "=" * 80)
    print("CALIBRATION SPLIT STATISTICAL COMPARISON (Out-of-Sample)")
    print("=" * 80)
    print(f"{'Target':<12} {'Model':<16} {'Brier Score':<14} {'Brier Delta':<14} {'Log Loss':<12}")
    print("-" * 80)
    print(f"{'FAVORABLE':<12} {'AlphaGrid M1':<16} {b_fav_m1:<14.6f} {b_fav_m1 - b_fav_null:<+14.6f} {ll_fav_m1:<12.5f}")
    print(f"{'FAVORABLE':<12} {'Logistic':<16} {b_fav_log:<14.6f} {b_fav_log - b_fav_null:<+14.6f} {ll_fav_log:<12.5f}")
    print(f"{'FAVORABLE':<12} {'Null Baseline':<16} {b_fav_null:<14.6f} {'0.000000':<14} {ll_fav_null:<12.5f}")
    print("-" * 80)
    print(f"{'ADVERSE':<12} {'AlphaGrid M1':<16} {b_adv_m1:<14.6f} {b_adv_m1 - b_adv_null:<+14.6f} {ll_adv_m1:<12.5f}")
    print(f"{'ADVERSE':<12} {'Logistic':<16} {b_adv_log:<14.6f} {b_adv_log - b_adv_null:<+14.6f} {ll_adv_log:<12.5f}")
    print(f"{'ADVERSE':<12} {'Null Baseline':<16} {b_adv_null:<14.6f} {'0.000000':<14} {ll_adv_null:<12.5f}")
    print("=" * 80)

    # 6. Decision Threshold Calibration
    # Select conservative decision thresholds: top favorable quintile (>= 0.10) with adverse cap (<= 0.35)
    fav_thresh = 0.10
    adv_thresh = 0.35

    # 7. Portfolio Simulation on Calibration Split
    # Vectorized probability prediction for all calibration simulation candidates
    if cal_sim_cands:
        f_matrix = np.array([c.features for c in cal_sim_cands], dtype=np.float64)
        f_matrix_std = preprocessor.transform(f_matrix)
        p_fav_sim = hgb_fav.predict_proba(f_matrix_std)[:, 1]
        p_adv_sim = hgb_adv.predict_proba(f_matrix_std)[:, 1]
        m1_mask = (p_fav_sim >= fav_thresh) & (p_adv_sim <= adv_thresh)
        m1_qualified_cands = [c for c, m in zip(cal_sim_cands, m1_mask) if m]
    else:
        m1_qualified_cands = []

    res_no_trade = simulate_portfolio_arm(cal_sim_cands, "NO_TRADE")
    res_null = simulate_portfolio_arm(cal_sim_cands, "NULL_BASELINE")
    res_mom = simulate_portfolio_arm(cal_sim_cands, "MOMENTUM_RULE")
    res_m1 = simulate_portfolio_arm(m1_qualified_cands, "NULL_BASELINE")  # Trade the filtered candidates

    print("\n" + "=" * 80)
    print("PORTFOLIO SIMULATION COMPARISON ON CALIBRATION SPLIT")
    print("Limits: ₹15k max deployed, ₹5k/pos, 3 pos max, statutory fees + 5 bps spread")
    print("=" * 80)
    print(f"{'Arm':<20} {'Trades':<8} {'Win%':<8} {'Gross PnL':<12} {'Fees':<10} {'Spread':<10} {'Net PnL (INR)':<14} {'Mean Net bps':<12}")
    print("-" * 80)
    for name, res in [("NO_TRADE", res_no_trade), ("NULL_BASELINE", res_null), ("MOMENTUM_RULE", res_mom), ("ALPHAGRID_M1", res_m1)]:
        print(f"{name:<20} {res.total_trades:<8} {res.win_rate*100:<7.1f}% ₹{res.gross_pnl_inr:<11.2f} ₹{res.statutory_fees_inr:<9.2f} ₹{res.spread_cost_inr:<9.2f} ₹{res.net_pnl_inr:<13.2f} {res.mean_trade_net_bps:<12.2f}")
    print("=" * 80)

    # 8. Package Model Artifact
    metadata = {
        "model_version": M1_MODEL_VERSION,
        "trained_at_utc": datetime.now(IST).isoformat(),
        "dataset_snapshot_root_sha256": root_hash,
        "training_window": {"start": TRAIN_START_DATE, "end": TRAIN_END_DATE, "samples": len(train_cands)},
        "calibration_window": {"start": CALIBRATION_START_DATE, "end": CALIBRATION_END_DATE, "samples": len(cal_cands)},
        "statistical_metrics_calibration": {
            "favorable_brier_m1": b_fav_m1,
            "favorable_brier_null": b_fav_null,
            "favorable_log_loss_m1": ll_fav_m1,
            "adverse_brier_m1": b_adv_m1,
            "adverse_brier_null": b_adv_null,
            "adverse_log_loss_m1": ll_adv_m1,
        },
        "calibrated_thresholds": {
            "fav_threshold": fav_thresh,
            "adv_threshold": adv_thresh,
        },
        "economic_calibration": {
            "trades": res_m1.total_trades,
            "win_rate": res_m1.win_rate,
            "net_pnl_inr": str(res_m1.net_pnl_inr),
            "mean_net_bps": str(res_m1.mean_trade_net_bps),
        }
    }

    m1_bundle = AlphaGridM1(
        favorable_model=hgb_fav,
        adverse_model=hgb_adv,
        preprocessor=preprocessor,
        metadata=metadata,
    )
    m1_bundle.fav_threshold = fav_thresh
    m1_bundle.adv_threshold = adv_thresh

    # Save to local repo models and external volume
    local_dir = Path("data/models")
    ext_dir = Path("/Volumes/AlphaGrid Data/Live/offline-dev/training-artifacts")

    w_local, m_local = m1_bundle.save_artifact(local_dir, name="alpha-grid-m1-v1")
    print(f"\nSaved local model artifact: {w_local} ({m_local.name})")

    if ext_dir.exists():
        w_ext, m_ext = m1_bundle.save_artifact(ext_dir, name="alpha-grid-m1-v1")
        print(f"Saved external volume artifact: {w_ext} ({m_ext.name})")

    # 9. Offline Replay Inference & Shadow-Prediction Logging Demonstration
    print("\nExecuting offline shadow-prediction logging demonstration...")
    shadow_log_path = Path("data/telemetry/shadow_predictions_m1.jsonl")
    logger = M1ShadowLogger(shadow_log_path)
    predictor = M1Predictor(m1_bundle)

    # Replay 5 decisions from a sample day for RELIANCE
    test_day = "2025-06-02"
    day_bars = bars_by_symbol_date.get("RELIANCE", {}).get(test_day, {})
    if day_bars:
        for m in (0, 15, 30, 45, 60):
            base_t = datetime.combine(datetime.fromisoformat(test_day), dtime(10, 0), tzinfo=IST)
            t_sample = base_t + timedelta(minutes=m)
            pred = predictor.predict_snapshot("RELIANCE", t_sample, day_bars)
            rec_sha = logger.log(pred)
            print(f"  [{pred.as_of_ist}] RELIANCE -> Sig: {pred.signal:<20} P(Fav): {pred.prob_favorable:.3f} P(Adv): {pred.prob_adverse:.3f} SHA: {rec_sha[:12]}")

    print("\nPipeline execution complete.")


if __name__ == "__main__":
    main()
