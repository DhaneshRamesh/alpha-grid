//! Local read-only HTTP and bounded latest-value WebSocket telemetry.
use crate::protocol::{
    Envelope, MessageType, WIRE_VERSION, WireEngineSnapshot, WireEngineSnapshotStatus,
    WireEngineUpdate,
};
use crate::read_model::{TelemetryShared, now_ns};
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::IntoResponse;
use axum::routing::get;
use axum::{Json, Router};
use futures_util::StreamExt;
use std::sync::Arc;
use std::sync::atomic::Ordering;
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
}

pub struct Gateway {
    state: Arc<AppState>,
}
impl Gateway {
    pub fn new(shared: Arc<TelemetryShared>, config: GatewayConfig) -> Result<Self, &'static str> {
        let config = config.validate()?;
        let first = Arc::new(shared.snapshot(0, config.stale_after_ns));
        let (updates, _) = watch::channel(Arc::clone(&first));
        Ok(Self {
            state: Arc::new(AppState {
                shared,
                current: arc_swap::ArcSwap::from(first),
                updates,
                config,
            }),
        })
    }

    pub fn router(&self) -> Router {
        Router::new()
            .route("/api/snapshot", get(snapshot))
            .route("/api/health", get(health))
            .route("/api/instruments/{key}", get(instrument))
            .route("/ws", get(websocket))
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
            loop {
                tokio::select! {
                    _ = tick.tick() => {
                        let counts = state.shared.counters.load();
                        let total = counts.journal_records_accepted + counts.replayed_records;
                        if total > observed_events + 1 {
                            state.shared.coalesced_updates.fetch_add(total - observed_events - 1, Ordering::Relaxed);
                        }
                        observed_events = total;
                        let previous = state.current.load_full();
                        let candidate = state.shared.snapshot(generation + 1, state.config.stale_after_ns);
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

fn envelope(
    state: &AppState,
    kind: MessageType,
    payload: serde_json::Value,
    source: Option<String>,
) -> Envelope {
    let current = state.current.load();
    Envelope {
        schema_version: WIRE_VERSION,
        message_type: kind,
        instrument_key: None,
        engine_boot_id: state.shared.boot_id.clone(),
        publication_sequence: current.generation.clone(),
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
    ))
}

async fn health(State(state): State<Arc<AppState>>) -> Json<WireEngineSnapshotStatus> {
    Json((&*state.current.load_full()).into())
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
                let (kind, payload) = if new_generation != old_generation + 1 {
                    state.shared.skipped_updates.fetch_add(new_generation.saturating_sub(old_generation + 1), Ordering::Relaxed);
                    (MessageType::EngineSnapshot, serde_json::to_value(&*next))
                } else {
                    let instruments = next.instruments.iter().filter(|v| !previous.instruments.contains(v)).cloned().collect();
                    let update = WireEngineUpdate { generation: next.generation.clone(), status: (&*next).into(), instruments, audit: next.audit.clone() };
                    (MessageType::EngineUpdate, serde_json::to_value(update))
                };
                let Ok(payload) = payload else { break; };
                if !send(&mut socket, envelope(&state, kind, payload, next.latest_data_timestamp_ns.clone())).await { break; }
                previous = next;
            }
            _ = heartbeat.tick() => {
                if !send(&mut socket, envelope(&state, MessageType::Heartbeat, serde_json::json!({}), None)).await { break; }
            }
            received = socket.next() => {
                match received { Some(Ok(Message::Close(_))) | None | Some(Err(_)) => break, _ => {} }
            }
        }
    }
}

#[cfg(test)]
mod tests {
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
        assert_eq!(
            update.payload["instruments"][0]["depth"]["bids"][0]["price_nanos"],
            "3"
        );
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
        assert_eq!(
            full.payload["instruments"][0]["depth"]["bids"][0]["price_nanos"],
            "3"
        );
        stop.send(true).unwrap();
        server.await.unwrap().unwrap();
    }
}
