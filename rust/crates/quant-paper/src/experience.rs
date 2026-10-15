//! Offline export of auditable decision and outcome records. Never runs in the
//! market-event hot path and never adds modelled fills to rejected decisions.
use crate::contracts::{
    DecisionAction, ExperienceRecord, OutcomeAttribution, PAPER_SCHEMA_VERSION, ResearchReward,
};
use crate::ledger::{LedgerEvent, PaperLedgerReader};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::io;
use std::path::Path;

pub fn load_experiences(path: &Path) -> io::Result<Vec<ExperienceRecord>> {
    let mut reader = PaperLedgerReader::open(path)?;
    let mut experiences = BTreeMap::<String, ExperienceRecord>::new();
    let mut entry_decision_by_order = BTreeMap::<String, String>::new();
    let mut entry_decision_by_exit_order = BTreeMap::<String, String>::new();
    while let Some(record) = reader.next_record()? {
        match record.event {
            LedgerEvent::Decision(decision) => {
                let id = decision.decision_id.clone();
                if experiences.contains_key(&id) {
                    return Err(io::Error::other("duplicate decision in paper ledger"));
                }
                experiences.insert(
                    id.clone(),
                    ExperienceRecord {
                        schema_version: PAPER_SCHEMA_VERSION,
                        experience_id: id,
                        recorded_at_ns: decision.created_at_ns,
                        updated_at_ns: None,
                        decision: *decision,
                        order_ids: Vec::new(),
                        fill_ids: Vec::new(),
                        total_paper_turnover_nanos: None,
                        total_paper_fees_nanos: None,
                        attribution: None,
                        outcome_is_counterfactual: false,
                    },
                );
            }
            LedgerEvent::Order(event) => {
                if let Some(intent) = event.intent {
                    let experience = experiences
                        .get_mut(&intent.decision_id)
                        .ok_or_else(|| io::Error::other("paper order without decision"))?;
                    experience.order_ids.push(intent.order_id.clone());
                    experience.updated_at_ns = Some(event.timestamp_ns);
                    if let Some(entry_order_id) = intent.entry_order_id {
                        if let Some(entry_decision) = entry_decision_by_order.get(&entry_order_id) {
                            entry_decision_by_exit_order
                                .insert(intent.order_id.clone(), entry_decision.clone());
                            if let Some(entry_exp) = experiences.get_mut(entry_decision) {
                                entry_exp.order_ids.push(intent.order_id);
                            }
                        }
                    } else {
                        entry_decision_by_order.insert(intent.order_id, intent.decision_id);
                    }
                }
            }
            LedgerEvent::Fill(fill) => {
                let mut ids = vec![fill.decision_id.clone()];
                if let Some(entry) = entry_decision_by_exit_order.get(&fill.order_id)
                    && entry != &fill.decision_id
                {
                    ids.push(entry.clone());
                }
                for id in ids {
                    let experience = experiences
                        .get_mut(&id)
                        .ok_or_else(|| io::Error::other("paper fill without decision"))?;
                    experience.fill_ids.push(fill.fill_id.clone());
                    let turnover = experience.total_paper_turnover_nanos.get_or_insert(0);
                    *turnover += i128::from(fill.price_nanos) * i128::from(fill.quantity);
                    let fees = experience.total_paper_fees_nanos.get_or_insert(0);
                    *fees += fill.fees_nanos;
                    experience.updated_at_ns = Some(fill.filled_at_ns);
                }
            }
            LedgerEvent::Outcome {
                decision_id,
                attribution,
                timestamp_ns,
            } => {
                let experience = experiences
                    .get_mut(&decision_id)
                    .ok_or_else(|| io::Error::other("paper outcome without decision"))?;
                experience.updated_at_ns = Some(timestamp_ns);
                experience.attribution = Some(attribution);
            }
            LedgerEvent::Halt { .. } | LedgerEvent::Resume { .. } => {}
        }
    }
    Ok(experiences.into_values().collect())
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct RewardConfig {
    /// Dimensionless parts per million applied to explicit INR-nanounit inputs.
    pub downside_penalty_ppm: u32,
    pub drawdown_penalty_ppm: u32,
    pub turnover_penalty_ppm: u32,
    pub execution_fee_penalty_ppm: u32,
}

pub fn reward_for_experience(
    experience: &ExperienceRecord,
    capital_nanos: i128,
    config: &RewardConfig,
) -> ResearchReward {
    if experience.decision.action == DecisionAction::NoTrade
        || experience
            .attribution
            .as_ref()
            .and_then(|o| o.observed_net_pnl_nanos)
            .is_none()
    {
        return ResearchReward::no_trade(capital_nanos);
    }
    let outcome: &OutcomeAttribution = experience.attribution.as_ref().expect("checked");
    let pnl = outcome.observed_net_pnl_nanos.expect("checked");
    let downside = outcome
        .max_adverse_excursion_nanos
        .unwrap_or(0)
        .saturating_neg()
        .max(0)
        * i128::from(config.downside_penalty_ppm)
        / 1_000_000;
    let drawdown = outcome
        .max_adverse_excursion_nanos
        .unwrap_or(0)
        .saturating_neg()
        .max(0)
        * i128::from(config.drawdown_penalty_ppm)
        / 1_000_000;
    let turnover = experience.total_paper_turnover_nanos.unwrap_or(0)
        * i128::from(config.turnover_penalty_ppm)
        / 1_000_000;
    let execution = experience.total_paper_fees_nanos.unwrap_or(0)
        * i128::from(config.execution_fee_penalty_ppm)
        / 1_000_000;
    ResearchReward {
        schema_version: PAPER_SCHEMA_VERSION,
        net_pnl_nanos: Some(pnl),
        downside_penalty_nanos: downside,
        drawdown_penalty_nanos: drawdown,
        turnover_penalty_nanos: turnover,
        execution_penalty_nanos: execution,
        total_nanos: Some(pnl - downside - drawdown - turnover - execution),
        normalization_capital_nanos: capital_nanos,
        explanation:
            "INR nanounits; default extra penalties zero. Net paper P&L already includes fill fees."
                .into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn default_reward_does_not_double_count_fees() {
        assert_eq!(RewardConfig::default().execution_fee_penalty_ppm, 0);
    }
}
