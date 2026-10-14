# Phase 2B DATA_ONLY market engine

The official [Dhan standard-feed layout](https://dhanhq.co/docs/v2/live-market-feed/)
and [SDK parser](https://github.com/dhan-oss/DhanHQ-py/blob/main/src/dhanhq/marketfeed.py)
are audited in [standard-feed.md](standard-feed.md). The standard and 20-depth
decoders remain separate. `StandardCapture::on_frame` takes a caller-supplied
receive timestamp captured immediately at WebSocket receipt; it decodes the
whole frame before writing any packet. Unknown packet codes and malformed
frames produce visible errors and counters. Valid normalized events are
appended and fsynced before derived output is published. A journal or engine
failure poisons that capture instance; recovery requires investigation and
replay. This milestone does not open a Dhan connection.

## Volume and candles

`VolumeTracker` keeps one cumulative baseline per venue/segment/security ID
and injected market session. It emits no increment for the first quote,
session change, feed reconnect, disconnect or cumulative reset. Repeated
cumulative volume emits zero. Out-of-order observations do not rewind the
baseline. LTQ is reported by the source and may repeat; it is not a separate
trade print. Displayed book quantity is never treated as traded volume.

Six bars (1s, 5s, 15s, 1m, 5m, 15m) update on every price observation.
They sample LTP, so their OHLC values are **quote-sampled**, not verified
trade-by-trade OHLC. `observed_volume_delta` sums only intervals wholly inside
the bar. `volume` is null if any observed interval is unknown or crosses a bar
edge. A feed reconnect finalizes open bars as incomplete and resets rolling
price/volume relationships. Quiet intervals create no synthetic bars.
Configurable receive-time or
source-LTT event time and a bounded lateness watermark determine finalization.
An event arriving after finalization is rejected explicitly; no silent bar
revision occurs. The caller must inject the exact market-session calendar,
including holidays and special sessions. Source LTT has seconds precision and
is not an exchange-clock latency measurement.

## Feature definitions and cost

Each update touches one instrument, six open bars and bounded rolling rings.
No historical journal scan occurs during a state update. With window size
`W`, storage is O(W) per instrument plus open bars. Running sums/squares and
cross-products make rolling statistics O(1) per observation.

| Field | Definition |
| --- | --- |
| Midprice | (best bid + best ask) / 2 |
| Absolute spread | best ask − best bid |
| Relative spread | spread / midprice |
| Microprice | (ask price × best bid quantity + bid price × best ask quantity) / (bid quantity + ask quantity) |
| Level-weighted depth | sum(quantity at level `i` / `i`) for each side, levels 1–20 |
| Bid/ask imbalance | (sum bid quantity − sum ask quantity) / (sum bid + sum ask quantity), levels 1–20 |
| Snapshot OFI | top-level bid contribution plus ask contribution from consecutive paired book snapshots; price improvement contributes new quantity, unchanged price contributes quantity change, worsening price removes old quantity |
| Price return | current sampled LTP / prior sampled LTP − 1 |
| Rolling volatility | sample standard deviation of bounded sampled returns |
| Rolling volume activity | mean of defensible cumulative-volume deltas in the bounded window |
| Price/volume relationship | Pearson correlation of paired sampled return and cumulative-volume delta, null until at least 3 nondegenerate pairs |

Snapshot OFI measures **aggregated snapshot changes**, not individual order
adds/cancellations or queue position. Cross-feed clocks are unverified. Book
features are excluded when the latest depth/price receive timestamps differ
by more than the configured freshness limit; otherwise
`cross_feed_uncertain=true` still signals that simultaneity is unproven.

## Contracts for a future read-only telemetry gateway

`MarketStateSnapshot`, `QuantFeatureSnapshot`, `CandleSnapshot`, and
`EngineHealthSnapshot` carry schema version 1, instrument, processing and
source timestamps, provenance, freshness and quality. A gateway should expose
a full per-instrument snapshot on request, then stream versioned updates.
Market state and lightweight features update on every relevant event;
provisional bars update on observed prices; finalized bars publish only when
the watermark advances or a session ends. Disconnect/reconnect and quality
transitions must remain visible. A future presentation channel may coalesce
state/feature/provisional-bar updates, but raw normalized market and
session/disconnect events must stay durable. Finalized candles are
reconstructible from that journal; a gateway must deliver them or offer a
full-snapshot refresh after a missed presentation update. UI backpressure
must not slow journal ingestion.

The existing QJ01 journal keeps historical Depth/Trade/DepthSide Postcard
tags 0/1/2; new Price/Quote/Session variants append tags 3/4/5. Legacy
serialization regression tests cover all prior variants. Replayed source
events through the same calendar/configuration produce identical derived
outputs. `append_and_process` fsyncs before publishing; if a derivation fails
after fsync, the bad source event remains durable and the engine poisons
itself. Exactly-once publication across an external gateway is not claimed.

## Offline benchmark

From the repository root (release build, no network):

```sh
cargo build --locked -p quant-engine --bin feature_bench --release --manifest-path rust/Cargo.toml
rust/target/release/feature_bench --duration-seconds 10 --instrument-count 20 --queue-limit 2000 --mode compute --output benchmark-data/phase2b-compute.jsonl
rust/target/release/feature_bench --duration-seconds 10 --instrument-count 20 --queue-limit 2000 --mode durable --output benchmark-data/phase2b-durable.jsonl
```

The generator offers 100, 500, 1,000, 5,000 and 10,000 **synthetic frames/s**
per trial, with four quote cycles and one paired 20-depth cycle per
instrument. A depth frame creates two side records and one derived book, all
journaled in durable mode. A bounded queue rejects and stops offering when
full; accepted work drains and is accounted for. `compute` excludes journal
latency; `durable` fsyncs every record. Raw per-frame JSONL and per-rate QJ01
files are stored beside a `*.summary.json` index. Measurement uses monotonic
elapsed clocks; receive timestamps are wall-clock provenance only. Summary
percentiles are nearest rank. `sustained=false` if processing trails the
offered interval by more than 10%, the queue rejects a frame, or processing
fails. The program exits nonzero on any unsustained trial. Peak RSS uses
`getrusage`; it is process-wide high-water memory, not trial-local memory.

Local macOS measurements on 2026-10-14 with 20 instruments, 2-second compute
trials, and 1-second fsync-every-record trials (short samples, **not Utho**):

| Offered frames/s | Compute processed/s | Compute p99 decode+state | Durable processed/s | Durable p99 fsync | Durable sustained? |
| ---: | ---: | ---: | ---: | ---: | --- |
| 100 | 100 | 0.121 ms | 100 | 4.97 ms | yes |
| 500 | 500 | 0.048 ms | 260 | 3.38 ms | no; queue grew to 228 |
| 1,000 | 1,000 | 0.043 ms | 248 | 3.41 ms | no; queue grew to 723 |
| 5,000 | 5,000 | 0.027 ms | 246 | 4.21 ms | no; 1 rejected frame |
| 10,000 | 10,000 | 0.010 ms | 254 | 4.95 ms | no; 1 rejected frame |

Compute figures come from ignored
`benchmark-data/phase2b-compute-20261014.jsonl`; durable figures come from
`benchmark-data/phase2b-durable-streamed-20261014.jsonl`. Both have companion
summary files. They show a local fsync-per-record bottleneck;
they do not establish live Dhan feed capacity, Utho performance, or trading
latency. At 100/s, 1-second p99 has only one tail sample. Test longer and
compare explicit group-commit durability windows before a capacity decision.
No 15-minute soak or authenticated live-feed validation was performed here.
