//! Offline-testable adapter from standard-feed frames to durable engine output.
//! The transport must timestamp immediately on WebSocket frame receipt.
use crate::pipeline::{EngineError, EngineOutput, MarketEngine};
use quant_core::{Instrument, MarketEvent, PriceSource};
use quant_journal::{JournalRecord, JournalWriter, encoded_record_bytes, raw_standard_record};
use std::io;

const MAX_SCOPED_JOURNAL_BYTES: u64 = 64 * 1024 * 1024;
use quant_market_data::dhan_standard::{self, DecodedPacket};

#[derive(Debug)]
pub enum CaptureError {
    Decode(dhan_standard::DecodeError),
    UnsupportedCode(u8),
    UnexpectedInstrument,
    NotConnected,
    StaleObservation,
    Storage(io::Error),
    OrderingOverflow,
    Engine(EngineError),
    Poisoned,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct CaptureCounters {
    pub frames_received: u64,
    pub packets_decoded: u64,
    pub packets_durable: u64,
    pub malformed_frames: u64,
    pub unsupported_frames: u64,
    pub processing_failures: u64,
    pub identity_rejections: u64,
    pub raw_frames_durable: u64,
}

pub struct StandardCapture {
    next_frame_id: u64,
    next_arrival_ordinal: u64,
    counters: CaptureCounters,
    poisoned: bool,
    expected_instrument: Option<Instrument>,
}

impl Default for StandardCapture {
    fn default() -> Self {
        Self {
            next_frame_id: 1,
            next_arrival_ordinal: 1,
            counters: CaptureCounters::default(),
            poisoned: false,
            expected_instrument: None,
        }
    }
}

impl StandardCapture {
    /// Reject other securities before any record from the frame reaches QJ01.
    pub fn for_instrument(instrument: Instrument) -> Self {
        Self {
            expected_instrument: Some(instrument),
            ..Self::default()
        }
    }

    pub fn expected_instrument(&self) -> Option<&Instrument> {
        self.expected_instrument.as_ref()
    }

    pub fn counters(&self) -> CaptureCounters {
        self.counters
    }

    pub fn on_frame(
        &mut self,
        frame: &[u8],
        receive_timestamp_ns: i64,
        feed_session_id: &str,
        writer: &mut JournalWriter,
        engine: &mut MarketEngine,
    ) -> Result<Vec<EngineOutput>, CaptureError> {
        self.on_frame_observed(
            frame,
            receive_timestamp_ns,
            feed_session_id,
            writer,
            engine,
            |_, _| {},
        )
    }

    /// The observer sees each normalized record only after its file sync and
    /// deterministic engine processing have both succeeded.
    pub fn on_frame_observed(
        &mut self,
        frame: &[u8],
        receive_timestamp_ns: i64,
        feed_session_id: &str,
        writer: &mut JournalWriter,
        engine: &mut MarketEngine,
        mut observe: impl FnMut(&JournalRecord, &EngineOutput),
    ) -> Result<Vec<EngineOutput>, CaptureError> {
        if self.poisoned {
            return Err(CaptureError::Poisoned);
        }
        self.counters.frames_received += 1;
        self.persist_raw_if_scoped(frame, receive_timestamp_ns, feed_session_id, writer)?;
        let packets = dhan_standard::decode_frame(
            frame,
            receive_timestamp_ns,
            feed_session_id,
            self.next_frame_id,
            self.next_arrival_ordinal,
        )
        .map_err(|error| {
            self.counters.malformed_frames += 1;
            if self.expected_instrument.is_some() {
                self.poisoned = true;
            }
            CaptureError::Decode(error)
        })?;
        let decoded_count = packets.len();
        let mut events = Vec::with_capacity(packets.len());
        for packet in packets {
            let event = match packet {
                DecodedPacket::Price(price) => {
                    if self.expected_instrument.is_some() {
                        self.counters.unsupported_frames += 1;
                        self.poisoned = true;
                        return Err(CaptureError::UnsupportedCode(2));
                    }
                    MarketEvent::Price(price)
                }
                DecodedPacket::Quote(quote) => {
                    if self.expected_instrument.is_some()
                        && quote.price.source != PriceSource::Quote
                    {
                        self.counters.unsupported_frames += 1;
                        self.poisoned = true;
                        return Err(CaptureError::UnsupportedCode(8));
                    }
                    MarketEvent::Quote(quote)
                }
                DecodedPacket::Session(session) => MarketEvent::Session(Box::new(session)),
                DecodedPacket::AuxiliaryOpenInterest { instrument, .. } => {
                    if self.expected_instrument.as_ref().is_some_and(|expected| {
                        instrument.venue != expected.venue
                            || instrument.exchange_segment != expected.exchange_segment
                            || instrument.security_id != expected.security_id
                    }) {
                        self.counters.identity_rejections += 1;
                        self.poisoned = true;
                        return Err(CaptureError::UnexpectedInstrument);
                    }
                    continue; // Raw packet retained; OI is not an NSE cash quote.
                }
                DecodedPacket::Unsupported { code, .. } => {
                    self.counters.unsupported_frames += 1;
                    self.poisoned = self.expected_instrument.is_some();
                    return Err(CaptureError::UnsupportedCode(code));
                }
            };
            if let Some(expected) = &self.expected_instrument {
                let actual = match &event {
                    MarketEvent::Price(price) => Some(&price.instrument),
                    MarketEvent::Quote(quote) => Some(&quote.price.instrument),
                    MarketEvent::Session(session) => session.instrument.as_ref(),
                    _ => None,
                };
                if actual.is_some_and(|actual| {
                    actual.venue != expected.venue
                        || actual.exchange_segment != expected.exchange_segment
                        || actual.security_id != expected.security_id
                }) {
                    self.counters.identity_rejections += 1;
                    self.poisoned = true;
                    return Err(CaptureError::UnexpectedInstrument);
                }
            }
            events.push(event);
        }
        let next_frame_id = self
            .next_frame_id
            .checked_add(1)
            .ok_or(CaptureError::OrderingOverflow)?;
        let next_arrival_ordinal = self
            .next_arrival_ordinal
            .checked_add(decoded_count as u64)
            .ok_or(CaptureError::OrderingOverflow)?;
        self.counters.packets_decoded += decoded_count as u64;
        let mut outputs = Vec::with_capacity(events.len());
        for event in events {
            let record = JournalRecord {
                session_id: feed_session_id.into(),
                event,
            };
            if self.expected_instrument.is_some() {
                self.check_capacity(writer, &record)?;
            }
            match engine.append_and_process(writer, &record) {
                Ok(result) => {
                    self.counters.packets_durable += 1;
                    observe(&record, &result);
                    outputs.push(result);
                }
                Err(error) => {
                    if !matches!(error, EngineError::Journal(_)) {
                        self.counters.packets_durable += 1;
                    }
                    self.poisoned = true;
                    self.counters.processing_failures += 1;
                    return Err(CaptureError::Engine(error));
                }
            }
        }
        self.next_frame_id = next_frame_id;
        self.next_arrival_ordinal = next_arrival_ordinal;
        Ok(outputs)
    }

    fn check_capacity(
        &mut self,
        writer: &JournalWriter,
        record: &JournalRecord,
    ) -> Result<(), CaptureError> {
        let needed = encoded_record_bytes(record).map_err(|error| {
            self.poisoned = true;
            CaptureError::Storage(error)
        })?;
        let used = writer.estimated_bytes().map_err(|error| {
            self.poisoned = true;
            CaptureError::Storage(error)
        })?;
        if used.saturating_add(needed) > MAX_SCOPED_JOURNAL_BYTES {
            self.poisoned = true;
            return Err(CaptureError::Storage(io::Error::other(
                "scoped raw journal byte cap reached",
            )));
        }
        Ok(())
    }

    fn append_bounded(
        &mut self,
        writer: &mut JournalWriter,
        record: &JournalRecord,
    ) -> Result<(), CaptureError> {
        self.check_capacity(writer, record)?;
        if let Err(error) = writer.append(record).and_then(|_| writer.sync()) {
            self.poisoned = true;
            return Err(CaptureError::Storage(error));
        }
        Ok(())
    }

    /// Keep the exact binary evidence while refusing a stale observation.
    pub fn retain_stale_frame(
        &mut self,
        frame: &[u8],
        receive_timestamp_ns: i64,
        feed_session_id: &str,
        writer: &mut JournalWriter,
    ) -> Result<(), CaptureError> {
        if self.poisoned || self.expected_instrument.is_none() {
            return Err(CaptureError::NotConnected);
        }
        self.counters.frames_received += 1;
        let result =
            self.persist_raw_if_scoped(frame, receive_timestamp_ns, feed_session_id, writer);
        self.poisoned = true;
        result
    }

    fn persist_raw_if_scoped(
        &mut self,
        frame: &[u8],
        receive_timestamp_ns: i64,
        feed_session_id: &str,
        writer: &mut JournalWriter,
    ) -> Result<(), CaptureError> {
        if self.expected_instrument.is_none() {
            return Ok(());
        }
        let raw = raw_standard_record(
            feed_session_id,
            self.next_frame_id,
            receive_timestamp_ns,
            frame,
        )
        .map_err(|error| {
            self.poisoned = true;
            CaptureError::Storage(error)
        })?;
        if writer.recovered_tail_bytes() != 0 {
            self.poisoned = true;
            return Err(CaptureError::Storage(io::Error::new(
                io::ErrorKind::InvalidData,
                "raw journal has a recovered tail",
            )));
        }
        self.append_bounded(writer, &raw)?;
        self.counters.raw_frames_durable += 1;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{MarketCalendar, TimestampPolicy, bars::BarConfig, features::FeatureConfig};
    use quant_journal::{DurabilityPolicy, JournalReader};

    struct Calendar;
    impl MarketCalendar for Calendar {
        fn session_at(&self, _: i64) -> Option<String> {
            Some("test".into())
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
                stale_after_ns: 1_000_000_000,
                rolling_window: 4,
            },
        )
        .unwrap()
    }
    fn quote_packet_for(id: i32) -> Vec<u8> {
        let mut p = vec![4];
        p.extend(50_u16.to_le_bytes());
        p.push(1);
        p.extend(id.to_le_bytes());
        p.extend(100.0_f32.to_le_bytes());
        p.extend(1_i16.to_le_bytes());
        p.extend(1_700_000_000_i32.to_le_bytes());
        p.extend(100.0_f32.to_le_bytes());
        p.extend(100_i32.to_le_bytes());
        p.extend(10_i32.to_le_bytes());
        p.extend(10_i32.to_le_bytes());
        for v in [100.0_f32, 99.0, 101.0, 98.0] {
            p.extend(v.to_le_bytes());
        }
        p
    }
    fn quote_packet() -> Vec<u8> {
        quote_packet_for(1333)
    }

    #[test]
    fn scoped_reliance_quote_preserves_receipt_and_rejects_other_id() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("capture.qj");
        let mut writer = JournalWriter::open(&path, DurabilityPolicy::EveryEvent).unwrap();
        let instrument = Instrument {
            venue: "NSE".into(),
            exchange_segment: "NSE_EQ".into(),
            security_id: "2885".into(),
            symbol: "RELIANCE".into(),
        };
        let mut capture = StandardCapture::for_instrument(instrument);
        let mut engine = engine();
        let mut observed = 0;
        capture
            .on_frame_observed(
                &quote_packet_for(2885),
                1_700_000_000_500_000_000,
                "fixture-session",
                &mut writer,
                &mut engine,
                |record, _| {
                    assert!(matches!(record.event, MarketEvent::Quote(_)));
                    observed += 1;
                },
            )
            .unwrap();
        assert_eq!(observed, 1);
        let mut oi = vec![5, 12, 0, 1];
        oi.extend(2885_i32.to_le_bytes());
        oi.extend(0_i32.to_le_bytes());
        assert!(
            capture
                .on_frame(
                    &oi,
                    1_700_000_000_700_000_000,
                    "fixture-session",
                    &mut writer,
                    &mut engine
                )
                .unwrap()
                .is_empty()
        );
        assert!(matches!(
            capture.on_frame(
                &quote_packet_for(1333),
                1_700_000_001_500_000_000,
                "fixture-session",
                &mut writer,
                &mut engine
            ),
            Err(CaptureError::UnexpectedInstrument)
        ));
        writer.close().unwrap();
        let mut reader = JournalReader::open(&path).unwrap();
        let raw = reader.next_record().unwrap().unwrap();
        let MarketEvent::RawStandardFrame(raw_frame) = raw.event else {
            panic!("raw frame required")
        };
        assert_eq!(raw_frame.bytes, quote_packet_for(2885));
        assert_eq!(raw_frame.frame_id, 1);
        assert_eq!(raw_frame.receive_timestamp_ns, 1_700_000_000_500_000_000);
        let record = reader.next_record().unwrap().unwrap();
        let MarketEvent::Quote(quote) = record.event else {
            panic!("quote required")
        };
        assert_eq!(quote.price.instrument.security_id, "2885");
        assert_eq!(
            quote.price.metadata.receive_timestamp_ns,
            1_700_000_000_500_000_000
        );
        assert_eq!(
            quote.price.metadata.source_last_trade_timestamp_ns,
            Some(1_700_000_000_000_000_000)
        );
        assert_eq!(
            quote.price.metadata.protocol,
            quant_core::SourceProtocol::DhanStandardV2
        );
        assert_eq!(quote.cumulative_traded_volume, 100);
        assert_eq!(quote.price.metadata.frame_id, raw_frame.frame_id);
        assert!(matches!(
            reader.next_record().unwrap().unwrap().event,
            MarketEvent::RawStandardFrame(_)
        ));
        let rejected_raw = reader.next_record().unwrap().unwrap();
        assert!(matches!(
            rejected_raw.event,
            MarketEvent::RawStandardFrame(_)
        ));
        assert!(reader.next_record().unwrap().is_none());
    }

    #[test]
    fn malformed_scoped_packet_keeps_only_raw_evidence() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("malformed.qj");
        let instrument = Instrument {
            venue: "NSE".into(),
            exchange_segment: "NSE_EQ".into(),
            security_id: "2885".into(),
            symbol: "RELIANCE".into(),
        };
        let mut capture = StandardCapture::for_instrument(instrument);
        let mut writer = JournalWriter::open(&path, DurabilityPolicy::EveryEvent).unwrap();
        let packet = quote_packet_for(2885);
        let frame = &packet[..10];
        let mut market_engine = engine();
        assert!(matches!(
            capture.on_frame(
                frame,
                123,
                "fixture-session",
                &mut writer,
                &mut market_engine
            ),
            Err(CaptureError::Decode(_))
        ));
        assert!(matches!(
            capture.on_frame(
                frame,
                124,
                "fixture-session",
                &mut writer,
                &mut market_engine
            ),
            Err(CaptureError::Poisoned)
        ));
        writer.close().unwrap();
        let mut reader = JournalReader::open(&path).unwrap();
        let record = reader.next_record().unwrap().unwrap();
        let MarketEvent::RawStandardFrame(raw) = record.event else {
            panic!("raw frame required")
        };
        assert_eq!(raw.bytes, frame);
        assert!(reader.next_record().unwrap().is_none());
    }
    #[test]
    fn frame_capture_is_durable_and_malformed_is_visible() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("capture.qj");
        let mut writer = JournalWriter::open(&path, DurabilityPolicy::EveryEvent).unwrap();
        let mut capture = StandardCapture::default();
        let mut engine = engine();
        assert_eq!(
            capture
                .on_frame(&quote_packet(), 100, "feed-a", &mut writer, &mut engine)
                .unwrap()
                .len(),
            1
        );
        assert!(matches!(
            capture.on_frame(
                &quote_packet()[..10],
                200,
                "feed-a",
                &mut writer,
                &mut engine
            ),
            Err(CaptureError::Decode(_))
        ));
        assert_eq!(
            capture.counters(),
            CaptureCounters {
                frames_received: 2,
                packets_decoded: 1,
                packets_durable: 1,
                malformed_frames: 1,
                unsupported_frames: 0,
                processing_failures: 0,
                identity_rejections: 0,
                raw_frames_durable: 0
            }
        );
        writer.close().unwrap();
        let mut reader = JournalReader::open(&path).unwrap();
        assert!(matches!(
            reader.next_record().unwrap().unwrap().event,
            MarketEvent::Quote(_)
        ));
        assert!(reader.next_record().unwrap().is_none());
    }
}
