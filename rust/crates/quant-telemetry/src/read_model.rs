//! Single-writer read model. Each instrument slot is replaced atomically after
//! durable append and engine processing; gateway reads never lock ingestion.
use crate::protocol::{
    AuditEvent, LatencySummary, WireCandle, WireCounters, WireDepth, WireEngineSnapshot,
    WireFeatures, WireInstrument, WireInstrumentSnapshot, WireMarket, instrument_key,
};
use arc_swap::{ArcSwap, ArcSwapOption};
use quant_core::{Instrument, MarketEvent, MarketSessionKind};
use quant_engine::pipeline::EngineOutput;
use quant_journal::JournalRecord;
use std::collections::{BTreeMap, VecDeque};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicI64, AtomicU8, AtomicU64, AtomicUsize, Ordering};
use std::time::Instant;

const MAX_PER_TIMEFRAME: usize = 240;
const MAX_CANDLES: usize = MAX_PER_TIMEFRAME * 6;
const MAX_AUDIT: usize = 200;

pub struct TelemetryShared {
    pub(crate) slots: BTreeMap<String, ArcSwapOption<WireInstrumentSnapshot>>,
    freshness_clocks: BTreeMap<String, AtomicI64>,
    revision: AtomicU64,
    pub(crate) counters: ArcSwap<WireCounters>,
    pub(crate) audit: ArcSwap<Vec<AuditEvent>>,
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

    fn snapshot_once(&self, generation: u64, stale_after_ns: i64) -> WireEngineSnapshot {
        let now = now_ns();
        let mut latest = None::<i64>;
        let mut instruments = Vec::with_capacity(self.slots.len());
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
                if observed_wall > 0
                    && now.saturating_sub(observed_wall) > stale_after_ns
                    && !self.replay_paused.load(Ordering::Acquire)
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
        WireEngineSnapshot {
            generation: generation.to_string(),
            replay_epoch: self.replay_epoch.load(Ordering::Acquire).to_string(),
            mode: self.mode.clone(),
            engine_running: self.engine_running.load(Ordering::Acquire),
            feed_connected: false, // Replay/demo is not an authenticated live feed.
            journal_blocked: self.journal_blocked.load(Ordering::Acquire),
            replay_paused: self.replay_paused.load(Ordering::Acquire),
            replay_speed: (self.mode == "REPLAY")
                .then(|| self.replay_speed.load(Ordering::Acquire)),
            session_id: self.session_id.clone(),
            latest_data_timestamp_ns: latest.map(|n| n.to_string()),
            storage_status: if self.journal_blocked.load(Ordering::Acquire) {
                "BLOCKED"
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
            audit: (**self.audit.load()).clone(),
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
                snapshot.market = Some(WireMarket::from_output(features));
                snapshot.features = Some(WireFeatures::from_output(features));
                if snapshot.depth.is_some() {
                    if let Some(market) = &mut snapshot.market {
                        market.quality.book_pairing_unverified = true;
                    }
                    if let Some(features) = &mut snapshot.features {
                        features.quality.book_pairing_unverified = true;
                    }
                }
            }
            let history = self.candles.entry(key.clone()).or_default();
            for candle in output
                .finalized_candles
                .iter()
                .chain(&output.provisional_candles)
            {
                let wire = WireCandle::from(candle);
                if let Some(existing) = history
                    .iter_mut()
                    .find(|v| v.timeframe_ns == wire.timeframe_ns && v.start_ns == wire.start_ns)
                {
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
