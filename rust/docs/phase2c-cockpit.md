# Phase 2C DATA_ONLY cockpit

## Current single-instrument quote preparation (2026-10-26)

The current `telemetry_local` entry point remains disconnected and makes no
Dhan request. The existing Dhan v2 standard decoder accepts the documented
50-byte little-endian quote packet (response code 4). A scoped
`StandardCapture` rejects any instrument other than NSE_EQ security ID
2885 before writing a normalized observation. Given an already authenticated socket and an independently
verified session calendar, `run_authenticated_standard_socket` sends one quote
subscription (request code 17), timestamps each received frame locally, uses
the existing QJ01 file sync and engine, then publishes to the existing gateway.
No caller in the runtime opens that socket during this phase. Controlled packet
fixtures in tests are software checks, never market evidence.

The cockpit can display Dhan-tagged price, cumulative traded volume, local
receipt and source last-trade times, freshness and explicit NO TRADE reasons.
Source-time meaning and upstream packet continuity remain unverified. A local
decode or identity failure stops the feed; a disconnect makes the last quote
stale, and restart requires a new gateway/feed session. Scoped capture syncs an
exact binary-frame QJ01 record first, containing its SHA-256, local receipt time,
feed session and frame ID. Normalized records link by feed session and frame ID
and retain packet offset, ordinal, source last-trade time and protocol. Raw
records are omitted from the JSONL research export. Capture stops at a 64 MiB
QJ01 cap, on a recovered tail, on raw write/hash failure, or on stale receipt;
stale/rejected frames retain raw evidence without publishing a quote. The
source last-trade timestamp does not measure local receipt or prove exchange
clock accuracy. Dhan's volume field is retained as a cumulative observation,
not an individual executed trade.

Native Windows synthetic tests wrote and recovered the QJ01 raw record under
an NTFS directory whose directory and inherited file DACLs passed the existing
private-storage verifier. The quote-feed runtime has no authenticated socket
caller and does not yet enforce this private-path preflight itself; an operator
must not start a trial against a broadly accessible journal path. There are no
previously captured authentic binary feed frames in the inspected local data.

On native Windows, segmented QJ01 syncs file data and publishes its manifest
with atomic rename, while the journal validates directory paths instead of
calling unsupported directory `sync_all`. Disk free-space checks use the native
Windows API and still fail closed. POSIX keeps directory sync. Windows power
loss durability for new directory entries and renamed manifests remains
unproven; the cockpit reports `FILE_SYNC_ONLY`, never an equivalent POSIX
directory guarantee. Interior checksum corruption still blocks recovery.

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

The opt-in `telemetry_local --dhan-quote-readonly` path now calls the Dhan v2
standard WebSocket only after private NTFS, master identity, verified current
session, clock and credential preflight. It is limited to NSE_EQ RELIANCE
security ID 2885 and Quote RequestCode 17, without reconnect, depth or orders.
The required token and client ID are assembled into the TLS connection URL in
memory and masked from returned errors. Raw binary frames are committed to
the existing 64 MiB QJ01 before normalized publication. OI is raw-only;
quote-derived candles are withheld. The ordinary-user clock gate and the
October 27 OBSERVE_ONLY identity/session/private-NTFS preflight passed offline;
dated RELIANCE CAS classification remains unverified. The Codex sandbox user
cannot query W32Time or use its older `data/journal` path as the ordinary user.
The ordinary-user journal is `data/.secrets/quote-journal/reliance-quote-2885.qj`.
No authentic quote has been verified. Windows file flushing is supported;
power-loss durability of directory metadata remains unproven.

### Authorized October 28 OBSERVE_ONLY launch

The October 27 one-attempt trial reached the local gateway but its TLS worker
panicked before any quote was verified. The corrected caller is offline-tested
only. The user has authorized exactly one new, read-only NSE_EQ RELIANCE/2885
Quote-17 attempt for October 28, 2026, conditional on fresh startup preflight.
The October 27 approval is consumed. An unsuccessful WebSocket attempt on
October 28 does not authorize a retry. A preflight failure opens no socket;
correct that prerequisite and rerun the preflight before using the one attempt.

Run from the repository root in an ordinary, non-elevated PowerShell. The
caller reads process environment variables; it does not load `.env` or the
private token file. The cockpit key is stored in the existing private, ignored
NTFS `data/.secrets/cockpit-session-key` file. The numeric Dhan client ID
still comes from your existing secure source. The masked prompt below keeps
its value out of command history and output.
The saved Dhan access token is loaded from the existing private callback store;
its presence does not establish validity or entitlement.

This dated manual quote-feed procedure predates automatic cockpit sign-in and
is not a current launch command. Use the single-start launcher below for the
read-only cockpit. A future feed trial needs separate authorization and a
reviewed launch path; opening Vite alone no longer prompts for a key.

```powershell
$env:PYTHONPATH = 'src'
$env:ALPHA_GRID_UI_SESSION_SECRET = & .\.venv\Scripts\python.exe -c "from pathlib import Path; from quant.acquisition.dhan import _private_directory, _verify_windows_private_acl; p=Path('data/.secrets/cockpit-session-key'); _private_directory(p.parent); _verify_windows_private_acl(p); print(p.read_text(encoding='ascii'))"
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($env:ALPHA_GRID_UI_SESSION_SECRET)) { throw 'Private cockpit key unavailable' }
try {
  $client = Read-Host 'Dhan client ID' -AsSecureString
  $env:DHAN_CLIENT_ID = [System.Net.NetworkCredential]::new('', $client).Password
  Remove-Variable client
  $env:DHAN_ACCESS_TOKEN = [IO.File]::ReadAllText(
    (Resolve-Path -LiteralPath 'data/.secrets/dhan-access-token')
  ).Trim()
  $journal = (Resolve-Path -LiteralPath 'data/.secrets/quote-journal/reliance-quote-2885.qj').Path
  foreach ($name in @('ALPHA_GRID_UI_SESSION_SECRET', 'DHAN_CLIENT_ID', 'DHAN_ACCESS_TOKEN')) {
    if ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($name, 'Process'))) {
      throw "$name missing"
    }
  }
  $env:ALPHA_GRID_ENABLE_DHAN_QUOTE = '1'
  & .\rust\target\debug\telemetry_local.exe `
    --dhan-quote-readonly --security-id 2885 `
    --approval-ledger .local/paper-approvals.qp `
    --quote-journal $journal `
    --instrument-master data/instruments/dhan-scrip-master-2026-10-24.csv `
    --master-manifest data/instruments/dhan-scrip-master-2026-10-24.manifest.json `
    --calendar data/config/reliance-observation-2026-10-28.json
} finally {
  Remove-Item Env:ALPHA_GRID_UI_SESSION_SECRET,Env:DHAN_CLIENT_ID,Env:DHAN_ACCESS_TOKEN,Env:ALPHA_GRID_ENABLE_DHAN_QUOTE,Env:PYTHONPATH -ErrorAction SilentlyContinue
}
```

### Single-start localhost cockpit

The ordinary-user private NTFS key at `data/.secrets/cockpit-session-key` is
loaded into the backend process by:

```powershell
powershell -NoProfile -File tools/start-cockpit.ps1
```

The one foreground launcher starts the existing Rust gateway, Vite cockpit
and local settings/status service, then opens the localhost page. It blocks
duplicate occupied ports. Press **Ctrl-C in that PowerShell** to stop only
the three processes it started. Startup errors are printed locally and
service logs are under ignored `.local/alpha-grid-logs/`; the cockpit shows
disconnected or unavailable states if a backend fails. The launcher starts
only `DATA_ONLY / NO TRADE`; it does not load the Dhan token, call a Dhan API
or open a feed. The historical panel reads the pinned, private October 15-16
RELIANCE response offline, checks its hash and provenance, and marks October
27 and daily candles unavailable. Research and execution remain blocked.

### Persistent read-only Online mode (2026-10-28 implementation)

`tools/start-cockpit.ps1 -Online` uses the same localhost ports and private
credential store. It requests only NSE_EQ RELIANCE/2885 Quote-17 and the five
already approved read-only account endpoints. The Rust caller reruns its
ordinary-session, identity, clock and private-QJ01 checks before each process
start. The existing account reservation limits each endpoint to one request
per five minutes. Quotes remain OBSERVE_ONLY / NO TRADE and stop before the
unverified closing-auction transition. The launcher may restart the Rust
process at most twice after a narrow set of transient transport failures,
with 2- and 5-second backoff. A new process has a new feed session and must
pass the same preflight; authenticated Dhan errors, decoded disconnect packets,
bad clock, packet rejection and journal failure do not qualify for retry.
An account-service reauthentication-required status also stops the feed.
The Rust gateway stays on port 8765; no second local feed listener is used.

`-Offline` retains the verified local account cache without Dhan refresh.
Default startup retains DATA_ONLY with the existing approved account refresh.
The Online flag does not enable orders, research eligibility or paper fills.
Each future trading day still needs its own independently verified dated
ordinary-session evidence. The private journal stops before its 64 MiB cap.

This checkout's PowerShell execution policy is `Restricted`, and Windows
Application Control currently rejects newly built Rust test executables
(`os error 4551`). The launcher parsed, the Rust caller built, and an
unmocked local DATA_ONLY gateway start/authenticated-snapshot/stop smoke passed,
but the full launcher and new Online quote path have not run. Do not treat
them as genuine Dhan verification or weaken system policy to run them.

The single-start launcher opens a tab with a one-use sign-in token. The page
exchanges it for the existing signed, HttpOnly, SameSite=Strict cookie and
immediately removes it from the browser URL. There is no browser key prompt.
Subsequent browser and ordinary backend restarts reuse the cookie for up to
seven days while the
same private backend key is present. “Forget this device” clears the browser
cookie; rotating the private backend key revokes all previously issued cookies.
The localhost HTTP cookie is protected from JavaScript, but local users with
browser-profile access or access to the private backend key can impersonate
that session. Keep the Windows account and browser profile private.

This dated calendar allows only October 28 before 15:15 IST. The caller stops
the quote capture after at most 60 seconds, and by 15:14 IST at the latest.
It rechecks clock, current master, session, private storage, approval ledger
and credential presence before its sole connection attempt. The cockpit server
remains open afterward; inspect its NO TRADE state, then press **Ctrl-C** to
exit and clear the process environment in the `finally` block. Run the block
only when the fresh clock and session gates pass. W32Time root dispersion
briefly exceeded the unchanged 0.1-second limit after midnight, then measured
0.050 seconds at 00:05 IST. The caller must check clock health again at launch.

- No authenticated Dhan market session, exchange clock calibration, or live
  feed continuity measurement has been performed.
- No sustained 50-instrument or full React-renderer performance trial exists.
  The Phase 2B fsync-per-record capacity limit still applies to live capture.
- Disk free space, retention policy and QJ01 recovery counts are not wired
  into this replay dashboard; they display `N/A`.
- No local browser-pixel latency distribution or long soak test was run.
- This release provides monitoring only. It has no predictive trading model,
  order API, broker session or live execution path.

## Approved read-only account sync, 2026-10-28

`powershell -NoProfile -ExecutionPolicy Bypass -File tools/start-cockpit.ps1`
starts the three existing localhost services and now enables only the five
approved read-only Dhan GETs: `/v2/fundlimit`, `/holdings`, `/positions`,
`/orders` and `/trades`. Ctrl+C in that foreground PowerShell stops them.
The Node account adapter reads the existing private callback token file;
the launcher reads `DHAN_CLIENT_ID` from private NTFS
`data/.secrets/dhan-client-id` into that child process only.
The existing private NTFS ACL, Dhan-provided expiry metadata and token hash
linkage are checked before any account request. Manual consent renewal is
documented in `docs/dhan-api-key-callbacks.md`; restart the launcher after
renewal. Local expiry checks do not prove future Dhan entitlement.

Each endpoint reserves an attempt in private NTFS state before network I/O.
The minimum interval is five minutes per endpoint across restarts, with
sequential requests started at least one second apart and no retry loop.
Exact successful and error HTTP bodies are immutable private files with
SHA-256, method, URL, receipt time, status and client identity metadata.
A 401 stops refreshes until manual renewal and restart; private-storage
failure stops further requests. A failed panel leaves prior verified data
marked stale. The browser receives only allowlisted account fields after
the existing signed-cookie check. Today's orders/trades are not presented
as current on a later IST session. Historical trades, ledger, market
history and live feed remain outside this authorization.
