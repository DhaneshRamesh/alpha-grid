//! Local DATA_ONLY fixture capture and durable replay. Never opens a Dhan session.
use quant_core::{Instrument, MarketEvent};
use quant_engine::bars::BarConfig;
use quant_engine::features::FeatureConfig;
use quant_engine::pipeline::MarketEngine;
use quant_engine::{MarketCalendar, TimestampPolicy};
use quant_journal::{DurabilityPolicy, JournalReader, JournalRecord, JournalWriter};
use quant_market_data::{dhan_standard, dhan20};
use quant_telemetry::gateway::{DemoControls, Gateway, GatewayConfig};
use quant_telemetry::protocol::AuditEvent;
use quant_telemetry::read_model::{TelemetryPublisher, TelemetryShared, now_ns};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;
use tokio::net::TcpListener;
use tokio::sync::watch;

const SESSION: &str = "synthetic-replay-demo";
const BASE_SECONDS: i32 = 1_700_000_000;

struct DemoCalendar;
impl MarketCalendar for DemoCalendar {
    fn session_at(&self, _: i64) -> Option<String> {
        Some(SESSION.into())
    }
}
fn engine() -> MarketEngine {
    MarketEngine::new(
        Box::new(DemoCalendar),
        BarConfig {
            timestamp_policy: TimestampPolicy::Receive,
            max_lateness_ns: 0,
            max_open_bars_per_instrument: 100,
        },
        FeatureConfig {
            max_cross_feed_age_ns: 2_000_000_000,
            stale_after_ns: 5_000_000_000,
            rolling_window: 32,
        },
    )
    .expect("valid demo engine")
}
fn instruments() -> Vec<Instrument> {
    [(1333, "RELIANCE"), (2885, "TATAMOTORS")]
        .into_iter()
        .map(|(id, symbol)| Instrument {
            venue: "NSE".into(),
            exchange_segment: "NSE_EQ".into(),
            security_id: id.to_string(),
            symbol: symbol.into(),
        })
        .collect()
}
fn quote_frame(id: i32, step: i32) -> Vec<u8> {
    let base = if id == 1333 { 100.0 } else { 120.0 };
    let price = base + (step % 20) as f32 * 0.05;
    let mut packet = vec![4];
    packet.extend(50_u16.to_le_bytes());
    packet.push(1);
    packet.extend(id.to_le_bytes());
    packet.extend(price.to_le_bytes());
    packet.extend(5_i16.to_le_bytes());
    packet.extend((BASE_SECONDS + step).to_le_bytes());
    packet.extend(price.to_le_bytes());
    packet.extend((100 + step * 5).to_le_bytes());
    packet.extend(10_i32.to_le_bytes());
    packet.extend(10_i32.to_le_bytes());
    for value in [base, base - 1.0, base + 1.0, base - 2.0] {
        packet.extend(value.to_le_bytes());
    }
    packet
}
fn previous_close_frame(id: i32) -> Vec<u8> {
    let mut packet = vec![6];
    packet.extend(16_u16.to_le_bytes());
    packet.push(1);
    packet.extend(id.to_le_bytes());
    packet.extend((if id == 1333 { 99.5_f32 } else { 119.5_f32 }).to_le_bytes());
    packet.extend(0_i32.to_le_bytes());
    packet
}
fn depth_frame(id: i32, step: i32) -> Vec<u8> {
    let mut frame = Vec::with_capacity(2 * dhan20::SIDE_BYTES);
    let base = if id == 1333 { 100.0 } else { 120.0 };
    for code in [41_u8, 51_u8] {
        frame.extend((dhan20::SIDE_BYTES as i16).to_le_bytes());
        frame.push(code);
        frame.push(1);
        frame.extend(id.to_le_bytes());
        frame.extend(0_u32.to_le_bytes());
        for level in 0..20 {
            let price = if code == 41 {
                base - 0.05 - level as f64 * 0.05
            } else {
                base + 0.05 + level as f64 * 0.05
            };
            frame.extend(price.to_le_bytes());
            frame.extend((10 + level as u32 + step as u32 % 4).to_le_bytes());
            frame.extend((2 + level as u32 % 3).to_le_bytes());
        }
    }
    frame
}
fn standard_records(
    frame: &[u8],
    timestamp: i64,
    ordinal: u64,
) -> Result<Vec<JournalRecord>, String> {
    let decoded = dhan_standard::decode_frame(frame, timestamp, SESSION, ordinal, ordinal)
        .map_err(|e| e.to_string())?;
    decoded
        .into_iter()
        .map(|packet| {
            let event = match packet {
                dhan_standard::DecodedPacket::Quote(value) => MarketEvent::Quote(value),
                dhan_standard::DecodedPacket::Session(value) => {
                    MarketEvent::Session(Box::new(value))
                }
                _ => return Err("unexpected standard fixture packet".into()),
            };
            Ok(JournalRecord {
                session_id: SESSION.into(),
                event,
            })
        })
        .collect()
}
fn depth_records(
    frame: &[u8],
    timestamp: i64,
    frame_id: u64,
) -> Result<Vec<JournalRecord>, String> {
    let decoded =
        dhan20::decode_frame(frame, timestamp, SESSION, frame_id).map_err(|e| e.to_string())?;
    let mut records: Vec<_> = decoded
        .iter()
        .filter_map(|packet| match packet {
            dhan20::DecodedPacket::Side(value) => Some(JournalRecord {
                session_id: SESSION.into(),
                event: MarketEvent::DepthSide(value.clone()),
            }),
            _ => None,
        })
        .collect();
    records.extend(
        dhan20::assemble_frame(&decoded)
            .into_iter()
            .map(|book| JournalRecord {
                session_id: SESSION.into(),
                event: MarketEvent::Depth(Box::new(book)),
            }),
    );
    if records.len() != 3 {
        return Err("fixture depth pair missing".into());
    }
    Ok(records)
}
fn build_fixture(path: &Path) -> Result<(), Box<dyn std::error::Error>> {
    if path.exists() {
        return Ok(());
    }
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let mut writer = JournalWriter::open(path, DurabilityPolicy::EveryEvent)?;
    let mut engine = engine();
    let base_ns = i64::from(BASE_SECONDS) * 1_000_000_000;
    let mut ordinal = 1_u64;
    for id in [1333, 2885] {
        for record in standard_records(&previous_close_frame(id), base_ns, ordinal)? {
            engine
                .append_and_process(&mut writer, &record)
                .map_err(|e| format!("fixture append: {e:?}"))?;
        }
        ordinal += 1;
    }
    for step in 0..120 {
        let timestamp = base_ns + i64::from(step) * 1_000_000_000;
        for id in [1333, 2885] {
            let mut records = standard_records(&quote_frame(id, step), timestamp, ordinal)?;
            ordinal += 1;
            records.extend(depth_records(&depth_frame(id, step), timestamp, ordinal)?);
            ordinal += 1;
            for record in records {
                engine
                    .append_and_process(&mut writer, &record)
                    .map_err(|e| format!("fixture append: {e:?}"))?;
            }
        }
    }
    writer.sync()?;
    Ok(())
}
fn replay(
    path: PathBuf,
    instruments: Vec<Instrument>,
    shared: Arc<TelemetryShared>,
    controls: Arc<DemoControls>,
    stop: Arc<AtomicBool>,
) {
    let mut seen_restart = controls.restart_requested.load(Ordering::Acquire);
    'run: while !stop.load(Ordering::Acquire) {
        shared.reset_replay();
        let mut reader = match JournalReader::open(&path) {
            Ok(v) => v,
            Err(error) => {
                shared.set_journal_blocked(true);
                eprintln!("replay journal: {error}");
                break;
            }
        };
        let mut engine = engine();
        let mut publisher = TelemetryPublisher::new(Arc::clone(&shared), &instruments);
        publisher.audit(AuditEvent {
            timestamp_ns: now_ns().to_string(),
            instrument_key: None,
            kind: "REPLAY_STARTED".into(),
            detail: "Synthetic Dhan-compatible QJ01 fixture; no live feed".into(),
        });
        let mut previous_time = None::<i64>;
        loop {
            if stop.load(Ordering::Acquire) {
                break 'run;
            }
            let requested = controls.restart_requested.load(Ordering::Acquire);
            if requested != seen_restart {
                seen_restart = requested;
                continue 'run;
            }
            if shared.replay_paused() {
                std::thread::sleep(Duration::from_millis(25));
                continue;
            }
            let record = match reader.next_record() {
                Ok(Some(v)) => v,
                Ok(None) => {
                    publisher.audit(AuditEvent {
                        timestamp_ns: now_ns().to_string(),
                        instrument_key: None,
                        kind: "REPLAY_COMPLETE".into(),
                        detail: "Reached journal EOF without decode error".into(),
                    });
                    shared.stop();
                    while !stop.load(Ordering::Acquire)
                        && controls.restart_requested.load(Ordering::Acquire) == seen_restart
                    {
                        std::thread::sleep(Duration::from_millis(50));
                    }
                    continue 'run;
                }
                Err(error) => {
                    shared.set_journal_blocked(true);
                    publisher.audit(AuditEvent {
                        timestamp_ns: now_ns().to_string(),
                        instrument_key: None,
                        kind: "REPLAY_ERROR".into(),
                        detail: error.to_string(),
                    });
                    break 'run;
                }
            };
            let timestamp = record.event.receive_timestamp_ns();
            if previous_time.is_some_and(|old| timestamp > old) {
                let speed = u64::from(shared.replay_speed().max(1));
                let mut remaining = ((timestamp - previous_time.unwrap_or(timestamp)) as u64
                    / speed)
                    .min(1_000_000_000);
                while remaining > 0 {
                    if stop.load(Ordering::Acquire)
                        || controls.restart_requested.load(Ordering::Acquire) != seen_restart
                    {
                        continue 'run;
                    }
                    if shared.replay_paused() {
                        std::thread::sleep(Duration::from_millis(25));
                        continue;
                    }
                    let chunk = remaining.min(25_000_000);
                    std::thread::sleep(Duration::from_nanos(chunk));
                    remaining -= chunk;
                }
            }
            previous_time = Some(timestamp);
            match engine.process(&record) {
                Ok(output) => publisher.observe(&record, &output, true),
                Err(error) => {
                    shared.set_journal_blocked(true);
                    publisher.audit(AuditEvent {
                        timestamp_ns: now_ns().to_string(),
                        instrument_key: None,
                        kind: "ENGINE_ERROR".into(),
                        detail: format!("{error:?}"),
                    });
                    break 'run;
                }
            }
        }
    }
    shared.stop();
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut journal = PathBuf::from(".local/phase2c/demo.qj");
    let mut port = 8765_u16;
    let mut arguments = std::env::args().skip(1);
    while let Some(flag) = arguments.next() {
        let value = arguments.next().ok_or("missing argument value")?;
        match flag.as_str() {
            "--journal" => journal = PathBuf::from(value),
            "--port" => port = value.parse()?,
            _ => return Err(format!("unknown option {flag}").into()),
        }
    }
    build_fixture(&journal)?;
    let instruments = instruments();
    let shared = TelemetryShared::new(&instruments, "REPLAY", SESSION)?;
    let controls = DemoControls::new(Arc::clone(&shared));
    let gateway = Gateway::new(Arc::clone(&shared), GatewayConfig::default())?
        .with_demo_controls(Arc::clone(&controls));
    let listener = TcpListener::bind(("127.0.0.1", port)).await?;
    eprintln!(
        "DATA_ONLY synthetic replay on http://127.0.0.1:{port}; journal {}",
        journal.display()
    );
    let stopped = Arc::new(AtomicBool::new(false));
    let worker = std::thread::spawn({
        let shared = Arc::clone(&shared);
        let stopped = Arc::clone(&stopped);
        move || replay(journal, instruments, shared, controls, stopped)
    });
    let (shutdown, shutdown_rx) = watch::channel(false);
    let server = tokio::spawn(gateway.serve(listener, shutdown_rx));
    tokio::signal::ctrl_c().await?;
    stopped.store(true, Ordering::Release);
    let _ = shutdown.send(true);
    server.await??;
    worker.join().map_err(|_| "replay worker panicked")?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fixture_round_trips_through_existing_decoders_and_journal() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("demo.qj");
        build_fixture(&path).unwrap();
        let mut reader = JournalReader::open(&path).unwrap();
        let mut count = 0;
        let mut depth = 0;
        let mut quotes = 0;
        while let Some(record) = reader.next_record().unwrap() {
            count += 1;
            match record.event {
                MarketEvent::Depth(_) => depth += 1,
                MarketEvent::Quote(_) => quotes += 1,
                _ => {}
            }
        }
        assert_eq!(count, 962);
        assert_eq!(depth, 240);
        assert_eq!(quotes, 240);
    }
}
