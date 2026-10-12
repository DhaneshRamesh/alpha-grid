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
}

impl MarketEvent {
    pub fn receive_timestamp_ns(&self) -> i64 {
        match self {
            Self::Depth(event) => event.receive_timestamp_ns,
            Self::Trade(event) => event.receive_timestamp_ns,
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
