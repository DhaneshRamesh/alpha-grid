//! Controlled, offline QJ01 replay comparison. No feed or broker connections.
use quant_core::{Instrument, MarketEvent};
use quant_engine::bars::BarConfig;
use quant_engine::features::FeatureConfig;
use quant_engine::pipeline::MarketEngine;
use quant_engine::{MarketCalendar, TimestampPolicy};
use quant_journal::JournalReader;
use quant_paper::fees::FeeSchedule;
use quant_paper::oms::PaperEngine;
use quant_paper::policy::{PolicyConfig, PolicyMode};
use quant_paper::risk::RiskConfig;
use quant_telemetry::read_model::{TelemetryPublisher, TelemetryShared, now_ns};
use serde_json::json;
use std::fs::{File, OpenOptions};
use std::io::{self, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Instant;

const VERSION: &str = "phase3a-paper-eval-v1";
const SESSION: &str = "synthetic-replay-demo";

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
    .expect("valid replay engine")
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

fn elapsed_ns(start: Instant) -> u64 {
    u64::try_from(start.elapsed().as_nanos()).unwrap_or(u64::MAX)
}

fn percentile(values: &[u64], percentile: u64) -> Option<u64> {
    if values.is_empty() {
        return None;
    }
    let index = (values.len() as u64 * percentile)
        .div_ceil(100)
        .saturating_sub(1)
        .min(values.len() as u64 - 1);
    Some(values[index as usize])
}

fn p999(values: &[u64]) -> Option<u64> {
    if values.len() < 1000 {
        return None;
    }
    let index = ((values.len() as u64 * 999).div_ceil(1000))
        .saturating_sub(1)
        .min(values.len() as u64 - 1);
    Some(values[index as usize])
}

fn event_type(event: &MarketEvent) -> &'static str {
    match event {
        MarketEvent::Depth(_) => "depth",
        MarketEvent::DepthSide(_) => "depth_side",
        MarketEvent::Trade(_) => "trade",
        MarketEvent::Price(_) => "price",
        MarketEvent::Quote(_) => "quote",
        MarketEvent::Session(_) => "session",
    }
}

fn summary(mut values: Vec<u64>) -> serde_json::Value {
    values.sort_unstable();
    json!({
        "samples": values.len(),
        "p50_ns": percentile(&values, 50),
        "p95_ns": percentile(&values, 95),
        "p99_ns": percentile(&values, 99),
        "p999_ns": p999(&values),
        "max_ns": values.last(),
    })
}

fn new_file(path: &Path) -> io::Result<File> {
    OpenOptions::new().write(true).create_new(true).open(path)
}

fn run_policy(
    journal: &Path,
    out: &Path,
    label: &str,
    mode: PolicyMode,
) -> Result<serde_json::Value, Box<dyn std::error::Error>> {
    let ledger_path = out.join(format!("{label}.qp"));
    let raw_path = out.join(format!("{label}.jsonl"));
    let mut raw = BufWriter::new(new_file(&raw_path)?);
    // create_new prevents accidentally replaying a prior result as fresh evidence.
    let ledger_file = new_file(&ledger_path)?;
    drop(ledger_file);
    let mut paper = PaperEngine::open(
        &ledger_path,
        PolicyConfig::default(),
        RiskConfig::default(),
        FeeSchedule::dhan_delivery_research(),
        mode,
    )?;
    let mut reader = JournalReader::open(journal)?;
    let mut features = engine();
    let instruments = instruments();
    let shared = TelemetryShared::new(&instruments, "PAPER_REPLAY", SESSION)?;
    let mut publisher = TelemetryPublisher::new(Arc::clone(&shared), &instruments);
    let mut samples = vec![Vec::<u64>::new(); 8];
    let mut decision_samples = Vec::new();
    let mut risk_samples = Vec::new();
    let mut journal_transition_samples = Vec::new();
    let mut index = 0_u64;
    let mut last_ts = 0_i64;
    let run_start = Instant::now();
    loop {
        let read_start = Instant::now();
        let Some(record) = reader.next_record()? else {
            break;
        };
        let read_ns = elapsed_ns(read_start);
        index += 1;
        last_ts = record.event.receive_timestamp_ns();
        let feature_start = Instant::now();
        let output = features
            .process(&record)
            .map_err(|e| format!("feature processing: {e:?}"))?;
        let feature_ns = elapsed_ns(feature_start);
        let paper_snapshot = paper.on_market_record(index, &record, &output)?;
        let timing = paper.last_timing();
        if timing.candidate_ns > 0 {
            decision_samples.push(timing.candidate_ns);
            risk_samples.push(timing.risk_ns);
        }
        journal_transition_samples.extend_from_slice(paper.last_journal_writes_ns());
        let telemetry_start = Instant::now();
        publisher.observe(&record, &output, true);
        shared.publish_paper(&paper_snapshot);
        let telemetry_ns = elapsed_ns(telemetry_start);
        let values = [
            read_ns,
            feature_ns,
            timing.candidate_ns,
            timing.risk_ns,
            timing.oms_transition_ns,
            timing.journal_durability_ns,
            timing.snapshot_ns,
            telemetry_ns,
        ];
        for (slot, value) in samples.iter_mut().zip(values) {
            slot.push(value);
        }
        serde_json::to_writer(
            &mut raw,
            &json!({
                "benchmark_version": VERSION,
                "policy": label,
                "source_record_index": index,
                "source_receive_timestamp_ns": last_ts.to_string(),
                "measurement_wall_timestamp_ns": now_ns().to_string(),
                "source_event_type": event_type(&record.event),
                "journal_read_ns": read_ns,
                "feature_update_ns": feature_ns,
                "candidate_evaluation_ns": timing.candidate_ns,
                "risk_evaluation_ns": timing.risk_ns,
                "paper_oms_transition_ns": timing.oms_transition_ns,
                "paper_journal_durability_ns": timing.journal_durability_ns,
                "paper_journal_transition_ns": paper.last_journal_writes_ns(),
                "paper_snapshot_ns": timing.snapshot_ns,
                "telemetry_publication_ns": telemetry_ns,
                "paper_total_ns": timing.total_ns,
            }),
        )?;
        raw.write_all(b"\n")?;
    }
    let elapsed = run_start.elapsed();
    paper.end_session(index, last_ts)?;
    let final_snapshot = paper.snapshot();
    paper.close()?;
    raw.flush()?;
    raw.get_ref().sync_all()?;
    let names = [
        "journal_read",
        "feature_update",
        "candidate_evaluation",
        "risk_evaluation",
        "paper_oms_transition",
        "paper_journal_durability",
        "paper_snapshot",
        "telemetry_publication",
    ];
    let latency: serde_json::Map<String, serde_json::Value> = names
        .into_iter()
        .zip(samples)
        .map(|(name, values)| (name.to_string(), summary(values)))
        .collect();
    Ok(json!({
        "benchmark_version": VERSION,
        "policy": label,
        "source": "local synthetic QJ01 replay, not live capture or market evidence",
        "market_journal_path": journal,
        "paper_ledger_path": ledger_path,
        "raw_measurements_path": raw_path,
        "records": index,
        "elapsed_ns": elapsed.as_nanos().to_string(),
        "records_per_second": index as f64 / elapsed.as_secs_f64(),
        "paper_ledger_bytes": std::fs::metadata(&ledger_path)?.len(),
        "recent_decisions": final_snapshot.recent_decisions.len(),
        "recent_fills": final_snapshot.recent_fills.len(),
        "recent_outcomes": final_snapshot.recent_outcomes.len(),
        "starting_capital_nanos": final_snapshot.portfolio.starting_capital_nanos.to_string(),
        "cash_nanos": final_snapshot.portfolio.cash_nanos.to_string(),
        "marked_equity_nanos": final_snapshot.portfolio.marked_equity_nanos.map(|v| v.to_string()),
        "realized_paper_pnl_nanos": final_snapshot.portfolio.realized_pnl_nanos.to_string(),
        "fees_paid_nanos": final_snapshot.portfolio.fees_paid_nanos.to_string(),
        "max_drawdown_nanos": final_snapshot.portfolio.max_drawdown_nanos.map(|v| v.to_string()),
        "unclosed_positions": final_snapshot.portfolio.positions.len(),
        "latency_ns": latency,
        "active_candidate_latency_ns": summary(decision_samples),
        "active_risk_latency_ns": summary(risk_samples),
        "paper_journal_transition_latency_ns": summary(journal_transition_samples),
        "unmeasured": ["feed decode", "market journal fsync", "browser reception"],
    }))
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 5 || args[1] != "--journal" || args[3] != "--output-dir" {
        return Err("usage: paper_eval --journal PATH --output-dir NEW_DIRECTORY".into());
    }
    let journal = PathBuf::from(&args[2]);
    let out = PathBuf::from(&args[4]);
    std::fs::create_dir(&out)?;
    let mut summaries = Vec::new();
    for (label, mode) in [
        ("always_no_trade", PolicyMode::AlwaysNoTrade),
        ("simple_signal", PolicyMode::SimpleSignal),
        ("cost_aware", PolicyMode::CostAwareSignal),
        ("risk_filtered", PolicyMode::RiskFilteredSignal),
    ] {
        summaries.push(run_policy(&journal, &out, label, mode)?);
    }
    let summary_path = out.join("summary.json");
    let mut file = new_file(&summary_path)?;
    serde_json::to_writer_pretty(&mut file, &summaries)?;
    file.write_all(b"\n")?;
    file.sync_all()?;
    println!("{}", summary_path.display());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn percentiles_require_sufficient_tail_samples() {
        assert_eq!(percentile(&[], 50), None);
        assert_eq!(summary(vec![1, 2, 3])["p999_ns"], serde_json::Value::Null);
        assert_eq!(percentile(&[1, 2, 3, 4, 5], 95), Some(5));
    }
}
