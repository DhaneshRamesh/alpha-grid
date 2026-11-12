//! Read-only WebSocket transport. The only outbound application message is the
//! documented depth subscription; live connectivity is feature/runtime gated.
use crate::capture::Capture;
use futures_util::{SinkExt, StreamExt};
use quant_core::DepthEvent;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::future::Future;
use std::io;
use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncRead, AsyncWrite};
use tokio::sync::watch;
#[cfg(feature = "live-readonly")]
use tokio_tungstenite::tungstenite::Error as WebSocketError;
use tokio_tungstenite::{WebSocketStream, tungstenite::Message};

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub struct Subscription {
    pub exchange_segment: String,
    #[serde(rename = "SecurityId")]
    pub security_id: String,
}

#[derive(Clone, Debug)]
pub struct TransportConfig {
    pub subscriptions: Vec<Subscription>,
    pub max_reconnects: u32,
    pub base_backoff: Duration,
    pub max_backoff: Duration,
    pub idle_timeout: Duration,
}

impl TransportConfig {
    pub fn validate(&self) -> io::Result<()> {
        if self.subscriptions.is_empty()
            || self.subscriptions.len() > 50
            || self.base_backoff.is_zero()
            || self.max_backoff < self.base_backoff
            || self.idle_timeout.is_zero()
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid depth transport limits",
            ));
        }
        let mut unique = std::collections::BTreeSet::new();
        for item in &self.subscriptions {
            if !matches!(item.exchange_segment.as_str(), "NSE_EQ" | "NSE_FNO")
                || !item.security_id.parse::<i32>().is_ok_and(|id| id > 0)
                || !unique.insert((item.exchange_segment.as_str(), item.security_id.as_str()))
            {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "invalid or duplicate depth subscription",
                ));
            }
        }
        Ok(())
    }

    pub fn subscription_json(&self) -> io::Result<String> {
        self.validate()?;
        #[derive(Serialize)]
        #[serde(rename_all = "PascalCase")]
        struct Request<'a> {
            request_code: u8,
            instrument_count: usize,
            instrument_list: &'a [Subscription],
        }
        serde_json::to_string(&Request {
            request_code: 23,
            instrument_count: self.subscriptions.len(),
            instrument_list: &self.subscriptions,
        })
        .map_err(io::Error::other)
    }
}

fn unix_ns() -> io::Result<i64> {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(io::Error::other)?
        .as_nanos();
    nanos
        .try_into()
        .map_err(|_| io::Error::other("clock overflow"))
}

fn write_local_status(connected: bool, last_frame_ns: Option<i64>, frames: u64) -> io::Result<()> {
    let Ok(path) = std::env::var("ALPHA_GRID_DEPTH_STATUS_FILE") else {
        return Ok(());
    };
    let target = Path::new(&path);
    let temporary = target.with_extension(format!("{}.tmp", std::process::id()));
    let body = serde_json::json!({
        "schema_version": "alpha-grid-depth-status-v1",
        "connected": connected,
        "last_frame_received_ns": last_frame_ns.map(|value| value.to_string()),
        "frames_received": frames,
        "checked_at_ns": unix_ns()?,
    });
    std::fs::write(&temporary, serde_json::to_vec(&body)?)?;
    std::fs::rename(temporary, target)?;
    Ok(())
}

fn write_local_books(books: &BTreeMap<String, DepthEvent>) -> io::Result<()> {
    let Ok(path) = std::env::var("ALPHA_GRID_DEPTH_BOOKS_FILE") else {
        return Ok(());
    };
    if books.is_empty() {
        return Ok(());
    }
    let target = Path::new(&path);
    let temporary = target.with_extension(format!("books_{}.tmp", std::process::id()));
    let wire_books: Vec<serde_json::Value> = books
        .values()
        .map(|book| {
            let mut cumulative_bid = 0_u64;
            let bids: Vec<serde_json::Value> = book
                .bids
                .iter()
                .map(|l| {
                    cumulative_bid = cumulative_bid.saturating_add(l.quantity);
                    serde_json::json!({
                        "price_nanos": l.price_nanos.to_string(),
                        "displayed_quantity": l.quantity.to_string(),
                        "order_count": l.order_count,
                        "cumulative_visible_quantity": cumulative_bid.to_string(),
                    })
                })
                .collect();
            let mut cumulative_ask = 0_u64;
            let asks: Vec<serde_json::Value> = book
                .asks
                .iter()
                .map(|l| {
                    cumulative_ask = cumulative_ask.saturating_add(l.quantity);
                    serde_json::json!({
                        "price_nanos": l.price_nanos.to_string(),
                        "displayed_quantity": l.quantity.to_string(),
                        "order_count": l.order_count,
                        "cumulative_visible_quantity": cumulative_ask.to_string(),
                    })
                })
                .collect();
            serde_json::json!({
                "security_id": book.instrument.security_id,
                "exchange_segment": book.instrument.exchange_segment,
                "symbol": book.instrument.symbol,
                "receive_timestamp_ns": book.receive_timestamp_ns.to_string(),
                "exchange_timestamp_ns": book.exchange_timestamp_ns.map(|n| n.to_string()),
                "derived_pairing": true,
                "atomic_exchange_snapshot_verified": false,
                "source": "DEPTH_20",
                "bids": bids,
                "asks": asks,
            })
        })
        .collect();

    let body = serde_json::json!({
        "schema_version": "alpha-grid-depth-books-v1",
        "checked_at_ns": unix_ns()?,
        "book_count": books.len(),
        "books": wire_books,
    });
    std::fs::write(&temporary, serde_json::to_vec(&body)?)?;
    std::fs::rename(temporary, target)?;
    Ok(())
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SessionExit {
    Disconnected,
    Shutdown,
}

pub async fn run_socket<S>(
    mut socket: WebSocketStream<S>,
    capture: &mut Capture,
    config: &TransportConfig,
    stop: &mut watch::Receiver<bool>,
) -> io::Result<SessionExit>
where
    S: AsyncRead + AsyncWrite + Unpin,
{
    let subscribe = config.subscription_json()?;
    socket
        .send(Message::Text(subscribe.into()))
        .await
        .map_err(|_| io::Error::other("depth subscription send failed"))?;
    capture.metrics.connected = true;
    write_local_status(true, None, capture.metrics.websocket_frames)?;
    let mut last_report = std::time::Instant::now() - Duration::from_secs(2);
    let mut last_frame_ns = None;
    loop {
        tokio::select! {
            _ = tokio::time::sleep(Duration::from_secs(5)) => {
                write_local_status(true, last_frame_ns, capture.metrics.websocket_frames)?;
            }
            changed = stop.changed() => {
                if changed.is_err() || *stop.borrow() {
                    let _ = socket.close(None).await;
                    capture.metrics.connected = false;
                    write_local_status(false, None, capture.metrics.websocket_frames)?;
                    return Ok(SessionExit::Shutdown);
                }
            }
            incoming = tokio::time::timeout(config.idle_timeout, socket.next()) => {
                match incoming {
                    Ok(Some(Ok(Message::Binary(bytes)))) => {
                        // Capture wall time immediately after delivery, before parsing or fsync.
                        let received_ns = unix_ns()?;
                        if capture.ingest_frame(&bytes, received_ns).await? {
                            capture.metrics.connected = false;
                            write_local_status(false, None, capture.metrics.websocket_frames)?;
                            return Ok(SessionExit::Disconnected);
                        }
                        last_frame_ns = Some(received_ns);
                        if last_report.elapsed() >= Duration::from_secs(1) {
                            write_local_status(true, Some(received_ns), capture.metrics.websocket_frames)?;
                            let _ = write_local_books(capture.all_latest_books());
                            last_report = std::time::Instant::now();
                        }
                    }
                    Ok(Some(Ok(Message::Ping(payload)))) => {
                        socket.send(Message::Pong(payload)).await
                            .map_err(|_| io::Error::other("depth pong send failed"))?;
                    }
                    Ok(Some(Ok(Message::Close(_)))) | Ok(None) | Ok(Some(Err(_))) | Err(_) => {
                        capture.metrics.connected = false;
                        write_local_status(false, None, capture.metrics.websocket_frames)?;
                        return Ok(SessionExit::Disconnected);
                    }
                    Ok(Some(Ok(Message::Text(_)))) => {
                        capture.metrics.subscription_failures += 1;
                        return Err(io::Error::new(io::ErrorKind::InvalidData, "unexpected text response from binary depth feed"));
                    }
                    Ok(Some(Ok(_))) => {}
                }
            }
        }
    }
}

pub async fn run_reconnecting<F, Fut, S>(
    mut connect: F,
    capture: &mut Capture,
    config: &TransportConfig,
    stop: &mut watch::Receiver<bool>,
) -> io::Result<()>
where
    F: FnMut() -> Fut,
    Fut: Future<Output = io::Result<WebSocketStream<S>>>,
    S: AsyncRead + AsyncWrite + Unpin,
{
    config.validate()?;
    let mut last_connect_error = None;
    for attempt in 0..=config.max_reconnects {
        if *stop.borrow() {
            return Ok(());
        }
        capture.metrics.invalidate_session();
        capture.new_feed_session(format!("feed-{}-{attempt}", unix_ns()?));
        let outcome = match connect().await {
            Ok(socket) => run_socket(socket, capture, config, stop).await?,
            Err(error) => {
                last_connect_error = Some(error);
                SessionExit::Disconnected
            }
        };
        if outcome == SessionExit::Shutdown {
            return Ok(());
        }
        capture.metrics.connected = false;
        if attempt == config.max_reconnects {
            break;
        }
        capture.metrics.reconnects += 1;
        let shift = attempt.min(20);
        let base = config
            .base_backoff
            .saturating_mul(1 << shift)
            .min(config.max_backoff);
        let jitter = Duration::from_millis((attempt as u64 * 37 + 11) % 97);
        tokio::select! {
            _ = tokio::time::sleep(base.saturating_add(jitter).min(config.max_backoff)) => {},
            _ = stop.changed() => { if *stop.borrow() { return Ok(()); } }
        }
    }
    Err(last_connect_error.unwrap_or_else(|| {
        io::Error::new(
            io::ErrorKind::ConnectionAborted,
            "DEPTH_SOCKET_DISCONNECTED",
        )
    }))
}

/// Requires `--features live-readonly`, explicit runtime opt-in, and local env.
/// Never prints the credential-bearing URL or upstream error strings.
#[cfg(feature = "live-readonly")]
pub async fn run_live(
    capture: &mut Capture,
    config: &TransportConfig,
    stop: &mut watch::Receiver<bool>,
) -> io::Result<()> {
    if std::env::var("QUANT_ENABLE_LIVE_READONLY").as_deref() != Ok("1") {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "live depth mode is disabled",
        ));
    }
    let token = std::env::var("DHAN_ACCESS_TOKEN")
        .map_err(|_| io::Error::new(io::ErrorKind::PermissionDenied, "missing local Dhan token"))?;
    let client = std::env::var("DHAN_CLIENT_ID").map_err(|_| {
        io::Error::new(
            io::ErrorKind::PermissionDenied,
            "missing local Dhan client ID",
        )
    })?;
    if token.is_empty()
        || !token
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
        || client.is_empty()
        || !client.bytes().all(|b| b.is_ascii_digit())
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "invalid local credential format",
        ));
    }
    let url = format!(
        "wss://depth-api-feed.dhan.co/twentydepth?token={token}&clientId={client}&authType=2"
    );
    run_reconnecting(
        || {
            let url = url.clone();
            async move {
                tokio_tungstenite::connect_async(url.as_str())
                    .await
                    .map(|(socket, _)| socket)
                    .map_err(|error| match error {
                        WebSocketError::Http(response) => io::Error::other(format!(
                            "DEPTH_UPGRADE_HTTP_{}",
                            response.status().as_u16()
                        )),
                        _ => io::Error::other("DEPTH_CONNECT_FAILED"),
                    })
            }
        },
        capture,
        config,
        stop,
    )
    .await
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::capture::Capture;
    use quant_journal::{
        DurabilityPolicy,
        segmented::{SegmentConfig, replay_segments},
    };
    use tokio::net::{TcpListener, TcpStream};

    fn config() -> TransportConfig {
        TransportConfig {
            subscriptions: vec![Subscription {
                exchange_segment: "NSE_EQ".into(),
                security_id: "1333".into(),
            }],
            max_reconnects: 1,
            base_backoff: Duration::from_millis(1),
            max_backoff: Duration::from_millis(10),
            idle_timeout: Duration::from_secs(2),
        }
    }
    fn side(code: u8) -> Vec<u8> {
        let mut p = Vec::new();
        p.extend_from_slice(&332_i16.to_le_bytes());
        p.extend([code, 1]);
        p.extend_from_slice(&1333_i32.to_le_bytes());
        p.extend_from_slice(&0_u32.to_le_bytes());
        for i in 0..20 {
            p.extend_from_slice(&(100.0 + i as f64).to_le_bytes());
            p.extend_from_slice(&10_u32.to_le_bytes());
            p.extend_from_slice(&1_u32.to_le_bytes());
        }
        p
    }
    #[test]
    fn subscription_shape_and_limits() {
        let v: serde_json::Value =
            serde_json::from_str(&config().subscription_json().unwrap()).unwrap();
        assert_eq!(v["RequestCode"], 23);
        assert_eq!(v["InstrumentCount"], 1);
        assert_eq!(v["InstrumentList"][0]["SecurityId"], "1333");
        let mut many = config();
        many.subscriptions = vec![many.subscriptions[0].clone(); 51];
        assert!(many.validate().is_err());
        let mut fifty = config();
        fifty.subscriptions = (1..=50)
            .map(|id| Subscription {
                exchange_segment: "NSE_EQ".into(),
                security_id: id.to_string(),
            })
            .collect();
        assert!(fifty.validate().is_ok());
        let mut duplicate = config();
        duplicate
            .subscriptions
            .push(duplicate.subscriptions[0].clone());
        assert!(duplicate.validate().is_err());
        let mut invalid = config();
        invalid.subscriptions[0].exchange_segment = "BSE_EQ".into();
        assert!(invalid.validate().is_err());
        invalid.subscriptions[0].exchange_segment = "NSE_EQ".into();
        invalid.subscriptions[0].security_id = "-1".into();
        assert!(invalid.validate().is_err());
    }
    #[tokio::test]
    async fn mock_ping_disconnect_and_resubscribe() {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            for code in [41, 51] {
                let (tcp, _) = listener.accept().await.unwrap();
                let mut ws = tokio_tungstenite::accept_async(tcp).await.unwrap();
                let subscribed = ws.next().await.unwrap().unwrap();
                assert!(subscribed.to_text().unwrap().contains("\"RequestCode\":23"));
                ws.send(Message::Ping(vec![1, 2].into())).await.unwrap();
                assert!(matches!(
                    ws.next().await.unwrap().unwrap(),
                    Message::Pong(_)
                ));
                ws.send(Message::Binary(side(code).into())).await.unwrap();
                ws.close(None).await.unwrap();
            }
        });
        let dir = tempfile::tempdir().unwrap();
        let journal_config = SegmentConfig {
            max_bytes: 10_000,
            max_age: Duration::from_secs(60),
            max_disk_bytes: 1_000_000,
            min_free_bytes: 0,
            durability: DurabilityPolicy::EveryEvent,
        };
        let mut capture = Capture::open(dir.path(), journal_config, 2, "mock".into()).unwrap();
        let (_stop_tx, mut stop_rx) = watch::channel(false);
        let result = run_reconnecting(
            || async {
                let tcp = TcpStream::connect(address).await?;
                tokio_tungstenite::client_async(format!("ws://{address}"), tcp)
                    .await
                    .map(|(socket, _)| socket)
                    .map_err(|_| io::Error::other("mock handshake failed"))
            },
            &mut capture,
            &config(),
            &mut stop_rx,
        )
        .await;
        assert_eq!(result.unwrap_err().kind(), io::ErrorKind::ConnectionAborted);
        server.await.unwrap();
        assert_eq!(capture.metrics.reconnects, 1);
        assert_eq!(capture.metrics.bid_packets, 1);
        assert_eq!(capture.metrics.ask_packets, 1);
        assert_eq!(capture.metrics.complete_books, 0); // Never pair across sessions.
        assert!(
            capture.metrics.per_instrument["NSE_EQ:1333"]
                .last_bid
                .is_none()
        );
        capture.close().await.unwrap();
        let recovered = replay_segments(dir.path()).unwrap();
        assert_eq!(recovered.len(), 4); // Each side has its original raw frame.
        assert_eq!(
            recovered
                .iter()
                .filter(|record| matches!(record.event, quant_core::MarketEvent::RawDepthFrame(_)))
                .count(),
            2
        );
    }

    #[tokio::test]
    async fn bounded_connect_preserves_sanitized_http_failure() {
        let dir = tempfile::tempdir().unwrap();
        let journal_config = SegmentConfig {
            max_bytes: 10_000,
            max_age: Duration::from_secs(60),
            max_disk_bytes: 1_000_000,
            min_free_bytes: 0,
            durability: DurabilityPolicy::EveryEvent,
        };
        let mut capture = Capture::open(dir.path(), journal_config, 2, "fixture".into()).unwrap();
        let (_stop_tx, mut stop_rx) = watch::channel(false);
        let mut bounded = config();
        bounded.max_reconnects = 0;
        let result = run_reconnecting(
            || async {
                Err::<WebSocketStream<TcpStream>, _>(io::Error::other("DEPTH_UPGRADE_HTTP_403"))
            },
            &mut capture,
            &bounded,
            &mut stop_rx,
        )
        .await;
        assert_eq!(result.unwrap_err().to_string(), "DEPTH_UPGRADE_HTTP_403");
    }
}
