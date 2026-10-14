use futures_util::{SinkExt, StreamExt};
use quant_depth::capture::Capture;
use quant_depth::health::HealthThresholds;
use quant_depth::transport::{Subscription, TransportConfig, run_reconnecting};
use quant_journal::DurabilityPolicy;
use quant_journal::segmented::{SegmentConfig, SegmentedJournal, count_recovered_records};
use quant_market_data::dhan20::{assemble_frame, decode_frame};
use std::io;
use std::path::Path;
use std::time::Duration;
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::watch;
use tokio_tungstenite::tungstenite::Message;

fn fixture_side(code: u8) -> Vec<u8> {
    let mut bytes = Vec::with_capacity(332);
    bytes.extend_from_slice(&332_i16.to_le_bytes());
    bytes.extend([code, 1]);
    bytes.extend_from_slice(&1333_i32.to_le_bytes());
    bytes.extend_from_slice(&0_u32.to_le_bytes());
    for i in 0..20 {
        bytes.extend_from_slice(&(100.125 + i as f64).to_le_bytes());
        bytes.extend_from_slice(&(10 + i as u32).to_le_bytes());
        bytes.extend_from_slice(&(2 + i as u32).to_le_bytes());
    }
    bytes
}

fn config() -> SegmentConfig {
    SegmentConfig {
        max_bytes: 64 * 1024 * 1024,
        max_age: Duration::from_secs(3600),
        max_disk_bytes: 4 * 1024 * 1024 * 1024,
        min_free_bytes: 512 * 1024 * 1024,
        durability: DurabilityPolicy::EveryEvent,
    }
}

fn transport_config() -> TransportConfig {
    TransportConfig {
        subscriptions: vec![Subscription {
            exchange_segment: "NSE_EQ".into(),
            security_id: "1333".into(),
        }],
        max_reconnects: 2,
        base_backoff: Duration::from_millis(100),
        max_backoff: Duration::from_secs(2),
        idle_timeout: Duration::from_secs(45),
    }
}

async fn mock_feed(root: &Path) -> io::Result<()> {
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let address = listener.local_addr()?;
    let server = tokio::spawn(async move {
        let (tcp, _) = listener.accept().await?;
        let mut socket = tokio_tungstenite::accept_async(tcp)
            .await
            .map_err(|_| io::Error::other("mock handshake failed"))?;
        let _subscription = socket.next().await;
        let mut frame = fixture_side(41);
        frame.extend(fixture_side(51));
        socket
            .send(Message::Binary(frame.into()))
            .await
            .map_err(|_| io::Error::other("mock send failed"))?;
        socket
            .close(None)
            .await
            .map_err(|_| io::Error::other("mock close failed"))?;
        Ok::<(), io::Error>(())
    });
    let mut capture = Capture::open(root, config(), 16, "mock".into())?;
    let (_tx, mut stop) = watch::channel(false);
    let mut transport = transport_config();
    transport.max_reconnects = 0;
    let result = run_reconnecting(
        || async {
            let tcp = TcpStream::connect(address).await?;
            tokio_tungstenite::client_async(format!("ws://{address}"), tcp)
                .await
                .map(|(socket, _)| socket)
                .map_err(|_| io::Error::other("mock connect failed"))
        },
        &mut capture,
        &transport,
        &mut stop,
    )
    .await;
    server.await.map_err(io::Error::other)??;
    if !matches!(result, Err(ref e) if e.kind() == io::ErrorKind::ConnectionAborted) {
        result?;
    }
    let state = capture
        .metrics
        .state(std::time::Instant::now(), HealthThresholds::default());
    let metrics = capture.close().await?;
    println!(
        "{}",
        serde_json::to_string_pretty(
            &serde_json::json!({"feed_state": format!("{state:?}").to_uppercase(), "metrics": metrics})
        )?
    );
    Ok(())
}

#[tokio::main]
async fn main() -> io::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let mode = args.get(1).map(String::as_str).unwrap_or("offline-fixture");
    match mode {
        "offline-fixture" => {
            let mut frame = fixture_side(41);
            frame.extend(fixture_side(51));
            let packets = decode_frame(&frame, 0, "fixture", 1).map_err(io::Error::other)?;
            println!(
                "packets={} complete_books={}",
                packets.len(),
                assemble_frame(&packets).len()
            );
        }
        "mock-feed" => {
            let root = args.get(2).ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "mock-feed requires journal directory",
                )
            })?;
            mock_feed(Path::new(root)).await?;
        }
        "replay" => {
            let root = args.get(2).ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "replay requires journal directory",
                )
            })?;
            println!(
                "recovered_records={}",
                count_recovered_records(Path::new(root))?
            );
        }
        "recover" => {
            let root = args.get(2).ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "recover requires journal directory",
                )
            })?;
            let writer = SegmentedJournal::open(Path::new(root), config())?;
            println!("recovered_tail_bytes={}", writer.recovered_tail_bytes());
        }
        "live-readonly" => {
            #[cfg(feature = "live-readonly")]
            {
                if std::env::var("QUANT_ENABLE_LIVE_READONLY").as_deref() != Ok("1") {
                    return Err(io::Error::new(
                        io::ErrorKind::PermissionDenied,
                        "live depth mode is disabled",
                    ));
                }
                let root = args.get(2).ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::InvalidInput,
                        "live-readonly requires journal directory",
                    )
                })?;
                let subscriptions = args.get(3).ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::InvalidInput,
                        "live-readonly requires subscriptions JSON path",
                    )
                })?;
                let list: Vec<Subscription> =
                    serde_json::from_slice(&std::fs::read(subscriptions)?)?;
                let mut transport = transport_config();
                transport.subscriptions = list;
                transport.validate()?;
                let mut capture = Capture::open(Path::new(root), config(), 16, "live".into())?;
                let (tx, mut stop) = watch::channel(false);
                tokio::spawn(async move {
                    if tokio::signal::ctrl_c().await.is_ok() {
                        let _ = tx.send(true);
                    }
                });
                let result =
                    quant_depth::transport::run_live(&mut capture, &transport, &mut stop).await;
                let state = capture
                    .metrics
                    .state(std::time::Instant::now(), HealthThresholds::default());
                let (metrics, shutdown) = capture.finish().await;
                println!(
                    "{}",
                    serde_json::to_string_pretty(
                        &serde_json::json!({"feed_state": format!("{state:?}").to_uppercase(), "metrics": metrics})
                    )?
                );
                shutdown?;
                result?;
            }
            #[cfg(not(feature = "live-readonly"))]
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "live-readonly feature is disabled",
            ));
        }
        _ => {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "mode: offline-fixture | mock-feed <dir> | replay <dir> | recover <dir> | live-readonly <dir> <subscriptions.json>",
            ));
        }
    }
    Ok(())
}
