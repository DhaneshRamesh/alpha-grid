//! Incremental book and price features. Top-level OFI is snapshot-based, not order flow.
use crate::{Freshness, Provenance, SCHEMA_VERSION};
use quant_core::{
    BOOK_LEVELS, DepthEvent, DepthLevel, Instrument, MarketDataQuality, PriceObservation,
    SourceProtocol, VolumeObservation,
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, VecDeque};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MarketStateSnapshot {
    pub schema_version: u16,
    pub instrument: Instrument,
    pub processing_timestamp_ns: i64,
    pub source_last_trade_timestamp_ns: Option<i64>,
    pub freshness: Freshness,
    pub quality: MarketDataQuality,
    pub provenance: Provenance,
    pub price_nanos: Option<i64>,
    pub incremental_volume: Option<u64>,
    pub best_bid: Option<DepthLevel>,
    pub best_ask: Option<DepthLevel>,
    pub depth_receive_timestamp_ns: Option<i64>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct QuantFeatureSnapshot {
    pub schema_version: u16,
    pub instrument: Instrument,
    pub processing_timestamp_ns: i64,
    pub source_last_trade_timestamp_ns: Option<i64>,
    pub freshness: Freshness,
    pub quality: MarketDataQuality,
    pub provenance: Provenance,
    pub midprice_nanos: Option<i64>,
    pub absolute_spread_nanos: Option<i64>,
    pub relative_spread: Option<f64>,
    pub microprice_nanos: Option<i64>,
    pub bid_level_weighted_depth: Option<f64>,
    pub ask_level_weighted_depth: Option<f64>,
    pub bid_ask_imbalance: Option<f64>,
    pub snapshot_ofi_quantity: Option<i64>,
    pub latest_price_return: Option<f64>,
    pub rolling_return_volatility: Option<f64>,
    pub rolling_volume_activity: Option<f64>,
    pub price_volume_correlation: Option<f64>,
    pub rolling_sample_count: usize,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct EngineHealthSnapshot {
    pub schema_version: u16,
    pub instrument: Instrument,
    pub processing_timestamp_ns: i64,
    pub source_last_trade_timestamp_ns: Option<i64>,
    pub freshness: Freshness,
    pub quality: MarketDataQuality,
    pub provenance: Provenance,
    pub events_processed: u64,
    pub rejected_events: u64,
    pub last_receive_timestamp_ns: i64,
    pub cross_feed_age_ns: Option<i64>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct FeatureOutput {
    pub market: MarketStateSnapshot,
    pub features: QuantFeatureSnapshot,
    pub health: EngineHealthSnapshot,
}

#[derive(Clone, Copy, Debug)]
pub struct FeatureConfig {
    pub max_cross_feed_age_ns: i64,
    pub stale_after_ns: i64,
    pub rolling_window: usize,
}

impl FeatureConfig {
    pub fn validate(self) -> Result<Self, &'static str> {
        if self.max_cross_feed_age_ns < 0 || self.stale_after_ns < 0 || self.rolling_window < 2 {
            return Err("invalid feature configuration");
        }
        Ok(self)
    }
}

#[derive(Default)]
struct Running {
    values: VecDeque<f64>,
    sum: f64,
    sum_sq: f64,
}
impl Running {
    fn add(&mut self, value: f64, limit: usize) {
        self.values.push_back(value);
        self.sum += value;
        self.sum_sq += value * value;
        if self.values.len() > limit
            && let Some(old) = self.values.pop_front()
        {
            self.sum -= old;
            self.sum_sq -= old * old;
        }
    }
    fn mean(&self) -> Option<f64> {
        (!self.values.is_empty()).then(|| self.sum / self.values.len() as f64)
    }
    fn sample_std(&self) -> Option<f64> {
        (self.values.len() >= 2).then(|| {
            let n = self.values.len() as f64;
            ((self.sum_sq - self.sum * self.sum / n).max(0.0) / (n - 1.0)).sqrt()
        })
    }
}

#[derive(Default)]
struct PairedRunning {
    values: VecDeque<(f64, f64)>,
    sum_x: f64,
    sum_y: f64,
    sum_xx: f64,
    sum_yy: f64,
    sum_xy: f64,
}
impl PairedRunning {
    fn add(&mut self, x: f64, y: f64, limit: usize) {
        self.values.push_back((x, y));
        self.sum_x += x;
        self.sum_y += y;
        self.sum_xx += x * x;
        self.sum_yy += y * y;
        self.sum_xy += x * y;
        if self.values.len() > limit
            && let Some((a, b)) = self.values.pop_front()
        {
            self.sum_x -= a;
            self.sum_y -= b;
            self.sum_xx -= a * a;
            self.sum_yy -= b * b;
            self.sum_xy -= a * b;
        }
    }
    fn correlation(&self) -> Option<f64> {
        if self.values.len() < 3 {
            return None;
        }
        let n = self.values.len() as f64;
        let cov = self.sum_xy - self.sum_x * self.sum_y / n;
        let vx = self.sum_xx - self.sum_x * self.sum_x / n;
        let vy = self.sum_yy - self.sum_y * self.sum_y / n;
        (vx > 0.0 && vy > 0.0).then(|| (cov / (vx * vy).sqrt()).clamp(-1.0, 1.0))
    }
}

struct BookState {
    event: DepthEvent,
    feed_session_id: String,
}

#[derive(Default)]
struct InstrumentState {
    book: Option<BookState>,
    price: Option<PriceObservation>,
    volume: Option<VolumeObservation>,
    returns: Running,
    volumes: Running,
    price_volume: PairedRunning,
    last_return: Option<f64>,
    latest_ofi: Option<i64>,
    processed: u64,
    rejected: u64,
}

pub struct FeatureEngine {
    config: FeatureConfig,
    instruments: BTreeMap<(String, String, String), InstrumentState>,
}

impl FeatureEngine {
    pub fn invalidate_all(&mut self) {
        for state in self.instruments.values_mut() {
            state.book = None;
            state.price = None;
            state.volume = None;
            state.returns = Running::default();
            state.volumes = Running::default();
            state.price_volume = PairedRunning::default();
            state.last_return = None;
            state.latest_ofi = None;
        }
    }

    pub fn new(config: FeatureConfig) -> Result<Self, &'static str> {
        Ok(Self {
            config: config.validate()?,
            instruments: BTreeMap::new(),
        })
    }

    pub fn observe_price(
        &mut self,
        price: &PriceObservation,
        volume: Option<&VolumeObservation>,
        processing_timestamp_ns: i64,
    ) -> Result<FeatureOutput, &'static str> {
        let state = self.instruments.entry(key(&price.instrument)).or_default();
        if price.price_nanos <= 0
            || volume
                .is_some_and(|v| v.metadata != price.metadata || v.instrument != price.instrument)
            || state
                .price
                .as_ref()
                .is_some_and(|p| price.metadata.arrival_ordinal <= p.metadata.arrival_ordinal)
        {
            state.rejected += 1;
            return Err("invalid, mismatched or out-of-order price event");
        }
        if state
            .price
            .as_ref()
            .is_some_and(|p| p.metadata.feed_session_id != price.metadata.feed_session_id)
        {
            state.price = None;
            state.volume = None;
            state.returns = Running::default();
            state.volumes = Running::default();
            state.price_volume = PairedRunning::default();
            state.last_return = None;
        }
        let latest_return = state
            .price
            .as_ref()
            .map(|p| price.price_nanos as f64 / p.price_nanos as f64 - 1.0);
        if let Some(value) = latest_return {
            state.returns.add(value, self.config.rolling_window);
        }
        if let Some(delta) = volume.and_then(|v| v.incremental_volume) {
            state.volumes.add(delta as f64, self.config.rolling_window);
            if let Some(ret) = latest_return {
                state
                    .price_volume
                    .add(ret, delta as f64, self.config.rolling_window);
            }
        }
        state.last_return = latest_return;
        state.price = Some(price.clone());
        state.volume = volume.cloned();
        state.processed += 1;
        Ok(render(
            state,
            &price.instrument,
            &Provenance::from(&price.metadata),
            processing_timestamp_ns,
            price.metadata.receive_timestamp_ns,
            price.metadata.source_last_trade_timestamp_ns,
            &self.config,
        ))
    }

    pub fn observe_depth(
        &mut self,
        depth: &DepthEvent,
        feed_session_id: &str,
        processing_timestamp_ns: i64,
    ) -> Result<FeatureOutput, &'static str> {
        let state = self.instruments.entry(key(&depth.instrument)).or_default();
        if !valid_book(depth)
            || state
                .book
                .as_ref()
                .is_some_and(|b| depth.receive_timestamp_ns < b.event.receive_timestamp_ns)
        {
            state.rejected += 1;
            return Err("invalid or out-of-order 20-level book");
        }
        state.latest_ofi = state.book.as_ref().and_then(|b| {
            (b.feed_session_id == feed_session_id).then(|| snapshot_ofi(&b.event, depth))
        });
        state.book = Some(BookState {
            event: depth.clone(),
            feed_session_id: feed_session_id.into(),
        });
        state.processed += 1;
        let provenance = Provenance {
            feed_session_id: feed_session_id.into(),
            arrival_ordinal: None,
            source_protocol: SourceProtocol::DhanDepth20,
            source_last_trade_timestamp_ns: None,
            source_exchange_timestamp_ns: depth.exchange_timestamp_ns,
        };
        Ok(render(
            state,
            &depth.instrument,
            &provenance,
            processing_timestamp_ns,
            depth.receive_timestamp_ns,
            None,
            &self.config,
        ))
    }
}

fn key(instrument: &Instrument) -> (String, String, String) {
    (
        instrument.venue.clone(),
        instrument.exchange_segment.clone(),
        instrument.security_id.clone(),
    )
}

fn valid_book(book: &DepthEvent) -> bool {
    let bid = book.bids[0];
    let ask = book.asks[0];
    bid.price_nanos > 0 && ask.price_nanos > bid.price_nanos && bid.quantity > 0 && ask.quantity > 0
}

fn snapshot_ofi(old: &DepthEvent, new: &DepthEvent) -> i64 {
    let b = if new.bids[0].price_nanos > old.bids[0].price_nanos {
        new.bids[0].quantity as i128
    } else if new.bids[0].price_nanos == old.bids[0].price_nanos {
        new.bids[0].quantity as i128 - old.bids[0].quantity as i128
    } else {
        -(old.bids[0].quantity as i128)
    };
    let a = if new.asks[0].price_nanos > old.asks[0].price_nanos {
        old.asks[0].quantity as i128
    } else if new.asks[0].price_nanos == old.asks[0].price_nanos {
        old.asks[0].quantity as i128 - new.asks[0].quantity as i128
    } else {
        -(new.asks[0].quantity as i128)
    };
    (b + a).clamp(i64::MIN as i128, i64::MAX as i128) as i64
}

fn render(
    state: &InstrumentState,
    instrument: &Instrument,
    provenance: &Provenance,
    now: i64,
    receive_ns: i64,
    source_ns: Option<i64>,
    config: &FeatureConfig,
) -> FeatureOutput {
    let cross_age = state.book.as_ref().and_then(|b| {
        state.price.as_ref().map(|p| {
            ((i128::from(b.event.receive_timestamp_ns)
                - i128::from(p.metadata.receive_timestamp_ns))
            .abs()
            .min(i128::from(i64::MAX))) as i64
        })
    });
    let book_fresh = state.book.is_some()
        && (state.price.is_none()
            || cross_age.is_some_and(|age| age <= config.max_cross_feed_age_ns));
    let latest_receive = state
        .price
        .as_ref()
        .map(|p| p.metadata.receive_timestamp_ns)
        .unwrap_or(receive_ns)
        .max(
            state
                .book
                .as_ref()
                .map(|b| b.event.receive_timestamp_ns)
                .unwrap_or(receive_ns),
        );
    let freshness = if now.saturating_sub(latest_receive) > config.stale_after_ns {
        Freshness::Stale
    } else {
        Freshness::Fresh
    };
    let mut quality = state.price.as_ref().map(|p| p.quality).unwrap_or_default();
    if let Some(v) = &state.volume {
        quality.volume_interval_incomplete |= v.quality.volume_interval_incomplete;
        quality.inconsistent_last_trade_quantity |= v.quality.inconsistent_last_trade_quantity;
    }
    quality.cross_feed_uncertain = state.book.is_some(); // cross-feed clocks and simultaneity are unverified
    quality.stale = freshness == Freshness::Stale || (state.book.is_some() && !book_fresh);
    let book = state.book.as_ref().filter(|_| book_fresh).map(|b| &b.event);
    let (best_bid, best_ask) = book
        .map(|b| (Some(b.bids[0]), Some(b.asks[0])))
        .unwrap_or((None, None));
    let mut features = QuantFeatureSnapshot {
        schema_version: SCHEMA_VERSION,
        instrument: instrument.clone(),
        processing_timestamp_ns: now,
        source_last_trade_timestamp_ns: source_ns,
        freshness,
        quality,
        provenance: provenance.clone(),
        midprice_nanos: None,
        absolute_spread_nanos: None,
        relative_spread: None,
        microprice_nanos: None,
        bid_level_weighted_depth: None,
        ask_level_weighted_depth: None,
        bid_ask_imbalance: None,
        snapshot_ofi_quantity: None,
        latest_price_return: state.last_return,
        rolling_return_volatility: state.returns.sample_std(),
        rolling_volume_activity: state.volumes.mean(),
        price_volume_correlation: state.price_volume.correlation(),
        rolling_sample_count: state.returns.values.len(),
    };
    if let Some(b) = book {
        let bid = b.bids[0];
        let ask = b.asks[0];
        let mid = (i128::from(bid.price_nanos) + i128::from(ask.price_nanos)) / 2;
        let spread = ask.price_nanos - bid.price_nanos;
        features.midprice_nanos = Some(mid as i64);
        features.absolute_spread_nanos = Some(spread);
        features.relative_spread = Some(spread as f64 / mid as f64);
        features.microprice_nanos = Some(
            ((ask.price_nanos as f64 * bid.quantity as f64
                + bid.price_nanos as f64 * ask.quantity as f64)
                / (bid.quantity as f64 + ask.quantity as f64))
                .round() as i64,
        );
        let weighted = |levels: &[DepthLevel; BOOK_LEVELS]| -> f64 {
            levels
                .iter()
                .enumerate()
                .map(|(i, l)| l.quantity as f64 / (i + 1) as f64)
                .sum()
        };
        features.bid_level_weighted_depth = Some(weighted(&b.bids));
        features.ask_level_weighted_depth = Some(weighted(&b.asks));
        let bsum: f64 = b.bids.iter().map(|l| l.quantity as f64).sum();
        let asum: f64 = b.asks.iter().map(|l| l.quantity as f64).sum();
        features.bid_ask_imbalance = ((bsum + asum) > 0.0).then_some((bsum - asum) / (bsum + asum));
        features.snapshot_ofi_quantity = state.latest_ofi;
    }
    let market = MarketStateSnapshot {
        schema_version: SCHEMA_VERSION,
        instrument: instrument.clone(),
        processing_timestamp_ns: now,
        source_last_trade_timestamp_ns: source_ns,
        freshness,
        quality,
        provenance: provenance.clone(),
        price_nanos: state.price.as_ref().map(|p| p.price_nanos),
        incremental_volume: state.volume.as_ref().and_then(|v| v.incremental_volume),
        best_bid,
        best_ask,
        depth_receive_timestamp_ns: book.map(|b| b.receive_timestamp_ns),
    };
    let health = EngineHealthSnapshot {
        schema_version: SCHEMA_VERSION,
        instrument: instrument.clone(),
        processing_timestamp_ns: now,
        source_last_trade_timestamp_ns: source_ns,
        freshness,
        quality,
        provenance: provenance.clone(),
        events_processed: state.processed,
        rejected_events: state.rejected,
        last_receive_timestamp_ns: latest_receive,
        cross_feed_age_ns: cross_age,
    };
    FeatureOutput {
        market,
        features,
        health,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use quant_core::{PriceSource, SourceTimestampMetadata};

    fn instrument() -> Instrument {
        Instrument {
            venue: "NSE".into(),
            exchange_segment: "NSE_EQ".into(),
            security_id: "1333".into(),
            symbol: "".into(),
        }
    }
    fn price(ns: i64, ordinal: u64, value: i64) -> PriceObservation {
        PriceObservation {
            instrument: instrument(),
            metadata: SourceTimestampMetadata {
                receive_timestamp_ns: ns,
                source_last_trade_timestamp_ns: None,
                feed_session_id: "standard".into(),
                arrival_ordinal: ordinal,
                frame_id: ordinal,
                packet_offset: 0,
                protocol: SourceProtocol::DhanStandardV2,
            },
            source: PriceSource::Ticker,
            price_nanos: value,
            quality: MarketDataQuality::default(),
        }
    }
    fn book(ns: i64, bid: i64, ask: i64, bid_qty: u64, ask_qty: u64) -> DepthEvent {
        let mut bids = [DepthLevel::default(); BOOK_LEVELS];
        let mut asks = [DepthLevel::default(); BOOK_LEVELS];
        bids[0] = DepthLevel {
            price_nanos: bid,
            quantity: bid_qty,
            order_count: Some(1),
        };
        asks[0] = DepthLevel {
            price_nanos: ask,
            quantity: ask_qty,
            order_count: Some(1),
        };
        DepthEvent {
            instrument: instrument(),
            exchange_timestamp_ns: None,
            receive_timestamp_ns: ns,
            sequence: None,
            bids,
            asks,
        }
    }
    #[test]
    fn book_features_and_snapshot_ofi() {
        let mut e = FeatureEngine::new(FeatureConfig {
            max_cross_feed_age_ns: 100,
            stale_after_ns: 1_000,
            rolling_window: 8,
        })
        .unwrap();
        e.observe_price(&price(100, 1, 100), None, 100).unwrap();
        let out = e
            .observe_depth(&book(105, 99, 101, 10, 20), "depth", 105)
            .unwrap();
        assert_eq!(out.features.midprice_nanos, Some(100));
        assert_eq!(out.features.absolute_spread_nanos, Some(2));
        assert_eq!(out.features.relative_spread, Some(0.02));
        assert_eq!(out.features.microprice_nanos, Some(100));
        assert_eq!(out.features.bid_level_weighted_depth, Some(10.0));
        assert_eq!(out.features.bid_ask_imbalance, Some(-1.0 / 3.0));
        assert_eq!(out.features.snapshot_ofi_quantity, None);
        assert!(out.features.quality.cross_feed_uncertain);
        let out = e
            .observe_depth(&book(110, 99, 101, 15, 18), "depth", 110)
            .unwrap();
        assert_eq!(out.features.snapshot_ofi_quantity, Some(7));
        assert_eq!(
            e.observe_depth(&book(115, 99, 101, 20, 10), "new-depth", 115)
                .unwrap()
                .features
                .snapshot_ofi_quantity,
            None
        );
    }
    #[test]
    fn stale_depth_is_excluded_from_fresh_price() {
        let mut e = FeatureEngine::new(FeatureConfig {
            max_cross_feed_age_ns: 10,
            stale_after_ns: 100,
            rolling_window: 4,
        })
        .unwrap();
        e.observe_depth(&book(100, 99, 101, 10, 10), "depth", 100)
            .unwrap();
        let out = e.observe_price(&price(200, 1, 102), None, 200).unwrap();
        assert_eq!(out.market.best_bid, None);
        assert_eq!(out.features.midprice_nanos, None);
        assert!(out.features.quality.stale);
        assert_eq!(out.health.cross_feed_age_ns, Some(100));
        let out = e.observe_price(&price(210, 2, 103), None, 210).unwrap();
        assert_eq!(out.features.latest_price_return, Some(103.0 / 102.0 - 1.0));
        assert_eq!(
            e.observe_price(&price(190, 2, 99), None, 220),
            Err("invalid, mismatched or out-of-order price event")
        );
    }

    #[test]
    fn reconnect_resets_rolling_returns() {
        let mut e = FeatureEngine::new(FeatureConfig {
            max_cross_feed_age_ns: 10,
            stale_after_ns: 100,
            rolling_window: 4,
        })
        .unwrap();
        e.observe_price(&price(100, 1, 100), None, 100).unwrap();
        let mut next = price(200, 2, 110);
        next.metadata.feed_session_id = "new-standard-session".into();
        let out = e.observe_price(&next, None, 200).unwrap();
        assert_eq!(out.features.latest_price_return, None);
        assert_eq!(out.features.rolling_sample_count, 0);
    }
}
