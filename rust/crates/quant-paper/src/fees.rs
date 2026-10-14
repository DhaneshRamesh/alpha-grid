//! Configurable, provisional NSE cash-delivery research tariffs.
use crate::contracts::{ExecutionCostEstimate, MoneyNanos, OrderSide, PAPER_SCHEMA_VERSION};
use serde::{Deserialize, Serialize};

const RATE_DENOMINATOR: i128 = 1_000_000_000;
const PAISE_NANOS: i128 = 10_000_000;
const RUPEE_NANOS: i128 = 1_000_000_000;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct FeeSchedule {
    pub version: String,
    pub source_url: String,
    pub verified_at_utc: String,
    pub provisional: bool,
    pub delivery_brokerage_nanos_per_order: MoneyNanos,
    pub exchange_rate_ppb: i128,
    pub sebi_rate_ppb: i128,
    pub ipft_rate_ppb: i128,
    pub stt_rate_ppb: i128,
    pub buy_stamp_rate_ppb: i128,
    pub gst_rate_ppb: i128,
    pub delivery_dp_sell_nanos_per_instruction: MoneyNanos,
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct FeeBreakdown {
    pub brokerage_nanos: MoneyNanos,
    pub exchange_nanos: MoneyNanos,
    pub sebi_nanos: MoneyNanos,
    pub ipft_nanos: MoneyNanos,
    pub stt_nanos: MoneyNanos,
    pub stamp_nanos: MoneyNanos,
    pub gst_nanos: MoneyNanos,
    pub dp_nanos: MoneyNanos,
    pub total_nanos: MoneyNanos,
}

fn round_up(value: i128, unit: i128) -> i128 {
    ((value + unit - 1) / unit) * unit
}
fn nearest_rupee(value: i128) -> i128 {
    ((value + RUPEE_NANOS / 2) / RUPEE_NANOS) * RUPEE_NANOS
}
fn charge(notional_nanos: i128, rate_ppb: i128) -> i128 {
    round_up(notional_nanos * rate_ppb / RATE_DENOMINATOR, PAISE_NANOS)
}

impl FeeSchedule {
    /// Dhan's published retail NSE equity-delivery rates as inspected 2026-10-14.
    /// Provisional because contract-note aggregation, product classification,
    /// exemptions and future tariff changes are not independently calibrated.
    pub fn dhan_delivery_research() -> Self {
        Self {
            version: "dhan-nse-delivery-research-2026-10-14-v1".into(),
            source_url: "https://dhan.co/pricing/".into(),
            verified_at_utc: "2026-10-14".into(),
            provisional: true,
            delivery_brokerage_nanos_per_order: 0,
            exchange_rate_ppb: 30_699,
            sebi_rate_ppb: 1_000,
            ipft_rate_ppb: 1,
            stt_rate_ppb: 1_000_000,
            buy_stamp_rate_ppb: 150_000,
            gst_rate_ppb: 180_000_000,
            delivery_dp_sell_nanos_per_instruction: 12_500_000_000,
        }
    }

    /// `first_sell_fill` charges the DP instruction once per paper sell order.
    /// Each partial fill's other charges are rounded separately, conservatively.
    pub fn for_fill(
        &self,
        notional_nanos: MoneyNanos,
        side: OrderSide,
        first_sell_fill: bool,
    ) -> FeeBreakdown {
        let exchange = charge(notional_nanos, self.exchange_rate_ppb);
        let sebi = charge(notional_nanos, self.sebi_rate_ppb);
        let ipft = charge(notional_nanos, self.ipft_rate_ppb);
        let brokerage = self.delivery_brokerage_nanos_per_order;
        let stt = nearest_rupee(notional_nanos * self.stt_rate_ppb / RATE_DENOMINATOR);
        let stamp = if side == OrderSide::Buy {
            nearest_rupee(notional_nanos * self.buy_stamp_rate_ppb / RATE_DENOMINATOR)
        } else {
            0
        };
        let dp = if side == OrderSide::Sell && first_sell_fill {
            self.delivery_dp_sell_nanos_per_instruction
        } else {
            0
        };
        let gst = round_up(
            (brokerage + exchange + sebi + ipft + dp) * self.gst_rate_ppb / RATE_DENOMINATOR,
            PAISE_NANOS,
        );
        FeeBreakdown {
            brokerage_nanos: brokerage,
            exchange_nanos: exchange,
            sebi_nanos: sebi,
            ipft_nanos: ipft,
            stt_nanos: stt,
            stamp_nanos: stamp,
            gst_nanos: gst,
            dp_nanos: dp,
            total_nanos: brokerage + exchange + sebi + ipft + stt + stamp + gst + dp,
        }
    }

    pub fn estimate_round_trip(
        &self,
        price_nanos: i64,
        quantity: u64,
        half_spread_nanos: i64,
        slippage_bps: u32,
        impact_bps: u32,
        expected_gross_edge_nanos: Option<MoneyNanos>,
    ) -> ExecutionCostEstimate {
        let notional = i128::from(price_nanos) * i128::from(quantity);
        let buy = self.for_fill(notional, OrderSide::Buy, false).total_nanos;
        let sell = self.for_fill(notional, OrderSide::Sell, true).total_nanos;
        let spread = 2 * i128::from(half_spread_nanos) * i128::from(quantity);
        let slippage = notional * i128::from(slippage_bps) / 10_000;
        let impact = notional * i128::from(impact_bps) / 10_000;
        let partial = slippage / 2;
        let adverse = 2 * slippage + impact;
        let expected = buy + sell + spread + slippage + impact + partial;
        ExecutionCostEstimate {
            schema_version: PAPER_SCHEMA_VERSION,
            fee_schedule_version: self.version.clone(),
            provisional: self.provisional,
            buy_fees_nanos: buy,
            sell_fees_nanos: sell,
            spread_crossing_nanos: spread,
            base_slippage_nanos: slippage,
            market_impact_nanos: impact,
            partial_fill_allowance_nanos: partial,
            adverse_movement_nanos: adverse,
            expected_total_nanos: expected,
            adverse_total_nanos: expected + adverse,
            expected_net_edge_nanos: expected_gross_edge_nanos.map(|gross| gross - expected),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn delivery_fee_components_and_dp_once_are_explicit() {
        let fees = FeeSchedule::dhan_delivery_research();
        let buy = fees.for_fill(20_000_000_000_000, OrderSide::Buy, false);
        let sell = fees.for_fill(20_000_000_000_000, OrderSide::Sell, true);
        let later = fees.for_fill(1_000_000_000_000, OrderSide::Sell, false);
        assert_eq!(buy.brokerage_nanos, 0);
        assert_eq!(buy.stt_nanos, 20_000_000_000);
        assert_eq!(buy.stamp_nanos, 3_000_000_000);
        assert_eq!(sell.stamp_nanos, 0);
        assert_eq!(sell.dp_nanos, 12_500_000_000);
        assert_eq!(later.dp_nanos, 0);
        assert!(buy.total_nanos > buy.stt_nanos + buy.stamp_nanos);
        let estimate = fees.estimate_round_trip(100_000_000_000, 10, 50_000_000, 5, 2, None);
        assert!(estimate.expected_net_edge_nanos.is_none());
        assert!(estimate.adverse_total_nanos > estimate.expected_total_nanos);
        assert!(estimate.provisional);
    }
}
