//! Predeclared snapshot hypothesis; its threshold is not an expected return.
use crate::contracts::{
    CandidateOpportunity, DecisionAction, DecisionContext, DecisionReason, PAPER_SCHEMA_VERSION,
};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum PolicyMode {
    AlwaysNoTrade,
    SimpleSignal,
    CostAwareSignal,
    RiskFilteredSignal,
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
