"""Unit tests verifying Alpha Grid offline autonomous decision and execution-safety invariants."""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
import pytest

from quant.apps.execution_safety import (
    AutonomousDecisionPolicy,
    AutonomousExecutionPath,
    IndependentRiskController,
    IllegalStateTransitionError,
    MockBrokerEngine,
    OrderState,
    PaperOrder,
    PORTFOLIO_CAPITAL,
    PORTFOLIO_MAX_DEPLOYED,
    PORTFOLIO_MAX_INSTRUMENT,
    PORTFOLIO_SESSION_LOSS,
)


def _fresh_timestamp() -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=0.5)


def test_decision_policy_fails_closed_when_stage3_unapproved() -> None:
    """Requirement 1: Unapproved Stage 3 model predictions fail closed with NO_TRADE."""
    policy = AutonomousDecisionPolicy(stage3_approved=False)
    decision = policy.evaluate_candidate(
        symbol="RELIANCE",
        bid_price=Decimal("2500.00"),
        ask_price=Decimal("2501.25"),
        quote_timestamp_utc=_fresh_timestamp(),
        model_prob_favorable=0.85,  # High model probability
        model_prob_adverse=0.10,
        expected_gross_bps=Decimal("35.0"),
        statutory_fees_bps=Decimal("10.5"),
        spread_bps=Decimal("5.0"),
        slippage_bps=Decimal("5.0"),
        uncertainty_ci_lower_bps=Decimal("12.0"),
    )
    assert decision.action == "NO_TRADE"
    assert "MODEL_STAGE3_REJECTED" in decision.reason
    assert decision.target_shares == 0


def test_decision_policy_rejects_stale_data() -> None:
    """Requirement 1 & 4: Stale quotes (> 2s) fail closed with NO_TRADE."""
    policy = AutonomousDecisionPolicy(stage3_approved=True)
    stale_ts = datetime.now(timezone.utc) - timedelta(seconds=5.0)
    decision = policy.evaluate_candidate(
        symbol="RELIANCE",
        bid_price=Decimal("2500.00"),
        ask_price=Decimal("2501.25"),
        quote_timestamp_utc=stale_ts,
        model_prob_favorable=0.85,
        model_prob_adverse=0.10,
        expected_gross_bps=Decimal("35.0"),
        statutory_fees_bps=Decimal("10.5"),
        spread_bps=Decimal("5.0"),
        slippage_bps=Decimal("5.0"),
        uncertainty_ci_lower_bps=Decimal("12.0"),
    )
    assert decision.action == "NO_TRADE"
    assert "STALE_QUOTE" in decision.reason


def test_decision_policy_uncertainty_gate() -> None:
    """Requirement 1: 95% bootstrap lower bound <= 0 bps rejects trade."""
    policy = AutonomousDecisionPolicy(stage3_approved=True)
    decision = policy.evaluate_candidate(
        symbol="RELIANCE",
        bid_price=Decimal("2500.00"),
        ask_price=Decimal("2501.25"),
        quote_timestamp_utc=_fresh_timestamp(),
        model_prob_favorable=0.85,
        model_prob_adverse=0.10,
        expected_gross_bps=Decimal("35.0"),
        statutory_fees_bps=Decimal("10.5"),
        spread_bps=Decimal("5.0"),
        slippage_bps=Decimal("5.0"),
        uncertainty_ci_lower_bps=Decimal("-1.5"),  # Negative CI lower bound
    )
    assert decision.action == "NO_TRADE"
    assert "STATISTICAL_UNCERTAINTY_BREACH" in decision.reason


def test_risk_controller_enforces_limits() -> None:
    """Requirement 2: Enforces position, capital, session-loss, and spread boundaries."""
    risk = IndependentRiskController()

    # 1. Position size limit (₹5,000)
    ok, reason = risk.validate_pre_trade(
        "RELIANCE", Decimal("6000.00"), Decimal("5.0"), Decimal("0"), 0, Decimal("0")
    )
    assert not ok and "MAX_POSITION_SIZE_EXCEEDED" in reason

    # 2. Deployed capital limit (₹15,000)
    ok, reason = risk.validate_pre_trade(
        "RELIANCE", Decimal("4000.00"), Decimal("5.0"), Decimal("12000.00"), 2, Decimal("0")
    )
    assert not ok and "MAX_DEPLOYED_CAPITAL_EXCEEDED" in reason

    # 3. Max concurrent positions limit (3)
    ok, reason = risk.validate_pre_trade(
        "RELIANCE", Decimal("4000.00"), Decimal("5.0"), Decimal("8000.00"), 3, Decimal("0")
    )
    assert not ok and "MAX_POSITIONS_REACHED" in reason

    # 4. Session loss limit (₹500)
    ok, reason = risk.validate_pre_trade(
        "RELIANCE", Decimal("4000.00"), Decimal("5.0"), Decimal("0"), 0, Decimal("-550.00")
    )
    assert not ok and "SESSION_LOSS_BREACHED" in reason

    # 5. Max spread limit (25 bps)
    ok, reason = risk.validate_pre_trade(
        "RELIANCE", Decimal("4000.00"), Decimal("30.0"), Decimal("0"), 0, Decimal("0")
    )
    assert not ok and "SPREAD_TOO_WIDE" in reason

    # A smaller configured capital balance is an independent hard ceiling.
    small = IndependentRiskController(capital=Decimal("1000"))
    ok, reason = small.validate_pre_trade(
        "RELIANCE", Decimal("4000"), Decimal("5"), Decimal("0"), 0, Decimal("0")
    )
    assert not ok and "CAPITAL" in reason


def test_order_lifecycle_and_illegal_transitions() -> None:
    """Requirement 3: State machine enforces valid transitions and rejects illegal ones."""
    order = PaperOrder(
        client_order_id="test_ord_1",
        symbol="RELIANCE",
        side="BUY",
        shares=2,
        filled_shares=0,
        limit_price=Decimal("2500.00"),
        avg_fill_price=Decimal("0"),
        state=OrderState.PENDING,
        retries=0,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        updated_at_utc=datetime.now(timezone.utc).isoformat(),
    )

    # Valid: PENDING -> ACKNOWLEDGED -> FILLED
    order.transition_to(OrderState.ACKNOWLEDGED)
    assert order.state == OrderState.ACKNOWLEDGED

    order.transition_to(OrderState.FILLED)
    assert order.state == OrderState.FILLED

    # Illegal: FILLED is terminal; cannot transition to CANCELLED or PENDING
    with pytest.raises(IllegalStateTransitionError):
        order.transition_to(OrderState.CANCELLED)

    with pytest.raises(IllegalStateTransitionError):
        order.transition_to(OrderState.PENDING)


def test_idempotency_in_mock_broker() -> None:
    """Requirement 4: Duplicate client order IDs return identical order without duplication."""
    broker = MockBrokerEngine(mode="NORMAL")
    order = PaperOrder(
        client_order_id="idempotent_order_42",
        symbol="INFY",
        side="BUY",
        shares=3,
        filled_shares=0,
        limit_price=Decimal("1500.00"),
        avg_fill_price=Decimal("0"),
        state=OrderState.PENDING,
        retries=0,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
        updated_at_utc=datetime.now(timezone.utc).isoformat(),
    )

    res1 = broker.submit_order(order)
    assert res1.state == OrderState.FILLED
    assert len(broker.orders) == 1

    # Re-submission with same ID
    res2 = broker.submit_order(order)
    assert res2.state == OrderState.FILLED
    assert len(broker.orders) == 1  # No duplicate created


def test_fail_closed_on_hdd_loss(tmp_path: Path) -> None:
    """Requirement 5: Missing or inaccessible HDD path immediately fails closed."""
    policy = AutonomousDecisionPolicy(stage3_approved=True)
    risk = IndependentRiskController()
    broker = MockBrokerEngine()
    non_existent_hdd = tmp_path / "unmounted_volume"

    exec_path = AutonomousExecutionPath(
        decision_policy=policy,
        risk_controller=risk,
        broker_engine=broker,
        hdd_path=non_existent_hdd,
    )

    decision, order = exec_path.process_signal(
        symbol="RELIANCE",
        bid_price=Decimal("2500.00"),
        ask_price=Decimal("2501.25"),
        quote_timestamp_utc=_fresh_timestamp(),
        model_prob_favorable=0.85,
        model_prob_adverse=0.10,
        expected_gross_bps=Decimal("35.0"),
        statutory_fees_bps=Decimal("10.5"),
        spread_bps=Decimal("5.0"),
        slippage_bps=Decimal("5.0"),
        uncertainty_ci_lower_bps=Decimal("12.0"),
    )

    assert decision.action == "NO_TRADE"
    assert "STORAGE_INTEGRITY_FAIL_CLOSED" in decision.reason
    assert order is None
    assert risk.kill_switch_engaged is True


def test_broker_unknown_state_engages_kill_switch(tmp_path: Path) -> None:
    """Requirement 4 & 5: Broker timeout/ambiguity engages kill switch immediately."""
    policy = AutonomousDecisionPolicy(stage3_approved=True)
    risk = IndependentRiskController()
    broker = MockBrokerEngine(mode="TIMEOUT")  # Simulates broker ambiguity

    exec_path = AutonomousExecutionPath(
        decision_policy=policy,
        risk_controller=risk,
        broker_engine=broker,
        hdd_path=tmp_path,
    )

    decision, order = exec_path.process_signal(
        symbol="RELIANCE",
        bid_price=Decimal("2500.00"),
        ask_price=Decimal("2501.25"),
        quote_timestamp_utc=_fresh_timestamp(),
        model_prob_favorable=0.85,
        model_prob_adverse=0.10,
        expected_gross_bps=Decimal("35.0"),
        statutory_fees_bps=Decimal("10.5"),
        spread_bps=Decimal("5.0"),
        slippage_bps=Decimal("5.0"),
        uncertainty_ci_lower_bps=Decimal("12.0"),
    )

    assert decision.action == "BUY"
    assert order is not None
    assert order.state == OrderState.UNKNOWN
    assert risk.kill_switch_engaged is True
    assert "BROKER_UNKNOWN_STATE_TRIGGERED" in risk.kill_switch_reason


def test_predictions_cannot_bypass_risk_or_submit_real_orders(tmp_path: Path) -> None:
    """Requirement 6: Even maximum model probability is blocked by risk controller limits."""
    policy = AutonomousDecisionPolicy(stage3_approved=True)
    risk = IndependentRiskController()
    broker = MockBrokerEngine()

    exec_path = AutonomousExecutionPath(
        decision_policy=policy,
        risk_controller=risk,
        broker_engine=broker,
        hdd_path=tmp_path,
    )

    # Artificially engage kill switch
    risk.engage_kill_switch("OPERATOR_MANUAL_STOP")

    decision, order = exec_path.process_signal(
        symbol="RELIANCE",
        bid_price=Decimal("2500.00"),
        ask_price=Decimal("2501.25"),
        quote_timestamp_utc=_fresh_timestamp(),
        model_prob_favorable=0.99,  # 99% probability
        model_prob_adverse=0.01,
        expected_gross_bps=Decimal("100.0"),
        statutory_fees_bps=Decimal("10.5"),
        spread_bps=Decimal("5.0"),
        slippage_bps=Decimal("5.0"),
        uncertainty_ci_lower_bps=Decimal("50.0"),
    )

    assert decision.action == "NO_TRADE"
    assert "RISK_CONTROLLER_BLOCKED: KILL_SWITCH_ACTIVE" in decision.reason
    assert order is None
    assert len(broker.orders) == 0


def test_duplicate_signal_does_not_double_reserve_capital(tmp_path: Path) -> None:
    path = AutonomousExecutionPath(
        AutonomousDecisionPolicy(stage3_approved=True), IndependentRiskController(),
        MockBrokerEngine(), hdd_path=tmp_path,
    )
    ts = _fresh_timestamp()
    args = ("RELIANCE", Decimal("100"), Decimal("100.05"), ts, 0.9, 0.1,
            Decimal("50"), Decimal("10"), Decimal("5"), Decimal("5"), Decimal("10"))
    first, first_order = path.process_signal(*args)
    reserved = path.deployed_capital
    second, second_order = path.process_signal(*args)
    assert first.action == "BUY" and first_order is not None
    assert second.action == "NO_TRADE" and second_order is None
    assert path.deployed_capital == reserved
    assert len(path.broker_engine.orders) == 1


def test_partial_orders_reserve_shared_limits(tmp_path: Path) -> None:
    path = AutonomousExecutionPath(
        AutonomousDecisionPolicy(stage3_approved=True), IndependentRiskController(),
        MockBrokerEngine(mode="PARTIAL_THEN_FILL"), hdd_path=tmp_path,
    )
    for symbol in ("RELIANCE", "INFY", "HDFCBANK"):
        decision, order = path.process_signal(
            symbol, Decimal("100"), Decimal("100.05"), _fresh_timestamp(), 0.9, 0.1,
            Decimal("50"), Decimal("10"), Decimal("5"), Decimal("5"), Decimal("10"),
        )
        assert decision.action == "BUY" and order is not None
        assert order.state == OrderState.PARTIALLY_FILLED
    assert len(path.active_positions) == 3
    assert path.deployed_capital > Decimal("14000")
    blocked, order = path.process_signal(
        "SBIN", Decimal("100"), Decimal("100.05"), _fresh_timestamp(), 0.9, 0.1,
        Decimal("50"), Decimal("10"), Decimal("5"), Decimal("5"), Decimal("10"),
    )
    assert blocked.action == "NO_TRADE" and order is None
    assert len(path.broker_engine.orders) == 3

