//! Broker-independent fixed-point market data. Prices are INR × 10^9;
//! timestamps are Unix nanoseconds, preserving source precision when supplied.
use serde::{Deserialize, Serialize};

pub const BOOK_LEVELS: usize = 20;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Instrument {
    pub venue: String,
    pub exchange_segment: String,
    pub security_id: String,
    pub symbol: String,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct DepthLevel {
    pub price_nanos: i64,
    pub quantity: u64,
    pub order_count: Option<u32>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct DepthEvent {
    pub instrument: Instrument,
    pub exchange_timestamp_ns: Option<i64>,
    pub receive_timestamp_ns: i64,
    pub sequence: Option<u64>,
    pub bids: [DepthLevel; BOOK_LEVELS],
    pub asks: [DepthLevel; BOOK_LEVELS],
}

/// A single Dhan depth side. A side packet is durable even when no matching
/// opposite side arrived; the wire sequence is deliberately not interpreted.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum DepthSide {
    Bid,
    Ask,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct DepthSideUpdate {
    pub instrument: Instrument,
    pub side: DepthSide,
    pub levels: [DepthLevel; BOOK_LEVELS],
    pub receive_timestamp_ns: i64,
    pub feed_session_id: String,
    pub frame_id: u64,
    pub packet_offset: u32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum SourceProtocol {
    DhanStandardV2,
    DhanDepth20,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum PriceSource {
    Ticker,
    Quote,
    Full,
}

/// Source seconds are retained as nanoseconds, not treated as measured latency.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct SourceTimestampMetadata {
    pub receive_timestamp_ns: i64,
    pub source_last_trade_timestamp_ns: Option<i64>,
    pub feed_session_id: String,
    pub arrival_ordinal: u64,
    pub frame_id: u64,
    pub packet_offset: u32,
    pub protocol: SourceProtocol,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct MarketDataQuality {
    pub source_time_unverified: bool,
    pub volume_interval_incomplete: bool,
    pub cross_feed_uncertain: bool,
    pub stale: bool,
    pub inconsistent_last_trade_quantity: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct PriceObservation {
    pub instrument: Instrument,
    pub metadata: SourceTimestampMetadata,
    pub source: PriceSource,
    pub price_nanos: i64,
    pub quality: MarketDataQuality,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct FiveLevelQuote {
    pub bid: DepthLevel,
    pub ask: DepthLevel,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct QuoteObservation {
    pub price: PriceObservation,
    pub last_trade_quantity: u64,
    pub average_trade_price_nanos: Option<i64>,
    pub cumulative_traded_volume: u64,
    pub total_sell_quantity: u64,
    pub total_buy_quantity: u64,
    pub day_open_nanos: Option<i64>,
    pub day_close_nanos: Option<i64>,
    pub day_high_nanos: Option<i64>,
    pub day_low_nanos: Option<i64>,
    pub open_interest: Option<u64>,
    pub day_high_open_interest: Option<u64>,
    pub day_low_open_interest: Option<u64>,
    pub five_level_book: Option<[FiveLevelQuote; 5]>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum VolumeStatus {
    BaselineUnknown,
    Unchanged,
    Increased,
    Reset,
    SessionChanged,
    OutOfOrder,
    ReconnectUnknown,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct VolumeObservation {
    pub instrument: Instrument,
    pub metadata: SourceTimestampMetadata,
    pub cumulative_traded_volume: u64,
    pub incremental_volume: Option<u64>,
    pub status: VolumeStatus,
    pub quality: MarketDataQuality,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum MarketSessionKind {
    /// Code 7 contains no documented open/close value; state is unknown.
    StatusSignal,
    Disconnected {
        reason_code: u16,
    },
    PreviousClose {
        price_nanos: i64,
        previous_open_interest: u64,
    },
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct MarketSessionEvent {
    pub instrument: Option<Instrument>,
    pub metadata: SourceTimestampMetadata,
    pub kind: MarketSessionKind,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct TradeEvent {
    pub instrument: Instrument,
    pub exchange_timestamp_ns: Option<i64>,
    pub receive_timestamp_ns: i64,
    pub sequence: Option<u64>,
    pub price_nanos: i64,
    pub quantity: u64,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum MarketEvent {
    Depth(Box<DepthEvent>),
    Trade(TradeEvent),
    // Appended: QJ01/Postcard discriminants for historical Depth/Trade stay 0/1.
    DepthSide(Box<DepthSideUpdate>),
    // Append-only enum variants preserve historical Postcard tags 0/1/2.
    Price(PriceObservation),
    Quote(Box<QuoteObservation>),
    Session(Box<MarketSessionEvent>),
}

impl MarketEvent {
    pub fn receive_timestamp_ns(&self) -> i64 {
        match self {
            Self::Depth(event) => event.receive_timestamp_ns,
            Self::Trade(event) => event.receive_timestamp_ns,
            Self::DepthSide(event) => event.receive_timestamp_ns,
            Self::Price(event) => event.metadata.receive_timestamp_ns,
            Self::Quote(event) => event.price.metadata.receive_timestamp_ns,
            Self::Session(event) => event.metadata.receive_timestamp_ns,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum FeedState {
    Healthy,
    Degraded,
    Stale,
    Disconnected,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct FeedHealth {
    pub instrument: Instrument,
    pub state: FeedState,
    pub last_receive_timestamp_ns: Option<i64>,
    pub events_received: u64,
    pub malformed: u64,
    pub reconnects: u64,
    pub sequence_gaps: u64,
}
