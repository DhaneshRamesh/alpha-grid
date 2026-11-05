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
    AuxiliaryOpenInterest { instrument: Instrument, value: u64 },
    Unsupported { code: u8, declared_length: u16 },
}

fn read_u16(p: &[u8], at: usize) -> u16 {
    u16::from_le_bytes([p[at], p[at + 1]])
}
fn read_i32(p: &[u8], at: usize) -> i32 {
    i32::from_le_bytes(p[at..at + 4].try_into().expect("packet length checked"))
}
fn read_u32(p: &[u8], at: usize) -> u32 {
    u32::from_le_bytes(p[at..at + 4].try_into().expect("packet length checked"))
}
fn read_f32(p: &[u8], at: usize) -> f32 {
    f32::from_le_bytes(p[at..at + 4].try_into().expect("packet length checked"))
}
fn quantity_u32(p: &[u8], at: usize) -> u64 {
    u64::from(read_u32(p, at))
}
fn quantity_u16(p: &[u8], at: usize) -> u64 {
    u64::from(read_u16(p, at))
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
fn ltt(p: &[u8], at: usize, _offset: usize) -> Result<Option<i64>, DecodeError> {
    let seconds = read_u32(p, at);
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
    let last_trade_quantity = quantity_u16(packet, 12);
    let average_trade_price_nanos = price(packet, 18, offset, true)?;
    let cumulative_traded_volume = quantity_u32(packet, 22);
    let total_sell_quantity = quantity_u32(packet, 26);
    let total_buy_quantity = quantity_u32(packet, 30);
    let (
        open_interest,
        day_high_open_interest,
        day_low_open_interest,
        daily_offset,
        five_level_book,
    ) = if full {
        let oi = quantity_u32(packet, 34);
        let oi_high = quantity_u32(packet, 38);
        let oi_low = quantity_u32(packet, 42);
        let mut levels = [FiveLevelQuote::default(); 5];
        let mut numeric_depth_valid = true;
        for (index, level) in levels.iter_mut().enumerate() {
            let at = 62 + 20 * index;
            let bid_price = price(packet, at + 12, offset, true);
            let ask_price = price(packet, at + 16, offset, true);
            numeric_depth_valid &= bid_price.is_ok() && ask_price.is_ok();
            *level = FiveLevelQuote {
                bid: DepthLevel {
                    quantity: quantity_u32(packet, at),
                    order_count: Some(u32::from(read_u16(packet, at + 8))),
                    price_nanos: bid_price.ok().flatten().unwrap_or(0),
                },
                ask: DepthLevel {
                    quantity: quantity_u32(packet, at + 4),
                    order_count: Some(u32::from(read_u16(packet, at + 10))),
                    price_nanos: ask_price.ok().flatten().unwrap_or(0),
                },
            };
        }
        let valid_book = numeric_depth_valid
            && levels.iter().enumerate().all(|(i, level)| {
                let bid = level.bid;
                let ask = level.ask;
                bid.price_nanos > 0
                    && ask.price_nanos > 0
                    && bid.quantity > 0
                    && ask.quantity > 0
                    && bid.order_count.unwrap_or(0) > 0
                    && ask.order_count.unwrap_or(0) > 0
                    && bid.price_nanos < ask.price_nanos
                    && (i == 0
                        || (levels[i - 1].bid.price_nanos > bid.price_nanos
                            && levels[i - 1].ask.price_nanos < ask.price_nanos))
            });
        (
            Some(oi),
            Some(oi_high),
            Some(oi_low),
            46,
            valid_book.then_some(levels),
        )
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
            5 => Some(12),
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
            5 => DecodedPacket::AuxiliaryOpenInterest {
                instrument: instrument(packet, offset)?,
                value: quantity_u32(packet, 8),
            },
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
                        previous_open_interest: quantity_u32(packet, 12),
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
            for index in 0..5 {
                p.extend_from_slice(&10_i32.to_le_bytes());
                p.extend_from_slice(&11_i32.to_le_bytes());
                p.extend_from_slice(&2_i16.to_le_bytes());
                p.extend_from_slice(&3_i16.to_le_bytes());
                p.extend_from_slice(&(101.0_f32 - index as f32).to_le_bytes());
                p.extend_from_slice(&(102.0_f32 + index as f32).to_le_bytes());
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
    fn invalid_full_book_isolated_without_losing_other_packets() {
        let mut invalid = quote_packet(8);
        invalid[62 + 16..62 + 20].copy_from_slice(&100.0_f32.to_le_bytes());
        let mut frame = invalid;
        let mut valid_other_stock = quote_packet(8);
        valid_other_stock[4..8].copy_from_slice(&2885_i32.to_le_bytes());
        frame.extend(valid_other_stock);
        let rows = parse(&frame).unwrap();
        let DecodedPacket::Quote(first) = &rows[0] else {
            panic!("first full")
        };
        let DecodedPacket::Quote(second) = &rows[1] else {
            panic!("second full")
        };
        assert!(first.five_level_book.is_none());
        assert!(second.five_level_book.is_some());
        assert_eq!(second.price.instrument.security_id, "2885");
        assert_eq!(second.price.metadata.packet_offset, 162);
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
        let mut oi = header(5, 12, 2885);
        oi.extend_from_slice(&0_i32.to_le_bytes());
        assert!(matches!(
            parse(&oi).unwrap()[0],
            DecodedPacket::AuxiliaryOpenInterest { value: 0, .. }
        ));
    }

    #[test]
    fn quote_quantities_follow_dhan_unsigned_wire_contract() {
        let mut p = quote_packet(4);
        p[12..14].copy_from_slice(&48_142_u16.to_le_bytes());
        p[22..26].copy_from_slice(&3_000_000_000_u32.to_le_bytes());
        let rows = parse(&p).unwrap();
        let DecodedPacket::Quote(quote) = &rows[0] else {
            panic!("quote")
        };
        assert_eq!(quote.last_trade_quantity, 48_142);
        assert_eq!(quote.cumulative_traded_volume, 3_000_000_000);
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
