# Alpha Grid

Python 3.12+ and Rust research foundation for NSE market data and offline paper
trading. It includes a Dhan 20-level depth source, durable storage, feed health,
and an offline paper decision engine. It has no broker order API.

## Single-start read-only cockpit (Windows)

The UI opens on `DATA UNAVAILABLE / NO TRADE`. It does not show sample market
prices, stock picks, fills or performance. The separate Trading settings tab
settings tab saves maximum amount per trade, maximum daily loss, stop-loss and
profit target to ignored `.local/trading/settings.json`, with INR or percentage
options. The Trade desk shows the verified local October 15-16 RELIANCE
minute archive and the five approved read-only Dhan account panels when a
valid token is available. Current stock selection and live order execution
remain disabled. See the
[trading product brief](docs/trading-product-brief.md) for the intended workflow.

From an ordinary PowerShell in the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/start-cockpit.ps1
```

The launcher opens `http://127.0.0.1:5173/` and starts the existing Rust
gateway, React UI and local account/settings service. Press Ctrl+C in that
PowerShell to stop all three. The launcher signs in the opened localhost tab
once with a one-use startup token; the seven-day signed HttpOnly cookie restores
login across ordinary browser and cockpit restarts without a local key prompt.
Forget this device logs out; restart the launcher to sign in again. The Dhan
token is renewed separately through the private manual consent flow.
The authorized refresh scope is five GETs (funds, holdings, positions,
today's orders and today's trades), each at most once per five minutes.
The live feed, historical backfill, ledger and broker orders do not start.

## Local SHADOW / PAPER research

The [Phase 3A paper guide](rust/docs/phase3a-paper.md) describes the independent
paper risk gate, provisional cost model, conservative fill simulator, QP01
experience ledger and offline evaluator. Synthetic fixtures are isolated to
software tests. Replay/evaluation executables refuse normal runtime use until
QJ01 can be bound to authenticated Dhan source evidence.

## Local DATA_ONLY cockpit

The Phase 2C [Rust telemetry gateway and React cockpit](rust/docs/phase2c-cockpit.md)
are retained as presentation infrastructure. Replay/demo snapshots are
rejected by the cockpit; no real market results are available. The UI does
not touch the Utho VM.

The single-start command above shows DATA_ONLY / NO TRADE and never opens a
Dhan market-feed socket. Approval and revocation remain in the local QP01
ledger for future paper research only. The historical cache is descriptive;
it does not pass the causal research gate. See the
[cockpit runbook](rust/docs/phase2c-cockpit.md) and
[manual Dhan consent renewal](docs/dhan-api-key-callbacks.md).

## Set up

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
ruff check .
ruff format --check .
mypy
```

The `dev` extra includes all dependencies for the full test suite, including
DhanHQ, websockets and pandas. For exact resolved versions, use
`uv sync --extra dev --locked` with the committed `uv.lock`.

`quant-diagnostics` validates the environment settings and writes a JSON startup
log. Copy `.env.example` to `.env` if useful, then export its variables; the
application deliberately does not load `.env` files or embed credentials.
For Dhan API-key login and isolated order postbacks, see
[local callback setup](docs/dhan-api-key-callbacks.md).

## Collect Dhan 20-level depth

```sh
python -m pip install -e '.[dev,dhan]'
cp .env.example .env
# Edit .env to set DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN, and the instrument list.
set -a; . ./.env; set +a
python -m quant.apps.collect_depth
```

The collector appends each normalized event to the local journal before emitting
it as JSON on stdout. If journaling fails, collection stops; stdout is never
used as a fallback. Its
connection, reconnect, packet, event, malformed-packet, and disconnect counters
are available through `MarketDepthFeed.counters`; operational JSON logs go to
stderr. It reconnects with a capped exponential delay. Explicit Dhan server
rejection codes 806–809 stop the collector; connection errors retry with the
capped delay. No account is needed to run the tests.

The example config contains 20 NSE equity security IDs checked against Dhan's
[instrument master](https://dhanhq.co/docs/v2/instruments/). Replace or reduce
the list as needed; one connection supports up to 50 distinct NSE equity or
derivatives instruments. IDs should be checked again before use because the
instrument master can change.

Dhan sends separate 20-level bid and ask packets. The adapter pairs only recent
halves for the same instrument and drops malformed or incomplete packets. The
receive timestamp is captured immediately after each WebSocket receive, before
decoding. The [documented 20-level packet](https://dhanhq.co/docs/v2/full-market-depth/)
does not contain an exchange timestamp, so `exchange_timestamp` is normally
`null`; a timestamp is retained if a future decoder supplies one. The header
sequence is attached only when the two sides carry the same value. Dhan's
documentation says to ignore that sequence, so consumers should not use it as
a gap detector without live verification. Empty price levels are retained as
zero-price, zero-quantity placeholders so supplied 20-level frames remain 20
levels long.
These packet assumptions were checked against DhanHQ-py 2.3.0 and its published
protocol; they have not been checked against a live account session.

## Local event journal

`quant.storage.journal.EventJournal` writes checksummed, length-prefixed JSON
records under `data/journal/session=<session ID>/date=<Asia/Kolkata date>/events.qj`.
The session defaults to `NSE-YYYY-MM-DD` in India time, so a restart resumes the
same session. A session lock prevents two writers from interleaving records.
Records contain the collector version, session ID, security ID, optional symbol,
receive timestamp, event type, and the complete normalized event. Decimal prices
remain strings and timestamp text retains extra fractional digits when supplied
by a nanosecond-capable `datetime` subclass. `EventJournal.records()` reads the
JSON envelopes in date order.

On startup, the journal validates existing records and truncates only an
incomplete or bad-checksum final record. Recovery reports discarded tail bytes
and logs a warning; corruption before the final record stops startup. The
default `QUANT_JOURNAL_FSYNC=always` calls `fsync` after each record. `on_close`
and `never` trade crash durability for throughput. The journal exposes appended
record, byte, and fsync counts. Keep the journal volume persistent and monitor
free disk space; local `fsync` does not protect against loss of the entire host.

## Research Parquet replay

Install `.[research]` (or `.[dev]` for tests), set
`QUANT_JOURNAL_SESSION_ID` to the session to replay, then run
`python -m quant.apps.export_parquet`. The exporter reads only validated raw
journal records. It batches depth events by size (default 5,000) or receive-time
span (default 15 minutes), writes Arrow tables as Parquet 2.6 with Zstandard,
and partitions by India date, instrument, and hour. Price columns use exact
`decimal128(38,18)`; timestamps use UTC nanoseconds. Level counts and nullable
level columns preserve incomplete snapshots and missing order counts.
[PyArrow's Parquet writer](https://arrow.apache.org/docs/python/generated/pyarrow.parquet.write_table.html)
documents the version 2.6 nanosecond and Zstandard support.

Each replay builds a complete generation and atomically switches the
`QUANT_PARQUET_DIR/session=<session ID>` symlink when all files are durable.
Other sessions remain available. Failed runs leave the published dataset intact.
Replaying the same journal produces one final copy of each event. Older
generations remain in a hidden sibling directory so readers already using them
can finish; remove old generations during planned maintenance to reclaim space.
Trade events remain in the raw journal and are reported as
skipped by this depth-only exporter. If an event's price exceeds the schema's
decimal scale, export fails and the raw journal remains available.

Run `PYTHONPATH=src python benchmarks/parquet_benchmark.py --events 2000` for
conversion events/sec and compressed bytes/event on synthetic 20-level books.
The benchmark disables journal fsync only for input generation; production
collection retains its configured journal policy.

## Feed data quality

The collector monitors each configured instrument while it runs. Read the latest
status with `python -m quant.apps.feed_health` or select one with
`--instrument RELIANCE` (a symbol or security ID). The status file is written
atomically at `QUANT_HEALTH_STATUS_PATH`. The CLI marks an old status file as
disconnected, so a dead collector cannot leave a misleading healthy result.
Exit codes are 0 healthy, 1 degraded, and 2 stale/disconnected.

Per-instrument status includes last packet and event receive times, exchange
timestamp age/lag when available, packet and event counts, rolling events/sec,
suspected sequence gaps, malformed packets, reconnects, duplicates, missing
instruments, stale duration, and timestamp regressions. A duplicate requires a
repeated sequence number or exchange timestamp and matching payload; without
either identifier, identical book contents may be legitimate and are not called
duplicates. Freshness uses the last **unique** event, so repeated copies do not
hide a frozen book. Thresholds are set through the `QUANT_HEALTH_*` variables in
`.env.example`.

Structured logs include `session_id`, symbol, security ID, time, and anomaly
details. `feed_gap_opened` and `feed_gap_closed` include the interval endpoints
and a shared gap ID;
`feed_sequence_gap` includes the previous/current sequence and missing count.
This supports queries such as whether RELIANCE had a gap from 11:02 to 11:05.
Dhan documents its header sequence as ignorable, so sequence discontinuities
are counted and logged but do not degrade state by default. Set
`QUANT_HEALTH_SEQUENCE_GAPS_DEGRADE=true` only after verifying continuity on
your subscribed feed. The documented Dhan depth packet lacks an exchange
timestamp, so exchange lag remains unavailable unless the decoder supplies one.
Run the collector only during the intended market session; this monitor does not
currently apply an NSE holiday or auction calendar.

## Layout and contracts

- `quant.domain`: immutable instrument, depth, trade, and feed-health records.
- `quant.config`: typed settings loaded from `QUANT_` environment variables.
- `quant.logging`: UTC JSON logs for applications and services.
- `quant.feeds`: asynchronous normalized market-event source protocol and Dhan
  depth adapter. The adapter uses the official DhanHQ decoder and a direct
  WebSocket receive path to timestamp packets before decoding.
- `quant.storage`: asynchronous sink contract, raw journal, and replayable
  research Parquet exporter.
- `quant.health`: feed freshness and per-instrument data-quality monitoring.
- `quant.apps`: operational entry points only.

`DepthEvent` retains venue, exchange segment, instrument ID, optional exchange
timestamp, local receive timestamp, bid and ask levels, and optional sequence
number. Timestamps must include a timezone; prices use `Decimal`. Depth levels
are immutable tuples. Instrument IDs remain strings so exchange identifiers are
never silently coerced or truncated.

The intended later flow is market data → normalized events → research models →
risk and optimization → execution and OMS. Those later components are deliberately
outside this foundation. No live orders can be placed from this repository.

## Offline statistical research

The offline [forward candle observation guide](docs/forward-observations.md)
describes fixed 5- and 15-minute movement outcomes from verified one-minute
cash-equity candles, with point-in-time features and separate matured labels.
It requires independently evidenced candle timing and dated sessions.

Phase 3B research contracts and validation limits are in [rust/docs/phase3b-research.md](rust/docs/phase3b-research.md); the opt-in, read-only data plan is in [docs/research-acquisition.md](docs/research-acquisition.md). No trained market model or live order path is enabled.

Phase 3B-0 adds offline historical acquisition planning, local NSE archive
import, point-in-time identity and session quality, a checksummed OHLCV lake,
and separate candle research. See [docs/phase3b0-acquisition.md](docs/phase3b0-acquisition.md).
The active Phase 3B-1 acquisition path is Dhan-only and subscription-gated:
see [docs/phase3b1-dhan-pilot.md](docs/phase3b1-dhan-pilot.md).
