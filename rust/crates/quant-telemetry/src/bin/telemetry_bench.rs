//! Local replay-only benchmark. Measures core processing separately from
//! bounded presentation work, with and without a draining WebSocket client.
use futures_util::StreamExt;
use quant_engine::bars::BarConfig;
use quant_engine::features::FeatureConfig;
use quant_engine::pipeline::MarketEngine;
use quant_engine::{MarketCalendar, TimestampPolicy};
use quant_journal::JournalReader;
use quant_telemetry::gateway::{Gateway, GatewayConfig};
use quant_telemetry::read_model::{TelemetryPublisher, TelemetryShared};
use serde::Serialize;
use std::fs::{File, OpenOptions};
use std::io::{BufWriter, Write};
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};
use tokio::net::TcpListener;
use tokio::sync::watch;

struct Calendar;
impl MarketCalendar for Calendar {
    fn session_at(&self, _: i64) -> Option<String> {
        Some("synthetic-replay-demo".into())
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
            max_cross_feed_age_ns: 2_000_000_000,
            stale_after_ns: 5_000_000_000,
            rolling_window: 32,
        },
    )
    .expect("known benchmark config")
}
fn instruments() -> Vec<quant_core::Instrument> {
    [(1333, "RELIANCE"), (2885, "TATAMOTORS")]
        .into_iter()
        .map(|(id, symbol)| quant_core::Instrument {
            venue: "NSE".into(),
            exchange_segment: "NSE_EQ".into(),
            security_id: id.to_string(),
            symbol: symbol.into(),
        })
        .collect()
}
#[derive(Serialize)]
struct Sample {
    benchmark_version: &'static str,
    client_connected: bool,
    run: u32,
    pace_rate: u64,
    record_ordinal: u64,
    engine_process_ns: u64,
    telemetry_adapter_ns: u64,
    total_ns: u64,
}
#[derive(Serialize)]
struct Summary {
    benchmark_version: &'static str,
    client_connected: bool,
    run: u32,
    pace_rate: u64,
    records: u64,
    elapsed_ns: u64,
    records_per_second: f64,
    engine_p50_ns: u64,
    engine_p95_ns: u64,
    engine_p99_ns: u64,
    adapter_p50_ns: u64,
    adapter_p95_ns: u64,
    adapter_p99_ns: u64,
    total_p50_ns: u64,
    total_p95_ns: u64,
    total_p99_ns: u64,
    publication_p50_ns: Option<String>,
    publication_p95_ns: Option<String>,
    publication_p99_ns: Option<String>,
    publication_samples: u64,
    client_messages: u64,
    failures: u64,
}
fn percentile(values: &[u64], fraction: f64) -> u64 {
    let mut sorted = values.to_vec();
    sorted.sort_unstable();
    sorted[((sorted.len() as f64 * fraction).ceil() as usize)
        .saturating_sub(1)
        .min(sorted.len() - 1)]
}
struct ReplayRun {
    records: u64,
    elapsed_ns: u64,
    engines: Vec<u64>,
    adapters: Vec<u64>,
    totals: Vec<u64>,
}
fn run_replay(
    path: PathBuf,
    shared: Arc<TelemetryShared>,
    connected: bool,
    run: u32,
    raw: PathBuf,
    pace_rate: u64,
) -> Result<ReplayRun, String> {
    let mut reader = JournalReader::open(&path).map_err(|e| e.to_string())?;
    let instruments = instruments();
    let mut publisher = TelemetryPublisher::new(shared, &instruments);
    let mut engine = engine();
    let file = OpenOptions::new()
        .append(true)
        .open(raw)
        .map_err(|e| e.to_string())?;
    let mut output = BufWriter::new(file);
    let mut engine_times = Vec::new();
    let mut adapter_times = Vec::new();
    let mut total_times = Vec::new();
    let started = Instant::now();
    let mut ordinal = 0;
    while let Some(record) = reader.next_record().map_err(|e| e.to_string())? {
        ordinal += 1;
        if let Some(period) = 1_000_000_000_u64.checked_div(pace_rate) {
            let target = started + Duration::from_nanos((ordinal - 1) * period);
            if let Some(delay) = target.checked_duration_since(Instant::now()) {
                std::thread::sleep(delay);
            }
        }
        let one = Instant::now();
        let derived = engine
            .process(&record)
            .map_err(|e| format!("engine {e:?}"))?;
        let engine_ns = one.elapsed().as_nanos() as u64;
        let adapter = Instant::now();
        publisher.observe(&record, &derived, true);
        let adapter_ns = adapter.elapsed().as_nanos() as u64;
        let total_ns = one.elapsed().as_nanos() as u64;
        engine_times.push(engine_ns);
        adapter_times.push(adapter_ns);
        total_times.push(total_ns);
        serde_json::to_writer(
            &mut output,
            &Sample {
                benchmark_version: "phase2c-1",
                client_connected: connected,
                run,
                pace_rate,
                record_ordinal: ordinal,
                engine_process_ns: engine_ns,
                telemetry_adapter_ns: adapter_ns,
                total_ns,
            },
        )
        .map_err(|e| e.to_string())?;
        output.write_all(b"\n").map_err(|e| e.to_string())?;
    }
    output.flush().map_err(|e| e.to_string())?;
    Ok(ReplayRun {
        records: ordinal,
        elapsed_ns: started.elapsed().as_nanos() as u64,
        engines: engine_times,
        adapters: adapter_times,
        totals: total_times,
    })
}
async fn trial(
    path: PathBuf,
    raw: PathBuf,
    connected: bool,
    run: u32,
    pace_rate: u64,
) -> Result<Summary, Box<dyn std::error::Error>> {
    let shared = TelemetryShared::new(&instruments(), "REPLAY", "synthetic-replay-demo")?;
    let gateway = Gateway::new(Arc::clone(&shared), GatewayConfig::default())?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let address = listener.local_addr()?;
    let (shutdown, shutdown_rx) = watch::channel(false);
    let server = tokio::spawn(gateway.serve(listener, shutdown_rx));
    let received = Arc::new(AtomicU64::new(0));
    let client = if connected {
        let (mut socket, _) =
            tokio_tungstenite::connect_async(format!("ws://{address}/ws")).await?;
        let count = Arc::clone(&received);
        Some(tokio::spawn(async move {
            while let Some(Ok(_)) = socket.next().await {
                count.fetch_add(1, Ordering::Relaxed);
            }
        }))
    } else {
        None
    };
    let worker_shared = Arc::clone(&shared);
    let result = tokio::task::spawn_blocking(move || {
        run_replay(path, worker_shared, connected, run, raw, pace_rate)
    })
    .await??;
    tokio::time::sleep(Duration::from_millis(150)).await;
    let snapshot = shared.snapshot(0, i64::MAX).ok_or("busy read model")?;
    let publication = snapshot.counters.telemetry_publication_latency;
    if let Some(client) = client {
        client.abort();
        let _ = client.await;
    }
    let client_messages = received.load(Ordering::Relaxed);
    shutdown.send(true)?;
    server.await??;
    let ReplayRun {
        records,
        elapsed_ns,
        engines,
        adapters,
        totals,
    } = result;
    Ok(Summary {
        benchmark_version: "phase2c-1",
        client_connected: connected,
        run,
        pace_rate,
        records,
        elapsed_ns,
        records_per_second: records as f64 * 1e9 / elapsed_ns as f64,
        engine_p50_ns: percentile(&engines, 0.5),
        engine_p95_ns: percentile(&engines, 0.95),
        engine_p99_ns: percentile(&engines, 0.99),
        adapter_p50_ns: percentile(&adapters, 0.5),
        adapter_p95_ns: percentile(&adapters, 0.95),
        adapter_p99_ns: percentile(&adapters, 0.99),
        total_p50_ns: percentile(&totals, 0.5),
        total_p95_ns: percentile(&totals, 0.95),
        total_p99_ns: percentile(&totals, 0.99),
        publication_p50_ns: publication.p50_ns,
        publication_p95_ns: publication.p95_ns,
        publication_p99_ns: publication.p99_ns,
        publication_samples: publication.sample_count,
        client_messages,
        failures: 0,
    })
}
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    if !cfg!(test) {
        return Err(
            "DATA UNAVAILABLE: telemetry replay requires verified Dhan QJ01 provenance".into(),
        );
    }
    let mut args = std::env::args().skip(1);
    let journal = PathBuf::from(
        args.next()
            .ok_or("usage: telemetry_bench JOURNAL RAW.jsonl [runs] [pace_rate]")?,
    );
    let raw = PathBuf::from(args.next().ok_or("missing RAW.jsonl")?);
    let runs: u32 = args.next().map(|v| v.parse()).transpose()?.unwrap_or(5);
    let pace_rate: u64 = args.next().map(|v| v.parse()).transpose()?.unwrap_or(0);
    if runs == 0 || runs > 20 {
        return Err("runs must be 1..20".into());
    }
    if pace_rate > 1_000_000 {
        return Err("pace_rate must be <= 1,000,000".into());
    }
    File::create(&raw)?;
    for run in 1..=runs {
        for connected in [false, true] {
            let summary = trial(journal.clone(), raw.clone(), connected, run, pace_rate).await?;
            println!("{}", serde_json::to_string(&summary)?);
        }
    }
    Ok(())
}
