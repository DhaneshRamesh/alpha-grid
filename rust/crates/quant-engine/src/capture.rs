//! Offline-testable adapter from standard-feed frames to durable engine output.
//! The transport must timestamp immediately on WebSocket frame receipt.
use crate::pipeline::{EngineError, EngineOutput, MarketEngine};
use quant_core::MarketEvent;
use quant_journal::{JournalRecord, JournalWriter};
use quant_market_data::dhan_standard::{self, DecodedPacket};

#[derive(Debug)]
pub enum CaptureError {
    Decode(dhan_standard::DecodeError),
    UnsupportedCode(u8),
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
}

pub struct StandardCapture {
    next_frame_id: u64,
    next_arrival_ordinal: u64,
    counters: CaptureCounters,
    poisoned: bool,
}

impl Default for StandardCapture {
    fn default() -> Self {
        Self {
            next_frame_id: 1,
            next_arrival_ordinal: 1,
            counters: CaptureCounters::default(),
            poisoned: false,
        }
    }
}

impl StandardCapture {
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
        if self.poisoned {
            return Err(CaptureError::Poisoned);
        }
        self.counters.frames_received += 1;
        let packets = dhan_standard::decode_frame(
            frame,
            receive_timestamp_ns,
            feed_session_id,
            self.next_frame_id,
            self.next_arrival_ordinal,
        )
        .map_err(|error| {
            self.counters.malformed_frames += 1;
            CaptureError::Decode(error)
        })?;
        let mut events = Vec::with_capacity(packets.len());
        for packet in packets {
            let event = match packet {
                DecodedPacket::Price(price) => MarketEvent::Price(price),
                DecodedPacket::Quote(quote) => MarketEvent::Quote(quote),
                DecodedPacket::Session(session) => MarketEvent::Session(Box::new(session)),
                DecodedPacket::Unsupported { code, .. } => {
                    self.counters.unsupported_frames += 1;
                    return Err(CaptureError::UnsupportedCode(code));
                }
            };
            events.push(event);
        }
        let next_frame_id = self
            .next_frame_id
            .checked_add(1)
            .ok_or(CaptureError::OrderingOverflow)?;
        let next_arrival_ordinal = self
            .next_arrival_ordinal
            .checked_add(events.len() as u64)
            .ok_or(CaptureError::OrderingOverflow)?;
        self.counters.packets_decoded += events.len() as u64;
        let mut outputs = Vec::with_capacity(events.len());
        for event in events {
            let record = JournalRecord {
                session_id: feed_session_id.into(),
                event,
            };
            match engine.append_and_process(writer, &record) {
                Ok(result) => {
                    self.counters.packets_durable += 1;
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
    fn quote_packet() -> Vec<u8> {
        let mut p = vec![4];
        p.extend(50_u16.to_le_bytes());
        p.push(1);
        p.extend(1333_i32.to_le_bytes());
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
                processing_failures: 0
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
