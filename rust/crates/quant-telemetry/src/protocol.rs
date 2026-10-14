//! Versioned JSON read model. Nanoseconds and fixed-point values are strings
//! because JavaScript numbers cannot represent all i64/u64 values exactly.
use quant_core::{DepthEvent, DepthLevel, Instrument, MarketDataQuality, SourceProtocol};
use quant_engine::Provenance;
use quant_engine::bars::CandleSnapshot;
use quant_engine::features::FeatureOutput;
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
    pub fn from_output(value: &FeatureOutput) -> Self {
        let market = &value.market;
        Self {
            price_nanos: market.price_nanos.map(|n| n.to_string()),
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
