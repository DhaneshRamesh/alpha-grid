//! DATA_ONLY synthetic ingestion benchmark. JSON contains raw nanosecond samples.
use quant_core::MarketEvent;
use quant_journal::{Commit, DurabilityPolicy, JournalReader, JournalRecord, JournalWriter};
use quant_market_data::{normalize, synthetic_packet};
use serde::Serialize;
use std::env;
use std::fs;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::sync::mpsc;

const VERSION: &str = "rust-ingestion-0.2.0";
const RATES: [u64; 5] = [100, 500, 1_000, 5_000, 10_000];

#[derive(Clone, Copy, Debug, Serialize)]
enum Policy {
    EveryEvent,
    Periodic,
    GroupBytes,
}

impl Policy {
    fn durability(self, sync_interval_ms: u64, group_bytes: usize) -> DurabilityPolicy {
        match self {
            Self::EveryEvent => DurabilityPolicy::EveryEvent,
            Self::Periodic => DurabilityPolicy::Group {
                max_events: usize::MAX,
                max_bytes: usize::MAX,
                max_delay_ms: sync_interval_ms,
            },
            Self::GroupBytes => DurabilityPolicy::Group {
                max_events: usize::MAX,
                max_bytes: group_bytes,
                max_delay_ms: sync_interval_ms,
            },
        }
    }
}

#[derive(Serialize)]
struct Case {
    trial: usize,
    started_unix_ns: u128,
    rate_target: u64,
    policy: Policy,
    scheduled: u64,
    offered: u64,
    unproduced: u64,
    accepted: u64,
    journal_buffered: u64,
    persisted_to_kernel: u64,
    durable: u64,
    rejected: u64,
    elapsed_ns: u64,
    producer_elapsed_ns: u64,
    actual_source_events_per_second: f64,
    max_schedule_lag_ns: u64,
    queue_depth_at_input_end: usize,
    max_queue_depth: usize,
    backpressure_detected: bool,
    max_uncommitted_events: usize,
    journal_bytes: u64,
    bytes_per_million_events: Option<u64>,
    actual_durable_events_per_second: f64,
    recovered_events: u64,
    recovery_truncated_bytes: u64,
    user_cpu_ns: u64,
    system_cpu_ns: u64,
    peak_rss_bytes: Option<u64>,
    durability_latency_ns: Vec<u64>,
    append_latency_ns: Vec<u64>,
    fsync_latency_ns: Vec<u64>,
    write_batch_latency_ns: Vec<u64>,
    durability_summary_ns: Percentiles,
    fsync_summary_ns: Percentiles,
    error: Option<String>,
}

#[derive(Serialize)]
struct Percentiles {
    count: usize,
    p50: Option<u64>,
    p95: Option<u64>,
    p99: Option<u64>,
    p999: Option<u64>,
    max: Option<u64>,
}

fn percentiles(samples: &[u64]) -> Percentiles {
    let mut sorted = samples.to_vec();
    sorted.sort_unstable();
    let at = |quantile: f64, minimum: usize| {
        if sorted.len() < minimum {
            None
        } else {
            let index = ((sorted.len() as f64 * quantile).ceil() as usize)
                .saturating_sub(1)
                .min(sorted.len() - 1);
            Some(sorted[index])
        }
    };
    Percentiles {
        count: sorted.len(),
        p50: at(0.5, 2),
        p95: at(0.95, 20),
        p99: at(0.99, 100),
        p999: at(0.999, 1000),
        max: sorted.last().copied(),
    }
}

#[derive(Serialize)]
struct Output {
    version: &'static str,
    provider: String,
    plan: String,
    region: String,
    host: String,
    started_unix_ns: u128,
    duration_seconds: f64,
    queue_limit: usize,
    repetitions: usize,
    seed: u64,
    sync_interval_ms: u64,
    group_bytes: usize,
    simulated_sync_delay_ms: u64,
    instrument_count: usize,
    data_only: bool,
    cases: Vec<Case>,
}

#[derive(Serialize)]
struct RawTrial<'a> {
    benchmark_version: &'static str,
    provider: &'a str,
    plan: &'a str,
    region: &'a str,
    case: &'a Case,
}

struct Envelope {
    record: JournalRecord,
    arrived: Instant,
}

#[derive(Default)]
struct WriterResult {
    persisted_to_kernel: u64,
    durable: u64,
    journal_bytes: u64,
    max_uncommitted_events: usize,
    durability_latency_ns: Vec<u64>,
    append_latency_ns: Vec<u64>,
    fsync_latency_ns: Vec<u64>,
    write_batch_latency_ns: Vec<u64>,
    error: Option<String>,
}

fn add_commit(result: &mut WriterResult, commit: Commit, pending: &mut Vec<Instant>) {
    if commit.events == 0 {
        return;
    }
    let now = Instant::now();
    result.durable += commit.events as u64;
    result.journal_bytes += commit.bytes as u64;
    result.fsync_latency_ns.push(commit.fsync_ns);
    result.write_batch_latency_ns.push(commit.write_ns);
    result
        .durability_latency_ns
        .extend(pending.drain(..).map(|at| nanos(now.duration_since(at))));
}

async fn write_events(
    path: PathBuf,
    policy: Policy,
    sync_interval: Duration,
    group_bytes: usize,
    simulated_sync_delay: Duration,
    mut rx: mpsc::Receiver<Envelope>,
) -> WriterResult {
    let mut result = WriterResult::default();
    let mut journal = match JournalWriter::open(
        &path,
        policy.durability(sync_interval.as_millis() as u64, group_bytes),
    ) {
        Ok(journal) => journal,
        Err(error) => {
            result.error = Some(error.to_string());
            return result;
        }
    };
    let mut pending = Vec::new();
    loop {
        let deadline = pending
            .first()
            .copied()
            .map(|at| tokio::time::Instant::from_std(at + sync_interval));
        tokio::select! {
            item = rx.recv() => match item {
                Some(item) => {
                    let started = Instant::now();
                    pending.push(item.arrived);
                    if matches!(policy, Policy::EveryEvent) && !simulated_sync_delay.is_zero() {
                        tokio::time::sleep(simulated_sync_delay).await;
                    }
                    match journal.append(&item.record) {
                        Ok(commit) => {
                            result.append_latency_ns.push(nanos(started.elapsed()));
                            result.max_uncommitted_events = result.max_uncommitted_events.max(pending.len());
                            if let Some(commit) = commit { add_commit(&mut result, commit, &mut pending); }
                        }
                        Err(error) => { result.error = Some(error.to_string()); break; }
                    }
                }
                None => break,
            },
            _ = async {
                if let Some(deadline) = deadline {
                    tokio::time::sleep_until(deadline).await;
                }
            }, if deadline.is_some() => {
                if journal.pending() > 0 {
                    if !simulated_sync_delay.is_zero() { tokio::time::sleep(simulated_sync_delay).await; }
                    match journal.sync() {
                        Ok(commit) => add_commit(&mut result, commit, &mut pending),
                        Err(error) => { result.error = Some(error.to_string()); break; }
                    }
                }
            },
        }
    }
    if result.error.is_none() && journal.pending() > 0 {
        if !simulated_sync_delay.is_zero() {
            tokio::time::sleep(simulated_sync_delay).await;
        }
        match journal.sync() {
            Ok(commit) => add_commit(&mut result, commit, &mut pending),
            Err(error) => result.error = Some(error.to_string()),
        }
    }
    result.persisted_to_kernel = journal.written_events() as u64;
    result
}

fn nanos(duration: Duration) -> u64 {
    duration.as_nanos().try_into().unwrap_or(u64::MAX)
}

fn usage() -> (u64, u64, Option<u64>) {
    let mut info = std::mem::MaybeUninit::<libc::rusage>::uninit();
    if unsafe { libc::getrusage(libc::RUSAGE_SELF, info.as_mut_ptr()) } != 0 {
        return (0, 0, None);
    }
    let info = unsafe { info.assume_init() };
    let time_ns = |value: libc::timeval| -> u64 {
        (value.tv_sec as u64) * 1_000_000_000 + (value.tv_usec as u64) * 1_000
    };
    let rss = if cfg!(target_os = "linux") {
        (info.ru_maxrss as u64) * 1024
    } else {
        info.ru_maxrss as u64
    };
    (time_ns(info.ru_utime), time_ns(info.ru_stime), Some(rss))
}

#[derive(Clone, Copy)]
struct RunConfig {
    queue_limit: usize,
    instrument_count: usize,
    sync_interval: Duration,
    group_bytes: usize,
    simulated_sync_delay: Duration,
}

async fn run_case(
    root: &Path,
    trial: usize,
    rate: u64,
    duration_seconds: f64,
    policy: Policy,
    config: RunConfig,
) -> Case {
    let started_unix_ns = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let session = format!(
        "rust-{rate}-{policy:?}-{}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let path = root.join(format!("{session}.qj"));
    let (tx, rx) = mpsc::channel(config.queue_limit);
    let writer = tokio::spawn(write_events(
        path.clone(),
        policy,
        config.sync_interval,
        config.group_bytes,
        config.simulated_sync_delay,
        rx,
    ));
    let (user_before, system_before, _) = usage();
    let started = Instant::now();
    let count = (rate as f64 * duration_seconds).round() as u64;
    let mut offered = 0;
    let mut accepted = 0;
    let mut rejected = 0;
    let mut max_schedule_lag_ns = 0;
    let mut max_queue_depth = 0;
    let mut producer_error = None;
    for index in 0..count {
        let scheduled = started + Duration::from_nanos(index * 1_000_000_000 / rate);
        tokio::time::sleep_until(tokio::time::Instant::from_std(scheduled)).await;
        max_schedule_lag_ns =
            max_schedule_lag_ns.max(nanos(Instant::now().saturating_duration_since(scheduled)));
        offered += 1;
        let event = match normalize(synthetic_packet(index, config.instrument_count)) {
            Ok(event) => event,
            Err(error) => {
                rejected += 1;
                producer_error = Some(format!("normalization: {error:?}"));
                break;
            }
        };
        let record = JournalRecord {
            session_id: session.clone(),
            event: MarketEvent::Depth(Box::new(event)),
        };
        // Match Python: event-to-durable starts at the bounded queue handoff.
        let at = Instant::now();
        if let Err(error) = tx.try_send(Envelope {
            record,
            arrived: at,
        }) {
            rejected += 1;
            producer_error = Some(format!("bounded queue rejected event: {error}"));
            break;
        }
        accepted += 1;
        max_queue_depth = max_queue_depth.max(config.queue_limit - tx.capacity());
    }
    let queue_depth_at_input_end = config.queue_limit - tx.capacity();
    let producer_elapsed_ns = nanos(started.elapsed());
    drop(tx);
    let result = match writer.await {
        Ok(result) => result,
        Err(error) => WriterResult {
            error: Some(format!("journal task failed: {error}")),
            ..Default::default()
        },
    };
    let elapsed_ns = nanos(started.elapsed());
    let (user_after, system_after, peak_rss_bytes) = usage();
    let mut recovered_events = 0;
    let mut recovery_truncated_bytes = 0;
    let mut recovery_error = None;
    match JournalReader::open(&path) {
        Ok(mut reader) => {
            recovery_truncated_bytes = reader.recovered_tail_bytes();
            if recovery_truncated_bytes > 0 {
                recovery_error = Some("journal recovery truncated tail bytes".into());
            }
            loop {
                match reader.next_record() {
                    Ok(Some(_)) => recovered_events += 1,
                    Ok(None) => break,
                    Err(error) => {
                        recovery_error = Some(error.to_string());
                        break;
                    }
                }
            }
        }
        Err(error) => recovery_error = Some(error.to_string()),
    }
    let error = producer_error
        .or(result.error)
        .or(recovery_error)
        .or_else(|| {
            if result.persisted_to_kernel != accepted
                || result.durable != accepted
                || recovered_events != result.durable
            {
                Some("accepted/persisted/durable/recovered mismatch".into())
            } else {
                None
            }
        });
    let durability_summary_ns = percentiles(&result.durability_latency_ns);
    let fsync_summary_ns = percentiles(&result.fsync_latency_ns);
    let journal_bytes = fs::metadata(&path)
        .map(|metadata| metadata.len())
        .unwrap_or(result.journal_bytes);
    Case {
        trial,
        started_unix_ns,
        rate_target: rate,
        policy,
        scheduled: count,
        offered,
        unproduced: count - offered,
        accepted,
        journal_buffered: result.append_latency_ns.len() as u64,
        persisted_to_kernel: result.persisted_to_kernel,
        durable: result.durable,
        rejected,
        elapsed_ns,
        producer_elapsed_ns,
        actual_source_events_per_second: accepted as f64 / (producer_elapsed_ns as f64 / 1e9),
        max_schedule_lag_ns,
        queue_depth_at_input_end,
        max_queue_depth,
        backpressure_detected: rejected > 0 || max_queue_depth == config.queue_limit,
        max_uncommitted_events: result.max_uncommitted_events,
        journal_bytes,
        bytes_per_million_events: (result.durable > 0)
            .then(|| journal_bytes.saturating_mul(1_000_000) / result.durable),
        actual_durable_events_per_second: result.durable as f64 / (elapsed_ns as f64 / 1e9),
        recovered_events,
        recovery_truncated_bytes,
        user_cpu_ns: user_after.saturating_sub(user_before),
        system_cpu_ns: system_after.saturating_sub(system_before),
        peak_rss_bytes,
        durability_latency_ns: result.durability_latency_ns,
        append_latency_ns: result.append_latency_ns,
        fsync_latency_ns: result.fsync_latency_ns,
        write_batch_latency_ns: result.write_batch_latency_ns,
        durability_summary_ns,
        fsync_summary_ns,
        error,
    }
}

fn arg(args: &[String], name: &str, default: &str) -> String {
    args.windows(2)
        .find(|pair| pair[0] == name)
        .map(|pair| pair[1].clone())
        .unwrap_or_else(|| default.into())
}

fn shuffled_cases(rates: &[u64], repetitions: usize, seed: u64) -> Vec<(usize, Policy, u64)> {
    let mut cases = Vec::new();
    for trial in 0..repetitions {
        for policy in [Policy::EveryEvent, Policy::Periodic, Policy::GroupBytes] {
            for &rate in rates {
                cases.push((trial, policy, rate));
            }
        }
    }
    let mut state = seed.max(1);
    for index in (1..cases.len()).rev() {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        cases.swap(index, state as usize % (index + 1));
    }
    cases
}

#[tokio::main]
async fn main() -> io::Result<()> {
    let args: Vec<String> = env::args().collect();
    let started_unix_ns = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let output = PathBuf::from(arg(&args, "--output", "rust-benchmark.json"));
    let trials = PathBuf::from(format!("{}.trials.jsonl", output.display()));
    let root = PathBuf::from(arg(&args, "--journal-dir", "rust-benchmark-journals"));
    let duration_seconds: f64 = arg(&args, "--duration", "1")
        .parse()
        .map_err(io::Error::other)?;
    let queue_limit: usize = arg(&args, "--queue-limit", "2000")
        .parse()
        .map_err(io::Error::other)?;
    let instrument_count: usize = arg(&args, "--instrument-count", "20")
        .parse()
        .map_err(io::Error::other)?;
    let sync_interval_ms: u64 = arg(&args, "--sync-interval-ms", "50")
        .parse()
        .map_err(io::Error::other)?;
    let group_bytes: usize = arg(&args, "--group-bytes", "262144")
        .parse()
        .map_err(io::Error::other)?;
    let repetitions: usize = arg(&args, "--repetitions", "1")
        .parse()
        .map_err(io::Error::other)?;
    let seed: u64 = arg(&args, "--seed", "20261011")
        .parse()
        .map_err(io::Error::other)?;
    let simulated_sync_delay_ms: u64 = arg(&args, "--simulate-slow-sync-ms", "0")
        .parse()
        .map_err(io::Error::other)?;
    if duration_seconds <= 0.0
        || !duration_seconds.is_finite()
        || queue_limit == 0
        || !(1..=50).contains(&instrument_count)
        || sync_interval_ms == 0
        || group_bytes == 0
        || repetitions == 0
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid duration or queue limit",
        ));
    }
    fs::create_dir_all(&root)?;
    let rates = if args.iter().any(|value| value == "--all") {
        RATES.to_vec()
    } else {
        vec![
            arg(&args, "--rate", "1000")
                .parse()
                .map_err(io::Error::other)?,
        ]
    };
    if rates.contains(&0) {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, "zero rate"));
    }
    if let Some(parent) = output.parent().filter(|part| !part.as_os_str().is_empty()) {
        fs::create_dir_all(parent)?;
    }
    if output.exists() {
        return Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "output exists",
        ));
    }
    let mut trial_file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&trials)?;
    let mut cases = Vec::new();
    let provider = arg(&args, "--provider", "local");
    let plan = arg(&args, "--plan", "development-host");
    let region = arg(&args, "--region", "local");
    let config = RunConfig {
        queue_limit,
        instrument_count,
        sync_interval: Duration::from_millis(sync_interval_ms),
        group_bytes,
        simulated_sync_delay: Duration::from_millis(simulated_sync_delay_ms),
    };
    for (trial, policy, rate) in shuffled_cases(&rates, repetitions, seed) {
        let case = run_case(&root, trial, rate, duration_seconds, policy, config).await;
        serde_json::to_writer(
            &mut trial_file,
            &RawTrial {
                benchmark_version: VERSION,
                provider: &provider,
                plan: &plan,
                region: &region,
                case: &case,
            },
        )?;
        trial_file.write_all(b"\n")?;
        trial_file.sync_all()?;
        cases.push(case);
    }
    let result = Output {
        version: VERSION,
        provider,
        plan,
        region,
        host: env::var("HOSTNAME").unwrap_or_else(|_| "unknown".into()),
        started_unix_ns,
        duration_seconds,
        queue_limit,
        repetitions,
        seed,
        sync_interval_ms,
        group_bytes,
        simulated_sync_delay_ms,
        instrument_count,
        data_only: true,
        cases,
    };
    let mut summary_file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&output)?;
    serde_json::to_writer_pretty(&mut summary_file, &result)?;
    summary_file.write_all(b"\n")?;
    summary_file.sync_all()?;
    println!("{}", output.display());
    if result.cases.iter().any(|case| case.error.is_some()) {
        return Err(io::Error::other(
            "one or more benchmark cases failed; inspect JSON",
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn slow_sync_exposes_bounded_queue_failure() {
        let root = tempfile::tempdir().unwrap();
        let case = run_case(
            root.path(),
            0,
            10_000,
            0.1,
            Policy::EveryEvent,
            RunConfig {
                queue_limit: 1,
                instrument_count: 20,
                sync_interval: Duration::from_millis(50),
                group_bytes: 262_144,
                simulated_sync_delay: Duration::from_millis(100),
            },
        )
        .await;
        assert!(case.rejected > 0);
        assert!(case.error.is_some());
        assert_eq!(case.durable, case.accepted);
        assert_eq!(case.recovered_events, case.durable);
    }

    #[tokio::test]
    async fn storage_open_error_is_visible() {
        let root = tempfile::tempdir().unwrap();
        let blocked = root.path().join("file-instead-of-directory");
        fs::write(&blocked, b"x").unwrap();
        let case = run_case(
            &blocked,
            0,
            100,
            0.1,
            Policy::EveryEvent,
            RunConfig {
                queue_limit: 1,
                instrument_count: 20,
                sync_interval: Duration::from_millis(50),
                group_bytes: 262_144,
                simulated_sync_delay: Duration::ZERO,
            },
        )
        .await;
        assert!(case.error.is_some());
        assert_eq!(case.durable, 0);
    }
}
