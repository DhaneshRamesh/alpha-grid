# Phase 2C DATA_ONLY cockpit

The dashboard is an observer of the existing Rust engine. The local demo builds
a synthetic Dhan-compatible standard quote and 20-level depth fixture, decodes
it with the existing parsers, appends 962 normalized records to a checksummed
QJ01 journal with per-record fsync, then replays that journal through the
Phase 2B volume, candle and feature engine. The fixture has two instruments
and 120 simulated seconds beginning 14 November 2023 UTC. Its prices are
artificial. No authenticated feed, broker token, cloud VM or order endpoint is
used.

## Start and stop

Use Rust 1.99, Node 24, and two local terminals from the repository root:

```sh
# Terminal 1: build the fixture once, then replay it on localhost.
cargo run --locked --release -p quant-telemetry --bin telemetry_demo \
  --manifest-path rust/Cargo.toml -- \
  --journal .local/phase2c/demo.qj

# Terminal 2: serve the cockpit on localhost.
cd web
npm ci
npm run dev
```

Open `http://127.0.0.1:5173/`. The Rust gateway binds
`127.0.0.1:8765`; Vite proxies `/api` and `/ws`. Stop each terminal with
**Ctrl-C**. Replay pause, resume, 1x/2x/5x speed and restart are local-only
controls. Restart resets the engine and read model, then rereads the same
journal. A browser reconnect only requests a current snapshot; it never starts
another capture process or writes a second journal. The existing fixture is
reused if the path exists. Use a new journal path to build a fresh fixture.

Read APIs: `GET /api/snapshot`, `GET /api/health`,
`GET /api/instruments/{venue:segment:id}`, and `GET /ws` with WebSocket
upgrade. `POST /api/demo/control` is present only when the replay controls are
configured; other gateway instances return 404. The four accepted actions are
`pause`, `resume`, `restart`, and `speed` (1, 2 or 5). The UI uses them only in
REPLAY mode. No API places, changes or cancels orders.

## Contract and concurrency

`quant-telemetry` owns version 1 JSON envelopes. The source receive timestamp,
publication timestamp, fixed-point price and exchange/feed ordinals are decimal
strings, preserving nanosecond and integer precision in JavaScript. Source
time quality is explicit; the fixture's source clock has not been verified.
The browser TypeScript types are generated from committed Rust JSON schemas:

```sh
cargo run --locked -q -p quant-telemetry --bin schema_export \
  --manifest-path rust/Cargo.toml -- web/schemas
npm run schema:generate --prefix web
```

The collector must invoke `TelemetryPublisher::observe` only after a durable
append and successful engine derivation. Replay calls it after `process`, and
the counters distinguish replayed records from newly committed records. The
single-writer adapter atomically replaces a bounded per-instrument read slot.
A revision check gives each gateway snapshot a stable generation across all
slots; a busy read is skipped and retried on the next tick. The gateway never
locks the ingestion engine during network writes. Its `watch` channel keeps
only the newest display generation per client. A missed sequence or replay
epoch sends a full snapshot. Telemetry publication sequence is independent of
feed sequence and QJ01 record count. Presentation coalescing does not discard
journal records.

Limits: 1–50 configured instruments, at most 8 clients by default (configurable
to 32), 1–20 publications per second (10 default), 4 KiB incoming WebSocket
messages, 4 MiB outgoing messages, 240 candles per timeframe/instrument
bounded by 1,440 candles per instrument, and 200 presentation audit events.
Slow clients time out after two seconds per send and are disconnected. These
limits keep telemetry memory bounded, though 50-instrument full-snapshot size
and update cost have **not** been load-tested. The REST snapshot can be large;
public-network exposure requires a separate review. The gateway binds localhost
in the demo and accepts only local development origins for WebSockets and demo
controls. No wildcard CORS or credentials are used.

The cockpit keeps `N/A` distinct from zero. Previous close appears only after
a previous-close source packet. It shows all six quote-sampled candle intervals,
provisional/finalized state, defensible volume only, 20 levels per book side,
actual Phase 2B features, capture/journal/replay state and quality flags.
Derived side pairing is labelled non-atomic. The audit display is bounded;
the journal remains authoritative. Kalman, OU, Hawkes, P&L and execution
signals are not implemented.

## Local measurements

The release-mode benchmark used the 962-record fixture on a local **arm64
macOS** host. The exact CPU model was unavailable in this sandbox. These are
offline replay measurements, **not Utho or Dhan results**. They exclude QJ01
fsync because the source journal was already durable. Each comparison used the
same engine configuration, journal, raw JSONL measurement output and a gateway
at 10 Hz. The “connected” case used a draining WebSocket client, not the full
React renderer. Measurements use `Instant` for elapsed time. The separate
sampled `publication` metric is the most recent processed event to gateway
snapshot, not all source events to browser pixels.

| Workload | Client | Records | Mean records/s | Total processing p50 / p95 / p99 | Gateway publication p95 / p99 |
| --- | --- | ---: | ---: | --- | --- |
| Unpaced, 5 runs | absent | 4,810 | 9,172 | 0.111 / 0.156 / 0.171 ms | 10 samples; tail unsupported |
| Unpaced, 5 runs | connected | 4,810 | 9,167 | 0.110 / 0.155 / 0.166 ms | 10 samples; tail unsupported |
| Paced 200 records/s, 3 runs | absent | 2,886 | 200.1 | 0.455 / 1.001 / 1.268 ms | p95 3.31–4.39 ms; p99 91–92 ms |
| Paced 200 records/s, 3 runs | connected | 2,886 | 200.1 | 0.435 / 0.960 / 1.289 ms | p95 5.10–6.58 ms; p99 89–92 ms |

Each paced run yielded 49 publication samples, 147 per connection state.
Across all 16 trials, 15,392 journal records were replayed and the benchmark
reported zero engine, adapter and WebSocket failures. The connected trial
received 115 messages in total. Unpaced throughput differed by 0.06%, well
within run-to-run variation; this sample does not prove equivalence under
large books or 50 instruments. The 250 ms **browser freshness** target remains
unverified: there is no monotonic cross-process browser-pixel measurement.
The UI’s wall-clock message-lag estimate is diagnostic only. Existing Phase 2B
fsync-every-record measurements remain the relevant durability bottleneck;
this benchmark does not improve or remeasure them.

Reproduce and preserve raw output:

```sh
mkdir -p outputs/phase2c
cargo build --locked --release -p quant-telemetry --bin telemetry_bench \
  --manifest-path rust/Cargo.toml
rust/target/release/telemetry_bench .local/phase2c/demo.qj \
  outputs/phase2c/unpaced-raw.jsonl 5 0 > outputs/phase2c/unpaced-summary.jsonl
rust/target/release/telemetry_bench .local/phase2c/demo.qj \
  outputs/phase2c/paced-raw.jsonl 3 200 > outputs/phase2c/paced-summary.jsonl
```

The committed [unpaced](../../docs/benchmarks/phase2c/unpaced-raw.jsonl.gz)
and [paced](../../docs/benchmarks/phase2c/paced-raw.jsonl.gz) raw JSONL
artifacts are losslessly gzip-compressed. Their adjacent JSONL summaries
contain run-level percentiles and failure counts. The raw gzip SHA-256 values
are `a6e999689f99846c1f7cd7fad25cf75b71e793b96e974a13a65bdb7810c70ed5`
and `ddb8d39e8f45b4ca5510b68d557dbef4e98b43a4c755c6339a00c195ecb11afb`.

Screenshots: [overview](../../docs/screenshots/phase2c-overview.png),
[candles](../../docs/screenshots/phase2c-candles.png), and
[mobile](../../docs/screenshots/phase2c-mobile.png). They show the synthetic
historical replay and are not live market data.

## Remaining limits

- No authenticated Dhan market session, exchange clock calibration, or live
  feed continuity measurement has been performed.
- No sustained 50-instrument or full React-renderer performance trial exists.
  The Phase 2B fsync-per-record capacity limit still applies to live capture.
- Disk free space, retention policy and QJ01 recovery counts are not wired
  into this replay dashboard; they display `N/A`.
- No local browser-pixel latency distribution or long soak test was run.
- This release provides monitoring only. It has no predictive trading model,
  order API, broker session or live execution path.
