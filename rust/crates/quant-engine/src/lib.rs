//! Deterministic, per-instrument DATA_ONLY market-state processing.
pub mod bars;
pub mod capture;
pub mod features;
pub mod pipeline;
pub mod volume;

use serde::{Deserialize, Serialize};

pub const SCHEMA_VERSION: u16 = 1;

/// The caller supplies exchange-approved sessions, including holidays and
/// special sessions. No default weekday/trading-hour guess is provided.
pub trait MarketCalendar: Send {
    fn session_at(&self, timestamp_ns: i64) -> Option<String>;
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum TimestampPolicy {
    Receive,
    SourceLastTrade,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum Freshness {
    Fresh,
    Stale,
    Unknown,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Provenance {
    pub feed_session_id: String,
    pub arrival_ordinal: Option<u64>,
    pub source_protocol: quant_core::SourceProtocol,
    pub source_last_trade_timestamp_ns: Option<i64>,
    pub source_exchange_timestamp_ns: Option<i64>,
}

impl From<&quant_core::SourceTimestampMetadata> for Provenance {
    fn from(meta: &quant_core::SourceTimestampMetadata) -> Self {
        Self {
            feed_session_id: meta.feed_session_id.clone(),
            arrival_ordinal: Some(meta.arrival_ordinal),
            source_protocol: meta.protocol,
            source_last_trade_timestamp_ns: meta.source_last_trade_timestamp_ns,
            source_exchange_timestamp_ns: None,
        }
    }
}
