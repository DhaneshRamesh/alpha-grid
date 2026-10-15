//! Local read-only HTTP and bounded latest-value WebSocket telemetry.
use crate::protocol::{
    Envelope, LatencySummary, MessageType, WIRE_VERSION, WireEngineSnapshot,
    WireEngineSnapshotStatus, WireEngineUpdate,
};
use crate::read_model::{TelemetryShared, now_ns};
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use futures_util::StreamExt;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;
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
}

pub struct DemoControls {
    pub shared: Arc<TelemetryShared>,
    pub restart_requested: AtomicU64,
}
impl DemoControls {
    pub fn new(shared: Arc<TelemetryShared>) -> Arc<Self> {
        Arc::new(Self {
            shared,
            restart_requested: AtomicU64::new(0),
        })
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
            }),
        })
    }

    pub fn with_demo_controls(mut self, controls: Arc<DemoControls>) -> Self {
        Arc::get_mut(&mut self.state)
            .expect("unshared gateway")
            .demo = Some(controls);
        self
    }

    /// Load a previously produced offline manifest. No analysis runs in the gateway.
    pub fn with_research_manifest(
        mut self,
        manifest: serde_json::Value,
    ) -> Result<Self, &'static str> {
        if manifest.get("schema_version").and_then(|v| v.as_str()) != Some("research-v1")
            || manifest.get("status").and_then(|v| v.as_str()).is_none()
        {
            return Err("invalid research manifest");
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

    pub fn router(&self) -> Router {
        Router::new()
            .route("/api/snapshot", get(snapshot))
            .route("/api/health", get(health))
            .route("/api/paper", get(paper))
            .route("/api/research", get(research))
            .route("/api/acquisition", get(acquisition))
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

async fn snapshot(State(state): State<Arc<AppState>>) -> Json<Envelope> {
    let current = state.current.load_full();
    Json(envelope(
        &state,
        MessageType::EngineSnapshot,
        serde_json::to_value(&*current).expect("snapshot JSON"),
        current.latest_data_timestamp_ns.clone(),
        &current.generation,
    ))
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
    if let Some(origin) = headers.get("origin") {
        let allowed = matches!(
            origin.to_str(),
            Ok("http://127.0.0.1:5173"
                | "http://localhost:5173"
                | "http://127.0.0.1:8765"
                | "http://localhost:8765")
        );
        if !allowed {
            return StatusCode::FORBIDDEN.into_response();
        }
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
        let gateway = Gateway::new(shared, GatewayConfig::default())
            .unwrap()
            .with_research_manifest(serde_json::json!({
                "schema_version": "research-v1", "status": "NOT_TRAINED",
                "source_kind": "SYNTHETIC", "independent_sessions": 1,
            }))
            .unwrap();
        let value = research(State(Arc::clone(&gateway.state))).await.0;
        assert_eq!(value["source_kind"], "SYNTHETIC");
        assert_eq!(value["independent_sessions"], 1);
    }

    use super::*;
    use crate::read_model::TelemetryPublisher;
    use quant_core::{DepthEvent, DepthLevel, Instrument, MarketEvent};
    use quant_engine::pipeline::EngineOutput;
    use quant_journal::JournalRecord;

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
        let gateway = Gateway::new(Arc::clone(&shared), GatewayConfig::default()).unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let (stop, stop_rx) = watch::channel(false);
        let server = tokio::spawn(gateway.serve(listener, stop_rx));
        let url = format!("ws://{address}/ws");
        let (mut socket, _) = tokio_tungstenite::connect_async(&url).await.unwrap();
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
        let (mut reconnected, _) = tokio_tungstenite::connect_async(&url).await.unwrap();
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
        .unwrap();
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let (stop, stop_rx) = watch::channel(false);
        let server = tokio::spawn(gateway.serve(listener, stop_rx));
        let url = format!("ws://{address}/ws");
        let (first, _) = tokio_tungstenite::connect_async(&url).await.unwrap();
        assert!(tokio_tungstenite::connect_async(&url).await.is_err());
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
