# Dhan 20-level depth: evidence and capture contract

Sources: [Dhan full-depth specification](https://dhanhq.co/docs/v2/full-market-depth/) and [official Python SDK decoder](https://github.com/dhan-oss/DhanHQ-py/blob/main/src/dhanhq/fulldepth.py). This applies only to `twentydepth`.

| Wire field | Evidence | Rust handling |
| --- | --- | --- |
| WebSocket endpoint and query `token`, `clientId`, `authType=2` | Dhan, Establishing Connection | Only feature/runtime gated read-only mode. Environment credentials are never logged. |
| Subscription `RequestCode=23`, `InstrumentCount`, `InstrumentList` | Dhan, Adding Instruments | 1–50 unique positive IDs in NSE_EQ/NSE_FNO; re-sent after reconnect. |
| Header bytes 1–2, signed 16-bit packet length | Dhan, Response Header; SDK `<hBBiI` | Little-endian, includes 12-byte header; depth packet exactly 332 bytes. |
| Header byte 3, response code | Dhan, Response Header | 41=bid, 51=ask, 50=disconnect; others explicitly unsupported. |
| Header byte 4, exchange segment | Dhan annexure; SDK | 1=NSE_EQ, 2=NSE_FNO for depth. Others rejected. |
| Header bytes 5–8, security ID | Dhan, Response Header; SDK | Little-endian signed 32-bit, positive. No symbol inferred. |
| Header bytes 9–12, message sequence | Dhan explicitly says “to be ignored” | Not used for loss detection, pairing or normalized sequence. |
| 20 levels: f64 price, u32 quantity, u32 order count (16 bytes each) | Dhan, Depth Packet; SDK `<dII` | Original level order kept. Price rounded to INR × 10⁹ with finite/nonnegative/range checks. |
| Concatenated bid/ask packet order | Dhan note after Depth Packet | Adjacent bid then ask for same instrument in same frame may make a derived book; both sides journaled first. No atomic exchange snapshot guarantee. |
| Disconnect code 50/reason | Dhan, Feed Disconnect; SDK | Docs conflict: call header 8 bytes though common header is 12, and place reason at 13–14. SDK reads final header word. Decoder supports 12-byte header reason or 14-byte body reason; other layouts yield unknown reason. Bytes after disconnect rejected. |
| Exchange timestamp | Neither 20-level layout nor SDK supplies one | `None`. Receive time captured immediately on WebSocket delivery before parse. |

The feed carries aggregate price levels, quantities and order counts; it cannot reconstruct individual order additions, cancellations or fills. A complete book is a **derived pairing** with consistency unverified. Side updates retain session ID, frame ID and packet offset. Pairing never crosses a frame or reconnect. Malformed frames are rejected as a whole; unsupported codes are counted. The ignored wire sequence cannot prove feed continuity.

## Journal and archival contract

QJ01 retains framed Postcard + CRC32. `DepthSide` is appended after the historical Depth/Trade variants, preserving their discriminants. A regression test reads legacy variant shapes. New journals require the new reader; old readers may reject the new variant. Segments are numbered `0000000000000000.qj`, etc. A sealed segment is fsynced and listed in atomically replaced, fsynced `manifest.json` with byte length and CRC32. The final unlisted segment is active and recoverable. Sealed-data mismatch fails replay. Existing QJ01 recovery truncates an incomplete final record and reports it. CRC32 detects accidental corruption, not malicious modification.

The capture pipeline accepts a bounded WebSocket frame and acknowledges it only after all side and derived-book records are fsynced. `queue_accepted`, `kernel_written`, `fsync_confirmed` and `recovered` are distinct. Queue, write, sync and disk-guard errors stop capture visibly. `EveryEvent` syncs each record. Group policy may sync within a frame, but capture performs a final sync at each frame boundary, so this is presently frame-batched durability. A failed frame may have a durable prefix; replay is authoritative.

Disk budget and minimum free-space guards run before append. Rotation occurs by size or age; source segments are never deleted. `SegmentedReader` and `replay_segmented_each` stream records in manifest order without loading an entire journal. Parquet/Zstd archival is **deferred**. Future archival must write temporary files, fsync and atomically rename verified outputs, record source segment checksums and row counts, retry idempotently, and retain journals until a verified archive exists. No automatic deletion or upload exists.
