//! Read-only WebSocket transport. The only outbound application message is the
//! documented depth subscription; live connectivity is feature/runtime gated.
use crate::capture::Capture;
use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use std::future::Future;
use std::io;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncRead, AsyncWrite};
use tokio::sync::watch;
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
    loop {
        tokio::select! {
            changed = stop.changed() => {
                if changed.is_err() || *stop.borrow() {
                    let _ = socket.close(None).await;
                    capture.metrics.connected = false;
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
                            return Ok(SessionExit::Disconnected);
                        }
                    }
                    Ok(Some(Ok(Message::Ping(payload)))) => {
                        socket.send(Message::Pong(payload)).await
                            .map_err(|_| io::Error::other("depth pong send failed"))?;
                    }
                    Ok(Some(Ok(Message::Close(_)))) | Ok(None) | Ok(Some(Err(_))) | Err(_) => {
                        capture.metrics.connected = false;
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
    for attempt in 0..=config.max_reconnects {
        if *stop.borrow() {
            return Ok(());
        }
        capture.metrics.invalidate_session();
        capture.session_id = format!("feed-{}-{attempt}", unix_ns()?);
        let outcome = match connect().await {
            Ok(socket) => run_socket(socket, capture, config, stop).await?,
            Err(_) => SessionExit::Disconnected,
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
    Err(io::Error::new(
        io::ErrorKind::ConnectionAborted,
        "depth reconnect limit reached",
    ))
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
                    .map_err(|_| io::Error::other("depth connection failed"))
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
        assert_eq!(replay_segments(dir.path()).unwrap().len(), 2);
    }
}
