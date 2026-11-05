//! Loopback cockpit with an opt-in, scoped read-only quote path.
use quant_core::Instrument;
use quant_engine::bars::BarConfig;
use quant_engine::capture::StandardCapture;
use quant_engine::features::FeatureConfig;
use quant_engine::pipeline::MarketEngine;
use quant_engine::{MarketCalendar, TimestampPolicy};
use quant_journal::{DurabilityPolicy, JournalWriter};
use quant_telemetry::gateway::{Gateway, GatewayConfig};
use quant_telemetry::protocol::WireDepth;
use quant_telemetry::read_model::{
    FeedLifecycle, FeedStopReason, TelemetryPublisher, TelemetryShared, now_ns,
    run_authenticated_standard_socket,
};
use serde::Deserialize;
use std::ffi::OsString;
use std::io;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Arc;
use std::time::Duration;
use tokio::net::{TcpListener, TcpStream, lookup_host};
use tokio::sync::watch;

struct Credentials {
    token: String,
    client: String,
}
impl std::fmt::Debug for Credentials {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("Credentials(REDACTED)")
    }
}
fn credentials() -> io::Result<Credentials> {
    let token = std::env::var("DHAN_ACCESS_TOKEN").map_err(|_| {
        io::Error::new(
            io::ErrorKind::PermissionDenied,
            "Dhan credential unavailable",
        )
    })?;
    let client = std::env::var("DHAN_CLIENT_ID").map_err(|_| {
        io::Error::new(
            io::ErrorKind::PermissionDenied,
            "Dhan credential unavailable",
        )
    })?;
    if !credential_format_valid(&token, &client) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "Dhan credential format invalid",
        ));
    }
    Ok(Credentials { token, client })
}
fn credential_format_valid(token: &str, client: &str) -> bool {
    !token.is_empty()
        && token
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
        && !client.is_empty()
        && client.bytes().all(|b| b.is_ascii_digit())
}

fn field<'a>(status: &'a str, name: &str) -> Option<&'a str> {
    status.lines().find_map(|line| {
        line.trim()
            .strip_prefix(name)?
            .trim()
            .strip_prefix(':')
            .map(str::trim)
    })
}
fn seconds(status: &str, name: &str) -> Option<f64> {
    let value = field(status, name)?
        .split('s')
        .next()?
        .trim()
        .parse::<f64>()
        .ok()?;
    value.is_finite().then_some(value)
}
fn clock_healthy(status: &str) -> bool {
    let source = field(status, "Source").unwrap_or("").to_ascii_lowercase();
    let stratum = field(status, "Stratum")
        .and_then(|v| v.split_whitespace().next())
        .and_then(|v| v.parse::<u8>().ok());
    field(status, "Leap Indicator").is_some_and(|v| v.starts_with('0'))
        && stratum.is_some_and(|v| (1..=15).contains(&v))
        && !source.is_empty()
        && !source.contains("local")
        && !source.contains("free-running")
        && seconds(status, "Phase Offset").is_some_and(|v| v.abs() <= 0.1)
        && seconds(status, "Root Dispersion").is_some_and(|v| v <= 0.1)
        && seconds(status, "Time since Last Good Sync Time")
            .is_some_and(|v| (0.0..=300.0).contains(&v))
        && field(status, "Last Sync Error").is_some_and(|v| v.starts_with('0'))
}
fn require_clock_health() -> io::Result<()> {
    if cfg!(target_os = "macos") {
        let output = Command::new("sntp")
            .arg("time.google.com")
            .output()
            .map_err(|_| io::Error::other("Mac independent clock status unavailable"))?;
        if !output.status.success() || !mac_clock_healthy(&String::from_utf8_lossy(&output.stdout))
        {
            return Err(io::Error::other(
                "Mac clock offset or uncertainty unverified",
            ));
        }
        return Ok(());
    }
    if !cfg!(windows) {
        return Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "independent clock check unavailable",
        ));
    }
    let output = Command::new("w32tm")
        .args(["/query", "/status", "/verbose"])
        .output()
        .map_err(|_| io::Error::other("local clock status unavailable"))?;
    if !output.status.success() || !clock_healthy(&String::from_utf8_lossy(&output.stdout)) {
        return Err(io::Error::other("local clock health unverified"));
    }
    Ok(())
}

fn mac_clock_healthy(status: &str) -> bool {
    let mut fields = status.split_whitespace();
    let (Some(offset), Some("+/-"), Some(uncertainty), Some("time.google.com")) =
        (fields.next(), fields.next(), fields.next(), fields.next())
    else {
        return false;
    };
    let (Ok(offset), Ok(uncertainty)) = (offset.parse::<f64>(), uncertainty.parse::<f64>()) else {
        return false;
    };
    offset.is_finite()
        && uncertainty.is_finite()
        && uncertainty >= 0.0
        && offset.abs() + uncertainty <= 0.100
}

#[derive(Deserialize)]
struct SessionEvidence {
    start_ns: i64,
    end_ns: i64,
    calendar_version: String,
    classification: String,
}
fn require_source_and_storage(
    journal: &Path,
    master: &Path,
    manifest: &Path,
    calendar: &Path,
    now: i64,
    nse_source: Option<(&Path, &Path)>,
    universe: Option<&Path>,
    raw_capture_only: bool,
) -> io::Result<SessionEvidence> {
    if !Path::new("src/quant/apps/dhan_quote_preflight.py").is_file() {
        return Err(io::Error::other("repository preflight unavailable"));
    }
    let python = std::env::var_os("ALPHA_GRID_PYTHON").unwrap_or_else(|| {
        OsString::from(if cfg!(windows) {
            ".venv\\Scripts\\python.exe"
        } else {
            ".venv/bin/python"
        })
    });
    let mut command = Command::new(python);
    command
        .args(["-m", "quant.apps.dhan_quote_preflight", "--journal"])
        .arg(journal)
        .arg("--master")
        .arg(master)
        .arg("--manifest")
        .arg(manifest)
        .arg("--calendar")
        .arg(calendar)
        .arg("--now-ns")
        .arg(now.to_string());
    if raw_capture_only {
        command.arg("--raw-capture-only");
    }
    if let Some((source, source_manifest)) = nse_source {
        command
            .arg("--nse-security")
            .arg(source)
            .arg("--nse-manifest")
            .arg(source_manifest);
        if let Some(universe) = universe {
            command.arg("--universe").arg(universe);
        } else {
            command.arg("--pilot-three");
        }
    }
    let output = command
        .env("PYTHONPATH", "src")
        .env_remove("DHAN_ACCESS_TOKEN")
        .env_remove("DHAN_CLIENT_ID")
        .env_remove("DHAN_API_KEY")
        .env_remove("DHAN_API_SECRET")
        .output()
        .map_err(|_| io::Error::other("private source preflight unavailable"))?;
    if !output.status.success() {
        return Err(io::Error::other("private source preflight blocked"));
    }
    let evidence: SessionEvidence = serde_json::from_slice(&output.stdout)
        .map_err(|_| io::Error::other("private source preflight invalid"))?;
    if evidence.calendar_version.is_empty()
        || evidence.classification != "UNVERIFIED"
        || !(evidence.start_ns..evidence.end_ns).contains(&now)
    {
        return Err(io::Error::other("verified session unavailable"));
    }
    Ok(evidence)
}

fn cached_history() -> Option<serde_json::Value> {
    let python = std::env::var_os("ALPHA_GRID_PYTHON").unwrap_or_else(|| {
        OsString::from(if cfg!(windows) {
            ".venv\\Scripts\\python.exe"
        } else {
            ".venv/bin/python"
        })
    });
    let output = Command::new(python)
        .args(["-m", "quant.apps.cockpit_cache"])
        .env("PYTHONPATH", "src")
        .env_remove("ALPHA_GRID_UI_SESSION_SECRET")
        .env_remove("DHAN_ACCESS_TOKEN")
        .env_remove("DHAN_CLIENT_ID")
        .env_remove("DHAN_API_KEY")
        .env_remove("DHAN_API_SECRET")
        .output()
        .ok()?;
    output
        .status
        .success()
        .then(|| serde_json::from_slice(&output.stdout).ok())
        .flatten()
}

struct SingleSessionCalendar(SessionEvidence);
impl MarketCalendar for SingleSessionCalendar {
    fn session_at(&self, timestamp_ns: i64) -> Option<String> {
        (self.0.start_ns..self.0.end_ns)
            .contains(&timestamp_ns)
            .then(|| self.0.calendar_version.clone())
    }
}

const MAX_QUOTE_CAPTURE_NS: i64 = 60_000_000_000;
const AUCTION_STOP_MARGIN_NS: i64 = 60_000_000_000;

fn observation_deadline(now: i64, session_end: i64) -> io::Result<i64> {
    Ok(now
        .saturating_add(MAX_QUOTE_CAPTURE_NS)
        .min(session_deadline(now, session_end)?))
}

fn session_deadline(now: i64, session_end: i64) -> io::Result<i64> {
    let cutoff = session_end
        .checked_sub(AUCTION_STOP_MARGIN_NS)
        .ok_or_else(|| io::Error::other("observation cutoff unavailable"))?;
    if now >= cutoff.saturating_sub(10_000_000_000) {
        return Err(io::Error::other("observation window too close to auction"));
    }
    Ok(cutoff)
}

fn pilot_cutoff(start_ns: i64) -> io::Result<i64> {
    start_ns
        .checked_add(5 * 60 * 60 * 1_000_000_000 + 30 * 60 * 1_000_000_000)
        .ok_or_else(|| io::Error::other("pilot cutoff overflow"))
}

async fn shutdown_requested(stop_file: Option<&Path>) -> io::Result<()> {
    if let Some(path) = stop_file {
        tokio::select! {
            result = tokio::signal::ctrl_c() => result?,
            _ = async {
                loop {
                    if path.exists() { break; }
                    tokio::time::sleep(Duration::from_millis(100)).await;
                }
            } => {}
        }
    } else {
        tokio::signal::ctrl_c().await?;
    }
    Ok(())
}

fn redact_connection<T, E>(result: Result<T, E>) -> io::Result<T> {
    result.map_err(|_| io::Error::other("Dhan quote connection failed"))
}

fn install_crypto_provider() -> io::Result<()> {
    rustls::crypto::ring::default_provider()
        .install_default()
        .map_err(|_| io::Error::other("TLS provider initialization failed"))
}

async fn finish_feed_worker(
    worker: tokio::task::JoinHandle<io::Result<()>>,
    status: &TelemetryShared,
) -> bool {
    let result = worker.await;
    status.stop_feed();
    match result {
        Ok(Ok(())) => {
            status
                .record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::CaptureWindowEnded));
            true
        }
        Ok(Err(_)) => {
            status.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::WorkerFailed));
            false
        }
        Err(_) => {
            status.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::WorkerPanicked));
            false
        }
    }
}

fn upgrade_failure_reason(error: &tokio_tungstenite::tungstenite::Error) -> FeedStopReason {
    use tokio_tungstenite::tungstenite::Error;
    match error {
        Error::Tls(_) => FeedStopReason::TlsFailed,
        Error::Http(response) => FeedStopReason::UpgradeHttp(response.status().as_u16()),
        Error::Protocol(_) => FeedStopReason::UpgradeProtocolFailed,
        Error::Io(_) => FeedStopReason::TlsOrUpgradeIoFailed,
        _ => FeedStopReason::UpgradeProtocolFailed,
    }
}

async fn connect_once(
    credentials: Credentials,
    status: &TelemetryShared,
) -> io::Result<
    tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>>,
> {
    status.record_feed_lifecycle(FeedLifecycle::ResolvingDns);
    let addrs: Vec<_> = lookup_host(("api-feed.dhan.co", 443))
        .await
        .map_err(|_| {
            status.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::DnsFailed));
            io::Error::other("Dhan quote DNS failed")
        })?
        .collect();
    if addrs.is_empty() {
        status.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::DnsFailed));
        return Err(io::Error::other("Dhan quote DNS returned no address"));
    }
    status.record_feed_lifecycle(FeedLifecycle::DnsResolved);
    status.record_feed_lifecycle(FeedLifecycle::ConnectingTcp);
    let stream = TcpStream::connect(addrs.as_slice()).await.map_err(|_| {
        status.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::TcpFailed));
        io::Error::other("Dhan quote TCP failed")
    })?;
    status.record_feed_lifecycle(FeedLifecycle::TcpConnected);
    // Dhan v2 requires query authentication. The URL exists only for this
    // attempt; upstream errors and the URL are never propagated.
    let url = format!(
        "wss://api-feed.dhan.co/?version=2&token={}&clientId={}&authType=2",
        credentials.token, credentials.client
    );
    status.record_feed_lifecycle(FeedLifecycle::TlsAndUpgradeStarted);
    let result =
        tokio_tungstenite::client_async_tls_with_config(url.as_str(), stream, None, None).await;
    drop(url);
    match result {
        Ok((socket, _)) => {
            status.record_feed_lifecycle(FeedLifecycle::TlsEstablished);
            status.record_feed_lifecycle(FeedLifecycle::WebSocketUpgraded);
            Ok(socket)
        }
        Err(error) => {
            let reason = upgrade_failure_reason(&error);
            if matches!(
                reason,
                FeedStopReason::UpgradeHttp(_) | FeedStopReason::UpgradeProtocolFailed
            ) {
                status.record_feed_lifecycle(FeedLifecycle::TlsEstablished);
            }
            status.record_feed_lifecycle(FeedLifecycle::Stopped(reason));
            redact_connection(Err(error))
        }
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    install_crypto_provider()?;
    let mut security_id = None::<String>;
    let mut security_ids = None::<String>;
    let mut ledger = PathBuf::from(".local/paper-approvals.qp");
    let mut port = 8765_u16;
    let mut live = false;
    let mut online = false;
    let mut raw_capture_only = false;
    let mut eligible_projection = false;
    let mut standard_full = false;
    let mut stop_file = None::<PathBuf>;
    let mut journal = None::<PathBuf>;
    let mut master = None::<PathBuf>;
    let mut manifest = None::<PathBuf>;
    let mut calendar = None::<PathBuf>;
    let mut nse_security = None::<PathBuf>;
    let mut nse_manifest = None::<PathBuf>;
    let mut universe = None::<PathBuf>;
    let mut depth_books = std::env::var_os("ALPHA_GRID_DEPTH_BOOKS_FILE").map(PathBuf::from);
    let mut arguments = std::env::args().skip(1);
    while let Some(flag) = arguments.next() {
        if flag == "--dhan-quote-readonly" {
            live = true;
            continue;
        }
        if flag == "--online" {
            online = true;
            continue;
        }
        if flag == "--raw-capture-only" {
            raw_capture_only = true;
            continue;
        }
        if flag == "--eligible-projection" {
            eligible_projection = true;
            continue;
        }
        if flag == "--standard-full" {
            standard_full = true;
            continue;
        }
        let value = arguments.next().ok_or("missing argument value")?;
        match flag.as_str() {
            "--security-id" => security_id = Some(value),
            "--security-ids" => security_ids = Some(value),
            "--approval-ledger" => ledger = PathBuf::from(value),
            "--port" => port = value.parse()?,
            "--quote-journal" => journal = Some(PathBuf::from(value)),
            "--instrument-master" => master = Some(PathBuf::from(value)),
            "--master-manifest" => manifest = Some(PathBuf::from(value)),
            "--calendar" => calendar = Some(PathBuf::from(value)),
            "--nse-security" => nse_security = Some(PathBuf::from(value)),
            "--nse-manifest" => nse_manifest = Some(PathBuf::from(value)),
            "--universe" => universe = Some(PathBuf::from(value)),
            "--depth-books" => depth_books = Some(PathBuf::from(value)),
            "--stop-file" => stop_file = Some(PathBuf::from(value)),
            _ => return Err("unknown option".into()),
        }
    }
    if online && !live {
        return Err("online mode requires read-only quote scope".into());
    }
    if raw_capture_only && (!online || universe.is_none()) {
        return Err("raw-only capture requires online frozen universe".into());
    }
    if eligible_projection && !raw_capture_only {
        return Err("eligible projection requires independent raw capture".into());
    }
    if standard_full && (!live || !online || !raw_capture_only) {
        return Err("Full subscription requires approved online raw capture".into());
    }
    if security_id.is_some() && security_ids.is_some() {
        return Err("choose one quote scope".into());
    }
    let scoped: Vec<(String, String)> = if let Some(ids) = security_ids {
        if !live && universe.is_none() {
            return Err("read-only NSE cash scope required".into());
        }
        if ids == "1333,2885,1594" && universe.is_none() {
            vec![
                ("1333".into(), "HDFCBANK".into()),
                ("2885".into(), "RELIANCE".into()),
                ("1594".into(), "INFY".into()),
            ]
        } else {
            let path = universe.as_deref().ok_or("fixed universe path required")?;
            let manifest: serde_json::Value = serde_json::from_slice(&std::fs::read(path)?)?;
            let entries = manifest
                .get("instruments")
                .and_then(|v| v.as_array())
                .ok_or("fixed universe unavailable")?;
            if manifest.get("schema_version").and_then(|v| v.as_str())
                != Some("alpha-grid-cross-stock-universe-v1")
                || entries.len() != 25
            {
                return Err("fixed 25-name universe required".into());
            }
            let pairs = entries
                .iter()
                .map(|row| {
                    Ok::<_, Box<dyn std::error::Error>>((
                        row.get("security_id")
                            .and_then(|v| v.as_str())
                            .ok_or("universe ID missing")?
                            .to_string(),
                        row.get("symbol")
                            .and_then(|v| v.as_str())
                            .ok_or("universe symbol missing")?
                            .to_string(),
                    ))
                })
                .collect::<Result<Vec<_>, _>>()?;
            if pairs
                .iter()
                .map(|(id, _)| id.as_str())
                .collect::<Vec<_>>()
                .join(",")
                != ids
                || pairs
                    .iter()
                    .map(|(id, _)| id)
                    .collect::<std::collections::BTreeSet<_>>()
                    .len()
                    != 25
            {
                return Err("exact frozen 25-name quote scope required".into());
            }
            pairs
        }
    } else {
        let id = security_id
            .as_deref()
            .ok_or("--security-id or --security-ids required")?;
        if !id.bytes().all(|byte| byte.is_ascii_digit()) || (live && id != "2885") {
            return Err("single NSE cash security ID 2885 required".into());
        }
        vec![(
            id.to_string(),
            if live { "RELIANCE".into() } else { "".into() },
        )]
    };
    let secret = std::env::var("ALPHA_GRID_UI_SESSION_SECRET")
        .map_err(|_| "ALPHA_GRID_UI_SESSION_SECRET is required")?;
    let instruments: Vec<Instrument> = scoped
        .iter()
        .map(|(id, symbol)| Instrument {
            venue: "NSE".into(),
            exchange_segment: "NSE_EQ".into(),
            security_id: id.clone(),
            symbol: symbol.clone(),
        })
        .collect();
    let prepared = if live {
        if std::env::var("ALPHA_GRID_ENABLE_DHAN_QUOTE").as_deref() != Ok("1") {
            return Err("read-only Dhan quote mode disabled".into());
        }
        if !raw_capture_only || eligible_projection {
            require_clock_health()?;
        }
        let journal = journal.ok_or("--quote-journal required")?;
        let master = master.ok_or("--instrument-master required")?;
        let manifest = manifest.ok_or("--master-manifest required")?;
        let calendar = calendar.ok_or("--calendar required")?;
        let nse_source = if instruments.len() > 1 {
            Some((
                nse_security.as_deref().ok_or("--nse-security required")?,
                nse_manifest.as_deref().ok_or("--nse-manifest required")?,
            ))
        } else {
            None
        };
        let evidence = require_source_and_storage(
            &journal,
            &master,
            &manifest,
            &calendar,
            now_ns(),
            nse_source,
            universe.as_deref(),
            raw_capture_only && !eligible_projection,
        )?;
        let mut deadline_ns = if online {
            session_deadline(now_ns(), evidence.end_ns)?
        } else {
            observation_deadline(now_ns(), evidence.end_ns)?
        };
        if instruments.len() == 3 {
            deadline_ns = deadline_ns.min(pilot_cutoff(evidence.start_ns)?);
            if now_ns() >= deadline_ns.saturating_sub(10_000_000_000) {
                return Err("pilot window expired".into());
            }
        }
        let credentials = credentials()?;
        let writer = JournalWriter::open(&journal, DurabilityPolicy::EveryEvent)?;
        if writer.recovered_tail_bytes() != 0 || writer.estimated_bytes()? > writer.max_allowed_bytes() {
            return Err("raw journal integrity or capacity blocked".into());
        }
        Some((evidence, credentials, writer, deadline_ns))
    } else {
        None
    };
    let mode = if live { "OBSERVE_ONLY" } else { "DATA_ONLY" };
    let session_id = if live {
        format!("observe-only-dhan-quote-{}", now_ns())
    } else {
        "no-feed".into()
    };
    let shared = TelemetryShared::new(&instruments, mode, &session_id)?;
    if live {
        shared.record_feed_lifecycle(FeedLifecycle::PreflightPassed);
    }
    let mut gateway = Gateway::new(Arc::clone(&shared), GatewayConfig::default())?
        .with_local_session(secret)?
        .with_approval_ledger(&ledger)?;
    if let Some(history) = cached_history() {
        if gateway.with_historical_cache(history).is_err() {
            eprintln!("cached Dhan historical source rejected; NO TRADE");
        }
    } else {
        eprintln!("cached Dhan historical source unavailable; NO TRADE");
    }
    let listener = TcpListener::bind(("127.0.0.1", port)).await?;
    eprintln!("DATA UNAVAILABLE / NO TRADE cockpit on 127.0.0.1:{port}");
    let (shutdown, receiver) = watch::channel(false);
    let server = tokio::spawn(gateway.serve(listener, receiver));
    if let Some(books_path) = depth_books {
        let shared_depth = Arc::clone(&shared);
        tokio::spawn(async move {
            let mut last_read_time = std::time::SystemTime::UNIX_EPOCH;
            loop {
                tokio::time::sleep(Duration::from_millis(500)).await;
                if let Ok(metadata) = std::fs::metadata(&books_path) {
                    if let Ok(modified) = metadata.modified() {
                        if modified > last_read_time {
                            last_read_time = modified;
                            if let Ok(bytes) = std::fs::read(&books_path) {
                                if let Ok(doc) = serde_json::from_slice::<serde_json::Value>(&bytes)
                                {
                                    if let Some(books) = doc.get("books").and_then(|b| b.as_array())
                                    {
                                        for item in books {
                                            if let Some(sec_id) =
                                                item.get("security_id").and_then(|s| s.as_str())
                                            {
                                                let segment = item
                                                    .get("exchange_segment")
                                                    .and_then(|s| s.as_str())
                                                    .unwrap_or("NSE_EQ");
                                                let key = format!("{}:{}", segment, sec_id);
                                                if let Ok(wire_depth) =
                                                    serde_json::from_value::<WireDepth>(
                                                        item.clone(),
                                                    )
                                                {
                                                    shared_depth
                                                        .update_depth_book(&key, wire_depth);
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        });
    }
    let feed = prepared.map(|(evidence, credentials, mut writer, deadline_ns)| {
        let shared = Arc::clone(&shared);
        let instruments = instruments.clone();
        let feed_session = session_id.clone();
        let (feed_stop, mut feed_receiver) = watch::channel(false);
        let timer_sender = feed_stop.clone();
        let timer = tokio::spawn(async move {
            let remaining = deadline_ns.saturating_sub(now_ns()).max(0) as u64;
            tokio::time::sleep(Duration::from_nanos(remaining)).await;
            let _ = timer_sender.send(true);
        });
        let monitor_status = Arc::clone(&shared);
        let worker = tokio::spawn(async move {
            let mut capture = StandardCapture::for_instruments(instruments.clone());
            let mut publisher = TelemetryPublisher::new(Arc::clone(&shared), &instruments);
            if raw_capture_only {
                publisher.quarantine_raw_only();
            }
            if eligible_projection {
                publisher.enable_eligible_projection();
            }
            let mut engine = MarketEngine::new(
                Box::new(SingleSessionCalendar(evidence)),
                BarConfig {
                    timestamp_policy: TimestampPolicy::Receive,
                    max_lateness_ns: 0,
                    max_open_bars_per_instrument: 8,
                },
                FeatureConfig {
                    max_cross_feed_age_ns: 1_000_000_000,
                    stale_after_ns: 5_000_000_000,
                    rolling_window: 8,
                },
            ).map_err(|_| io::Error::other("quote engine unavailable"))?;
            let projection_clock_watch = eligible_projection.then(|| {
                let status = Arc::clone(&shared);
                tokio::spawn(async move {
                    loop {
                        tokio::time::sleep(Duration::from_secs(60)).await;
                        if require_clock_health().is_ok() {
                            status.enable_eligible_projection();
                        } else {
                            status.disable_eligible_projection();
                        }
                    }
                })
            });
            let result = async {
                if *feed_receiver.borrow() || now_ns() >= deadline_ns {
                    shared.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::CaptureWindowEnded));
                    return Ok(());
                }
                let socket = tokio::select! {
                    changed = feed_receiver.changed() => {
                        let _ = changed;
                        shared.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::Cancelled));
                        return Ok(());
                    }
                    result = tokio::time::timeout(Duration::from_secs(10), connect_once(credentials, &shared)) => {
                        match result {
                            Ok(result) => result?,
                            Err(_) => {
                                shared.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::ConnectTimedOut));
                                return Err(io::Error::new(io::ErrorKind::TimedOut, "Dhan quote connection timed out"));
                            }
                        }
                    }
                };
                let remaining = deadline_ns.saturating_sub(now_ns()).max(0) as u64;
                let outcome = tokio::select! {
                    result = tokio::time::timeout(
                        Duration::from_nanos(remaining),
                        run_authenticated_standard_socket(
                            socket, &mut capture, &mut publisher, &mut writer, &mut engine,
                            &feed_session, Duration::from_secs(30), standard_full, &mut feed_receiver,
                        ),
                    ) => match result {
                        Ok(result) => result,
                        Err(_) => {
                            shared.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::CaptureWindowEnded));
                            Ok(())
                        }
                    },
                    _ = async {
                        loop {
                            tokio::time::sleep(Duration::from_secs(60)).await;
                            if require_clock_health().is_err() { break; }
                        }
                    }, if online && !raw_capture_only => {
                        shared.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::ClockUnhealthy));
                        Err(io::Error::other("local clock health lost"))
                    }
                };
                if online && outcome.is_ok() && now_ns() < deadline_ns.saturating_sub(1_000_000_000) {
                    return Err(io::Error::other("Dhan quote feed ended before session cutoff"));
                }
                outcome
            }.await;
            if let Some(watch) = projection_clock_watch {
                watch.abort();
            }
            writer.close().map_err(|_| {
                shared.set_journal_blocked(true);
                shared.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::StorageFailed));
                io::Error::other("quote journal close failed")
            })?;
            result
        });
        let task = tokio::spawn(async move {
            let success = finish_feed_worker(worker, &monitor_status).await;
            if !success {
                eprintln!("read-only Dhan feed stopped; NO TRADE");
            }
            success
        });
        (feed_stop, timer, task)
    });
    if let Some((stop, timer, mut task)) = feed {
        if online {
            tokio::select! {
                result = &mut task => {
                    timer.abort();
                    let _ = result;
                    // The launcher may recheck every source gate before one
                    // bounded replacement connection. This process exits.
                }
                result = shutdown_requested(stop_file.as_deref()) => {
                    result?;
                    let _ = stop.send(true);
                    timer.abort();
                    let _ = task.await;
                }
            }
        } else {
            shutdown_requested(stop_file.as_deref()).await?;
            let _ = stop.send(true);
            timer.abort();
            let _ = task.await;
        }
    } else {
        shutdown_requested(stop_file.as_deref()).await?;
    }
    let _ = shutdown.send(true);
    server.await??;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mac_clock_parser_keeps_offset_plus_uncertainty_gate() {
        assert!(mac_clock_healthy(
            "+0.010450 +/- 0.052030 time.google.com 2001:db8::1"
        ));
        assert!(!mac_clock_healthy(
            "+0.007047 +/- 0.099384 time.google.com 2001:db8::1"
        ));
        assert!(!mac_clock_healthy(
            "+0.010450 +/- 0.052030 time.apple.com 2001:db8::1"
        ));
        assert!(!mac_clock_healthy("clock unavailable"));
    }

    #[test]
    fn november_two_pilot_cutoff_is_1445_ist() {
        assert_eq!(
            pilot_cutoff(1_793_591_100_000_000_000).unwrap(),
            1_793_610_900_000_000_000
        );
    }

    #[test]
    fn tls_provider_installs_before_startup_and_failure_is_clean() {
        assert!(install_crypto_provider().is_ok());
        assert!(rustls::crypto::CryptoProvider::get_default().is_some());
        let _ = rustls::ClientConfig::builder();
        let error = install_crypto_provider().unwrap_err();
        assert_eq!(error.to_string(), "TLS provider initialization failed");
    }

    #[tokio::test]
    async fn worker_panic_disconnects_observation_feed() {
        let instrument = Instrument {
            venue: "NSE".into(),
            exchange_segment: "NSE_EQ".into(),
            security_id: "2885".into(),
            symbol: "RELIANCE".into(),
        };
        let status =
            TelemetryShared::new(&[instrument], "OBSERVE_ONLY", "fixture-session").unwrap();
        status.start_observation_feed("fixture-session").unwrap();
        assert!(status.snapshot(1, 5_000_000_000).unwrap().feed_connected);
        let worker: tokio::task::JoinHandle<io::Result<()>> = tokio::spawn(async {
            panic!("isolated worker failure fixture");
        });
        assert!(!finish_feed_worker(worker, &status).await);
        let snapshot = status.snapshot(2, 5_000_000_000).unwrap();
        assert!(!snapshot.feed_connected);
        assert!(
            snapshot
                .no_trade_reasons
                .contains(&"OBSERVATION_ONLY".into())
        );
        assert!(
            snapshot
                .audit
                .iter()
                .any(|event| event.detail == "STOPPED:WORKER_PANICKED")
        );
    }

    #[test]
    fn handshake_failure_exposes_only_http_status() {
        use tokio_tungstenite::tungstenite::{Error, http::Response};
        let response = Response::builder()
            .status(403)
            .body(Some(b"sensitive body".to_vec()))
            .unwrap();
        let reason = upgrade_failure_reason(&Error::Http(Box::new(response)));
        assert_eq!(reason, FeedStopReason::UpgradeHttp(403));
        let shared = TelemetryShared::new(
            &[Instrument {
                venue: "NSE".into(),
                exchange_segment: "NSE_EQ".into(),
                security_id: "2885".into(),
                symbol: "RELIANCE".into(),
            }],
            "OBSERVE_ONLY",
            "fixture-session",
        )
        .unwrap();
        shared.record_feed_lifecycle(FeedLifecycle::Stopped(reason));
        let audit = shared.snapshot(1, 5_000_000_000).unwrap().audit;
        assert_eq!(audit.last().unwrap().detail, "STOPPED:UPGRADE_HTTP_403");
        assert!(!format!("{audit:?}").contains("sensitive body"));
    }

    const HEALTHY: &str = "Leap Indicator: 0(no warning)\nStratum: 2\nSource: time.example\nPhase Offset: -0.001s\nRoot Dispersion: 0.01s\nTime since Last Good Sync Time: 12s\nLast Sync Error: 0";

    #[test]
    fn startup_clock_and_session_scope_fail_closed() {
        assert!(clock_healthy(HEALTHY));
        assert!(!clock_healthy(&HEALTHY.replace("-0.001s", "0.2s")));
        assert!(!clock_healthy(
            &HEALTHY.replace("time.example", "Local CMOS Clock")
        ));
        assert!(!clock_healthy("Access is denied"));
        let calendar = SingleSessionCalendar(SessionEvidence {
            start_ns: 100,
            end_ns: 200,
            calendar_version: "fixture-only".into(),
            classification: "UNVERIFIED".into(),
        });
        assert!(calendar.session_at(99).is_none());
        assert!(calendar.session_at(150).is_some());
        assert!(calendar.session_at(200).is_none());
        let session_end = 500_000_000_000_i64;
        assert_eq!(
            observation_deadline(100_000_000_000, session_end).unwrap(),
            160_000_000_000
        );
        assert_eq!(
            observation_deadline(400_000_000_000, session_end).unwrap(),
            440_000_000_000
        );
        assert_eq!(
            session_deadline(100_000_000_000, session_end).unwrap(),
            440_000_000_000
        );
        assert!(observation_deadline(430_000_000_000, session_end).is_err());
    }

    #[tokio::test]
    async fn launcher_stop_file_ends_without_network() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("stop.flag");
        let signal = tokio::spawn(async move { shutdown_requested(Some(&path)).await });
        tokio::time::sleep(Duration::from_millis(120)).await;
        std::fs::write(directory.path().join("stop.flag"), b"").unwrap();
        tokio::time::timeout(Duration::from_secs(2), signal)
            .await
            .unwrap()
            .unwrap()
            .unwrap();
    }

    #[test]
    fn credential_format_and_connection_errors_never_echo_secrets() {
        assert!(credential_format_valid("fixture.token-abc", "12345"));
        assert!(!credential_format_valid("fixture&injected=1", "12345"));
        assert!(!credential_format_valid("fixture", "id-123"));
        let secret = "fixture-secret-marker";
        let credentials = Credentials {
            token: secret.into(),
            client: "12345".into(),
        };
        assert!(!format!("{credentials:?}").contains(secret));
        assert!(!format!("{credentials:?}").contains("12345"));
        let result: io::Result<()> = redact_connection::<(), _>(Err(secret));
        assert!(!result.unwrap_err().to_string().contains(secret));
    }

    #[test]
    fn dhan_websocket_request_has_absolute_path() {
        use tokio_tungstenite::tungstenite::client::IntoClientRequest;
        let old = "wss://api-feed.dhan.co?version=2&token=fixture&clientId=12345&authType=2"
            .into_client_request()
            .unwrap();
        assert!(
            old.uri()
                .path_and_query()
                .unwrap()
                .as_str()
                .starts_with('?')
        );
        let request = "wss://api-feed.dhan.co/?version=2&token=fixture&clientId=12345&authType=2"
            .into_client_request()
            .unwrap();
        assert_eq!(
            request.uri().path_and_query().unwrap().as_str(),
            "/?version=2&token=fixture&clientId=12345&authType=2"
        );
    }
}
