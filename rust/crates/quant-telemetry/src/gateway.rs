//! Local read-only HTTP and bounded latest-value WebSocket telemetry.
use crate::protocol::{
    Envelope, LatencySummary, MessageType, WIRE_VERSION, WireEngineSnapshot,
    WireEngineSnapshotStatus, WireEngineUpdate,
};
use crate::read_model::{TelemetryShared, now_ns};
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Path, State};
use axum::http::{HeaderMap, HeaderValue, StatusCode, header};
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use futures_util::StreamExt;
use quant_paper::ledger::{LedgerEvent, LedgerRecord, PaperLedgerReader, PaperLedgerWriter};
use ring::hmac;
use std::collections::BTreeSet;
use std::path::Path as FilePath;
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::net::TcpListener;
use tokio::sync::watch;

#[derive(Clone, Copy)]
pub struct GatewayConfig {
    pub publication_hz: u8,
    pub max_clients: usize,
    pub stale_after_ns: i64,
}
impl Default for GatewayConfig {
    fn default() -> Self {
        Self {
            publication_hz: 10,
            max_clients: 8,
            stale_after_ns: 5_000_000_000,
        }
    }
}
impl GatewayConfig {
    pub fn validate(self) -> Result<Self, &'static str> {
        if !(1..=20).contains(&self.publication_hz)
            || !(1..=32).contains(&self.max_clients)
            || self.stale_after_ns <= 0
        {
            return Err("invalid gateway limits");
        }
        Ok(self)
    }
}

struct AppState {
    shared: Arc<TelemetryShared>,
    current: arc_swap::ArcSwap<WireEngineSnapshot>,
    updates: watch::Sender<Arc<WireEngineSnapshot>>,
    config: GatewayConfig,
    demo: Option<Arc<DemoControls>>,
    research: Option<serde_json::Value>,
    acquisition: Option<serde_json::Value>,
    historical: Option<serde_json::Value>,
    ui_session_secret: Option<String>,
    approval_ledger: Option<Mutex<(PaperLedgerWriter, BTreeSet<String>)>>,
}

pub struct DemoControls {
    pub shared: Arc<TelemetryShared>,
    pub restart_requested: AtomicU64,
    pub paper_enabled: std::sync::atomic::AtomicBool,
    pub halt_requested: std::sync::atomic::AtomicBool,
    approval_changes: Mutex<Vec<(String, bool)>>,
    approved_security_ids: Mutex<BTreeSet<String>>,
}
impl DemoControls {
    pub fn new(shared: Arc<TelemetryShared>) -> Arc<Self> {
        Arc::new(Self {
            shared,
            restart_requested: AtomicU64::new(0),
            paper_enabled: std::sync::atomic::AtomicBool::new(false),
            halt_requested: std::sync::atomic::AtomicBool::new(false),
            approval_changes: Mutex::new(Vec::new()),
            approved_security_ids: Mutex::new(BTreeSet::new()),
        })
    }

    pub fn take_approval_changes(&self) -> Vec<(String, bool)> {
        std::mem::take(&mut *self.approval_changes.lock().expect("approval lock"))
    }
}

pub struct Gateway {
    state: Arc<AppState>,
}
impl Gateway {
    pub fn new(shared: Arc<TelemetryShared>, config: GatewayConfig) -> Result<Self, &'static str> {
        let config = config.validate()?;
        let first = Arc::new(
            shared
                .snapshot(0, config.stale_after_ns)
                .ok_or("read model busy")?,
        );
        let (updates, _) = watch::channel(Arc::clone(&first));
        Ok(Self {
            state: Arc::new(AppState {
                shared,
                current: arc_swap::ArcSwap::from(first),
                updates,
                config,
                demo: None,
                research: None,
                acquisition: None,
                historical: None,
                ui_session_secret: None,
                approval_ledger: None,
            }),
        })
    }

    pub fn with_demo_controls(mut self, controls: Arc<DemoControls>) -> Self {
        Arc::get_mut(&mut self.state)
            .expect("unshared gateway")
            .demo = Some(controls);
        self
    }

    pub fn with_local_session(mut self, secret: String) -> Result<Self, &'static str> {
        if secret.len() < 32
            || !secret
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
        {
            return Err("local UI session secret must be 32+ safe ASCII characters");
        }
        Arc::get_mut(&mut self.state)
            .expect("unshared gateway")
            .ui_session_secret = Some(secret);
        Ok(self)
    }

    pub fn with_approval_ledger(mut self, path: &FilePath) -> std::io::Result<Self> {
        let writer = PaperLedgerWriter::open(path)?;
        let mut reader = PaperLedgerReader::open(path)?;
        let mut approvals = BTreeSet::new();
        while let Some(record) = reader.next_record()? {
            match record.event {
                LedgerEvent::StockApproval {
                    security_id,
                    approved,
                    ..
                } => {
                    if approved {
                        approvals.insert(security_id);
                    } else {
                        approvals.remove(&security_id);
                    }
                }
                _ => {
                    return Err(std::io::Error::other(
                        "approval ledger contains non-approval event",
                    ));
                }
            }
        }
        self.state
            .shared
            .publish_approvals(approvals.iter().cloned().collect());
        if let Some(snapshot) = self
            .state
            .shared
            .snapshot(0, self.state.config.stale_after_ns)
        {
            let snapshot = Arc::new(snapshot);
            self.state.current.store(Arc::clone(&snapshot));
            self.state.updates.send_replace(snapshot);
        }
        Arc::get_mut(&mut self.state)
            .expect("unshared gateway")
            .approval_ledger = Some(Mutex::new((writer, approvals)));
        Ok(self)
    }

    /// Load a previously produced offline manifest. No analysis runs in the gateway.
    pub fn with_research_manifest(
        mut self,
        manifest: serde_json::Value,
    ) -> Result<Self, &'static str> {
        if manifest.get("schema_version").and_then(|v| v.as_str()) != Some("research-v1")
            || manifest.get("status").and_then(|v| v.as_str()).is_none()
            || manifest.get("source_kind").and_then(|v| v.as_str()) != Some("REAL_VERIFIED")
            || manifest.get("source").and_then(|v| v.as_str()) != Some("DHAN_HISTORICAL")
        {
            return Err("verified real-source research manifest required");
        }
        Arc::get_mut(&mut self.state)
            .expect("unshared gateway")
            .research = Some(manifest);
        Ok(self)
    }

    /// Serve only approved public summary fields from an offline status file.
    pub fn with_acquisition_status(
        mut self,
        status: serde_json::Value,
    ) -> Result<Self, &'static str> {
        if status.get("schema_version").and_then(|v| v.as_str()) != Some("acquisition-status-v1") {
            return Err("invalid acquisition status");
        }
        let mut public = serde_json::Map::new();
        for key in [
            "schema_version",
            "status",
            "sources_configured",
            "access_status",
            "source_files_available",
            "genuine_source_files_available",
            "source_verification_status",
            "calendar_completeness",
            "mapping_uncertainty",
            "first_date",
            "last_date",
            "instrument_count",
            "independent_sessions",
            "missing_bars",
            "quality_warning_count",
            "partition_count",
            "partition_bytes",
            "checksum_verified",
            "collection_mode",
            "research_eligibility",
            "historical_dataset_eligibility",
            "training_blocked",
            "blocked_model_families",
            "block_reasons",
        ] {
            if let Some(value) = status.get(key) {
                public.insert(key.into(), value.clone());
            }
        }
        Arc::get_mut(&mut self.state)
            .expect("unshared gateway")
            .acquisition = Some(serde_json::Value::Object(public));
        Ok(self)
    }

    /// Only an offline verifier may supply the pinned authentic Dhan archive.
    pub fn with_historical_cache(&mut self, cache: serde_json::Value) -> Result<(), &'static str> {
        if cache.get("schema_version").and_then(|v| v.as_str()) != Some("dhan-historical-cache-v1")
            || cache.get("status").and_then(|v| v.as_str()) != Some("AVAILABLE")
            || cache.get("source").and_then(|v| v.as_str()) != Some("DHAN_HISTORICAL")
            || cache.get("instrument").and_then(|v| v.as_str()) != Some("NSE_EQ:2885:RELIANCE")
            || cache.get("response_sha256").and_then(|v| v.as_str())
                != Some("bcace97b4dfd1b1cbfe10481b4718b770368a50c5264fb1f61a50f9c325d9861")
            || cache.get("causal_eligibility").and_then(|v| v.as_str()) != Some("BLOCKED")
            || cache.get("production_eligibility").and_then(|v| v.as_str()) != Some("BLOCKED")
            || cache.get("source_integrity").and_then(|v| v.as_str()) != Some("VERIFIED")
            || cache
                .get("bar_timestamp_semantics")
                .and_then(|v| v.as_str())
                != Some("UNVERIFIED")
            || cache
                .get("bars")
                .and_then(|v| v.as_array())
                .is_none_or(|v| v.len() != 720)
        {
            return Err("verified cached historical source required");
        }
        Arc::get_mut(&mut self.state)
            .expect("unshared gateway")
            .historical = Some(cache);
        Ok(())
    }

    pub fn router(&self) -> Router {
        Router::new()
            .route("/api/snapshot", get(snapshot))
            .route(
                "/api/session",
                post(ui_session)
                    .get(ui_session_status)
                    .delete(ui_session_logout),
            )
            .route("/api/paper/approval", post(paper_approval))
            .route("/api/health", get(health))
            .route("/api/paper", get(paper))
            .route("/api/research", get(research))
            .route("/api/acquisition", get(acquisition))
            .route("/api/historical/reliance", get(historical_reliance))
            .route("/api/instruments/{key}", get(instrument))
            .route("/ws", get(websocket))
            .route("/api/demo/control", post(demo_control))
            .with_state(Arc::clone(&self.state))
    }

    /// A stopped gateway does not stop the collector or journal.
    pub async fn serve(
        self,
        listener: TcpListener,
        mut shutdown: watch::Receiver<bool>,
    ) -> std::io::Result<()> {
        if !listener.local_addr()?.ip().is_loopback() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::PermissionDenied,
                "cockpit gateway requires loopback listener",
            ));
        }
        let state = Arc::clone(&self.state);
        let mut tick = tokio::time::interval(Duration::from_millis(
            1000 / u64::from(state.config.publication_hz),
        ));
        tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        let publisher = tokio::spawn(async move {
            let mut generation = 0_u64;
            let mut observed_events = 0_u64;
            let mut observed_epoch = 0_u64;
            let mut publication_samples = std::collections::VecDeque::<u64>::new();
            loop {
                tokio::select! {
                    _ = tick.tick() => {
                        let epoch = state.shared.replay_epoch.load(Ordering::Acquire);
                        if epoch != observed_epoch { observed_epoch = epoch; observed_events = 0; publication_samples.clear(); }
                        let counts = state.shared.counters.load();
                        let total = counts.journal_records_accepted + counts.replayed_records;
                        if total > observed_events {
                            let last = state.shared.last_observed_mono_ns.load(Ordering::Acquire);
                            if last > 0 {
                                let now = state.shared.clock_started.elapsed().as_nanos().min(u64::MAX as u128) as u64;
                                publication_samples.push_back(now.saturating_sub(last));
                                while publication_samples.len() > 10_000 { publication_samples.pop_front(); }
                                let mut sorted: Vec<_> = publication_samples.iter().copied().collect();
                                sorted.sort_unstable();
                                let at = |fraction: f64| sorted[((sorted.len() as f64 * fraction).ceil() as usize).saturating_sub(1)];
                                state.shared.publication_latency.store(Arc::new(LatencySummary {
                                    sample_count: sorted.len() as u64, p50_ns: Some(at(0.5).to_string()),
                                    p95_ns: Some(at(0.95).to_string()), p99_ns: Some(at(0.99).to_string()),
                                    max_ns: sorted.last().map(ToString::to_string),
                                }));
                            }
                        }
                        if total > observed_events + 1 {
                            state.shared.coalesced_updates.fetch_add(total - observed_events - 1, Ordering::Relaxed);
                        }
                        observed_events = total;
                        let previous = state.current.load_full();
                        let Some(candidate) = state.shared.snapshot(generation + 1, state.config.stale_after_ns) else { continue; };
                        let mut old_without_generation = (*previous).clone();
                        old_without_generation.generation = candidate.generation.clone();
                        if candidate != old_without_generation {
                            generation += 1;
                            let next = Arc::new(candidate);
                            state.current.store(Arc::clone(&next));
                            state.updates.send_replace(next);
                        }
                    },
                    changed = shutdown.changed() => {
                        if changed.is_err() || *shutdown.borrow() { break; }
                    }
                }
            }
        });
        let router = self.router();
        axum::serve(listener, router)
            .with_graceful_shutdown(async {
                let _ = publisher.await;
            })
            .await
    }
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ControlRequest {
    action: String,
    speed: Option<u8>,
    security_id: Option<String>,
}

fn allowed_origin(headers: &HeaderMap) -> bool {
    matches!(
        headers
            .get(header::ORIGIN)
            .and_then(|value| value.to_str().ok()),
        Some(
            "http://127.0.0.1:5173"
                | "http://localhost:5173"
                | "http://127.0.0.1:8765"
                | "http://localhost:8765"
        )
    )
}

fn session_authorized(state: &AppState, headers: &HeaderMap) -> bool {
    let Some(secret) = &state.ui_session_secret else {
        return false;
    };
    headers
        .get(header::COOKIE)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|cookies| {
            cookies.split(';').any(|cookie| {
                cookie
                    .trim()
                    .strip_prefix("alpha_grid_session=")
                    .is_some_and(|value| {
                        let mut parts = value.split('.');
                        let (Some("v1"), Some(expiry), Some(signature), None) =
                            (parts.next(), parts.next(), parts.next(), parts.next())
                        else {
                            return false;
                        };
                        let Ok(expiry_seconds) = expiry.parse::<u64>() else {
                            return false;
                        };
                        let now = SystemTime::now()
                            .duration_since(UNIX_EPOCH)
                            .ok()
                            .map(|v| v.as_secs());
                        if !now.is_some_and(|now| {
                            now < expiry_seconds
                                && expiry_seconds <= now.saturating_add(SESSION_LIFETIME_SECS)
                        }) {
                            return false;
                        }
                        let Some(tag) = decode_hex_tag(signature) else {
                            return false;
                        };
                        let key = hmac::Key::new(hmac::HMAC_SHA256, secret.as_bytes());
                        hmac::verify(
                            &key,
                            format!("alpha-grid-session-v1:{expiry}").as_bytes(),
                            &tag,
                        )
                        .is_ok()
                    })
            })
        })
}

const SESSION_LIFETIME_SECS: u64 = 7 * 24 * 60 * 60;

fn decode_hex_tag(value: &str) -> Option<[u8; 32]> {
    if value.len() != 64 {
        return None;
    }
    let mut result = [0_u8; 32];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        let hex = |byte: u8| match byte {
            b'0'..=b'9' => Some(byte - b'0'),
            b'a'..=b'f' => Some(byte - b'a' + 10),
            _ => None,
        };
        result[index] = (hex(pair[0])? << 4) | hex(pair[1])?;
    }
    Some(result)
}

async fn ui_session_status(State(state): State<Arc<AppState>>, headers: HeaderMap) -> StatusCode {
    if session_authorized(&state, &headers) {
        StatusCode::NO_CONTENT
    } else {
        StatusCode::UNAUTHORIZED
    }
}

async fn ui_session_logout(headers: HeaderMap) -> impl IntoResponse {
    if !allowed_origin(&headers) {
        return StatusCode::FORBIDDEN.into_response();
    }
    (
        StatusCode::NO_CONTENT,
        [
            (
                header::SET_COOKIE,
                HeaderValue::from_static(
                    "alpha_grid_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/",
                ),
            ),
            (header::CACHE_CONTROL, HeaderValue::from_static("no-store")),
        ],
    )
        .into_response()
}

async fn ui_session(State(state): State<Arc<AppState>>, headers: HeaderMap) -> impl IntoResponse {
    let Some(secret) = &state.ui_session_secret else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    if !allowed_origin(&headers)
        || headers
            .get(header::AUTHORIZATION)
            .and_then(|value| value.to_str().ok())
            != Some(format!("Bearer {secret}").as_str())
    {
        return StatusCode::FORBIDDEN.into_response();
    }
    let Ok(now) = SystemTime::now().duration_since(UNIX_EPOCH) else {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    };
    let expiry = now.as_secs().saturating_add(SESSION_LIFETIME_SECS);
    let key = hmac::Key::new(hmac::HMAC_SHA256, secret.as_bytes());
    let tag = hmac::sign(&key, format!("alpha-grid-session-v1:{expiry}").as_bytes());
    let signature = tag
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    let Ok(value) = HeaderValue::from_str(&format!(
        "alpha_grid_session=v1.{expiry}.{signature}; Max-Age={SESSION_LIFETIME_SECS}; HttpOnly; SameSite=Strict; Path=/"
    )) else {
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    };
    (
        StatusCode::NO_CONTENT,
        [
            (header::SET_COOKIE, value),
            (header::CACHE_CONTROL, HeaderValue::from_static("no-store")),
        ],
    )
        .into_response()
}

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ApprovalRequest {
    security_id: String,
    approved: bool,
}

async fn paper_approval(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(request): Json<ApprovalRequest>,
) -> StatusCode {
    if !session_authorized(&state, &headers) || !allowed_origin(&headers) {
        return StatusCode::FORBIDDEN;
    }
    let Some(ledger) = &state.approval_ledger else {
        return StatusCode::SERVICE_UNAVAILABLE;
    };
    if !state
        .current
        .load()
        .instruments
        .iter()
        .any(|item| item.instrument.security_id == request.security_id)
    {
        return StatusCode::BAD_REQUEST;
    }
    let mut ledger = ledger.lock().expect("approval ledger lock");
    if ledger
        .0
        .append(&LedgerRecord::new(
            0,
            LedgerEvent::StockApproval {
                security_id: request.security_id.clone(),
                approved: request.approved,
                timestamp_ns: now_ns(),
            },
        ))
        .is_err()
    {
        return StatusCode::SERVICE_UNAVAILABLE;
    }
    if request.approved {
        ledger.1.insert(request.security_id);
    } else {
        ledger.1.remove(&request.security_id);
    }
    state
        .shared
        .publish_approvals(ledger.1.iter().cloned().collect());
    StatusCode::NO_CONTENT
}

async fn demo_control(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
    Json(request): Json<ControlRequest>,
) -> impl IntoResponse {
    let Some(demo) = &state.demo else {
        return StatusCode::NOT_FOUND;
    };
    if let Some(origin) = headers.get("origin") {
        let allowed = matches!(
            origin.to_str(),
            Ok("http://127.0.0.1:5173"
                | "http://localhost:5173"
                | "http://127.0.0.1:8765"
                | "http://localhost:8765")
        );
        if !allowed {
            return StatusCode::FORBIDDEN;
        }
    }
    match request.action.as_str() {
        "pause" => demo.shared.set_replay_paused(true),
        "resume" => demo.shared.set_replay_paused(false),
        "restart" => {
            demo.restart_requested.fetch_add(1, Ordering::AcqRel);
            demo.shared.set_replay_paused(false);
        }
        "speed" if matches!(request.speed, Some(1 | 2 | 5)) => demo
            .shared
            .replay_speed
            .store(request.speed.unwrap_or(1), Ordering::Release),
        "approve_stock" | "revoke_stock" => {
            if !demo.paper_enabled.load(Ordering::Acquire) {
                return StatusCode::NOT_FOUND;
            }
            let Some(id) = request.security_id else {
                return StatusCode::BAD_REQUEST;
            };
            if !state
                .current
                .load()
                .instruments
                .iter()
                .any(|item| item.instrument.security_id == id)
            {
                return StatusCode::BAD_REQUEST;
            }
            let approved = request.action == "approve_stock";
            let mut pending = demo.approval_changes.lock().expect("approval lock");
            if pending.len() >= 32 {
                return StatusCode::TOO_MANY_REQUESTS;
            }
            let mut active = demo.approved_security_ids.lock().expect("approval lock");
            if active.contains(&id) != approved {
                if approved {
                    active.insert(id.clone());
                } else {
                    active.remove(&id);
                }
                pending.push((id, approved));
            }
        }
        "halt_paper" if demo.paper_enabled.load(Ordering::Acquire) => {
            demo.halt_requested.store(true, Ordering::Release);
        }
        _ => return StatusCode::BAD_REQUEST,
    }
    StatusCode::NO_CONTENT
}

fn envelope(
    state: &AppState,
    kind: MessageType,
    payload: serde_json::Value,
    source: Option<String>,
    publication_sequence: &str,
) -> Envelope {
    Envelope {
        schema_version: WIRE_VERSION,
        message_type: kind,
        instrument_key: None,
        engine_boot_id: state.shared.boot_id.clone(),
        publication_sequence: publication_sequence.into(),
        source_receive_timestamp_ns: source,
        publication_timestamp_ns: now_ns().to_string(),
        provenance: None,
        freshness: None,
        quality: None,
        payload,
    }
}

async fn snapshot(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> Result<Json<Envelope>, StatusCode> {
    if !session_authorized(&state, &headers) {
        return Err(StatusCode::UNAUTHORIZED);
    }
    let current = state.current.load_full();
    Ok(Json(envelope(
        &state,
        MessageType::EngineSnapshot,
        serde_json::to_value(&*current).expect("snapshot JSON"),
        current.latest_data_timestamp_ns.clone(),
        &current.generation,
    )))
}

async fn health(State(state): State<Arc<AppState>>) -> Json<WireEngineSnapshotStatus> {
    Json((&*state.current.load_full()).into())
}

async fn paper(
    State(state): State<Arc<AppState>>,
) -> Result<Json<crate::protocol::WirePaperSnapshot>, StatusCode> {
    state
        .current
        .load()
        .paper
        .clone()
        .map(Json)
        .ok_or(StatusCode::NOT_FOUND)
}

async fn research(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    Json(state.research.clone().unwrap_or_else(|| {
        serde_json::json!({
            "schema_version": "research-v1",
            "status": "NOT_TRAINED",
            "sufficiency_reasons": ["no_dataset_manifest_loaded"],
            "source_kind": null,
            "feature_rows": 0,
            "label_rows": 0,
            "independent_sessions": 0,
            "label_fraction": 0.0,
            "quality_fraction": 0.0,
            "dataset_sha256": null
        })
    }))
}

async fn acquisition(State(state): State<Arc<AppState>>) -> Json<serde_json::Value> {
    Json(state.acquisition.clone().unwrap_or_else(|| {
        serde_json::json!({
            "schema_version": "acquisition-status-v1",
            "status": "WAITING_FOR_REAL_SOURCE",
            "sources_configured": ["DHAN_HISTORICAL_DISABLED", "NSE_LOCAL_IMPORT"],
            "access_status": "UNVERIFIED",
            "source_files_available": 0,
            "genuine_source_files_available": 0,
            "source_verification_status": "UNVERIFIED",
            "calendar_completeness": "UNVERIFIED",
            "mapping_uncertainty": "UNVERIFIED",
            "first_date": null,
            "last_date": null,
            "instrument_count": 0,
            "independent_sessions": 0,
            "missing_bars": null,
            "quality_warning_count": null,
            "partition_count": 0,
            "partition_bytes": 0,
            "checksum_verified": false,
            "collection_mode": "DATA_ONLY_OFFLINE",
            "research_eligibility": "NOT_TRAINED",
            "historical_dataset_eligibility": "BLOCKED",
            "training_blocked": true,
            "blocked_model_families": ["DEPTH", "ORDER_FLOW", "HAWKES", "EXECUTION"],
            "block_reasons": ["no_verified_real_market_dataset"]
        })
    }))
}

async fn historical_reliance(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> Result<Json<serde_json::Value>, StatusCode> {
    if !session_authorized(&state, &headers) {
        return Err(StatusCode::UNAUTHORIZED);
    }
    Ok(Json(state.historical.clone().unwrap_or_else(|| {
        serde_json::json!({
            "schema_version": "dhan-historical-cache-v1",
            "status": "DATA_UNAVAILABLE",
            "source": null,
            "bars": [],
            "causal_eligibility": "BLOCKED"
        })
    })))
}

async fn instrument(
    Path(key): Path<String>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<crate::protocol::WireInstrumentSnapshot>, StatusCode> {
    state
        .current
        .load()
        .instruments
        .iter()
        .find(|v| v.instrument.key == key)
        .cloned()
        .map(Json)
        .ok_or(StatusCode::NOT_FOUND)
}

async fn websocket(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if !allowed_origin(&headers) || !session_authorized(&state, &headers) {
        return StatusCode::FORBIDDEN.into_response();
    }
    let previous = state
        .shared
        .connected_clients
        .fetch_add(1, Ordering::AcqRel);
    if previous >= state.config.max_clients {
        state
            .shared
            .connected_clients
            .fetch_sub(1, Ordering::AcqRel);
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    }
    ws.max_message_size(4096)
        .max_frame_size(4096)
        .on_upgrade(move |socket| client(socket, state))
        .into_response()
}

struct ClientGuard(Arc<TelemetryShared>);
impl Drop for ClientGuard {
    fn drop(&mut self) {
        self.0.connected_clients.fetch_sub(1, Ordering::AcqRel);
    }
}

async fn send(socket: &mut WebSocket, envelope: Envelope) -> bool {
    let Ok(text) = serde_json::to_string(&envelope) else {
        return false;
    };
    if text.len() > 4 * 1024 * 1024 {
        return false;
    }
    matches!(
        tokio::time::timeout(
            Duration::from_secs(2),
            socket.send(Message::Text(text.into()))
        )
        .await,
        Ok(Ok(()))
    )
}

async fn client(mut socket: WebSocket, state: Arc<AppState>) {
    let _guard = ClientGuard(Arc::clone(&state.shared));
    let mut receiver = state.updates.subscribe();
    let initial = state.current.load_full();
    let mut previous = Arc::clone(&initial);
    if !send(
        &mut socket,
        envelope(
            &state,
            MessageType::EngineSnapshot,
            serde_json::to_value(&*initial).expect("snapshot JSON"),
            initial.latest_data_timestamp_ns.clone(),
            &initial.generation,
        ),
    )
    .await
    {
        return;
    }
    let mut heartbeat = tokio::time::interval(Duration::from_secs(5));
    loop {
        tokio::select! {
            changed = receiver.changed() => {
                if changed.is_err() { break; }
                let next = receiver.borrow_and_update().clone();
                let old_generation = previous.generation.parse::<u64>().unwrap_or(0);
                let new_generation = next.generation.parse::<u64>().unwrap_or(0);
                let (kind, payload) = if new_generation != old_generation + 1 || next.replay_epoch != previous.replay_epoch {
                    state.shared.skipped_updates.fetch_add(new_generation.saturating_sub(old_generation + 1), Ordering::Relaxed);
                    (MessageType::EngineSnapshot, serde_json::to_value(&*next))
                } else {
                    let instruments = next.instruments.iter().filter(|v| !previous.instruments.contains(v)).cloned().collect();
                    let update = WireEngineUpdate { generation: next.generation.clone(), status: (&*next).into(), instruments, audit: next.audit.clone() };
                    (MessageType::EngineUpdate, serde_json::to_value(update))
                };
                let Ok(payload) = payload else { break; };
                if !send(&mut socket, envelope(&state, kind, payload, next.latest_data_timestamp_ns.clone(), &next.generation)).await { break; }
                previous = next;
            }
            _ = heartbeat.tick() => {
                let current = state.current.load();
                if !send(&mut socket, envelope(&state, MessageType::Heartbeat, serde_json::json!({}), None, &current.generation)).await { break; }
            }
            received = socket.next() => {
                match received { Some(Ok(Message::Close(_))) | None | Some(Err(_)) => break, _ => {} }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    const TEST_SESSION: &str = "testlocaluisessionsecret0123456789abcdef";

    fn test_cookie() -> String {
        let expiry = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs()
            + SESSION_LIFETIME_SECS;
        let key = hmac::Key::new(hmac::HMAC_SHA256, TEST_SESSION.as_bytes());
        let tag = hmac::sign(&key, format!("alpha-grid-session-v1:{expiry}").as_bytes());
        let signature = tag
            .as_ref()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        format!("alpha_grid_session=v1.{expiry}.{signature}")
    }

    fn websocket_request(url: &str) -> tokio_tungstenite::tungstenite::http::Request<()> {
        use tokio_tungstenite::tungstenite::client::IntoClientRequest;
        let mut request = url.into_client_request().unwrap();
        request
            .headers_mut()
            .insert("Origin", "http://127.0.0.1:5173".parse().unwrap());
        request
            .headers_mut()
            .insert("Cookie", test_cookie().parse().unwrap());
        request
    }

    #[tokio::test]
    async fn acquisition_status_is_read_only_and_strips_private_fields() {
        let instrument = quant_core::Instrument {
            venue: "NSE".into(),
            exchange_segment: "NSE_EQ".into(),
            security_id: "1333".into(),
            symbol: String::new(),
        };
        let shared =
            crate::read_model::TelemetryShared::new(&[instrument], "REPLAY", "test").unwrap();
        let empty = Gateway::new(Arc::clone(&shared), GatewayConfig::default()).unwrap();
        assert_eq!(
            acquisition(State(Arc::clone(&empty.state))).await.0["access_status"],
            "UNVERIFIED"
        );
        assert_eq!(
            acquisition(State(Arc::clone(&empty.state))).await.0["status"],
            "WAITING_FOR_REAL_SOURCE"
        );
        let gateway = Gateway::new(shared, GatewayConfig::default())
            .unwrap()
            .with_acquisition_status(serde_json::json!({
                "schema_version": "acquisition-status-v1",
                "instrument_count": 5,
                "source_verification_status": "UNVERIFIED",
                "training_blocked": true,
                "access_token": "must-not-leak",
            }))
            .unwrap();
        let value = acquisition(State(Arc::clone(&gateway.state))).await.0;
        assert_eq!(value["instrument_count"], 5);
        assert_eq!(value["source_verification_status"], "UNVERIFIED");
        assert_eq!(value["training_blocked"], true);
        assert!(value.get("access_token").is_none());
    }

    #[tokio::test]
    async fn research_endpoint_reports_only_precomputed_manifest() {
        let instrument = quant_core::Instrument {
            venue: "NSE".into(),
            exchange_segment: "NSE_EQ".into(),
            security_id: "1333".into(),
            symbol: String::new(),
        };
        let shared =
            crate::read_model::TelemetryShared::new(&[instrument], "REPLAY", "test").unwrap();
        let empty = Gateway::new(Arc::clone(&shared), GatewayConfig::default()).unwrap();
        assert_eq!(
            research(State(Arc::clone(&empty.state))).await.0["status"],
            "NOT_TRAINED"
        );
        assert!(
            Gateway::new(shared, GatewayConfig::default())
                .unwrap()
                .with_research_manifest(serde_json::json!({
                    "schema_version": "research-v1", "status": "NOT_TRAINED",
                    "source_kind": "SYNTHETIC", "independent_sessions": 1,
                }))
                .is_err()
        );
    }

    use super::*;
    use crate::read_model::TelemetryPublisher;
    use quant_core::{DepthEvent, DepthLevel, Instrument, MarketEvent};
    use quant_engine::pipeline::EngineOutput;
    use quant_journal::JournalRecord;

    #[tokio::test]
    async fn local_session_and_approval_require_auth_and_write_qp01() {
        use quant_paper::ledger::{LedgerEvent, PaperLedgerReader};

        let instrument = Instrument {
            venue: "NSE".into(),
            exchange_segment: "NSE_EQ".into(),
            security_id: "2885".into(),
            symbol: String::new(),
        };
        let shared = TelemetryShared::new(&[instrument], "DATA_ONLY", "no-feed").unwrap();
        let dir = tempfile::tempdir().unwrap();
        let ledger = dir.path().join("approvals.qp");
        let gateway = Gateway::new(Arc::clone(&shared), GatewayConfig::default())
            .unwrap()
            .with_local_session(TEST_SESSION.into())
            .unwrap()
            .with_approval_ledger(&ledger)
            .unwrap();
        let mut headers = HeaderMap::new();
        headers.insert(header::ORIGIN, "http://127.0.0.1:5173".parse().unwrap());
        assert_eq!(
            ui_session(State(Arc::clone(&gateway.state)), HeaderMap::new())
                .await
                .into_response()
                .status(),
            StatusCode::FORBIDDEN
        );
        let session = ui_session(State(Arc::clone(&gateway.state)), headers.clone())
            .await
            .into_response();
        assert_eq!(session.status(), StatusCode::FORBIDDEN);
        headers.insert(header::AUTHORIZATION, "Bearer wrong-key".parse().unwrap());
        assert_eq!(
            ui_session(State(Arc::clone(&gateway.state)), headers.clone())
                .await
                .into_response()
                .status(),
            StatusCode::FORBIDDEN
        );
        headers.insert(
            header::AUTHORIZATION,
            format!("Bearer {TEST_SESSION}").parse().unwrap(),
        );
        let session = ui_session(State(Arc::clone(&gateway.state)), headers.clone())
            .await
            .into_response();
        assert_eq!(session.status(), StatusCode::NO_CONTENT);
        assert!(
            session.headers()[header::SET_COOKIE]
                .to_str()
                .unwrap()
                .contains("HttpOnly; SameSite=Strict")
        );
        assert!(
            session.headers()[header::SET_COOKIE]
                .to_str()
                .unwrap()
                .contains("Max-Age=604800")
        );
        assert!(
            !session.headers()[header::SET_COOKIE]
                .to_str()
                .unwrap()
                .contains(TEST_SESSION)
        );
        let request = || ApprovalRequest {
            security_id: "2885".into(),
            approved: true,
        };
        assert_eq!(
            paper_approval(
                State(Arc::clone(&gateway.state)),
                headers.clone(),
                Json(request())
            )
            .await,
            StatusCode::FORBIDDEN
        );
        headers.insert(header::COOKIE, test_cookie().parse().unwrap());
        assert_eq!(
            paper_approval(
                State(Arc::clone(&gateway.state)),
                headers.clone(),
                Json(ApprovalRequest {
                    security_id: "9999".into(),
                    approved: true,
                })
            )
            .await,
            StatusCode::BAD_REQUEST
        );
        assert_eq!(
            paper_approval(
                State(Arc::clone(&gateway.state)),
                headers.clone(),
                Json(request())
            )
            .await,
            StatusCode::NO_CONTENT
        );
        let current = shared.snapshot(1, 5_000_000_000).unwrap();
        assert_eq!(current.approved_security_ids, ["2885"]);
        assert!(current.paper.is_none());
        assert!(
            current
                .no_trade_reasons
                .contains(&"NO_ELIGIBLE_DHAN_FEED".into())
        );
        let mut reader = PaperLedgerReader::open(&ledger).unwrap();
        assert!(matches!(
            reader.next_record().unwrap().unwrap().event,
            LedgerEvent::StockApproval {
                security_id,
                approved: true,
                ..
            } if security_id == "2885"
        ));
        assert_eq!(
            paper_approval(
                State(Arc::clone(&gateway.state)),
                headers,
                Json(ApprovalRequest {
                    security_id: "2885".into(),
                    approved: false
                })
            )
            .await,
            StatusCode::NO_CONTENT
        );
        assert!(
            shared
                .snapshot(2, 5_000_000_000)
                .unwrap()
                .approved_security_ids
                .is_empty()
        );
        drop(gateway);
        let recovered = Gateway::new(Arc::clone(&shared), GatewayConfig::default())
            .unwrap()
            .with_local_session(TEST_SESSION.into())
            .unwrap()
            .with_approval_ledger(&ledger)
            .unwrap();
        assert!(
            recovered
                .state
                .current
                .load()
                .approved_security_ids
                .is_empty()
        );
    }

    #[tokio::test]
    async fn signed_session_survives_gateway_restart_and_rejects_tampering() {
        let instrument = Instrument {
            venue: "NSE".into(),
            exchange_segment: "NSE_EQ".into(),
            security_id: "2885".into(),
            symbol: String::new(),
        };
        let shared = TelemetryShared::new(&[instrument], "DATA_ONLY", "no-feed").unwrap();
        let gateway = Gateway::new(Arc::clone(&shared), GatewayConfig::default())
            .unwrap()
            .with_local_session(TEST_SESSION.into())
            .unwrap();
        let restarted = Gateway::new(shared, GatewayConfig::default())
            .unwrap()
            .with_local_session(TEST_SESSION.into())
            .unwrap();
        let mut headers = HeaderMap::new();
        headers.insert(header::ORIGIN, "http://127.0.0.1:5173".parse().unwrap());
        headers.insert(
            header::AUTHORIZATION,
            format!("Bearer {TEST_SESSION}").parse().unwrap(),
        );
        let response = ui_session(State(Arc::clone(&gateway.state)), headers.clone())
            .await
            .into_response();
        let issued = response.headers()[header::SET_COOKIE]
            .to_str()
            .unwrap()
            .split(';')
            .next()
            .unwrap()
            .to_owned();
        headers.insert(header::COOKIE, issued.parse().unwrap());
        assert_eq!(
            ui_session_status(State(Arc::clone(&restarted.state)), headers.clone()).await,
            StatusCode::NO_CONTENT
        );
        let forged = issued.replacen("v1.", "v2.", 1);
        headers.insert(header::COOKIE, forged.parse().unwrap());
        assert_eq!(
            ui_session_status(State(Arc::clone(&restarted.state)), headers.clone()).await,
            StatusCode::UNAUTHORIZED
        );
        headers.insert(header::COOKIE, test_cookie().parse().unwrap());
        assert_eq!(
            ui_session_logout(headers.clone())
                .await
                .into_response()
                .status(),
            StatusCode::NO_CONTENT
        );
        let cleared = ui_session_logout(headers).await.into_response();
        assert!(
            cleared.headers()[header::SET_COOKIE]
                .to_str()
                .unwrap()
                .contains("Max-Age=0")
        );
    }

    #[tokio::test]
    async fn cached_history_is_authenticated_and_missing_data_stays_unavailable() {
        let instrument = Instrument {
            venue: "NSE".into(),
            exchange_segment: "NSE_EQ".into(),
            security_id: "2885".into(),
            symbol: String::new(),
        };
        let shared = TelemetryShared::new(&[instrument], "DATA_ONLY", "no-feed").unwrap();
        let mut gateway = Gateway::new(shared, GatewayConfig::default())
            .unwrap()
            .with_local_session(TEST_SESSION.into())
            .unwrap();
        assert!(
            gateway
                .with_historical_cache(serde_json::json!({
                    "schema_version": "dhan-historical-cache-v1", "status": "AVAILABLE",
                    "source": "SYNTHETIC_TEST", "bars": []
                }))
                .is_err()
        );
        let state = Arc::clone(&gateway.state);
        assert_eq!(
            historical_reliance(State(Arc::clone(&state)), HeaderMap::new())
                .await
                .unwrap_err(),
            StatusCode::UNAUTHORIZED
        );
        let mut headers = HeaderMap::new();
        headers.insert(header::COOKIE, test_cookie().parse().unwrap());
        let body = historical_reliance(State(state), headers).await.unwrap().0;
        assert_eq!(body["status"], "DATA_UNAVAILABLE");
        assert_eq!(body["bars"].as_array().unwrap().len(), 0);
    }

    #[tokio::test]
    async fn demo_stock_approval_is_bounded_to_known_paper_instruments() {
        let instrument = Instrument {
            venue: "NSE".into(),
            exchange_segment: "NSE_EQ".into(),
            security_id: "1333".into(),
            symbol: "RELIANCE".into(),
        };
        let shared = TelemetryShared::new(&[instrument], "REPLAY", "test").unwrap();
        let controls = DemoControls::new(Arc::clone(&shared));
        controls.paper_enabled.store(true, Ordering::Release);
        let gateway = Gateway::new(shared, GatewayConfig::default())
            .unwrap()
            .with_demo_controls(Arc::clone(&controls));
        let send = |security_id: &str, action: &str| ControlRequest {
            action: action.into(),
            speed: None,
            security_id: Some(security_id.into()),
        };
        assert_eq!(
            demo_control(
                State(Arc::clone(&gateway.state)),
                HeaderMap::new(),
                Json(send("9999", "approve_stock"))
            )
            .await
            .into_response()
            .status(),
            StatusCode::BAD_REQUEST
        );
        assert_eq!(
            demo_control(
                State(Arc::clone(&gateway.state)),
                HeaderMap::new(),
                Json(send("1333", "approve_stock"))
            )
            .await
            .into_response()
            .status(),
            StatusCode::NO_CONTENT
        );
        assert_eq!(
            controls.take_approval_changes(),
            vec![("1333".into(), true)]
        );
        assert_eq!(
            demo_control(
                State(Arc::clone(&gateway.state)),
                HeaderMap::new(),
                Json(send("1333", "approve_stock"))
            )
            .await
            .into_response()
            .status(),
            StatusCode::NO_CONTENT
        );
        assert!(controls.take_approval_changes().is_empty());
        assert_eq!(
            demo_control(
                State(Arc::clone(&gateway.state)),
                HeaderMap::new(),
                Json(send("1333", "revoke_stock"))
            )
            .await
            .into_response()
            .status(),
            StatusCode::NO_CONTENT
        );
        assert_eq!(
            controls.take_approval_changes(),
            vec![("1333".into(), false)]
        );
        assert_eq!(
            demo_control(
                State(Arc::clone(&gateway.state)),
                HeaderMap::new(),
                Json(ControlRequest {
                    action: "halt_paper".into(),
                    speed: None,
                    security_id: None
                })
            )
            .await
            .into_response()
            .status(),
            StatusCode::NO_CONTENT
        );
        assert!(controls.halt_requested.load(Ordering::Acquire));
    }

    #[tokio::test]
    async fn websocket_reconnect_gets_full_snapshot_and_updates_are_coalesced() {
        let instrument = Instrument {
            venue: "NSE".into(),
            exchange_segment: "NSE_EQ".into(),
            security_id: "1333".into(),
            symbol: "RELIANCE".into(),
        };
        let shared =
            TelemetryShared::new(std::slice::from_ref(&instrument), "REPLAY", "test").unwrap();
        let mut publisher =
            TelemetryPublisher::new(Arc::clone(&shared), std::slice::from_ref(&instrument));
        let gateway = Gateway::new(Arc::clone(&shared), GatewayConfig::default())
            .unwrap()
            .with_local_session(TEST_SESSION.into())
            .unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let (stop, stop_rx) = watch::channel(false);
        let server = tokio::spawn(gateway.serve(listener, stop_rx));
        let url = format!("ws://{address}/ws");
        assert!(tokio_tungstenite::connect_async(&url).await.is_err());
        let (mut socket, _) = tokio_tungstenite::connect_async(websocket_request(&url))
            .await
            .unwrap();
        let first = socket.next().await.unwrap().unwrap().into_text().unwrap();
        let initial: Envelope = serde_json::from_str(&first).unwrap();
        assert_eq!(initial.message_type, MessageType::EngineSnapshot);
        assert_eq!(initial.publication_sequence, initial.payload["generation"]);
        let output = EngineOutput {
            volume: None,
            finalized_candles: Vec::new(),
            provisional_candles: Vec::new(),
            features: None,
        };
        for n in 1..=3 {
            let record = JournalRecord {
                session_id: "test".into(),
                event: MarketEvent::Depth(Box::new(DepthEvent {
                    instrument: instrument.clone(),
                    exchange_timestamp_ns: None,
                    receive_timestamp_ns: now_ns() + n,
                    sequence: None,
                    bids: [DepthLevel {
                        price_nanos: n,
                        quantity: 1,
                        order_count: None,
                    }; 20],
                    asks: [DepthLevel {
                        price_nanos: n + 1,
                        quantity: 1,
                        order_count: None,
                    }; 20],
                })),
            };
            publisher.observe(&record, &output, true);
        }
        let update = tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                let text = socket.next().await.unwrap().unwrap().into_text().unwrap();
                let envelope: Envelope = serde_json::from_str(&text).unwrap();
                if envelope.message_type == MessageType::EngineUpdate {
                    break envelope;
                }
            }
        })
        .await
        .unwrap();
        assert_eq!(update.publication_sequence, update.payload["generation"]);
        assert_eq!(
            update.payload["instruments"][0]["depth"]["bids"][0]["price_nanos"],
            "3"
        );
        shared.reset_replay();
        let reset = tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                let text = socket.next().await.unwrap().unwrap().into_text().unwrap();
                let envelope: Envelope = serde_json::from_str(&text).unwrap();
                if envelope.message_type == MessageType::EngineSnapshot
                    && envelope.payload["replay_epoch"] == "1"
                {
                    break envelope;
                }
            }
        })
        .await
        .unwrap();
        assert_eq!(reset.publication_sequence, reset.payload["generation"]);
        assert!(reset.payload["instruments"][0]["depth"].is_null());
        drop(socket);
        let (mut reconnected, _) = tokio_tungstenite::connect_async(websocket_request(&url))
            .await
            .unwrap();
        let full: Envelope = serde_json::from_str(
            &reconnected
                .next()
                .await
                .unwrap()
                .unwrap()
                .into_text()
                .unwrap(),
        )
        .unwrap();
        assert_eq!(full.message_type, MessageType::EngineSnapshot);
        assert_eq!(full.publication_sequence, full.payload["generation"]);
        assert!(full.payload["instruments"][0]["depth"].is_null());
        stop.send(true).unwrap();
        server.await.unwrap().unwrap();
    }

    #[tokio::test]
    async fn scoped_quote_reaches_local_websocket_only_after_qj01_sync() {
        use quant_engine::bars::BarConfig;
        use quant_engine::capture::StandardCapture;
        use quant_engine::features::FeatureConfig;
        use quant_engine::pipeline::MarketEngine;
        use quant_engine::{MarketCalendar, TimestampPolicy};
        use quant_journal::{DurabilityPolicy, JournalReader, JournalWriter};

        struct FixtureCalendar;
        impl MarketCalendar for FixtureCalendar {
            fn session_at(&self, _: i64) -> Option<String> {
                Some("fixture".into())
            }
        }
        let instrument = Instrument {
            venue: "NSE".into(),
            exchange_segment: "NSE_EQ".into(),
            security_id: "2885".into(),
            symbol: "RELIANCE".into(),
        };
        let shared = TelemetryShared::new(
            std::slice::from_ref(&instrument),
            "OBSERVE_ONLY",
            "fixture-session",
        )
        .unwrap();
        let mut publisher =
            TelemetryPublisher::new(Arc::clone(&shared), std::slice::from_ref(&instrument));
        let gateway = Gateway::new(Arc::clone(&shared), GatewayConfig::default())
            .unwrap()
            .with_local_session(TEST_SESSION.into())
            .unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let (stop, receiver) = watch::channel(false);
        let server = tokio::spawn(gateway.serve(listener, receiver));
        let (mut socket, _) =
            tokio_tungstenite::connect_async(websocket_request(&format!("ws://{address}/ws")))
                .await
                .unwrap();
        let first: Envelope =
            serde_json::from_str(&socket.next().await.unwrap().unwrap().into_text().unwrap())
                .unwrap();
        assert_eq!(first.message_type, MessageType::EngineSnapshot);
        assert_eq!(first.payload["mode"], "OBSERVE_ONLY");
        assert_eq!(first.payload["feed_connected"], false);
        assert!(first.payload["paper"].is_null());

        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("fixture.qj");
        let mut writer = JournalWriter::open(&path, DurabilityPolicy::EveryEvent).unwrap();
        let mut capture = StandardCapture::for_instrument(instrument);
        let mut engine = MarketEngine::new(
            Box::new(FixtureCalendar),
            BarConfig {
                timestamp_policy: TimestampPolicy::Receive,
                max_lateness_ns: 0,
                max_open_bars_per_instrument: 10,
            },
            FeatureConfig {
                max_cross_feed_age_ns: 1_000_000_000,
                stale_after_ns: 5_000_000_000,
                rolling_window: 8,
            },
        )
        .unwrap();
        let mut packet = vec![4];
        packet.extend(50_u16.to_le_bytes());
        packet.push(1);
        packet.extend(2885_i32.to_le_bytes());
        packet.extend(100.0_f32.to_le_bytes());
        packet.extend(5_i16.to_le_bytes());
        packet.extend(1_700_000_000_i32.to_le_bytes());
        packet.extend(100.0_f32.to_le_bytes());
        packet.extend(500_i32.to_le_bytes());
        packet.extend(10_i32.to_le_bytes());
        packet.extend(10_i32.to_le_bytes());
        for price in [100.0_f32, 0.0, 101.0, 99.0] {
            packet.extend(price.to_le_bytes());
        }
        shared.start_observation_feed("fixture-session").unwrap();
        assert_eq!(
            publisher
                .ingest_standard_frame(
                    &mut capture,
                    &packet,
                    now_ns(),
                    "fixture-session",
                    &mut writer,
                    &mut engine
                )
                .unwrap(),
            1
        );
        let committed_at = std::time::Instant::now();
        let mut reader = JournalReader::open(&path).unwrap();
        assert!(matches!(
            reader.next_record().unwrap().unwrap().event,
            MarketEvent::RawStandardFrame(_)
        ));
        assert!(matches!(
            reader.next_record().unwrap().unwrap().event,
            MarketEvent::Quote(_)
        ));
        let update = tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                let text = socket.next().await.unwrap().unwrap().into_text().unwrap();
                let item: Envelope = serde_json::from_str(&text).unwrap();
                if item.message_type == MessageType::EngineUpdate
                    && item.payload["instruments"]
                        .as_array()
                        .is_some_and(|items| !items.is_empty())
                {
                    break item;
                }
            }
        })
        .await
        .unwrap();
        let publication_ms = committed_at.elapsed().as_millis();
        eprintln!("fixture QJ01-commit-to-WebSocket update: {publication_ms} ms");
        assert!(
            publication_ms < 2_000,
            "fixture update exceeded two seconds: {publication_ms} ms"
        );
        assert_eq!(
            update.payload["instruments"][0]["market"]["price_nanos"],
            "100000000000"
        );
        assert_eq!(
            update.payload["instruments"][0]["market"]["cumulative_traded_volume"],
            "500"
        );
        assert_eq!(
            update.payload["instruments"][0]["market"]["provenance"]["source_protocol"],
            "DhanStandardV2"
        );
        for reason in [
            "OBSERVATION_ONLY",
            "SESSION_CLASSIFICATION_UNVERIFIED",
            "NO_ELIGIBLE_DHAN_FEED",
        ] {
            assert!(
                update.payload["status"]["no_trade_reasons"]
                    .as_array()
                    .unwrap()
                    .contains(&serde_json::json!(reason))
            );
        }
        assert!(update.payload["instruments"][0]["features"].is_null());
        assert!(
            update.payload["instruments"][0]["candles"]
                .as_array()
                .unwrap()
                .is_empty()
        );
        assert!(
            update.payload["status"]["no_trade_reasons"]
                .as_array()
                .unwrap()
                .contains(&serde_json::json!("SOURCE_TIME_UNVERIFIED"))
        );
        assert!(update.payload["status"]["paper"].is_null());
        assert!(
            update.payload["status"]["no_trade_reasons"]
                .as_array()
                .unwrap()
                .contains(&serde_json::json!("NO_VALIDATED_PAPER_DECISIONS"))
        );
        shared.stop_feed();
        let disconnected = tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                let text = socket.next().await.unwrap().unwrap().into_text().unwrap();
                let item: Envelope = serde_json::from_str(&text).unwrap();
                if item.message_type == MessageType::EngineUpdate
                    && item.payload["status"]["feed_connected"] == false
                {
                    break item;
                }
            }
        })
        .await
        .unwrap();
        assert!(
            disconnected.payload["status"]["no_trade_reasons"]
                .as_array()
                .unwrap()
                .contains(&serde_json::json!("NO_ELIGIBLE_DHAN_FEED"))
        );
        writer.close().unwrap();
        drop(socket);
        stop.send(true).unwrap();
        server.await.unwrap().unwrap();
    }

    #[tokio::test]
    async fn client_limit_rejects_excess_connection_and_cleans_up() {
        let instrument = Instrument {
            venue: "NSE".into(),
            exchange_segment: "NSE_EQ".into(),
            security_id: "1333".into(),
            symbol: String::new(),
        };
        let shared = TelemetryShared::new(&[instrument], "REPLAY", "test").unwrap();
        let gateway = Gateway::new(
            Arc::clone(&shared),
            GatewayConfig {
                max_clients: 1,
                ..GatewayConfig::default()
            },
        )
        .unwrap()
        .with_local_session(TEST_SESSION.into())
        .unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let (stop, stop_rx) = watch::channel(false);
        let server = tokio::spawn(gateway.serve(listener, stop_rx));
        let url = format!("ws://{address}/ws");
        let (first, _) = tokio_tungstenite::connect_async(websocket_request(&url))
            .await
            .unwrap();
        assert!(
            tokio_tungstenite::connect_async(websocket_request(&url))
                .await
                .is_err()
        );
        assert_eq!(shared.connected_clients.load(Ordering::Acquire), 1);
        drop(first);
        tokio::time::timeout(Duration::from_secs(2), async {
            while shared.connected_clients.load(Ordering::Acquire) != 0 {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .unwrap();
        stop.send(true).unwrap();
        server.await.unwrap().unwrap();
    }
}
