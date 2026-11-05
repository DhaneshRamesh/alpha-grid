"""Alpha Grid — Prospective Statistical Edge Measurement Protocol.

Defines the frozen prospective evaluation framework:
- Reconciled Dhan/NSE cash intraday fee schedule (including current 0.00297% NSE txn rate).
- Accurate position-size scaling (₹5,000, ₹15,000, ₹100,000).
- Explicit separation of observed quoted spread from residual friction (no double-counting).
- Four pre-declared comparative arms: NO TRADE, Simple ORB, Matched Random, Price/Volume.
- Session-level block uncertainty and minimum evidence criteria.
- No live trading, broker orders, or QP01 mutations.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal

# Current verified NSE Cash Market transaction charge per dated Dhan tariff: 0.0030699% (₹3.0699 per lakh)
NSE_CASH_TRANSACTION_RATE = Decimal("0.000030699")
SEBI_TURNOVER_RATE = Decimal("0.000001")          # ₹10 per crore
IPFT_TURNOVER_RATE = Decimal("0.000000001")       # ₹0.01 per crore
STT_SELL_RATE = Decimal("0.00025")                # 0.025% on sell leg
STAMP_DUTY_BUY_RATE = Decimal("0.00003")          # 0.003% on buy leg
BROKERAGE_PCT = Decimal("0.0003")                 # 0.03% capped at ₹20/order
BROKERAGE_CAP = Decimal("20.00")
GST_RATE = Decimal("0.18")                        # 18% on (brokerage + txn + sebi + ipft)

ComparativeArm = Literal["NO_TRADE", "SIMPLE_ORB", "MATCHED_RANDOM", "PRICE_VOLUME"]


def _paise(value: Decimal) -> Decimal:
    """Quantize to nearest paisa (₹0.01) with half-up rounding."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _whole(value: Decimal) -> Decimal:
    """Quantize to nearest integer rupee (₹1) with half-up rounding (for STT/stamp duty)."""
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class FeeBreakdown:
    brokerage_buy: Decimal
    brokerage_sell: Decimal
    total_brokerage: Decimal
    stt_sell: Decimal
    nse_transaction: Decimal
    sebi_turnover: Decimal
    ipft: Decimal
    gst: Decimal
    stamp_duty_buy: Decimal
    total_statutory_fees: Decimal
    fee_burden_bps: Decimal

    def to_dict(self) -> dict[str, str]:
        return {
            "brokerage_buy_inr": str(self.brokerage_buy),
            "brokerage_sell_inr": str(self.brokerage_sell),
            "total_brokerage_inr": str(self.total_brokerage),
            "stt_sell_inr": str(self.stt_sell),
            "nse_transaction_inr": str(self.nse_transaction),
            "sebi_turnover_inr": str(self.sebi_turnover),
            "ipft_inr": str(self.ipft),
            "gst_inr": str(self.gst),
            "stamp_duty_buy_inr": str(self.stamp_duty_buy),
            "total_statutory_fees_inr": str(self.total_statutory_fees),
            "fee_burden_bps": str(self.fee_burden_bps),
        }


def compute_dhan_intraday_fees(buy_notional: Decimal, sell_notional: Decimal) -> FeeBreakdown:
    """Calculate exact published Dhan equity cash intraday fees and statutory levies."""
    turnover = buy_notional + sell_notional
    b_buy = _paise(min(BROKERAGE_CAP, buy_notional * BROKERAGE_PCT))
    b_sell = _paise(min(BROKERAGE_CAP, sell_notional * BROKERAGE_PCT))
    tot_brokerage = b_buy + b_sell

    stt = _whole(sell_notional * STT_SELL_RATE)
    stamp = _whole(buy_notional * STAMP_DUTY_BUY_RATE)
    txn = _paise(turnover * NSE_CASH_TRANSACTION_RATE)
    sebi = _paise(turnover * SEBI_TURNOVER_RATE)
    ipft = _paise(turnover * IPFT_TURNOVER_RATE)
    gst = _paise((tot_brokerage + txn + sebi + ipft) * GST_RATE)

    tot_fees = tot_brokerage + stt + txn + sebi + ipft + gst + stamp
    base = buy_notional if buy_notional > 0 else sell_notional
    bps = (tot_fees * Decimal(10000) / base) if base > 0 else Decimal(0)

    return FeeBreakdown(
        brokerage_buy=b_buy,
        brokerage_sell=b_sell,
        total_brokerage=tot_brokerage,
        stt_sell=stt,
        nse_transaction=txn,
        sebi_turnover=sebi,
        ipft=ipft,
        gst=gst,
        stamp_duty_buy=stamp,
        total_statutory_fees=tot_fees,
        fee_burden_bps=bps.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
    )


@dataclass(frozen=True, slots=True)
class ExecutionAttribution:
    """Rigorous attribution of trade economics separating spread from residual friction."""
    symbol: str
    buy_shares: int
    entry_mid_price: Decimal
    exit_mid_price: Decimal
    observed_entry_ask: Decimal
    observed_exit_bid: Decimal
    buy_notional: Decimal
    sell_notional: Decimal
    mid_to_mid_gross: Decimal
    observed_spread_cost: Decimal
    touch_to_touch_gross: Decimal
    statutory_fees: Decimal
    residual_friction_bps: Decimal
    residual_friction_cost: Decimal
    conservative_net_pnl: Decimal

    @classmethod
    def from_touch_execution(
        cls,
        symbol: str,
        shares: int,
        entry_bid: Decimal,
        entry_ask: Decimal,
        exit_bid: Decimal,
        exit_ask: Decimal,
        residual_friction_bps: Decimal = Decimal(0),
    ) -> ExecutionAttribution:
        """Construct attribution without double-counting quoted spread."""
        entry_mid = (entry_bid + entry_ask) / Decimal(2)
        exit_mid = (exit_bid + exit_ask) / Decimal(2)

        buy = entry_ask * shares
        sell = exit_bid * shares

        mid_buy = entry_mid * shares
        mid_sell = exit_mid * shares
        mid_gross = _paise(mid_sell - mid_buy)

        spread_entry = (entry_ask - entry_mid) * shares
        spread_exit = (exit_mid - exit_bid) * shares
        total_spread = _paise(spread_entry + spread_exit)

        touch_gross = _paise(sell - buy)  # already equals mid_gross - total_spread
        fees = compute_dhan_intraday_fees(buy, sell)

        residual_cost = _paise(buy * residual_friction_bps / Decimal(10000))
        net = _paise(touch_gross - fees.total_statutory_fees - residual_cost)

        return cls(
            symbol=symbol,
            buy_shares=shares,
            entry_mid_price=entry_mid,
            exit_mid_price=exit_mid,
            observed_entry_ask=entry_ask,
            observed_exit_bid=exit_bid,
            buy_notional=buy,
            sell_notional=sell,
            mid_to_mid_gross=mid_gross,
            observed_spread_cost=total_spread,
            touch_to_touch_gross=touch_gross,
            statutory_fees=fees.total_statutory_fees,
            residual_friction_bps=residual_friction_bps,
            residual_friction_cost=residual_cost,
            conservative_net_pnl=net,
        )


@dataclass(frozen=True, slots=True)
class ProspectiveSpecification:
    """Predeclared specification for forward edge verification."""
    schema_version: str = "alpha-grid-prospective-v1"
    primary_metric: str = "session_bootstrap_95pct_mean_daily_net_low_nanos"
    min_sessions_required: int = 30
    session_loss_limit_inr: Decimal = Decimal("500.00")
    experiment_drawdown_limit_inr: Decimal = Decimal("2500.00")
    arms: tuple[ComparativeArm, ...] = (
        "NO_TRADE",
        "SIMPLE_ORB",
        "MATCHED_RANDOM",
        "PRICE_VOLUME",
    )
    sensitivity_friction_bps: tuple[Decimal, ...] = (
        Decimal("0"),
        Decimal("5"),
        Decimal("10"),
        Decimal("20"),
        Decimal("40"),
    )
    universe_size: int = 25
    position_sizing_configs: tuple[Decimal, ...] = (
        Decimal("5000"),    # Single portfolio instrument
        Decimal("15000"),   # Total deployed live portfolio ceiling
        Decimal("100000"),  # Research reference benchmark
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "primary_metric": self.primary_metric,
            "min_sessions_required": self.min_sessions_required,
            "session_loss_limit_inr": str(self.session_loss_limit_inr),
            "experiment_drawdown_limit_inr": str(self.experiment_drawdown_limit_inr),
            "comparative_arms": list(self.arms),
            "sensitivity_friction_bps": [str(x) for x in self.sensitivity_friction_bps],
            "universe_size": self.universe_size,
            "position_sizing_configs": [str(x) for x in self.position_sizing_configs],
        }
