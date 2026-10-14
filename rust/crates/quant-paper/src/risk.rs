//! Independent deterministic cash-equity paper risk gate.
use crate::contracts::{
    CandidateOpportunity, DecisionAction, DecisionContext, DecisionReason, EstimateStatus,
    ExecutionCostEstimate, ModelEstimate, MoneyNanos, PAPER_SCHEMA_VERSION, PaperPortfolio,
    RiskAssessment,
};
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RiskConfig {
    pub version: String,
    pub starting_capital_nanos: MoneyNanos,
    pub max_deployed_nanos: MoneyNanos,
    pub max_instrument_notional_nanos: MoneyNanos,
    pub max_positions: usize,
    pub max_loss_per_decision_nanos: MoneyNanos,
    pub max_session_loss_nanos: MoneyNanos,
    pub max_spread_bps: f64,
    pub max_market_age_ns: i64,
    pub max_slippage_bps: u32,
    pub max_return_volatility: f64,
    pub max_order_age_ns: i64,
    pub assumed_downside_move_bps: u32,
    pub allow_uncalibrated_paper: bool,
}

impl Default for RiskConfig {
    fn default() -> Self {
        Self {
            version: "cash-paper-risk-v1".into(),
            starting_capital_nanos: 20_000_000_000_000,
            max_deployed_nanos: 15_000_000_000_000,
            max_instrument_notional_nanos: 5_000_000_000_000,
            max_positions: 3,
            max_loss_per_decision_nanos: 200_000_000_000,
            max_session_loss_nanos: 500_000_000_000,
            max_spread_bps: 25.0,
            max_market_age_ns: 2_000_000_000,
            max_slippage_bps: 20,
            max_return_volatility: 0.01,
            max_order_age_ns: 10_000_000_000,
            assumed_downside_move_bps: 200,
            allow_uncalibrated_paper: true,
        }
    }
}

pub struct RiskInput<'a> {
    pub context: &'a DecisionContext,
    pub candidate: &'a CandidateOpportunity,
    pub model: &'a ModelEstimate,
    pub costs: Option<&'a ExecutionCostEstimate>,
    pub portfolio: &'a PaperPortfolio,
    pub quantity: u64,
    pub notional_nanos: MoneyNanos,
    pub open_order_exists: bool,
    pub evaluation_time_ns: i64,
}

impl RiskConfig {
    pub fn validate(&self) -> Result<(), &'static str> {
        if self.starting_capital_nanos <= 0
            || self.max_deployed_nanos <= 0
            || self.max_deployed_nanos > self.starting_capital_nanos
            || self.max_instrument_notional_nanos <= 0
            || self.max_instrument_notional_nanos > self.max_deployed_nanos
            || self.max_positions == 0
            || self.max_loss_per_decision_nanos <= 0
            || self.max_session_loss_nanos <= 0
            || !self.max_spread_bps.is_finite()
            || self.max_spread_bps <= 0.0
            || self.max_market_age_ns <= 0
            || !self.max_return_volatility.is_finite()
            || self.max_return_volatility <= 0.0
            || self.max_order_age_ns <= 0
            || !(5..=500).contains(&self.max_slippage_bps)
            || self.assumed_downside_move_bps == 0
        {
            return Err("invalid paper risk limits");
        }
        Ok(())
    }

    pub fn assess(&self, input: RiskInput<'_>) -> RiskAssessment {
        let RiskInput {
            context,
            candidate,
            model,
            costs,
            portfolio,
            quantity,
            notional_nanos,
            open_order_exists,
            evaluation_time_ns,
        } = input;
        let mut reasons = Vec::new();
        let entry = candidate.action == DecisionAction::Buy;
        let action_requested = entry || candidate.action == DecisionAction::SellExit;
        if !action_requested {
            reasons.push(DecisionReason::NoSignal);
        }
        if portfolio.halted && entry {
            reasons.push(DecisionReason::Halted);
        }
        if !context.journal_healthy {
            reasons.push(DecisionReason::JournalFailure);
        }
        if !context.feed_connected {
            reasons.push(DecisionReason::FeedDisconnected);
        }
        if context.market.quality.stale
            || context.features.quality.stale
            || evaluation_time_ns.saturating_sub(context.source_receive_timestamp_ns)
                > self.max_market_age_ns
        {
            reasons.push(DecisionReason::StaleFeed);
        }
        if open_order_exists {
            reasons.push(DecisionReason::Duplicate);
        }
        let spread_bps = context.features.relative_spread.map(|v| v * 10_000.0);
        if spread_bps.is_none_or(|v| !v.is_finite() || v > self.max_spread_bps) {
            reasons.push(DecisionReason::SpreadTooWide);
        }
        if context
            .features
            .rolling_return_volatility
            .is_some_and(|v| v > self.max_return_volatility)
        {
            reasons.push(DecisionReason::RiskLimit);
        }
        if model.status != EstimateStatus::Calibrated {
            if self.allow_uncalibrated_paper {
                // Caveat is carried by ModelEstimate; it is not a failed rule.
            } else {
                reasons.push(DecisionReason::UncalibratedModel);
            }
        }
        if let Some(costs) = costs {
            if model
                .expected_gross_edge_nanos
                .is_some_and(|gross| gross <= costs.expected_total_nanos)
            {
                reasons.push(DecisionReason::CostExceedsEdge);
            }
        }
        let estimated_max_loss = (notional_nanos > 0).then(|| {
            notional_nanos * i128::from(self.assumed_downside_move_bps) / 10_000
                + costs.map_or(0, |c| c.adverse_total_nanos)
        });
        if entry {
            let exposure: MoneyNanos = portfolio
                .positions
                .values()
                .map(|p| p.cost_basis_nanos)
                .sum();
            if quantity == 0
                || notional_nanos <= 0
                || notional_nanos > self.max_instrument_notional_nanos
                || exposure + portfolio.reserved_cash_nanos + notional_nanos
                    > self.max_deployed_nanos
                || portfolio.positions.len() >= self.max_positions
                || portfolio.cash_nanos - portfolio.reserved_cash_nanos
                    < notional_nanos + costs.map_or(0, |c| c.buy_fees_nanos)
                || estimated_max_loss.is_none_or(|loss| loss > self.max_loss_per_decision_nanos)
            {
                reasons.push(DecisionReason::RiskLimit);
            }
            let session_pnl = portfolio.realized_pnl_nanos
                + portfolio
                    .positions
                    .values()
                    .filter_map(|p| p.unrealized_pnl_nanos)
                    .sum::<i128>();
            if session_pnl <= -self.max_session_loss_nanos {
                reasons.push(DecisionReason::RiskLimit);
            }
        }
        let remaining = (self.max_session_loss_nanos + portfolio.realized_pnl_nanos).max(0);
        RiskAssessment {
            schema_version: PAPER_SCHEMA_VERSION,
            rules_version: self.version.clone(),
            approved_for_paper: reasons.is_empty(),
            reasons,
            estimated_max_loss_nanos: estimated_max_loss,
            remaining_session_loss_nanos: remaining,
            assessed_at_ns: evaluation_time_ns,
        }
    }
}
