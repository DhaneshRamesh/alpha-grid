//! Synthetic source and validation boundary. No Dhan wire decoding is assumed.
use quant_core::{BOOK_LEVELS, DepthEvent, DepthLevel, Instrument};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug, PartialEq, Eq)]
pub enum NormalizeError {
    WrongLevelCount,
    Clock,
}

pub struct RawDepthPacket {
    pub instrument: Instrument,
    pub exchange_timestamp_ns: Option<i64>,
    pub sequence: Option<u64>,
    pub bids: Vec<DepthLevel>,
    pub asks: Vec<DepthLevel>,
}

pub fn normalize(packet: RawDepthPacket) -> Result<DepthEvent, NormalizeError> {
    // Take the local timestamp at the normalization boundary, before validation.
    let receive_timestamp_ns = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|_| NormalizeError::Clock)?
        .as_nanos()
        .try_into()
        .map_err(|_| NormalizeError::Clock)?;
    Ok(DepthEvent {
        instrument: packet.instrument,
        exchange_timestamp_ns: packet.exchange_timestamp_ns,
        receive_timestamp_ns,
        sequence: packet.sequence,
        bids: packet
            .bids
            .try_into()
            .map_err(|_| NormalizeError::WrongLevelCount)?,
        asks: packet
            .asks
            .try_into()
            .map_err(|_| NormalizeError::WrongLevelCount)?,
    })
}

pub fn synthetic_packet(index: u64, instrument_count: usize) -> RawDepthPacket {
    let slot = index as usize % instrument_count;
    let instrument = Instrument {
        venue: "NSE".into(),
        exchange_segment: "NSE_EQ".into(),
        security_id: (10_000 + slot).to_string(),
        symbol: format!("S{slot}"),
    };
    let bids = (0..BOOK_LEVELS)
        .map(|level| DepthLevel {
            price_nanos: 2_500_000_000_000 - (level as i64) * 50_000_000,
            quantity: 100 + level as u64 * 7,
            order_count: Some(2 + level as u32),
        })
        .collect();
    let asks = (0..BOOK_LEVELS)
        .map(|level| DepthLevel {
            price_nanos: 2_500_050_000_000 + (level as i64) * 50_000_000,
            quantity: 95 + level as u64 * 5,
            order_count: Some(2 + level as u32),
        })
        .collect();
    RawDepthPacket {
        instrument,
        exchange_timestamp_ns: None,
        sequence: Some(index / instrument_count as u64 + 1),
        bids,
        asks,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_exactly_twenty_levels() {
        let event = normalize(synthetic_packet(0, 20)).unwrap();
        assert_eq!(event.bids.len(), 20);
        assert_eq!(event.asks.len(), 20);
        assert!(event.receive_timestamp_ns > 0);
        let mut bad = synthetic_packet(0, 20);
        bad.asks.pop();
        assert_eq!(normalize(bad), Err(NormalizeError::WrongLevelCount));
    }
}
