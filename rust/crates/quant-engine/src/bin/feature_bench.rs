//! Offline, rate-paced standard quote + 20-depth benchmark; no network or auth.
use quant_core::MarketEvent;
use quant_engine::bars::BarConfig;
use quant_engine::features::FeatureConfig;
use quant_engine::pipeline::MarketEngine;
use quant_engine::{MarketCalendar, TimestampPolicy};
use quant_journal::{DurabilityPolicy, JournalRecord, JournalWriter};
use quant_market_data::{dhan_standard, dhan20};
use serde::Serialize;
use std::fs::OpenOptions;
use std::io::{self, BufWriter, Write};
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, mpsc};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const RATES: [u64; 5] = [100, 500, 1_000, 5_000, 10_000];

struct Calendar;
impl MarketCalendar for Calendar {
    fn session_at(&self, _: i64) -> Option<String> {
        Some("synthetic-session".into())
    }
}

#[derive(Clone, Copy, Debug, Serialize)]
enum Mode {
    Compute,
    Durable,
}

struct Args {
    duration_seconds: u64,
    instruments: usize,
    queue_limit: usize,
    output: PathBuf,
    mode: Mode,
}

fn args() -> Result<Args, String> {
    let mut parsed = Args {
        duration_seconds: 5,
        instruments: 20,
        queue_limit: 2_000,
        output: PathBuf::from("feature-bench.jsonl"),
        mode: Mode::Compute,
    };
    let mut iter = std::env::args().skip(1);
    while let Some(option) = iter.next() {
        let value = iter
            .next()
            .ok_or_else(|| format!("missing value for {option}"))?;
        match option.as_str() {
            "--duration-seconds" => {
                parsed.duration_seconds = value.parse().map_err(|_| "invalid duration")?
            }
            "--instrument-count" => {
                parsed.instruments = value.parse().map_err(|_| "invalid instrument count")?
            }
            "--queue-limit" => {
                parsed.queue_limit = value.parse().map_err(|_| "invalid queue limit")?
            }
            "--output" => parsed.output = PathBuf::from(value),
            "--mode" => {
                parsed.mode = match value.as_str() {
                    "compute" => Mode::Compute,
                    "durable" => Mode::Durable,
                    _ => return Err("mode must be compute or durable".into()),
                }
            }
            _ => return Err(format!("unknown option {option}")),
        }
    }
    if parsed.duration_seconds == 0
        || parsed.duration_seconds > 1_800
        || parsed.instruments == 0
        || parsed.instruments > 50
        || parsed.queue_limit == 0
    {
        return Err("duration 1..1800, instruments 1..50, queue limit >=1 required".into());
    }
    Ok(parsed)
}

fn now_ns() -> io::Result<i64> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(io::Error::other)?
        .as_nanos()
        .try_into()
        .map_err(io::Error::other)
}

fn quote_packet(id: i32, serial: u64) -> Vec<u8> {
    let mut packet = Vec::with_capacity(50);
    packet.push(4);
    packet.extend(50_u16.to_le_bytes());
    packet.push(1);
    packet.extend(id.to_le_bytes());
    packet.extend((100.0_f32 + (serial % 30) as f32 * 0.05).to_le_bytes());
    packet.extend(1_i16.to_le_bytes());
    packet.extend(1_700_000_000_i32.to_le_bytes());
    packet.extend(100.0_f32.to_le_bytes());
    packet.extend(((serial / 5 + 100) as i32).to_le_bytes());
    packet.extend(10_i32.to_le_bytes());
    packet.extend(10_i32.to_le_bytes());
    for value in [100.0_f32, 99.0, 101.0, 98.0] {
        packet.extend(value.to_le_bytes());
    }
    packet
}

fn depth_packet(id: i32) -> Vec<u8> {
    let mut frame = Vec::with_capacity(2 * dhan20::SIDE_BYTES);
    for code in [41_u8, 51_u8] {
        frame.extend((dhan20::SIDE_BYTES as i16).to_le_bytes());
        frame.push(code);
        frame.push(1);
        frame.extend(id.to_le_bytes());
        frame.extend(0_u32.to_le_bytes());
        for level in 0..20 {
            let price = if code == 41 {
                99.0 - level as f64 * 0.05
            } else {
                101.0 + level as f64 * 0.05
            };
            frame.extend(price.to_le_bytes());
            frame.extend((10_u32 + level).to_le_bytes());
            frame.extend(2_u32.to_le_bytes());
        }
    }
    frame
}

struct Work {
    serial: u64,
    recv_ns: i64,
    instant: Instant,
    depth: bool,
    frame: Vec<u8>,
}

#[derive(Serialize)]
struct Sample {
    benchmark_version: &'static str,
    rate_offered_per_second: u64,
    mode: Mode,
    serial: u64,
    kind: &'static str,
    receive_timestamp_ns: i64,
    decode_ns: u64,
    state_update_ns: u64,
    journal_durable_ns: u64,
    fsync_max_ns: Option<u64>,
    receive_to_durable_ns: Option<u64>,
    end_to_end_ns: u64,
    durable_records: u64,
}

#[derive(Serialize)]
struct Summary {
    benchmark_version: &'static str,
    rate_offered_per_second: u64,
    mode: Mode,
    instruments: usize,
    duration_seconds: u64,
    offered: u64,
    queue_accepted: u64,
    rejected: u64,
    processed: u64,
    durable_records: u64,
    max_queue_occupancy: usize,
    actual_processed_per_second: f64,
    p50_ns: u64,
    p95_ns: u64,
    p99_ns: u64,
    max_ns: u64,
    fsync_p99_ns: Option<u64>,
    receive_to_durable_p99_ns: Option<u64>,
    sustained: bool,
    cpu_seconds: f64,
    peak_rss_bytes: u64,
    error: Option<String>,
}

struct WorkerResult {
    latencies: Vec<u64>,
    fsyncs: Vec<u64>,
    durable_latencies: Vec<u64>,
    durable_records: u64,
    error: Option<String>,
}

fn percentile(sorted: &[u64], fraction: f64) -> u64 {
    if sorted.is_empty() {
        return 0;
    }
    sorted[((sorted.len() as f64 * fraction).ceil() as usize)
        .saturating_sub(1)
        .min(sorted.len() - 1)]
}

fn resource_usage() -> (f64, u64) {
    let mut usage = std::mem::MaybeUninit::<libc::rusage>::uninit();
    // SAFETY: getrusage initializes the supplied rusage on success.
    if unsafe { libc::getrusage(libc::RUSAGE_SELF, usage.as_mut_ptr()) } != 0 {
        return (0.0, 0);
    }
    // SAFETY: successful getrusage initialized this value.
    let usage = unsafe { usage.assume_init() };
    let cpu = usage.ru_utime.tv_sec as f64
        + usage.ru_utime.tv_usec as f64 / 1e6
        + usage.ru_stime.tv_sec as f64
        + usage.ru_stime.tv_usec as f64 / 1e6;
    #[cfg(target_os = "macos")]
    let rss = usage.ru_maxrss as u64;
    #[cfg(not(target_os = "macos"))]
    let rss = usage.ru_maxrss as u64 * 1024;
    (cpu, rss)
}

fn one_trial(rate: u64, parsed: &Args) -> io::Result<Summary> {
    let (tx, rx) = mpsc::sync_channel::<Work>(parsed.queue_limit);
    let occupancy = Arc::new(AtomicUsize::new(0));
    let worker_occupancy = Arc::clone(&occupancy);
    let mode = parsed.mode;
    let journal_path = parsed.output.with_extension(format!("{rate}.qj"));
    if matches!(mode, Mode::Durable) && journal_path.exists() {
        return Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "benchmark journal already exists",
        ));
    }
    let output_path = parsed.output.clone();
    let started = Instant::now();
    let (cpu_before, _) = resource_usage();
    let worker = std::thread::spawn(move || -> io::Result<WorkerResult> {
        let mut raw = BufWriter::new(OpenOptions::new().append(true).open(output_path)?);
        let mut engine = MarketEngine::new(
            Box::new(Calendar),
            BarConfig {
                timestamp_policy: TimestampPolicy::Receive,
                max_lateness_ns: 0,
                max_open_bars_per_instrument: 128,
            },
            FeatureConfig {
                max_cross_feed_age_ns: 2_000_000_000,
                stale_after_ns: 2_000_000_000,
                rolling_window: 64,
            },
        )
        .map_err(|e| io::Error::other(format!("engine config: {e:?}")))?;
        let mut writer = if matches!(mode, Mode::Durable) {
            Some(JournalWriter::open(
                &journal_path,
                DurabilityPolicy::EveryEvent,
            )?)
        } else {
            None
        };
        let mut latencies = Vec::new();
        let mut fsyncs = Vec::new();
        let mut durable_latencies = Vec::new();
        let mut durable_records = 0;
        let mut error = None;
        for work in rx {
            worker_occupancy.fetch_sub(1, Ordering::Relaxed);
            let decode_started = Instant::now();
            let records: Vec<JournalRecord> = if work.depth {
                match dhan20::decode_frame(
                    &work.frame,
                    work.recv_ns,
                    "synthetic-depth",
                    work.serial,
                ) {
                    Ok(packets) => {
                        let mut records = Vec::new();
                        for packet in &packets {
                            if let dhan20::DecodedPacket::Side(side) = packet {
                                records.push(JournalRecord {
                                    session_id: "synthetic-depth".into(),
                                    event: MarketEvent::DepthSide(side.clone()),
                                });
                            }
                        }
                        for book in dhan20::assemble_frame(&packets) {
                            records.push(JournalRecord {
                                session_id: "synthetic-depth".into(),
                                event: MarketEvent::Depth(Box::new(book)),
                            });
                        }
                        records
                    }
                    Err(e) => {
                        error = Some(format!("depth decode: {e}"));
                        break;
                    }
                }
            } else {
                match dhan_standard::decode_frame(
                    &work.frame,
                    work.recv_ns,
                    "synthetic-standard",
                    work.serial,
                    work.serial,
                ) {
                    Ok(packets) => packets
                        .into_iter()
                        .filter_map(|p| match p {
                            dhan_standard::DecodedPacket::Quote(q) => Some(JournalRecord {
                                session_id: "synthetic-session".into(),
                                event: MarketEvent::Quote(q),
                            }),
                            _ => None,
                        })
                        .collect(),
                    Err(e) => {
                        error = Some(format!("standard decode: {e}"));
                        break;
                    }
                }
            };
            if records.len() != if work.depth { 3 } else { 1 } {
                error = Some("unexpected decoded event count".into());
                break;
            }
            let decode_ns = decode_started.elapsed().as_nanos() as u64;
            let mut journal_ns = 0;
            let mut fsync_max_ns: Option<u64> = None;
            let mut receive_to_durable_ns: Option<u64> = None;
            let mut state_ns = 0;
            for record in &records {
                if let Some(journal) = writer.as_mut() {
                    let started = Instant::now();
                    let commit = match journal.append(record).and_then(|commit| match commit {
                        Some(c) => Ok(c),
                        None => journal.sync(),
                    }) {
                        Ok(c) => c,
                        Err(e) => {
                            error = Some(format!("journal durability: {e}"));
                            break;
                        }
                    };
                    journal_ns += started.elapsed().as_nanos() as u64;
                    fsync_max_ns = Some(fsync_max_ns.unwrap_or(0).max(commit.fsync_ns));
                    receive_to_durable_ns = Some(work.instant.elapsed().as_nanos() as u64);
                    durable_records += 1;
                }
                let started = Instant::now();
                if let Err(e) = engine.process(record) {
                    error = Some(format!("engine: {e:?}"));
                    break;
                }
                state_ns += started.elapsed().as_nanos() as u64;
            }
            if error.is_some() {
                break;
            }
            let sample = Sample {
                benchmark_version: "phase2b-1",
                rate_offered_per_second: rate,
                mode,
                serial: work.serial,
                kind: if work.depth {
                    "20-depth-pair"
                } else {
                    "standard-quote"
                },
                receive_timestamp_ns: work.recv_ns,
                decode_ns,
                state_update_ns: state_ns,
                journal_durable_ns: journal_ns,
                fsync_max_ns,
                receive_to_durable_ns,
                end_to_end_ns: work.instant.elapsed().as_nanos() as u64,
                durable_records: if writer.is_some() {
                    if work.depth { 3 } else { 1 }
                } else {
                    0
                },
            };
            serde_json::to_writer(&mut raw, &sample)?;
            raw.write_all(b"\n")?;
            latencies.push(sample.decode_ns + sample.state_update_ns);
            if let Some(value) = sample.fsync_max_ns {
                fsyncs.push(value);
            }
            if let Some(value) = sample.receive_to_durable_ns {
                durable_latencies.push(value);
            }
        }
        raw.flush()?;
        raw.get_ref().sync_all()?;
        if let Some(journal) = writer {
            journal.close()?;
        }
        Ok(WorkerResult {
            latencies,
            fsyncs,
            durable_latencies,
            durable_records,
            error,
        })
    });
    let target = rate * parsed.duration_seconds;
    let mut offered = 0;
    let mut accepted = 0;
    let mut rejected = 0;
    let mut max_queue = 0;
    for serial in 0..target {
        let due = started + Duration::from_nanos(serial.saturating_mul(1_000_000_000) / rate);
        if let Some(delay) = due.checked_duration_since(Instant::now()) {
            std::thread::sleep(delay);
        }
        let recv_ns = now_ns()?;
        let id = 10_000 + (serial as usize % parsed.instruments) as i32;
        let depth = (serial / parsed.instruments as u64) % 5 == 0;
        let frame = if depth {
            depth_packet(id)
        } else {
            quote_packet(id, serial)
        };
        offered += 1;
        let queued = occupancy.fetch_add(1, Ordering::Relaxed) + 1;
        match tx.try_send(Work {
            serial: serial + 1,
            recv_ns,
            instant: Instant::now(),
            depth,
            frame,
        }) {
            Ok(()) => {
                accepted += 1;
                max_queue = max_queue.max(queued);
            }
            Err(mpsc::TrySendError::Full(_)) => {
                occupancy.fetch_sub(1, Ordering::Relaxed);
                rejected += 1;
                break;
            }
            Err(mpsc::TrySendError::Disconnected(_)) => {
                occupancy.fetch_sub(1, Ordering::Relaxed);
                rejected += 1;
                break;
            }
        }
    }
    drop(tx);
    let WorkerResult {
        mut latencies,
        mut fsyncs,
        mut durable_latencies,
        durable_records,
        error,
    } = worker
        .join()
        .map_err(|_| io::Error::other("worker panicked"))??;
    let elapsed = started.elapsed().as_secs_f64();
    let (cpu_after, peak_rss) = resource_usage();
    latencies.sort_unstable();
    fsyncs.sort_unstable();
    durable_latencies.sort_unstable();
    Ok(Summary {
        benchmark_version: "phase2b-1",
        rate_offered_per_second: rate,
        mode,
        instruments: parsed.instruments,
        duration_seconds: parsed.duration_seconds,
        offered,
        queue_accepted: accepted,
        rejected,
        processed: latencies.len() as u64,
        durable_records,
        max_queue_occupancy: max_queue,
        actual_processed_per_second: latencies.len() as f64 / elapsed,
        p50_ns: percentile(&latencies, 0.50),
        p95_ns: percentile(&latencies, 0.95),
        p99_ns: percentile(&latencies, 0.99),
        max_ns: *latencies.last().unwrap_or(&0),
        fsync_p99_ns: (!fsyncs.is_empty()).then(|| percentile(&fsyncs, 0.99)),
        receive_to_durable_p99_ns: (!durable_latencies.is_empty())
            .then(|| percentile(&durable_latencies, 0.99)),
        sustained: rejected == 0
            && error.is_none()
            && latencies.len() as u64 == accepted
            && elapsed <= parsed.duration_seconds as f64 * 1.10,
        cpu_seconds: cpu_after - cpu_before,
        peak_rss_bytes: peak_rss,
        error,
    })
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let parsed = args().map_err(io::Error::other)?;
    if let Some(parent) = parsed.output.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let _file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&parsed.output)?;
    let mut summaries = Vec::new();
    for rate in RATES {
        let summary = one_trial(rate, &parsed)?;
        println!("{}", serde_json::to_string(&summary)?);
        summaries.push(summary);
    }
    let summary_path = parsed.output.with_extension("summary.json");
    let mut summary_file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&summary_path)?;
    serde_json::to_writer_pretty(&mut summary_file, &summaries)?;
    summary_file.sync_all()?;
    if summaries.iter().any(|s| !s.sustained) {
        return Err("benchmark overload or processing failure; see summary".into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn synthetic_wire_fixtures_decode_as_expected() {
        let quote =
            dhan_standard::decode_frame(&quote_packet(13_333, 125), 123, "fixture", 1, 1).unwrap();
        let dhan_standard::DecodedPacket::Quote(value) = &quote[0] else {
            panic!("quote")
        };
        assert_eq!(value.price.instrument.security_id, "13333");
        assert_eq!(value.cumulative_traded_volume, 125);
        let depth = dhan20::decode_frame(&depth_packet(13_333), 123, "fixture", 1).unwrap();
        assert_eq!(depth.len(), 2);
        let books = dhan20::assemble_frame(&depth);
        assert_eq!(books.len(), 1);
        assert_eq!(books[0].bids.len(), 20);
        assert_eq!(books[0].asks.len(), 20);
        assert_eq!(books[0].bids[0].price_nanos, 99_000_000_000);
    }
}
