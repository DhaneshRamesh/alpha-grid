//! Single-writer read model. Each instrument slot is replaced atomically after
//! durable append and engine processing; gateway reads never lock ingestion.
use crate::protocol::{
    AuditEvent, LatencySummary, WireCandle, WireCounters, WireDepth, WireEngineSnapshot,
    WireFeatures, WireInstrument, WireInstrumentSnapshot, WireMarket, WirePaperSnapshot,
    instrument_key,
};
use arc_swap::{ArcSwap, ArcSwapOption};
use futures_util::{SinkExt, StreamExt};
use quant_core::{Instrument, MarketEvent, MarketSessionKind};
use quant_engine::capture::{CaptureError, StandardCapture};
use quant_engine::pipeline::{EngineOutput, MarketEngine};
use quant_journal::{JournalRecord, JournalWriter};
use quant_paper::oms::PaperSnapshot;
use std::collections::{BTreeMap, VecDeque};
use std::io;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicI64, AtomicU8, AtomicU64, AtomicUsize, Ordering};
use std::time::{Duration, Instant};
use tokio::io::{AsyncRead, AsyncWrite};
use tokio::sync::watch;
use tokio_tungstenite::{WebSocketStream, tungstenite::Message};

const MAX_PER_TIMEFRAME: usize = 240;
const MAX_CANDLES: usize = MAX_PER_TIMEFRAME * 6;
const MAX_AUDIT: usize = 200;
const MAX_FEED_AUDIT: usize = 48;

/// Static, credential-free transport diagnostics. Never store upstream error
/// strings, response bodies, headers, or the authenticated WebSocket URL.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FeedStopReason {
    DnsFailed,
    TcpFailed,
    TlsFailed,
    UpgradeHttp(u16),
    UpgradeProtocolFailed,
    TlsOrUpgradeIoFailed,
    ConnectTimedOut,
    SubscriptionTimedOut,
    SubscriptionSendFailed,
    ServerClosed(Option<u16>),
    FeedDisconnectPacket(u16),
    TransportEof,
    SocketFailed,
    IdleTimedOut,
    UnexpectedMessage,
    PacketRejected,
    StorageFailed,
    Cancelled,
    CaptureWindowEnded,
    WorkerFailed,
    WorkerPanicked,
    ClockUnhealthy,
}

impl FeedStopReason {
    fn code(self) -> String {
        match self {
            Self::DnsFailed => "DNS_FAILED".into(),
            Self::TcpFailed => "TCP_FAILED".into(),
            Self::TlsFailed => "TLS_FAILED".into(),
            Self::UpgradeHttp(status) => format!("UPGRADE_HTTP_{status}"),
            Self::UpgradeProtocolFailed => "UPGRADE_PROTOCOL_FAILED".into(),
            Self::TlsOrUpgradeIoFailed => "TLS_OR_UPGRADE_IO_FAILED".into(),
            Self::ConnectTimedOut => "CONNECT_TIMED_OUT".into(),
            Self::SubscriptionTimedOut => "SUBSCRIPTION_TIMED_OUT".into(),
            Self::SubscriptionSendFailed => "SUBSCRIPTION_SEND_FAILED".into(),
            Self::ServerClosed(Some(code)) => format!("SERVER_CLOSE_{code}"),
            Self::ServerClosed(None) => "SERVER_CLOSED_NO_CODE".into(),
            Self::FeedDisconnectPacket(code) => format!("DHAN_DISCONNECT_{code}"),
            Self::TransportEof => "TRANSPORT_EOF".into(),
            Self::SocketFailed => "SOCKET_FAILED".into(),
            Self::IdleTimedOut => "IDLE_TIMED_OUT".into(),
            Self::UnexpectedMessage => "UNEXPECTED_MESSAGE".into(),
            Self::PacketRejected => "PACKET_REJECTED".into(),
            Self::StorageFailed => "STORAGE_FAILED".into(),
            Self::Cancelled => "CANCELLED".into(),
            Self::CaptureWindowEnded => "CAPTURE_WINDOW_ENDED".into(),
            Self::WorkerFailed => "WORKER_FAILED".into(),
            Self::WorkerPanicked => "WORKER_PANICKED".into(),
            Self::ClockUnhealthy => "CLOCK_UNHEALTHY".into(),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FeedLifecycle {
    PreflightPassed,
    ResolvingDns,
    DnsResolved,
    ConnectingTcp,
    TcpConnected,
    TlsAndUpgradeStarted,
    TlsEstablished,
    WebSocketUpgraded,
    QuoteSubscriptionSent,
    FirstBinaryFrameDurable,
    Stopped(FeedStopReason),
}

impl FeedLifecycle {
    fn detail(self) -> String {
        match self {
            Self::PreflightPassed => "PREFLIGHT_PASSED".into(),
            Self::ResolvingDns => "RESOLVING_DNS".into(),
            Self::DnsResolved => "DNS_RESOLVED".into(),
            Self::ConnectingTcp => "CONNECTING_TCP".into(),
            Self::TcpConnected => "TCP_CONNECTED".into(),
            Self::TlsAndUpgradeStarted => "TLS_AND_UPGRADE_STARTED".into(),
            Self::TlsEstablished => "TLS_ESTABLISHED".into(),
            Self::WebSocketUpgraded => "WEBSOCKET_UPGRADED".into(),
            Self::QuoteSubscriptionSent => "QUOTE17_SUBSCRIPTION_SENT".into(),
            Self::FirstBinaryFrameDurable => "FIRST_BINARY_FRAME_DURABLE".into(),
            Self::Stopped(reason) => format!("STOPPED:{}", reason.code()),
        }
    }
}

pub struct TelemetryShared {
    pub(crate) slots: BTreeMap<String, ArcSwapOption<WireInstrumentSnapshot>>,
    freshness_clocks: BTreeMap<String, AtomicI64>,
    revision: AtomicU64,
    pub(crate) counters: ArcSwap<WireCounters>,
    pub(crate) audit: ArcSwap<Vec<AuditEvent>>,
    feed_audit: ArcSwap<Vec<AuditEvent>>,
    feed_terminal_recorded: AtomicBool,
    paper: ArcSwapOption<WirePaperSnapshot>,
    approvals: ArcSwap<Vec<String>>,
    feed_connected: AtomicBool,
    feed_started: AtomicBool,
    feed_fault: AtomicBool,
    stale_rejected: AtomicBool,
    pub(crate) connected_clients: AtomicUsize,
    pub(crate) coalesced_updates: AtomicU64,
    pub(crate) skipped_updates: AtomicU64,
    pub(crate) journal_blocked: AtomicBool,
    pub(crate) replay_paused: AtomicBool,
    pub(crate) replay_speed: AtomicU8,
    pub(crate) replay_epoch: AtomicU64,
    last_observed_wall_ns: AtomicI64,
    pub(crate) clock_started: Instant,
    pub(crate) last_observed_mono_ns: AtomicU64,
    pub(crate) publication_latency: ArcSwap<LatencySummary>,
    pub(crate) engine_running: AtomicBool,
    pub(crate) mode: String,
    pub(crate) session_id: String,
    pub(crate) boot_id: String,
}

impl TelemetryShared {
    pub fn new(
        instruments: &[Instrument],
        mode: &str,
        session_id: &str,
    ) -> Result<Arc<Self>, &'static str> {
        if instruments.is_empty() || instruments.len() > 50 {
            return Err("instrument count must be 1..=50");
        }
        let mut slots = BTreeMap::new();
        let mut freshness_clocks = BTreeMap::new();
        for instrument in instruments {
            let key = instrument_key(instrument);
            if slots.insert(key.clone(), ArcSwapOption::empty()).is_some() {
                return Err("duplicate instrument");
            }
            freshness_clocks.insert(key, AtomicI64::new(0));
        }
        Ok(Arc::new(Self {
            slots,
            freshness_clocks,
            revision: AtomicU64::new(0),
            counters: ArcSwap::from_pointee(WireCounters::default()),
            audit: ArcSwap::from_pointee(Vec::new()),
            feed_audit: ArcSwap::from_pointee(Vec::new()),
            feed_terminal_recorded: AtomicBool::new(false),
            paper: ArcSwapOption::empty(),
            approvals: ArcSwap::from_pointee(Vec::new()),
            feed_connected: AtomicBool::new(false),
            feed_started: AtomicBool::new(false),
            feed_fault: AtomicBool::new(false),
            stale_rejected: AtomicBool::new(false),
            connected_clients: AtomicUsize::new(0),
            coalesced_updates: AtomicU64::new(0),
            skipped_updates: AtomicU64::new(0),
            journal_blocked: AtomicBool::new(false),
            replay_paused: AtomicBool::new(false),
            replay_speed: AtomicU8::new(1),
            replay_epoch: AtomicU64::new(0),
            last_observed_wall_ns: AtomicI64::new(0),
            clock_started: Instant::now(),
            last_observed_mono_ns: AtomicU64::new(0),
            publication_latency: ArcSwap::from_pointee(LatencySummary::default()),
            engine_running: AtomicBool::new(true),
            mode: mode.into(),
            session_id: session_id.into(),
            boot_id: format!("{}-{}", std::process::id(), now_ns()),
        }))
    }

    pub fn set_journal_blocked(&self, blocked: bool) {
        self.journal_blocked.store(blocked, Ordering::Release);
    }

    /// Trusted transport calls this only after authenticating the Dhan socket
    /// and subscribing the configured feed session. Reconnect requires a new
    /// gateway/feed session; stale baselines are never resumed implicitly.
    pub fn start_observation_feed(&self, session_id: &str) -> Result<(), &'static str> {
        if self.mode != "OBSERVE_ONLY" || session_id.is_empty() || session_id != self.session_id {
            return Err("observation-only Dhan feed session required");
        }
        if self.feed_started.swap(true, Ordering::AcqRel) {
            return Err("feed session already started; restart required");
        }
        self.feed_connected.store(true, Ordering::Release);
        Ok(())
    }

    pub fn stop_feed(&self) {
        self.feed_connected.store(false, Ordering::Release);
    }

    pub fn record_feed_lifecycle(&self, lifecycle: FeedLifecycle) {
        if self.mode != "OBSERVE_ONLY" {
            return;
        }
        if matches!(lifecycle, FeedLifecycle::Stopped(_)) {
            if self.feed_terminal_recorded.swap(true, Ordering::AcqRel) {
                return;
            }
        } else if self.feed_terminal_recorded.load(Ordering::Acquire) {
            return;
        }
        let event = AuditEvent {
            timestamp_ns: now_ns().to_string(),
            instrument_key: Some("NSE:NSE_EQ:2885".into()),
            kind: "FEED_LIFECYCLE".into(),
            detail: lifecycle.detail(),
        };
        eprintln!("Dhan feed stage: {}", event.detail);
        self.feed_audit.rcu(|prior| {
            let mut events = (**prior).clone();
            events.push(event.clone());
            if events.len() > MAX_FEED_AUDIT {
                events.remove(0);
            }
            Arc::new(events)
        });
    }
    pub fn set_replay_paused(&self, paused: bool) {
        self.replay_paused.store(paused, Ordering::Release);
    }
    pub fn replay_paused(&self) -> bool {
        self.replay_paused.load(Ordering::Acquire)
    }
    pub fn replay_speed(&self) -> u8 {
        self.replay_speed.load(Ordering::Acquire)
    }
    pub fn stop(&self) {
        self.engine_running.store(false, Ordering::Release);
    }

    pub fn reset_replay(&self) {
        let _write = WriteGuard::new(self);
        for slot in self.slots.values() {
            slot.store(None);
        }
        for clock in self.freshness_clocks.values() {
            clock.store(0, Ordering::Release);
        }
        self.counters.store(Arc::new(WireCounters::default()));
        self.audit.store(Arc::new(Vec::new()));
        self.paper.store(None);
        self.replay_epoch.fetch_add(1, Ordering::AcqRel);
        self.last_observed_wall_ns.store(0, Ordering::Release);
        self.last_observed_mono_ns.store(0, Ordering::Release);
        self.engine_running.store(true, Ordering::Release);
    }

    pub fn snapshot(&self, generation: u64, stale_after_ns: i64) -> Option<WireEngineSnapshot> {
        for _ in 0..3 {
            let before = self.revision.load(Ordering::Acquire);
            if before & 1 != 0 {
                continue;
            }
            let value = self.snapshot_once(generation, stale_after_ns);
            if self.revision.load(Ordering::Acquire) == before {
                return Some(value);
            }
        }
        None
    }

    /// Publish only after the paper ledger has fsynced and accepted the transition.
    pub fn publish_paper(&self, snapshot: &PaperSnapshot) {
        if self.mode == "OBSERVE_ONLY" {
            return;
        }
        let _write = WriteGuard::new(self);
        self.paper
            .store(Some(Arc::new(WirePaperSnapshot::from(snapshot))));
    }

    /// Called only after an approval transition is durable in QP01.
    pub fn publish_approvals(&self, security_ids: Vec<String>) {
        let _write = WriteGuard::new(self);
        self.approvals.store(Arc::new(security_ids));
    }

    fn snapshot_once(&self, generation: u64, stale_after_ns: i64) -> WireEngineSnapshot {
        let now = now_ns();
        let mut latest = None::<i64>;
        let mut instruments = Vec::with_capacity(self.slots.len());
        let queue_rejected = self.counters.load().queue_rejections > 0;
        for (key, slot) in &self.slots {
            let mut value =
                slot.load_full()
                    .map(|v| (*v).clone())
                    .unwrap_or_else(|| WireInstrumentSnapshot {
                        instrument: WireInstrument {
                            key: key.clone(),
                            venue: key.split(':').next().unwrap_or("").into(),
                            exchange_segment: key.split(':').nth(1).unwrap_or("").into(),
                            security_id: key.split(':').nth(2).unwrap_or("").into(),
                            symbol: None,
                        },
                        market: None,
                        features: None,
                        depth: None,
                        candles: Vec::new(),
                        last_update_timestamp_ns: None,
                        previous_close_nanos: None,
                    });
            if let Some(ns) = value
                .last_update_timestamp_ns
                .as_deref()
                .and_then(|s| s.parse::<i64>().ok())
            {
                latest = Some(latest.map_or(ns, |old| old.max(ns)));
                let observed_wall = self
                    .freshness_clocks
                    .get(key)
                    .map(|clock| clock.load(Ordering::Acquire))
                    .unwrap_or(0);
                if (self.mode == "OBSERVE_ONLY" && !self.feed_connected.load(Ordering::Acquire))
                    || self.feed_fault.load(Ordering::Acquire)
                    || self.journal_blocked.load(Ordering::Acquire)
                    || queue_rejected
                    || (observed_wall > 0
                        && now.saturating_sub(observed_wall) > stale_after_ns
                        && !self.replay_paused.load(Ordering::Acquire))
                {
                    if let Some(market) = &mut value.market {
                        market.freshness = "STALE".into();
                        market.quality.stale = true;
                    }
                    if let Some(features) = &mut value.features {
                        features.freshness = "STALE".into();
                        features.quality.stale = true;
                    }
                }
            }
            instruments.push(value);
        }
        let mut counters = (**self.counters.load()).clone();
        counters.connected_clients = self.connected_clients.load(Ordering::Acquire);
        counters.telemetry_coalesced_updates = self.coalesced_updates.load(Ordering::Acquire);
        counters.telemetry_skipped_updates = self.skipped_updates.load(Ordering::Acquire);
        counters.telemetry_publication_latency = (**self.publication_latency.load()).clone();
        let mut no_trade_reasons = Vec::new();
        if self.mode == "OBSERVE_ONLY" || !self.feed_connected.load(Ordering::Acquire) {
            no_trade_reasons.push("NO_ELIGIBLE_DHAN_FEED".into());
        }
        if self.mode == "OBSERVE_ONLY" {
            no_trade_reasons.push("OBSERVATION_ONLY".into());
            no_trade_reasons.push("SESSION_CLASSIFICATION_UNVERIFIED".into());
        }
        if self.journal_blocked.load(Ordering::Acquire) {
            no_trade_reasons.push("JOURNAL_BLOCKED".into());
        }
        if self.feed_fault.load(Ordering::Acquire) {
            no_trade_reasons.push("FEED_PACKET_REJECTED".into());
        }
        if self.stale_rejected.load(Ordering::Acquire) {
            no_trade_reasons.push("STALE_OBSERVATION_REJECTED".into());
        }
        if queue_rejected {
            no_trade_reasons.push("JOURNAL_QUEUE_REJECTED".into());
        }
        if instruments.iter().all(|item| item.market.is_none()) {
            no_trade_reasons.push("NO_CURRENT_QUOTE".into());
        }
        if instruments.iter().any(|item| {
            item.market
                .as_ref()
                .is_some_and(|market| market.quality.stale)
        }) {
            no_trade_reasons.push("MARKET_DATA_STALE".into());
        }
        if instruments.iter().any(|item| {
            item.market
                .as_ref()
                .is_some_and(|market| market.quality.source_time_unverified)
        }) {
            no_trade_reasons.push("SOURCE_TIME_UNVERIFIED".into());
        }
        if self.paper.load().is_none() {
            no_trade_reasons.push("NO_VALIDATED_PAPER_DECISIONS".into());
        }
        if self.mode == "OBSERVE_ONLY" {
            no_trade_reasons.push("SOURCE_SEQUENCE_UNVERIFIED".into());
        }
        let mut audit = (**self.audit.load()).clone();
        audit.extend(self.feed_audit.load().iter().cloned());
        audit.sort_by_key(|event| event.timestamp_ns.parse::<i64>().unwrap_or(0));
        if audit.len() > MAX_AUDIT {
            audit.drain(..audit.len() - MAX_AUDIT);
        }
        WireEngineSnapshot {
            generation: generation.to_string(),
            replay_epoch: self.replay_epoch.load(Ordering::Acquire).to_string(),
            mode: self.mode.clone(),
            engine_running: self.engine_running.load(Ordering::Acquire),
            feed_connected: self.feed_connected.load(Ordering::Acquire),
            journal_blocked: self.journal_blocked.load(Ordering::Acquire),
            replay_paused: self.replay_paused.load(Ordering::Acquire),
            replay_speed: (self.mode == "REPLAY")
                .then(|| self.replay_speed.load(Ordering::Acquire)),
            session_id: self.session_id.clone(),
            latest_data_timestamp_ns: latest.map(|n| n.to_string()),
            storage_status: if self.journal_blocked.load(Ordering::Acquire) {
                "BLOCKED"
            } else if self.mode == "DATA_ONLY" {
                "NO_MARKET_JOURNAL"
            } else if cfg!(windows) {
                "FILE_SYNC_ONLY"
            } else {
                "JOURNALED"
            }
            .into(),
            backpressure_state: if counters.queue_rejections > 0 {
                "REJECTED"
            } else {
                "CLEAR"
            }
            .into(),
            counters,
            instruments,
            audit,
            paper: self.paper.load_full().map(|value| (*value).clone()),
            approved_security_ids: (**self.approvals.load()).clone(),
            no_trade_reasons,
        }
    }
}

struct WriteGuard<'a>(&'a TelemetryShared);
impl<'a> WriteGuard<'a> {
    fn new(shared: &'a TelemetryShared) -> Self {
        shared.revision.fetch_add(1, Ordering::AcqRel);
        Self(shared)
    }
}
impl Drop for WriteGuard<'_> {
    fn drop(&mut self) {
        self.0.revision.fetch_add(1, Ordering::Release);
    }
}

pub struct TelemetryPublisher {
    shared: Arc<TelemetryShared>,
    snapshots: BTreeMap<String, WireInstrumentSnapshot>,
    candles: BTreeMap<String, VecDeque<WireCandle>>,
    audit: VecDeque<AuditEvent>,
    counters: WireCounters,
}

impl TelemetryPublisher {
    /// A trusted authenticated transport supplies the bytes and receipt time.
    /// The existing capture fsyncs QJ01 before this observer publishes.
    pub fn ingest_standard_frame(
        &mut self,
        capture: &mut StandardCapture,
        frame: &[u8],
        receive_timestamp_ns: i64,
        feed_session_id: &str,
        writer: &mut JournalWriter,
        engine: &mut MarketEngine,
    ) -> Result<usize, CaptureError> {
        let receipt_age_ns = now_ns().saturating_sub(receive_timestamp_ns);
        if !self.shared.feed_connected.load(Ordering::Acquire)
            || self.shared.session_id != feed_session_id
            || self.shared.journal_blocked.load(Ordering::Acquire)
            || self.shared.feed_fault.load(Ordering::Acquire)
            || capture
                .expected_instrument()
                .is_none_or(|instrument| !self.snapshots.contains_key(&instrument_key(instrument)))
        {
            return Err(CaptureError::NotConnected);
        }
        if receive_timestamp_ns <= 0 || !(0..=5_000_000_000).contains(&receipt_age_ns) {
            let retained =
                capture.retain_stale_frame(frame, receive_timestamp_ns, feed_session_id, writer);
            self.shared.feed_fault.store(true, Ordering::Release);
            self.shared.stale_rejected.store(true, Ordering::Release);
            self.shared.stop_feed();
            return Err(retained.err().unwrap_or(CaptureError::StaleObservation));
        }
        let mut count = 0;
        let mut disconnected = None;
        let result = capture.on_frame_observed(
            frame,
            receive_timestamp_ns,
            feed_session_id,
            writer,
            engine,
            |record, output| {
                self.observe(record, output, false);
                if let MarketEvent::Session(session) = &record.event {
                    if let MarketSessionKind::Disconnected { reason_code } = &session.kind {
                        disconnected = Some(*reason_code);
                    }
                }
                count += 1;
            },
        );
        let capture_counts = capture.counters();
        {
            let _write = WriteGuard::new(&self.shared);
            self.counters.frames_received = capture_counts.frames_received;
            self.counters.packets_decoded = capture_counts.packets_decoded;
            self.counters.malformed_frames = capture_counts.malformed_frames;
            self.shared.counters.store(Arc::new(self.counters.clone()));
        }
        if result.is_err() {
            self.shared.feed_fault.store(true, Ordering::Release);
            self.shared.stop_feed();
        } else if let Some(code) = disconnected {
            self.shared.record_feed_lifecycle(FeedLifecycle::Stopped(
                FeedStopReason::FeedDisconnectPacket(code),
            ));
            self.shared.stop_feed();
        }
        result.map(|_| count)
    }
    pub fn new(shared: Arc<TelemetryShared>, instruments: &[Instrument]) -> Self {
        let snapshots = instruments
            .iter()
            .map(|instrument| {
                let wire = WireInstrument::from(instrument);
                (
                    wire.key.clone(),
                    WireInstrumentSnapshot {
                        instrument: wire,
                        market: None,
                        features: None,
                        depth: None,
                        candles: Vec::new(),
                        last_update_timestamp_ns: None,
                        previous_close_nanos: None,
                    },
                )
            })
            .collect();
        Self {
            shared,
            snapshots,
            candles: BTreeMap::new(),
            audit: VecDeque::new(),
            counters: WireCounters::default(),
        }
    }

    /// Call only after append_and_process succeeds, or from an explicitly marked replay.
    pub fn observe(&mut self, record: &JournalRecord, output: &EngineOutput, replay: bool) {
        if matches!(record.event, MarketEvent::RawStandardFrame(_)) {
            return;
        }
        let _write = WriteGuard::new(&self.shared);
        self.shared
            .last_observed_wall_ns
            .store(now_ns(), Ordering::Release);
        self.shared.last_observed_mono_ns.store(
            self.shared
                .clock_started
                .elapsed()
                .as_nanos()
                .min(u64::MAX as u128) as u64,
            Ordering::Release,
        );
        if replay {
            self.counters.replayed_records += 1;
        } else {
            self.counters.journal_records_accepted += 1;
            self.counters.durably_committed_records += 1;
        }
        let instrument = match &record.event {
            MarketEvent::Depth(v) => &v.instrument,
            MarketEvent::DepthSide(v) => &v.instrument,
            MarketEvent::Trade(v) => &v.instrument,
            MarketEvent::Price(v) => &v.instrument,
            MarketEvent::Quote(v) => &v.price.instrument,
            MarketEvent::RawStandardFrame(_) => unreachable!("raw frame handled above"),
            MarketEvent::Session(session) => {
                if let (Some(instrument), MarketSessionKind::PreviousClose { price_nanos, .. }) =
                    (&session.instrument, &session.kind)
                {
                    let key = instrument_key(instrument);
                    if let Some(snapshot) = self.snapshots.get_mut(&key) {
                        snapshot.previous_close_nanos = Some(price_nanos.to_string());
                        if let Some(slot) = self.shared.slots.get(&key) {
                            slot.store(Some(Arc::new(snapshot.clone())));
                        }
                    }
                }
                self.shared.counters.store(Arc::new(self.counters.clone()));
                return;
            }
        };
        let key = instrument_key(instrument);
        if let Some(snapshot) = self.snapshots.get_mut(&key) {
            let timestamp = record.event.receive_timestamp_ns();
            if output.features.is_some() || matches!(record.event, MarketEvent::Depth(_)) {
                snapshot.last_update_timestamp_ns = Some(timestamp.to_string());
                if let Some(clock) = self.shared.freshness_clocks.get(&key) {
                    clock.store(now_ns(), Ordering::Release);
                }
            }
            if let MarketEvent::Depth(depth) = &record.event {
                snapshot.depth = Some(WireDepth::from(depth.as_ref()));
            }
            if let Some(features) = &output.features {
                snapshot.market = Some(WireMarket::from_output(features, output.volume.as_ref()));
                if self.shared.mode != "OBSERVE_ONLY" {
                    snapshot.features = Some(WireFeatures::from_output(features));
                }
                if snapshot.depth.is_some() {
                    if let Some(market) = &mut snapshot.market {
                        market.quality.book_pairing_unverified = true;
                    }
                    if let Some(features) = &mut snapshot.features {
                        features.quality.book_pairing_unverified = true;
                    }
                }
            }
            if self.shared.mode != "OBSERVE_ONLY" {
                let history = self.candles.entry(key.clone()).or_default();
                for candle in output
                    .finalized_candles
                    .iter()
                    .chain(&output.provisional_candles)
                {
                    let wire = WireCandle::from(candle);
                    if let Some(existing) = history.iter_mut().find(|v| {
                        v.timeframe_ns == wire.timeframe_ns && v.start_ns == wire.start_ns
                    }) {
                        *existing = wire;
                    } else {
                        let timeframe = wire.timeframe_ns.clone();
                        history.push_back(wire);
                        if history
                            .iter()
                            .filter(|v| v.timeframe_ns == timeframe)
                            .count()
                            > MAX_PER_TIMEFRAME
                            && let Some(oldest) =
                                history.iter().position(|v| v.timeframe_ns == timeframe)
                        {
                            history.remove(oldest);
                        }
                    }
                }
                while history.len() > MAX_CANDLES {
                    history.pop_front();
                }
                snapshot.candles = history.iter().cloned().collect();
            }
            if let Some(slot) = self.shared.slots.get(&key) {
                slot.store(Some(Arc::new(snapshot.clone())));
            }
        }
        self.shared.counters.store(Arc::new(self.counters.clone()));
    }

    pub fn audit(&mut self, event: AuditEvent) {
        let _write = WriteGuard::new(&self.shared);
        self.audit.push_back(event);
        while self.audit.len() > MAX_AUDIT {
            self.audit.pop_front();
        }
        self.shared
            .audit
            .store(Arc::new(self.audit.iter().cloned().collect()));
    }
}

pub fn now_ns() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(i64::MAX as u128) as i64
}

/// Consumes an already authenticated Dhan v2 socket. This function never
/// creates a connection or reads credentials. Caller must verify entitlement
/// and provide an independently approved market session calendar to the engine.
#[allow(clippy::too_many_arguments)] // Existing capture, journal and telemetry components remain separate.
pub async fn run_authenticated_standard_socket<S>(
    mut socket: WebSocketStream<S>,
    capture: &mut StandardCapture,
    publisher: &mut TelemetryPublisher,
    writer: &mut JournalWriter,
    engine: &mut MarketEngine,
    feed_session_id: &str,
    idle_timeout: Duration,
    stop: &mut watch::Receiver<bool>,
) -> io::Result<()>
where
    S: AsyncRead + AsyncWrite + Unpin,
{
    let expected = capture.expected_instrument().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::PermissionDenied,
            "scoped NSE_EQ quote capture required",
        )
    })?;
    if expected.venue != "NSE"
        || expected.exchange_segment != "NSE_EQ"
        || expected.security_id != "2885"
        || idle_timeout.is_zero()
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "single RELIANCE quote scope required",
        ));
    }
    if *stop.borrow() {
        publisher
            .shared
            .record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::Cancelled));
        return Ok(());
    }
    let subscribe = serde_json::json!({
        "RequestCode": 17,
        "InstrumentCount": 1,
        "InstrumentList": [{ "ExchangeSegment": "NSE_EQ", "SecurityId": "2885" }]
    })
    .to_string();
    let sent = tokio::select! {
        changed = stop.changed() => {
            let _ = changed;
            publisher.shared.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::Cancelled));
            return Ok(());
        }
        sent = tokio::time::timeout(idle_timeout, socket.send(Message::Text(subscribe.into()))) => sent,
    };
    match sent {
        Ok(Ok(())) => publisher
            .shared
            .record_feed_lifecycle(FeedLifecycle::QuoteSubscriptionSent),
        Ok(Err(_)) => {
            publisher
                .shared
                .record_feed_lifecycle(FeedLifecycle::Stopped(
                    FeedStopReason::SubscriptionSendFailed,
                ));
            return Err(io::Error::other("Dhan quote subscription send failed"));
        }
        Err(_) => {
            publisher
                .shared
                .record_feed_lifecycle(FeedLifecycle::Stopped(
                    FeedStopReason::SubscriptionTimedOut,
                ));
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "Dhan quote subscription timed out",
            ));
        }
    }
    publisher
        .shared
        .start_observation_feed(feed_session_id)
        .map_err(|_| {
            io::Error::new(
                io::ErrorKind::PermissionDenied,
                "observation feed session unavailable",
            )
        })?;
    let mut first_binary_frame = true;
    let result = async {
        loop {
            tokio::select! {
                changed = stop.changed() => {
                    if changed.is_err() || *stop.borrow() {
                        publisher.shared.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::Cancelled));
                        return Ok(());
                    }
                }
                incoming = tokio::time::timeout(idle_timeout, socket.next()) => {
                    match incoming {
                        Ok(Some(Ok(Message::Binary(bytes)))) => {
                            let received_ns = now_ns();
                            if writer.estimated_bytes().map_or(true, |used| {
                                used.saturating_add(bytes.len() as u64).saturating_add(64 * 1024) > 64 * 1024 * 1024
                            }) {
                                publisher.shared.set_journal_blocked(true);
                                publisher.shared.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::StorageFailed));
                                return Err(io::Error::other("Dhan quote journal capacity blocked"));
                            }
                            publisher.ingest_standard_frame(capture, &bytes, received_ns, feed_session_id, writer, engine)
                                .map_err(|error| {
                                    let reason = if matches!(error, CaptureError::Storage(_) | CaptureError::Engine(quant_engine::pipeline::EngineError::Journal(_))) {
                                        FeedStopReason::StorageFailed
                                    } else {
                                        FeedStopReason::PacketRejected
                                    };
                                    publisher.shared.record_feed_lifecycle(FeedLifecycle::Stopped(reason));
                                    io::Error::new(io::ErrorKind::InvalidData, "Dhan quote packet rejected; feed stopped")
                                })?;
                            if first_binary_frame {
                                publisher.shared.record_feed_lifecycle(FeedLifecycle::FirstBinaryFrameDurable);
                                first_binary_frame = false;
                            }
                            if !publisher.shared.feed_connected.load(Ordering::Acquire) {
                                return Ok(());
                            }
                        }
                        Ok(Some(Ok(Message::Ping(payload)))) => {
                            socket.send(Message::Pong(payload)).await.map_err(|_| {
                                publisher.shared.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::SocketFailed));
                                io::Error::other("Dhan quote pong failed")
                            })?;
                        }
                        Ok(Some(Ok(Message::Pong(_)))) => {}
                        Ok(Some(Ok(Message::Close(frame)))) => {
                            publisher.shared.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::ServerClosed(frame.map(|value| u16::from(value.code)))));
                            return Ok(());
                        }
                        Ok(None) => {
                            publisher.shared.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::TransportEof));
                            return Ok(());
                        }
                        Ok(Some(Err(_))) => {
                            publisher.shared.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::SocketFailed));
                            return Err(io::Error::other("Dhan quote socket failed"));
                        }
                        Err(_) => {
                            publisher.shared.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::IdleTimedOut));
                            return Err(io::Error::new(io::ErrorKind::TimedOut, "Dhan quote feed idle"));
                        }
                        Ok(Some(Ok(_))) => {
                            publisher.shared.feed_fault.store(true, Ordering::Release);
                            publisher.shared.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::UnexpectedMessage));
                            return Err(io::Error::new(io::ErrorKind::InvalidData, "unexpected Dhan quote message"));
                        }
                    }
                }
            }
        }
    }.await;
    publisher.shared.stop_feed();
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use quant_core::{DepthEvent, DepthLevel};
    use quant_engine::bars::BarConfig;
    use quant_engine::features::FeatureConfig;
    use quant_engine::pipeline::MarketEngine;
    use quant_engine::{MarketCalendar, TimestampPolicy};

    struct Calendar;
    impl MarketCalendar for Calendar {
        fn session_at(&self, _: i64) -> Option<String> {
            Some("test".into())
        }
    }

    fn instrument() -> Instrument {
        Instrument {
            venue: "NSE".into(),
            exchange_segment: "NSE_EQ".into(),
            security_id: "1333".into(),
            symbol: "RELIANCE".into(),
        }
    }
    fn reliance_quote_frame() -> Vec<u8> {
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
        for value in [100.0_f32, 0.0, 101.0, 99.0] {
            packet.extend(value.to_le_bytes());
        }
        packet
    }

    #[test]
    fn scoped_quote_is_file_synced_before_telemetry_and_faults_stop_feed() {
        use quant_journal::{DurabilityPolicy, JournalReader, JournalWriter};
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("quote.qj");
        let instrument = Instrument {
            security_id: "2885".into(),
            symbol: "RELIANCE".into(),
            ..instrument()
        };
        let shared = TelemetryShared::new(
            std::slice::from_ref(&instrument),
            "OBSERVE_ONLY",
            "fixture-session",
        )
        .unwrap();
        let mut publisher =
            TelemetryPublisher::new(Arc::clone(&shared), std::slice::from_ref(&instrument));
        let mut capture = StandardCapture::for_instrument(instrument);
        let mut writer = JournalWriter::open(&path, DurabilityPolicy::EveryEvent).unwrap();
        let mut engine = MarketEngine::new(
            Box::new(Calendar),
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
        shared.start_observation_feed("fixture-session").unwrap();
        assert_eq!(
            publisher
                .ingest_standard_frame(
                    &mut capture,
                    &reliance_quote_frame(),
                    now_ns(),
                    "fixture-session",
                    &mut writer,
                    &mut engine
                )
                .unwrap(),
            1
        );
        let visible = shared.snapshot(1, 5_000_000_000).unwrap();
        let market = visible.instruments[0].market.as_ref().unwrap();
        assert_eq!(market.price_nanos.as_deref(), Some("100000000000"));
        assert_eq!(market.cumulative_traded_volume.as_deref(), Some("500"));
        assert_eq!(market.provenance.source_protocol, "DhanStandardV2");
        assert_eq!(visible.mode, "OBSERVE_ONLY");
        assert!(visible.paper.is_none());
        assert!(visible.instruments[0].features.is_none());
        assert!(visible.instruments[0].candles.is_empty());
        for reason in [
            "OBSERVATION_ONLY",
            "SESSION_CLASSIFICATION_UNVERIFIED",
            "NO_ELIGIBLE_DHAN_FEED",
        ] {
            assert!(visible.no_trade_reasons.contains(&reason.to_string()));
        }
        assert!(
            visible
                .no_trade_reasons
                .contains(&"SOURCE_TIME_UNVERIFIED".into())
        );
        assert_eq!(visible.counters.durably_committed_records, 1);
        writer.close().unwrap();
        let mut reader = JournalReader::open(&path).unwrap();
        assert!(matches!(
            reader.next_record().unwrap().unwrap().event,
            MarketEvent::RawStandardFrame(_)
        ));
        assert!(matches!(
            reader.next_record().unwrap().unwrap().event,
            MarketEvent::Quote(_)
        ));
        assert!(reader.next_record().unwrap().is_none());
        shared.stop_feed();
        let disconnected = shared.snapshot(2, 5_000_000_000).unwrap();
        assert!(
            disconnected.instruments[0]
                .market
                .as_ref()
                .unwrap()
                .quality
                .stale
        );
        assert!(
            disconnected
                .no_trade_reasons
                .contains(&"NO_ELIGIBLE_DHAN_FEED".into())
        );
        assert!(shared.start_observation_feed("fixture-session").is_err());
    }

    #[test]
    fn observation_feed_cannot_start_in_other_modes() {
        let instrument = instrument();
        for mode in ["DATA_ONLY", "DHAN_VERIFIED", "REPLAY"] {
            let shared =
                TelemetryShared::new(std::slice::from_ref(&instrument), mode, "fixture-session")
                    .unwrap();
            assert!(shared.start_observation_feed("fixture-session").is_err());
        }
    }

    #[test]
    fn stale_receipt_retains_raw_bytes_but_never_publishes_quote() {
        use quant_journal::{DurabilityPolicy, JournalReader, JournalWriter};
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("stale.qj");
        let instrument = Instrument {
            security_id: "2885".into(),
            symbol: "RELIANCE".into(),
            ..instrument()
        };
        let shared = TelemetryShared::new(
            std::slice::from_ref(&instrument),
            "OBSERVE_ONLY",
            "fixture-session",
        )
        .unwrap();
        let mut publisher =
            TelemetryPublisher::new(Arc::clone(&shared), std::slice::from_ref(&instrument));
        let mut capture = StandardCapture::for_instrument(instrument);
        let mut writer = JournalWriter::open(&path, DurabilityPolicy::EveryEvent).unwrap();
        let mut engine = MarketEngine::new(
            Box::new(Calendar),
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
        shared.start_observation_feed("fixture-session").unwrap();
        let frame = reliance_quote_frame();
        assert!(matches!(
            publisher.ingest_standard_frame(
                &mut capture,
                &frame,
                now_ns() - 10_000_000_000,
                "fixture-session",
                &mut writer,
                &mut engine,
            ),
            Err(CaptureError::StaleObservation)
        ));
        writer.close().unwrap();
        let snapshot = shared.snapshot(1, 5_000_000_000).unwrap();
        assert!(!snapshot.feed_connected);
        assert!(snapshot.instruments[0].market.is_none());
        assert!(
            snapshot
                .no_trade_reasons
                .contains(&"FEED_PACKET_REJECTED".into())
        );
        assert!(
            snapshot
                .no_trade_reasons
                .contains(&"STALE_OBSERVATION_REJECTED".into())
        );
        let mut reader = JournalReader::open(&path).unwrap();
        let record = reader.next_record().unwrap().unwrap();
        let MarketEvent::RawStandardFrame(raw) = record.event else {
            panic!("raw frame required")
        };
        assert_eq!(raw.bytes, frame);
        assert!(reader.next_record().unwrap().is_none());
    }

    #[tokio::test]
    async fn local_quote_socket_sends_only_scoped_subscription_and_stops_on_close() {
        use quant_journal::{DurabilityPolicy, JournalReader, JournalWriter};
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let packet = reliance_quote_frame();
        let peer = tokio::spawn(async move {
            let (tcp, _) = listener.accept().await.unwrap();
            let mut socket = tokio_tungstenite::accept_async(tcp).await.unwrap();
            let request: serde_json::Value =
                serde_json::from_str(&socket.next().await.unwrap().unwrap().into_text().unwrap())
                    .unwrap();
            assert_eq!(request["RequestCode"], 17);
            assert_eq!(request["InstrumentCount"], 1);
            assert_eq!(request["InstrumentList"][0]["SecurityId"], "2885");
            socket.send(Message::Binary(packet.into())).await.unwrap();
            socket.close(None).await.unwrap();
        });
        let (socket, _) = tokio_tungstenite::connect_async(format!("ws://{address}"))
            .await
            .unwrap();
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("local-quote.qj");
        let instrument = Instrument {
            security_id: "2885".into(),
            symbol: "RELIANCE".into(),
            ..instrument()
        };
        let shared = TelemetryShared::new(
            std::slice::from_ref(&instrument),
            "OBSERVE_ONLY",
            "fixture-session",
        )
        .unwrap();
        let mut publisher =
            TelemetryPublisher::new(Arc::clone(&shared), std::slice::from_ref(&instrument));
        let mut capture = StandardCapture::for_instrument(instrument);
        let mut writer = JournalWriter::open(&path, DurabilityPolicy::EveryEvent).unwrap();
        let mut engine = MarketEngine::new(
            Box::new(Calendar),
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
        let (_stop, mut receiver) = watch::channel(false);
        run_authenticated_standard_socket(
            socket,
            &mut capture,
            &mut publisher,
            &mut writer,
            &mut engine,
            "fixture-session",
            Duration::from_secs(2),
            &mut receiver,
        )
        .await
        .unwrap();
        peer.await.unwrap();
        writer.close().unwrap();
        let snapshot = shared.snapshot(1, 5_000_000_000).unwrap();
        assert!(!snapshot.feed_connected);
        assert!(snapshot.paper.is_none());
        assert!(
            snapshot
                .no_trade_reasons
                .contains(&"OBSERVATION_ONLY".into())
        );
        let stages: Vec<_> = snapshot
            .audit
            .iter()
            .filter(|event| event.kind == "FEED_LIFECYCLE")
            .map(|event| event.detail.as_str())
            .collect();
        assert_eq!(
            stages,
            [
                "QUOTE17_SUBSCRIPTION_SENT",
                "FIRST_BINARY_FRAME_DURABLE",
                "STOPPED:SERVER_CLOSED_NO_CODE"
            ]
        );
        assert_eq!(snapshot.counters.frames_received, 1);
        assert_eq!(snapshot.counters.packets_decoded, 1);
        let mut reader = JournalReader::open(&path).unwrap();
        assert!(matches!(
            reader.next_record().unwrap().unwrap().event,
            MarketEvent::RawStandardFrame(_)
        ));
        assert!(matches!(
            reader.next_record().unwrap().unwrap().event,
            MarketEvent::Quote(_)
        ));
        assert!(reader.next_record().unwrap().is_none());
    }

    #[test]
    fn feed_lifecycle_is_bounded_and_terminal_reason_is_first_wins() {
        let instrument = Instrument {
            security_id: "2885".into(),
            ..instrument()
        };
        let shared =
            TelemetryShared::new(&[instrument], "OBSERVE_ONLY", "fixture-session").unwrap();
        for _ in 0..MAX_FEED_AUDIT + 3 {
            shared.record_feed_lifecycle(FeedLifecycle::ResolvingDns);
        }
        shared.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::UpgradeHttp(403)));
        shared.record_feed_lifecycle(FeedLifecycle::Stopped(FeedStopReason::WorkerFailed));
        shared.record_feed_lifecycle(FeedLifecycle::WebSocketUpgraded);
        let snapshot = shared.snapshot(1, 5_000_000_000).unwrap();
        let stages: Vec<_> = snapshot
            .audit
            .iter()
            .filter(|event| event.kind == "FEED_LIFECYCLE")
            .map(|event| event.detail.as_str())
            .collect();
        assert_eq!(stages.len(), MAX_FEED_AUDIT);
        assert_eq!(stages.last(), Some(&"STOPPED:UPGRADE_HTTP_403"));
        assert!(snapshot.paper.is_none());
    }
    #[test]
    fn atomic_slot_contains_complete_depth_and_replay_does_not_claim_new_durability() {
        let instrument = instrument();
        let shared =
            TelemetryShared::new(std::slice::from_ref(&instrument), "REPLAY", "test").unwrap();
        let mut publisher =
            TelemetryPublisher::new(Arc::clone(&shared), std::slice::from_ref(&instrument));
        let record = JournalRecord {
            session_id: "test".into(),
            event: MarketEvent::Depth(Box::new(DepthEvent {
                instrument,
                exchange_timestamp_ns: None,
                receive_timestamp_ns: now_ns(),
                sequence: None,
                bids: [DepthLevel {
                    price_nanos: 100_000_000_000,
                    quantity: 10,
                    order_count: Some(1),
                }; 20],
                asks: [DepthLevel {
                    price_nanos: 101_000_000_000,
                    quantity: 20,
                    order_count: Some(2),
                }; 20],
            })),
        };
        let output = EngineOutput {
            volume: None,
            finalized_candles: Vec::new(),
            provisional_candles: Vec::new(),
            features: None,
        };
        publisher.observe(&record, &output, true);
        let snapshot = shared.snapshot(7, 5_000_000_000).unwrap();
        assert_eq!(snapshot.generation, "7");
        assert_eq!(
            snapshot.instruments[0].depth.as_ref().unwrap().bids.len(),
            20
        );
        assert_eq!(
            snapshot.instruments[0].depth.as_ref().unwrap().asks.len(),
            20
        );
        assert_eq!(snapshot.counters.replayed_records, 1);
        assert_eq!(snapshot.counters.durably_committed_records, 0);
        assert_eq!(
            shared.snapshot(8, 1).unwrap().instruments[0]
                .depth
                .as_ref()
                .unwrap()
                .bids
                .len(),
            20
        );
    }

    #[test]
    fn fixed_instrument_limit_and_staleness_are_enforced() {
        let instrument = instrument();
        assert!(TelemetryShared::new(&vec![instrument.clone(); 51], "REPLAY", "x").is_err());
        let shared = TelemetryShared::new(&[instrument], "REPLAY", "x").unwrap();
        assert!(
            shared.snapshot(0, 1).unwrap().instruments[0]
                .market
                .is_none()
        );
    }

    #[test]
    fn concurrent_snapshots_never_mix_depth_and_receive_generations() {
        let instrument = instrument();
        let shared =
            TelemetryShared::new(std::slice::from_ref(&instrument), "REPLAY", "x").unwrap();
        let writer_shared = Arc::clone(&shared);
        let worker = std::thread::spawn(move || {
            let mut publisher = TelemetryPublisher::new(
                Arc::clone(&writer_shared),
                std::slice::from_ref(&instrument),
            );
            let output = EngineOutput {
                volume: None,
                finalized_candles: Vec::new(),
                provisional_candles: Vec::new(),
                features: None,
            };
            for n in 1..=1_000 {
                let record = JournalRecord {
                    session_id: "x".into(),
                    event: MarketEvent::Depth(Box::new(DepthEvent {
                        instrument: instrument.clone(),
                        exchange_timestamp_ns: None,
                        receive_timestamp_ns: n,
                        sequence: None,
                        bids: [DepthLevel::default(); 20],
                        asks: [DepthLevel::default(); 20],
                    })),
                };
                publisher.observe(&record, &output, true);
            }
        });
        for _ in 0..1_000 {
            if let Some(snapshot) = shared.snapshot(0, i64::MAX) {
                let value = &snapshot.instruments[0];
                if let Some(depth) = &value.depth {
                    assert_eq!(
                        value.last_update_timestamp_ns.as_ref(),
                        Some(&depth.receive_timestamp_ns)
                    );
                }
            }
        }
        worker.join().unwrap();
        assert_eq!(
            shared
                .snapshot(0, i64::MAX)
                .unwrap()
                .counters
                .replayed_records,
            1_000
        );
    }

    #[test]
    fn stale_status_is_per_instrument_and_book_pairing_is_unverified() {
        let first = instrument();
        let second = Instrument {
            security_id: "2885".into(),
            symbol: "TATAMOTORS".into(),
            ..first.clone()
        };
        let instruments = [first, second];
        let shared = TelemetryShared::new(&instruments, "REPLAY", "test").unwrap();
        let mut publisher = TelemetryPublisher::new(Arc::clone(&shared), &instruments);
        let mut engine = MarketEngine::new(
            Box::new(Calendar),
            BarConfig {
                timestamp_policy: TimestampPolicy::Receive,
                max_lateness_ns: 0,
                max_open_bars_per_instrument: 10,
            },
            FeatureConfig {
                max_cross_feed_age_ns: 1_000_000_000,
                stale_after_ns: 2_000_000_000,
                rolling_window: 8,
            },
        )
        .unwrap();
        for instrument in &instruments {
            let record = JournalRecord {
                session_id: "test".into(),
                event: MarketEvent::Depth(Box::new(DepthEvent {
                    instrument: instrument.clone(),
                    exchange_timestamp_ns: None,
                    receive_timestamp_ns: now_ns(),
                    sequence: None,
                    bids: [DepthLevel {
                        price_nanos: 100_000_000_000,
                        quantity: 10,
                        order_count: Some(1),
                    }; 20],
                    asks: [DepthLevel {
                        price_nanos: 101_000_000_000,
                        quantity: 20,
                        order_count: Some(2),
                    }; 20],
                })),
            };
            let output = engine.process(&record).unwrap();
            publisher.observe(&record, &output, true);
        }
        let first_key = instrument_key(&instruments[0]);
        shared
            .freshness_clocks
            .get(&first_key)
            .unwrap()
            .store(now_ns() - 10_000_000_000, Ordering::Release);
        let snapshot = shared.snapshot(1, 1_000_000_000).unwrap();
        assert_eq!(
            snapshot.instruments[0].market.as_ref().unwrap().freshness,
            "STALE"
        );
        assert_ne!(
            snapshot.instruments[1].market.as_ref().unwrap().freshness,
            "STALE"
        );
        assert!(
            snapshot.instruments[0]
                .market
                .as_ref()
                .unwrap()
                .quality
                .book_pairing_unverified
        );
    }
}
