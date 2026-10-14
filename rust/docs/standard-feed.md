# Dhan standard-feed contract (offline audit)

Source: [Dhan v2 live feed](https://dhanhq.co/docs/v2/live-market-feed/) and
[official Python SDK](https://github.com/dhan-oss/DhanHQ-py/blob/main/src/dhanhq/marketfeed.py).
This decoder is separate from the 12-byte 20-depth decoder. No authenticated
session or live payload has been inspected.

All fields are little-endian. Standard header: code `u8` at 0, total packet
length `u16` at 1, segment `u8` at 3, security ID `i32` at 4. Supported NSE
segments: 1 equity and 2 F&O. A frame may concatenate packets. Known packet
lengths are exact; truncated/malformed frames are rejected atomically.

| Code | Length | Payload offsets from packet start |
| --- | ---: | --- |
| 2 ticker | 16 | LTP f32 8; last-trade epoch seconds i32 12 |
| 4 quote | 50 | LTP f32 8; LTQ i16 12; LTT i32 14; average price f32 18; cumulative volume i32 22; sell quantity i32 26; buy quantity i32 30; daily O/C/H/L f32 34/38/42/46 |
| 6 previous close | 16 | price f32 8; previous OI i32 12 |
| 8 full | 162 | quote fields through 33; OI/high OI/low OI i32 34/38/42; daily O/C/H/L f32 46/50/54/58; five 20-byte levels from 62, each bid/ask quantity i32, bid/ask order count i16, bid/ask price f32 |
| 7 status | 8 | no documented status value; emit unknown status signal |
| 50 disconnect | 10 | reason code u16 8; requires last packet in frame |

Prices are preserved as fixed-point INR × 10^9 from the transmitted f32
value; this does not add precision beyond f32. Source LTT is only second
resolution and is **not** a network-latency measurement. Zero in optional
price fields means unavailable. Ticker has no volume. Quote/full cumulative
volume is an aggregate observation, never an individual trade print. Full
packet's five levels are distinct from the 20-depth feed.

The SDK decodes some quantity fields as unsigned H/I although the public
layout calls them signed i16/i32; our parser conservatively rejects negative
values. The status code carries no verified open/closed state. These and exact
real-feed framing should be checked against sanitized live captures before
operational use. Unknown codes are surfaced explicitly rather than guessed.
