"""Focused unit tests for OpportunityRecord evaluation and fail-closed safety invariants."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
from pathlib import Path
import pytest

from quant.apps.execution_safety import (
    AutonomousDecisionPolicy,
    AutonomousExecutionPath,
    CostAssumptions,
    DecisionResult,
    IndependentRiskController,
    MockBrokerEngine,
    OpportunityRecord,
    OrderState,
    PaperOrder,
    PORTFOLIO_CAPITAL,
    PORTFOLIO_MAX_DEPLOYED,
    PORTFOLIO_MAX_INSTRUMENT,
    PORTFOLIO_MAX_SPREAD_BPS,
    evaluate_opportunity_record,
)


def test_opportunity_record_schema_and_serialization():
    """Verify that OpportunityRecord produces valid dictionary and JSON formats."""
    now = datetime(2026, 11, 10, 10, 30, 0, tzinfo=timezone.utc)
    costs = CostAssumptions(
        statutory_fees_bps=Decimal("2.8"),
        spread_bps=Decimal("5.0"),
        slippage_bps=Decimal("2.0"),
        residual_friction_bps=Decimal("20.0"),
        total_costs_bps=Decimal("29.8"),
    )
    rec = OpportunityRecord(
        schema_version="alpha-grid-opportunity-record-v1",
        decision_timestamp_utc=now.isoformat(),
        symbol="RELIANCE",
        security_id="2885",
        market_data_provenance="HISTORICAL_ARCHIVE",
        freshness_status="NOT_APPLICABLE_HISTORICAL",
        source_frame_sha256=None,
        quote_age_seconds=None,
        snapshot_id="alpha-grid-snapshot-2026-11-10-plain-v1",
        snapshot_merkle_root="06f3124a842109b42d0bf508fc30ab662b895d32c692c23fcbeba903176d90b3",
        strategy_name="ALPHA_GRID_M1",
        strategy_version="alpha-grid-m1-v1",
        strategy_approved=False,
        trailing_volatility_15m_bps=4.52,
        volatility_source="TRAILING_15M_BARS",
        observed_bid=None,
        observed_ask=None,
        observed_spread_bps=None,
        observed_depth_levels=None,
        depth_source=None,
        model_validated=False,
        model_prob_favorable=0.12,
        model_prob_adverse=0.38,
        expected_gross_bps=Decimal("15.0"),
        costs=costs,
        conservative_net_opportunity_bps=Decimal("-14.8"),
        uncertainty_ci_lower_bps=Decimal("0.0"),
        decision="NO_TRADE",
        decision_reasons=("HISTORICAL_ARCHIVE_NO_GENUINE_LIVE_QUOTE", "MODEL_STAGE3_REJECTED"),
        target_shares=0,
        virtual_reference_price=None,
    )

    d = rec.to_dict()
    assert d["schema_version"] == "alpha-grid-opportunity-record-v1"
    assert d["symbol"] == "RELIANCE"
    assert d["decision"] == "NO_TRADE"
    assert d["action"] == "NO_TRADE"  # backward compat
    assert d["reference_price_status"] == "VIRTUAL_REFERENCE_NEVER_FILL"
    assert "MODEL_STAGE3_REJECTED" in d["reason"]

    raw_json = rec.to_json()
    parsed = json.loads(raw_json)
    assert parsed["symbol"] == "RELIANCE"
    assert parsed["costs"]["total_costs_bps"] == "29.8"


def test_missing_quote_or_depth_strictly_abstains():
    """Verify that absent live quotes or depth never fabricate prices and strictly abstain."""
    now = datetime(2026, 11, 10, 10, 30, 0, tzinfo=timezone.utc)
    rec = evaluate_opportunity_record(
        symbol="INFY",
        security_id="1594",
        decision_timestamp_utc=now,
        market_data_provenance="HISTORICAL_ARCHIVE",
        strategy_name="ALPHA_GRID_M1",
        strategy_version="alpha-grid-m1-v1",
        stage3_approved=True,  # Even if approved, missing quote must abstain
        bid_price=None,
        ask_price=None,
        quote_timestamp_utc=None,
        depth_source=None,
        depth_levels=None,
        trailing_volatility_15m_bps=5.0,
        volatility_source="TRAILING_15M_BARS",
        model_prob_favorable=0.25,
        model_prob_adverse=0.15,
        expected_gross_bps=Decimal("50.0"),
        statutory_fees_bps=Decimal("2.8"),
        slippage_bps=Decimal("2.0"),
        residual_friction_bps=Decimal("20.0"),
        uncertainty_ci_lower_bps=Decimal("10.0"),
    )

    assert rec.decision == "NO_TRADE"
    assert rec.target_shares == 0
    assert rec.observed_bid is None
    assert rec.observed_ask is None
    assert rec.observed_depth_levels is None
    assert rec.depth_source is None
    assert any("NO_GENUINE_LIVE_QUOTE" in r for r in rec.decision_reasons)


def test_stale_quote_rejection():
    """Verify that quotes older than MAX_QUOTE_AGE_SECONDS (2.0s) or negative age are rejected."""
    now = datetime(2026, 11, 10, 10, 30, 0, tzinfo=timezone.utc)
    stale_quote_time = now - timedelta(seconds=2.5)

    rec = evaluate_opportunity_record(
        symbol="TCS",
        security_id="11536",
        decision_timestamp_utc=now,
        market_data_provenance="LIVE_WS",
        strategy_name="ALPHA_GRID_M1",
        strategy_version="alpha-grid-m1-v1",
        stage3_approved=True,
        bid_price=Decimal("4100.00"),
        ask_price=Decimal("4101.00"),
        quote_timestamp_utc=stale_quote_time,
        source_frame_sha256="abc12345",
        depth_source="STANDARD_FULL_5LEVEL",
        depth_levels=5,
        trailing_volatility_15m_bps=6.2,
        volatility_source="TRAILING_15M_BARS",
        model_prob_favorable=0.30,
        model_prob_adverse=0.10,
        expected_gross_bps=Decimal("60.0"),
        statutory_fees_bps=Decimal("2.8"),
        slippage_bps=Decimal("2.0"),
        residual_friction_bps=Decimal("20.0"),
        uncertainty_ci_lower_bps=Decimal("15.0"),
    )

    assert rec.decision == "NO_TRADE"
    assert rec.freshness_status == "STALE"
    assert rec.quote_age_seconds == 2.5
    assert any("STALE_QUOTE" in r for r in rec.decision_reasons)


def test_unvalidated_stage3_model_rejection():
    """Verify that unapproved Stage 3 models strictly yield NO_TRADE."""
    now = datetime(2026, 11, 10, 10, 30, 0, tzinfo=timezone.utc)
    quote_time = now - timedelta(seconds=0.5)

    rec = evaluate_opportunity_record(
        symbol="HDFCBANK",
        security_id="1333",
        decision_timestamp_utc=now,
        market_data_provenance="LIVE_WS",
        strategy_name="ALPHA_GRID_M1",
        strategy_version="alpha-grid-m1-v1",
        stage3_approved=False,  # Unvalidated
        bid_price=Decimal("1500.00"),
        ask_price=Decimal("1500.50"),
        quote_timestamp_utc=quote_time,
        source_frame_sha256="def67890",
        depth_source="STANDARD_FULL_5LEVEL",
        depth_levels=5,
        trailing_volatility_15m_bps=4.0,
        volatility_source="TRAILING_15M_BARS",
        model_prob_favorable=0.28,
        model_prob_adverse=0.12,
        expected_gross_bps=Decimal("45.0"),
        statutory_fees_bps=Decimal("2.8"),
        slippage_bps=Decimal("2.0"),
        residual_friction_bps=Decimal("20.0"),
        uncertainty_ci_lower_bps=Decimal("8.0"),
    )

    assert rec.decision == "NO_TRADE"
    assert rec.strategy_approved is False
    assert any("MODEL_STAGE3_REJECTED" in r for r in rec.decision_reasons)


def test_corporate_action_economic_ineligibility():
    """Verify that windows flagged with unresolved corporate actions strictly abstain."""
    now = datetime(2026, 11, 10, 10, 30, 0, tzinfo=timezone.utc)
    quote_time = now - timedelta(seconds=0.2)

    rec = evaluate_opportunity_record(
        symbol="MCX",
        security_id="31181",
        decision_timestamp_utc=now,
        market_data_provenance="LIVE_WS",
        strategy_name="ALPHA_GRID_M1",
        strategy_version="alpha-grid-m1-v1",
        stage3_approved=True,
        bid_price=Decimal("5000.00"),
        ask_price=Decimal("5002.00"),
        quote_timestamp_utc=quote_time,
        trailing_volatility_15m_bps=5.0,
        volatility_source="TRAILING_15M_BARS",
        model_prob_favorable=0.35,
        model_prob_adverse=0.08,
        expected_gross_bps=Decimal("60.0"),
        uncertainty_ci_lower_bps=Decimal("12.0"),
        is_corporate_action_ineligible=True,  # Corporate action window
    )

    assert rec.decision == "NO_TRADE"
    assert any("INELIGIBLE_UNVERIFIED_INTRADAY_PRICE_BASIS" in r for r in rec.decision_reasons)


def test_missing_past_volatility_rejection():
    """Verify that missing trailing volatility measurement strictly abstains."""
    now = datetime(2026, 11, 10, 10, 30, 0, tzinfo=timezone.utc)
    quote_time = now - timedelta(seconds=0.3)

    rec = evaluate_opportunity_record(
        symbol="RELIANCE",
        security_id="2885",
        decision_timestamp_utc=now,
        market_data_provenance="LIVE_WS",
        strategy_name="ALPHA_GRID_M1",
        strategy_version="alpha-grid-m1-v1",
        stage3_approved=True,
        bid_price=Decimal("2500.00"),
        ask_price=Decimal("2500.50"),
        quote_timestamp_utc=quote_time,
        trailing_volatility_15m_bps=None,  # Missing volatility
        volatility_source=None,
        model_prob_favorable=0.30,
        model_prob_adverse=0.10,
        expected_gross_bps=Decimal("50.0"),
        uncertainty_ci_lower_bps=Decimal("10.0"),
    )

    assert rec.decision == "NO_TRADE"
    assert any("PAST_VOLATILITY_UNAVAILABLE" in r for r in rec.decision_reasons)


def test_sub_hurdle_conservative_net_rejection():
    """Verify that expected net opportunity <= 5.0 bps strictly abstains."""
    now = datetime(2026, 11, 10, 10, 30, 0, tzinfo=timezone.utc)
    quote_time = now - timedelta(seconds=0.1)

    # Spread = (2502 - 2500) / 2502 * 10000 = ~8.0 bps
    # Fees = 2.8, slippage = 2.0, friction = 20.0 -> total cost = 32.8 bps
    # If gross = 35.0 bps -> net = 2.2 bps <= 5.0 bps hurdle
    rec = evaluate_opportunity_record(
        symbol="RELIANCE",
        security_id="2885",
        decision_timestamp_utc=now,
        market_data_provenance="LIVE_WS",
        strategy_name="ALPHA_GRID_M1",
        strategy_version="alpha-grid-m1-v1",
        stage3_approved=True,
        bid_price=Decimal("2500.00"),
        ask_price=Decimal("2502.00"),
        quote_timestamp_utc=quote_time,
        source_frame_sha256="frame_hash_1",
        depth_source="STANDARD_FULL_5LEVEL",
        depth_levels=5,
        trailing_volatility_15m_bps=4.8,
        volatility_source="TRAILING_15M_BARS",
        model_prob_favorable=0.20,
        model_prob_adverse=0.15,
        expected_gross_bps=Decimal("35.0"),
        statutory_fees_bps=Decimal("2.8"),
        slippage_bps=Decimal("2.0"),
        residual_friction_bps=Decimal("20.0"),
        uncertainty_ci_lower_bps=Decimal("6.0"),
    )

    assert rec.decision == "NO_TRADE"
    assert any("SUB_HURDLE_NET_OPPORTUNITY" in r for r in rec.decision_reasons)


def test_qualified_trade_candidate_when_all_gates_pass():
    """Verify deterministic TRADE_CANDIDATE qualification when every gate clears."""
    now = datetime(2026, 11, 10, 10, 30, 0, tzinfo=timezone.utc)
    quote_time = now - timedelta(seconds=0.1)

    # Spread = (2500.50 - 2500.00) / 2500.50 * 10000 = ~2.0 bps
    # Fees = 2.8, slippage = 2.0, friction = 20.0 -> total cost = ~26.8 bps
    # Gross = 60.0 bps -> net = ~33.2 bps > 5.0 bps hurdle
    rec = evaluate_opportunity_record(
        symbol="RELIANCE",
        security_id="2885",
        decision_timestamp_utc=now,
        market_data_provenance="LIVE_WS",
        strategy_name="ALPHA_GRID_M1",
        strategy_version="alpha-grid-m1-v1",
        stage3_approved=True,
        bid_price=Decimal("2500.00"),
        ask_price=Decimal("2500.50"),
        quote_timestamp_utc=quote_time,
        source_frame_sha256="frame_hash_valid",
        depth_source="STANDARD_FULL_5LEVEL",
        depth_levels=5,
        trailing_volatility_15m_bps=5.2,
        volatility_source="TRAILING_15M_BARS",
        model_prob_favorable=0.45,
        model_prob_adverse=0.10,
        expected_gross_bps=Decimal("60.0"),
        statutory_fees_bps=Decimal("2.8"),
        slippage_bps=Decimal("2.0"),
        residual_friction_bps=Decimal("20.0"),
        uncertainty_ci_lower_bps=Decimal("15.0"),
        is_corporate_action_ineligible=False,
    )

    assert rec.decision == "TRADE_CANDIDATE"
    assert len(rec.decision_reasons) == 0
    # Max instrument = 5000 / 2500.50 = 1 share
    assert rec.target_shares == 1
    assert rec.virtual_reference_price == Decimal("2500.50")
    d = rec.to_dict()
    assert d["reference_price_status"] == "VIRTUAL_REFERENCE_NEVER_FILL"
    assert d["action"] == "TRADE_CANDIDATE"


def test_missing_gross_payoff_yields_unknown_and_no_trade():
    """Verify that absent gross payoff model does not fabricate returns and yields UNKNOWN + NO_TRADE."""
    now = datetime(2026, 11, 10, 10, 30, 0, tzinfo=timezone.utc)
    quote_time = now - timedelta(seconds=0.1)

    rec = evaluate_opportunity_record(
        symbol="RELIANCE",
        security_id="2885",
        decision_timestamp_utc=now,
        market_data_provenance="LIVE_WS",
        strategy_name="ALPHA_GRID_M1",
        strategy_version="alpha-grid-m1-v1",
        stage3_approved=True,
        bid_price=Decimal("2500.00"),
        ask_price=Decimal("2500.50"),
        quote_timestamp_utc=quote_time,
        source_frame_sha256="frame_hash_valid",
        depth_source="STANDARD_FULL_5LEVEL",
        depth_levels=5,
        trailing_volatility_15m_bps=5.2,
        volatility_source="TRAILING_15M_BARS",
        model_prob_favorable=0.45,
        model_prob_adverse=0.10,
        expected_gross_bps=None,  # No validated payoff model
        statutory_fees_bps=Decimal("2.8"),
        slippage_bps=Decimal("2.0"),
        residual_friction_bps=Decimal("20.0"),
        uncertainty_ci_lower_bps=Decimal("15.0"),
        is_corporate_action_ineligible=False,
    )

    assert rec.decision == "NO_TRADE"
    assert rec.expected_gross_bps is None
    assert rec.conservative_net_opportunity_bps is None
    d = rec.to_dict()
    assert d["expected_gross_bps"] == "UNKNOWN"
    assert d["conservative_net_opportunity_bps"] == "UNKNOWN"
    assert any("EXPECTED_GROSS_PAYOFF_UNESTABLISHED" in r for r in rec.decision_reasons)


def test_missing_uncertainty_ci_yields_unknown_and_no_trade():
    """Verify that absent uncertainty CI does not fabricate 0.0 and yields UNKNOWN + NO_TRADE."""
    now = datetime(2026, 11, 10, 10, 30, 0, tzinfo=timezone.utc)
    quote_time = now - timedelta(seconds=0.1)

    rec = evaluate_opportunity_record(
        symbol="RELIANCE",
        security_id="2885",
        decision_timestamp_utc=now,
        market_data_provenance="LIVE_WS",
        strategy_name="ALPHA_GRID_M1",
        strategy_version="alpha-grid-m1-v1",
        stage3_approved=True,
        bid_price=Decimal("2500.00"),
        ask_price=Decimal("2500.50"),
        quote_timestamp_utc=quote_time,
        source_frame_sha256="frame_hash_valid",
        depth_source="STANDARD_FULL_5LEVEL",
        depth_levels=5,
        trailing_volatility_15m_bps=5.2,
        volatility_source="TRAILING_15M_BARS",
        model_prob_favorable=0.45,
        model_prob_adverse=0.10,
        expected_gross_bps=Decimal("60.0"),
        statutory_fees_bps=Decimal("2.8"),
        slippage_bps=Decimal("2.0"),
        residual_friction_bps=Decimal("20.0"),
        uncertainty_ci_lower_bps=None,  # Missing CI lower bound
        is_corporate_action_ineligible=False,
    )

    assert rec.decision == "NO_TRADE"
    assert rec.uncertainty_ci_lower_bps is None
    d = rec.to_dict()
    assert d["uncertainty_ci_lower_bps"] == "UNKNOWN"
    assert any("STATISTICAL_UNCERTAINTY_UNKNOWN" in r for r in rec.decision_reasons)


def test_missing_quotes_yield_costs_and_net_unknown():
    """Verify that absent quotes yield UNKNOWN for spread, total costs, and net opportunity."""
    now = datetime(2026, 11, 10, 10, 30, 0, tzinfo=timezone.utc)

    rec = evaluate_opportunity_record(
        symbol="RELIANCE",
        security_id="2885",
        decision_timestamp_utc=now,
        market_data_provenance="HISTORICAL_ARCHIVE",
        strategy_name="ALPHA_GRID_M1",
        strategy_version="alpha-grid-m1-v1",
        stage3_approved=True,
        bid_price=None,
        ask_price=None,
        quote_timestamp_utc=None,
        trailing_volatility_15m_bps=5.2,
        volatility_source="TRAILING_15M_BARS",
        model_prob_favorable=0.45,
        model_prob_adverse=0.10,
        expected_gross_bps=Decimal("60.0"),
        uncertainty_ci_lower_bps=Decimal("15.0"),
    )

    assert rec.decision == "NO_TRADE"
    assert rec.costs.spread_bps is None
    assert rec.costs.total_costs_bps is None
    assert rec.conservative_net_opportunity_bps is None
    d = rec.to_dict()
    assert d["costs"]["spread_bps"] == "UNKNOWN"
    assert d["costs"]["total_costs_bps"] == "UNKNOWN"
    assert d["conservative_net_opportunity_bps"] == "UNKNOWN"
    assert any("COSTS_UNKNOWN" in r for r in rec.decision_reasons)
    assert any("CONSERVATIVE_NET_UNESTABLISHED" in r for r in rec.decision_reasons)


def test_autonomous_execution_path_qualified_candidate_paper_order(tmp_path: Path):
    """Test an eligible hypothetical opportunity using isolated deterministic fixtures to verify correct policy/risk behavior without orders."""
    now = datetime.now(timezone.utc)
    policy = AutonomousDecisionPolicy(stage3_approved=True)
    risk = IndependentRiskController()
    broker = MockBrokerEngine()
    path = AutonomousExecutionPath(
        decision_policy=policy,
        risk_controller=risk,
        broker_engine=broker,
        hdd_path=tmp_path,
    )

    symbol = "RELIANCE"
    bid = Decimal("2500.00")
    ask = Decimal("2500.50")
    spread_bps = (ask - bid) / ask * Decimal("10000.0")

    decision, order = path.process_signal(
        symbol=symbol,
        bid_price=bid,
        ask_price=ask,
        quote_timestamp_utc=now,
        model_prob_favorable=0.45,
        model_prob_adverse=0.10,
        expected_gross_bps=Decimal("60.0"),
        statutory_fees_bps=Decimal("2.8"),
        spread_bps=spread_bps,
        slippage_bps=Decimal("2.0"),
        uncertainty_ci_lower_bps=Decimal("15.0"),
    )

    assert decision.action == "BUY"
    assert order is not None
    assert order.symbol == symbol
    assert order.state == OrderState.FILLED
    assert order.filled_shares == decision.target_shares
    assert order.avg_fill_price == ask
    assert path.deployed_capital == ask * Decimal(order.filled_shares)
    assert symbol in path.active_positions


