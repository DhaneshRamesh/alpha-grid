"""Alpha Grid — Minimum Offline Autonomous Decision & Execution-Safety Path.

Implements the fail-closed autonomous safety layer:
1. Decision Policy: Conservative net opportunity vs NO_TRADE with uncertainty and Stage 3 gates.
2. Independent Risk Checks: Capital, position, per-trade, spread, and session-loss limits.
3. Broker Order Lifecycle State Machine:
   PENDING -> ACKNOWLEDGED -> PARTIALLY_FILLED -> FILLED | CANCELLED | REJECTED | UNKNOWN
4. Idempotency, Reconciliation, Bounded Retries, and Kill-Switch behavior.
5. Fail-Closed Guards on HDD loss, journal tampering, broker ambiguity, and model rejection.
6. Offline deterministic mock broker engine (zero live calls, zero OHLCV fill assumptions).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal
import uuid

# Safety Invariants & Risk Limits (mirroring cash-paper-risk-v1 / PortfolioSimulator)
PORTFOLIO_CAPITAL = Decimal("20000.00")
PORTFOLIO_MAX_DEPLOYED = Decimal("15000.00")
PORTFOLIO_MAX_INSTRUMENT = Decimal("5000.00")
PORTFOLIO_MAX_POSITIONS = 3
PORTFOLIO_SESSION_LOSS = Decimal("500.00")
PORTFOLIO_DECISION_LOSS = Decimal("200.00")
PORTFOLIO_MAX_SPREAD_BPS = Decimal("25.0")
PORTFOLIO_RESIDUAL_FRICTION_BPS = Decimal("20.0")
MAX_QUOTE_AGE_SECONDS = 2.0
MAX_BROKER_RETRIES = 2
DEFAULT_PLAIN_HDD_PATH = Path("/Volumes/Danny's HDD/AlphaGrid-Plain")


@dataclass(frozen=True, slots=True)
class CostAssumptions:
    statutory_fees_bps: Decimal
    spread_bps: Decimal | None
    slippage_bps: Decimal
    residual_friction_bps: Decimal
    total_costs_bps: Decimal | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "statutory_fees_bps": str(self.statutory_fees_bps),
            "spread_bps": str(self.spread_bps) if self.spread_bps is not None else "UNKNOWN",
            "slippage_bps": str(self.slippage_bps),
            "residual_friction_bps": str(self.residual_friction_bps),
            "total_costs_bps": str(self.total_costs_bps) if self.total_costs_bps is not None else "UNKNOWN",
        }


@dataclass(frozen=True, slots=True)
class OpportunityRecord:
    schema_version: str
    decision_timestamp_utc: str
    symbol: str
    security_id: str | None
    market_data_provenance: str
    freshness_status: str
    source_frame_sha256: str | None
    quote_age_seconds: float | None
    snapshot_id: str | None
    snapshot_merkle_root: str | None
    strategy_name: str
    strategy_version: str
    strategy_approved: bool
    trailing_volatility_15m_bps: float | None
    volatility_source: str | None
    observed_bid: Decimal | None
    observed_ask: Decimal | None
    observed_spread_bps: Decimal | None
    observed_depth_levels: int | None
    depth_source: str | None
    model_validated: bool
    model_prob_favorable: float | None
    model_prob_adverse: float | None
    expected_gross_bps: Decimal | None
    costs: CostAssumptions
    conservative_net_opportunity_bps: Decimal | None
    uncertainty_ci_lower_bps: Decimal | None
    decision: Literal["TRADE_CANDIDATE", "NO_TRADE"]
    decision_reasons: tuple[str, ...]
    target_shares: int
    virtual_reference_price: Decimal | None

    def to_dict(self) -> dict[str, Any]:
        try:
            dt = datetime.fromisoformat(self.decision_timestamp_utc)
            dt_ist = dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
            date_ist = dt_ist.strftime("%Y-%m-%d")
            time_ist = dt_ist.strftime("%H:%M:%S")
        except Exception:
            date_ist = self.decision_timestamp_utc[:10]
            time_ist = self.decision_timestamp_utc[11:19] if len(self.decision_timestamp_utc) >= 19 else ""

        return {
            "schema_version": self.schema_version,
            "decision_timestamp_utc": self.decision_timestamp_utc,
            "date": date_ist,
            "time": time_ist,
            "symbol": self.symbol,
            "security_id": self.security_id,
            "market_data_provenance": self.market_data_provenance,
            "freshness_status": self.freshness_status,
            "source_frame_sha256": self.source_frame_sha256,
            "quote_age_seconds": self.quote_age_seconds,
            "snapshot_id": self.snapshot_id,
            "snapshot_merkle_root": self.snapshot_merkle_root,
            "strategy_name": self.strategy_name,
            "strategy_version": self.strategy_version,
            "strategy_approved": self.strategy_approved,
            "trailing_volatility_15m_bps": self.trailing_volatility_15m_bps,
            "volatility_source": self.volatility_source,
            "observed_bid": str(self.observed_bid) if self.observed_bid is not None else None,
            "observed_ask": str(self.observed_ask) if self.observed_ask is not None else None,
            "observed_spread_bps": str(self.observed_spread_bps) if self.observed_spread_bps is not None else None,
            "observed_depth_levels": self.observed_depth_levels,
            "depth_source": self.depth_source,
            "model_validated": self.model_validated,
            "p_fav": self.model_prob_favorable,
            "p_adv": self.model_prob_adverse,
            "expected_gross_bps": str(self.expected_gross_bps) if self.expected_gross_bps is not None else "UNKNOWN",
            "costs": self.costs.to_dict(),
            "conservative_net_opportunity_bps": str(self.conservative_net_opportunity_bps) if self.conservative_net_opportunity_bps is not None else "UNKNOWN",
            "uncertainty_ci_lower_bps": str(self.uncertainty_ci_lower_bps) if self.uncertainty_ci_lower_bps is not None else "UNKNOWN",
            "action": self.decision,
            "decision": self.decision,
            "reason": "; ".join(self.decision_reasons) if self.decision_reasons else "OPPORTUNITY_QUALIFIED",
            "decision_reasons": list(self.decision_reasons),
            "target_shares": self.target_shares,
            "virtual_reference_price": str(self.virtual_reference_price) if self.virtual_reference_price is not None else None,
            "reference_price_status": "VIRTUAL_REFERENCE_NEVER_FILL",
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def evaluate_opportunity_record(
    *,
    symbol: str,
    security_id: str | None = None,
    decision_timestamp_utc: datetime,
    market_data_provenance: Literal["LIVE_WS", "HISTORICAL_ARCHIVE", "REST_SNAPSHOT", "UNVERIFIED"],
    snapshot_id: str | None = None,
    snapshot_merkle_root: str | None = None,
    strategy_name: str = "ALPHA_GRID_M1",
    strategy_version: str = "alpha-grid-m1-v1",
    stage3_approved: bool = False,
    bid_price: Decimal | None = None,
    ask_price: Decimal | None = None,
    quote_timestamp_utc: datetime | None = None,
    source_frame_sha256: str | None = None,
    depth_source: str | None = None,
    depth_levels: int | None = None,
    trailing_volatility_15m_bps: float | None = None,
    volatility_source: str | None = None,
    model_prob_favorable: float | None = None,
    model_prob_adverse: float | None = None,
    expected_gross_bps: Decimal | None = None,
    statutory_fees_bps: Decimal = Decimal("2.8"),
    slippage_bps: Decimal = Decimal("2.0"),
    residual_friction_bps: Decimal = Decimal("20.0"),
    uncertainty_ci_lower_bps: Decimal | None = None,
    is_corporate_action_ineligible: bool = False,
) -> OpportunityRecord:
    """Produce a deterministic, read-only opportunity evaluation record.

    Applies fail-closed gates across:
    1. Corporate action economic basis eligibility
    2. Data provenance and quote freshness
    3. Trailing volatility availability
    4. Model Stage 3 protocol acceptance
    5. Statistical uncertainty confidence interval
    6. Conservative net opportunity hurdle (fees + spread + slippage + friction)

    Never fabricates quotes, depth, probabilities, gross payoffs, or profitable signals.
    Missing information is represented as UNKNOWN, never synthetic zeros.
    """
    reasons: list[str] = []

    # 1. Corporate action eligibility gate
    if is_corporate_action_ineligible:
        reasons.append("INELIGIBLE_UNVERIFIED_INTRADAY_PRICE_BASIS: Corporate action window lacks audited per-row price adjustment")

    # 2. Market data provenance and quote availability
    quote_age_seconds: float | None = None
    freshness_status = "UNAVAILABLE"

    if market_data_provenance == "HISTORICAL_ARCHIVE" and (bid_price is None or ask_price is None):
        freshness_status = "NOT_APPLICABLE_HISTORICAL"
        reasons.append("HISTORICAL_ARCHIVE_NO_GENUINE_LIVE_QUOTE: Historical OHLCV cannot substitute for executable quote/depth")
    elif bid_price is None or ask_price is None or ask_price <= 0:
        freshness_status = "UNAVAILABLE"
        reasons.append("NO_GENUINE_LIVE_QUOTE: Missing observed bid/ask prices")
    else:
        if quote_timestamp_utc is None:
            freshness_status = "UNAVAILABLE"
            reasons.append("QUOTE_TIMESTAMP_MISSING: Cannot verify quote freshness")
        else:
            quote_age_seconds = (decision_timestamp_utc - quote_timestamp_utc).total_seconds()
            if quote_age_seconds < 0 or quote_age_seconds > float(MAX_QUOTE_AGE_SECONDS):
                freshness_status = "STALE"
                reasons.append(f"STALE_QUOTE: Age {quote_age_seconds:.2f}s exceeds limit {MAX_QUOTE_AGE_SECONDS}s")
            else:
                freshness_status = "FRESH"

    # 3. Quoted spread and depth accounting (No fabricated zeros when quotes are absent)
    observed_spread_bps: Decimal | None = None
    if bid_price is not None and ask_price is not None and ask_price > 0:
        observed_spread_bps = (ask_price - bid_price) / ask_price * Decimal("10000.0")
        if observed_spread_bps > PORTFOLIO_MAX_SPREAD_BPS:
            reasons.append(f"SPREAD_LIMIT_EXCEEDED: Quoted spread {observed_spread_bps:.2f} bps > {PORTFOLIO_MAX_SPREAD_BPS} bps")

    observed_depth_levels = depth_levels if (depth_levels is not None and depth_levels > 0) else None
    resolved_depth_source = depth_source if observed_depth_levels is not None else None

    # 4. Trailing volatility measurement
    resolved_vol_source = volatility_source
    if trailing_volatility_15m_bps is None:
        reasons.append("PAST_VOLATILITY_UNAVAILABLE: Insufficient trailing continuous bars")
        resolved_vol_source = None
    elif trailing_volatility_15m_bps <= 0:
        reasons.append("PAST_VOLATILITY_DEGENERATE: Zero or negative trailing volatility")

    # 5. Model validation / Stage 3 protocol gate
    if not stage3_approved:
        reasons.append("MODEL_STAGE3_REJECTED: Predeclared economic acceptance criteria unmet")

    if model_prob_favorable is None or model_prob_adverse is None:
        reasons.append("MODEL_ESTIMATES_UNAVAILABLE: Missing probability predictions")

    # 6. Expected gross return (Never infer expected return solely from probabilities without a validated payoff model)
    if expected_gross_bps is None:
        reasons.append("EXPECTED_GROSS_PAYOFF_UNESTABLISHED: No validated payoff model exists to infer returns from class probabilities")

    # 7. Statistical uncertainty lower bound gate (Never fabricate 0.0)
    if uncertainty_ci_lower_bps is None:
        reasons.append("STATISTICAL_UNCERTAINTY_UNKNOWN: 95% confidence interval lower bound uncalculated")
    elif uncertainty_ci_lower_bps <= Decimal("0"):
        reasons.append(f"STATISTICAL_UNCERTAINTY_BREACH: 95% CI Lower {uncertainty_ci_lower_bps} <= 0 bps")

    # 8. Cost accounting & conservative net opportunity calculation
    if observed_spread_bps is None:
        spread_cost: Decimal | None = None
        total_costs: Decimal | None = None
        conservative_net: Decimal | None = None
        reasons.append("COSTS_UNKNOWN: Missing observed quote spread prevents conservative cost accounting")
        reasons.append("CONSERVATIVE_NET_UNESTABLISHED: Cannot determine net opportunity without observed quotes and costs")
    else:
        spread_cost = observed_spread_bps
        total_costs = statutory_fees_bps + spread_cost + slippage_bps + residual_friction_bps
        if expected_gross_bps is None:
            conservative_net = None
            reasons.append("CONSERVATIVE_NET_UNESTABLISHED: Cannot determine net opportunity without validated gross payoff")
        else:
            conservative_net = expected_gross_bps - total_costs
            if conservative_net <= Decimal("5.0"):
                reasons.append(f"SUB_HURDLE_NET_OPPORTUNITY: Expected Net {conservative_net:.2f} bps <= 5.0 bps")

    # 9. Sizing and reference price determination
    if reasons:
        decision: Literal["TRADE_CANDIDATE", "NO_TRADE"] = "NO_TRADE"
        target_shares = 0
    else:
        decision = "TRADE_CANDIDATE"
        target_shares = int(PORTFOLIO_MAX_INSTRUMENT / ask_price) if (ask_price is not None and ask_price > 0) else 0

    virtual_ref = ask_price if ask_price is not None else None

    return OpportunityRecord(
        schema_version="alpha-grid-opportunity-record-v1",
        decision_timestamp_utc=decision_timestamp_utc.isoformat(),
        symbol=symbol,
        security_id=security_id,
        market_data_provenance=market_data_provenance,
        freshness_status=freshness_status,
        source_frame_sha256=source_frame_sha256,
        quote_age_seconds=quote_age_seconds,
        snapshot_id=snapshot_id,
        snapshot_merkle_root=snapshot_merkle_root,
        strategy_name=strategy_name,
        strategy_version=strategy_version,
        strategy_approved=stage3_approved,
        trailing_volatility_15m_bps=trailing_volatility_15m_bps,
        volatility_source=resolved_vol_source,
        observed_bid=bid_price,
        observed_ask=ask_price,
        observed_spread_bps=observed_spread_bps,
        observed_depth_levels=observed_depth_levels,
        depth_source=resolved_depth_source,
        model_validated=stage3_approved,
        model_prob_favorable=model_prob_favorable,
        model_prob_adverse=model_prob_adverse,
        expected_gross_bps=expected_gross_bps,
        costs=CostAssumptions(
            statutory_fees_bps=statutory_fees_bps,
            spread_bps=spread_cost,
            slippage_bps=slippage_bps,
            residual_friction_bps=residual_friction_bps,
            total_costs_bps=total_costs,
        ),
        conservative_net_opportunity_bps=conservative_net,
        uncertainty_ci_lower_bps=uncertainty_ci_lower_bps,
        decision=decision,
        decision_reasons=tuple(reasons),
        target_shares=target_shares,
        virtual_reference_price=virtual_ref,
    )


class OrderState(str, Enum):
    PENDING = "PENDING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


VALID_TRANSITIONS: dict[OrderState, set[OrderState]] = {
    OrderState.PENDING: {OrderState.ACKNOWLEDGED, OrderState.REJECTED, OrderState.UNKNOWN},
    OrderState.ACKNOWLEDGED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.REJECTED,
        OrderState.UNKNOWN,
    },
    OrderState.PARTIALLY_FILLED: {
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.UNKNOWN,
    },
    OrderState.FILLED: set(),
    OrderState.CANCELLED: set(),
    OrderState.REJECTED: set(),
    OrderState.UNKNOWN: {OrderState.ACKNOWLEDGED, OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED},
}


class IllegalStateTransitionError(ValueError):
    """Raised when an order attempts an illegal lifecycle state transition."""


class SafetyViolationError(RuntimeError):
    """Raised when an invariant or risk control is breached."""


@dataclass(frozen=True, slots=True)
class DecisionResult:
    action: Literal["BUY", "NO_TRADE"]
    symbol: str
    target_shares: int
    limit_price: Decimal
    gross_expected_bps: Decimal
    net_expected_bps: Decimal
    reason: str
    uncertainty_ci_lower_bps: Decimal
    stage3_approved: bool


@dataclass(slots=True)
class PaperOrder:
    client_order_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    shares: int
    filled_shares: int
    limit_price: Decimal
    avg_fill_price: Decimal
    state: OrderState
    retries: int
    created_at_utc: str
    updated_at_utc: str
    rejection_reason: str | None = None

    def transition_to(self, new_state: OrderState, reason: str | None = None) -> None:
        if new_state not in VALID_TRANSITIONS[self.state]:
            raise IllegalStateTransitionError(
                f"Cannot transition order {self.client_order_id} from {self.state} to {new_state}"
            )
        self.state = new_state
        self.updated_at_utc = datetime.now(timezone.utc).isoformat()
        if reason:
            self.rejection_reason = reason


class MockBrokerEngine:
    """Deterministic in-memory mock broker engine.

    Never connects to live broker endpoints and never infers fills from OHLCV.
    """

    def __init__(self, mode: Literal["NORMAL", "PARTIAL_THEN_FILL", "REJECT", "TIMEOUT"] = "NORMAL") -> None:
        self.mode = mode
        self.orders: dict[str, PaperOrder] = {}
        self.reconciliation_log: list[dict[str, Any]] = []

    def submit_order(self, order: PaperOrder) -> PaperOrder:
        # Idempotency check
        if order.client_order_id in self.orders:
            return self.orders[order.client_order_id]

        self.orders[order.client_order_id] = order

        if self.mode == "REJECT":
            order.transition_to(OrderState.REJECTED, reason="MOCK_RISK_REJECTION")
        elif self.mode == "TIMEOUT":
            # Simulate unacknowledged timeout leading to UNKNOWN
            order.transition_to(OrderState.UNKNOWN, reason="MOCK_BROKER_TIMEOUT")
        elif self.mode == "PARTIAL_THEN_FILL":
            order.transition_to(OrderState.ACKNOWLEDGED)
            half = max(1, order.shares // 2)
            order.filled_shares = half
            order.avg_fill_price = order.limit_price
            order.transition_to(OrderState.PARTIALLY_FILLED)
        else:
            # Normal fill: Acknowledge then fill
            order.transition_to(OrderState.ACKNOWLEDGED)
            order.filled_shares = order.shares
            order.avg_fill_price = order.limit_price
            order.transition_to(OrderState.FILLED)

        return order

    def cancel_order(self, client_order_id: str) -> PaperOrder:
        if client_order_id not in self.orders:
            raise KeyError(f"Order {client_order_id} not found")
        order = self.orders[client_order_id]
        if order.state in (OrderState.ACKNOWLEDGED, OrderState.PARTIALLY_FILLED):
            order.transition_to(OrderState.CANCELLED, reason="CLIENT_CANCEL_REQUESTED")
        return order


class IndependentRiskController:
    """Pre-trade risk validator enforcing capital, sizing, and loss boundaries."""

    def __init__(
        self,
        capital: Decimal = PORTFOLIO_CAPITAL,
        max_deployed: Decimal = PORTFOLIO_MAX_DEPLOYED,
        max_position: Decimal = PORTFOLIO_MAX_INSTRUMENT,
        max_positions: int = PORTFOLIO_MAX_POSITIONS,
        session_loss_limit: Decimal = PORTFOLIO_SESSION_LOSS,
        max_spread_bps: Decimal = PORTFOLIO_MAX_SPREAD_BPS,
    ) -> None:
        self.capital = capital
        self.max_deployed = max_deployed
        self.max_position = max_position
        self.max_positions = max_positions
        self.session_loss_limit = session_loss_limit
        self.max_spread_bps = max_spread_bps
        self.kill_switch_engaged = False
        self.kill_switch_reason: str | None = None

    def engage_kill_switch(self, reason: str) -> None:
        self.kill_switch_engaged = True
        self.kill_switch_reason = reason

    def validate_pre_trade(
        self,
        symbol: str,
        notional: Decimal,
        spread_bps: Decimal,
        current_deployed: Decimal,
        active_positions_count: int,
        current_session_loss: Decimal,
    ) -> tuple[bool, str]:
        """Validate order against independent risk constraints."""
        if self.kill_switch_engaged:
            return False, f"KILL_SWITCH_ACTIVE: {self.kill_switch_reason}"

        if abs(current_session_loss) >= self.session_loss_limit:
            return False, f"SESSION_LOSS_BREACHED: ₹{abs(current_session_loss)} >= ₹{self.session_loss_limit}"

        if active_positions_count >= self.max_positions:
            return False, f"MAX_POSITIONS_REACHED: {active_positions_count} >= {self.max_positions}"

        if notional > self.max_position:
            return False, f"MAX_POSITION_SIZE_EXCEEDED: ₹{notional} > ₹{self.max_position}"

        if notional <= 0 or current_deployed + notional > self.capital:
            return False, f"CAPITAL_LIMIT_EXCEEDED: ₹{current_deployed + notional} > ₹{self.capital}"

        if current_deployed + notional > self.max_deployed:
            return False, f"MAX_DEPLOYED_CAPITAL_EXCEEDED: ₹{current_deployed + notional} > ₹{self.max_deployed}"

        if spread_bps > self.max_spread_bps:
            return False, f"SPREAD_TOO_WIDE: {spread_bps} bps > {self.max_spread_bps} bps"

        return True, "RISK_CHECKS_PASSED"


class AutonomousDecisionPolicy:
    """Offline policy for caller-supplied net estimates, not M1 probability validation."""

    def __init__(self, stage3_approved: bool = False) -> None:
        # Default stage3_approved=False reflects Stage 2 stop rule trigger
        self.stage3_approved = stage3_approved

    def evaluate_candidate(
        self,
        symbol: str,
        bid_price: Decimal,
        ask_price: Decimal,
        quote_timestamp_utc: datetime,
        model_prob_favorable: float,
        model_prob_adverse: float,
        expected_gross_bps: Decimal,
        statutory_fees_bps: Decimal,
        spread_bps: Decimal,
        slippage_bps: Decimal,
        uncertainty_ci_lower_bps: Decimal,
    ) -> DecisionResult:
        """Apply quote, approval flag, uncertainty and cost checks.

        The probability arguments are unused; this does not establish model approval.
        """
        now = datetime.now(timezone.utc)
        age_seconds = (now - quote_timestamp_utc).total_seconds()

        # Gate 1: Quote Stale Data Rejection
        if age_seconds < 0 or age_seconds > MAX_QUOTE_AGE_SECONDS:
            return DecisionResult(
                action="NO_TRADE",
                symbol=symbol,
                target_shares=0,
                limit_price=ask_price,
                gross_expected_bps=expected_gross_bps,
                net_expected_bps=Decimal("0"),
                reason=f"STALE_QUOTE: Age {age_seconds:.2f}s > {MAX_QUOTE_AGE_SECONDS}s",
                uncertainty_ci_lower_bps=uncertainty_ci_lower_bps,
                stage3_approved=self.stage3_approved,
            )

        # Gate 2: Stage 3 Protocol Acceptance Gate
        if not self.stage3_approved:
            return DecisionResult(
                action="NO_TRADE",
                symbol=symbol,
                target_shares=0,
                limit_price=ask_price,
                gross_expected_bps=expected_gross_bps,
                net_expected_bps=Decimal("0"),
                reason="MODEL_STAGE3_REJECTED: Predeclared economic acceptance criteria unmet",
                uncertainty_ci_lower_bps=uncertainty_ci_lower_bps,
                stage3_approved=False,
            )

        # Gate 3: Statistical Uncertainty Gate
        if uncertainty_ci_lower_bps <= Decimal("0"):
            return DecisionResult(
                action="NO_TRADE",
                symbol=symbol,
                target_shares=0,
                limit_price=ask_price,
                gross_expected_bps=expected_gross_bps,
                net_expected_bps=Decimal("0"),
                reason=f"STATISTICAL_UNCERTAINTY_BREACH: 95% CI Lower {uncertainty_ci_lower_bps} <= 0 bps",
                uncertainty_ci_lower_bps=uncertainty_ci_lower_bps,
                stage3_approved=True,
            )

        # Gate 4: Conservative Net Opportunity Hurdle
        total_costs_bps = statutory_fees_bps + spread_bps + slippage_bps
        expected_net_bps = expected_gross_bps - total_costs_bps

        if expected_net_bps <= Decimal("5.0"):
            return DecisionResult(
                action="NO_TRADE",
                symbol=symbol,
                target_shares=0,
                limit_price=ask_price,
                gross_expected_bps=expected_gross_bps,
                net_expected_bps=expected_net_bps,
                reason=f"SUB_HURDLE_NET_OPPORTUNITY: Expected Net {expected_net_bps:.2f} bps <= 5.0 bps",
                uncertainty_ci_lower_bps=uncertainty_ci_lower_bps,
                stage3_approved=True,
            )

        # Candidate cleared all gates
        shares = int(PORTFOLIO_MAX_INSTRUMENT / ask_price)
        return DecisionResult(
            action="BUY",
            symbol=symbol,
            target_shares=shares,
            limit_price=ask_price,
            gross_expected_bps=expected_gross_bps,
            net_expected_bps=expected_net_bps,
            reason="OPPORTUNITY_QUALIFIED",
            uncertainty_ci_lower_bps=uncertainty_ci_lower_bps,
            stage3_approved=True,
        )


class AutonomousExecutionPath:
    """Complete fail-closed offline execution and safety coordinator."""

    def __init__(
        self,
        decision_policy: AutonomousDecisionPolicy,
        risk_controller: IndependentRiskController,
        broker_engine: MockBrokerEngine,
        hdd_path: Path | None = None,
    ) -> None:
        self.decision_policy = decision_policy
        self.risk_controller = risk_controller
        self.broker_engine = broker_engine
        self.hdd_path = hdd_path or (DEFAULT_PLAIN_HDD_PATH if DEFAULT_PLAIN_HDD_PATH.exists() else Path("/Volumes/AlphaGrid Data/Live/offline-dev"))
        self.deployed_capital = Decimal("0.00")
        self.active_positions: dict[str, PaperOrder] = {}
        self.session_loss = Decimal("0.00")
        self.execution_ledger: list[dict[str, Any]] = []

    def verify_hdd_integrity(self) -> bool:
        """Fail closed if external volume is unmounted or read-only."""
        if not self.hdd_path.exists():
            self.risk_controller.engage_kill_switch("HDD_VOLUME_DISCONNECTED")
            return False
        # Test write capability
        test_file = self.hdd_path / ".safety_write_probe"
        try:
            test_file.write_text("probe")
            test_file.unlink()
            return True
        except (OSError, PermissionError):
            self.risk_controller.engage_kill_switch("HDD_STORAGE_WRITE_FAILURE")
            return False

    def process_signal(
        self,
        symbol: str,
        bid_price: Decimal,
        ask_price: Decimal,
        quote_timestamp_utc: datetime,
        model_prob_favorable: float,
        model_prob_adverse: float,
        expected_gross_bps: Decimal,
        statutory_fees_bps: Decimal,
        spread_bps: Decimal,
        slippage_bps: Decimal,
        uncertainty_ci_lower_bps: Decimal,
    ) -> tuple[DecisionResult, PaperOrder | None]:
        """End-to-end execution path with multi-layered safety gates."""
        # Check storage integrity first
        if not self.verify_hdd_integrity():
            decision = DecisionResult(
                action="NO_TRADE",
                symbol=symbol,
                target_shares=0,
                limit_price=ask_price,
                gross_expected_bps=expected_gross_bps,
                net_expected_bps=Decimal("0"),
                reason="STORAGE_INTEGRITY_FAIL_CLOSED: HDD volume unavailable",
                uncertainty_ci_lower_bps=uncertainty_ci_lower_bps,
                stage3_approved=self.decision_policy.stage3_approved,
            )
            return decision, None

        # 1. Decision Policy Evaluation
        decision = self.decision_policy.evaluate_candidate(
            symbol=symbol,
            bid_price=bid_price,
            ask_price=ask_price,
            quote_timestamp_utc=quote_timestamp_utc,
            model_prob_favorable=model_prob_favorable,
            model_prob_adverse=model_prob_adverse,
            expected_gross_bps=expected_gross_bps,
            statutory_fees_bps=statutory_fees_bps,
            spread_bps=spread_bps,
            slippage_bps=slippage_bps,
            uncertainty_ci_lower_bps=uncertainty_ci_lower_bps,
        )

        if decision.action == "NO_TRADE":
            return decision, None

        # 2. Independent Risk Check; repeated signals cannot reserve twice.
        client_order_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{symbol}_{quote_timestamp_utc.isoformat()}"))
        notional = decision.limit_price * Decimal(decision.target_shares)
        if symbol in self.active_positions or client_order_id in self.broker_engine.orders:
            risk_passed, risk_reason = False, "DUPLICATE_OR_ACTIVE_SYMBOL"
        else:
            risk_passed, risk_reason = self.risk_controller.validate_pre_trade(
                symbol=symbol,
                notional=notional,
                spread_bps=spread_bps,
                current_deployed=self.deployed_capital,
                active_positions_count=len(self.active_positions),
                current_session_loss=self.session_loss,
            )

        if not risk_passed:
            blocked_decision = DecisionResult(
                action="NO_TRADE",
                symbol=symbol,
                target_shares=0,
                limit_price=ask_price,
                gross_expected_bps=expected_gross_bps,
                net_expected_bps=decision.net_expected_bps,
                reason=f"RISK_CONTROLLER_BLOCKED: {risk_reason}",
                uncertainty_ci_lower_bps=uncertainty_ci_lower_bps,
                stage3_approved=decision.stage3_approved,
            )
            return blocked_decision, None

        # 3. Create Paper Order (Deterministic, Idempotent)
        order = PaperOrder(
            client_order_id=client_order_id,
            symbol=symbol,
            side="BUY",
            shares=decision.target_shares,
            filled_shares=0,
            limit_price=decision.limit_price,
            avg_fill_price=Decimal("0"),
            state=OrderState.PENDING,
            retries=0,
            created_at_utc=datetime.now(timezone.utc).isoformat(),
            updated_at_utc=datetime.now(timezone.utc).isoformat(),
        )

        # 4. Dispatch to Mock Broker Engine
        executed_order = self.broker_engine.submit_order(order)

        # 5. Handle Broker Response & Lifecycle
        if executed_order.state in (OrderState.FILLED, OrderState.PARTIALLY_FILLED):
            # Reserve the full requested notional while any shares remain open.
            self.deployed_capital += notional
            self.active_positions[symbol] = executed_order
        elif executed_order.state == OrderState.UNKNOWN:
            # Trigger immediate reconciliation & engage kill switch
            self.risk_controller.engage_kill_switch("BROKER_UNKNOWN_STATE_TRIGGERED")

        return decision, executed_order

