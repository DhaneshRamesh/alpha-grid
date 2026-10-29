//! Versioned JSON read model. Nanoseconds and fixed-point values are strings
//! because JavaScript numbers cannot represent all i64/u64 values exactly.
use quant_core::{DepthEvent, DepthLevel, Instrument, MarketDataQuality, SourceProtocol};
use quant_engine::Provenance;
use quant_engine::bars::CandleSnapshot;
use quant_engine::features::FeatureOutput;
use quant_paper::oms::PaperSnapshot;
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const WIRE_VERSION: u16 = 1;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub enum MessageType {
    #[serde(rename = "engine.snapshot")]
    EngineSnapshot,
    #[serde(rename = "engine.update")]
    EngineUpdate,
    #[serde(rename = "engine.health")]
    EngineHealth,
    #[serde(rename = "market.snapshot")]
    MarketSnapshot,
    #[serde(rename = "market.update")]
    MarketUpdate,
    #[serde(rename = "depth.snapshot")]
    DepthSnapshot,
    #[serde(rename = "candle.snapshot")]
    CandleSnapshot,
    #[serde(rename = "candle.update")]
    CandleUpdate,
    #[serde(rename = "feature.snapshot")]
    FeatureSnapshot,
    #[serde(rename = "feature.update")]
    FeatureUpdate,
    #[serde(rename = "journal.status")]
    JournalStatus,
    #[serde(rename = "telemetry.status")]
    TelemetryStatus,
    #[serde(rename = "session.status")]
    SessionStatus,
    #[serde(rename = "error")]
    Error,
    #[serde(rename = "heartbeat")]
    Heartbeat,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Envelope {
    pub schema_version: u16,
    pub message_type: MessageType,
    pub instrument_key: Option<String>,
    pub engine_boot_id: String,
    pub publication_sequence: String,
    pub source_receive_timestamp_ns: Option<String>,
    pub publication_timestamp_ns: String,
    pub provenance: Option<WireProvenance>,
    pub freshness: Option<String>,
    pub quality: Option<WireQuality>,
    pub payload: Value,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct WireQuality {
    pub source_time_unverified: bool,
    pub volume_interval_incomplete: bool,
    pub cross_feed_uncertain: bool,
    pub stale: bool,
    pub inconsistent_last_trade_quantity: bool,
    pub book_pairing_unverified: bool,
}
impl From<MarketDataQuality> for WireQuality {
    fn from(value: MarketDataQuality) -> Self {
        Self {
            source_time_unverified: value.source_time_unverified,
            volume_interval_incomplete: value.volume_interval_incomplete,
            cross_feed_uncertain: value.cross_feed_uncertain,
            stale: value.stale,
            inconsistent_last_trade_quantity: value.inconsistent_last_trade_quantity,
            book_pairing_unverified: false,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct WireProvenance {
    pub feed_session_id: String,
    pub arrival_ordinal: Option<String>,
    pub source_protocol: String,
    pub source_last_trade_timestamp_ns: Option<String>,
    pub source_exchange_timestamp_ns: Option<String>,
}
impl From<&Provenance> for WireProvenance {
    fn from(value: &Provenance) -> Self {
        Self {
            feed_session_id: value.feed_session_id.clone(),
            arrival_ordinal: value.arrival_ordinal.map(|n| n.to_string()),
            source_protocol: protocol_name(value.source_protocol).into(),
            source_last_trade_timestamp_ns: value
                .source_last_trade_timestamp_ns
                .map(|n| n.to_string()),
            source_exchange_timestamp_ns: value.source_exchange_timestamp_ns.map(|n| n.to_string()),
        }
    }
}
fn protocol_name(value: SourceProtocol) -> &'static str {
    match value {
        SourceProtocol::DhanStandardV2 => "DhanStandardV2",
        SourceProtocol::DhanDepth20 => "DhanDepth20",
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct WireInstrument {
    pub key: String,
    pub venue: String,
    pub exchange_segment: String,
    pub security_id: String,
    pub symbol: Option<String>,
}
impl From<&Instrument> for WireInstrument {
    fn from(value: &Instrument) -> Self {
        Self {
            key: instrument_key(value),
            venue: value.venue.clone(),
            exchange_segment: value.exchange_segment.clone(),
            security_id: value.security_id.clone(),
            symbol: (!value.symbol.is_empty()).then(|| value.symbol.clone()),
        }
    }
}
pub fn instrument_key(value: &Instrument) -> String {
    format!(
        "{}:{}:{}",
        value.venue, value.exchange_segment, value.security_id
    )
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct WireDepthLevel {
    pub price_nanos: String,
    pub displayed_quantity: String,
    pub order_count: Option<u32>,
    pub cumulative_visible_quantity: String,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct WireDepth {
    pub receive_timestamp_ns: String,
    pub exchange_timestamp_ns: Option<String>,
    pub derived_pairing: bool,
    pub atomic_exchange_snapshot_verified: bool,
    pub bids: Vec<WireDepthLevel>,
    pub asks: Vec<WireDepthLevel>,
}
fn wire_levels(levels: &[DepthLevel; 20]) -> Vec<WireDepthLevel> {
    let mut cumulative = 0_u64;
    levels
        .iter()
        .map(|level| {
            cumulative = cumulative.saturating_add(level.quantity);
            WireDepthLevel {
                price_nanos: level.price_nanos.to_string(),
                displayed_quantity: level.quantity.to_string(),
                order_count: level.order_count,
                cumulative_visible_quantity: cumulative.to_string(),
            }
        })
        .collect()
}
impl From<&DepthEvent> for WireDepth {
    fn from(value: &DepthEvent) -> Self {
        Self {
            receive_timestamp_ns: value.receive_timestamp_ns.to_string(),
            exchange_timestamp_ns: value.exchange_timestamp_ns.map(|n| n.to_string()),
            derived_pairing: true,
            atomic_exchange_snapshot_verified: false,
            bids: wire_levels(&value.bids),
            asks: wire_levels(&value.asks),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WireMarket {
    pub price_nanos: Option<String>,
    pub cumulative_traded_volume: Option<String>,
    pub incremental_volume: Option<String>,
    pub best_bid_price_nanos: Option<String>,
    pub best_ask_price_nanos: Option<String>,
    pub depth_receive_timestamp_ns: Option<String>,
    pub source_receive_timestamp_ns: String,
    pub source_last_trade_timestamp_ns: Option<String>,
    pub freshness: String,
    pub quality: WireQuality,
    pub provenance: WireProvenance,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WireFeatures {
    pub midprice_nanos: Option<String>,
    pub absolute_spread_nanos: Option<String>,
    pub relative_spread: Option<f64>,
    pub microprice_nanos: Option<String>,
    pub bid_level_weighted_depth: Option<f64>,
    pub ask_level_weighted_depth: Option<f64>,
    pub bid_ask_imbalance: Option<f64>,
    pub snapshot_ofi_quantity: Option<String>,
    pub latest_price_return: Option<f64>,
    pub rolling_return_volatility: Option<f64>,
    pub rolling_volume_activity: Option<f64>,
    pub price_volume_correlation: Option<f64>,
    pub rolling_sample_count: usize,
    pub processing_timestamp_ns: String,
    pub freshness: String,
    pub quality: WireQuality,
    pub provenance: WireProvenance,
}

fn freshness_name(value: quant_engine::Freshness) -> &'static str {
    match value {
        quant_engine::Freshness::Fresh => "FRESH",
        quant_engine::Freshness::Stale => "STALE",
        quant_engine::Freshness::Unknown => "UNKNOWN",
    }
}
impl WireMarket {
    pub fn from_output(
        value: &FeatureOutput,
        volume: Option<&quant_core::VolumeObservation>,
    ) -> Self {
        let market = &value.market;
        Self {
            price_nanos: market.price_nanos.map(|n| n.to_string()),
            cumulative_traded_volume: volume.map(|v| v.cumulative_traded_volume.to_string()),
            incremental_volume: market.incremental_volume.map(|n| n.to_string()),
            best_bid_price_nanos: market.best_bid.map(|n| n.price_nanos.to_string()),
            best_ask_price_nanos: market.best_ask.map(|n| n.price_nanos.to_string()),
            depth_receive_timestamp_ns: market.depth_receive_timestamp_ns.map(|n| n.to_string()),
            source_receive_timestamp_ns: value.health.last_receive_timestamp_ns.to_string(),
            source_last_trade_timestamp_ns: market
                .source_last_trade_timestamp_ns
                .map(|n| n.to_string()),
            freshness: freshness_name(market.freshness).into(),
            quality: market.quality.into(),
            provenance: (&market.provenance).into(),
        }
    }
}
impl WireFeatures {
    pub fn from_output(value: &FeatureOutput) -> Self {
        let f = &value.features;
        Self {
            midprice_nanos: f.midprice_nanos.map(|n| n.to_string()),
            absolute_spread_nanos: f.absolute_spread_nanos.map(|n| n.to_string()),
            relative_spread: f.relative_spread,
            microprice_nanos: f.microprice_nanos.map(|n| n.to_string()),
            bid_level_weighted_depth: f.bid_level_weighted_depth,
            ask_level_weighted_depth: f.ask_level_weighted_depth,
            bid_ask_imbalance: f.bid_ask_imbalance,
            snapshot_ofi_quantity: f.snapshot_ofi_quantity.map(|n| n.to_string()),
            latest_price_return: f.latest_price_return,
            rolling_return_volatility: f.rolling_return_volatility,
            rolling_volume_activity: f.rolling_volume_activity,
            price_volume_correlation: f.price_volume_correlation,
            rolling_sample_count: f.rolling_sample_count,
            processing_timestamp_ns: f.processing_timestamp_ns.to_string(),
            freshness: freshness_name(f.freshness).into(),
            quality: f.quality.into(),
            provenance: (&f.provenance).into(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WireCandle {
    pub timeframe_ns: String,
    pub start_ns: String,
    pub end_ns: String,
    pub open_nanos: String,
    pub high_nanos: String,
    pub low_nanos: String,
    pub close_nanos: String,
    pub volume: Option<String>,
    pub observed_volume_delta: String,
    pub observation_count: u64,
    pub finalized: bool,
    pub source: String,
    pub timestamp_policy: String,
    pub market_session_id: String,
    pub processing_timestamp_ns: String,
    pub source_last_trade_timestamp_ns: Option<String>,
    pub freshness: String,
    pub quality: WireQuality,
    pub provenance: WireProvenance,
}
impl From<&CandleSnapshot> for WireCandle {
    fn from(value: &CandleSnapshot) -> Self {
        Self {
            timeframe_ns: value.timeframe_ns.to_string(),
            start_ns: value.start_ns.to_string(),
            end_ns: value.end_ns.to_string(),
            open_nanos: value.open_nanos.to_string(),
            high_nanos: value.high_nanos.to_string(),
            low_nanos: value.low_nanos.to_string(),
            close_nanos: value.close_nanos.to_string(),
            volume: value.volume.map(|n| n.to_string()),
            observed_volume_delta: value.observed_volume_delta.to_string(),
            observation_count: value.observation_count,
            finalized: value.finalized,
            source: format!("{:?}", value.source),
            timestamp_policy: format!("{:?}", value.timestamp_policy),
            market_session_id: value.market_session_id.clone(),
            processing_timestamp_ns: value.processing_timestamp_ns.to_string(),
            source_last_trade_timestamp_ns: value
                .source_last_trade_timestamp_ns
                .map(|n| n.to_string()),
            freshness: freshness_name(value.freshness).into(),
            quality: value.quality.into(),
            provenance: (&value.provenance).into(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WireInstrumentSnapshot {
    pub instrument: WireInstrument,
    pub market: Option<WireMarket>,
    pub features: Option<WireFeatures>,
    pub depth: Option<WireDepth>,
    pub candles: Vec<WireCandle>,
    pub last_update_timestamp_ns: Option<String>,
    pub previous_close_nanos: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct AuditEvent {
    pub timestamp_ns: String,
    pub instrument_key: Option<String>,
    pub kind: String,
    pub detail: String,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct LatencySummary {
    pub sample_count: u64,
    pub p50_ns: Option<String>,
    pub p95_ns: Option<String>,
    pub p99_ns: Option<String>,
    pub max_ns: Option<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WireCounters {
    pub frames_received: u64,
    pub packets_decoded: u64,
    pub journal_records_accepted: u64,
    pub durably_committed_records: u64,
    pub recovered_records: Option<u64>,
    pub replayed_records: u64,
    pub queue_current: usize,
    pub queue_high_water: usize,
    pub queue_rejections: u64,
    pub malformed_frames: u64,
    pub reconnects: u64,
    pub telemetry_coalesced_updates: u64,
    pub telemetry_skipped_updates: u64,
    pub connected_clients: usize,
    pub offered_frames_per_second: Option<f64>,
    pub processed_events_per_second: Option<f64>,
    pub durable_events_per_second: Option<f64>,
    pub processing_latency: LatencySummary,
    pub decode_latency: LatencySummary,
    pub feature_latency: LatencySummary,
    pub fsync_latency: LatencySummary,
    pub telemetry_publication_latency: LatencySummary,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WireEngineSnapshot {
    pub generation: String,
    pub replay_epoch: String,
    pub mode: String,
    pub engine_running: bool,
    pub feed_connected: bool,
    pub journal_blocked: bool,
    pub replay_paused: bool,
    pub replay_speed: Option<u8>,
    pub session_id: String,
    pub latest_data_timestamp_ns: Option<String>,
    pub storage_status: String,
    pub backpressure_state: String,
    pub counters: WireCounters,
    pub instruments: Vec<WireInstrumentSnapshot>,
    pub audit: Vec<AuditEvent>,
    pub paper: Option<WirePaperSnapshot>,
    pub approved_security_ids: Vec<String>,
    pub no_trade_reasons: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WireEngineUpdate {
    pub generation: String,
    pub status: WireEngineSnapshotStatus,
    pub instruments: Vec<WireInstrumentSnapshot>,
    pub audit: Vec<AuditEvent>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WireEngineSnapshotStatus {
    pub engine_running: bool,
    pub feed_connected: bool,
    pub journal_blocked: bool,
    pub replay_paused: bool,
    pub replay_speed: Option<u8>,
    pub latest_data_timestamp_ns: Option<String>,
    pub storage_status: String,
    pub backpressure_state: String,
    pub counters: WireCounters,
    pub paper: Option<WirePaperSnapshot>,
    pub approved_security_ids: Vec<String>,
    pub no_trade_reasons: Vec<String>,
}

impl From<&WireEngineSnapshot> for WireEngineSnapshotStatus {
    fn from(value: &WireEngineSnapshot) -> Self {
        Self {
            engine_running: value.engine_running,
            feed_connected: value.feed_connected,
            journal_blocked: value.journal_blocked,
            replay_paused: value.replay_paused,
            replay_speed: value.replay_speed,
            latest_data_timestamp_ns: value.latest_data_timestamp_ns.clone(),
            storage_status: value.storage_status.clone(),
            backpressure_state: value.backpressure_state.clone(),
            counters: value.counters.clone(),
            paper: value.paper.clone(),
            approved_security_ids: value.approved_security_ids.clone(),
            no_trade_reasons: value.no_trade_reasons.clone(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WirePaperPosition {
    pub instrument: WireInstrument,
    pub quantity: String,
    pub cost_basis_nanos: String,
    pub entry_order_id: String,
    pub opened_at_ns: String,
    pub last_bid_mark_nanos: Option<String>,
    pub unrealized_pnl_nanos: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WirePaperPortfolio {
    pub starting_capital_nanos: String,
    pub cash_nanos: String,
    pub reserved_cash_nanos: String,
    pub available_cash_nanos: String,
    pub realized_pnl_nanos: String,
    pub fees_paid_nanos: String,
    pub marked_equity_nanos: Option<String>,
    pub max_drawdown_nanos: Option<String>,
    pub exposure_nanos: String,
    pub remaining_session_loss_nanos: Option<String>,
    pub halted: bool,
    pub halt_reason: Option<String>,
    pub positions: Vec<WirePaperPosition>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WirePaperDecision {
    pub decision_id: String,
    pub source_record_index: String,
    pub instrument: WireInstrument,
    pub action: String,
    pub candidate_action: String,
    pub reason: String,
    pub hypothesis_id: String,
    pub evidence: Vec<String>,
    pub model_version: String,
    pub model_status: String,
    pub expected_gross_edge_nanos: Option<String>,
    pub expected_net_edge_nanos: Option<String>,
    pub forecast_uncertainty_nanos: Option<String>,
    pub estimated_cost_nanos: Option<String>,
    pub adverse_cost_nanos: Option<String>,
    pub cost_provisional: bool,
    pub risk_approved: bool,
    pub risk_reasons: Vec<String>,
    pub quantity: Option<String>,
    pub notional_nanos: Option<String>,
    pub source_receive_timestamp_ns: String,
    pub expires_at_ns: String,
    pub volume_complete: bool,
    pub cross_feed_uncertain: bool,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WirePaperOrder {
    pub order_id: String,
    pub decision_id: String,
    pub instrument: WireInstrument,
    pub side: String,
    pub status: String,
    pub kind: String,
    pub scenario: String,
    pub quantity: String,
    pub remaining_quantity: String,
    pub filled_quantity: String,
    pub price_collar_nanos: Option<String>,
    pub created_at_ns: String,
    pub last_transition_ns: String,
    pub expires_at_ns: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WirePaperFill {
    pub fill_id: String,
    pub order_id: String,
    pub instrument: WireInstrument,
    pub side: String,
    pub quantity: String,
    pub price_nanos: String,
    pub fees_nanos: String,
    pub timestamp_ns: String,
    pub assumptions: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WirePaperOutcome {
    pub decision_id: String,
    pub net_pnl_nanos: Option<String>,
    pub max_favorable_excursion_nanos: Option<String>,
    pub max_adverse_excursion_nanos: Option<String>,
    pub holding_time_ns: Option<String>,
    pub categories: Vec<String>,
    pub explanation: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WirePaperSnapshot {
    pub schema_version: u16,
    pub mode: String,
    pub portfolio: WirePaperPortfolio,
    pub recent_decisions: Vec<WirePaperDecision>,
    pub orders: Vec<WirePaperOrder>,
    pub recent_fills: Vec<WirePaperFill>,
    pub recent_outcomes: Vec<WirePaperOutcome>,
    pub last_source_record_index: String,
    pub ledger_recovered_tail_bytes: String,
    pub ledger_healthy: bool,
    pub fee_schedule_version: String,
    pub risk_rules_version: String,
    pub policy_version: String,
    pub risk_limits: WirePaperRiskLimits,
    pub approved_security_ids: Vec<String>,
    pub scanner: Vec<WirePaperOpportunity>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WirePaperOpportunity {
    pub instrument: WireInstrument,
    pub horizon: String,
    pub horizon_ns: Option<String>,
    pub approved: bool,
    pub source_receive_timestamp_ns: String,
    pub model_version: String,
    pub model_status: String,
    pub expected_gross_edge_nanos: Option<String>,
    pub expected_cost_nanos: Option<String>,
    pub adverse_cost_nanos: Option<String>,
    pub uncertainty_nanos: Option<String>,
    pub robust_net_edge_nanos: Option<String>,
    pub reason: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WirePaperRiskLimits {
    pub max_deployed_nanos: String,
    pub max_instrument_notional_nanos: String,
    pub max_positions: usize,
    pub max_loss_per_decision_nanos: String,
    pub max_session_loss_nanos: String,
    pub max_spread_bps: f64,
    pub max_market_age_ns: String,
    pub max_slippage_bps: u32,
    pub max_return_volatility: f64,
    pub max_order_age_ns: String,
    pub allow_uncalibrated_paper: bool,
}

impl From<&PaperSnapshot> for WirePaperSnapshot {
    fn from(value: &PaperSnapshot) -> Self {
        let p = &value.portfolio;
        Self {
            schema_version: value.schema_version,
            mode: value.mode.clone(),
            portfolio: WirePaperPortfolio {
                starting_capital_nanos: p.starting_capital_nanos.to_string(),
                cash_nanos: p.cash_nanos.to_string(),
                reserved_cash_nanos: p.reserved_cash_nanos.to_string(),
                available_cash_nanos: (p.cash_nanos - p.reserved_cash_nanos).to_string(),
                realized_pnl_nanos: p.realized_pnl_nanos.to_string(),
                fees_paid_nanos: p.fees_paid_nanos.to_string(),
                marked_equity_nanos: p.marked_equity_nanos.map(|v| v.to_string()),
                max_drawdown_nanos: p.max_drawdown_nanos.map(|v| v.to_string()),
                exposure_nanos: p
                    .positions
                    .values()
                    .map(|v| v.cost_basis_nanos)
                    .sum::<i128>()
                    .to_string(),
                remaining_session_loss_nanos: p
                    .positions
                    .values()
                    .map(|position| position.unrealized_pnl_nanos)
                    .sum::<Option<i128>>()
                    .map(|unrealized| {
                        (value.risk_limits.max_session_loss_nanos
                            + p.realized_pnl_nanos
                            + unrealized)
                            .max(0)
                            .to_string()
                    }),
                halted: p.halted,
                halt_reason: p.halt_reason.map(|v| format!("{v:?}")),
                positions: p
                    .positions
                    .values()
                    .map(|v| WirePaperPosition {
                        instrument: WireInstrument::from(&v.instrument),
                        quantity: v.quantity.to_string(),
                        cost_basis_nanos: v.cost_basis_nanos.to_string(),
                        entry_order_id: v.entry_order_id.clone(),
                        opened_at_ns: v.opened_at_ns.to_string(),
                        last_bid_mark_nanos: v.last_bid_mark_nanos.map(|n| n.to_string()),
                        unrealized_pnl_nanos: v.unrealized_pnl_nanos.map(|n| n.to_string()),
                    })
                    .collect(),
            },
            recent_decisions: value
                .recent_decisions
                .iter()
                .map(|d| WirePaperDecision {
                    decision_id: d.decision_id.clone(),
                    source_record_index: d.context.source_record_index.to_string(),
                    instrument: WireInstrument::from(&d.context.instrument),
                    action: format!("{:?}", d.action),
                    candidate_action: format!("{:?}", d.candidate.action),
                    reason: format!("{:?}", d.reason),
                    hypothesis_id: d.candidate.hypothesis_id.clone(),
                    evidence: d.candidate.evidence.clone(),
                    model_version: d.model.model_version.clone(),
                    model_status: format!("{:?}", d.model.status),
                    expected_gross_edge_nanos: d
                        .model
                        .expected_gross_edge_nanos
                        .map(|n| n.to_string()),
                    expected_net_edge_nanos: d
                        .cost
                        .as_ref()
                        .and_then(|c| c.expected_net_edge_nanos.map(|n| n.to_string())),
                    forecast_uncertainty_nanos: d
                        .model
                        .forecast_uncertainty_nanos
                        .map(|n| n.to_string()),
                    estimated_cost_nanos: d
                        .cost
                        .as_ref()
                        .map(|c| c.expected_total_nanos.to_string()),
                    adverse_cost_nanos: d.cost.as_ref().map(|c| c.adverse_total_nanos.to_string()),
                    cost_provisional: d.cost.as_ref().is_some_and(|c| c.provisional),
                    risk_approved: d.risk.approved_for_paper,
                    risk_reasons: d.risk.reasons.iter().map(|r| format!("{r:?}")).collect(),
                    quantity: d.quantity.map(|n| n.to_string()),
                    notional_nanos: d.notional_nanos.map(|n| n.to_string()),
                    source_receive_timestamp_ns: d.context.source_receive_timestamp_ns.to_string(),
                    expires_at_ns: d.expires_at_ns.to_string(),
                    volume_complete: !d.context.market.quality.volume_interval_incomplete,
                    cross_feed_uncertain: d.context.market.quality.cross_feed_uncertain,
                })
                .collect(),
            orders: value
                .orders
                .iter()
                .map(|o| WirePaperOrder {
                    order_id: o.intent.order_id.clone(),
                    decision_id: o.intent.decision_id.clone(),
                    instrument: WireInstrument::from(&o.intent.instrument),
                    side: format!("{:?}", o.intent.side),
                    status: format!("{:?}", o.status),
                    kind: format!("{:?}", o.intent.kind),
                    scenario: format!("{:?}", o.intent.scenario),
                    quantity: o.intent.quantity.to_string(),
                    remaining_quantity: o.remaining_quantity.to_string(),
                    filled_quantity: o.filled_quantity.to_string(),
                    price_collar_nanos: o.intent.limit_price_nanos.map(|n| n.to_string()),
                    created_at_ns: o.intent.created_at_ns.to_string(),
                    last_transition_ns: o.last_transition_ns.to_string(),
                    expires_at_ns: o.intent.expires_at_ns.to_string(),
                })
                .collect(),
            recent_fills: value
                .recent_fills
                .iter()
                .map(|f| WirePaperFill {
                    fill_id: f.fill_id.clone(),
                    order_id: f.order_id.clone(),
                    instrument: WireInstrument::from(&f.instrument),
                    side: format!("{:?}", f.side),
                    quantity: f.quantity.to_string(),
                    price_nanos: f.price_nanos.to_string(),
                    fees_nanos: f.fees_nanos.to_string(),
                    timestamp_ns: f.filled_at_ns.to_string(),
                    assumptions: f.assumptions.clone(),
                })
                .collect(),
            recent_outcomes: value
                .recent_outcomes
                .iter()
                .map(|(id, a)| WirePaperOutcome {
                    decision_id: id.clone(),
                    net_pnl_nanos: a.observed_net_pnl_nanos.map(|n| n.to_string()),
                    max_favorable_excursion_nanos: a
                        .max_favorable_excursion_nanos
                        .map(|n| n.to_string()),
                    max_adverse_excursion_nanos: a
                        .max_adverse_excursion_nanos
                        .map(|n| n.to_string()),
                    holding_time_ns: a.holding_time_ns.map(|n| n.to_string()),
                    categories: a.categories.iter().map(|c| format!("{c:?}")).collect(),
                    explanation: a.explanation.clone(),
                })
                .collect(),
            last_source_record_index: value.last_source_record_index.to_string(),
            ledger_recovered_tail_bytes: value.ledger_recovered_tail_bytes.to_string(),
            ledger_healthy: value.ledger_healthy,
            fee_schedule_version: value.fee_schedule_version.clone(),
            risk_rules_version: value.risk_rules_version.clone(),
            policy_version: value.policy_version.clone(),
            risk_limits: WirePaperRiskLimits {
                max_deployed_nanos: value.risk_limits.max_deployed_nanos.to_string(),
                max_instrument_notional_nanos: value
                    .risk_limits
                    .max_instrument_notional_nanos
                    .to_string(),
                max_positions: value.risk_limits.max_positions,
                max_loss_per_decision_nanos: value
                    .risk_limits
                    .max_loss_per_decision_nanos
                    .to_string(),
                max_session_loss_nanos: value.risk_limits.max_session_loss_nanos.to_string(),
                max_spread_bps: value.risk_limits.max_spread_bps,
                max_market_age_ns: value.risk_limits.max_market_age_ns.to_string(),
                max_slippage_bps: value.risk_limits.max_slippage_bps,
                max_return_volatility: value.risk_limits.max_return_volatility,
                max_order_age_ns: value.risk_limits.max_order_age_ns.to_string(),
                allow_uncalibrated_paper: value.risk_limits.allow_uncalibrated_paper,
            },
            approved_security_ids: value.approved_security_ids.clone(),
            scanner: value
                .scanner
                .iter()
                .map(|row| WirePaperOpportunity {
                    instrument: WireInstrument::from(&row.instrument),
                    horizon: row.assessment.horizon.clone(),
                    horizon_ns: row.assessment.horizon_ns.map(|v| v.to_string()),
                    approved: row.assessment.approved,
                    source_receive_timestamp_ns: row
                        .assessment
                        .source_receive_timestamp_ns
                        .to_string(),
                    model_version: row.assessment.model_version.clone(),
                    model_status: format!("{:?}", row.assessment.model_status),
                    expected_gross_edge_nanos: row
                        .assessment
                        .expected_gross_edge_nanos
                        .map(|v| v.to_string()),
                    expected_cost_nanos: row.assessment.expected_cost_nanos.map(|v| v.to_string()),
                    adverse_cost_nanos: row.assessment.adverse_cost_nanos.map(|v| v.to_string()),
                    uncertainty_nanos: row.assessment.uncertainty_nanos.map(|v| v.to_string()),
                    robust_net_edge_nanos: row
                        .assessment
                        .robust_net_edge_nanos
                        .map(|v| v.to_string()),
                    reason: format!("{:?}", row.assessment.reason),
                })
                .collect(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct WireError {
    pub code: String,
    pub detail: String,
    pub recoverable: bool,
}

pub fn schema_json<T: JsonSchema>() -> serde_json::Value {
    serde_json::to_value(schemars::schema_for!(T)).expect("schema serialization")
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn nanoseconds_and_fixed_prices_remain_exact_strings() {
        let event = DepthEvent {
            instrument: Instrument {
                venue: "NSE".into(),
                exchange_segment: "NSE_EQ".into(),
                security_id: "1333".into(),
                symbol: "".into(),
            },
            exchange_timestamp_ns: None,
            receive_timestamp_ns: 1_700_000_000_123_456_789,
            sequence: None,
            bids: [DepthLevel {
                price_nanos: 1_234_567_890_123,
                quantity: 20,
                order_count: Some(2),
            }; 20],
            asks: [DepthLevel {
                price_nanos: 1_234_567_890_124,
                quantity: 10,
                order_count: Some(1),
            }; 20],
        };
        let wire = WireDepth::from(&event);
        let json = serde_json::to_value(&wire).unwrap();
        assert_eq!(json["receive_timestamp_ns"], "1700000000123456789");
        assert_eq!(json["bids"][0]["price_nanos"], "1234567890123");
        assert_eq!(json["bids"][19]["cumulative_visible_quantity"], "400");
        assert!(!wire.atomic_exchange_snapshot_verified);
    }
    #[test]
    fn schema_has_envelope_and_payload_fields() {
        let envelope = schema_json::<Envelope>();
        let snapshot = schema_json::<WireEngineSnapshot>();
        assert!(envelope.to_string().contains("publication_sequence"));
        assert!(snapshot.to_string().contains("durably_committed_records"));
        assert_eq!(WIRE_VERSION, 1);
    }
}
