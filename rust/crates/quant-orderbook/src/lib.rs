//! Full-snapshot book state; no individual order events are inferred.
use quant_core::{BOOK_LEVELS, DepthEvent, DepthLevel, Instrument};

#[derive(Clone, Debug)]
pub struct OrderBook {
    pub instrument: Instrument,
    pub sequence: Option<u64>,
    pub bids: [DepthLevel; BOOK_LEVELS],
    pub asks: [DepthLevel; BOOK_LEVELS],
}

impl From<DepthEvent> for OrderBook {
    fn from(event: DepthEvent) -> Self {
        Self {
            instrument: event.instrument,
            sequence: event.sequence,
            bids: event.bids,
            asks: event.asks,
        }
    }
}

impl OrderBook {
    pub fn replace_snapshot(&mut self, event: DepthEvent) -> Result<(), &'static str> {
        if self.instrument != event.instrument {
            return Err("instrument mismatch");
        }
        self.sequence = event.sequence;
        self.bids = event.bids;
        self.asks = event.asks;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use quant_market_data::{normalize, synthetic_packet};

    #[test]
    fn replaces_only_matching_instrument_snapshot() {
        let first = normalize(synthetic_packet(0, 2)).unwrap();
        let mut book = OrderBook::from(first.clone());
        book.replace_snapshot(first).unwrap();
        assert!(book.replace_snapshot(normalize(synthetic_packet(1, 2)).unwrap()).is_err());
    }
}
