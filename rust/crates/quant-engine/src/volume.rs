//! Cumulative traded volume reconstruction; displayed book quantity is never used.
use crate::MarketCalendar;
use quant_core::{MarketDataQuality, QuoteObservation, VolumeObservation, VolumeStatus};
use std::collections::BTreeMap;

#[derive(Clone)]
struct Baseline {
    market_session: String,
    feed_session: String,
    cumulative: u64,
    arrival_ordinal: u64,
    receive_timestamp_ns: i64,
    source_timestamp_ns: Option<i64>,
    disconnected: bool,
}

#[derive(Default)]
pub struct VolumeTracker {
    baselines: BTreeMap<(String, String, String), Baseline>,
}

impl VolumeTracker {
    pub fn invalidate_all(&mut self) {
        for baseline in self.baselines.values_mut() {
            baseline.disconnected = true;
        }
    }

    pub fn observe(
        &mut self,
        quote: &QuoteObservation,
        calendar: &dyn MarketCalendar,
    ) -> Result<VolumeObservation, &'static str> {
        self.observe_at(quote, calendar, quote.price.metadata.receive_timestamp_ns)
    }

    pub fn observe_at(
        &mut self,
        quote: &QuoteObservation,
        calendar: &dyn MarketCalendar,
        session_timestamp_ns: i64,
    ) -> Result<VolumeObservation, &'static str> {
        let meta = &quote.price.metadata;
        let session = calendar
            .session_at(session_timestamp_ns)
            .ok_or("event time outside configured market session")?;
        let key = (
            quote.price.instrument.venue.clone(),
            quote.price.instrument.exchange_segment.clone(),
            quote.price.instrument.security_id.clone(),
        );
        let previous = self.baselines.get(&key);
        let mut quality = quote.price.quality;
        let (status, delta, prev_recv, prev_source) = match previous {
            None => (VolumeStatus::BaselineUnknown, None, None, None),
            Some(p)
                if meta.arrival_ordinal <= p.arrival_ordinal
                    || meta.receive_timestamp_ns < p.receive_timestamp_ns
                    || meta
                        .source_last_trade_timestamp_ns
                        .zip(p.source_timestamp_ns)
                        .is_some_and(|(now, before)| now < before) =>
            {
                (VolumeStatus::OutOfOrder, None, None, None)
            }
            Some(p) if p.market_session != session => {
                (VolumeStatus::SessionChanged, None, None, None)
            }
            Some(p) if p.disconnected => (VolumeStatus::ReconnectUnknown, None, None, None),
            Some(p) if p.feed_session != meta.feed_session_id => {
                (VolumeStatus::ReconnectUnknown, None, None, None)
            }
            Some(p) if quote.cumulative_traded_volume < p.cumulative => {
                (VolumeStatus::Reset, None, None, None)
            }
            Some(p) => {
                let delta = quote.cumulative_traded_volume - p.cumulative;
                (
                    if delta == 0 {
                        VolumeStatus::Unchanged
                    } else {
                        VolumeStatus::Increased
                    },
                    Some(delta),
                    Some(p.receive_timestamp_ns),
                    p.source_timestamp_ns,
                )
            }
        };
        if let Some(value) = delta
            && value > 0
            && quote.last_trade_quantity > value
        {
            quality.inconsistent_last_trade_quantity = true;
        }
        quality.volume_interval_incomplete = delta.is_none();
        if status != VolumeStatus::OutOfOrder {
            self.baselines.insert(
                key,
                Baseline {
                    market_session: session,
                    feed_session: meta.feed_session_id.clone(),
                    cumulative: quote.cumulative_traded_volume,
                    arrival_ordinal: meta.arrival_ordinal,
                    receive_timestamp_ns: meta.receive_timestamp_ns,
                    source_timestamp_ns: meta.source_last_trade_timestamp_ns,
                    disconnected: false,
                },
            );
        }
        Ok(VolumeObservation {
            instrument: quote.price.instrument.clone(),
            metadata: meta.clone(),
            cumulative_traded_volume: quote.cumulative_traded_volume,
            incremental_volume: delta,
            previous_receive_timestamp_ns: prev_recv,
            previous_source_last_trade_timestamp_ns: prev_source,
            status,
            quality: MarketDataQuality { ..quality },
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use quant_core::{
        Instrument, PriceObservation, PriceSource, SourceProtocol, SourceTimestampMetadata,
    };

    struct Calendar;
    impl MarketCalendar for Calendar {
        fn session_at(&self, ns: i64) -> Option<String> {
            Some(if ns < 1_000 { "day1" } else { "day2" }.into())
        }
    }
    fn q(recv: i64, ordinal: u64, cumulative: u64, feed: &str) -> QuoteObservation {
        QuoteObservation {
            price: PriceObservation {
                instrument: Instrument {
                    venue: "NSE".into(),
                    exchange_segment: "NSE_EQ".into(),
                    security_id: "1333".into(),
                    symbol: "".into(),
                },
                metadata: SourceTimestampMetadata {
                    receive_timestamp_ns: recv,
                    source_last_trade_timestamp_ns: Some(recv),
                    feed_session_id: feed.into(),
                    arrival_ordinal: ordinal,
                    frame_id: ordinal,
                    packet_offset: 0,
                    protocol: SourceProtocol::DhanStandardV2,
                },
                source: PriceSource::Quote,
                price_nanos: 100_000_000_000,
                quality: MarketDataQuality::default(),
            },
            last_trade_quantity: 1,
            average_trade_price_nanos: None,
            cumulative_traded_volume: cumulative,
            total_buy_quantity: 0,
            total_sell_quantity: 0,
            day_open_nanos: None,
            day_close_nanos: None,
            day_high_nanos: None,
            day_low_nanos: None,
            open_interest: None,
            day_high_open_interest: None,
            day_low_open_interest: None,
            five_level_book: None,
        }
    }
    #[test]
    fn first_duplicate_increase_reset_reconnect_and_session() {
        let mut t = VolumeTracker::default();
        let observations = [
            (10, 1, 100, "a"),
            (20, 2, 100, "a"),
            (30, 3, 105, "a"),
            (40, 4, 103, "a"),
            (50, 5, 107, "b"),
            (60, 6, 110, "b"),
            (1_010, 7, 115, "b"),
        ];
        let results: Vec<_> = observations
            .iter()
            .map(|(r, o, v, s)| t.observe(&q(*r, *o, *v, s), &Calendar).unwrap())
            .collect();
        assert_eq!(
            results.iter().map(|x| x.status).collect::<Vec<_>>(),
            vec![
                VolumeStatus::BaselineUnknown,
                VolumeStatus::Unchanged,
                VolumeStatus::Increased,
                VolumeStatus::Reset,
                VolumeStatus::ReconnectUnknown,
                VolumeStatus::Increased,
                VolumeStatus::SessionChanged
            ]
        );
        assert_eq!(
            results
                .iter()
                .map(|x| x.incremental_volume)
                .collect::<Vec<_>>(),
            vec![None, Some(0), Some(5), None, None, Some(3), None]
        );
        assert_eq!(results[2].previous_receive_timestamp_ns, Some(20));
    }
    #[test]
    fn out_of_order_does_not_rewind_baseline() {
        let mut t = VolumeTracker::default();
        t.observe(&q(10, 1, 100, "a"), &Calendar).unwrap();
        t.observe(&q(30, 3, 110, "a"), &Calendar).unwrap();
        assert_eq!(
            t.observe(&q(20, 2, 106, "a"), &Calendar).unwrap().status,
            VolumeStatus::OutOfOrder
        );
        assert_eq!(
            t.observe(&q(40, 4, 115, "a"), &Calendar)
                .unwrap()
                .incremental_volume,
            Some(5)
        );
    }
}
