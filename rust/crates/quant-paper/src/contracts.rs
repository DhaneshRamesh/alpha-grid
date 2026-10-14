//! Versioned research contracts. Monetary amounts are signed INR nanounits.
//! Unknown estimates remain `None`; paper outcomes never imply real fills.
use quant_core::{DepthEvent, Instrument};
use quant_engine::features::{EngineHealthSnapshot, MarketStateSnapshot, QuantFeatureSnapshot};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const PAPER_SCHEMA_VERSION: u16 = 1;
pub type MoneyNanos = i128;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum DecisionAction {
    Buy,
    SellExit,
    Wait,
    NoTrade,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum OrderSide {
    Buy,
    Sell,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum OrderKind {
    Marketable,
    PassiveLimit,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum FillScenario {
    OptimisticDiagnostic,
    ConservativeBase,
    Adverse,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum PaperOrderStatus {
    Created,
    RiskApproved,
    PaperSubmitted,
    PaperWorking,
    PartiallyFilled,
    Filled,
    Cancelled,
    Expired,
    Rejected,
    UnknownUnresolved,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum EstimateStatus {
    NotTrained,
    InsufficientData,
    ProvisionalResearch,
    Calibrated,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum DecisionReason {
    HypothesisSignal,
    ExitSignal,
    HorizonExpired,
    NoSignal,
    MissingFeature,
    StaleFeed,
    FeedDisconnected,
    DataQuality,
    JournalFailure,
    EngineFailure,
    SpreadTooWide,
    CostExceedsEdge,
    UncalibratedModel,
    RiskLimit,
    Duplicate,
    Halted,
    SessionEnded,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum AttributionCategory {
    PredictionError,
    ExecutionError,
    DataQualityFailure,
    RiskIntervention,
    RegimeShift,
    Unattributable,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DecisionContext {
    pub schema_version: u16,
    pub source_record_index: u64,
    pub source_event_id: String,
    pub instrument: Instrument,
    pub session_id: String,
    pub source_receive_timestamp_ns: i64,
    pub evaluated_at_ns: i64,
    pub market: MarketStateSnapshot,
    pub features: QuantFeatureSnapshot,
    pub health: EngineHealthSnapshot,
    pub depth: Option<DepthEvent>,
    pub journal_healthy: bool,
    pub feed_connected: bool,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct CandidateOpportunity {
    pub schema_version: u16,
    pub hypothesis_id: String,
    pub action: DecisionAction,
    pub horizon_ns: i64,
    pub expires_at_ns: i64,
    pub evidence: Vec<String>,
    pub signal_strength: Option<f64>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ModelEstimate {
    pub schema_version: u16,
    pub model_version: String,
    pub status: EstimateStatus,
    pub expected_gross_edge_nanos: Option<MoneyNanos>,
    pub forecast_uncertainty_nanos: Option<MoneyNanos>,
    pub evaluation_horizon_ns: i64,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExecutionCostEstimate {
    pub schema_version: u16,
    pub fee_schedule_version: String,
    pub provisional: bool,
    pub buy_fees_nanos: MoneyNanos,
    pub sell_fees_nanos: MoneyNanos,
    pub spread_crossing_nanos: MoneyNanos,
    pub base_slippage_nanos: MoneyNanos,
    pub market_impact_nanos: MoneyNanos,
    pub partial_fill_allowance_nanos: MoneyNanos,
    pub adverse_movement_nanos: MoneyNanos,
    pub expected_total_nanos: MoneyNanos,
    pub adverse_total_nanos: MoneyNanos,
    pub expected_net_edge_nanos: Option<MoneyNanos>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct RiskAssessment {
    pub schema_version: u16,
    pub rules_version: String,
    pub approved_for_paper: bool,
    pub reasons: Vec<DecisionReason>,
    pub estimated_max_loss_nanos: Option<MoneyNanos>,
    pub remaining_session_loss_nanos: MoneyNanos,
    pub assessed_at_ns: i64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DecisionRecord {
    pub schema_version: u16,
    pub decision_id: String,
    pub context: DecisionContext,
    pub candidate: CandidateOpportunity,
    pub model: ModelEstimate,
    pub cost: Option<ExecutionCostEstimate>,
    pub risk: RiskAssessment,
    pub policy_version: String,
    pub action: DecisionAction,
    pub reason: DecisionReason,
    pub quantity: Option<u64>,
    pub notional_nanos: Option<MoneyNanos>,
    pub created_at_ns: i64,
    pub expires_at_ns: i64,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct PaperOrderIntent {
    pub schema_version: u16,
    pub order_id: String,
    pub decision_id: String,
    pub entry_order_id: Option<String>,
    pub instrument: Instrument,
    pub side: OrderSide,
    pub kind: OrderKind,
    pub quantity: u64,
    pub limit_price_nanos: Option<i64>,
    pub created_at_ns: i64,
    pub expires_at_ns: i64,
    pub scenario: FillScenario,
    pub assumed_entry_delay_ns: i64,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct PaperOrderEvent {
    pub schema_version: u16,
    pub event_id: String,
    pub source_record_index: u64,
    pub order_id: String,
    pub prior_status: Option<PaperOrderStatus>,
    pub status: PaperOrderStatus,
    pub remaining_quantity: u64,
    pub reason: Option<DecisionReason>,
    pub timestamp_ns: i64,
    pub intent: Option<PaperOrderIntent>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct PaperFill {
    pub schema_version: u16,
    pub fill_id: String,
    pub source_record_index: u64,
    pub order_id: String,
    pub decision_id: String,
    pub instrument: Instrument,
    pub side: OrderSide,
    pub quantity: u64,
    pub price_nanos: i64,
    pub fees_nanos: MoneyNanos,
    pub scenario: FillScenario,
    pub assumptions: Vec<String>,
    pub source_receive_timestamp_ns: i64,
    pub filled_at_ns: i64,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct PaperPosition {
    pub instrument: Instrument,
    pub quantity: u64,
    pub cost_basis_nanos: MoneyNanos,
    pub entry_order_id: String,
    pub opened_at_ns: i64,
    pub last_bid_mark_nanos: Option<i64>,
    pub unrealized_pnl_nanos: Option<MoneyNanos>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct PaperPortfolio {
    pub schema_version: u16,
    pub starting_capital_nanos: MoneyNanos,
    pub cash_nanos: MoneyNanos,
    pub reserved_cash_nanos: MoneyNanos,
    pub positions: BTreeMap<String, PaperPosition>,
    pub realized_pnl_nanos: MoneyNanos,
    pub fees_paid_nanos: MoneyNanos,
    pub marked_equity_nanos: Option<MoneyNanos>,
    pub max_drawdown_nanos: Option<MoneyNanos>,
    pub halted: bool,
    pub halt_reason: Option<DecisionReason>,
    pub updated_at_ns: Option<i64>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct OutcomeAttribution {
    pub schema_version: u16,
    pub categories: Vec<AttributionCategory>,
    pub observed_net_pnl_nanos: Option<MoneyNanos>,
    pub max_favorable_excursion_nanos: Option<MoneyNanos>,
    pub max_adverse_excursion_nanos: Option<MoneyNanos>,
    pub holding_time_ns: Option<i64>,
    pub prediction_error_nanos: Option<MoneyNanos>,
    pub execution_cost_error_nanos: Option<MoneyNanos>,
    pub risk_model_error_nanos: Option<MoneyNanos>,
    pub exit_reason: Option<DecisionReason>,
    pub explanation: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ExperienceRecord {
    pub schema_version: u16,
    pub experience_id: String,
    pub decision: DecisionRecord,
    pub order_ids: Vec<String>,
    pub fill_ids: Vec<String>,
    pub attribution: Option<OutcomeAttribution>,
    pub outcome_is_counterfactual: bool,
    pub recorded_at_ns: i64,
    pub updated_at_ns: Option<i64>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ResearchReward {
    pub schema_version: u16,
    pub net_pnl_nanos: Option<MoneyNanos>,
    pub downside_penalty_nanos: MoneyNanos,
    pub drawdown_penalty_nanos: MoneyNanos,
    pub turnover_penalty_nanos: MoneyNanos,
    pub execution_penalty_nanos: MoneyNanos,
    pub total_nanos: Option<MoneyNanos>,
    pub normalization_capital_nanos: MoneyNanos,
    pub explanation: String,
}

impl ResearchReward {
    pub fn no_trade(capital_nanos: MoneyNanos) -> Self {
        Self {
            schema_version: PAPER_SCHEMA_VERSION,
            net_pnl_nanos: None,
            downside_penalty_nanos: 0,
            drawdown_penalty_nanos: 0,
            turnover_penalty_nanos: 0,
            execution_penalty_nanos: 0,
            total_nanos: None,
            normalization_capital_nanos: capital_nanos,
            explanation: "No observed paper outcome; reward unavailable".into(),
        }
    }
}

/// Future learned models must return an explicit untrained state until validated.
pub trait OpportunityModel {
    fn estimate(&self, context: &DecisionContext, horizon_ns: i64) -> ModelEstimate;
}

pub struct UntrainedModel;
impl OpportunityModel for UntrainedModel {
    fn estimate(&self, _: &DecisionContext, horizon_ns: i64) -> ModelEstimate {
        ModelEstimate {
            schema_version: PAPER_SCHEMA_VERSION,
            model_version: "untrained-v1".into(),
            status: EstimateStatus::NotTrained,
            expected_gross_edge_nanos: None,
            forecast_uncertainty_nanos: None,
            evaluation_horizon_ns: horizon_ns,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn untrained_estimate_and_no_trade_reward_do_not_invent_returns() {
        let estimate = ModelEstimate {
            schema_version: PAPER_SCHEMA_VERSION,
            model_version: "untrained-v1".into(),
            status: EstimateStatus::NotTrained,
            expected_gross_edge_nanos: None,
            forecast_uncertainty_nanos: None,
            evaluation_horizon_ns: 60_000_000_000,
        };
        let encoded = serde_json::to_vec(&estimate).unwrap();
        assert_eq!(
            serde_json::from_slice::<ModelEstimate>(&encoded).unwrap(),
            estimate
        );
        assert!(estimate.expected_gross_edge_nanos.is_none());
        let reward = ResearchReward::no_trade(20_000_000_000_000);
        assert!(reward.total_nanos.is_none());
        assert_eq!(reward.normalization_capital_nanos, 20_000_000_000_000);
    }
}
