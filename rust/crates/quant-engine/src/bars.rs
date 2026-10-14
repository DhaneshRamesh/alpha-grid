//! Quote-sampled LTP bars. Cumulative deltas spanning a bar edge remain unknown.
use crate::{Freshness, MarketCalendar, Provenance, SCHEMA_VERSION, TimestampPolicy};
use quant_core::{Instrument, MarketDataQuality, PriceObservation, VolumeObservation};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

pub const TIMEFRAMES_NS: [i64; 6] = [
    1_000_000_000,
    5_000_000_000,
    15_000_000_000,
    60_000_000_000,
    300_000_000_000,
    900_000_000_000,
];

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum CandleSource {
    PriceOnlyQuote,
    QuoteSampledLtpWithCumulativeDelta,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct CandleSnapshot {
    pub schema_version: u16,
    pub instrument: Instrument,
    pub processing_timestamp_ns: i64,
    pub source_last_trade_timestamp_ns: Option<i64>,
    pub freshness: Freshness,
    pub quality: MarketDataQuality,
    pub provenance: Provenance,
    pub market_session_id: String,
    pub timeframe_ns: i64,
    pub start_ns: i64,
    pub end_ns: i64,
    pub open_nanos: i64,
    pub high_nanos: i64,
    pub low_nanos: i64,
    pub close_nanos: i64,
    /// Only Some when every observed cumulative interval belongs to this bar.
    pub volume: Option<u64>,
    /// Sum of defensible within-bar deltas, even if other intervals are unknown.
    pub observed_volume_delta: u64,
    pub observation_count: u64,
    pub finalized: bool,
    pub source: CandleSource,
    pub timestamp_policy: TimestampPolicy,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BarError {
    InvalidConfig,
    MissingSourceTimestamp,
    OutsideSession,
    InvalidPrice,
    LateAfterFinalization,
    OutOfOrderArrival,
    CapacityExceeded,
    InstrumentMismatch,
}

#[derive(Clone, Copy)]
pub struct BarConfig {
    pub timestamp_policy: TimestampPolicy,
    pub max_lateness_ns: i64,
    pub max_open_bars_per_instrument: usize,
}

impl BarConfig {
    pub fn validate(self) -> Result<Self, BarError> {
        if self.max_lateness_ns < 0 || self.max_open_bars_per_instrument < TIMEFRAMES_NS.len() {
            return Err(BarError::InvalidConfig);
        }
        Ok(self)
    }
}

struct BarAcc {
    snapshot: CandleSnapshot,
    first_key: (i64, u64),
    last_key: (i64, u64),
    volume_complete: bool,
    saw_volume: bool,
}

#[derive(Default)]
struct InstrumentBars {
    session: String,
    feed_session: String,
    max_event_time_ns: i64,
    last_arrival_ordinal: Option<u64>,
    bars: BTreeMap<(i64, i64), BarAcc>,
}

pub struct BarEngine {
    config: BarConfig,
    instruments: BTreeMap<(String, String, String), InstrumentBars>,
}

impl BarEngine {
    pub fn new(config: BarConfig) -> Result<Self, BarError> {
        Ok(Self {
            config: config.validate()?,
            instruments: BTreeMap::new(),
        })
    }

    fn event_time(&self, price: &PriceObservation) -> Result<i64, BarError> {
        match self.config.timestamp_policy {
            TimestampPolicy::Receive => Ok(price.metadata.receive_timestamp_ns),
            TimestampPolicy::SourceLastTrade => price
                .metadata
                .source_last_trade_timestamp_ns
                .ok_or(BarError::MissingSourceTimestamp),
        }
    }

    pub fn observe(
        &mut self,
        price: &PriceObservation,
        volume: Option<&VolumeObservation>,
        calendar: &dyn MarketCalendar,
    ) -> Result<Vec<CandleSnapshot>, BarError> {
        if price.price_nanos <= 0 {
            return Err(BarError::InvalidPrice);
        }
        if volume.is_some_and(|v| v.instrument != price.instrument || v.metadata != price.metadata)
        {
            return Err(BarError::InstrumentMismatch);
        }
        let event_time = self.event_time(price)?;
        let session = calendar
            .session_at(event_time)
            .ok_or(BarError::OutsideSession)?;
        let key = (
            price.instrument.venue.clone(),
            price.instrument.exchange_segment.clone(),
            price.instrument.security_id.clone(),
        );
        let state = self.instruments.entry(key).or_default();
        if state
            .last_arrival_ordinal
            .is_some_and(|last| price.metadata.arrival_ordinal <= last)
        {
            return Err(BarError::OutOfOrderArrival);
        }
        let session_changed = !state.session.is_empty()
            && (state.session != session || state.feed_session != price.metadata.feed_session_id);
        if session_changed && event_time < state.max_event_time_ns {
            return Err(BarError::LateAfterFinalization);
        }
        let watermark = state
            .max_event_time_ns
            .saturating_sub(self.config.max_lateness_ns);
        if !session_changed
            && (event_time < watermark
                || TIMEFRAMES_NS.iter().any(|size| {
                    event_time
                        .div_euclid(*size)
                        .saturating_mul(*size)
                        .saturating_add(*size)
                        <= watermark
                }))
        {
            return Err(BarError::LateAfterFinalization);
        }
        let new_bars = TIMEFRAMES_NS
            .iter()
            .filter(|size| {
                let start = event_time.div_euclid(**size) * **size;
                !state.bars.contains_key(&(**size, start))
            })
            .count();
        if !session_changed
            && state.bars.len() + new_bars > self.config.max_open_bars_per_instrument
        {
            return Err(BarError::CapacityExceeded);
        }
        let mut finalized = Vec::new();
        if session_changed {
            for (_, mut bar) in std::mem::take(&mut state.bars) {
                bar.snapshot.quality.volume_interval_incomplete = true;
                bar.snapshot.volume = None;
                bar.snapshot.finalized = true;
                finalized.push(bar.snapshot);
            }
            state.max_event_time_ns = event_time;
        }
        state.session = session.clone();
        state.feed_session = price.metadata.feed_session_id.clone();
        state.max_event_time_ns = state.max_event_time_ns.max(event_time);
        state.last_arrival_ordinal = Some(price.metadata.arrival_ordinal);
        for size in TIMEFRAMES_NS {
            let start = event_time.div_euclid(size) * size;
            let end = start + size;
            let bar = state.bars.entry((size, start)).or_insert_with(|| BarAcc {
                snapshot: CandleSnapshot {
                    schema_version: SCHEMA_VERSION,
                    instrument: price.instrument.clone(),
                    processing_timestamp_ns: price.metadata.receive_timestamp_ns,
                    source_last_trade_timestamp_ns: price.metadata.source_last_trade_timestamp_ns,
                    freshness: Freshness::Fresh,
                    quality: price.quality,
                    provenance: Provenance::from(&price.metadata),
                    market_session_id: session.clone(),
                    timeframe_ns: size,
                    start_ns: start,
                    end_ns: end,
                    open_nanos: price.price_nanos,
                    high_nanos: price.price_nanos,
                    low_nanos: price.price_nanos,
                    close_nanos: price.price_nanos,
                    volume: None,
                    observed_volume_delta: 0,
                    observation_count: 0,
                    finalized: false,
                    source: CandleSource::PriceOnlyQuote,
                    timestamp_policy: self.config.timestamp_policy,
                },
                first_key: (event_time, price.metadata.arrival_ordinal),
                last_key: (event_time, price.metadata.arrival_ordinal),
                volume_complete: true,
                saw_volume: false,
            });
            let ordering_key = (event_time, price.metadata.arrival_ordinal);
            if ordering_key < bar.first_key {
                bar.first_key = ordering_key;
                bar.snapshot.open_nanos = price.price_nanos;
            }
            if ordering_key >= bar.last_key {
                bar.last_key = ordering_key;
                bar.snapshot.close_nanos = price.price_nanos;
            }
            bar.snapshot.high_nanos = bar.snapshot.high_nanos.max(price.price_nanos);
            bar.snapshot.low_nanos = bar.snapshot.low_nanos.min(price.price_nanos);
            bar.snapshot.observation_count += 1;
            bar.snapshot.processing_timestamp_ns = price.metadata.receive_timestamp_ns;
            bar.snapshot.source_last_trade_timestamp_ns =
                price.metadata.source_last_trade_timestamp_ns;
            bar.snapshot.provenance = Provenance::from(&price.metadata);
            if let Some(v) = volume {
                bar.saw_volume = true;
                bar.snapshot.source = CandleSource::QuoteSampledLtpWithCumulativeDelta;
                let previous_ns = match self.config.timestamp_policy {
                    TimestampPolicy::Receive => v.previous_receive_timestamp_ns,
                    TimestampPolicy::SourceLastTrade => v.previous_source_last_trade_timestamp_ns,
                };
                match (v.incremental_volume, previous_ns) {
                    (Some(delta), Some(previous))
                        if previous >= start && previous <= event_time =>
                    {
                        bar.snapshot.observed_volume_delta =
                            bar.snapshot.observed_volume_delta.saturating_add(delta);
                    }
                    _ => bar.volume_complete = false,
                }
                bar.snapshot.quality.inconsistent_last_trade_quantity |=
                    v.quality.inconsistent_last_trade_quantity;
            } else {
                bar.volume_complete = false;
            }
            bar.snapshot.quality.volume_interval_incomplete = !bar.volume_complete;
            bar.snapshot.volume = (bar.saw_volume && bar.volume_complete)
                .then_some(bar.snapshot.observed_volume_delta);
        }
        let watermark = state
            .max_event_time_ns
            .saturating_sub(self.config.max_lateness_ns);
        let ready: Vec<_> = state
            .bars
            .keys()
            .copied()
            .filter(|(size, start)| start + size <= watermark)
            .collect();
        for key in ready {
            if let Some(mut bar) = state.bars.remove(&key) {
                bar.snapshot.finalized = true;
                finalized.push(bar.snapshot);
            }
        }
        finalized.sort_by_key(|bar| (bar.start_ns, bar.timeframe_ns));
        Ok(finalized)
    }

    pub fn open_snapshots(&self) -> Vec<CandleSnapshot> {
        self.instruments
            .values()
            .flat_map(|s| s.bars.values().map(|bar| bar.snapshot.clone()))
            .collect()
    }

    pub fn open_for(&self, instrument: &Instrument) -> Vec<CandleSnapshot> {
        let key = (
            instrument.venue.clone(),
            instrument.exchange_segment.clone(),
            instrument.security_id.clone(),
        );
        self.instruments
            .get(&key)
            .map(|state| {
                state
                    .bars
                    .values()
                    .map(|bar| bar.snapshot.clone())
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Explicit end-of-session flush. Bars are finalized without inventing
    /// observations in the unobserved remainder of an interval.
    pub fn close_session(&mut self) -> Vec<CandleSnapshot> {
        let mut result = Vec::new();
        for state in self.instruments.values_mut() {
            for (_, mut bar) in std::mem::take(&mut state.bars) {
                bar.snapshot.finalized = true;
                result.push(bar.snapshot);
            }
            state.session.clear();
            state.feed_session.clear();
        }
        result.sort_by_key(|bar| {
            (
                bar.start_ns,
                bar.timeframe_ns,
                bar.instrument.security_id.clone(),
            )
        });
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use quant_core::{PriceSource, SourceProtocol, SourceTimestampMetadata, VolumeStatus};

    struct Calendar;
    impl MarketCalendar for Calendar {
        fn session_at(&self, ns: i64) -> Option<String> {
            Some(if ns < 10_000_000_000 { "day1" } else { "day2" }.into())
        }
    }
    fn price(t: i64, ordinal: u64, value: i64) -> PriceObservation {
        PriceObservation {
            instrument: Instrument {
                venue: "NSE".into(),
                exchange_segment: "NSE_EQ".into(),
                security_id: "1333".into(),
                symbol: "".into(),
            },
            metadata: SourceTimestampMetadata {
                receive_timestamp_ns: t,
                source_last_trade_timestamp_ns: Some(t),
                feed_session_id: "a".into(),
                arrival_ordinal: ordinal,
                frame_id: ordinal,
                packet_offset: 0,
                protocol: SourceProtocol::DhanStandardV2,
            },
            source: PriceSource::Quote,
            price_nanos: value,
            quality: MarketDataQuality::default(),
        }
    }
    fn volume(
        p: &PriceObservation,
        delta: Option<u64>,
        previous: Option<i64>,
    ) -> VolumeObservation {
        VolumeObservation {
            instrument: p.instrument.clone(),
            metadata: p.metadata.clone(),
            cumulative_traded_volume: 100,
            incremental_volume: delta,
            previous_receive_timestamp_ns: previous,
            previous_source_last_trade_timestamp_ns: previous,
            status: if delta.is_some() {
                VolumeStatus::Increased
            } else {
                VolumeStatus::BaselineUnknown
            },
            quality: MarketDataQuality::default(),
        }
    }
    fn engine(lateness: i64) -> BarEngine {
        BarEngine::new(BarConfig {
            timestamp_policy: TimestampPolicy::Receive,
            max_lateness_ns: lateness,
            max_open_bars_per_instrument: 100,
        })
        .unwrap()
    }
    #[test]
    fn six_timeframes_and_known_ohlcv() {
        let mut e = engine(0);
        let p1 = price(100_000_000, 1, 100);
        e.observe(&p1, Some(&volume(&p1, None, None)), &Calendar)
            .unwrap();
        let p2 = price(200_000_000, 2, 105);
        e.observe(
            &p2,
            Some(&volume(&p2, Some(3), Some(100_000_000))),
            &Calendar,
        )
        .unwrap();
        let p3 = price(300_000_000, 3, 97);
        e.observe(
            &p3,
            Some(&volume(&p3, Some(4), Some(200_000_000))),
            &Calendar,
        )
        .unwrap();
        let p4 = price(1_100_000_000, 4, 102);
        let closed = e
            .observe(
                &p4,
                Some(&volume(&p4, Some(5), Some(300_000_000))),
                &Calendar,
            )
            .unwrap();
        assert_eq!(closed.len(), 1);
        let c = &closed[0];
        assert_eq!(
            (c.open_nanos, c.high_nanos, c.low_nanos, c.close_nanos),
            (100, 105, 97, 97)
        );
        assert_eq!(c.observation_count, 3);
        assert_eq!(c.observed_volume_delta, 7);
        assert_eq!(c.volume, None); // first quote had no baseline
        assert!(c.finalized);
        assert_eq!(e.open_snapshots().len(), 6);
        let remaining = e.close_session();
        assert_eq!(remaining.len(), 6);
        assert!(remaining.iter().any(|c| c.timeframe_ns == 900_000_000_000));
    }
    #[test]
    fn bounded_lateness_and_session_change() {
        let mut e = engine(500_000_000);
        let p1 = price(100_000_000, 1, 100);
        e.observe(&p1, None, &Calendar).unwrap();
        let p2 = price(1_100_000_000, 2, 105);
        e.observe(&p2, None, &Calendar).unwrap();
        let late_but_open = price(900_000_000, 3, 99);
        e.observe(&late_but_open, None, &Calendar).unwrap();
        let p3 = price(2_000_000_000, 4, 106);
        assert_eq!(e.observe(&p3, None, &Calendar).unwrap().len(), 1);
        let too_late = price(500_000_000, 5, 80);
        assert_eq!(
            e.observe(&too_late, None, &Calendar),
            Err(BarError::LateAfterFinalization)
        );
        let new_session = price(10_100_000_000, 6, 110);
        assert!(!e.observe(&new_session, None, &Calendar).unwrap().is_empty());
    }

    #[test]
    fn reconnect_finalizes_open_bars_as_incomplete() {
        let mut e = engine(500_000_000);
        let first = price(100_000_000, 1, 100);
        e.observe(&first, None, &Calendar).unwrap();
        let mut second = price(200_000_000, 2, 101);
        second.metadata.feed_session_id = "reconnected".into();
        let closed = e.observe(&second, None, &Calendar).unwrap();
        assert_eq!(closed.len(), 6);
        assert!(
            closed
                .iter()
                .all(|bar| bar.finalized && bar.quality.volume_interval_incomplete)
        );
    }
}
