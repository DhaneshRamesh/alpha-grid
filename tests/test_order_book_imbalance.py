"""Unit tests for Five-Level Order-Book Imbalance Research Module."""

from dataclasses import replace
from decimal import Decimal
import hashlib
import json
import math
import struct
import zlib
from pathlib import Path
import pytest

from quant.research.order_book_imbalance import (
    BookLevel,
    FiveLevelSnapshot,
    DepthFeatures,
    AdverseTargetOutcome,
    EconomicFilterTrade,
    brier_score,
    log_loss,
    classify_partition,
    locate_outcome_quote,
    evaluate_filter_economics,
    assess_repository_depth_readiness,
)


def _sample_valid_book() -> tuple[tuple[BookLevel, ...], tuple[BookLevel, ...]]:
    bids = (
        BookLevel(price_nanos=1314_500_000_000, displayed_quantity=1000, order_count=5),
        BookLevel(price_nanos=1314_400_000_000, displayed_quantity=1200, order_count=6),
        BookLevel(price_nanos=1314_300_000_000, displayed_quantity=1500, order_count=8),
        BookLevel(price_nanos=1314_200_000_000, displayed_quantity=2000, order_count=10),
        BookLevel(price_nanos=1314_100_000_000, displayed_quantity=2500, order_count=12),
    )
    asks = (
        BookLevel(price_nanos=1314_700_000_000, displayed_quantity=500, order_count=3),
        BookLevel(price_nanos=1314_800_000_000, displayed_quantity=800, order_count=4),
        BookLevel(price_nanos=1314_900_000_000, displayed_quantity=1000, order_count=5),
        BookLevel(price_nanos=1315_000_000_000, displayed_quantity=1200, order_count=6),
        BookLevel(price_nanos=1315_100_000_000, displayed_quantity=1500, order_count=7),
    )
    return bids, asks


def _qj_full_pair(path: Path, start_ns: int, exit_ns: int) -> tuple[FiveLevelSnapshot, FiveLevelSnapshot]:
    """Isolated QJ01/Full software fixture; no fixture enters market research."""
    def varint(value: int) -> bytes:
        out = bytearray()
        while value >= 128:
            out.append((value & 127) | 128)
            value >>= 7
        out.append(value)
        return bytes(out)

    packet = bytearray(162)
    packet[0] = 8
    struct.pack_into("<H", packet, 1, 162)
    packet[3] = 1
    struct.pack_into("<i", packet, 4, 2885)
    struct.pack_into("<f", packet, 8, 100.5)
    bids = tuple(BookLevel((100 - i) * 10**9, 100, 2) for i in range(5))
    asks = tuple(BookLevel((101 + i) * 10**9, 100, 3) for i in range(5))
    for i in range(5):
        at = 62 + i * 20
        struct.pack_into("<IIHHff", packet, at, 100, 100, 2, 3, 100 - i, 101 + i)
    raw = bytes(packet)
    digest = hashlib.sha256(raw).digest()
    session = b"session_1"
    snapshots = []
    with path.open("wb") as journal:
        for frame_id, stamp in enumerate((start_ns, exit_ns), 1):
            offset = journal.tell()
            payload = (varint(len(session)) + session + varint(6) + varint(len(session))
                       + session + varint(frame_id) + varint(stamp << 1)
                       + digest + varint(len(raw)) + raw)
            journal.write(struct.pack("<4sII", b"QJ01", len(payload), zlib.crc32(payload)))
            journal.write(payload)
            snapshots.append(FiveLevelSnapshot.validate_and_create(
                "RELIANCE", "2885", stamp, None, bids, asks,
                raw_frame_sha256=digest.hex(), journal_offset=offset,
                source_frame_id=str(frame_id), packet_offset=0,
                raw_durable_at_ns=stamp + 1, feed_session_id="session_1",
            ))
    return snapshots[0], snapshots[1]


def test_valid_five_level_book_and_features() -> None:
    bids, asks = _sample_valid_book()
    receive_ns = 1793780000000000000
    snap = FiveLevelSnapshot.validate_and_create(
        symbol="RELIANCE",
        security_id="2885",
        receive_timestamp_ns=receive_ns,
        exchange_timestamp_ns=receive_ns - 10_000_000,
        bids=bids,
        asks=asks,
        now_ns=receive_ns + 500_000_000,  # 0.5s age, fresh
    )
    assert snap.is_valid is True
    assert snap.rejection_reason is None

    features = DepthFeatures.from_snapshot(snap)
    assert features is not None
    assert features.symbol == "RELIANCE"
    assert features.mid_quote_nanos == 1314_600_000_000
    # Spread: (1314.70 - 1314.50) / 1314.60 * 10000 = 0.20 / 1314.60 * 10000 ≈ 1.5213 bps
    assert 1.52 < features.quoted_spread_bps < 1.53

    # L1 imbalance: (1000 - 500) / (1000 + 500) = 500 / 1500 = 1/3 ≈ 0.3333
    assert abs(features.imbalance_l1 - (1.0 / 3.0)) < 1e-4

    # L5 imbalance: Bids total = 8200, Asks total = 5000. (8200 - 5000) / 13200 = 3200 / 13200 ≈ 0.2424
    assert abs(features.imbalance_l5 - (3200.0 / 13200.0)) < 1e-4

    assert features.total_bid_depth_qty_l5 == 8200
    assert features.total_ask_depth_qty_l5 == 5000


def test_book_invalidation_rules() -> None:
    bids, asks = _sample_valid_book()
    receive_ns = 1793780000000000000

    # 1. Crossed book: Bid >= Ask
    crossed_bids = (BookLevel(1314_750_000_000, 1000), *bids[1:])
    snap_crossed = FiveLevelSnapshot.validate_and_create(
        "RELIANCE", "2885", receive_ns, None, crossed_bids, asks
    )
    assert snap_crossed.is_valid is False
    assert snap_crossed.rejection_reason == "CROSSED_OR_LOCKED_BOOK"
    assert DepthFeatures.from_snapshot(snap_crossed) is None

    # 2. Non-monotonic bids: Bid0 < Bid1
    non_mono_bids = (bids[1], bids[0], *bids[2:])
    snap_mono = FiveLevelSnapshot.validate_and_create(
        "RELIANCE", "2885", receive_ns, None, non_mono_bids, asks
    )
    assert snap_mono.is_valid is False
    assert snap_mono.rejection_reason == "BIDS_NOT_STRICTLY_DESCENDING"

    # 3. Insufficient levels: only 4 levels
    snap_short = FiveLevelSnapshot.validate_and_create(
        "RELIANCE", "2885", receive_ns, None, bids[:4], asks[:4]
    )
    assert snap_short.is_valid is False
    assert snap_short.rejection_reason == "INSUFFICIENT_LEVELS"

    # 4. Stale quote: age > 2s
    snap_stale = FiveLevelSnapshot.validate_and_create(
        "RELIANCE", "2885", receive_ns, None, bids, asks,
        now_ns=receive_ns + 3_000_000_000  # 3.0s age
    )
    assert snap_stale.is_valid is False
    assert snap_stale.rejection_reason == "STALE_QUOTE_AGE_EXCEEDED"


def test_loss_metrics() -> None:
    y_true = [0, 1, 0, 0]
    y_prob = [0.1, 0.8, 0.2, 0.1]
    # Brier: ((0.1)^2 + (0.8-1)^2 + (0.2)^2 + (0.1)^2) / 4 = (0.01 + 0.04 + 0.04 + 0.01) / 4 = 0.10 / 4 = 0.025
    bs = brier_score(y_true, y_prob)
    assert abs(bs - 0.025) < 1e-6

    ll = log_loss(y_true, y_prob)
    assert ll > 0.0


def test_adverse_target_outcome_evaluation() -> None:
    decision_ns = 1_000_000_000_000
    entry_mid = 1000_000_000_000  # ₹1000.00

    # 1. Immature outcome (< 300s) -> Censored
    outcome_immature = AdverseTargetOutcome.evaluate(
        symbol="RELIANCE",
        decision_timestamp_ns=decision_ns,
        entry_mid_nanos=entry_mid,
        exit_mid_nanos=998_000_000_000,
        observed_at_ns=decision_ns + 200_000_000_000,  # only 200s elapsed
    )
    assert outcome_immature.is_censored is True
    assert outcome_immature.censoring_reason == "OUTCOME_NOT_YET_MATURE"
    assert outcome_immature.is_adverse is None

    # 2. Missing exit quote at maturity -> Censored
    outcome_missing = AdverseTargetOutcome.evaluate(
        symbol="RELIANCE",
        decision_timestamp_ns=decision_ns,
        entry_mid_nanos=entry_mid,
        exit_mid_nanos=None,
        observed_at_ns=decision_ns + 300_000_000_000,
    )
    assert outcome_missing.is_censored is True
    assert outcome_missing.censoring_reason == "FORWARD_QUOTE_UNAVAILABLE_OR_INVALID"
    assert outcome_missing.is_adverse is None

    # 3. Mature adverse outcome: Price drops ₹1000 -> ₹998.90 (-11 bps <= -10 bps) -> is_adverse == 1
    exit_adverse = 998_900_000_000  # -11.0 bps
    outcome_adverse = AdverseTargetOutcome.evaluate(
        symbol="RELIANCE",
        decision_timestamp_ns=decision_ns,
        entry_mid_nanos=entry_mid,
        exit_mid_nanos=exit_adverse,
        observed_at_ns=decision_ns + 300_000_000_000,
    )
    assert outcome_adverse.is_censored is False
    assert outcome_adverse.is_adverse == 1
    assert outcome_adverse.forward_return_bps == -11.0

    # 4. Mature non-adverse outcome: Price drops ₹1000 -> ₹999.50 (-5 bps > -10 bps) -> is_adverse == 0
    exit_non_adverse = 999_500_000_000  # -5.0 bps
    outcome_non_adverse = AdverseTargetOutcome.evaluate(
        symbol="RELIANCE",
        decision_timestamp_ns=decision_ns,
        entry_mid_nanos=entry_mid,
        exit_mid_nanos=exit_non_adverse,
        observed_at_ns=decision_ns + 300_000_000_000,
    )
    assert outcome_non_adverse.is_censored is False
    assert outcome_non_adverse.is_adverse == 0
    assert outcome_non_adverse.forward_return_bps == -5.0


def test_partitioning_and_purging() -> None:
    # 3 partitions of 3600 seconds each with 300s purging buffer
    train_start = 10_000 * 1_000_000_000
    train_end = 20_000 * 1_000_000_000
    calib_start = 20_000 * 1_000_000_000
    calib_end = 30_000 * 1_000_000_000
    eval_start = 30_000 * 1_000_000_000
    eval_end = 40_000 * 1_000_000_000

    # Early training: 15_000s -> TRAINING
    p1 = classify_partition(15_000 * 1_000_000_000, train_start, train_end, calib_start, calib_end, eval_start, eval_end)
    assert p1 == "TRAINING"

    # Within 300s purge boundary before calib (19_800s): PURGED
    p_purge1 = classify_partition(19_800 * 1_000_000_000, train_start, train_end, calib_start, calib_end, eval_start, eval_end)
    assert p_purge1 == "PURGED"

    # Mid calibration: 25_000s -> CALIBRATION
    p_calib = classify_partition(25_000 * 1_000_000_000, train_start, train_end, calib_start, calib_end, eval_start, eval_end)
    assert p_calib == "CALIBRATION"

    # Within 300s purge boundary before eval (29_850s): PURGED
    p_purge2 = classify_partition(29_850 * 1_000_000_000, train_start, train_end, calib_start, calib_end, eval_start, eval_end)
    assert p_purge2 == "PURGED"

    # Evaluation partition: 35_000s -> EVALUATION
    p_eval = classify_partition(35_000 * 1_000_000_000, train_start, train_end, calib_start, calib_end, eval_start, eval_end)
    assert p_eval == "EVALUATION"


def test_filter_economics_tradeoff_and_single_counting() -> None:
    # Trade 1: Adverse loser (Gross -45, fees 5 -> Net -50)
    # Filter abstained (avoided loser)
    t1 = EconomicFilterTrade(
        symbol="RELIANCE",
        decision_timestamp_ns=1_000_000_000,
        predicted_adverse_prob=0.85,
        rejection_threshold=0.60,
        abstained=True,
        notional_inr=Decimal("5000.00"),
        unfiltered_gross_pnl_inr=Decimal("-45.00"),
        unfiltered_net_pnl_inr=Decimal("-50.00"),
        avoided_loser=True,
        rejected_winner=False,
        statutory_fees_inr=Decimal("5.00"),
        residual_friction_inr=Decimal("0.00"),
    )
    # Trade 2: Non-adverse winner (Gross +35, fees 5 -> Net +30)
    # Filter abstained (foregone winner)
    t2 = EconomicFilterTrade(
        symbol="INFY",
        decision_timestamp_ns=1_000_000_000,
        predicted_adverse_prob=0.70,
        rejection_threshold=0.60,
        abstained=True,
        notional_inr=Decimal("5000.00"),
        unfiltered_gross_pnl_inr=Decimal("35.00"),
        unfiltered_net_pnl_inr=Decimal("30.00"),
        avoided_loser=False,
        rejected_winner=True,
        statutory_fees_inr=Decimal("5.00"),
        residual_friction_inr=Decimal("0.00"),
    )
    # Trade 3: Winner executed (Gross +45, fees 5 -> Net +40)
    # Filter executed
    t3 = EconomicFilterTrade(
        symbol="TCS",
        decision_timestamp_ns=1_000_000_000,
        predicted_adverse_prob=0.30,
        rejection_threshold=0.60,
        abstained=False,
        notional_inr=Decimal("5000.00"),
        unfiltered_gross_pnl_inr=Decimal("45.00"),
        unfiltered_net_pnl_inr=Decimal("40.00"),
        avoided_loser=False,
        rejected_winner=False,
        statutory_fees_inr=Decimal("5.00"),
        residual_friction_inr=Decimal("0.00"),
    )

    comparison = evaluate_filter_economics([t1, t2, t3], max_deployed_capital_inr=Decimal("15000.00"))
    # Unfiltered net PnL: -50 + 30 + 40 = +20
    assert comparison.arm_unfiltered_net_pnl_inr == Decimal("20.00")
    # Filtered executed only t3: +40
    assert comparison.arm_filtered_net_pnl_inr == Decimal("40.00")

    # Single-counting verification:
    # Incremental net value = Filtered (+40) - Unfiltered (+20) = +20
    assert comparison.incremental_net_economic_value_inr == Decimal("20.00")
    # Avoided net losses: |-50| = 50
    assert comparison.avoided_net_loss_inr == Decimal("50.00")
    # Foregone net profits: +30
    assert comparison.foregone_net_profit_inr == Decimal("30.00")
    # Mathematical identity: Net Incremental Value == Avoided Net Losses - Foregone Net Profits
    assert comparison.incremental_net_economic_value_inr == comparison.avoided_net_loss_inr - comparison.foregone_net_profit_inr

    # Gross Equivalence identity:
    # Gross Avoided Losses: |-45| = 45; Gross Foregone Profits: 35
    assert comparison.avoided_gross_loss_inr == Decimal("45.00")
    assert comparison.foregone_gross_profit_inr == Decimal("35.00")
    assert comparison.statutory_fees_saved_inr == Decimal("10.00")
    # Gross Incremental = (45 - 35) + 10 (fees saved) = 10 + 10 = +20
    gross_equiv = (comparison.avoided_gross_loss_inr - comparison.foregone_gross_profit_inr) + comparison.statutory_fees_saved_inr
    assert comparison.incremental_net_economic_value_inr == gross_equiv

    # Counts
    assert comparison.avoided_losers_count == 1
    assert comparison.rejected_winners_count == 1
    assert comparison.capital_constrained_rejections == 0


def test_portfolio_capital_constraint_enforcement() -> None:
    # Ceiling ₹10,000 (allows 2 trades of ₹5,000 max)
    t1 = EconomicFilterTrade("RELIANCE", 1000, 0.2, 0.6, False, Decimal("5000.00"), Decimal("10"), Decimal("5"), False, False, Decimal("5"))
    t2 = EconomicFilterTrade("TCS", 1000, 0.2, 0.6, False, Decimal("5000.00"), Decimal("10"), Decimal("5"), False, False, Decimal("5"))
    t3_excess = EconomicFilterTrade("INFY", 1000, 0.2, 0.6, False, Decimal("5000.00"), Decimal("10"), Decimal("5"), False, False, Decimal("5"))

    comp = evaluate_filter_economics([t1, t2, t3_excess], max_deployed_capital_inr=Decimal("10000.00"))
    assert comp.capital_constrained_rejections == 1
    assert comp.executed_candidates == 2


def test_readiness_assessment_verdict() -> None:
    # 1. Inspected exploratory session (Nov 4) without dated MII manifest -> strictly READY TO COLLECT
    readiness = assess_repository_depth_readiness(
        live_sessions_captured=1,
        dated_mii_manifest_verified=False,
        pipeline_operational=True,
    )
    assert readiness.verdict == "READY TO COLLECT"
    assert readiness.can_execute_confirmatory_test is False
    assert "RAW_CAPTURE_ONLY" in readiness.rationale
    assert "alpha-grid-order-book-amendment-v3" in readiness.rationale

    # 2. Pipeline broken -> STOP
    readiness_stopped = assess_repository_depth_readiness(
        live_sessions_captured=1,
        dated_mii_manifest_verified=False,
        pipeline_operational=False,
    )
    assert readiness_stopped.verdict == "STOP"

    # 3. Accumulated forward sessions without enough clusters -> COLLECT MORE DATA
    readiness_collect = assess_repository_depth_readiness(
        live_sessions_captured=5,
        dated_mii_manifest_verified=True,
        pipeline_operational=True,
    )
    assert readiness_collect.verdict == "COLLECT MORE DATA"


def test_order_book_amendment_v3_specification() -> None:
    from quant.research.order_book_imbalance import ORDER_BOOK_AMENDMENT_V3
    spec = ORDER_BOOK_AMENDMENT_V3
    assert spec["schema_version"] == "alpha-grid-order-book-amendment-v3"
    assert spec["parent_protocol"] == "alpha-grid-prospective-v1"
    assert spec["verdict"] == "READY TO COLLECT"

    citations = spec["literature_citations"]
    assert len(citations) == 4
    # Check Tripathi et al. article identifier 101863
    tripathi = [c for c in citations if "Tripathi" in c["citation"]][0]
    assert tripathi["article_id"] == "101863"
    assert "101863" in tripathi["citation"]

    assert any("Gupta & Kumar" in c["citation"] and "American Business Review" in c["citation"] for c in citations)
    assert any("Jindal" in c["citation"] and "Journal of Prediction Markets" in c["citation"] for c in citations)
    assert any("Bailey" in c["citation"] and "Journal of Computational Finance" in c["citation"] for c in citations)

    assert spec["fixed_features"]["baseline_controls"] == ["return_5m_past_bps", "quoted_spread_bps", "vol_15m_trailing_bps"]
    assert spec["fixed_features"]["incremental_challenger_feature"] == "imbalance_l5"
    assert spec["target_specification"]["adverse_threshold_bps"] == -10.0
    assert spec["target_specification"]["horizon_seconds"] == 300
    assert spec["data_partitioning"]["purging_buffer_seconds"] == 300
    assert "0.0030699%" in spec["economic_accounting"]["fee_schedule"]
    assert "Segmented QJ01" in spec["journal_architecture"]["mode"]
    assert "Removes" in spec["literature_citations"][3]["usage"]


def test_provenance_and_traceability_contract() -> None:
    bids, asks = _sample_valid_book()
    receive_ns = 1793780000000000000
    raw_sha = "90abecc32ceb6ffeb3e5304b1460eeb16d057e55d42e97cd59b81c9e656b5a7e"
    feed_sess = "observe-only-dhan-quote-1793771142689960000"

    snap = FiveLevelSnapshot.validate_and_create(
        symbol="RELIANCE",
        security_id="2885",
        receive_timestamp_ns=receive_ns,
        exchange_timestamp_ns=receive_ns - 10_000_000,
        bids=bids,
        asks=asks,
        now_ns=receive_ns + 100_000_000,
        source_protocol="DhanStandardV2",
        raw_frame_sha256=raw_sha,
        journal_offset=65536,
        source_frame_id="2",
        packet_offset=0,
        raw_durable_at_ns=receive_ns + 5_000_000,
        feed_session_id=feed_sess,
    )
    assert snap.is_valid is True
    assert snap.raw_frame_sha256 == raw_sha
    assert snap.journal_offset == 65536
    assert snap.source_frame_id == "2"
    assert snap.packet_offset == 0
    assert snap.feed_session_id == feed_sess

    features = DepthFeatures.from_snapshot(snap)
    assert features is not None
    assert features.raw_frame_sha256 == raw_sha
    assert features.journal_offset == 65536
    assert features.source_frame_id == "2"
    assert features.packet_offset == 0
    assert features.feed_session_id == feed_sess


def test_outcome_tolerance_and_censoring_rules() -> None:
    decision_ns = 1_000_000_000_000
    maturity_ns = decision_ns + 300_000_000_000  # T+300s
    entry_mid = 1000_000_000_000

    # 1. Valid outcome within 5s tolerance (T + 301.5s) -> Uncensored
    outcome_valid = AdverseTargetOutcome.evaluate(
        symbol="RELIANCE",
        decision_timestamp_ns=decision_ns,
        entry_mid_nanos=entry_mid,
        exit_mid_nanos=998_000_000_000,
        observed_at_ns=maturity_ns + 10_000_000_000,
        exit_timestamp_ns=maturity_ns + 1_500_000_000,  # +1.5s, within 5.0s tolerance
    )
    assert outcome_valid.is_censored is False
    assert outcome_valid.is_adverse == 1  # -20 bps <= -10 bps

    # 2. Quote before maturity (T + 299s) -> Censored
    outcome_early = AdverseTargetOutcome.evaluate(
        symbol="RELIANCE",
        decision_timestamp_ns=decision_ns,
        entry_mid_nanos=entry_mid,
        exit_mid_nanos=998_000_000_000,
        observed_at_ns=maturity_ns + 10_000_000_000,
        exit_timestamp_ns=maturity_ns - 1_000_000_000,
    )
    assert outcome_early.is_censored is True
    assert outcome_early.censoring_reason == "CENSORED_OUTCOME_BEFORE_MATURITY"

    # 3. Quote past tolerance window (T + 306s, > 5.0s tolerance) -> Censored
    outcome_late = AdverseTargetOutcome.evaluate(
        symbol="RELIANCE",
        decision_timestamp_ns=decision_ns,
        entry_mid_nanos=entry_mid,
        exit_mid_nanos=998_000_000_000,
        observed_at_ns=maturity_ns + 10_000_000_000,
        exit_timestamp_ns=maturity_ns + 6_000_000_000,  # 6s late
    )
    assert outcome_late.is_censored is True
    assert outcome_late.censoring_reason == "CENSORED_TOLERANCE_EXCEEDED"

    # 4. Session close boundary (maturity falls after session end) -> Censored
    outcome_boundary = AdverseTargetOutcome.evaluate(
        symbol="RELIANCE",
        decision_timestamp_ns=decision_ns,
        entry_mid_nanos=entry_mid,
        exit_mid_nanos=998_000_000_000,
        observed_at_ns=maturity_ns + 10_000_000_000,
        exit_timestamp_ns=maturity_ns + 1_000_000_000,
        session_end_ns=maturity_ns - 1_000_000_000,  # session ended before maturity
    )
    assert outcome_boundary.is_censored is True
    assert outcome_boundary.censoring_reason == "CENSORED_SESSION_BOUNDARY"

    # 5. Feed discontinuity detected during window -> Censored
    outcome_gap = AdverseTargetOutcome.evaluate(
        symbol="RELIANCE",
        decision_timestamp_ns=decision_ns,
        entry_mid_nanos=entry_mid,
        exit_mid_nanos=998_000_000_000,
        observed_at_ns=maturity_ns + 10_000_000_000,
        exit_timestamp_ns=maturity_ns + 1_000_000_000,
        discontinuity_detected=True,
    )
    assert outcome_gap.is_censored is True
    assert outcome_gap.censoring_reason == "CENSORED_FEED_DISCONTINUITY"


def test_locate_outcome_quote_utility(tmp_path: Path) -> None:
    from quant.research.order_book_imbalance import locate_outcome_quote
    decision_ns = 1_000_000_000_000
    maturity_ns = decision_ns + 300_000_000_000
    journal = tmp_path / "raw.qj"
    decision, q_outcome = _qj_full_pair(journal, decision_ns, maturity_ns + 2_000_000_000)

    # Candidate subsequent quotes
    q_early = FiveLevelSnapshot.validate_and_create(
        "RELIANCE", "2885", decision_ns + 200_000_000_000, None, decision.bids, decision.asks,
        raw_frame_sha256="c" * 64, feed_session_id="session_1",
    )
    event_log = [{"feed_session_id": "session_1", "event": {
        "timestamp_ns": decision_ns, "detail": "FIRST_BINARY_FRAME_DURABLE"}}]

    outcome = locate_outcome_quote(
        decision=decision,
        subsequent_quotes=[q_early, q_outcome],
        as_of_ns=maturity_ns + 5_000_000_000,
        event_log=event_log,
        journal=journal,
    )
    assert outcome.is_censored is False
    assert outcome.exit_timestamp_ns == maturity_ns + 2_000_000_000
    assert outcome.exit_raw_frame_sha256 == q_outcome.raw_frame_sha256


def test_active_friction_sensitivity_across_scenarios() -> None:
    from quant.research.order_book_imbalance import evaluate_friction_sensitivities
    # 2 trades:
    # t1: Abstained loser (notional ₹15k, gross -₹50, fees ₹15.74)
    # t2: Executed winner (notional ₹15k, gross +₹80, fees ₹15.74)
    t1 = EconomicFilterTrade(
        symbol="RELIANCE",
        decision_timestamp_ns=1000,
        predicted_adverse_prob=0.8,
        rejection_threshold=0.6,
        abstained=True,
        notional_inr=Decimal("15000.00"),
        unfiltered_gross_pnl_inr=Decimal("-50.00"),
        unfiltered_net_pnl_inr=Decimal("-65.74"),
        avoided_loser=True,
        rejected_winner=False,
        statutory_fees_inr=Decimal("15.74"),
        residual_friction_inr=Decimal("0.00"),
    )
    t2 = EconomicFilterTrade(
        symbol="TCS",
        decision_timestamp_ns=2000,
        predicted_adverse_prob=0.2,
        rejection_threshold=0.6,
        abstained=False,
        notional_inr=Decimal("15000.00"),
        unfiltered_gross_pnl_inr=Decimal("80.00"),
        unfiltered_net_pnl_inr=Decimal("64.26"),
        avoided_loser=False,
        rejected_winner=False,
        statutory_fees_inr=Decimal("15.74"),
        residual_friction_inr=Decimal("0.00"),
    )

    sens = evaluate_friction_sensitivities(
        [t1, t2],
        friction_scenarios=(Decimal(0), Decimal(5), Decimal(10), Decimal(20), Decimal(40)),
        max_deployed_capital_inr=Decimal("30000.00"),
        max_instrument_notional_inr=Decimal("15000.00"),
        max_decision_loss_inr=Decimal("1000.00"),
        max_session_loss_inr=Decimal("2000.00"),
    )

    # 1. Net PnL must strictly decrease as friction increases for unfiltered arm
    pnl_0 = sens[Decimal(0)].arm_unfiltered_net_pnl_inr
    pnl_5 = sens[Decimal(5)].arm_unfiltered_net_pnl_inr
    pnl_10 = sens[Decimal(10)].arm_unfiltered_net_pnl_inr
    pnl_20 = sens[Decimal(20)].arm_unfiltered_net_pnl_inr
    pnl_40 = sens[Decimal(40)].arm_unfiltered_net_pnl_inr

    assert pnl_0 > pnl_5 > pnl_10 > pnl_20 > pnl_40

    # For 15k notional:
    # 5 bps = ₹7.50 friction per trade
    # 10 bps = ₹15.00 friction per trade
    # 20 bps = ₹30.00 friction per trade
    # 40 bps = ₹60.00 friction per trade
    # For 2 trades, unfiltered PnL drops by ₹15 for each 5 bps
    assert pnl_0 - pnl_5 == Decimal("15.00")  # 2 trades * 7.50
    assert pnl_0 - pnl_10 == Decimal("30.00") # 2 trades * 15.00
    assert pnl_0 - pnl_40 == Decimal("120.00") # 2 trades * 60.00

    # 2. Avoided net loss strictly INCREASES with friction (abstaining saved friction on the loser)
    loss_saved_0 = sens[Decimal(0)].avoided_net_loss_inr
    loss_saved_40 = sens[Decimal(40)].avoided_net_loss_inr
    assert loss_saved_40 - loss_saved_0 == Decimal("60.00")  # ₹60 friction saved

    # 3. Every scenario satisfies mathematical single-counting identity
    for f_bps, comp in sens.items():
        assert comp.incremental_net_economic_value_inr == comp.avoided_net_loss_inr - comp.foregone_net_profit_inr
        gross_id = (comp.avoided_gross_loss_inr - comp.foregone_gross_profit_inr) + comp.statutory_fees_saved_inr + comp.residual_friction_saved_inr
        assert comp.incremental_net_economic_value_inr == gross_id


def test_reused_walkforward_folds() -> None:
    from quant.research.order_book_imbalance import build_walkforward_folds_from_observations
    DAY_NS = 86_400 * 1_000_000_000
    # 4 distinct daily trading sessions separated by 24h
    obs = [
        ("sess_1", 1 * DAY_NS + 1000, 1),
        ("sess_1", 1 * DAY_NS + 2000, 2),
        ("sess_2", 2 * DAY_NS + 1000, 3),
        ("sess_2", 2 * DAY_NS + 2000, 4),
        ("sess_3", 3 * DAY_NS + 1000, 5),
        ("sess_3", 3 * DAY_NS + 2000, 6),
        ("sess_4", 4 * DAY_NS + 1000, 7),
        ("sess_4", 4 * DAY_NS + 2000, 8),
    ]

    folds = build_walkforward_folds_from_observations(
        obs,
        train_sessions=2,
        validation_sessions=1,
        test_sessions=1,
        mode="expanding",
    )
    assert len(folds) == 1
    f = folds[0]
    # Train has sess_1 and sess_2
    assert {r.session_id for r in f.train} == {"sess_1", "sess_2"}
    # Validation has sess_3
    assert {r.session_id for r in f.validation} == {"sess_3"}
    # Test has sess_4
    assert {r.session_id for r in f.test} == {"sess_4"}


def test_order_book_amendment_v4_specification() -> None:
    from quant.research.order_book_imbalance import ORDER_BOOK_AMENDMENT_V4
    spec = ORDER_BOOK_AMENDMENT_V4
    assert spec["schema_version"] == "alpha-grid-order-book-amendment-v4"
    assert spec["parent_protocol"] == "alpha-grid-order-book-amendment-v3"
    assert spec["research_status"] == "RESEARCH_SPECIFICATION_UNVALIDATED"
    assert spec["target_specification"]["outcome_tolerance_seconds"] == 5
    assert spec["economic_accounting"]["active_sensitivity"] is True
    assert spec["provenance_and_causality"]["quote_depth_separation"] != ""
    assert "RAW_CAPTURE_ONLY" in spec["november_4_treatment"]


def test_genuine_november_4_pipeline_trace() -> None:
    """End-to-end pipeline integrity test on genuine QJ01 records from Nov 4."""
    from quant.research.order_book_imbalance import _qj_source_matches
    from pathlib import Path
    qj_path = Path("/Users/dhaneshramesh/Library/Application Support/AlphaGrid/offline-dev/raw-standard-2026-11-04.qj")
    if not qj_path.exists():
        pytest.skip("local raw QJ01 journal absent")

    import struct
    def read_varint(b, offset):
        val = 0
        shift = 0
        while True:
            byte = b[offset]
            offset += 1
            val |= (byte & 0x7F) << shift
            if (byte & 0x80) == 0:
                break
            shift += 7
        return val, offset

    # Locate Record 76877 (decision quote for RELIANCE at T)
    # and Record 85155 (outcome quote for RELIANCE at T+300s)
    with qj_path.open("rb") as f:
        record_idx = 0
        decision_record = None
        outcome_record = None
        while True:
            record_idx += 1
            record_offset = f.tell()
            header = f.read(12)
            if not header:
                break
            magic, length, crc = struct.unpack("<4sII", header)
            payload = f.read(length)
            if record_idx in (76877, 85155):
                assert magic == b"QJ01" and zlib.crc32(payload) == crc
                off = 0
                sess_len, off = read_varint(payload, off)
                off += sess_len
                event_disc, off = read_varint(payload, off)
                feed_sess_len, off = read_varint(payload, off)
                feed_sess = payload[off:off+feed_sess_len].decode()
                off += feed_sess_len
                frame_id, off = read_varint(payload, off)
                zz, off = read_varint(payload, off)
                ts_ns = (zz >> 1) ^ (-(zz & 1))
                sha256 = payload[off:off+32].hex()
                off += 32
                frame_bytes_len, off = read_varint(payload, off)
                frame_bytes = payload[off:off+frame_bytes_len]
                assert hashlib.sha256(frame_bytes).hexdigest() == sha256

                if record_idx == 76877:
                    decision_record = (frame_id, feed_sess, ts_ns, sha256, frame_bytes, record_offset)
                else:
                    outcome_record = (frame_id, feed_sess, ts_ns, sha256, frame_bytes, record_offset)
            if decision_record and outcome_record:
                break

    assert decision_record is not None
    assert outcome_record is not None

    # Step 1: Decision feature at T
    frame_id, feed_sess, t_decision_ns, sha_dec, bytes_dec, decision_offset = decision_record
    assert sha_dec == "90abecc32ceb6ffeb3e5304b1460eeb16d057e55d42e97cd59b81c9e656b5a7e"
    bids = []
    asks = []
    for i in range(5):
        at = 62 + 20 * i
        b_qty = struct.unpack_from("<I", bytes_dec, at)[0]
        b_ord = struct.unpack_from("<H", bytes_dec, at + 8)[0]
        b_p = struct.unpack_from("<f", bytes_dec, at + 12)[0]
        a_qty = struct.unpack_from("<I", bytes_dec, at + 4)[0]
        a_ord = struct.unpack_from("<H", bytes_dec, at + 10)[0]
        a_p = struct.unpack_from("<f", bytes_dec, at + 16)[0]
        bids.append(BookLevel(int(round(b_p * 1e9)), b_qty, b_ord))
        asks.append(BookLevel(int(round(a_p * 1e9)), a_qty, a_ord))

    snap = FiveLevelSnapshot.validate_and_create(
        symbol="RELIANCE",
        security_id="2885",
        receive_timestamp_ns=t_decision_ns,
        exchange_timestamp_ns=None,
        bids=tuple(bids),
        asks=tuple(asks),
        raw_frame_sha256=sha_dec,
        feed_session_id=feed_sess,
    )
    assert snap.is_valid is True
    feat = DepthFeatures.from_snapshot(snap)
    assert feat is not None
    assert feat.imbalance_l5 > 0.40  # OIB5 ≈ +0.4110
    assert feat.raw_frame_sha256 == sha_dec
    assert feat.feed_session_id == feed_sess
    # The test binds bytes and offsets; genuine fsync/availability time is not
    # recoverable from this QJ record, so this does not establish causal eligibility.
    source_bound = replace(snap, journal_offset=decision_offset,
                           source_frame_id=str(frame_id), packet_offset=0,
                           raw_durable_at_ns=t_decision_ns)
    assert _qj_source_matches(source_bound, qj_path, t_decision_ns)

    # Step 2: Outcome at T+300s
    exit_frame_id, exit_feed_sess, t_outcome_ns, sha_out, bytes_out, outcome_offset = outcome_record
    assert sha_out == "511c6168a8bca2c2e0e625507c9220617740993921ae8b228236a5c593aa4a8a"
    delay_s = (t_outcome_ns - (t_decision_ns + 300_000_000_000)) / 1e9
    assert 0.0 <= delay_s <= 5.0  # Exactly 0.511s within tolerance
    exit_bids = tuple(BookLevel(math.floor(struct.unpack_from("<f", bytes_out, 62 + 20 * i + 12)[0] * 1e9 + 0.5),
                                struct.unpack_from("<I", bytes_out, 62 + 20 * i)[0],
                                struct.unpack_from("<H", bytes_out, 62 + 20 * i + 8)[0]) for i in range(5))
    exit_asks = tuple(BookLevel(math.floor(struct.unpack_from("<f", bytes_out, 62 + 20 * i + 16)[0] * 1e9 + 0.5),
                                struct.unpack_from("<I", bytes_out, 62 + 20 * i + 4)[0],
                                struct.unpack_from("<H", bytes_out, 62 + 20 * i + 10)[0]) for i in range(5))
    exit_source_bound = FiveLevelSnapshot.validate_and_create(
        "RELIANCE", "2885", t_outcome_ns, None, exit_bids, exit_asks,
        raw_frame_sha256=sha_out, feed_session_id=exit_feed_sess,
        journal_offset=outcome_offset, source_frame_id=str(exit_frame_id),
        packet_offset=0, raw_durable_at_ns=t_outcome_ns,
    )
    assert _qj_source_matches(exit_source_bound, qj_path, t_outcome_ns)

    b_exit_p = struct.unpack_from("<f", bytes_out, 62 + 12)[0]
    a_exit_p = struct.unpack_from("<f", bytes_out, 62 + 16)[0]
    exit_mid_nanos = int(round(((b_exit_p + a_exit_p) / 2.0) * 1e9))

    outcome = AdverseTargetOutcome.evaluate(
        symbol="RELIANCE",
        decision_timestamp_ns=t_decision_ns,
        entry_mid_nanos=feat.mid_quote_nanos,
        exit_mid_nanos=exit_mid_nanos,
        observed_at_ns=t_outcome_ns + 1_000_000_000,
        exit_timestamp_ns=t_outcome_ns,
        entry_raw_frame_sha256=sha_dec,
        exit_raw_frame_sha256=sha_out,
        feed_session_id=feed_sess,
    )
    assert outcome.is_censored is False
    assert outcome.is_adverse == 0  # -2.3 bps, not adverse
    assert outcome.entry_raw_frame_sha256 == sha_dec
    assert outcome.exit_raw_frame_sha256 == sha_out

    # Step 3: Eligibility Gate
    # Session 2026-11-04 is missing dated MII manifest and was inspected in forensic audit
    readiness = assess_repository_depth_readiness(
        live_sessions_captured=1,
        dated_mii_manifest_verified=False,
    )
    assert readiness.can_execute_confirmatory_test is False
    assert readiness.verdict == "READY TO COLLECT"
    assert "RAW_CAPTURE_ONLY" in readiness.rationale


def test_codex_regression_cross_session_rejection() -> None:
    """Codex finding: subsequent quote from a different feed session must be rejected."""
    t0 = 1_000_000_000_000
    bids = ((1000_000_000_000, 100), (999_000_000_000, 100), (998_000_000_000, 100), (997_000_000_000, 100), (996_000_000_000, 100))
    asks = ((1001_000_000_000, 100), (1002_000_000_000, 100), (1003_000_000_000, 100), (1004_000_000_000, 100), (1005_000_000_000, 100))

    decision = FiveLevelSnapshot(
        symbol="RELIANCE", security_id="2885", receive_timestamp_ns=t0,
        exchange_timestamp_ns=None, bids=bids, asks=asks,
        feed_session_id="feed_session_AAA", raw_frame_sha256="aa" * 32,
    )

    # Subsequent quote arrived at T+301s, but belongs to a different feed session "feed_session_BBB"
    t_exit = t0 + 301_000_000_000
    cross_session_quote = FiveLevelSnapshot(
        symbol="RELIANCE", security_id="2885", receive_timestamp_ns=t_exit,
        exchange_timestamp_ns=None, bids=bids, asks=asks,
        feed_session_id="feed_session_BBB", raw_frame_sha256="bb" * 32,
    )

    as_of = t0 + 302_000_000_000
    outcome = locate_outcome_quote(decision, [cross_session_quote], as_of_ns=as_of)
    assert outcome.is_censored is True
    # The cross-session quote is excluded or detected as discontinuity
    assert outcome.censoring_reason in {"CENSORED_OUTCOME_NOT_YET_AVAILABLE", "CENSORED_FEED_DISCONTINUITY", "CENSORED_CROSS_SESSION_QUOTE"}


def test_codex_regression_future_availability_censoring(tmp_path: Path) -> None:
    """Codex finding: outcome quote received after scoring as-of must not be evaluated ahead of arrival."""
    t0 = 1_000_000_000_000
    journal = tmp_path / "raw.qj"
    t_exit = t0 + 302_000_000_000
    decision, future_quote = _qj_full_pair(journal, t0, t_exit)

    # 1. Scoring as-of is T+300.5s: the quote at T+302s has NOT arrived yet!
    as_of_early = t0 + 300_500_000_000
    event_log = [{"feed_session_id": "session_1", "event": {
        "timestamp_ns": t0, "detail": "FIRST_BINARY_FRAME_DURABLE"}}]
    outcome_early = locate_outcome_quote(decision, [future_quote], as_of_ns=as_of_early,
                                         event_log=event_log, journal=journal)
    assert outcome_early.is_censored is True
    assert outcome_early.censoring_reason == "CENSORED_OUTCOME_NOT_YET_AVAILABLE"

    # 2. Scoring as-of reaches T+302.5s: the quote at T+302s is now causally available
    as_of_mature = t0 + 302_500_000_000
    outcome_mature = locate_outcome_quote(decision, [future_quote], as_of_ns=as_of_mature,
                                          event_log=event_log, journal=journal)
    assert outcome_mature.is_censored is False
    assert outcome_mature.exit_timestamp_ns == t_exit
    assert outcome_mature.exit_raw_frame_sha256 == future_quote.raw_frame_sha256


def test_outcome_source_binding_rejects_forged_offset_hash_and_book(tmp_path: Path) -> None:
    t0 = 1_000_000_000_000
    journal = tmp_path / "raw.qj"
    decision, exit_quote = _qj_full_pair(journal, t0, t0 + 301_000_000_000)
    events = [{"feed_session_id": "session_1", "event": {
        "timestamp_ns": t0, "detail": "FIRST_BINARY_FRAME_DURABLE"}}]
    kwargs = {"as_of_ns": t0 + 302_000_000_000, "event_log": events, "journal": journal}
    assert not locate_outcome_quote(decision, [exit_quote], **kwargs).is_censored
    for forged in (
        replace(exit_quote, journal_offset=0),
        replace(exit_quote, raw_frame_sha256="f" * 64),
        replace(exit_quote, bids=(replace(exit_quote.bids[0], displayed_quantity=999),
                                  *exit_quote.bids[1:])),
    ):
        result = locate_outcome_quote(decision, [forged], **kwargs)
        assert result.is_censored
        assert result.censoring_reason == "CENSORED_SOURCE_UNVERIFIED"


def test_outcome_requires_matching_lifecycle_and_rejects_nested_stop(tmp_path: Path) -> None:
    t0 = 1_000_000_000_000
    journal = tmp_path / "raw.qj"
    decision, exit_quote = _qj_full_pair(journal, t0, t0 + 301_000_000_000)
    log = tmp_path / "capture-events.jsonl"
    log.write_text(json.dumps({"feed_session_id": "another-session", "event": {
        "timestamp_ns": t0, "detail": "FIRST_BINARY_FRAME_DURABLE"}}) + "\n")
    kwargs = {"as_of_ns": t0 + 302_000_000_000, "event_log": log, "journal": journal}
    result = locate_outcome_quote(decision, [exit_quote], **kwargs)
    assert result.censoring_reason == "CENSORED_CONTINUITY_UNVERIFIED"
    with log.open("w") as stream:
        for detail, stamp in (("FIRST_BINARY_FRAME_DURABLE", t0),
                              ("STOPPED: SOCKET_CLOSED", t0 + 150_000_000_000)):
            stream.write(json.dumps({"feed_session_id": "session_1", "event": {
                "timestamp_ns": stamp, "detail": detail}}) + "\n")
    result = locate_outcome_quote(decision, [exit_quote], **kwargs)
    assert result.censoring_reason == "CENSORED_FEED_DISCONTINUITY"


def test_feed_discontinuity_detection_from_event_log(tmp_path: Path) -> None:
    """Continuity is derived from capture event logs, not blindly trusted from caller."""
    t0 = 1_000_000_000_000
    bids = ((1000_000_000_000, 100), (999_000_000_000, 100), (998_000_000_000, 100), (997_000_000_000, 100), (996_000_000_000, 100))
    asks = ((1001_000_000_000, 100), (1002_000_000_000, 100), (1003_000_000_000, 100), (1004_000_000_000, 100), (1005_000_000_000, 100))

    decision = FiveLevelSnapshot(
        symbol="RELIANCE", security_id="2885", receive_timestamp_ns=t0,
        exchange_timestamp_ns=None, bids=bids, asks=asks,
        feed_session_id="session_1", raw_frame_sha256="aa" * 32,
    )

    quote_exit = FiveLevelSnapshot(
        symbol="RELIANCE", security_id="2885", receive_timestamp_ns=t0 + 301_000_000_000,
        exchange_timestamp_ns=None, bids=bids, asks=asks,
        feed_session_id="session_1", raw_frame_sha256="bb" * 32,
    )

    event_log = tmp_path / "raw-capture-events.jsonl"
    event_log.write_text(
        json.dumps({
            "schema_version": "alpha-grid-capture-event-v1",
            "timestamp_ns": t0 + 150_000_000_000,
            "event": {"detail": "FEED_DISCONNECTED: SOCKET_TIMEOUT"},
            "feed_session_id": "session_1",
        }) + "\n"
    )

    outcome = locate_outcome_quote(decision, [quote_exit], as_of_ns=t0 + 302_000_000_000, event_log=event_log)
    assert outcome.is_censored is True
    assert outcome.censoring_reason == "CENSORED_FEED_DISCONTINUITY"


def test_codex_regression_dynamic_capital_release_and_redeployment() -> None:
    """Codex finding: Capital is released on position exit, and filter abstention allows capital redeployment."""
    # Ceiling ₹15,000
    ceiling = Decimal("15000.00")
    t0 = 1_000_000_000_000  # T = 0
    t_mid = t0 + 100_000_000_000  # T = 100s
    t_after = t0 + 350_000_000_000  # T = 350s (after 300s holding period)

    # Trade 1: T=0, notional ₹10,000, adverse loser (-₹100). Filter abstains.
    t1 = EconomicFilterTrade(
        symbol="RELIANCE", decision_timestamp_ns=t0, exit_timestamp_ns=t0 + 300_000_000_000,
        predicted_adverse_prob=0.85, rejection_threshold=0.60, abstained=True,
        notional_inr=Decimal("10000.00"), unfiltered_gross_pnl_inr=Decimal("-95.00"),
        unfiltered_net_pnl_inr=Decimal("-100.00"), avoided_loser=True, rejected_winner=False,
        statutory_fees_inr=Decimal("5.00"),
    )

    # Trade 2: T=0, notional ₹5,000, neutral trade (Net 0). Both accept.
    t2 = EconomicFilterTrade(
        symbol="TCS", decision_timestamp_ns=t0, exit_timestamp_ns=t0 + 300_000_000_000,
        predicted_adverse_prob=0.20, rejection_threshold=0.60, abstained=False,
        notional_inr=Decimal("5000.00"), unfiltered_gross_pnl_inr=Decimal("5.00"),
        unfiltered_net_pnl_inr=Decimal("0.00"), avoided_loser=False, rejected_winner=False,
        statutory_fees_inr=Decimal("5.00"),
    )

    # Trade 3: Arrives at T=100s, notional ₹10,000, attractive winner (+₹150).
    # Unfiltered arm has ₹10,000 (t1) + ₹5,000 (t2) = ₹15,000 deployed -> REJECTS due to capital!
    # Filtered arm abstained from t1, so only has ₹5,000 (t2) deployed -> ACCEPTS t3 (redeployed)!
    t3 = EconomicFilterTrade(
        symbol="INFY", decision_timestamp_ns=t_mid, exit_timestamp_ns=t_mid + 300_000_000_000,
        predicted_adverse_prob=0.15, rejection_threshold=0.60, abstained=False,
        notional_inr=Decimal("10000.00"), unfiltered_gross_pnl_inr=Decimal("155.00"),
        unfiltered_net_pnl_inr=Decimal("150.00"), avoided_loser=False, rejected_winner=False,
        statutory_fees_inr=Decimal("5.00"),
    )

    # Trade 4: Arrives at T=350s (after t1 and t2 have exited at T=300s). Notional ₹10,000, winner (+₹50).
    # Because t1/t2 exited, capital was released. Unfiltered arm CAN execute t4.
    t4 = EconomicFilterTrade(
        symbol="HDFCBANK", decision_timestamp_ns=t_after, exit_timestamp_ns=t_after + 300_000_000_000,
        predicted_adverse_prob=0.25, rejection_threshold=0.60, abstained=False,
        notional_inr=Decimal("10000.00"), unfiltered_gross_pnl_inr=Decimal("55.00"),
        unfiltered_net_pnl_inr=Decimal("50.00"), avoided_loser=False, rejected_winner=False,
        statutory_fees_inr=Decimal("5.00"),
    )

    comp = evaluate_filter_economics(
        [t1, t2, t3, t4],
        max_deployed_capital_inr=ceiling,
        max_instrument_notional_inr=Decimal("10000.00"),
        max_decision_loss_inr=Decimal("1000.00"),
        max_session_loss_inr=Decimal("2000.00"),
    )

    # Unfiltered arm took: t1 (-100), t2 (0), rejected t3 (capital), took t4 (+50) -> Net PnL = -100 + 0 + 50 = -50
    assert comp.arm_unfiltered_net_pnl_inr == Decimal("-50.00")
    assert comp.unfiltered_capital_rejections == 1

    # Filtered arm took: abstained t1, took t2 (0), took t3 (+150 redeployed), rejected t4 (because t3 is still active at 350s: exits at 400s)
    # -> Net PnL = 0 + 150 = +150
    assert comp.arm_filtered_net_pnl_inr == Decimal("150.00")
    assert comp.redeployed_trades_count == 1
    assert comp.redeployed_net_pnl_inr == Decimal("150.00")

    # Incremental net economic value = Filtered (+150) - Unfiltered (-50) = +200
    assert comp.incremental_net_economic_value_inr == Decimal("200.00")
    # Avoided net losses from t1: 100
    assert comp.avoided_net_loss_inr == Decimal("100.00")
    # Foregone net profit: 0
    assert comp.foregone_net_profit_inr == Decimal("0.00")
    # Full accounting identity: Incremental Value = (Avoided - Foregone) + Redeployed PnL
    # 200 == (100 - 0) + 150 - 50 (from t4 missed by filtered) = 200!
    assert comp.incremental_net_economic_value_inr == comp.arm_filtered_net_pnl_inr - comp.arm_unfiltered_net_pnl_inr


def test_fee_separation_brokerage_vs_statutory() -> None:
    """Verify distinct tracking of broker commissions and statutory charges."""
    t = EconomicFilterTrade(
        symbol="RELIANCE", decision_timestamp_ns=1000,
        predicted_adverse_prob=0.8, rejection_threshold=0.6, abstained=True,
        notional_inr=Decimal("10000.00"), unfiltered_gross_pnl_inr=Decimal("-90.00"),
        unfiltered_net_pnl_inr=Decimal("-100.00"), avoided_loser=True, rejected_winner=False,
        statutory_fees_inr=Decimal("10.00"),
        brokerage_charges_inr=Decimal("3.00"),
        statutory_charges_inr=Decimal("7.00"),
    )
    assert t.brokerage_charges_inr == Decimal("3.00")
    assert t.statutory_charges_inr == Decimal("7.00")
    assert t.brokerage_charges_inr + t.statutory_charges_inr == Decimal("10.00")


def test_codex_counterexample_missing_event_log_censored() -> None:
    """Codex finding: missing event log without dense quotes must NOT be assumed continuous."""
    t0 = 1_000_000_000_000
    bids, asks = _sample_valid_book()
    decision = FiveLevelSnapshot.validate_and_create(
        "RELIANCE", "2885", t0, None, bids, asks,
        raw_frame_sha256="aa" * 32, feed_session_id="session_1",
    )
    # Only single quote at T+301s, no intermediate dense quotes
    outcome_quote = FiveLevelSnapshot.validate_and_create(
        "RELIANCE", "2885", t0 + 301_000_000_000, None, bids, asks,
        raw_frame_sha256="bb" * 32, feed_session_id="session_1",
    )

    # Calling with event_log=None
    outcome = locate_outcome_quote(
        decision=decision,
        subsequent_quotes=[outcome_quote],
        as_of_ns=t0 + 305_000_000_000,
        event_log=None,
    )
    assert outcome.is_censored is True
    assert outcome.censoring_reason == "CENSORED_CONTINUITY_UNVERIFIED"


def test_codex_counterexample_empty_event_log_censored() -> None:
    """Codex finding: empty event log without dense quotes must NOT be assumed continuous."""
    t0 = 1_000_000_000_000
    bids, asks = _sample_valid_book()
    decision = FiveLevelSnapshot.validate_and_create(
        "RELIANCE", "2885", t0, None, bids, asks,
        raw_frame_sha256="aa" * 32, feed_session_id="session_1",
    )
    outcome_quote = FiveLevelSnapshot.validate_and_create(
        "RELIANCE", "2885", t0 + 301_000_000_000, None, bids, asks,
        raw_frame_sha256="bb" * 32, feed_session_id="session_1",
    )

    # Calling with empty event_log=[]
    outcome = locate_outcome_quote(
        decision=decision,
        subsequent_quotes=[outcome_quote],
        as_of_ns=t0 + 305_000_000_000,
        event_log=[],
    )
    assert outcome.is_censored is True
    assert outcome.censoring_reason == "CENSORED_CONTINUITY_UNVERIFIED"


def test_codex_counterexample_missing_provenance_censored() -> None:
    """Codex finding: observations missing 64-hex raw_frame_sha256 must be censored as missing provenance."""
    t0 = 1_000_000_000_000
    bids, asks = _sample_valid_book()

    # 1. Decision lacks raw_frame_sha256
    decision_no_hash = FiveLevelSnapshot.validate_and_create(
        "RELIANCE", "2885", t0, None, bids, asks,
        raw_frame_sha256=None, feed_session_id="session_1",
    )
    outcome_quote = FiveLevelSnapshot.validate_and_create(
        "RELIANCE", "2885", t0 + 301_000_000_000, None, bids, asks,
        raw_frame_sha256="bb" * 32, feed_session_id="session_1",
    )
    event_log = [{"feed_session_id": "session_1", "event": {
        "timestamp_ns": t0, "detail": "FIRST_BINARY_FRAME_DURABLE"}}]

    outcome1 = locate_outcome_quote(
        decision=decision_no_hash,
        subsequent_quotes=[outcome_quote],
        as_of_ns=t0 + 305_000_000_000,
        event_log=event_log,
    )
    assert outcome1.is_censored is True
    assert outcome1.censoring_reason == "CENSORED_PROVENANCE_MISSING"

    # 2. Outcome quote lacks raw_frame_sha256
    decision_ok = FiveLevelSnapshot.validate_and_create(
        "RELIANCE", "2885", t0, None, bids, asks,
        raw_frame_sha256="aa" * 32, feed_session_id="session_1",
    )
    outcome_no_hash = FiveLevelSnapshot.validate_and_create(
        "RELIANCE", "2885", t0 + 301_000_000_000, None, bids, asks,
        raw_frame_sha256=None, feed_session_id="session_1",
    )
    outcome2 = locate_outcome_quote(
        decision=decision_ok,
        subsequent_quotes=[outcome_no_hash],
        as_of_ns=t0 + 305_000_000_000,
        event_log=event_log,
    )
    assert outcome2.is_censored is True
    assert outcome2.censoring_reason == "CENSORED_PROVENANCE_MISSING"


def test_codex_counterexample_four_concurrent_positions_rejected() -> None:
    """Codex finding: 4 simultaneous trades of ₹1,000 must reject the 4th trade due to 3-position cap."""
    t0 = 1_000_000_000_000
    trades = [
        EconomicFilterTrade(
            symbol=f"SYM_{i}", decision_timestamp_ns=t0,
            predicted_adverse_prob=0.1, rejection_threshold=0.6, abstained=False,
            notional_inr=Decimal("1000.00"), unfiltered_gross_pnl_inr=Decimal("10.00"),
            unfiltered_net_pnl_inr=Decimal("8.00"), avoided_loser=False, rejected_winner=False,
            statutory_fees_inr=Decimal("2.00"),
        )
        for i in range(4)
    ]

    # Evaluate with default risk limits: max_concurrent_positions=3, max_deployed_capital_inr=15,000
    comp = evaluate_filter_economics(trades)

    # 4th trade must be rejected because max concurrent positions is 3!
    assert comp.total_candidates == 4
    assert comp.unfiltered_executed_count == 3
    assert comp.unfiltered_position_limit_rejections == 1
    assert comp.filtered_executed_count == 3
    assert comp.filtered_position_limit_rejections == 1


def test_portfolio_risk_limits_notional_and_session_loss() -> None:
    """Verify independent enforcement of per-instrument notional limit (₹5,000) and session loss limit (₹500)."""
    t0 = 1_000_000_000_000
    # 1. Trade exceeding ₹5,000 limit (₹6,000)
    t_oversized = EconomicFilterTrade(
        symbol="RELIANCE", decision_timestamp_ns=t0,
        predicted_adverse_prob=0.1, rejection_threshold=0.6, abstained=False,
        notional_inr=Decimal("6000.00"), unfiltered_gross_pnl_inr=Decimal("50.00"),
        unfiltered_net_pnl_inr=Decimal("45.00"), avoided_loser=False, rejected_winner=False,
        statutory_fees_inr=Decimal("5.00"),
    )
    comp_notional = evaluate_filter_economics([t_oversized])
    assert comp_notional.unfiltered_notional_limit_rejections == 1
    assert comp_notional.unfiltered_executed_count == 0

    # 2. Cumulative session loss hitting ₹500 halts further entries
    t_loss = EconomicFilterTrade(
        symbol="TCS", decision_timestamp_ns=t0, exit_timestamp_ns=t0 + 100,
        predicted_adverse_prob=0.1, rejection_threshold=0.6, abstained=False,
        notional_inr=Decimal("5000.00"), unfiltered_gross_pnl_inr=Decimal("-510.00"),
        unfiltered_net_pnl_inr=Decimal("-515.00"), avoided_loser=False, rejected_winner=False,
        statutory_fees_inr=Decimal("5.00"),
    )
    t_subsequent = EconomicFilterTrade(
        symbol="INFY", decision_timestamp_ns=t0 + 200,
        predicted_adverse_prob=0.1, rejection_threshold=0.6, abstained=False,
        notional_inr=Decimal("1000.00"), unfiltered_gross_pnl_inr=Decimal("20.00"),
        unfiltered_net_pnl_inr=Decimal("15.00"), avoided_loser=False, rejected_winner=False,
        statutory_fees_inr=Decimal("5.00"),
    )
    comp_loss = evaluate_filter_economics([t_loss, t_subsequent])
    assert comp_loss.unfiltered_executed_count == 1
    assert comp_loss.unfiltered_session_loss_rejections == 1


def test_economics_recognizes_loss_only_after_exit_and_resets_next_session() -> None:
    t0 = 1_000_000_000_000
    def trade(symbol: str, at: int, exit_at: int, net: str) -> EconomicFilterTrade:
        return EconomicFilterTrade(
            symbol=symbol, decision_timestamp_ns=at, exit_timestamp_ns=exit_at,
            predicted_adverse_prob=0.1, rejection_threshold=0.6, abstained=False,
            notional_inr=Decimal("1000"), unfiltered_gross_pnl_inr=Decimal(net) + 2,
            unfiltered_net_pnl_inr=Decimal(net), avoided_loser=False,
            rejected_winner=False, statutory_fees_inr=Decimal("2"),
        )
    next_day = t0 + 86_400_000_000_000
    trades = [
        trade("LOSS", t0, t0 + 300, "-510"),
        trade("BEFORE_EXIT", t0 + 100, t0 + 400, "10"),
        trade("AFTER_EXIT", t0 + 500, t0 + 800, "10"),
        trade("NEXT_SESSION", next_day, next_day + 300, "10"),
    ]
    result = evaluate_filter_economics(trades)
    assert result.unfiltered_executed_count == 3
    assert result.filtered_executed_count == 3
    assert result.unfiltered_session_loss_rejections == 1
    assert result.filtered_session_loss_rejections == 1
    assert result.arm_unfiltered_net_pnl_inr == Decimal("-490")
