//! Durable append precedes derivation. Replay uses the same deterministic path.
use crate::bars::{BarConfig, BarEngine, BarError, CandleSnapshot};
use crate::features::{FeatureConfig, FeatureEngine, FeatureOutput};
use crate::volume::VolumeTracker;
use crate::{MarketCalendar, TimestampPolicy};
use quant_core::{MarketEvent, MarketSessionKind, VolumeObservation};
use quant_journal::{JournalRecord, JournalWriter};
use std::io;

#[derive(Clone, Debug, PartialEq)]
pub struct EngineOutput {
    pub volume: Option<VolumeObservation>,
    pub finalized_candles: Vec<CandleSnapshot>,
    pub provisional_candles: Vec<CandleSnapshot>,
    pub features: Option<FeatureOutput>,
}

#[derive(Debug)]
pub enum EngineError {
    Journal(io::Error),
    Bar(BarError),
    Volume(&'static str),
    Feature(&'static str),
    Poisoned,
}

pub struct MarketEngine {
    calendar: Box<dyn MarketCalendar>,
    volume: VolumeTracker,
    bars: BarEngine,
    features: FeatureEngine,
    timestamp_policy: TimestampPolicy,
    poisoned: bool,
}

impl MarketEngine {
    pub fn new(
        calendar: Box<dyn MarketCalendar>,
        bar_config: BarConfig,
        feature_config: FeatureConfig,
    ) -> Result<Self, EngineError> {
        Ok(Self {
            calendar,
            volume: VolumeTracker::default(),
            bars: BarEngine::new(bar_config).map_err(EngineError::Bar)?,
            features: FeatureEngine::new(feature_config).map_err(EngineError::Feature)?,
            timestamp_policy: bar_config.timestamp_policy,
            poisoned: false,
        })
    }

    /// Append and fsync before publishing a derived output. A failed append or
    /// derivation poisons this instance; operators must investigate and replay.
    pub fn append_and_process(
        &mut self,
        writer: &mut JournalWriter,
        record: &JournalRecord,
    ) -> Result<EngineOutput, EngineError> {
        if self.poisoned {
            return Err(EngineError::Poisoned);
        }
        if let Err(error) = writer.append(record) {
            self.poisoned = true;
            return Err(EngineError::Journal(error));
        }
        if writer.pending() > 0 {
            if let Err(error) = writer.sync() {
                self.poisoned = true;
                return Err(EngineError::Journal(error));
            }
        }
        self.process(record)
    }

    /// Use only for replay or measurements of processing without durability.
    /// Live capture must call `append_and_process`.
    pub fn process(&mut self, record: &JournalRecord) -> Result<EngineOutput, EngineError> {
        if self.poisoned {
            return Err(EngineError::Poisoned);
        }
        let result = self.apply(record);
        if result.is_err() {
            self.poisoned = true;
        }
        result
    }

    fn apply(&mut self, record: &JournalRecord) -> Result<EngineOutput, EngineError> {
        let mut output = EngineOutput {
            volume: None,
            finalized_candles: Vec::new(),
            provisional_candles: Vec::new(),
            features: None,
        };
        match &record.event {
            MarketEvent::Price(price) => {
                output.finalized_candles = self
                    .bars
                    .observe(price, None, &*self.calendar)
                    .map_err(EngineError::Bar)?;
                output.provisional_candles = self.bars.open_for(&price.instrument);
                output.features = Some(
                    self.features
                        .observe_price(price, None, price.metadata.receive_timestamp_ns)
                        .map_err(EngineError::Feature)?,
                );
            }
            MarketEvent::Quote(quote) => {
                let session_time = match self.timestamp_policy {
                    TimestampPolicy::Receive => quote.price.metadata.receive_timestamp_ns,
                    TimestampPolicy::SourceLastTrade => quote
                        .price
                        .metadata
                        .source_last_trade_timestamp_ns
                        .ok_or(EngineError::Volume("missing source last-trade timestamp"))?,
                };
                let volume = self
                    .volume
                    .observe_at(quote, &*self.calendar, session_time)
                    .map_err(EngineError::Volume)?;
                output.finalized_candles = self
                    .bars
                    .observe(&quote.price, Some(&volume), &*self.calendar)
                    .map_err(EngineError::Bar)?;
                output.provisional_candles = self.bars.open_for(&quote.price.instrument);
                output.features = Some(
                    self.features
                        .observe_price(
                            &quote.price,
                            Some(&volume),
                            quote.price.metadata.receive_timestamp_ns,
                        )
                        .map_err(EngineError::Feature)?,
                );
                output.volume = Some(volume);
            }
            MarketEvent::Depth(depth) => {
                output.features = Some(
                    self.features
                        .observe_depth(depth, &record.session_id, depth.receive_timestamp_ns)
                        .map_err(EngineError::Feature)?,
                );
            }
            MarketEvent::Session(session) => {
                if matches!(session.kind, MarketSessionKind::Disconnected { .. }) {
                    self.volume.invalidate_all();
                    self.features.invalidate_all();
                    output.finalized_candles = self.bars.close_session();
                }
            }
            // Individual depth sides are retained in the journal; only paired
            // derived Depth events update book features. Legacy Trade records
            // remain readable and are not reinterpreted as standard-feed quotes.
            MarketEvent::DepthSide(_)
            | MarketEvent::Trade(_)
            | MarketEvent::RawStandardFrame(_) => {}
        }
        Ok(output)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::TimestampPolicy;
    use quant_core::{
        BOOK_LEVELS, DepthEvent, DepthLevel, Instrument, MarketDataQuality, PriceObservation,
        PriceSource, QuoteObservation, SourceProtocol, SourceTimestampMetadata,
    };
    use quant_journal::{DurabilityPolicy, JournalReader};

    struct Calendar;
    impl MarketCalendar for Calendar {
        fn session_at(&self, _: i64) -> Option<String> {
            Some("known-test-session".into())
        }
    }
    fn engine() -> MarketEngine {
        MarketEngine::new(
            Box::new(Calendar),
            BarConfig {
                timestamp_policy: TimestampPolicy::Receive,
                max_lateness_ns: 0,
                max_open_bars_per_instrument: 100,
            },
            FeatureConfig {
                max_cross_feed_age_ns: 1_000_000_000,
                stale_after_ns: 2_000_000_000,
                rolling_window: 8,
            },
        )
        .unwrap()
    }
    fn instrument() -> Instrument {
        Instrument {
            venue: "NSE".into(),
            exchange_segment: "NSE_EQ".into(),
            security_id: "1333".into(),
            symbol: "".into(),
        }
    }
    fn quote(t: i64, ordinal: u64, value: i64, cumulative: u64) -> JournalRecord {
        let p = PriceObservation {
            instrument: instrument(),
            metadata: SourceTimestampMetadata {
                receive_timestamp_ns: t,
                source_last_trade_timestamp_ns: Some(t),
                feed_session_id: "feed-a".into(),
                arrival_ordinal: ordinal,
                frame_id: ordinal,
                packet_offset: 0,
                protocol: SourceProtocol::DhanStandardV2,
            },
            source: PriceSource::Quote,
            price_nanos: value,
            quality: MarketDataQuality::default(),
        };
        JournalRecord {
            session_id: "known-test-session".into(),
            event: MarketEvent::Quote(Box::new(QuoteObservation {
                price: p,
                last_trade_quantity: 1,
                average_trade_price_nanos: None,
                cumulative_traded_volume: cumulative,
                total_sell_quantity: 10,
                total_buy_quantity: 10,
                day_open_nanos: None,
                day_close_nanos: None,
                day_high_nanos: None,
                day_low_nanos: None,
                open_interest: None,
                day_high_open_interest: None,
                day_low_open_interest: None,
                five_level_book: None,
            })),
        }
    }
    fn depth(t: i64) -> JournalRecord {
        let mut bids = [DepthLevel::default(); BOOK_LEVELS];
        let mut asks = [DepthLevel::default(); BOOK_LEVELS];
        bids[0] = DepthLevel {
            price_nanos: 99,
            quantity: 10,
            order_count: None,
        };
        asks[0] = DepthLevel {
            price_nanos: 101,
            quantity: 20,
            order_count: None,
        };
        JournalRecord {
            session_id: "depth-a".into(),
            event: MarketEvent::Depth(Box::new(DepthEvent {
                instrument: instrument(),
                exchange_timestamp_ns: None,
                receive_timestamp_ns: t,
                sequence: None,
                bids,
                asks,
            })),
        }
    }
    #[test]
    fn durable_capture_and_replay_are_identical() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("phase2b.qj");
        let mut writer = JournalWriter::open(
            &path,
            DurabilityPolicy::Group {
                max_events: 100,
                max_bytes: 1_000_000,
                max_delay_ms: 100,
            },
        )
        .unwrap();
        let mut online = engine();
        let events = [
            depth(100_000_000),
            quote(110_000_000, 1, 100, 100),
            quote(210_000_000, 2, 102, 103),
            quote(1_110_000_000, 3, 101, 105),
        ];
        let expected: Vec<_> = events
            .iter()
            .map(|r| online.append_and_process(&mut writer, r).unwrap())
            .collect();
        assert_eq!(writer.pending(), 0);
        writer.close().unwrap();
        let mut replayed = engine();
        let mut reader = JournalReader::open(&path).unwrap();
        let mut actual = Vec::new();
        while let Some(record) = reader.next_record().unwrap() {
            actual.push(replayed.process(&record).unwrap());
        }
        assert_eq!(actual, expected);
        assert_eq!(
            actual[0].features.as_ref().unwrap().features.midprice_nanos,
            Some(100)
        );
        assert_eq!(actual[3].finalized_candles.len(), 1);
        assert_eq!(
            actual[3].volume.as_ref().unwrap().incremental_volume,
            Some(2)
        );
    }
    #[test]
    fn journal_failure_prevents_feature_publication() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("phase2b.qj");
        let mut writer = JournalWriter::open(&path, DurabilityPolicy::EveryEvent).unwrap();
        let mut e = engine();
        let invalid = quote(100, 1, 0, 100);
        assert!(matches!(
            e.append_and_process(&mut writer, &invalid),
            Err(EngineError::Bar(BarError::InvalidPrice))
        ));
        assert!(matches!(
            e.append_and_process(&mut writer, &quote(200, 2, 100, 101)),
            Err(EngineError::Poisoned)
        ));
        // The invalid source event stays durable for investigation.
        writer.close().unwrap();
        assert!(
            JournalReader::open(&path)
                .unwrap()
                .next_record()
                .unwrap()
                .is_some()
        );
    }
}
