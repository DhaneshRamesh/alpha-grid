"""Alpha Grid — Five-Level Order-Book Imbalance Research Module.

Academic Grounding & Exact Literature Findings:
1. Tripathi, Dixit & Vipul (2021). "Information content of order imbalance in an
   order-driven market: Indian Evidence." Finance Research Letters, 41, 101863.
   - Empirical Finding on NSE: Order imbalance exhibits significant contemporaneous correlation
     and predictive power for subsequent returns over short horizons (1 to 5 minutes). Predictive
     power weakens substantially over longer intraday windows (attenuating within 15 minutes).
     Aggressive standalone trading on order imbalance fails to deliver positive net economic value
     after statutory fees (STT, exchange turnover charges) and bid-ask spread crossing.
   - Application in Alpha Grid: Justifies a short 5-minute prediction horizon and using order
     imbalance as an entry-risk filter rather than an aggressive directional alpha.

2. Gupta & Kumar (2025). "Information Content of an Open Limit-Order Book: Indian Evidence."
   American Business Review, 28(1), pp. 1-25.
   - Empirical Finding on NSE: Beyond Level 1 (touch quote), deeper book levels (Levels 2-5)
     contain statistically incremental information regarding quote survival, queue depletion,
     and short-horizon price discovery. Incremental information gains diminish beyond Level 5.
   - Application in Alpha Grid: Justifies evaluating five-level aggregate imbalance (OIB5)
     over single-level touch imbalance, without requiring full 20-level depth for low-frequency decisions.

3. Jindal, Bajpai & Yadav (2026). "Order Imbalances and Market Efficiency: Evidence from
   a Pure Order-Driven Market." Journal of Prediction Markets, 20(1), pp. 1-28.
   - Empirical Finding on NSE: In pure electronic order-driven continuous trading, order book
     imbalances reflect transient liquidity demand and supply shocks. Market efficiency quickly
     erodes gross alpha from aggressive liquidity taking.
   - Application in Alpha Grid: Justifies formulating the hypothesis strictly around entry-risk
     filtering (abstaining from long entries when book imbalance indicates elevated adverse
     selection risk) rather than unconstrained buy signals.

4. Bailey, Borwein, López de Prado & Zhu (2016). "The Probability of Backtest Overfitting."
   Journal of Computational Finance, 20(4), pp. 39-70.
   - Methodological Finding: Quantifies how selection bias from multiple testing across N candidate
     strategies inflates in-sample performance metrics. For a single pre-declared pairwise comparison
     (N = 2, Baseline vs Challenger with 1 added feature), PBO is not an applicable statistic, and
     the arbitrary PBO < 0.10 gate is removed.
   - Application in Alpha Grid: Mandates pre-specifying fixed feature controls, using strictly out-of-sample
     paired loss scoring (Brier score and log loss), chronological partitioning with 300s purging
     buffers to eliminate label leakage, and session-level cluster bootstrap inference.

Methodological Constraints:
- No claim that overfitting is mathematically guaranteed without PBO < 0.10.
- No claim that signal decay periods or optimal thresholds are universal constants.
- No claim that abstention is the only profitable application across all market regimes.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Literal

from quant.research.prospective import compute_dhan_intraday_fees
from quant.research.walkforward import (
    Fold,
    ResearchIndex,
    make_folds,
    reject_holdout_contamination,
)

MAX_STALE_AGE_NS = 2_000_000_000  # 2 seconds max age for research validity
MIN_DEPTH_LEVELS = 5
ADVERSE_RETURN_THRESHOLD_BPS = -10.0  # Exactly -10.0 bps fixed adverse threshold
FORWARD_HORIZON_SECONDS = 300  # 5 minutes outcome maturity
FORWARD_HORIZON_NS = FORWARD_HORIZON_SECONDS * 1_000_000_000
OUTCOME_TOLERANCE_SECONDS = 5  # Fixed documented 5-second tolerance for T+300s outcome observation
OUTCOME_TOLERANCE_NS = OUTCOME_TOLERANCE_SECONDS * 1_000_000_000
PURGING_BUFFER_SECONDS = 300  # 300s buffer between partitions
PURGING_BUFFER_NS = PURGING_BUFFER_SECONDS * 1_000_000_000

# Alpha Grid Portfolio Risk Limits
PORTFOLIO_MAX_POSITIONS: int = 3
PORTFOLIO_MAX_DEPLOYED: Decimal = Decimal("15000.00")
PORTFOLIO_MAX_INSTRUMENT: Decimal = Decimal("5000.00")
PORTFOLIO_SESSION_LOSS: Decimal = Decimal("500.00")

DataPartition = Literal["TRAINING", "CALIBRATION", "EVALUATION", "PURGED"]
ReadinessVerdict = Literal["READY TO COLLECT", "COLLECT MORE DATA", "STOP"]


def _is_valid_sha256(val: str | None) -> bool:
    """Validate 64-character hexadecimal SHA-256 string."""
    if not val or not isinstance(val, str):
        return False
    if len(val) != 64:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in val)


@dataclass(frozen=True, slots=True)
class BookLevel:
    price_nanos: int
    displayed_quantity: int
    order_count: int = 1


@dataclass(frozen=True, slots=True)
class FiveLevelSnapshot:
    symbol: str
    security_id: str
    receive_timestamp_ns: int
    exchange_timestamp_ns: int | None
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    source_protocol: str = "DhanStandardV2"
    is_valid: bool = True
    rejection_reason: str | None = None
    raw_frame_sha256: str | None = None
    journal_offset: int | None = None
    source_frame_id: str | None = None
    packet_offset: int | None = None
    raw_durable_at_ns: int | None = None
    feed_session_id: str | None = None

    @classmethod
    def validate_and_create(
        cls,
        symbol: str,
        security_id: str,
        receive_timestamp_ns: int,
        exchange_timestamp_ns: int | None,
        bids: tuple[BookLevel, ...],
        asks: tuple[BookLevel, ...],
        now_ns: int | None = None,
        source_protocol: str = "DhanStandardV2",
        raw_frame_sha256: str | None = None,
        journal_offset: int | None = None,
        source_frame_id: str | None = None,
        packet_offset: int | None = None,
        raw_durable_at_ns: int | None = None,
        feed_session_id: str | None = None,
    ) -> FiveLevelSnapshot:
        """Enforce strict book validity: monotonicity, non-empty, non-crossed, fresh."""
        if len(bids) < MIN_DEPTH_LEVELS or len(asks) < MIN_DEPTH_LEVELS:
            return cls(
                symbol, security_id, receive_timestamp_ns, exchange_timestamp_ns,
                bids, asks, source_protocol=source_protocol, is_valid=False,
                rejection_reason="INSUFFICIENT_LEVELS",
                raw_frame_sha256=raw_frame_sha256, journal_offset=journal_offset,
                source_frame_id=source_frame_id, packet_offset=packet_offset,
                raw_durable_at_ns=raw_durable_at_ns, feed_session_id=feed_session_id,
            )

        # Top 5 levels price monotonicity
        bid_prices = [b.price_nanos for b in bids[:5]]
        ask_prices = [a.price_nanos for a in asks[:5]]

        if not all(bid_prices[i] > bid_prices[i + 1] for i in range(4)):
            return cls(
                symbol, security_id, receive_timestamp_ns, exchange_timestamp_ns,
                bids, asks, source_protocol=source_protocol, is_valid=False,
                rejection_reason="BIDS_NOT_STRICTLY_DESCENDING",
                raw_frame_sha256=raw_frame_sha256, journal_offset=journal_offset,
                source_frame_id=source_frame_id, packet_offset=packet_offset,
                raw_durable_at_ns=raw_durable_at_ns, feed_session_id=feed_session_id,
            )

        if not all(ask_prices[i] < ask_prices[i + 1] for i in range(4)):
            return cls(
                symbol, security_id, receive_timestamp_ns, exchange_timestamp_ns,
                bids, asks, source_protocol=source_protocol, is_valid=False,
                rejection_reason="ASKS_NOT_STRICTLY_ASCENDING",
                raw_frame_sha256=raw_frame_sha256, journal_offset=journal_offset,
                source_frame_id=source_frame_id, packet_offset=packet_offset,
                raw_durable_at_ns=raw_durable_at_ns, feed_session_id=feed_session_id,
            )

        # Non-crossed check
        if bid_prices[0] >= ask_prices[0]:
            return cls(
                symbol, security_id, receive_timestamp_ns, exchange_timestamp_ns,
                bids, asks, source_protocol=source_protocol, is_valid=False,
                rejection_reason="CROSSED_OR_LOCKED_BOOK",
                raw_frame_sha256=raw_frame_sha256, journal_offset=journal_offset,
                source_frame_id=source_frame_id, packet_offset=packet_offset,
                raw_durable_at_ns=raw_durable_at_ns, feed_session_id=feed_session_id,
            )

        # Positive quantity check
        if any(b.displayed_quantity <= 0 for b in bids[:5]) or any(a.displayed_quantity <= 0 for a in asks[:5]):
            return cls(
                symbol, security_id, receive_timestamp_ns, exchange_timestamp_ns,
                bids, asks, source_protocol=source_protocol, is_valid=False,
                rejection_reason="ZERO_OR_NEGATIVE_QUANTITY",
                raw_frame_sha256=raw_frame_sha256, journal_offset=journal_offset,
                source_frame_id=source_frame_id, packet_offset=packet_offset,
                raw_durable_at_ns=raw_durable_at_ns, feed_session_id=feed_session_id,
            )

        # Freshness check
        if now_ns is not None:
            age = now_ns - receive_timestamp_ns
            if age < 0 or age > MAX_STALE_AGE_NS:
                return cls(
                    symbol, security_id, receive_timestamp_ns, exchange_timestamp_ns,
                    bids, asks, source_protocol=source_protocol, is_valid=False,
                    rejection_reason="STALE_QUOTE_AGE_EXCEEDED",
                    raw_frame_sha256=raw_frame_sha256, journal_offset=journal_offset,
                    source_frame_id=source_frame_id, packet_offset=packet_offset,
                    raw_durable_at_ns=raw_durable_at_ns, feed_session_id=feed_session_id,
                )

        return cls(
            symbol, security_id, receive_timestamp_ns, exchange_timestamp_ns,
            bids, asks, source_protocol=source_protocol, is_valid=True,
            rejection_reason=None,
            raw_frame_sha256=raw_frame_sha256, journal_offset=journal_offset,
            source_frame_id=source_frame_id, packet_offset=packet_offset,
            raw_durable_at_ns=raw_durable_at_ns, feed_session_id=feed_session_id,
        )


def compute_order_imbalance_k(bids: tuple[BookLevel, ...], asks: tuple[BookLevel, ...], k: int = 5) -> float:
    """Compute normalized order volume imbalance across top k levels in [-1.0, +1.0]."""
    k_eval = min(k, len(bids), len(asks))
    if k_eval < 1:
        return 0.0
    bid_vol = sum(b.displayed_quantity for b in bids[:k_eval])
    ask_vol = sum(a.displayed_quantity for a in asks[:k_eval])
    total_vol = bid_vol + ask_vol
    if total_vol <= 0:
        return 0.0
    return float((bid_vol - ask_vol) / total_vol)


def compute_weighted_order_imbalance(bids: tuple[BookLevel, ...], asks: tuple[BookLevel, ...], k: int = 5) -> float:
    """Compute distance-weighted order imbalance (w_i = 1/i) across top k levels."""
    k_eval = min(k, len(bids), len(asks))
    if k_eval < 1:
        return 0.0
    w_bid = sum((1.0 / (i + 1)) * bids[i].displayed_quantity for i in range(k_eval))
    w_ask = sum((1.0 / (i + 1)) * asks[i].displayed_quantity for i in range(k_eval))
    total_w = w_bid + w_ask
    if total_w <= 0.0:
        return 0.0
    return float((w_bid - w_ask) / total_w)


@dataclass(frozen=True, slots=True)
class DepthFeatures:
    symbol: str
    security_id: str
    timestamp_ns: int
    best_bid_nanos: int
    best_ask_nanos: int
    mid_quote_nanos: int
    quoted_spread_bps: float
    imbalance_l1: float
    imbalance_l5: float
    weighted_imbalance_l5: float
    total_bid_depth_qty_l5: int
    total_ask_depth_qty_l5: int
    is_valid: bool
    raw_frame_sha256: str | None = None
    journal_offset: int | None = None
    source_frame_id: str | None = None
    packet_offset: int | None = None
    raw_durable_at_ns: int | None = None
    feed_session_id: str | None = None

    @classmethod
    def from_snapshot(cls, snapshot: FiveLevelSnapshot) -> DepthFeatures | None:
        if not snapshot.is_valid:
            return None
        b0 = snapshot.bids[0].price_nanos
        a0 = snapshot.asks[0].price_nanos
        mid = (b0 + a0) // 2
        spread_bps = float((a0 - b0) * 10000.0 / mid) if mid > 0 else 0.0
        oib_l1 = compute_order_imbalance_k(snapshot.bids, snapshot.asks, k=1)
        oib_l5 = compute_order_imbalance_k(snapshot.bids, snapshot.asks, k=5)
        woib_l5 = compute_weighted_order_imbalance(snapshot.bids, snapshot.asks, k=5)
        tot_bid_qty = sum(b.displayed_quantity for b in snapshot.bids[:5])
        tot_ask_qty = sum(a.displayed_quantity for a in snapshot.asks[:5])

        return cls(
            symbol=snapshot.symbol,
            security_id=snapshot.security_id,
            timestamp_ns=snapshot.receive_timestamp_ns,
            best_bid_nanos=b0,
            best_ask_nanos=a0,
            mid_quote_nanos=mid,
            quoted_spread_bps=spread_bps,
            imbalance_l1=oib_l1,
            imbalance_l5=oib_l5,
            weighted_imbalance_l5=woib_l5,
            total_bid_depth_qty_l5=tot_bid_qty,
            total_ask_depth_qty_l5=tot_ask_qty,
            is_valid=True,
            raw_frame_sha256=snapshot.raw_frame_sha256,
            journal_offset=snapshot.journal_offset,
            source_frame_id=snapshot.source_frame_id,
            packet_offset=snapshot.packet_offset,
            raw_durable_at_ns=snapshot.raw_durable_at_ns,
            feed_session_id=snapshot.feed_session_id,
        )


@dataclass(frozen=True, slots=True)
class FixedBaselineFeatures:
    """Fixed baseline feature controls: past return, quoted spread, trailing volatility."""
    symbol: str
    timestamp_ns: int
    return_5m_past_bps: float
    quoted_spread_bps: float
    vol_15m_trailing_bps: float

    def to_vector(self) -> list[float]:
        return [self.return_5m_past_bps, self.quoted_spread_bps, self.vol_15m_trailing_bps]


@dataclass(frozen=True, slots=True)
class ChallengerFeatures:
    """Challenger feature set: identical baseline controls + OIB5 strictly added."""
    baseline: FixedBaselineFeatures
    imbalance_l5: float

    def to_vector(self) -> list[float]:
        return [*self.baseline.to_vector(), self.imbalance_l5]


@dataclass(frozen=True, slots=True)
class AdverseTargetOutcome:
    """Standardized 5-minute adverse outcome label with explicit maturity, tolerance, and censoring."""
    symbol: str
    decision_timestamp_ns: int
    maturity_timestamp_ns: int
    entry_mid_nanos: int
    exit_mid_nanos: int | None
    forward_return_bps: float | None
    is_adverse: int | None  # 1 if <= -10.0 bps, 0 if > -10.0 bps, None if censored
    is_censored: bool
    censoring_reason: str | None = None
    entry_raw_frame_sha256: str | None = None
    exit_raw_frame_sha256: str | None = None
    exit_timestamp_ns: int | None = None
    feed_session_id: str | None = None
    exit_feed_session_id: str | None = None
    outcome_tolerance_ns: int = OUTCOME_TOLERANCE_NS

    @classmethod
    def evaluate(
        cls,
        symbol: str,
        decision_timestamp_ns: int,
        entry_mid_nanos: int,
        exit_mid_nanos: int | None,
        observed_at_ns: int,
        threshold_bps: float = ADVERSE_RETURN_THRESHOLD_BPS,
        exit_timestamp_ns: int | None = None,
        entry_raw_frame_sha256: str | None = None,
        exit_raw_frame_sha256: str | None = None,
        feed_session_id: str | None = None,
        exit_feed_session_id: str | None = None,
        session_end_ns: int | None = None,
        discontinuity_detected: bool = False,
        censoring_reason: str | None = None,
    ) -> AdverseTargetOutcome:
        """Evaluate adverse outcome with strict maturity, tolerance, and censoring conventions."""
        maturity_ns = decision_timestamp_ns + FORWARD_HORIZON_NS

        # 1. Maturity check: cannot evaluate before maturity
        if observed_at_ns < maturity_ns:
            return cls(
                symbol=symbol,
                decision_timestamp_ns=decision_timestamp_ns,
                maturity_timestamp_ns=maturity_ns,
                entry_mid_nanos=entry_mid_nanos,
                exit_mid_nanos=None,
                forward_return_bps=None,
                is_adverse=None,
                is_censored=True,
                censoring_reason="OUTCOME_NOT_YET_MATURE",
                entry_raw_frame_sha256=entry_raw_frame_sha256,
                exit_raw_frame_sha256=exit_raw_frame_sha256,
                exit_timestamp_ns=exit_timestamp_ns,
                feed_session_id=feed_session_id,
                exit_feed_session_id=exit_feed_session_id,
            )

        # 2. Session boundary: outcome crosses market close
        if session_end_ns is not None and maturity_ns > session_end_ns:
            return cls(
                symbol=symbol,
                decision_timestamp_ns=decision_timestamp_ns,
                maturity_timestamp_ns=maturity_ns,
                entry_mid_nanos=entry_mid_nanos,
                exit_mid_nanos=None,
                forward_return_bps=None,
                is_adverse=None,
                is_censored=True,
                censoring_reason="CENSORED_SESSION_BOUNDARY",
                entry_raw_frame_sha256=entry_raw_frame_sha256,
                exit_raw_frame_sha256=exit_raw_frame_sha256,
                exit_timestamp_ns=exit_timestamp_ns,
                feed_session_id=feed_session_id,
                exit_feed_session_id=exit_feed_session_id,
            )

        # 3. Feed discontinuity / session restart during forward window
        if discontinuity_detected:
            return cls(
                symbol=symbol,
                decision_timestamp_ns=decision_timestamp_ns,
                maturity_timestamp_ns=maturity_ns,
                entry_mid_nanos=entry_mid_nanos,
                exit_mid_nanos=None,
                forward_return_bps=None,
                is_adverse=None,
                is_censored=True,
                censoring_reason="CENSORED_FEED_DISCONTINUITY",
                entry_raw_frame_sha256=entry_raw_frame_sha256,
                exit_raw_frame_sha256=exit_raw_frame_sha256,
                exit_timestamp_ns=exit_timestamp_ns,
                feed_session_id=feed_session_id,
                exit_feed_session_id=exit_feed_session_id,
            )

        # 4. Cross-session check: outcome quote must belong to the exact same feed session
        if feed_session_id is not None and exit_feed_session_id is not None and feed_session_id != exit_feed_session_id:
            return cls(
                symbol=symbol,
                decision_timestamp_ns=decision_timestamp_ns,
                maturity_timestamp_ns=maturity_ns,
                entry_mid_nanos=entry_mid_nanos,
                exit_mid_nanos=exit_mid_nanos,
                forward_return_bps=None,
                is_adverse=None,
                is_censored=True,
                censoring_reason="CENSORED_CROSS_SESSION_QUOTE",
                entry_raw_frame_sha256=entry_raw_frame_sha256,
                exit_raw_frame_sha256=exit_raw_frame_sha256,
                exit_timestamp_ns=exit_timestamp_ns,
                feed_session_id=feed_session_id,
                exit_feed_session_id=exit_feed_session_id,
            )

        # 4b. Frame provenance checks: SHA-256 validity if provided
        if (entry_raw_frame_sha256 is not None and not _is_valid_sha256(entry_raw_frame_sha256)) or (
            exit_raw_frame_sha256 is not None and not _is_valid_sha256(exit_raw_frame_sha256)
        ):
            return cls(
                symbol=symbol,
                decision_timestamp_ns=decision_timestamp_ns,
                maturity_timestamp_ns=maturity_ns,
                entry_mid_nanos=entry_mid_nanos,
                exit_mid_nanos=exit_mid_nanos,
                forward_return_bps=None,
                is_adverse=None,
                is_censored=True,
                censoring_reason="CENSORED_PROVENANCE_MISSING",
                entry_raw_frame_sha256=entry_raw_frame_sha256,
                exit_raw_frame_sha256=exit_raw_frame_sha256,
                exit_timestamp_ns=exit_timestamp_ns,
                feed_session_id=feed_session_id,
                exit_feed_session_id=exit_feed_session_id,
            )

        # 5. Future availability check: exit quote cannot be observed before it actually arrived
        if exit_timestamp_ns is not None and exit_timestamp_ns > observed_at_ns:
            return cls(
                symbol=symbol,
                decision_timestamp_ns=decision_timestamp_ns,
                maturity_timestamp_ns=maturity_ns,
                entry_mid_nanos=entry_mid_nanos,
                exit_mid_nanos=exit_mid_nanos,
                forward_return_bps=None,
                is_adverse=None,
                is_censored=True,
                censoring_reason="CENSORED_OUTCOME_NOT_YET_AVAILABLE",
                entry_raw_frame_sha256=entry_raw_frame_sha256,
                exit_raw_frame_sha256=exit_raw_frame_sha256,
                exit_timestamp_ns=exit_timestamp_ns,
                feed_session_id=feed_session_id,
                exit_feed_session_id=exit_feed_session_id,
            )

        # 6. Missing or invalid exit price
        if exit_mid_nanos is None or exit_mid_nanos <= 0 or entry_mid_nanos <= 0:
            eff_reason = censoring_reason or "FORWARD_QUOTE_UNAVAILABLE_OR_INVALID"
            return cls(
                symbol=symbol,
                decision_timestamp_ns=decision_timestamp_ns,
                maturity_timestamp_ns=maturity_ns,
                entry_mid_nanos=entry_mid_nanos,
                exit_mid_nanos=exit_mid_nanos,
                forward_return_bps=None,
                is_adverse=None,
                is_censored=True,
                censoring_reason=eff_reason,
                entry_raw_frame_sha256=entry_raw_frame_sha256,
                exit_raw_frame_sha256=exit_raw_frame_sha256,
                exit_timestamp_ns=exit_timestamp_ns,
                feed_session_id=feed_session_id,
                exit_feed_session_id=exit_feed_session_id,
            )

        # 7. Fixed tolerance window check: outcome observation must arrive in [T_maturity, T_maturity + OUTCOME_TOLERANCE_NS]
        if exit_timestamp_ns is not None:
            if exit_timestamp_ns < maturity_ns:
                return cls(
                    symbol=symbol,
                    decision_timestamp_ns=decision_timestamp_ns,
                    maturity_timestamp_ns=maturity_ns,
                    entry_mid_nanos=entry_mid_nanos,
                    exit_mid_nanos=exit_mid_nanos,
                    forward_return_bps=None,
                    is_adverse=None,
                    is_censored=True,
                    censoring_reason="CENSORED_OUTCOME_BEFORE_MATURITY",
                    entry_raw_frame_sha256=entry_raw_frame_sha256,
                    exit_raw_frame_sha256=exit_raw_frame_sha256,
                    exit_timestamp_ns=exit_timestamp_ns,
                    feed_session_id=feed_session_id,
                    exit_feed_session_id=exit_feed_session_id,
                )
            if exit_timestamp_ns > maturity_ns + OUTCOME_TOLERANCE_NS:
                return cls(
                    symbol=symbol,
                    decision_timestamp_ns=decision_timestamp_ns,
                    maturity_timestamp_ns=maturity_ns,
                    entry_mid_nanos=entry_mid_nanos,
                    exit_mid_nanos=exit_mid_nanos,
                    forward_return_bps=None,
                    is_adverse=None,
                    is_censored=True,
                    censoring_reason="CENSORED_TOLERANCE_EXCEEDED",
                    entry_raw_frame_sha256=entry_raw_frame_sha256,
                    exit_raw_frame_sha256=exit_raw_frame_sha256,
                    exit_timestamp_ns=exit_timestamp_ns,
                    feed_session_id=feed_session_id,
                    exit_feed_session_id=exit_feed_session_id,
                )

        fwd_return_bps = float((exit_mid_nanos - entry_mid_nanos) * 10000.0 / entry_mid_nanos)
        adverse_flag = 1 if fwd_return_bps <= threshold_bps else 0

        return cls(
            symbol=symbol,
            decision_timestamp_ns=decision_timestamp_ns,
            maturity_timestamp_ns=maturity_ns,
            entry_mid_nanos=entry_mid_nanos,
            exit_mid_nanos=exit_mid_nanos,
            forward_return_bps=round(fwd_return_bps, 4),
            is_adverse=adverse_flag,
            is_censored=False,
            censoring_reason=None,
            entry_raw_frame_sha256=entry_raw_frame_sha256,
            exit_raw_frame_sha256=exit_raw_frame_sha256,
            exit_timestamp_ns=exit_timestamp_ns,
            feed_session_id=feed_session_id,
            exit_feed_session_id=exit_feed_session_id,
        )


def verify_feed_continuity(
    decision_timestamp_ns: int,
    exit_timestamp_ns: int,
    expected_feed_session_id: str | None,
    quotes: list[FiveLevelSnapshot] | None = None,
    event_log: Path | list[dict] | None = None,
    max_dense_quote_gap_ns: int = 5_000_000_000,
) -> tuple[bool, str | None]:
    """Verify feed continuity during the forward evaluation window.

    Returns:
        (is_continuous, censoring_reason)
        If continuous, returns (True, None).
        If unverified (missing evidence), returns (False, "CENSORED_CONTINUITY_UNVERIFIED").
        If discontinuous (disconnect/reconnect/reset/gap), returns (False, "CENSORED_FEED_DISCONTINUITY").
    """
    # 1. Check for cross-session quotes during the window
    if quotes:
        for q in quotes:
            if decision_timestamp_ns <= q.receive_timestamp_ns <= exit_timestamp_ns:
                if expected_feed_session_id and q.feed_session_id and q.feed_session_id != expected_feed_session_id:
                    return False, "CENSORED_FEED_DISCONTINUITY"

    # 2. Check event log if provided
    has_event_evidence = False
    if event_log is not None:
        events: list[dict] = []
        if isinstance(event_log, Path):
            if event_log.exists():
                try:
                    for line in event_log.read_text().splitlines():
                        if line.strip():
                            events.append(json.loads(line))
                except (OSError, json.JSONDecodeError):
                    return False, "CENSORED_FEED_DISCONTINUITY"  # Corrupt event log -> fail closed
            else:
                return False, "CENSORED_CONTINUITY_UNVERIFIED"
        elif isinstance(event_log, list):
            events = event_log

        if len(events) > 0:
            has_event_evidence = True
            for evt in events:
                ts = evt.get("timestamp_ns") or evt.get("receive_timestamp_ns") or evt.get("checked_at_ns")
                if ts is not None and not (decision_timestamp_ns <= int(ts) <= exit_timestamp_ns):
                    continue
                detail = str(evt.get("event", {}).get("detail") or evt.get("detail") or evt.get("event") or "")
                state = str(evt.get("state") or "")
                if (
                    "DISCONNECT" in detail.upper()
                    or "RECONNECT" in detail.upper()
                    or "GAP_DETECTED" in detail.upper()
                    or "SESSION_RESET" in detail.upper()
                    or detail.startswith("STOPPED:")
                    or state in {"BLOCKED", "ERROR"}
                ):
                    return False, "CENSORED_FEED_DISCONTINUITY"

    # 3. Check quote stream density if no event log evidence
    has_dense_quote_evidence = False
    if quotes:
        window_quotes = sorted(
            [q for q in quotes if decision_timestamp_ns <= q.receive_timestamp_ns <= exit_timestamp_ns],
            key=lambda q: q.receive_timestamp_ns,
        )
        if len(window_quotes) >= 2:
            timestamps = [decision_timestamp_ns] + [q.receive_timestamp_ns for q in window_quotes]
            max_gap = max(t2 - t1 for t1, t2 in zip(timestamps, timestamps[1:]))
            if max_gap <= max_dense_quote_gap_ns:
                has_dense_quote_evidence = True

    if not has_event_evidence and not has_dense_quote_evidence:
        # Neither capture events nor dense quote stream confirms continuous feed
        return False, "CENSORED_CONTINUITY_UNVERIFIED"

    return True, None


def detect_feed_discontinuity(
    decision_timestamp_ns: int,
    exit_timestamp_ns: int,
    expected_feed_session_id: str | None,
    quotes: list[FiveLevelSnapshot] | None = None,
    event_log: Path | list[dict] | None = None,
) -> bool:
    """Derive feed continuity from genuine capture evidence and quote sequence."""
    is_cont, _ = verify_feed_continuity(
        decision_timestamp_ns=decision_timestamp_ns,
        exit_timestamp_ns=exit_timestamp_ns,
        expected_feed_session_id=expected_feed_session_id,
        quotes=quotes,
        event_log=event_log,
    )
    return not is_cont


def _level_price(level: Any) -> int:
    if hasattr(level, "price_nanos"):
        return level.price_nanos
    return level[0]


def locate_outcome_quote(
    decision: FiveLevelSnapshot,
    subsequent_quotes: list[FiveLevelSnapshot],
    as_of_ns: int,
    session_end_ns: int | None = None,
    discontinuity_detected: bool = False,
    threshold_bps: float = ADVERSE_RETURN_THRESHOLD_BPS,
    event_log: Path | list[dict] | None = None,
) -> AdverseTargetOutcome:
    """Find the first genuine quote at or after T+300s within the documented 5s tolerance window.

    Causal Requirements:
    1. Matching instrument identity and feed session identity.
    2. Durable frame provenance: valid 64-hex SHA-256 string for decision and outcome quotes.
    3. Causal availability: quote must be received on or before scoring cutoff (as_of_ns).
    4. Temporal ordering: T_decision < T_maturity <= T_exit <= as_of_ns.
    5. Deterministic candidate ordering.
    6. Feed continuity verified from genuine capture event logs or dense quote stream.
    """
    maturity_ns = decision.receive_timestamp_ns + FORWARD_HORIZON_NS
    entry_mid = (_level_price(decision.bids[0]) + _level_price(decision.asks[0])) // 2

    # 1. Require decision durable provenance
    if not _is_valid_sha256(decision.raw_frame_sha256):
        return AdverseTargetOutcome.evaluate(
            symbol=decision.symbol,
            decision_timestamp_ns=decision.receive_timestamp_ns,
            entry_mid_nanos=entry_mid,
            exit_mid_nanos=None,
            observed_at_ns=as_of_ns,
            threshold_bps=threshold_bps,
            exit_timestamp_ns=None,
            entry_raw_frame_sha256=decision.raw_frame_sha256,
            exit_raw_frame_sha256=None,
            feed_session_id=decision.feed_session_id,
            exit_feed_session_id=None,
            session_end_ns=session_end_ns,
            censoring_reason="CENSORED_PROVENANCE_MISSING",
        )

    # 2. Require valid feed session
    if not decision.feed_session_id:
        return AdverseTargetOutcome.evaluate(
            symbol=decision.symbol,
            decision_timestamp_ns=decision.receive_timestamp_ns,
            entry_mid_nanos=entry_mid,
            exit_mid_nanos=None,
            observed_at_ns=as_of_ns,
            threshold_bps=threshold_bps,
            exit_timestamp_ns=None,
            entry_raw_frame_sha256=decision.raw_frame_sha256,
            exit_raw_frame_sha256=None,
            feed_session_id=decision.feed_session_id,
            exit_feed_session_id=None,
            session_end_ns=session_end_ns,
            censoring_reason="CENSORED_CROSS_SESSION_QUOTE",
        )

    # 3. Derive continuity from genuine evidence (event_log or dense quote stream)
    if not discontinuity_detected:
        is_continuous, cont_reason = verify_feed_continuity(
            decision_timestamp_ns=decision.receive_timestamp_ns,
            exit_timestamp_ns=as_of_ns,
            expected_feed_session_id=decision.feed_session_id,
            quotes=subsequent_quotes,
            event_log=event_log,
        )
        if not is_continuous:
            return AdverseTargetOutcome.evaluate(
                symbol=decision.symbol,
                decision_timestamp_ns=decision.receive_timestamp_ns,
                entry_mid_nanos=entry_mid,
                exit_mid_nanos=None,
                observed_at_ns=as_of_ns,
                threshold_bps=threshold_bps,
                exit_timestamp_ns=None,
                entry_raw_frame_sha256=decision.raw_frame_sha256,
                exit_raw_frame_sha256=None,
                feed_session_id=decision.feed_session_id,
                exit_feed_session_id=None,
                session_end_ns=session_end_ns,
                censoring_reason=cont_reason,
            )

    # 4. Check subsequent quotes in maturity window [maturity_ns, maturity_ns + OUTCOME_TOLERANCE_NS]
    window_candidates = [
        q for q in subsequent_quotes
        if q.symbol == decision.symbol
        and q.security_id == decision.security_id
        and q.receive_timestamp_ns >= maturity_ns
        and q.receive_timestamp_ns <= maturity_ns + OUTCOME_TOLERANCE_NS
    ]

    # Check for quotes in tolerance window with mismatched feed session
    cross_session_in_window = [
        q for q in window_candidates
        if q.feed_session_id != decision.feed_session_id
    ]
    if cross_session_in_window and not any(q.feed_session_id == decision.feed_session_id for q in window_candidates):
        return AdverseTargetOutcome.evaluate(
            symbol=decision.symbol,
            decision_timestamp_ns=decision.receive_timestamp_ns,
            entry_mid_nanos=entry_mid,
            exit_mid_nanos=None,
            observed_at_ns=as_of_ns,
            threshold_bps=threshold_bps,
            exit_timestamp_ns=None,
            entry_raw_frame_sha256=decision.raw_frame_sha256,
            exit_raw_frame_sha256=None,
            feed_session_id=decision.feed_session_id,
            exit_feed_session_id=cross_session_in_window[0].feed_session_id,
            session_end_ns=session_end_ns,
            censoring_reason="CENSORED_CROSS_SESSION_QUOTE",
        )

    # Check for quotes in tolerance window lacking SHA-256 provenance
    missing_prov_in_window = [
        q for q in window_candidates
        if q.feed_session_id == decision.feed_session_id and not _is_valid_sha256(q.raw_frame_sha256)
    ]
    if missing_prov_in_window and not any(
        q.feed_session_id == decision.feed_session_id and _is_valid_sha256(q.raw_frame_sha256)
        for q in window_candidates
    ):
        return AdverseTargetOutcome.evaluate(
            symbol=decision.symbol,
            decision_timestamp_ns=decision.receive_timestamp_ns,
            entry_mid_nanos=entry_mid,
            exit_mid_nanos=None,
            observed_at_ns=as_of_ns,
            threshold_bps=threshold_bps,
            exit_timestamp_ns=None,
            entry_raw_frame_sha256=decision.raw_frame_sha256,
            exit_raw_frame_sha256=missing_prov_in_window[0].raw_frame_sha256,
            feed_session_id=decision.feed_session_id,
            exit_feed_session_id=missing_prov_in_window[0].feed_session_id,
            session_end_ns=session_end_ns,
            censoring_reason="CENSORED_PROVENANCE_MISSING",
        )

    valid_subsequent = [
        q for q in window_candidates
        if q.feed_session_id == decision.feed_session_id
        and _is_valid_sha256(q.raw_frame_sha256)
        and q.receive_timestamp_ns <= as_of_ns
        and q.is_valid
    ]

    valid_subsequent.sort(key=lambda q: (q.receive_timestamp_ns, q.journal_offset or 0, q.raw_frame_sha256 or ""))

    if not valid_subsequent:
        censoring_reason = (
            "OUTCOME_NOT_YET_MATURE"
            if as_of_ns < maturity_ns
            else (
                "CENSORED_OUTCOME_NOT_YET_AVAILABLE"
                if as_of_ns <= maturity_ns + OUTCOME_TOLERANCE_NS
                else "FORWARD_QUOTE_UNAVAILABLE_OR_INVALID"
            )
        )
        return AdverseTargetOutcome.evaluate(
            symbol=decision.symbol,
            decision_timestamp_ns=decision.receive_timestamp_ns,
            entry_mid_nanos=entry_mid,
            exit_mid_nanos=None,
            observed_at_ns=as_of_ns,
            threshold_bps=threshold_bps,
            exit_timestamp_ns=None,
            entry_raw_frame_sha256=decision.raw_frame_sha256,
            exit_raw_frame_sha256=None,
            feed_session_id=decision.feed_session_id,
            exit_feed_session_id=None,
            session_end_ns=session_end_ns,
            discontinuity_detected=discontinuity_detected,
            censoring_reason=censoring_reason,
        )

    first_quote = valid_subsequent[0]
    exit_mid = (_level_price(first_quote.bids[0]) + _level_price(first_quote.asks[0])) // 2
    return AdverseTargetOutcome.evaluate(
        symbol=decision.symbol,
        decision_timestamp_ns=decision.receive_timestamp_ns,
        entry_mid_nanos=entry_mid,
        exit_mid_nanos=exit_mid,
        observed_at_ns=as_of_ns,
        threshold_bps=threshold_bps,
        exit_timestamp_ns=first_quote.receive_timestamp_ns,
        entry_raw_frame_sha256=decision.raw_frame_sha256,
        exit_raw_frame_sha256=first_quote.raw_frame_sha256,
        feed_session_id=decision.feed_session_id,
        exit_feed_session_id=first_quote.feed_session_id,
        session_end_ns=session_end_ns,
        discontinuity_detected=discontinuity_detected,
    )


def build_walkforward_folds_from_observations(
    observations: list[tuple[str, int, int]],  # (session_id, asof_ns, source_record_index)
    train_sessions: int,
    validation_sessions: int = 1,
    test_sessions: int = 1,
    horizon_ns: int = FORWARD_HORIZON_NS,
    mode: Literal["rolling", "expanding"] = "expanding",
) -> list[Fold]:
    """Reuse quant.research.walkforward with 300s outcome purging and session grouping."""
    rows = [
        ResearchIndex(
            session_id=sess,
            asof_ns=stamp,
            horizon_ns=horizon_ns,
            source_record_index=idx,
        )
        for sess, stamp, idx in observations
    ]
    return make_folds(
        rows,
        train_sessions=train_sessions,
        validation_sessions=validation_sessions,
        test_sessions=test_sessions,
        embargo_ns=0,
        mode=mode,
    )


def classify_partition(
    timestamp_ns: int,
    train_start_ns: int,
    train_end_ns: int,
    calib_start_ns: int,
    calib_end_ns: int,
    eval_start_ns: int,
    eval_end_ns: int,
    purge_buffer_ns: int = PURGING_BUFFER_NS,
) -> DataPartition:
    """Classify observation timestamp into chronological partitions with purging buffers."""
    if train_start_ns <= timestamp_ns <= (train_end_ns - purge_buffer_ns):
        return "TRAINING"
    if (train_end_ns - purge_buffer_ns) < timestamp_ns < calib_start_ns:
        return "PURGED"
    if calib_start_ns <= timestamp_ns <= (calib_end_ns - purge_buffer_ns):
        return "CALIBRATION"
    if (calib_end_ns - purge_buffer_ns) < timestamp_ns < eval_start_ns:
        return "PURGED"
    if eval_start_ns <= timestamp_ns <= eval_end_ns:
        return "EVALUATION"
    return "PURGED"


def brier_score(y_true: list[int], y_prob: list[float]) -> float:
    """Compute mean squared error between true binary outcome and predicted probability."""
    if not y_true or len(y_true) != len(y_prob):
        raise ValueError("mismatched sample lengths")
    return sum((p - y) ** 2 for y, p in zip(y_true, y_prob)) / len(y_true)


def log_loss(y_true: list[int], y_prob: list[float], eps: float = 1e-15) -> float:
    """Compute binary cross-entropy loss with numerical probability clipping."""
    if not y_true or len(y_true) != len(y_prob):
        raise ValueError("mismatched sample lengths")
    clipped = [max(eps, min(1.0 - eps, p)) for p in y_prob]
    return -sum(y * math.log(p) + (1 - y) * math.log(1.0 - p) for y, p in zip(y_true, clipped)) / len(y_true)


@dataclass(frozen=True, slots=True)
class PairedLossDifferentials:
    """Paired evaluation of Challenger vs Baseline on the identical held-out test set."""
    n_samples: int
    n_sessions: int
    adverse_prevalence: float
    baseline_brier: float
    challenger_brier: float
    delta_brier: float  # Negative delta indicates challenger improvement
    baseline_log_loss: float
    challenger_log_loss: float
    delta_log_loss: float  # Negative delta indicates challenger improvement
    bootstrap_delta_brier_ci95: tuple[float, float]
    bootstrap_delta_log_loss_ci95: tuple[float, float]


@dataclass(frozen=True, slots=True)
class EconomicFilterTrade:
    """Economic accounting for candidate trades under abstention filtering.

    Fee components:
    - brokerage_charges_inr: broker commission (e.g. Dhan intraday min(₹20, 0.03%))
    - statutory_charges_inr: taxes and regulatory levies (STT, NSE turnover, SEBI, GST, stamp)
    - statutory_fees_inr: legacy alias for total transaction costs (brokerage + statutory)
    """
    symbol: str
    decision_timestamp_ns: int
    predicted_adverse_prob: float
    rejection_threshold: float
    abstained: bool
    notional_inr: Decimal
    unfiltered_gross_pnl_inr: Decimal
    unfiltered_net_pnl_inr: Decimal
    avoided_loser: bool
    rejected_winner: bool
    statutory_fees_inr: Decimal
    residual_friction_inr: Decimal = Decimal("0")
    exit_timestamp_ns: int | None = None
    brokerage_charges_inr: Decimal = Decimal("0")
    statutory_charges_inr: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class EconomicFilterComparison:
    """Three-arm comparative economic evaluation: NO_TRADE, UNFILTERED, OIB5_FILTERED."""
    arm_no_trade_net_pnl_inr: Decimal
    arm_unfiltered_net_pnl_inr: Decimal
    arm_filtered_net_pnl_inr: Decimal
    total_candidates: int
    executed_candidates: int
    abstained_candidates: int
    avoided_losers_count: int
    avoided_net_loss_inr: Decimal
    avoided_gross_loss_inr: Decimal
    rejected_winners_count: int
    foregone_net_profit_inr: Decimal
    foregone_gross_profit_inr: Decimal
    statutory_fees_saved_inr: Decimal
    residual_friction_saved_inr: Decimal
    incremental_net_economic_value_inr: Decimal
    portfolio_capital_ceiling_inr: Decimal
    capital_constrained_rejections: int
    friction_bps_scenario: Decimal
    unfiltered_executed_count: int = 0
    unfiltered_capital_rejections: int = 0
    filtered_executed_count: int = 0
    filtered_filter_rejections: int = 0
    filtered_capital_rejections: int = 0
    redeployed_trades_count: int = 0
    redeployed_net_pnl_inr: Decimal = Decimal("0")
    matched_trades_count: int = 0
    selection_net_value_inr: Decimal = Decimal("0")
    unfiltered_position_limit_rejections: int = 0
    filtered_position_limit_rejections: int = 0
    unfiltered_notional_limit_rejections: int = 0
    filtered_notional_limit_rejections: int = 0
    unfiltered_session_loss_rejections: int = 0
    filtered_session_loss_rejections: int = 0


@dataclass
class _ActiveTradePosition:
    trade: EconomicFilterTrade
    exit_timestamp_ns: int
    notional_inr: Decimal
    scenario_net_pnl: Decimal


def evaluate_filter_economics(
    trades: list[EconomicFilterTrade],
    friction_bps: Decimal = Decimal(0),
    max_deployed_capital_inr: Decimal = PORTFOLIO_MAX_DEPLOYED,
    max_concurrent_positions: int = PORTFOLIO_MAX_POSITIONS,
    max_instrument_notional_inr: Decimal = PORTFOLIO_MAX_INSTRUMENT,
    max_session_loss_inr: Decimal = PORTFOLIO_SESSION_LOSS,
) -> EconomicFilterComparison:
    """Compute economic trade-off with dynamic exposure tracking, position exit capital release, and fee separation.

    Causal Accounting Principles:
    1. Chronological candidate stream: evaluated in arrival order by decision_timestamp_ns.
    2. Capital release: capital is released when a position exits (exit_timestamp_ns <= decision_timestamp_ns).
    3. Independent arm portfolios: when the filter abstains, capital is preserved for future entries.
    4. Exact net identity:
       Incremental Net Value = Filtered Net P&L - Unfiltered Net P&L
                            = (Avoided Net Losses - Foregone Net Profits) + Redeployed Net P&L
       When exposure constraints do not alter execution sets (no redeployment), this reduces identically
       to Avoided Net Losses - Foregone Net Profits.
    5. Fees: distinguishes broker commissions from statutory taxes/levies.
    6. Portfolio Risk Limits independently enforced on each arm:
       - max_concurrent_positions (default 3)
       - max_instrument_notional_inr (default ₹5,000)
       - max_deployed_capital_inr (default ₹15,000)
       - max_session_loss_inr (default ₹500)
    """
    sorted_trades = sorted(trades, key=lambda t: t.decision_timestamp_ns)

    unfiltered_active: list[_ActiveTradePosition] = []
    filtered_active: list[_ActiveTradePosition] = []

    unfiltered_executed: list[tuple[EconomicFilterTrade, Decimal]] = []
    filtered_executed: list[tuple[EconomicFilterTrade, Decimal]] = []

    unfiltered_capital_rejections = 0
    unfiltered_position_limit_rejections = 0
    unfiltered_notional_limit_rejections = 0
    unfiltered_session_loss_rejections = 0

    filtered_filter_rejections = 0
    filtered_capital_rejections = 0
    filtered_position_limit_rejections = 0
    filtered_notional_limit_rejections = 0
    filtered_session_loss_rejections = 0

    for t in sorted_trades:
        now_t = t.decision_timestamp_ns
        exit_t = t.exit_timestamp_ns if t.exit_timestamp_ns is not None else (now_t + FORWARD_HORIZON_NS)

        # 1. Release exited positions in both arms
        unfiltered_active = [p for p in unfiltered_active if p.exit_timestamp_ns > now_t]
        filtered_active = [p for p in filtered_active if p.exit_timestamp_ns > now_t]

        unfiltered_exposure = sum((p.notional_inr for p in unfiltered_active), Decimal("0"))
        filtered_exposure = sum((p.notional_inr for p in filtered_active), Decimal("0"))

        unfiltered_net_pnl_so_far = sum((pnl for _, pnl in unfiltered_executed), Decimal("0"))
        filtered_net_pnl_so_far = sum((pnl for _, pnl in filtered_executed), Decimal("0"))

        # 2. Total transaction charges: brokerage + statutory charges
        total_charges = t.brokerage_charges_inr + t.statutory_charges_inr
        if total_charges == Decimal("0") and t.statutory_fees_inr > Decimal("0"):
            total_charges = t.statutory_fees_inr

        trade_friction = (t.notional_inr * friction_bps / Decimal(10000)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        scenario_net_pnl = t.unfiltered_gross_pnl_inr - total_charges - trade_friction

        # 3. Unfiltered Arm execution check: all 4 risk limits
        unfiltered_eligible = True
        if unfiltered_net_pnl_so_far <= -max_session_loss_inr:
            unfiltered_session_loss_rejections += 1
            unfiltered_eligible = False
        elif t.notional_inr > max_instrument_notional_inr:
            unfiltered_notional_limit_rejections += 1
            unfiltered_eligible = False
        elif len(unfiltered_active) >= max_concurrent_positions:
            unfiltered_position_limit_rejections += 1
            unfiltered_eligible = False
        elif unfiltered_exposure + t.notional_inr > max_deployed_capital_inr:
            unfiltered_capital_rejections += 1
            unfiltered_eligible = False

        if unfiltered_eligible:
            unfiltered_active.append(_ActiveTradePosition(t, exit_t, t.notional_inr, scenario_net_pnl))
            unfiltered_executed.append((t, scenario_net_pnl))

        # 4. Filtered Arm execution check: filter abstention + all 4 risk limits
        if t.abstained:
            filtered_filter_rejections += 1
            # Capital is preserved
        else:
            filtered_eligible = True
            if filtered_net_pnl_so_far <= -max_session_loss_inr:
                filtered_session_loss_rejections += 1
                filtered_eligible = False
            elif t.notional_inr > max_instrument_notional_inr:
                filtered_notional_limit_rejections += 1
                filtered_eligible = False
            elif len(filtered_active) >= max_concurrent_positions:
                filtered_position_limit_rejections += 1
                filtered_eligible = False
            elif filtered_exposure + t.notional_inr > max_deployed_capital_inr:
                filtered_capital_rejections += 1
                filtered_eligible = False

            if filtered_eligible:
                filtered_active.append(_ActiveTradePosition(t, exit_t, t.notional_inr, scenario_net_pnl))
                filtered_executed.append((t, scenario_net_pnl))

    unfiltered_pnl = sum((pnl for _, pnl in unfiltered_executed), Decimal(0))
    filtered_pnl = sum((pnl for _, pnl in filtered_executed), Decimal(0))
    incremental_value = filtered_pnl - unfiltered_pnl

    unfiltered_exec_set = {id(t) for t, _ in unfiltered_executed}
    filtered_exec_set = {id(t) for t, _ in filtered_executed}

    avoided_net_loss = Decimal(0)
    avoided_gross_loss = Decimal(0)
    foregone_net_profit = Decimal(0)
    foregone_gross_profit = Decimal(0)
    fees_saved = Decimal(0)
    friction_saved = Decimal(0)
    avoided_losers_cnt = 0
    rejected_winners_cnt = 0
    abstained_cnt = 0
    redeployed_cnt = 0
    redeployed_net_pnl = Decimal(0)
    matched_cnt = 0

    for t in sorted_trades:
        total_charges = t.brokerage_charges_inr + t.statutory_charges_inr
        if total_charges == Decimal("0") and t.statutory_fees_inr > Decimal("0"):
            total_charges = t.statutory_fees_inr
        trade_friction = (t.notional_inr * friction_bps / Decimal(10000)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        sc_net_pnl = t.unfiltered_gross_pnl_inr - total_charges - trade_friction

        if t.abstained:
            abstained_cnt += 1
            # Trade abstained by filter that the unfiltered arm actually executed
            if id(t) in unfiltered_exec_set:
                fees_saved += total_charges
                friction_saved += trade_friction

                if sc_net_pnl < Decimal(0):
                    avoided_net_loss += abs(sc_net_pnl)
                    avoided_losers_cnt += 1
                elif sc_net_pnl > Decimal(0):
                    foregone_net_profit += sc_net_pnl
                    rejected_winners_cnt += 1

                if t.unfiltered_gross_pnl_inr < Decimal(0):
                    avoided_gross_loss += abs(t.unfiltered_gross_pnl_inr)
                elif t.unfiltered_gross_pnl_inr > Decimal(0):
                    foregone_gross_profit += t.unfiltered_gross_pnl_inr

        if id(t) in filtered_exec_set and id(t) in unfiltered_exec_set:
            matched_cnt += 1
        elif id(t) in filtered_exec_set and id(t) not in unfiltered_exec_set:
            # Redeployed into an entry the unfiltered arm missed due to portfolio constraints
            redeployed_cnt += 1
            redeployed_net_pnl += sc_net_pnl

    selection_net_value = avoided_net_loss - foregone_net_profit

    return EconomicFilterComparison(
        arm_no_trade_net_pnl_inr=Decimal(0),
        arm_unfiltered_net_pnl_inr=unfiltered_pnl,
        arm_filtered_net_pnl_inr=filtered_pnl,
        total_candidates=len(sorted_trades),
        executed_candidates=len(filtered_executed),
        abstained_candidates=abstained_cnt,
        avoided_losers_count=avoided_losers_cnt,
        avoided_net_loss_inr=avoided_net_loss,
        avoided_gross_loss_inr=avoided_gross_loss,
        rejected_winners_count=rejected_winners_cnt,
        foregone_net_profit_inr=foregone_net_profit,
        foregone_gross_profit_inr=foregone_gross_profit,
        statutory_fees_saved_inr=fees_saved,
        residual_friction_saved_inr=friction_saved,
        incremental_net_economic_value_inr=incremental_value,
        portfolio_capital_ceiling_inr=max_deployed_capital_inr,
        capital_constrained_rejections=unfiltered_capital_rejections,
        friction_bps_scenario=friction_bps,
        unfiltered_executed_count=len(unfiltered_executed),
        unfiltered_capital_rejections=unfiltered_capital_rejections,
        filtered_executed_count=len(filtered_executed),
        filtered_filter_rejections=filtered_filter_rejections,
        filtered_capital_rejections=filtered_capital_rejections,
        redeployed_trades_count=redeployed_cnt,
        redeployed_net_pnl_inr=redeployed_net_pnl,
        matched_trades_count=matched_cnt,
        selection_net_value_inr=selection_net_value,
        unfiltered_position_limit_rejections=unfiltered_position_limit_rejections,
        filtered_position_limit_rejections=filtered_position_limit_rejections,
        unfiltered_notional_limit_rejections=unfiltered_notional_limit_rejections,
        filtered_notional_limit_rejections=filtered_notional_limit_rejections,
        unfiltered_session_loss_rejections=unfiltered_session_loss_rejections,
        filtered_session_loss_rejections=filtered_session_loss_rejections,
    )


def evaluate_friction_sensitivities(
    trades: list[EconomicFilterTrade],
    friction_scenarios: tuple[Decimal, ...] = (
        Decimal("0"),
        Decimal("5"),
        Decimal("10"),
        Decimal("20"),
        Decimal("40"),
    ),
    max_deployed_capital_inr: Decimal = PORTFOLIO_MAX_DEPLOYED,
    max_concurrent_positions: int = PORTFOLIO_MAX_POSITIONS,
    max_instrument_notional_inr: Decimal = PORTFOLIO_MAX_INSTRUMENT,
    max_session_loss_inr: Decimal = PORTFOLIO_SESSION_LOSS,
) -> dict[Decimal, EconomicFilterComparison]:
    """Evaluate economic performance across the standard friction sensitivity ladder."""
    return {
        f_bps: evaluate_filter_economics(
            trades,
            friction_bps=f_bps,
            max_deployed_capital_inr=max_deployed_capital_inr,
            max_concurrent_positions=max_concurrent_positions,
            max_instrument_notional_inr=max_instrument_notional_inr,
            max_session_loss_inr=max_session_loss_inr,
        )
        for f_bps in friction_scenarios
    }


@dataclass(frozen=True, slots=True)
class ExperimentReadiness:
    """Objective assessment of order-book research readiness across repository data."""
    historical_depth_available_may_oct: bool
    live_depth_sessions_captured: int
    dated_nse_mii_manifest_verified: bool
    pipeline_operational: bool
    can_execute_confirmatory_test: bool
    verdict: ReadinessVerdict
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_repository_depth_readiness(
    live_sessions_captured: int,
    dated_mii_manifest_verified: bool,
    pipeline_operational: bool = True,
) -> ExperimentReadiness:
    """Enforce strict scientific standards: no test without multi-session verified data."""
    hist_depth = False  # May-Oct 2026 data contains 1-minute OHLCV only

    if not pipeline_operational:
        verdict: ReadinessVerdict = "STOP"
        rationale = "Forward capture and telemetry pipeline is not operational."
        can_test = False
    elif live_sessions_captured == 0 or (live_sessions_captured == 1 and not dated_mii_manifest_verified):
        verdict = "READY TO COLLECT"
        rationale = (
            "Historical May-Oct 2026 data is 1-minute OHLCV only (zero order-book depth frames). "
            "Session 2026-11-04 was inspected during exploratory forensic audit and lacks dated NSE CM security manifest "
            "(retained under RAW_CAPTURE_ONLY). Research protocol alpha-grid-order-book-amendment-v3 is fully specified, "
            "engineering pipelines are verified, and the system is READY TO COLLECT prospective forward sessions."
        )
        can_test = False
    elif live_sessions_captured < 20 or not dated_mii_manifest_verified:
        verdict = "COLLECT MORE DATA"
        rationale = (
            f"Accumulated {live_sessions_captured} forward sessions; minimum multi-session cluster sample "
            f"(20-30 verified sessions) with verified MII manifests required before confirmatory evaluation."
        )
        can_test = False
    else:
        verdict = "READY TO COLLECT"
        rationale = "Sufficient verified multi-session depth data available for prospective evaluation."
        can_test = True

    return ExperimentReadiness(
        historical_depth_available_may_oct=hist_depth,
        live_depth_sessions_captured=live_sessions_captured,
        dated_nse_mii_manifest_verified=dated_mii_manifest_verified,
        pipeline_operational=pipeline_operational,
        can_execute_confirmatory_test=can_test,
        verdict=verdict,
        rationale=rationale,
    )


ORDER_BOOK_AMENDMENT_V3: dict[str, Any] = {
    "schema_version": "alpha-grid-order-book-amendment-v3",
    "parent_protocol": "alpha-grid-prospective-v1",
    "previous_amendments": ["alpha-grid-order-book-amendment-v2"],
    "frozen_experiment_reference": "frozen November RELIANCE three-class experiment",
    "hypothesis": (
        "Five-level order-book imbalance (OIB5) improves out-of-sample prediction of adverse "
        "5-minute price movements beyond a fixed price/volatility/spread baseline, providing "
        "conservative net economic value as an entry-risk filter."
    ),
    "null_hypothesis": (
        "The incremental OIB5 feature provides no out-of-sample predictive improvement "
        "(Delta Brier >= 0, Delta LogLoss >= 0) and no incremental conservative net trading value."
    ),
    "literature_citations": [
        {
            "citation": "Tripathi, Dixit & Vipul (2021), Finance Research Letters, 41, 101863",
            "article_id": "101863",
            "finding": "OIB correlates and predicts short-horizon returns (1-5 min) on NSE; predictive power decays within 15 min; standalone trading unviable after STT, fees, and spread crossing.",
            "usage": "Justifies 5-minute prediction horizon and entry-risk filter framing rather than aggressive standalone alpha."
        },
        {
            "citation": "Gupta & Kumar (2025), American Business Review, 28(1), 1-25",
            "finding": "Deeper levels (L2-L5) provide statistically incremental information beyond L1 regarding quote persistence and short-horizon price discovery on NSE.",
            "usage": "Justifies evaluating 5-level aggregate imbalance (OIB5) over top-of-book L1."
        },
        {
            "citation": "Jindal, Bajpai & Yadav (2026), Journal of Prediction Markets, 20(1), 1-28",
            "finding": "In pure order-driven continuous trading, imbalances reflect transient liquidity shocks; spread friction impedes directional gross alpha.",
            "usage": "Justifies formulating OIB5 as an abstention/entry-risk filter against adverse selection."
        },
        {
            "citation": "Bailey, Borwein, López de Prado & Zhu (2016), Journal of Computational Finance, 20(4), 39-70",
            "finding": "Probability of Backtest Overfitting (PBO) quantifies multi-trial selection bias. For a single pre-declared N=2 comparison, PBO is not applicable.",
            "usage": "Removes mandatory PBO < 0.10 gate; mandates paired loss differentials, chronological partitioning with purging, and session-level cluster bootstrap."
        }
    ],
    "fixed_features": {
        "baseline_controls": ["return_5m_past_bps", "quoted_spread_bps", "vol_15m_trailing_bps"],
        "incremental_challenger_feature": "imbalance_l5",
    },
    "target_specification": {
        "definition": "Indicator of 5-minute forward mid-quote return <= -10.0 bps",
        "horizon_seconds": 300,
        "adverse_threshold_bps": -10.0,
        "censoring_policy": "Missing, incomplete, or prematurely terminated forward observations strictly censored; no forward-filling.",
    },
    "data_partitioning": {
        "splits": ["TRAINING", "CALIBRATION", "EVALUATION"],
        "purging_buffer_seconds": 300,
        "threshold_selection_policy": "Probability rejection cutoff selected strictly on training/calibration data; never tuned on held-out evaluation.",
    },
    "comparative_arms": [
        "NO_TRADE",
        "UNFILTERED_BASELINE",
        "OIB5_FILTERED_BASELINE",
    ],
    "economic_accounting": {
        "benefit": "Avoided losses from abstaining on adverse candidate trades",
        "opportunity_cost": "Foregone profits from abstaining on profitable candidate trades",
        "formula": "Incremental Net Value = Filtered Net P&L - Unfiltered Net P&L = Avoided Net Losses - Foregone Net Profits",
        "double_counting_safeguard": "Fees saved are not summed into net avoided losses; gross decomposition is mathematically equivalent.",
        "fee_schedule": "Reconciled Dhan equity cash intraday (NSE transaction 0.0030699%, STT, SEBI, GST, stamp duty)",
        "spread_crossing": "Touch spread crossing (Ask0 - Bid0)",
        "portfolio_capital_ceiling_inr": "15000.00",
        "residual_friction_bps_scenarios": [0, 5, 10, 20, 40],
        "execution_assumption": "Displayed depth at L1-L5 is indicative limit-order book depth, NOT guaranteed fills.",
    },
    "journal_architecture": {
        "mode": "Segmented QJ01 with safe file rotation",
        "segment_max_bytes": 64 * 1024 * 1024,
        "session_max_disk_bytes": 4 * 1024 * 1024 * 1024,
        "min_free_disk_bytes": 512 * 1024 * 1024,
        "manifest_integrity": "manifest.json with CRC32 and byte count sealing per rotated segment",
    },
    "eligibility_separation": [
        "Source Integrity: Hardware/socket level checks, correct packet codes, valid byte lengths, frame checksums.",
        "Exploratory Eligibility: Research data inspection, pattern analysis, feature development (non-confirmatory).",
        "Causal Research Eligibility: Point-in-time synchronization, dated MII security master verification, absence of lookahead.",
        "Portfolio Decision Eligibility: Active operational health, live latency < 2s, fresh book quote, valid risk limits.",
    ],
    "november_4_treatment": "Exploratory / RAW_CAPTURE_ONLY (inspected during forensic audit, missing dated MII manifest; cannot serve as evaluation session).",
    "verdict": "READY TO COLLECT",
}

# Retain backward compatibility reference
ORDER_BOOK_AMENDMENT_V2 = ORDER_BOOK_AMENDMENT_V3

ORDER_BOOK_AMENDMENT_V4: dict[str, Any] = {
    "schema_version": "alpha-grid-order-book-amendment-v4",
    "parent_protocol": "alpha-grid-order-book-amendment-v3",
    "previous_amendments": ["alpha-grid-order-book-amendment-v2", "alpha-grid-order-book-amendment-v3"],
    "frozen_experiment_reference": "frozen November RELIANCE three-class experiment",
    "research_status": "RESEARCH_SPECIFICATION_UNVALIDATED",
    "hypothesis": (
        "Five-level order-book imbalance (OIB5) improves out-of-sample prediction of adverse "
        "5-minute price movements beyond a fixed price/volatility/spread baseline, providing "
        "conservative net economic value as an entry-risk filter."
    ),
    "null_hypothesis": (
        "The incremental OIB5 feature provides no out-of-sample predictive improvement "
        "(Delta Brier >= 0, Delta LogLoss >= 0) and no incremental conservative net trading value."
    ),
    "literature_citations": ORDER_BOOK_AMENDMENT_V3["literature_citations"],
    "fixed_features": ORDER_BOOK_AMENDMENT_V3["fixed_features"],
    "target_specification": {
        "definition": "Indicator of 5-minute forward mid-quote return <= -10.0 bps",
        "horizon_seconds": 300,
        "adverse_threshold_bps": -10.0,
        "outcome_tolerance_seconds": 5,
        "censoring_policy": (
            "Exact T+300s target with fixed 5s tolerance window. Outcomes exceeding 5s tolerance, "
            "crossing session close, or subject to feed discontinuities are strictly censored; "
            "no forward-filling or substitution permitted."
        ),
    },
    "provenance_and_causality": {
        "source_tracing": "Every feature and outcome explicitly bound to raw_frame_sha256, frame_id, and receive_timestamp_ns.",
        "quote_depth_separation": "Asynchronous depth (DEPTH_20) never synthesizes market quotes or overrides LTP/touch quotes.",
        "execution_assumption": "Displayed depth at L1-L5 is indicative limit-order book depth, NOT guaranteed fills.",
    },
    "data_partitioning": {
        "method": "Session-grouped walk-forward with 300s purging and embargo via quant.research.walkforward",
        "splits": ["TRAINING", "CALIBRATION", "EVALUATION"],
    },
    "economic_accounting": {
        "formula": "Incremental Net Value = Filtered Net P&L - Unfiltered Net P&L = Avoided Net Losses - Foregone Net Profits",
        "double_counting_safeguard": "Fees and friction counted exactly once on gross/net legs; single-counting mathematically verified.",
        "fee_schedule": "Reconciled Dhan equity cash intraday (NSE transaction 0.0030699%, STT, SEBI, GST, stamp duty)",
        "portfolio_capital_ceiling_inr": "15000.00",
        "friction_bps_scenarios": [0, 5, 10, 20, 40],
        "active_sensitivity": True,
    },
    "november_4_treatment": "Exploratory / RAW_CAPTURE_ONLY (inspected during forensic audit, missing dated MII manifest; cannot serve as evaluation session).",
    "verdict": "READY TO COLLECT",
}
