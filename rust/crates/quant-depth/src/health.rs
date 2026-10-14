use quant_core::{DepthSide, FeedState};
use serde::Serialize;
use std::collections::BTreeMap;
use std::time::{Duration, Instant};

#[derive(Clone, Copy, Debug)]
pub struct HealthThresholds {
    pub stale_after: Duration,
    pub degraded_after: Duration,
}

impl Default for HealthThresholds {
    fn default() -> Self {
        Self {
            degraded_after: Duration::from_secs(5),
            stale_after: Duration::from_secs(15),
        }
    }
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct InstrumentHealth {
    pub packets: u64,
    pub bid_packets: u64,
    pub ask_packets: u64,
    pub last_receive_timestamp_ns: Option<i64>,
    pub messages_per_second: Option<f64>,
    #[serde(skip)]
    pub(crate) started: Option<Instant>,
    #[serde(skip)]
    pub(crate) last_seen: Option<Instant>,
    #[serde(skip)]
    pub(crate) last_bid: Option<Instant>,
    #[serde(skip)]
    pub(crate) last_ask: Option<Instant>,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct CaptureMetrics {
    pub websocket_frames: u64,
    pub parsed_packets: u64,
    pub bid_packets: u64,
    pub ask_packets: u64,
    pub complete_books: u64,
    pub unpaired_sides: u64,
    pub unsupported_packets: u64,
    pub malformed_frames: u64,
    pub unexpected_lengths: u64,
    pub decode_errors: u64,
    pub reconnects: u64,
    pub subscription_failures: u64,
    pub queue_rejections: u64,
    pub fsync_errors: u64,
    pub replay_errors: u64,
    pub packet_received: u64,
    pub queue_accepted: u64,
    pub kernel_written: u64,
    pub fsync_confirmed: u64,
    pub recovered: u64,
    pub disk_remaining_bytes: Option<u64>,
    pub decode_time_ns: u64,
    pub queue_wait_time_ns: u64,
    pub journal_time_ns: u64,
    pub per_instrument: BTreeMap<String, InstrumentHealth>,
    #[serde(skip)]
    pub connected: bool,
    #[serde(skip)]
    pub(crate) last_frame: Option<Instant>,
    #[serde(skip)]
    pub(crate) last_quality_error: Option<Instant>,
}

impl CaptureMetrics {
    pub fn invalidate_session(&mut self) {
        self.connected = false;
        self.last_frame = None;
        for row in self.per_instrument.values_mut() {
            row.last_bid = None;
            row.last_ask = None;
        }
    }

    pub fn state(&self, now: Instant, thresholds: HealthThresholds) -> FeedState {
        if !self.connected {
            return FeedState::Disconnected;
        }
        let Some(last) = self.last_frame else {
            return FeedState::Stale;
        };
        if now.duration_since(last) >= thresholds.stale_after {
            return FeedState::Stale;
        }
        if now.duration_since(last) >= thresholds.degraded_after
            || self
                .last_quality_error
                .is_some_and(|at| now.duration_since(at) < thresholds.stale_after)
            || self
                .per_instrument
                .values()
                .any(|row| row.last_bid.is_none() || row.last_ask.is_none())
        {
            FeedState::Degraded
        } else {
            FeedState::Healthy
        }
    }

    pub(crate) fn observe_instrument(
        &mut self,
        id: &str,
        side: DepthSide,
        receive_ns: i64,
        now: Instant,
    ) {
        let row = self.per_instrument.entry(id.into()).or_default();
        let started = *row.started.get_or_insert(now);
        row.packets += 1;
        match side {
            DepthSide::Bid => {
                row.bid_packets += 1;
                row.last_bid = Some(now);
            }
            DepthSide::Ask => {
                row.ask_packets += 1;
                row.last_ask = Some(now);
            }
        }
        row.last_receive_timestamp_ns = Some(receive_ns);
        row.last_seen = Some(now);
        let seconds = now.duration_since(started).as_secs_f64();
        row.messages_per_second = (seconds >= 1.0).then(|| row.packets as f64 / seconds);
    }

    pub fn instrument_state(
        &self,
        id: &str,
        now: Instant,
        thresholds: HealthThresholds,
    ) -> FeedState {
        if !self.connected {
            return FeedState::Disconnected;
        }
        let Some(row) = self.per_instrument.get(id) else {
            return FeedState::Stale;
        };
        let Some(last) = row.last_seen else {
            return FeedState::Stale;
        };
        if now.duration_since(last) >= thresholds.stale_after {
            FeedState::Stale
        } else if now.duration_since(last) >= thresholds.degraded_after
            || row.last_bid.is_none()
            || row.last_ask.is_none()
        {
            FeedState::Degraded
        } else {
            FeedState::Healthy
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn state_transitions_and_missing_instruments() {
        let now = Instant::now();
        let mut m = CaptureMetrics::default();
        let t = HealthThresholds::default();
        assert_eq!(m.state(now, t), FeedState::Disconnected);
        m.connected = true;
        assert_eq!(m.instrument_state("1333", now, t), FeedState::Stale);
        m.last_frame = Some(now);
        m.observe_instrument("NSE_EQ:1333", DepthSide::Bid, 1, now);
        m.observe_instrument("NSE_EQ:1333", DepthSide::Ask, 1, now);
        assert_eq!(m.state(now, t), FeedState::Healthy);
        assert_eq!(
            m.instrument_state("NSE_EQ:1333", now + Duration::from_secs(6), t),
            FeedState::Degraded
        );
        assert_eq!(m.state(now + Duration::from_secs(16), t), FeedState::Stale);
    }
}
