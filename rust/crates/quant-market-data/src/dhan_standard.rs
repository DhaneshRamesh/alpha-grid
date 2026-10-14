//! Strict Dhan v2 standard-feed parser. Its 8-byte header is distinct from
//! the 12-byte 20-depth header. No quote is treated as an individual trade.
use quant_core::{
    DepthLevel, FiveLevelQuote, Instrument, MarketDataQuality, MarketSessionEvent,
    MarketSessionKind, PriceObservation, PriceSource, QuoteObservation, SourceProtocol,
    SourceTimestampMetadata,
};
use std::fmt;

pub const HEADER_BYTES: usize = 8;
pub const MAX_FRAME_BYTES: usize = 1_048_576;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum DecodeError {
    EmptyFrame,
    OversizedFrame,
    TruncatedHeader {
        offset: usize,
    },
    InvalidLength {
        offset: usize,
        declared: u16,
    },
    TruncatedPacket {
        offset: usize,
        declared: usize,
    },
    UnexpectedLength {
        offset: usize,
        code: u8,
        declared: usize,
    },
    InvalidInstrument {
        offset: usize,
    },
    InvalidQuantity {
        offset: usize,
    },
    InvalidTimestamp {
        offset: usize,
    },
    InvalidPrice {
        offset: usize,
    },
    UnexpectedAfterDisconnect {
        offset: usize,
    },
}

impl fmt::Display for DecodeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "invalid Dhan standard-feed frame: {self:?}")
    }
}
impl std::error::Error for DecodeError {}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum DecodedPacket {
    Price(PriceObservation),
    Quote(Box<QuoteObservation>),
    Session(MarketSessionEvent),
    Unsupported { code: u8, declared_length: u16 },
}

fn read_i16(p: &[u8], at: usize) -> i16 {
    i16::from_le_bytes([p[at], p[at + 1]])
}
fn read_i32(p: &[u8], at: usize) -> i32 {
    i32::from_le_bytes(p[at..at + 4].try_into().expect("packet length checked"))
}
fn read_f32(p: &[u8], at: usize) -> f32 {
    f32::from_le_bytes(p[at..at + 4].try_into().expect("packet length checked"))
}
fn nonnegative_i32(p: &[u8], at: usize, offset: usize) -> Result<u64, DecodeError> {
    u64::try_from(read_i32(p, at)).map_err(|_| DecodeError::InvalidQuantity { offset })
}
fn nonnegative_i16(p: &[u8], at: usize, offset: usize) -> Result<u64, DecodeError> {
    u64::try_from(read_i16(p, at)).map_err(|_| DecodeError::InvalidQuantity { offset })
}
fn price(p: &[u8], at: usize, offset: usize, optional: bool) -> Result<Option<i64>, DecodeError> {
    let value = read_f32(p, at);
    let scaled = (f64::from(value) * 1_000_000_000.0).round();
    if !value.is_finite()
        || value < 0.0
        || !scaled.is_finite()
        || scaled >= 9_223_372_036_854_775_808.0
        || (!optional && value == 0.0)
    {
        return Err(DecodeError::InvalidPrice { offset });
    }
    Ok(if optional && value == 0.0 {
        None
    } else {
        Some(scaled as i64)
    })
}
fn ltt(p: &[u8], at: usize, offset: usize) -> Result<Option<i64>, DecodeError> {
    let seconds = read_i32(p, at);
    if seconds < 0 {
        return Err(DecodeError::InvalidTimestamp { offset });
    }
    Ok((seconds > 0).then_some(i64::from(seconds) * 1_000_000_000))
}
fn instrument(p: &[u8], offset: usize) -> Result<Instrument, DecodeError> {
    let exchange_segment = match p[3] {
        1 => "NSE_EQ",
        2 => "NSE_FNO",
        _ => return Err(DecodeError::InvalidInstrument { offset }),
    };
    let id = read_i32(p, 4);
    if id <= 0 {
        return Err(DecodeError::InvalidInstrument { offset });
    }
    Ok(Instrument {
        venue: "NSE".into(),
        exchange_segment: exchange_segment.into(),
        security_id: id.to_string(),
        symbol: String::new(),
    })
}
fn metadata(
    receive_ns: i64,
    source_ns: Option<i64>,
    session: &str,
    arrival: u64,
    frame_id: u64,
    offset: usize,
) -> SourceTimestampMetadata {
    SourceTimestampMetadata {
        receive_timestamp_ns: receive_ns,
        source_last_trade_timestamp_ns: source_ns,
        feed_session_id: session.into(),
        arrival_ordinal: arrival,
        frame_id,
        packet_offset: offset as u32,
        protocol: SourceProtocol::DhanStandardV2,
    }
}
fn observed_price(
    instrument: Instrument,
    meta: SourceTimestampMetadata,
    source: PriceSource,
    value: i64,
) -> PriceObservation {
    PriceObservation {
        instrument,
        metadata: meta,
        source,
        price_nanos: value,
        quality: MarketDataQuality {
            source_time_unverified: true,
            ..Default::default()
        },
    }
}

fn quote(
    packet: &[u8],
    offset: usize,
    inst: Instrument,
    meta: SourceTimestampMetadata,
    full: bool,
) -> Result<QuoteObservation, DecodeError> {
    let last_trade_quantity = nonnegative_i16(packet, 12, offset)?;
    let average_trade_price_nanos = price(packet, 18, offset, true)?;
    let cumulative_traded_volume = nonnegative_i32(packet, 22, offset)?;
    let total_sell_quantity = nonnegative_i32(packet, 26, offset)?;
    let total_buy_quantity = nonnegative_i32(packet, 30, offset)?;
    let (
        open_interest,
        day_high_open_interest,
        day_low_open_interest,
        daily_offset,
        five_level_book,
    ) = if full {
        let oi = nonnegative_i32(packet, 34, offset)?;
        let oi_high = nonnegative_i32(packet, 38, offset)?;
        let oi_low = nonnegative_i32(packet, 42, offset)?;
        let mut levels = [FiveLevelQuote::default(); 5];
        for (index, level) in levels.iter_mut().enumerate() {
            let at = 62 + 20 * index;
            *level = FiveLevelQuote {
                bid: DepthLevel {
                    quantity: nonnegative_i32(packet, at, offset)?,
                    order_count: Some(u32::from(
                        u16::try_from(nonnegative_i16(packet, at + 8, offset)?)
                            .expect("u16 source"),
                    )),
                    price_nanos: price(packet, at + 12, offset, true)?.unwrap_or(0),
                },
                ask: DepthLevel {
                    quantity: nonnegative_i32(packet, at + 4, offset)?,
                    order_count: Some(u32::from(
                        u16::try_from(nonnegative_i16(packet, at + 10, offset)?)
                            .expect("u16 source"),
                    )),
                    price_nanos: price(packet, at + 16, offset, true)?.unwrap_or(0),
                },
            };
        }
        (Some(oi), Some(oi_high), Some(oi_low), 46, Some(levels))
    } else {
        (None, None, None, 34, None)
    };
    let p = observed_price(
        inst,
        meta,
        if full {
            PriceSource::Full
        } else {
            PriceSource::Quote
        },
        price(packet, 8, offset, false)?.expect("required price"),
    );
    Ok(QuoteObservation {
        price: p,
        last_trade_quantity,
        average_trade_price_nanos,
        cumulative_traded_volume,
        total_sell_quantity,
        total_buy_quantity,
        day_open_nanos: price(packet, daily_offset, offset, true)?,
        day_close_nanos: price(packet, daily_offset + 4, offset, true)?,
        day_high_nanos: price(packet, daily_offset + 8, offset, true)?,
        day_low_nanos: price(packet, daily_offset + 12, offset, true)?,
        open_interest,
        day_high_open_interest,
        day_low_open_interest,
        five_level_book,
    })
}

/// Parse concatenated standard packets atomically. `first_arrival_ordinal`
/// is caller-owned and only advances after this function succeeds.
pub fn decode_frame(
    frame: &[u8],
    receive_timestamp_ns: i64,
    feed_session_id: &str,
    frame_id: u64,
    first_arrival_ordinal: u64,
) -> Result<Vec<DecodedPacket>, DecodeError> {
    if frame.is_empty() {
        return Err(DecodeError::EmptyFrame);
    }
    if frame.len() > MAX_FRAME_BYTES {
        return Err(DecodeError::OversizedFrame);
    }
    let mut packets = Vec::new();
    let mut offset = 0;
    while offset < frame.len() {
        if frame.len() - offset < HEADER_BYTES {
            return Err(DecodeError::TruncatedHeader { offset });
        }
        let packet_len = u16::from_le_bytes([frame[offset + 1], frame[offset + 2]]);
        if usize::from(packet_len) < HEADER_BYTES {
            return Err(DecodeError::InvalidLength {
                offset,
                declared: packet_len,
            });
        }
        let length = usize::from(packet_len);
        if length > frame.len() - offset {
            return Err(DecodeError::TruncatedPacket {
                offset,
                declared: length,
            });
        }
        let packet = &frame[offset..offset + length];
        let code = packet[0];
        let expected = match code {
            2 | 6 => Some(16),
            4 => Some(50),
            8 => Some(162),
            7 => Some(8),
            50 => Some(10),
            _ => None,
        };
        if let Some(expected) = expected
            && length != expected
        {
            return Err(DecodeError::UnexpectedLength {
                offset,
                code,
                declared: length,
            });
        }
        let arrival = first_arrival_ordinal
            .checked_add(packets.len() as u64)
            .ok_or(DecodeError::InvalidTimestamp { offset })?;
        let item = match code {
            2 => {
                let inst = instrument(packet, offset)?;
                let meta = metadata(
                    receive_timestamp_ns,
                    ltt(packet, 12, offset)?,
                    feed_session_id,
                    arrival,
                    frame_id,
                    offset,
                );
                DecodedPacket::Price(observed_price(
                    inst,
                    meta,
                    PriceSource::Ticker,
                    price(packet, 8, offset, false)?.expect("required price"),
                ))
            }
            4 | 8 => {
                let inst = instrument(packet, offset)?;
                let meta = metadata(
                    receive_timestamp_ns,
                    ltt(packet, 14, offset)?,
                    feed_session_id,
                    arrival,
                    frame_id,
                    offset,
                );
                DecodedPacket::Quote(Box::new(quote(packet, offset, inst, meta, code == 8)?))
            }
            6 => {
                let inst = instrument(packet, offset)?;
                let meta = metadata(
                    receive_timestamp_ns,
                    None,
                    feed_session_id,
                    arrival,
                    frame_id,
                    offset,
                );
                DecodedPacket::Session(MarketSessionEvent {
                    instrument: Some(inst),
                    metadata: meta,
                    kind: MarketSessionKind::PreviousClose {
                        price_nanos: price(packet, 8, offset, false)?.expect("required price"),
                        previous_open_interest: nonnegative_i32(packet, 12, offset)?,
                    },
                })
            }
            7 => DecodedPacket::Session(MarketSessionEvent {
                instrument: None,
                metadata: metadata(
                    receive_timestamp_ns,
                    None,
                    feed_session_id,
                    arrival,
                    frame_id,
                    offset,
                ),
                kind: MarketSessionKind::StatusSignal,
            }),
            50 => {
                if offset + length != frame.len() {
                    return Err(DecodeError::UnexpectedAfterDisconnect { offset });
                }
                DecodedPacket::Session(MarketSessionEvent {
                    instrument: None,
                    metadata: metadata(
                        receive_timestamp_ns,
                        None,
                        feed_session_id,
                        arrival,
                        frame_id,
                        offset,
                    ),
                    kind: MarketSessionKind::Disconnected {
                        reason_code: u16::from_le_bytes([packet[8], packet[9]]),
                    },
                })
            }
            _ => DecodedPacket::Unsupported {
                code,
                declared_length: packet_len,
            },
        };
        packets.push(item);
        offset += length;
    }
    Ok(packets)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn header(code: u8, length: u16, id: i32) -> Vec<u8> {
        let mut p = vec![code];
        p.extend_from_slice(&length.to_le_bytes());
        p.push(1);
        p.extend_from_slice(&id.to_le_bytes());
        p
    }
    fn ticker() -> Vec<u8> {
        let mut p = header(2, 16, 1333);
        p.extend_from_slice(&100.125_f32.to_le_bytes());
        p.extend_from_slice(&1_700_000_000_i32.to_le_bytes());
        p
    }
    fn quote_packet(code: u8) -> Vec<u8> {
        let full = code == 8;
        let mut p = header(code, if full { 162 } else { 50 }, 1333);
        p.extend_from_slice(&101.5_f32.to_le_bytes());
        p.extend_from_slice(&5_i16.to_le_bytes());
        p.extend_from_slice(&1_700_000_001_i32.to_le_bytes());
        p.extend_from_slice(&101.0_f32.to_le_bytes());
        p.extend_from_slice(&100_i32.to_le_bytes());
        p.extend_from_slice(&20_i32.to_le_bytes());
        p.extend_from_slice(&30_i32.to_le_bytes());
        if full {
            for n in [1_i32, 2, 3] {
                p.extend_from_slice(&n.to_le_bytes());
            }
        }
        for v in [99.0_f32, 0.0, 102.0, 98.0] {
            p.extend_from_slice(&v.to_le_bytes());
        }
        if full {
            for _ in 0..5 {
                p.extend_from_slice(&10_i32.to_le_bytes());
                p.extend_from_slice(&11_i32.to_le_bytes());
                p.extend_from_slice(&2_i16.to_le_bytes());
                p.extend_from_slice(&3_i16.to_le_bytes());
                p.extend_from_slice(&101.0_f32.to_le_bytes());
                p.extend_from_slice(&102.0_f32.to_le_bytes());
            }
        }
        p
    }
    fn parse(p: &[u8]) -> Result<Vec<DecodedPacket>, DecodeError> {
        decode_frame(p, 123, "s", 7, 10)
    }
    #[test]
    fn ticker_quote_full_and_previous_close() {
        let mut frame = ticker();
        frame.extend(quote_packet(4));
        frame.extend(quote_packet(8));
        let mut prev = header(6, 16, 1333);
        prev.extend_from_slice(&99.0_f32.to_le_bytes());
        prev.extend_from_slice(&0_i32.to_le_bytes());
        frame.extend(prev);
        let rows = parse(&frame).unwrap();
        assert_eq!(rows.len(), 4);
        let DecodedPacket::Price(t) = &rows[0] else {
            panic!("ticker")
        };
        assert_eq!(t.price_nanos, 100_125_000_000);
        assert_eq!(
            t.metadata.source_last_trade_timestamp_ns,
            Some(1_700_000_000_000_000_000)
        );
        assert_eq!(t.metadata.packet_offset, 0);
        assert_eq!(t.metadata.arrival_ordinal, 10);
        let DecodedPacket::Quote(q) = &rows[1] else {
            panic!("quote")
        };
        assert_eq!(q.cumulative_traded_volume, 100);
        assert_eq!(q.last_trade_quantity, 5);
        assert_eq!(q.day_close_nanos, None);
        assert_eq!(q.price.metadata.packet_offset, 16);
        assert_eq!(q.price.metadata.arrival_ordinal, 11);
        let DecodedPacket::Quote(f) = &rows[2] else {
            panic!("full")
        };
        assert_eq!(f.five_level_book.unwrap()[0].bid.quantity, 10);
        assert_eq!(f.open_interest, Some(1));
        assert!(matches!(
            &rows[3],
            DecodedPacket::Session(MarketSessionEvent {
                kind: MarketSessionKind::PreviousClose {
                    price_nanos: 99_000_000_000,
                    ..
                },
                ..
            })
        ));
    }
    #[test]
    fn malformed_and_unknown_are_explicit() {
        assert!(matches!(
            parse(&[2, 16]),
            Err(DecodeError::TruncatedHeader { .. })
        ));
        assert!(matches!(
            parse(&ticker()[..15]),
            Err(DecodeError::TruncatedPacket { .. })
        ));
        let mut p = ticker();
        p[1..3].copy_from_slice(&7_u16.to_le_bytes());
        assert!(matches!(parse(&p), Err(DecodeError::InvalidLength { .. })));
        let mut p = ticker();
        p[1..3].copy_from_slice(&17_u16.to_le_bytes());
        p.push(0);
        assert!(matches!(
            parse(&p),
            Err(DecodeError::UnexpectedLength { .. })
        ));
        let mut p = ticker();
        p[3] = 3;
        assert!(matches!(
            parse(&p),
            Err(DecodeError::InvalidInstrument { .. })
        ));
        let mut p = ticker();
        p[8..12].copy_from_slice(&f32::NAN.to_le_bytes());
        assert!(matches!(parse(&p), Err(DecodeError::InvalidPrice { .. })));
        let mut p = quote_packet(4);
        p[22..26].copy_from_slice(&(-1_i32).to_le_bytes());
        assert!(matches!(
            parse(&p),
            Err(DecodeError::InvalidQuantity { .. })
        ));
        let mut p = ticker();
        p.extend([1, 2]);
        assert!(matches!(
            parse(&p),
            Err(DecodeError::TruncatedHeader { .. })
        ));
        let mut p = header(99, 8, 1333);
        assert_eq!(
            parse(&p),
            Ok(vec![DecodedPacket::Unsupported {
                code: 99,
                declared_length: 8
            }])
        );
        p = header(7, 8, 0);
        assert!(matches!(parse(&p).unwrap()[0], DecodedPacket::Session(_)));
    }
    #[test]
    fn disconnect_and_arbitrary_input() {
        let mut p = header(50, 10, 0);
        p.extend_from_slice(&805_u16.to_le_bytes());
        assert!(matches!(
            parse(&p).unwrap()[0],
            DecodedPacket::Session(MarketSessionEvent {
                kind: MarketSessionKind::Disconnected { reason_code: 805 },
                ..
            })
        ));
        let mut with_more = p;
        with_more.extend(ticker());
        assert!(matches!(
            parse(&with_more),
            Err(DecodeError::UnexpectedAfterDisconnect { .. })
        ));
        for len in 0..512 {
            let _ = parse(&vec![0xAA; len]);
        }
    }
}
