//! Strict decoder for Dhan's 20-level *side* packets. No exchange timestamp
//! or trustworthy loss-detection sequence is present in this wire format.
use quant_core::{BOOK_LEVELS, DepthEvent, DepthLevel, DepthSide, DepthSideUpdate, Instrument};
use std::fmt;

pub const HEADER_BYTES: usize = 12;
pub const SIDE_BYTES: usize = HEADER_BYTES + BOOK_LEVELS * 16;
pub const MAX_FRAME_BYTES: usize = 1_048_576;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum DecodeError {
    OversizedFrame,
    TruncatedHeader { offset: usize },
    InvalidLength { offset: usize, declared: i16 },
    TruncatedPacket { offset: usize, declared: usize },
    UnexpectedDepthLength { offset: usize, declared: usize },
    InvalidInstrument { offset: usize },
    InvalidPrice { offset: usize, level: usize },
    UnexpectedAfterDisconnect { offset: usize },
}

impl fmt::Display for DecodeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "invalid Dhan depth frame: {self:?}")
    }
}

impl std::error::Error for DecodeError {}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum DecodedPacket {
    Side(Box<DepthSideUpdate>),
    Disconnect { reason_code: Option<u16> },
    Unsupported { code: u8, declared_length: u16 },
}

fn fixed_price(price: f64, offset: usize, level: usize) -> Result<i64, DecodeError> {
    let scaled = (price * 1_000_000_000.0).round();
    if !price.is_finite()
        || price < 0.0
        || !scaled.is_finite()
        || scaled >= 9_223_372_036_854_775_808.0
    {
        return Err(DecodeError::InvalidPrice { offset, level });
    }
    Ok(scaled as i64)
}

/// All-or-nothing parsing: a bad trailing packet rejects the entire frame,
/// so callers never apply a prefix from a malformed WebSocket message.
pub fn decode_frame(
    frame: &[u8],
    receive_timestamp_ns: i64,
    feed_session_id: &str,
    frame_id: u64,
) -> Result<Vec<DecodedPacket>, DecodeError> {
    if frame.is_empty() {
        return Err(DecodeError::TruncatedHeader { offset: 0 });
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
        let declared = i16::from_le_bytes([frame[offset], frame[offset + 1]]);
        if declared < HEADER_BYTES as i16 {
            return Err(DecodeError::InvalidLength { offset, declared });
        }
        let length = declared as usize;
        if length > frame.len() - offset {
            return Err(DecodeError::TruncatedPacket {
                offset,
                declared: length,
            });
        }
        let code = frame[offset + 2];
        let packet = &frame[offset..offset + length];
        match code {
            41 | 51 => {
                if length != SIDE_BYTES {
                    return Err(DecodeError::UnexpectedDepthLength {
                        offset,
                        declared: length,
                    });
                }
                let segment = packet[3];
                let security_id =
                    i32::from_le_bytes(packet[4..8].try_into().expect("header checked"));
                let exchange_segment = match segment {
                    1 => "NSE_EQ",
                    2 => "NSE_FNO",
                    _ => return Err(DecodeError::InvalidInstrument { offset }),
                };
                if security_id <= 0 {
                    return Err(DecodeError::InvalidInstrument { offset });
                }
                let mut levels = [DepthLevel::default(); BOOK_LEVELS];
                for (index, level) in levels.iter_mut().enumerate() {
                    let start = HEADER_BYTES + index * 16;
                    let price = f64::from_le_bytes(
                        packet[start..start + 8].try_into().expect("length checked"),
                    );
                    let quantity = u32::from_le_bytes(
                        packet[start + 8..start + 12]
                            .try_into()
                            .expect("length checked"),
                    );
                    let orders = u32::from_le_bytes(
                        packet[start + 12..start + 16]
                            .try_into()
                            .expect("length checked"),
                    );
                    *level = DepthLevel {
                        price_nanos: fixed_price(price, offset, index)?,
                        quantity: u64::from(quantity),
                        order_count: Some(orders),
                    };
                }
                packets.push(DecodedPacket::Side(Box::new(DepthSideUpdate {
                    instrument: Instrument {
                        venue: "NSE".into(),
                        exchange_segment: exchange_segment.into(),
                        security_id: security_id.to_string(),
                        symbol: String::new(), // Not supplied in Dhan's binary header.
                    },
                    side: if code == 41 {
                        DepthSide::Bid
                    } else {
                        DepthSide::Ask
                    },
                    levels,
                    receive_timestamp_ns,
                    feed_session_id: feed_session_id.into(),
                    frame_id,
                    packet_offset: offset as u32,
                })));
            }
            50 => {
                // Dhan's prose conflicts on the disconnect body length. The
                // SDK reads the last header word; support an explicit u16 body
                // too, without interpreting any other bytes as a reason.
                let reason_code = if length == HEADER_BYTES {
                    u32::from_le_bytes(packet[8..12].try_into().expect("header checked"))
                        .try_into()
                        .ok()
                } else if length == HEADER_BYTES + 2 {
                    Some(u16::from_le_bytes(
                        packet[12..14].try_into().expect("length checked"),
                    ))
                } else {
                    None
                };
                packets.push(DecodedPacket::Disconnect { reason_code });
                if offset + length != frame.len() {
                    return Err(DecodeError::UnexpectedAfterDisconnect { offset });
                }
            }
            _ => packets.push(DecodedPacket::Unsupported {
                code,
                declared_length: length as u16,
            }),
        }
        offset += length;
    }
    Ok(packets)
}

/// A combined book is emitted only for adjacent Bid then Ask packets for the
/// same instrument in the same WebSocket frame and feed session. The SDK's
/// documented wire order supports this grouping, but no atomic exchange
/// snapshot guarantee is implied.
pub fn assemble_frame(packets: &[DecodedPacket]) -> Vec<DepthEvent> {
    let mut events = Vec::new();
    for pair in packets.windows(2) {
        let [DecodedPacket::Side(bid), DecodedPacket::Side(ask)] = pair else {
            continue;
        };
        if bid.side == DepthSide::Bid
            && ask.side == DepthSide::Ask
            && bid.instrument == ask.instrument
            && bid.feed_session_id == ask.feed_session_id
            && bid.frame_id == ask.frame_id
            && bid.receive_timestamp_ns == ask.receive_timestamp_ns
        {
            events.push(DepthEvent {
                instrument: bid.instrument.clone(),
                exchange_timestamp_ns: None,
                receive_timestamp_ns: bid.receive_timestamp_ns,
                sequence: None,
                bids: bid.levels,
                asks: ask.levels,
            });
        }
    }
    events
}

#[cfg(test)]
mod tests {
    use super::*;

    fn side(code: u8, segment: u8, id: i32) -> Vec<u8> {
        let mut bytes = Vec::with_capacity(SIDE_BYTES);
        bytes.extend_from_slice(&(SIDE_BYTES as i16).to_le_bytes());
        bytes.push(code);
        bytes.push(segment);
        bytes.extend_from_slice(&id.to_le_bytes());
        bytes.extend_from_slice(&999_u32.to_le_bytes()); // Ignored wire sequence.
        for i in 0..BOOK_LEVELS {
            bytes.extend_from_slice(&(100.125 + i as f64).to_le_bytes());
            bytes.extend_from_slice(&(10 + i as u32).to_le_bytes());
            bytes.extend_from_slice(&(2 + i as u32).to_le_bytes());
        }
        bytes
    }

    fn parse(frame: &[u8]) -> Result<Vec<DecodedPacket>, DecodeError> {
        decode_frame(frame, 123, "session-a", 7)
    }

    #[test]
    fn golden_bid_and_ask_keep_exactly_twenty_original_levels() {
        for (code, expected_side) in [(41, DepthSide::Bid), (51, DepthSide::Ask)] {
            let packets = parse(&side(code, 1, 1333)).unwrap();
            let DecodedPacket::Side(update) = &packets[0] else {
                panic!("side expected")
            };
            assert_eq!(update.side, expected_side);
            assert_eq!(update.instrument.security_id, "1333");
            assert_eq!(update.levels.len(), 20);
            assert_eq!(update.levels[0].price_nanos, 100_125_000_000);
            assert_eq!(update.levels[19].price_nanos, 119_125_000_000);
            assert_eq!(update.levels[19].quantity, 29);
            assert_eq!(update.levels[19].order_count, Some(21));
            assert_eq!(update.packet_offset, 0);
        }
    }

    #[test]
    fn concatenated_instruments_pair_only_adjacent_matching_sides() {
        let mut frame = Vec::new();
        for id in [1333, 2885] {
            frame.extend(side(41, 1, id));
            frame.extend(side(51, 1, id));
        }
        let packets = parse(&frame).unwrap();
        assert_eq!(packets.len(), 4);
        assert_eq!(assemble_frame(&packets).len(), 2);
        let DecodedPacket::Side(last) = &packets[3] else {
            panic!("side expected")
        };
        assert_eq!(last.packet_offset, (3 * SIDE_BYTES) as u32);
    }

    #[test]
    fn interleaved_or_unpaired_sides_do_not_make_books() {
        let mut frame = side(41, 1, 1333);
        frame.extend(side(41, 1, 2885));
        frame.extend(side(51, 1, 1333));
        frame.extend(side(51, 1, 2885));
        assert!(assemble_frame(&parse(&frame).unwrap()).is_empty());
        assert!(assemble_frame(&parse(&side(41, 1, 1333)).unwrap()).is_empty());
    }

    #[test]
    fn disconnect_breaks_pairing_and_parses_reason() {
        let mut frame = side(41, 1, 1333);
        let mut disconnect = Vec::from(12_i16.to_le_bytes());
        disconnect.extend([50, 0]);
        disconnect.extend(0_i32.to_le_bytes());
        disconnect.extend(805_u32.to_le_bytes());
        frame.extend(disconnect);
        frame.extend(side(51, 1, 1333));
        assert!(matches!(
            parse(&frame),
            Err(DecodeError::UnexpectedAfterDisconnect { .. })
        ));
        let packets = parse(&frame[..SIDE_BYTES + HEADER_BYTES]).unwrap();
        assert_eq!(
            packets[1],
            DecodedPacket::Disconnect {
                reason_code: Some(805)
            }
        );
        assert!(assemble_frame(&packets).is_empty());
    }

    #[test]
    fn malformed_frames_are_rejected_without_partial_output() {
        assert!(matches!(
            parse(&[1, 2]),
            Err(DecodeError::TruncatedHeader { .. })
        ));
        let good = side(41, 1, 1333);
        assert!(matches!(
            parse(&good[..331]),
            Err(DecodeError::TruncatedPacket { .. })
        ));
        let mut bad = good.clone();
        bad[0..2].copy_from_slice(&11_i16.to_le_bytes());
        assert!(matches!(
            parse(&bad),
            Err(DecodeError::InvalidLength { .. })
        ));
        let mut bad = good.clone();
        bad[0..2].copy_from_slice(&13_i16.to_le_bytes());
        assert!(matches!(
            parse(&bad),
            Err(DecodeError::UnexpectedDepthLength { .. })
        ));
        let mut bad = good.clone();
        bad.extend([1, 2, 3]);
        assert!(matches!(
            parse(&bad),
            Err(DecodeError::TruncatedHeader { .. })
        ));
        assert_eq!(
            parse(&vec![0; MAX_FRAME_BYTES + 1]),
            Err(DecodeError::OversizedFrame)
        );
        let mut prefixed = good;
        prefixed.extend([1, 2, 3]);
        assert!(parse(&prefixed).is_err());
    }

    #[test]
    fn invalid_identity_price_and_unknown_code_are_visible() {
        for (segment, id) in [(0, 1333), (3, 1333), (1, 0), (1, -1)] {
            assert!(matches!(
                parse(&side(41, segment, id)),
                Err(DecodeError::InvalidInstrument { .. })
            ));
        }
        for price in [f64::NAN, f64::INFINITY, -1.0, 1.0e20] {
            let mut packet = side(41, 1, 1333);
            packet[12..20].copy_from_slice(&price.to_le_bytes());
            assert!(matches!(
                parse(&packet),
                Err(DecodeError::InvalidPrice { .. })
            ));
        }
        let mut packet = side(77, 1, 1333);
        packet[12..20].copy_from_slice(&f64::NAN.to_le_bytes());
        assert_eq!(
            parse(&packet).unwrap(),
            vec![DecodedPacket::Unsupported {
                code: 77,
                declared_length: SIDE_BYTES as u16
            }]
        );
    }

    #[test]
    fn arbitrary_short_inputs_never_panic() {
        for len in 0..SIDE_BYTES * 2 {
            let bytes = vec![0xAA; len];
            let _ = parse(&bytes);
        }
        let mut seed = 0x9e37_79b9_u64;
        for len in 0..1024 {
            let mut bytes = vec![0_u8; len];
            for value in &mut bytes {
                seed ^= seed << 13;
                seed ^= seed >> 7;
                seed ^= seed << 17;
                *value = seed as u8;
            }
            let _ = parse(&bytes);
        }
    }
}
