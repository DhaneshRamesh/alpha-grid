//! Predeclared snapshot hypothesis; its threshold is not an expected return.
use crate::contracts::{
    CandidateOpportunity, DecisionAction, DecisionContext, DecisionReason, EstimateStatus,
    ExecutionCostEstimate, ModelEstimate, MoneyNanos, OpportunityModel, PAPER_SCHEMA_VERSION,
};
use crate::fees::FeeSchedule;
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum PolicyMode {
    AlwaysNoTrade,
    SimpleSignal,
    CostAwareSignal,
    RiskFilteredSignal,
    ApprovedMultiHorizon,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct HorizonAssessment {
    pub horizon: String,
    pub horizon_ns: Option<i64>,
    pub approved: bool,
    pub source_receive_timestamp_ns: i64,
    pub model_version: String,
    pub model_status: EstimateStatus,
    pub expected_gross_edge_nanos: Option<MoneyNanos>,
    pub expected_cost_nanos: Option<MoneyNanos>,
    pub adverse_cost_nanos: Option<MoneyNanos>,
    pub uncertainty_nanos: Option<MoneyNanos>,
    pub robust_net_edge_nanos: Option<MoneyNanos>,
    pub reason: DecisionReason,
}

/// One selection rule for 30m, 60m and the verified remainder of the session.
/// Unknown or uncalibrated edge is an abstention, never a zero-cost estimate.
pub fn evaluate_horizons(
    policy: &PolicyConfig,
    context: &DecisionContext,
    approved: bool,
    session_close_ns: Option<i64>,
    quantity: u64,
    fees: &FeeSchedule,
    model: &dyn OpportunityModel,
) -> (
    Vec<HorizonAssessment>,
    Option<(CandidateOpportunity, ModelEstimate, ExecutionCostEstimate)>,
) {
    let now = context.source_receive_timestamp_ns;
    let horizons = [
        ("30m", Some(1_800_000_000_000_i64)),
        ("60m", Some(3_600_000_000_000_i64)),
        (
            "intraday",
            session_close_ns
                .map(|end| end.saturating_sub(now))
                .filter(|n| *n > 0),
        ),
    ];
    let (base, base_reason) = policy.candidate(context, 0, None, PolicyMode::RiskFilteredSignal);
    let mut assessments = Vec::with_capacity(3);
    let mut best = None;
    let mut best_edge = 0_i128;
    for (label, horizon_ns) in horizons {
        let valid_horizon = horizon_ns.filter(|h| {
            *h > 0 && session_close_ns.is_some_and(|end| now.saturating_add(*h) <= end)
        });
        let model_estimate = model.estimate(context, valid_horizon.unwrap_or(0));
        let reference = context
            .depth
            .as_ref()
            .map(|d| d.asks[0].price_nanos)
            .unwrap_or(0);
        let half_spread = context
            .depth
            .as_ref()
            .map(|d| (d.asks[0].price_nanos - d.bids[0].price_nanos).max(0) / 2)
            .unwrap_or(0);
        let cost = (reference > 0 && quantity > 0).then(|| {
            fees.estimate_round_trip(
                reference,
                quantity,
                half_spread,
                5,
                2,
                model_estimate.expected_gross_edge_nanos,
            )
        });
        let quality_ok = !context.market.quality.stale
            && !context.features.quality.stale
            && !context.market.quality.source_time_unverified
            && !context.features.quality.source_time_unverified
            && !context.market.quality.inconsistent_last_trade_quantity
            && !context.features.quality.inconsistent_last_trade_quantity
            && !context.market.quality.volume_interval_incomplete
            && !context.market.quality.cross_feed_uncertain
            && context.market.processing_timestamp_ns >= now
            && context.features.processing_timestamp_ns >= now
            && context.feed_connected
            && context.journal_healthy;
        let robust_edge = model_estimate
            .expected_gross_edge_nanos
            .zip(model_estimate.forecast_uncertainty_nanos)
            .zip(cost.as_ref())
            .and_then(|((gross, uncertainty), cost)| {
                (uncertainty >= 0).then_some(gross - cost.adverse_total_nanos - uncertainty)
            });
        let reason = if !approved {
            DecisionReason::ApprovalRequired
        } else if valid_horizon.is_none() {
            DecisionReason::SessionCloseUnverified
        } else if !quality_ok {
            DecisionReason::DataQuality
        } else if base.action != DecisionAction::Buy {
            base_reason
        } else if model_estimate.status != EstimateStatus::Calibrated || robust_edge.is_none() {
            DecisionReason::UncalibratedModel
        } else if robust_edge.is_some_and(|edge| edge <= 0) {
            DecisionReason::CostExceedsEdge
        } else {
            DecisionReason::HypothesisSignal
        };
        if reason == DecisionReason::HypothesisSignal {
            let edge = robust_edge.expect("positive edge checked");
            if edge > best_edge {
                best_edge = edge;
                let mut candidate = base.clone();
                candidate.horizon_ns = valid_horizon.expect("horizon checked");
                candidate.expires_at_ns = now.saturating_add(candidate.horizon_ns);
                candidate.evidence.push(format!("selected {label}; robust edge after adverse cost and uncertainty={edge} INR nanounits"));
                best = Some((
                    candidate,
                    model_estimate.clone(),
                    cost.clone().expect("cost checked"),
                ));
            }
        }
        assessments.push(HorizonAssessment {
            horizon: label.into(),
            horizon_ns: valid_horizon,
            approved,
            source_receive_timestamp_ns: now,
            model_version: model_estimate.model_version,
            model_status: model_estimate.status,
            expected_gross_edge_nanos: model_estimate.expected_gross_edge_nanos,
            expected_cost_nanos: cost.as_ref().map(|c| c.expected_total_nanos),
            adverse_cost_nanos: cost.as_ref().map(|c| c.adverse_total_nanos),
            uncertainty_nanos: model_estimate.forecast_uncertainty_nanos,
            robust_net_edge_nanos: robust_edge,
            reason,
        });
    }
    (assessments, best)
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PolicyConfig {
    pub version: String,
    pub min_positive_ofi_quantity: i64,
    pub min_microprice_displacement_bps: f64,
    pub min_depth_imbalance: f64,
    pub max_hold_ns: i64,
    pub horizon_ns: i64,
    pub quantity: u64,
}

impl Default for PolicyConfig {
    fn default() -> Self {
        Self {
            version: "snapshot-hypothesis-v1".into(),
            min_positive_ofi_quantity: 1,
            min_microprice_displacement_bps: 0.05,
            min_depth_imbalance: 0.10,
            max_hold_ns: 30_000_000_000,
            horizon_ns: 60_000_000_000,
            quantity: 8,
        }
    }
}

impl PolicyConfig {
    pub fn validate(&self) -> Result<(), &'static str> {
        if self.min_positive_ofi_quantity < 0
            || !self.min_microprice_displacement_bps.is_finite()
            || self.min_microprice_displacement_bps < 0.0
            || !self.min_depth_imbalance.is_finite()
            || !(0.0..=1.0).contains(&self.min_depth_imbalance)
            || self.max_hold_ns <= 0
            || self.horizon_ns <= 0
            || self.quantity == 0
        {
            return Err("invalid policy thresholds");
        }
        Ok(())
    }

    pub fn candidate(
        &self,
        context: &DecisionContext,
        held_quantity: u64,
        held_since_ns: Option<i64>,
        mode: PolicyMode,
    ) -> (CandidateOpportunity, DecisionReason) {
        let mut action = DecisionAction::NoTrade;
        let mut reason = DecisionReason::NoSignal;
        let mut evidence =
            vec!["Uncalibrated snapshot OFI/microprice hypothesis; no forecasted return".into()];
        let mut strength = None;
        if mode != PolicyMode::AlwaysNoTrade {
            let f = &context.features;
            let m = &context.market;
            if !context.feed_connected || !context.journal_healthy {
                reason = if !context.journal_healthy {
                    DecisionReason::JournalFailure
                } else {
                    DecisionReason::FeedDisconnected
                };
            } else if m.quality.stale || f.quality.stale {
                reason = DecisionReason::StaleFeed;
            } else if m.quality.volume_interval_incomplete || m.incremental_volume.is_none() {
                reason = DecisionReason::MissingFeature;
            } else if held_quantity > 0 {
                let aged = held_since_ns.is_some_and(|since| {
                    context.source_receive_timestamp_ns.saturating_sub(since) >= self.max_hold_ns
                });
                if f.snapshot_ofi_quantity
                    .is_some_and(|ofi| ofi < -self.min_positive_ofi_quantity)
                    || aged
                {
                    action = DecisionAction::SellExit;
                    reason = if aged {
                        DecisionReason::HorizonExpired
                    } else {
                        DecisionReason::ExitSignal
                    };
                } else {
                    action = DecisionAction::Wait;
                }
            } else if let (Some(ofi), Some(micro), Some(mid), Some(imbalance)) = (
                f.snapshot_ofi_quantity,
                f.microprice_nanos,
                f.midprice_nanos,
                f.bid_ask_imbalance,
            ) {
                if mid > 0 {
                    let displacement_bps = (micro - mid) as f64 * 10_000.0 / mid as f64;
                    evidence.push(format!("snapshot OFI={ofi}; microprice displacement={displacement_bps:.3} bps; depth imbalance={imbalance:.3}"));
                    if ofi >= self.min_positive_ofi_quantity
                        && displacement_bps >= self.min_microprice_displacement_bps
                        && imbalance >= self.min_depth_imbalance
                    {
                        action = DecisionAction::Buy;
                        reason = DecisionReason::HypothesisSignal;
                        strength = Some(displacement_bps);
                    }
                }
            } else {
                reason = DecisionReason::MissingFeature;
            }
        }
        (
            CandidateOpportunity {
                schema_version: PAPER_SCHEMA_VERSION,
                hypothesis_id: self.version.clone(),
                action,
                horizon_ns: self.horizon_ns,
                expires_at_ns: context
                    .source_receive_timestamp_ns
                    .saturating_add(self.horizon_ns),
                evidence,
                signal_strength: strength,
            },
            reason,
        )
    }
}
