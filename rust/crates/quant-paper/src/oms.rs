//! Offline paper OMS. Every accepted transition is journaled and fsynced before
//! it is applied to the in-memory portfolio or sent to telemetry.
use crate::contracts::*;
use crate::fees::FeeSchedule;
use crate::ledger::{LedgerEvent, LedgerRecord, PaperLedgerReader, PaperLedgerWriter};
use crate::policy::{PolicyConfig, PolicyMode};
use crate::risk::{RiskConfig, RiskInput};
use quant_core::{DepthEvent, MarketEvent};
use quant_engine::pipeline::EngineOutput;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::io;
use std::path::Path;

const RECENT_LIMIT: usize = 100;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct PaperOrderView {
    pub intent: PaperOrderIntent,
    pub status: PaperOrderStatus,
    pub remaining_quantity: u64,
    pub filled_quantity: u64,
    pub last_transition_ns: i64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PaperSnapshot {
    pub schema_version: u16,
    pub mode: String,
    pub portfolio: PaperPortfolio,
    pub recent_decisions: Vec<DecisionRecord>,
    pub orders: Vec<PaperOrderView>,
    pub recent_fills: Vec<PaperFill>,
    pub recent_outcomes: Vec<(String, OutcomeAttribution)>,
    pub last_source_record_index: u64,
    pub ledger_recovered_tail_bytes: u64,
    pub ledger_healthy: bool,
    pub fee_schedule_version: String,
    pub risk_rules_version: String,
    pub policy_version: String,
}

fn key(instrument: &quant_core::Instrument) -> String {
    format!(
        "{}:{}:{}",
        instrument.venue, instrument.exchange_segment, instrument.security_id
    )
}
fn push_bounded<T>(items: &mut VecDeque<T>, value: T) {
    items.push_back(value);
    while items.len() > RECENT_LIMIT {
        items.pop_front();
    }
}

struct OrderState {
    view: PaperOrderView,
    cumulative_notional_nanos: MoneyNanos,
}

struct PositionTrace {
    max_favorable_nanos: MoneyNanos,
    max_adverse_nanos: MoneyNanos,
    realized_before_nanos: MoneyNanos,
    opened_at_ns: i64,
}

pub struct PaperEngine {
    policy: PolicyConfig,
    risk: RiskConfig,
    fees: FeeSchedule,
    mode: PolicyMode,
    ledger: PaperLedgerWriter,
    recovery: Option<PaperLedgerReader>,
    next_recovered: Option<LedgerRecord>,
    recovered_max_index: u64,
    recovered_tail_bytes: u64,
    last_source_index: u64,
    portfolio: PaperPortfolio,
    orders: BTreeMap<String, OrderState>,
    seen_event_ids: BTreeSet<String>,
    seen_fill_ids: BTreeSet<String>,
    recent_decisions: VecDeque<DecisionRecord>,
    recent_orders: VecDeque<PaperOrderView>,
    recent_fills: VecDeque<PaperFill>,
    recent_outcomes: VecDeque<(String, OutcomeAttribution)>,
    entry_decision: BTreeMap<String, String>,
    position_excursions: BTreeMap<String, PositionTrace>,
    equity_peak_nanos: MoneyNanos,
    feed_connected: bool,
    ledger_healthy: bool,
}

impl PaperEngine {
    pub fn open(
        path: &Path,
        policy: PolicyConfig,
        risk: RiskConfig,
        fees: FeeSchedule,
        mode: PolicyMode,
    ) -> io::Result<Self> {
        policy.validate().map_err(io::Error::other)?;
        risk.validate().map_err(io::Error::other)?;
        let ledger = PaperLedgerWriter::open(path)?;
        let recovered_tail_bytes = ledger.recovered_tail_bytes();
        let mut scan = PaperLedgerReader::open(path)?;
        let mut recovered_max_index = 0;
        let mut previous = 0;
        while let Some(record) = scan.next_record()? {
            if record.source_record_index < previous {
                return Err(io::Error::other("paper ledger source index regression"));
            }
            previous = record.source_record_index;
            recovered_max_index = record.source_record_index;
        }
        let mut recovery = PaperLedgerReader::open(path)?;
        let next_recovered = recovery.next_record()?;
        let starting_capital = risk.starting_capital_nanos;
        let portfolio = PaperPortfolio {
            schema_version: PAPER_SCHEMA_VERSION,
            starting_capital_nanos: risk.starting_capital_nanos,
            cash_nanos: risk.starting_capital_nanos,
            reserved_cash_nanos: 0,
            positions: BTreeMap::new(),
            realized_pnl_nanos: 0,
            fees_paid_nanos: 0,
            marked_equity_nanos: Some(risk.starting_capital_nanos),
            max_drawdown_nanos: Some(0),
            halted: false,
            halt_reason: None,
            updated_at_ns: None,
        };
        Ok(Self {
            policy,
            risk,
            fees,
            mode,
            ledger,
            recovery: Some(recovery),
            next_recovered,
            recovered_max_index,
            recovered_tail_bytes,
            last_source_index: 0,
            portfolio,
            orders: BTreeMap::new(),
            seen_event_ids: BTreeSet::new(),
            seen_fill_ids: BTreeSet::new(),
            recent_decisions: VecDeque::new(),
            recent_orders: VecDeque::new(),
            recent_fills: VecDeque::new(),
            recent_outcomes: VecDeque::new(),
            entry_decision: BTreeMap::new(),
            position_excursions: BTreeMap::new(),
            equity_peak_nanos: starting_capital,
            feed_connected: true,
            ledger_healthy: true,
        })
    }

    pub fn snapshot(&self) -> PaperSnapshot {
        let mut orders: Vec<_> = self.orders.values().map(|v| v.view.clone()).collect();
        orders.extend(
            self.recent_orders
                .iter()
                .rev()
                .take(RECENT_LIMIT.saturating_sub(orders.len()))
                .cloned(),
        );
        PaperSnapshot {
            schema_version: PAPER_SCHEMA_VERSION,
            mode: "PAPER_REPLAY".into(),
            portfolio: self.portfolio.clone(),
            recent_decisions: self.recent_decisions.iter().cloned().collect(),
            orders,
            recent_fills: self.recent_fills.iter().cloned().collect(),
            recent_outcomes: self.recent_outcomes.iter().cloned().collect(),
            last_source_record_index: self.last_source_index,
            ledger_recovered_tail_bytes: self.recovered_tail_bytes,
            ledger_healthy: self.ledger_healthy,
            fee_schedule_version: self.fees.version.clone(),
            risk_rules_version: self.risk.version.clone(),
            policy_version: self.policy.version.clone(),
        }
    }

    fn persist(&mut self, index: u64, event: LedgerEvent) -> io::Result<()> {
        let record = LedgerRecord::new(index, event);
        if let Err(error) = self.ledger.append(&record) {
            self.ledger_healthy = false;
            self.portfolio.halted = true;
            self.portfolio.halt_reason = Some(DecisionReason::JournalFailure);
            return Err(error);
        }
        if let Err(error) = self.apply(&record) {
            self.ledger_healthy = false;
            self.portfolio.halted = true;
            self.portfolio.halt_reason = Some(DecisionReason::EngineFailure);
            return Err(error);
        }
        Ok(())
    }

    fn apply(&mut self, record: &LedgerRecord) -> io::Result<()> {
        match &record.event {
            LedgerEvent::Decision(decision) => {
                push_bounded(&mut self.recent_decisions, (**decision).clone());
            }
            LedgerEvent::Order(event) => {
                if !self.seen_event_ids.insert(event.event_id.clone()) {
                    return Ok(());
                }
                if event.status == PaperOrderStatus::Created {
                    let intent = event
                        .intent
                        .clone()
                        .ok_or_else(|| io::Error::other("created order lacks intent"))?;
                    if self.orders.contains_key(&event.order_id) {
                        return Err(io::Error::other("duplicate paper order"));
                    }
                    self.orders.insert(
                        event.order_id.clone(),
                        OrderState {
                            view: PaperOrderView {
                                intent: intent.clone(),
                                status: event.status,
                                remaining_quantity: intent.quantity,
                                filled_quantity: 0,
                                last_transition_ns: event.timestamp_ns,
                            },
                            cumulative_notional_nanos: 0,
                        },
                    );
                    if intent.side == OrderSide::Buy {
                        self.entry_decision
                            .insert(intent.order_id, intent.decision_id);
                    }
                } else {
                    let state = self
                        .orders
                        .get_mut(&event.order_id)
                        .ok_or_else(|| io::Error::other("paper transition missing order"))?;
                    if event.prior_status != Some(state.view.status)
                        || !valid_transition(state.view.status, event.status)
                        || event.remaining_quantity > state.view.remaining_quantity
                    {
                        return Err(io::Error::other("invalid paper order transition"));
                    }
                    state.view.status = event.status;
                    state.view.remaining_quantity = event.remaining_quantity;
                    state.view.last_transition_ns = event.timestamp_ns;
                    if is_terminal(event.status) {
                        let finished = self.orders.remove(&event.order_id).expect("order exists");
                        push_bounded(&mut self.recent_orders, finished.view);
                    }
                }
            }
            LedgerEvent::Fill(fill) => self.apply_fill(fill)?,
            LedgerEvent::Outcome {
                decision_id,
                attribution,
            } => {
                push_bounded(
                    &mut self.recent_outcomes,
                    (decision_id.clone(), attribution.clone()),
                );
            }
            LedgerEvent::Halt { reason, .. } => {
                self.portfolio.halted = true;
                self.portfolio.halt_reason = Some(*reason);
            }
            LedgerEvent::Resume { .. } => {
                self.portfolio.halted = false;
                self.portfolio.halt_reason = None;
            }
        }
        self.recompute_reserve();
        Ok(())
    }

    fn recompute_reserve(&mut self) {
        self.portfolio.reserved_cash_nanos = self
            .orders
            .values()
            .filter(|o| o.view.intent.side == OrderSide::Buy)
            .map(|o| {
                let notional = i128::from(o.view.intent.limit_price_nanos.unwrap_or_default())
                    * i128::from(o.view.remaining_quantity);
                notional
                    + self
                        .fees
                        .for_fill(notional, OrderSide::Buy, false)
                        .total_nanos
            })
            .sum();
    }

    fn apply_fill(&mut self, fill: &PaperFill) -> io::Result<()> {
        if !self.seen_fill_ids.insert(fill.fill_id.clone()) {
            return Ok(());
        }
        let order = self
            .orders
            .get_mut(&fill.order_id)
            .ok_or_else(|| io::Error::other("fill missing paper order"))?;
        if fill.quantity == 0
            || fill.quantity > order.view.remaining_quantity
            || fill.side != order.view.intent.side
            || fill.instrument != order.view.intent.instrument
        {
            return Err(io::Error::other("invalid paper fill"));
        }
        order.view.filled_quantity += fill.quantity;
        order.cumulative_notional_nanos += i128::from(fill.price_nanos) * i128::from(fill.quantity);
        let notional = i128::from(fill.price_nanos) * i128::from(fill.quantity);
        let instrument_key = key(&fill.instrument);
        self.portfolio.fees_paid_nanos += fill.fees_nanos;
        if fill.side == OrderSide::Buy {
            if self.portfolio.cash_nanos < notional + fill.fees_nanos {
                return Err(io::Error::other("paper fill exceeds cash"));
            }
            self.portfolio.cash_nanos -= notional + fill.fees_nanos;
            let position = self
                .portfolio
                .positions
                .entry(instrument_key.clone())
                .or_insert_with(|| PaperPosition {
                    instrument: fill.instrument.clone(),
                    quantity: 0,
                    cost_basis_nanos: 0,
                    entry_order_id: fill.order_id.clone(),
                    opened_at_ns: fill.filled_at_ns,
                    last_bid_mark_nanos: None,
                    unrealized_pnl_nanos: None,
                });
            position.quantity += fill.quantity;
            position.cost_basis_nanos += notional + fill.fees_nanos;
            self.position_excursions
                .entry(instrument_key)
                .or_insert(PositionTrace {
                    max_favorable_nanos: 0,
                    max_adverse_nanos: 0,
                    realized_before_nanos: self.portfolio.realized_pnl_nanos,
                    opened_at_ns: fill.filled_at_ns,
                });
        } else {
            let position = self
                .portfolio
                .positions
                .get_mut(&instrument_key)
                .ok_or_else(|| io::Error::other("paper sell without position"))?;
            if position.quantity < fill.quantity {
                return Err(io::Error::other("paper short sale forbidden"));
            }
            let allocated_basis = if position.quantity == fill.quantity {
                position.cost_basis_nanos
            } else {
                position.cost_basis_nanos * i128::from(fill.quantity)
                    / i128::from(position.quantity)
            };
            position.quantity -= fill.quantity;
            position.cost_basis_nanos -= allocated_basis;
            self.portfolio.cash_nanos += notional - fill.fees_nanos;
            self.portfolio.realized_pnl_nanos += notional - fill.fees_nanos - allocated_basis;
            if position.quantity == 0 {
                self.portfolio.positions.remove(&instrument_key);
            }
        }
        self.portfolio.updated_at_ns = Some(fill.filled_at_ns);
        self.recompute_reserve();
        push_bounded(&mut self.recent_fills, fill.clone());
        Ok(())
    }

    fn order_event(
        &mut self,
        at: (u64, i64),
        order_id: &str,
        status: PaperOrderStatus,
        remaining_quantity: u64,
        reason: Option<DecisionReason>,
        intent: Option<PaperOrderIntent>,
    ) -> io::Result<()> {
        let (index, timestamp_ns) = at;
        let prior = self.orders.get(order_id).map(|v| v.view.status);
        let event = PaperOrderEvent {
            schema_version: PAPER_SCHEMA_VERSION,
            event_id: format!("{order_id}:{status:?}:{index}:{remaining_quantity}"),
            source_record_index: index,
            order_id: order_id.into(),
            prior_status: prior,
            status,
            remaining_quantity,
            reason,
            timestamp_ns,
            intent,
        };
        self.persist(index, LedgerEvent::Order(event))
    }

    pub fn on_market_record(
        &mut self,
        index: u64,
        record: &quant_journal::JournalRecord,
        output: &EngineOutput,
    ) -> io::Result<PaperSnapshot> {
        if index == self.last_source_index {
            return Ok(self.snapshot());
        }
        if index != self.last_source_index + 1 {
            return Err(io::Error::other("paper source index gap"));
        }
        self.last_source_index = index;
        if let MarketEvent::Session(session) = &record.event {
            if matches!(
                session.kind,
                quant_core::MarketSessionKind::Disconnected { .. }
            ) {
                self.feed_connected = false;
            }
        } else if matches!(record.event, MarketEvent::Quote(_) | MarketEvent::Price(_)) {
            self.feed_connected = true;
        }
        if index <= self.recovered_max_index {
            while self
                .next_recovered
                .as_ref()
                .is_some_and(|r| r.source_record_index == index)
            {
                let recovered = self.next_recovered.take().expect("checked");
                self.apply(&recovered)?;
                self.next_recovered = self
                    .recovery
                    .as_mut()
                    .expect("recovery reader")
                    .next_record()?;
            }
            self.mark(record);
            return Ok(self.snapshot());
        }
        self.recovery = None;
        self.next_recovered = None;
        self.mark(record);
        if let MarketEvent::Depth(depth) = &record.event {
            self.expire_and_fill(index, depth)?;
            self.mark(record);
            if let Some(features) = &output.features {
                self.decide(index, record, depth, features)?;
            }
        }
        Ok(self.snapshot())
    }

    fn mark(&mut self, record: &quant_journal::JournalRecord) {
        let MarketEvent::Depth(depth) = &record.event else {
            return;
        };
        let instrument_key = key(&depth.instrument);
        if let Some(position) = self.portfolio.positions.get_mut(&instrument_key) {
            let bid = depth.bids[0].price_nanos;
            if bid > 0 {
                position.last_bid_mark_nanos = Some(bid);
                let liquidation = i128::from(bid) * i128::from(position.quantity);
                let exit_fees = self
                    .fees
                    .for_fill(liquidation, OrderSide::Sell, true)
                    .total_nanos;
                let pnl = liquidation - position.cost_basis_nanos - exit_fees;
                position.unrealized_pnl_nanos = Some(pnl);
                if let Some(excursions) = self.position_excursions.get_mut(&instrument_key) {
                    excursions.max_favorable_nanos = excursions.max_favorable_nanos.max(pnl);
                    excursions.max_adverse_nanos = excursions.max_adverse_nanos.min(pnl);
                }
            }
        }
        if self
            .portfolio
            .positions
            .values()
            .all(|p| p.last_bid_mark_nanos.is_some())
        {
            let equity = self.portfolio.cash_nanos
                + self
                    .portfolio
                    .positions
                    .values()
                    .map(|p| {
                        let gross = i128::from(p.last_bid_mark_nanos.unwrap_or_default())
                            * i128::from(p.quantity);
                        gross - self.fees.for_fill(gross, OrderSide::Sell, true).total_nanos
                    })
                    .sum::<i128>();
            self.equity_peak_nanos = self.equity_peak_nanos.max(equity);
            self.portfolio.marked_equity_nanos = Some(equity);
            let drawdown = self.equity_peak_nanos - equity;
            self.portfolio.max_drawdown_nanos =
                Some(self.portfolio.max_drawdown_nanos.unwrap_or(0).max(drawdown));
        } else {
            self.portfolio.marked_equity_nanos = None;
        }
    }

    fn expire_and_fill(&mut self, index: u64, depth: &DepthEvent) -> io::Result<()> {
        let ids: Vec<_> = self
            .orders
            .iter()
            .filter(|(_, order)| key(&order.view.intent.instrument) == key(&depth.instrument))
            .map(|(id, _)| id.clone())
            .collect();
        for id in ids {
            let state = self.orders.get(&id).expect("collected order");
            let intent = state.view.intent.clone();
            if depth.receive_timestamp_ns >= intent.expires_at_ns {
                let remaining = state.view.remaining_quantity;
                self.order_event(
                    (index, depth.receive_timestamp_ns),
                    &id,
                    PaperOrderStatus::Expired,
                    remaining,
                    Some(DecisionReason::HorizonExpired),
                    None,
                )?;
                continue;
            }
            if depth.receive_timestamp_ns
                < intent
                    .created_at_ns
                    .saturating_add(intent.assumed_entry_delay_ns)
            {
                continue;
            }
            let Some((quantity, price, assumptions)) = simulate_fill(&intent, depth) else {
                continue;
            };
            let state = self.orders.get(&id).expect("collected order");
            let quantity = quantity.min(state.view.remaining_quantity);
            if quantity == 0 {
                continue;
            }
            let previous = state.cumulative_notional_nanos;
            let total = previous + i128::from(price) * i128::from(quantity);
            let fee_before = self
                .fees
                .for_fill(
                    previous,
                    intent.side,
                    intent.side == OrderSide::Sell && previous > 0,
                )
                .total_nanos;
            let fee_after = self
                .fees
                .for_fill(total, intent.side, intent.side == OrderSide::Sell)
                .total_nanos;
            let fees = if previous == 0 {
                fee_after
            } else {
                fee_after - fee_before
            };
            let fill = PaperFill {
                schema_version: PAPER_SCHEMA_VERSION,
                fill_id: format!("{id}:fill:{index}"),
                source_record_index: index,
                order_id: id.clone(),
                decision_id: intent.decision_id.clone(),
                instrument: intent.instrument.clone(),
                side: intent.side,
                quantity,
                price_nanos: price,
                fees_nanos: fees,
                scenario: intent.scenario,
                assumptions,
                source_receive_timestamp_ns: depth.receive_timestamp_ns,
                filled_at_ns: depth.receive_timestamp_ns,
            };
            self.persist(index, LedgerEvent::Fill(fill))?;
            let remaining = self
                .orders
                .get(&id)
                .expect("fill order")
                .view
                .remaining_quantity
                - quantity;
            let status = if remaining == 0 {
                PaperOrderStatus::Filled
            } else {
                PaperOrderStatus::PartiallyFilled
            };
            self.order_event(
                (index, depth.receive_timestamp_ns),
                &id,
                status,
                remaining,
                None,
                None,
            )?;
            if intent.side == OrderSide::Sell
                && !self
                    .portfolio
                    .positions
                    .contains_key(&key(&intent.instrument))
            {
                self.record_closed_outcome(index, &intent, depth.receive_timestamp_ns)?;
            }
        }
        Ok(())
    }

    fn record_closed_outcome(
        &mut self,
        index: u64,
        intent: &PaperOrderIntent,
        now: i64,
    ) -> io::Result<()> {
        let instrument_key = key(&intent.instrument);
        let trace = self.position_excursions.remove(&instrument_key);
        let pnl = trace
            .as_ref()
            .map(|v| self.portfolio.realized_pnl_nanos - v.realized_before_nanos);
        let attribution = OutcomeAttribution { schema_version: PAPER_SCHEMA_VERSION,
            categories: vec![AttributionCategory::Unattributable], observed_net_pnl_nanos: pnl,
            max_favorable_excursion_nanos: trace.as_ref().map(|v| v.max_favorable_nanos),
            max_adverse_excursion_nanos: trace.as_ref().map(|v| v.max_adverse_nanos),
            holding_time_ns: trace.as_ref().map(|v| now.saturating_sub(v.opened_at_ns)), prediction_error_nanos: None,
            execution_cost_error_nanos: None, risk_model_error_nanos: None,
            exit_reason: Some(DecisionReason::ExitSignal),
            explanation: "Paper fill result after configured costs; profitability cannot be attributed to the hypothesis from one synthetic path".into() };
        let decision_id = intent
            .entry_order_id
            .as_ref()
            .and_then(|id| self.entry_decision.get(id))
            .cloned()
            .unwrap_or_else(|| intent.decision_id.clone());
        self.persist(
            index,
            LedgerEvent::Outcome {
                decision_id,
                attribution,
            },
        )?;
        self.portfolio.updated_at_ns = Some(now);
        Ok(())
    }

    fn decide(
        &mut self,
        index: u64,
        record: &quant_journal::JournalRecord,
        depth: &DepthEvent,
        features: &quant_engine::features::FeatureOutput,
    ) -> io::Result<()> {
        let instrument_key = key(&depth.instrument);
        let position = self.portfolio.positions.get(&instrument_key).cloned();
        let context = DecisionContext {
            schema_version: PAPER_SCHEMA_VERSION,
            source_record_index: index,
            source_event_id: format!("{}:{index}:{instrument_key}:depth", record.session_id),
            instrument: depth.instrument.clone(),
            session_id: record.session_id.clone(),
            source_receive_timestamp_ns: depth.receive_timestamp_ns,
            evaluated_at_ns: depth.receive_timestamp_ns,
            market: features.market.clone(),
            features: features.features.clone(),
            health: features.health.clone(),
            depth: Some(depth.clone()),
            journal_healthy: self.ledger_healthy,
            feed_connected: self.feed_connected,
        };
        let (mut candidate, mut reason) = self.policy.candidate(
            &context,
            position.as_ref().map_or(0, |p| p.quantity),
            position.as_ref().map(|p| p.opened_at_ns),
            self.mode,
        );
        let model = UntrainedModel.estimate(&context, self.policy.horizon_ns);
        let quantity = match candidate.action {
            DecisionAction::Buy => self.policy.quantity,
            DecisionAction::SellExit => position.as_ref().map_or(0, |p| p.quantity),
            _ => 0,
        };
        let reference_price = if candidate.action == DecisionAction::SellExit {
            depth.bids[0].price_nanos
        } else {
            depth.asks[0].price_nanos
        };
        let notional = i128::from(reference_price) * i128::from(quantity);
        let half_spread = (depth.asks[0].price_nanos - depth.bids[0].price_nanos).max(0) / 2;
        let costs = (quantity > 0 && reference_price > 0).then(|| {
            self.fees.estimate_round_trip(
                reference_price,
                quantity,
                half_spread,
                5,
                2,
                model.expected_gross_edge_nanos,
            )
        });
        if self.mode == PolicyMode::CostAwareSignal
            && costs
                .as_ref()
                .is_some_and(|c| c.expected_net_edge_nanos.is_none_or(|v| v <= 0))
        {
            candidate.action = DecisionAction::NoTrade;
            reason = DecisionReason::UncalibratedModel;
        }
        let open_order_exists = self
            .orders
            .values()
            .any(|o| key(&o.view.intent.instrument) == instrument_key);
        let risk = self.risk.assess(RiskInput {
            context: &context,
            candidate: &candidate,
            model: &model,
            costs: costs.as_ref(),
            portfolio: &self.portfolio,
            quantity,
            notional_nanos: notional,
            open_order_exists,
            evaluation_time_ns: depth.receive_timestamp_ns,
        });
        let action = if risk.approved_for_paper {
            candidate.action
        } else {
            DecisionAction::NoTrade
        };
        if !risk.approved_for_paper
            && candidate.action != DecisionAction::NoTrade
            && candidate.action != DecisionAction::Wait
        {
            reason = risk
                .reasons
                .first()
                .copied()
                .unwrap_or(DecisionReason::RiskLimit);
        }
        let decision_id = format!("paper:{}:{index}:{instrument_key}", record.session_id);
        let decision = DecisionRecord {
            schema_version: PAPER_SCHEMA_VERSION,
            decision_id: decision_id.clone(),
            context,
            candidate,
            model,
            cost: costs,
            risk,
            policy_version: self.policy.version.clone(),
            action,
            reason,
            quantity: (quantity > 0).then_some(quantity),
            notional_nanos: (quantity > 0).then_some(notional),
            created_at_ns: depth.receive_timestamp_ns,
            expires_at_ns: depth
                .receive_timestamp_ns
                .saturating_add(self.risk.max_order_age_ns),
        };
        self.persist(index, LedgerEvent::Decision(Box::new(decision.clone())))?;
        if matches!(action, DecisionAction::Buy | DecisionAction::SellExit) {
            let order_id = format!("{decision_id}:order");
            let intent = PaperOrderIntent {
                schema_version: PAPER_SCHEMA_VERSION,
                order_id: order_id.clone(),
                decision_id,
                entry_order_id: position.as_ref().map(|p| p.entry_order_id.clone()),
                instrument: depth.instrument.clone(),
                side: if action == DecisionAction::Buy {
                    OrderSide::Buy
                } else {
                    OrderSide::Sell
                },
                kind: OrderKind::Marketable,
                quantity,
                limit_price_nanos: Some(if action == DecisionAction::Buy {
                    i64::try_from(
                        i128::from(reference_price)
                            * (10_000 + i128::from(self.risk.max_slippage_bps))
                            / 10_000,
                    )
                    .unwrap_or(i64::MAX)
                } else {
                    i64::try_from(
                        i128::from(reference_price)
                            * (10_000 - i128::from(self.risk.max_slippage_bps))
                            / 10_000,
                    )
                    .unwrap_or(0)
                }),
                created_at_ns: depth.receive_timestamp_ns,
                expires_at_ns: depth
                    .receive_timestamp_ns
                    .saturating_add(self.risk.max_order_age_ns),
                scenario: FillScenario::ConservativeBase,
                assumed_entry_delay_ns: 1_000_000_000,
            };
            self.order_event(
                (index, depth.receive_timestamp_ns),
                &order_id,
                PaperOrderStatus::Created,
                quantity,
                None,
                Some(intent),
            )?;
            for status in [
                PaperOrderStatus::RiskApproved,
                PaperOrderStatus::PaperSubmitted,
                PaperOrderStatus::PaperWorking,
            ] {
                self.order_event(
                    (index, depth.receive_timestamp_ns),
                    &order_id,
                    status,
                    quantity,
                    None,
                    None,
                )?;
            }
        }
        Ok(())
    }

    pub fn cancel(&mut self, index: u64, order_id: &str, at_ns: i64) -> io::Result<()> {
        let remaining = self
            .orders
            .get(order_id)
            .ok_or_else(|| io::Error::other("unknown paper order"))?
            .view
            .remaining_quantity;
        self.order_event(
            (index, at_ns),
            order_id,
            PaperOrderStatus::Cancelled,
            remaining,
            None,
            None,
        )
    }
    pub fn halt(&mut self, index: u64, reason: DecisionReason, at_ns: i64) -> io::Result<()> {
        self.persist(
            index,
            LedgerEvent::Halt {
                reason,
                timestamp_ns: at_ns,
            },
        )
    }
    pub fn resume(&mut self, index: u64, at_ns: i64) -> io::Result<()> {
        self.persist(
            index,
            LedgerEvent::Resume {
                timestamp_ns: at_ns,
            },
        )
    }
    pub fn end_session(&mut self, index: u64, at_ns: i64) -> io::Result<()> {
        if self.portfolio.halt_reason == Some(DecisionReason::SessionEnded) {
            return Ok(());
        }
        let open: Vec<_> = self
            .orders
            .iter()
            .map(|(id, state)| (id.clone(), state.view.remaining_quantity))
            .collect();
        for (id, remaining) in open {
            self.order_event(
                (index, at_ns),
                &id,
                PaperOrderStatus::Expired,
                remaining,
                Some(DecisionReason::SessionEnded),
                None,
            )?;
        }
        self.halt(index, DecisionReason::SessionEnded, at_ns)
    }
    pub fn close(self) -> io::Result<()> {
        self.ledger.close()
    }
}

fn is_terminal(status: PaperOrderStatus) -> bool {
    matches!(
        status,
        PaperOrderStatus::Filled
            | PaperOrderStatus::Cancelled
            | PaperOrderStatus::Expired
            | PaperOrderStatus::Rejected
            | PaperOrderStatus::UnknownUnresolved
    )
}
fn valid_transition(from: PaperOrderStatus, to: PaperOrderStatus) -> bool {
    use PaperOrderStatus::*;
    matches!(
        (from, to),
        (Created, RiskApproved | Rejected)
            | (RiskApproved, PaperSubmitted | Rejected)
            | (PaperSubmitted, PaperWorking | Rejected)
            | (
                PaperWorking,
                PartiallyFilled | Filled | Cancelled | Expired | UnknownUnresolved
            )
            | (
                PartiallyFilled,
                PartiallyFilled | Filled | Cancelled | Expired | UnknownUnresolved
            )
    )
}

/// Uses only the current book, after a full event of entry delay. Aggregate
/// visible quantity is capped by scenario participation; touch is not a fill.
pub fn simulate_fill(
    intent: &PaperOrderIntent,
    depth: &DepthEvent,
) -> Option<(u64, i64, Vec<String>)> {
    if intent.instrument != depth.instrument
        || depth.receive_timestamp_ns
            < intent
                .created_at_ns
                .saturating_add(intent.assumed_entry_delay_ns)
    {
        return None;
    }
    let level = if intent.side == OrderSide::Buy {
        depth.asks[0]
    } else {
        depth.bids[0]
    };
    if level.price_nanos <= 0 || level.quantity == 0 {
        return None;
    }
    if intent.kind == OrderKind::PassiveLimit {
        let limit = intent.limit_price_nanos?;
        let crossed = if intent.side == OrderSide::Buy {
            level.price_nanos < limit
        } else {
            level.price_nanos > limit
        };
        if !crossed && intent.scenario != FillScenario::OptimisticDiagnostic {
            return None;
        }
        if intent.scenario == FillScenario::Adverse {
            return None;
        }
    }
    let (participation, slippage_bps) = match intent.scenario {
        FillScenario::OptimisticDiagnostic => (100_u64, 0_i128),
        FillScenario::ConservativeBase => (10, 5),
        FillScenario::Adverse => (2, 20),
    };
    let quantity = (level.quantity * participation / 100).min(intent.quantity);
    if quantity == 0 {
        return None;
    }
    let raw = i128::from(level.price_nanos);
    let slipped = if intent.side == OrderSide::Buy {
        raw * (10_000 + slippage_bps) / 10_000
    } else {
        raw * (10_000 - slippage_bps) / 10_000
    };
    let price = i64::try_from(slipped).ok()?;
    if intent.kind == OrderKind::Marketable
        && intent.limit_price_nanos.is_some_and(|collar| {
            if intent.side == OrderSide::Buy {
                price > collar
            } else {
                price < collar
            }
        })
    {
        return None;
    }
    Some((
        quantity,
        price,
        vec![
            format!("Current displayed top level; {participation}% participation ceiling"),
            format!(
                "{} bps adverse slippage after one full event delay",
                slippage_bps
            ),
            "Aggregate depth does not establish queue priority or guaranteed execution".into(),
        ],
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use quant_core::{DepthLevel, Instrument, MarketDataQuality, SourceProtocol};
    use quant_engine::features::{
        EngineHealthSnapshot, FeatureOutput, MarketStateSnapshot, QuantFeatureSnapshot,
    };
    use quant_engine::{Freshness, Provenance};
    use quant_journal::JournalRecord;

    fn sample(
        index: u64,
        seconds: i64,
        bid: i64,
        ofi: i64,
        volume_known: bool,
    ) -> (JournalRecord, EngineOutput) {
        let instrument = Instrument {
            venue: "NSE".into(),
            exchange_segment: "NSE_EQ".into(),
            security_id: "1333".into(),
            symbol: "RELIANCE".into(),
        };
        let timestamp = 1_700_000_000_000_000_000 + seconds * 1_000_000_000;
        let ask = bid + 100_000_000;
        let depth = DepthEvent {
            instrument: instrument.clone(),
            exchange_timestamp_ns: None,
            receive_timestamp_ns: timestamp,
            sequence: None,
            bids: [DepthLevel {
                price_nanos: bid,
                quantity: 30,
                order_count: Some(2),
            }; 20],
            asks: [DepthLevel {
                price_nanos: ask,
                quantity: 10,
                order_count: Some(2),
            }; 20],
        };
        let quality = MarketDataQuality {
            volume_interval_incomplete: !volume_known,
            cross_feed_uncertain: true,
            source_time_unverified: true,
            ..MarketDataQuality::default()
        };
        let provenance = Provenance {
            feed_session_id: "synthetic".into(),
            arrival_ordinal: Some(index),
            source_protocol: SourceProtocol::DhanDepth20,
            source_last_trade_timestamp_ns: None,
            source_exchange_timestamp_ns: None,
        };
        let market = MarketStateSnapshot {
            schema_version: 1,
            instrument: instrument.clone(),
            processing_timestamp_ns: timestamp,
            source_last_trade_timestamp_ns: None,
            freshness: Freshness::Fresh,
            quality,
            provenance: provenance.clone(),
            price_nanos: Some(bid),
            incremental_volume: volume_known.then_some(5),
            best_bid: Some(depth.bids[0]),
            best_ask: Some(depth.asks[0]),
            depth_receive_timestamp_ns: Some(timestamp),
        };
        let features = QuantFeatureSnapshot {
            schema_version: 1,
            instrument: instrument.clone(),
            processing_timestamp_ns: timestamp,
            source_last_trade_timestamp_ns: None,
            freshness: Freshness::Fresh,
            quality,
            provenance: provenance.clone(),
            midprice_nanos: Some(bid + 50_000_000),
            absolute_spread_nanos: Some(100_000_000),
            relative_spread: Some(0.001),
            microprice_nanos: Some(bid + 75_000_000),
            bid_level_weighted_depth: Some(100.0),
            ask_level_weighted_depth: Some(35.0),
            bid_ask_imbalance: Some(0.5),
            snapshot_ofi_quantity: Some(ofi),
            latest_price_return: None,
            rolling_return_volatility: Some(0.001),
            rolling_volume_activity: Some(5.0),
            price_volume_correlation: None,
            rolling_sample_count: 3,
        };
        let health = EngineHealthSnapshot {
            schema_version: 1,
            instrument,
            processing_timestamp_ns: timestamp,
            source_last_trade_timestamp_ns: None,
            freshness: Freshness::Fresh,
            quality,
            provenance,
            events_processed: index,
            rejected_events: 0,
            last_receive_timestamp_ns: timestamp,
            cross_feed_age_ns: Some(0),
        };
        (
            JournalRecord {
                session_id: "synthetic".into(),
                event: MarketEvent::Depth(Box::new(depth)),
            },
            EngineOutput {
                volume: None,
                finalized_candles: vec![],
                provisional_candles: vec![],
                features: Some(FeatureOutput {
                    market,
                    features,
                    health,
                }),
            },
        )
    }

    fn open_paper(path: &Path) -> PaperEngine {
        PaperEngine::open(
            path,
            PolicyConfig::default(),
            RiskConfig::default(),
            FeeSchedule::dhan_delivery_research(),
            PolicyMode::RiskFilteredSignal,
        )
        .unwrap()
    }

    #[test]
    fn profitable_paper_round_trip_and_recovery_are_deterministic() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("paper.qp");
        let mut paper = open_paper(&path);
        let mut samples = Vec::new();
        for index in 1..=9 {
            let sample = sample(index, index as i64, 100_000_000_000, 5, true);
            let snap = paper.on_market_record(index, &sample.0, &sample.1).unwrap();
            if index == 1 {
                assert!(snap.portfolio.reserved_cash_nanos > 0);
                assert!(snap.recent_fills.is_empty());
            }
            if index == 2 {
                assert_eq!(
                    snap.portfolio.positions.values().next().unwrap().quantity,
                    1
                );
            }
            samples.push(sample);
        }
        assert_eq!(
            paper
                .snapshot()
                .portfolio
                .positions
                .values()
                .next()
                .unwrap()
                .quantity,
            8
        );
        assert_eq!(paper.snapshot().portfolio.reserved_cash_nanos, 0);
        let before = paper.snapshot().portfolio;
        paper.close().unwrap();
        let mut recovered = open_paper(&path);
        for (i, (record, output)) in samples.iter().enumerate() {
            recovered
                .on_market_record(i as u64 + 1, record, output)
                .unwrap();
        }
        assert_eq!(recovered.snapshot().portfolio, before);
        for index in 10..=13 {
            let (record, output) = sample(index, index as i64 + 31, 105_000_000_000, -5, true);
            recovered.on_market_record(index, &record, &output).unwrap();
        }
        let snapshot = recovered.snapshot();
        assert!(snapshot.portfolio.positions.is_empty());
        assert!(snapshot.portfolio.realized_pnl_nanos > 0);
        assert_eq!(snapshot.recent_outcomes.len(), 1);
        assert_eq!(snapshot.recent_outcomes[0].1.prediction_error_nanos, None);
    }

    #[test]
    fn no_lookahead_partial_fill_expiry_and_no_trade_are_visible() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("paper.qp");
        let mut paper = open_paper(&path);
        let (record, output) = sample(1, 1, 100_000_000_000, 5, false);
        let snapshot = paper.on_market_record(1, &record, &output).unwrap();
        assert!(snapshot.orders.is_empty());
        assert_eq!(
            snapshot.recent_decisions[0].reason,
            DecisionReason::MissingFeature
        );
        let (record, output) = sample(2, 2, 100_000_000_000, 5, true);
        let snapshot = paper.on_market_record(2, &record, &output).unwrap();
        assert_eq!(snapshot.orders[0].status, PaperOrderStatus::PaperWorking);
        assert!(snapshot.recent_fills.is_empty());
        let (record, output) = sample(3, 3, 100_000_000_000, 5, true);
        let snapshot = paper.on_market_record(3, &record, &output).unwrap();
        assert_eq!(snapshot.orders[0].status, PaperOrderStatus::PartiallyFilled);
        assert_eq!(snapshot.recent_fills[0].quantity, 1);
        let (record, output) = sample(4, 20, 100_000_000_000, 5, true);
        let snapshot = paper.on_market_record(4, &record, &output).unwrap();
        assert!(
            snapshot
                .orders
                .iter()
                .any(|o| o.status == PaperOrderStatus::Expired)
        );
        assert_eq!(
            snapshot
                .portfolio
                .positions
                .values()
                .next()
                .unwrap()
                .quantity,
            1
        );
        assert_eq!(snapshot.recent_outcomes.len(), 0);
    }

    #[test]
    fn passive_touch_is_not_base_fill_and_optimism_is_diagnostic() {
        let (record, _) = sample(1, 2, 100_000_000_000, 5, true);
        let MarketEvent::Depth(depth) = record.event else {
            panic!("depth")
        };
        let intent = PaperOrderIntent {
            schema_version: 1,
            order_id: "x".into(),
            decision_id: "d".into(),
            entry_order_id: None,
            instrument: depth.instrument.clone(),
            side: OrderSide::Buy,
            kind: OrderKind::PassiveLimit,
            quantity: 5,
            limit_price_nanos: Some(depth.asks[0].price_nanos),
            created_at_ns: depth.receive_timestamp_ns - 1_000_000_000,
            expires_at_ns: depth.receive_timestamp_ns + 1_000_000_000,
            scenario: FillScenario::ConservativeBase,
            assumed_entry_delay_ns: 1_000_000_000,
        };
        assert!(simulate_fill(&intent, &depth).is_none());
        let optimistic = PaperOrderIntent {
            scenario: FillScenario::OptimisticDiagnostic,
            ..intent
        };
        assert!(simulate_fill(&optimistic, &depth).is_some());
    }

    #[test]
    fn loss_and_session_end_preserve_audit_and_never_invent_a_close() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("paper.qp");
        let mut paper = open_paper(&path);
        for index in 1..=9 {
            let (record, output) = sample(index, index as i64, 100_000_000_000, 5, true);
            paper.on_market_record(index, &record, &output).unwrap();
        }
        let (record, output) = sample(10, 50, 95_000_000_000, -5, true);
        paper.on_market_record(10, &record, &output).unwrap();
        for index in 11..=13 {
            let (record, output) = sample(index, 50 + index as i64 - 10, 95_000_000_000, -5, true);
            paper.on_market_record(index, &record, &output).unwrap();
        }
        assert!(paper.snapshot().portfolio.realized_pnl_nanos < 0);
        let (record, output) = sample(14, 54, 95_000_000_000, 5, true);
        paper.on_market_record(14, &record, &output).unwrap();
        let quantity_before = paper
            .snapshot()
            .portfolio
            .positions
            .values()
            .next()
            .map_or(0, |p| p.quantity);
        paper.end_session(14, 1_700_000_055_000_000_000).unwrap();
        let ended = paper.snapshot();
        assert!(ended.portfolio.halted);
        assert_eq!(
            ended.portfolio.halt_reason,
            Some(DecisionReason::SessionEnded)
        );
        assert_eq!(
            ended
                .portfolio
                .positions
                .values()
                .next()
                .map_or(0, |p| p.quantity),
            quantity_before
        );
        assert!(
            ended
                .orders
                .iter()
                .all(|o| o.status != PaperOrderStatus::PaperWorking)
        );
    }

    #[test]
    fn independent_risk_rejects_spread_stale_uncalibrated_and_loss_limit() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("paper.qp");
        let mut paper = open_paper(&path);
        let (record, mut output) = sample(1, 1, 100_000_000_000, 5, true);
        output.features.as_mut().unwrap().features.relative_spread = Some(0.01);
        let snap = paper.on_market_record(1, &record, &output).unwrap();
        assert_eq!(
            snap.recent_decisions[0].reason,
            DecisionReason::SpreadTooWide
        );
        let (record, mut output) = sample(2, 2, 100_000_000_000, 5, true);
        output.features.as_mut().unwrap().market.quality.stale = true;
        let snap = paper.on_market_record(2, &record, &output).unwrap();
        assert_eq!(snap.recent_decisions[1].reason, DecisionReason::StaleFeed);
        paper.portfolio.realized_pnl_nanos = -paper.risk.max_session_loss_nanos;
        let (record, output) = sample(3, 3, 100_000_000_000, 5, true);
        let snap = paper.on_market_record(3, &record, &output).unwrap();
        assert_eq!(snap.recent_decisions[2].reason, DecisionReason::RiskLimit);
        let path2 = dir.path().join("uncalibrated.qp");
        let risk = RiskConfig {
            allow_uncalibrated_paper: false,
            ..RiskConfig::default()
        };
        let mut strict = PaperEngine::open(
            &path2,
            PolicyConfig::default(),
            risk,
            FeeSchedule::dhan_delivery_research(),
            PolicyMode::RiskFilteredSignal,
        )
        .unwrap();
        let (record, output) = sample(1, 1, 100_000_000_000, 5, true);
        let snap = strict.on_market_record(1, &record, &output).unwrap();
        assert_eq!(
            snap.recent_decisions[0].reason,
            DecisionReason::UncalibratedModel
        );
    }

    #[test]
    fn cancellation_and_duplicate_source_index_do_not_duplicate_orders() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("paper.qp");
        let mut paper = open_paper(&path);
        let (record, output) = sample(1, 1, 100_000_000_000, 5, true);
        paper.on_market_record(1, &record, &output).unwrap();
        let order_id = paper.snapshot().orders[0].intent.order_id.clone();
        paper
            .cancel(1, &order_id, 1_700_000_001_000_000_000)
            .unwrap();
        let cancelled = paper.snapshot();
        assert!(
            cancelled
                .orders
                .iter()
                .any(|o| o.status == PaperOrderStatus::Cancelled)
        );
        assert!(
            paper
                .cancel(1, &order_id, 1_700_000_001_000_000_000)
                .is_err()
        );
        let again = paper.on_market_record(1, &record, &output).unwrap();
        assert_eq!(again.recent_decisions.len(), 1);
        assert!(again.recent_fills.is_empty());
    }
}
