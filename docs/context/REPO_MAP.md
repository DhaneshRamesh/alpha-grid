# Alpha Grid context map

Inspected 2026-10-20 (Asia/Calcutta), against application HEAD `afed781`.
Scope: tracked repository, architecture documentation, manifests, CI, selected
implementation paths and test inventory. This is a navigation map, not a
line-by-line audit. Ignored datasets, secrets and remote systems were not inspected.

Product direction captured 2026-10-21: `docs/trading-product-brief.md` records
the owner's live Indian-equity trading workflow and the gap from this repository's
synthetic cockpit. It is an intended product brief, not current capability.
The implementation has `web/src/TradingWorkspace.tsx` and a loopback
`web/server/trading-service.mjs` settings and read-only account API. The
approved five-endpoint Dhan account refresh uses private credentials and
preserves original responses. The single-start launcher opens the cockpit
with a one-use local sign-in that obtains the existing signed HttpOnly cookie;
no browser key prompt remains. No market feed, OpenAI runtime or broker order
execution is connected in this checkout.

## Purpose and current state

Alpha Grid is an NSE data and offline research foundation: Python 3.12+,
an eleven-crate Rust workspace and a React/TypeScript cockpit. The Python
package version is 0.5.0. It implements Dhan depth capture, durable journals,
quality monitoring, historical acquisition, causal statistical research and
offline paper decisions. There is no broker order transport.

The latest tracked milestone is the RELIANCE 15-minute prospective experiment,
frozen in code/documentation on 2026-10-19. This does not prove that its ignored
local frozen-model artifact exists. The paper policy still uses an untrained
model; the standalone prospective forecaster does not replace that policy.
The 2026-10-24 real-data gate removed the active local replay journal and keeps
the cockpit in DATA UNAVAILABLE / NO TRADE without verified Dhan input.
Historical synthetic benchmark artifacts remain labelled, isolated fixtures.
An authentic October 15–16 RELIANCE Dhan minute-chart response was saved on
2026-10-26 (720 bars); its raw integrity and scoped calendar audit passed,
while historical Dhan identity linkage and candle timing remain unverified.
It is not connected to the cockpit or a causal research dataset.

## Data flow

```text
Python Dhan depth -> immutable domain events -> JSON QJ01 journal
                                           -> health status / stdout
JSON QJ01 -> validated replay -> depth Parquet generations

Rust depth / standard-feed packets -> normalized fixed-point events
  -> Postcard QJ01 journal -> deterministic engine / replay
  -> volume, candles, features -> telemetry snapshots -> React cockpit
                             -> paper risk / OMS -> separate QP01 ledger

Rust QJ01 -> export_jsonl + QP01 -> Python research dataset
  -> causal features + separate future labels -> sufficiency / experiment gates

Historical Dhan 1-minute candles -> checksummed raw evidence + provenance
  -> Dhan current identity + dated identity / compact NSE calendar / quality
  -> OHLCV lake -> candle research
  -> separate provisional RELIANCE experiment using verified saved responses

Separate local NSE daily-price import remains available but is not part of the
October Dhan minute-candle pilot.
```

## Implementation map

| Area | Main files | Responsibility and tests |
| --- | --- | --- |
| Python contracts | `src/quant/domain.py`, `config.py`, `logging.py` | Frozen events, Decimal prices, timezone-aware timestamps, string instrument IDs; `tests/test_domain.py`, `test_config.py`, `test_health_logging.py`. |
| Depth ingestion | `src/quant/feeds/dhan_depth.py`, `apps/collect_depth.py` | Direct WebSocket receive timestamps, official decoder, bid/ask pairing, reconnects; `test_dhan_depth.py`. Collector appends before health observation and stdout. |
| Persistence | `src/quant/storage/journal.py`, `parquet.py` | Single-writer checksummed JSON frames, tail recovery, durable Parquet publication; `test_journal.py`, `test_parquet.py`. |
| Health | `src/quant/health/monitor.py`, `status.py`, `apps/feed_health.py` | Unique-event freshness, anomalies and atomic status; `test_feed_monitor.py`, `test_health_logging.py`. |
| Historical acquisition | `src/quant/acquisition/`, `apps/acquisition.py` | Dhan request planning, provenance, recovery, instrument identity, dated calendars, raw/lake checksums; `test_acquisition_*.py`, `test_dhan_pilot_cli.py`, `test_dhan_backfill_cli.py`. |
| Candle research | `acquisition/ohlcv_research.py`, `apps/noise_reduction.py`, `apps/prospective_15m.py` | Completed-bar assumptions, causal transforms, provisional forecasts; `test_ohlcv_research.py`, `test_noise_reduction.py`, `test_prospective_15m.py`. |
| Forward observations | `acquisition/forward_observations.py`, `apps/forward_observations.py` | Offline 5/15/30/60-minute and remaining-session candle paths with separate as-of features and matured outcomes; `test_forward_observations.py`. Requires a checksummed lake, dated verified session, and dataset-bound timing evidence. |
| Dhan availability probe | `apps/dhan_candle_availability.py`, `docs/forward-observations.md` | Offline plan by default; guarded, fixed 14-poll one-session historical-chart observation proposed for separate approval. It preserves original responses and reports sampled first-seen/revisions without claiming candle completion or causal eligibility; `test_dhan_candle_availability.py` uses synthetic software fixtures only. |
| Statistical research | `src/quant/research/` | Dataset joins, labels/censoring, baselines, Kalman/OU, competing risks, walk-forward, session bootstrap, utility, inference and registry; `test_research_*.py`. |
| Infrastructure | `infra/benchmark/`, `benchmarks/parquet_benchmark.py` | Synthetic capacity, storage/network measurements and dated VM evidence; `test_infra_benchmark.py`, `test_ingestion_load.py`. |
| Cockpit | `web/src/App.tsx`, `Chart.tsx`, `PaperWorkspace.tsx`, `ResearchWorkspace.tsx`, `stream.ts` | Market, reliability, paper and research views; corresponding `*.test.ts(x)`. |

Python operational entry points are declared in `pyproject.toml`. Acquisition,
noise comparison and prospective evaluation also run through
`python -m quant.apps.<module>`; not every module has an installed console script.

### Rust crates

| Crate | Role |
| --- | --- |
| `quant-core` | Fixed-point prices, timestamps, normalized market/session events. |
| `quant-market-data` | Separate Dhan 20-depth and standard-feed decoders. |
| `quant-orderbook` | Full book snapshots. |
| `quant-journal` | Checksummed Postcard QJ01 frames and segmented journals. |
| `quant-replay` | Ordered/timed replay and `export_jsonl` research bridge. |
| `quant-depth` | Capture, feed health, mock transport; optional `live-readonly` transport. |
| `quant-engine` | Volume tracking, candles, incremental features and durable processing. |
| `quant-paper` | Typed decisions, independent risk, provisional fees, simulated OMS/fills, QP01 and experience records. |
| `quant-inference` | Bounded versioned linear inference adapter; not connected to paper policy. |
| `quant-telemetry` | Read models, gateway, wire schemas; `telemetry_local` loopback DATA_ONLY cockpit endpoint and QP01 approval ledger, plus `telemetry_demo`, `paper_eval`, `schema_export`, `telemetry_bench`. |
| `quant-bench` | Bounded-channel synthetic ingestion and durability measurements. |

Crate-local unit tests sit alongside implementations. Shared dependencies and
membership are in `rust/Cargo.toml`; `rust/Cargo.lock` pins resolved dependencies.

The local cockpit path uses `web/vite.config.ts` proxies for `/ws` and `/api`
to the existing Rust gateway on 127.0.0.1:8765. A private NTFS local session
key is checked by `/api/session`; a seven-day signed HttpOnly same-site cookie gates the
WebSocket and `/api/snapshot`. `/api/paper/approval` additionally checks the
local browser origin and appends a synced QP01 `StockApproval` record before
publishing approval state. It does not authorize orders. `telemetry_local`
starts with no feed, candidates, quotes, or paper positions and reports
DATA UNAVAILABLE / NO TRADE. No Dhan socket is connected by that command.
`tools/start-cockpit.ps1` loads the ignored private key into a DATA_ONLY
process without enabling the Dhan feed; `web/src/stream.ts` reuses the cookie
across browser and backend restarts and offers forget-device logout.
The launcher's `-Offline` option loads only verified local account cache,
labels the account state CACHE_ONLY and does not schedule Dhan account refresh.
In OBSERVE_ONLY, the Rust caller shares
the gateway process and port 8765 and publishes bounded, credential-free feed
lifecycle stages through the existing audit field; the cockpit shows the latest
stage. The consumed October 28 zero-frame attempt predates these diagnostics,
so its remote failure stage is still unknown.
The `-Online` launcher option now prepares a persistent, read-only Quote-17
process with at most two fresh-preflight restarts after selected transient
transport failures. It has not been exercised against Dhan: the local shell
blocks PowerShell scripts and Windows Application Control blocks fresh Rust
test executables. All broker execution paths remain absent and NO TRADE holds.

2026-10-26 quote-path preparation: `quant-engine/src/capture.rs` now scopes the
existing Dhan v2 standard decoder to NSE_EQ security ID 2885 and publishes only
after QJ01 file sync and processing. `quant-telemetry/src/read_model.rs` can
consume an already authenticated quote socket and send one request-code-17
subscription. The opt-in `telemetry_local` caller opens that socket only after
its startup gates and a separately authorized launch. The
existing gateway and cockpit can carry the quote price, cumulative volume,
source/receipt times, connectivity, freshness and explicit NO TRADE reasons.
Scoped QJ01 capture now retains exact binary frames, SHA-256, receipt time and
frame ID before normalized records, which retain source identity and packet
ordering metadata. The raw QJ01 has a 64 MiB cap, read-time SHA check and no
JSONL export of binary frames. A native NTFS private-path test passed, but the
runtime's opt-in caller enforces private-path preflight. Synthetic packet
fixtures are tests only. The default runtime remains DATA_ONLY and disconnected.
The opt-in caller uses OBSERVE_ONLY with a separate, dated ordinary-session
calendar that stops at 15:15 and marks RELIANCE's CAS classification UNVERIFIED.
Its research-facing `Session.verified` remains false; ordinary-session evidence is
recorded separately in the observation evidence.
The existing October 12-16 CAS research calendar remains unchanged. Observation
quotes retain NO TRADE and cannot publish paper state, features or candles to
the cockpit. Ordinary non-elevated W32Time and the unchanged clock gate passed;
Codex's sandbox identity alone received access denied. The ordinary-user-owned
private QJ01 is under `data/.secrets/quote-journal/`. The caller does not load
`.env` or the saved access-token file: process-local UI key, Dhan token and
client ID remain unconfigured, and a live trial still requires authorization.
Windows segmented QJ01 keeps file sync and atomic manifest rename, verifies
directory paths and uses a native fail-closed free-space check; Windows directory
entry/rename crash durability is unproven. POSIX directory sync is unchanged.

## Contracts to preserve

- Python uses `Decimal`; Rust uses INR nanounits and Unix nanoseconds. Browser
  monetary values and large integer wire fields use strings. Avoid lossy Number
  conversion for exact values.
- Both market journals have the QJ01 marker, but Python JSON and Rust Postcard
  payloads are distinct. Do not interchange their readers. QP01 is a separate
  paper-experience format. Use the Rust JSONL bridge for Python research.
- Python collection journals before publication. Rust
  `MarketEngine.append_and_process` writes and syncs before deriving outputs;
  a failed append/derivation poisons the engine. `process` alone is for replay
  or processing-only benchmarks.
- Journal recovery repairs only a damaged/incomplete final frame; interior
  corruption blocks startup. A buffered write is not a durability acknowledgment.
- Depth snapshots do not establish individual order events or queue position.
  Standard-feed quotes/LTP are not verified exchange trade ticks. Dhan 20-depth
  exchange timestamps are normally absent; source sequence continuity is
  unverified. Python and Rust pairing rules are documented separately.
- Causal features must precede labels. Match QP01 to QJ01 by session, source
  index, instrument and receive time. Censor uncovered horizons; keep missing
  forecasts and unknown outcomes unavailable.
- Synthetic replays are correctness fixtures, not independent real sessions.
  `research/sufficiency.py`, `walkforward.py` and `registry.py` implement
  provenance, split and promotion gates. Defaults are engineering guards,
  not proof of statistical power. Paper fills and cost assumptions remain
  simulated/provisional.

## UI and schema boundary

The demo binds loopback; Vite serves port 5173 and proxies `/api` and `/ws` to
127.0.0.1:8765. `gateway.rs` exposes snapshot, health, paper, research,
acquisition and per-instrument GET routes, WebSocket streaming, and a POST
`/api/demo/control` route for synthetic replay controls.

`web/src/stream.ts` tracks boot ID and publication sequence, applies contiguous
updates, fetches a full snapshot on a sequence gap and reconnects with backoff.
Telemetry is separated from capture. Research/acquisition views consume
precomputed status; they do not train models or acquire market data.
The cockpit accepts OBSERVE_ONLY quote snapshots only with Dhan standard-feed
provenance, NSE_EQ/2885 scope, absent paper/derived state and explicit NO TRADE
reasons. It labels provisional quotes and session classification visibly.

Wire contracts originate in Rust `quant-telemetry/src/protocol.rs` and
`schema_export`. Regenerate `web/schemas/` and `web/src/generated/` together.
CI rejects generated schema/type drift.

## Latest research constraints

Authoritative specification: `SPEC` in `src/quant/apps/prospective_15m.py`;
protocol: `docs/reliance-15m-prospective.md`.

- April-July 2026 training; August development only. September-October has
  already been examined and is excluded from this experiment.
- November 2026 is the prospective evaluation month. As of this inspection it
  is future data, so there are no November outcomes to assess.
- Three fixed raw features, training-only scaling, fixed softmax model and
  penalty; class cutoffs at +/-20.6 bps, reference prices at t+3 and t+18.
- Score equally weighted session-mean multiclass Brier against training class
  frequencies. Bootstrap whole sessions, 5,000 draws, seed 20261019. Improvement
  requires the model-minus-null upper 95% bound below zero.
- Evaluation checks code/spec/training-source hashes, an independently verified
  full November session-date list, at least 15 sessions, complete common-window
  bars and raw response receipt after November ends. Outputs use new paths.
- Source timestamp, completed-bar, adjustment and historical identity semantics
  remain limitations. Economic sensitivities describe reference returns, not
  executable P&L or permission to promote a model.

Older acquisition documents record status at their own dates. They do not
establish whether subsequent ignored raw responses exist. Inspect local evidence
only when needed for an authorized task. Do not refit or change the frozen
protocol as a routine cleanup.

### Forward candle observations (added 2026-10-20)

`docs/forward-observations.md` defines the CLI, v2 record schema, outcome states
and exclusion rules. It reads `verify_dataset` output and rejects unverified
bar-start or completion timing. For post-August 3, 2026 sessions, dated calendar
evidence must classify CAS versus non-CAS continuous close. QJ01/QP01 and the
frozen RELIANCE experiment are unchanged. The checkout has no ignored historical
lake or independently verified completion evidence, so its distribution summary
is synthetic only. Nine focused tests passed using a synthetic lake; the fixture
alone originally bypassed the lake writer's POSIX fsync call on native Windows.
The 2026-10-23 synthetic acquisition-to-lake test now exercises native NTFS
publication and read-back; it does not establish real-market eligibility.

## Validation and local environment

CI source of truth: `.github/workflows/validate.yml`.

```sh
uv sync --extra dev --locked
uv run --locked --extra dev python -m pytest -q
uv run --locked --extra dev ruff check .
uv run --locked --extra dev ruff format --check .
uv run --locked --extra dev mypy

cargo test --locked --workspace --all-targets --manifest-path rust/Cargo.toml
cargo fmt --all --manifest-path rust/Cargo.toml -- --check
cargo clippy --locked --workspace --all-targets --manifest-path rust/Cargo.toml -- -D warnings
cargo build --locked --workspace --release --manifest-path rust/Cargo.toml
cargo check --locked -p quant-depth --features live-readonly --manifest-path rust/Cargo.toml

npm ci --prefix web
cargo run --locked -q -p quant-telemetry --bin schema_export --manifest-path rust/Cargo.toml -- web/schemas
npm run schema:generate --prefix web
git diff --exit-code -- web/schemas web/src/generated
npm run typecheck --prefix web
npm run lint --prefix web
npm run format:check --prefix web
npm test --prefix web
npm run build --prefix web
```

Repo pins Rust 1.99.0 and CI Node 24; Python minimum is 3.12. On this Windows
inspection host, Git and PowerShell were available; the Python launcher found
no installed Python, and uv, Rust and Node/npm were not on PATH. Application
tests were not run. Python journaling imports POSIX `fcntl`; Linux CI is the
documented verification environment, so native Windows parity is unproven.

## Context index maintenance

`tools/repo-context.ps1` builds `docs/context/merkle.json` from current working
bytes of Git-tracked files plus explicit support files listed
in the script. The manifest excludes itself to avoid self-reference. No ignored
raw data, environment file or arbitrary untracked file is discovered. Stage new
source files or add them to the explicit list before refreshing. Tracked deletions are omitted and appear as
removed leaves when checking the previous manifest.

Each file stores its ordinary SHA-256 and a domain-separated blob hash. Each
directory hashes its sorted child names, kinds and hashes, propagating changes
to the root. The manifest includes directory children so the hierarchy can be
traversed. Exact-byte hashing deliberately detects line-ending changes; roots
can differ between checkouts with different Git line-ending settings.

```powershell
powershell -NoProfile -File tools/repo-context.ps1 -Check
powershell -NoProfile -File tools/repo-context.ps1
```

This host blocks `.ps1` execution by default. Verification used the approved
process-only override `powershell -NoProfile -ExecutionPolicy Bypass -File
tools/repo-context.ps1` (append `-Check` to validate); no persistent execution
policy was changed.

### Windows UI launch verified 2026-10-21

Project-local runtimes now exist under ignored `.local/toolchains`: Node
24.22.0, Rust 1.99.0 GNU, and w64devkit 2.10.1. They are not on the persistent
system PATH. Frontend dependencies were installed with `npm ci`; Rust builds
require the local Cargo and w64devkit bins on PATH, project-local `RUSTUP_HOME`
and `CARGO_HOME`, and `RUSTFLAGS='-C link-self-contained=yes'` to use Rust's
matching GNU runtime libraries.

The Unix `statvfs` implementation in `quant-journal/src/segmented.rs` is now
platform-gated. Other platforms return `Unsupported` for that disk-space check;
this does not establish Windows segmented-journal support (directory sync is
also platform-dependent). The demo uses the single-file journal.

The native Windows `telemetry_demo` build, its two fixture/ledger tests, Rust
format check, and frontend production build passed. The dev UI on loopback
5173 and gateway on loopback 8765 passed HTTP snapshot/health and proxied
WebSocket snapshot/heartbeat checks with two synthetic instruments. Servers
were left running in hidden processes; PIDs and logs are in ignored `.local/`.
No browser automation surface was available, so rendered-page visual inspection
was not performed. Full application suites and live feeds were not exercised.

Check reports changed/added/removed leaves and directory branches. It detects
content drift, not whether prose remains semantically correct, and is not a
signed authenticity proof. After source changes, read affected branches,
update this map and decision summaries when needed, then refresh. The root
`AGENTS.md` points future repository sessions to these files; persistence depends
on those sessions reading them.

## Dhan-only runtime gate, 2026-10-24

- The former fictional trade desk is removed from the active cockpit. The
  account route returns `503` without reading credentials or calling Dhan;
  the UI shows `DATA UNAVAILABLE / NO TRADE` and retains local risk settings.
  Telemetry snapshots with replay/demo modes do not enter the cockpit stream.
- `telemetry_demo`, `paper_eval` and `telemetry_bench` refuse normal runtime
  execution because their QJ01 files have no authenticated Dhan provenance
  binding. Their synthetic fixtures remain in isolated software tests.
  The QJ01/QP01 research-dataset command also refuses publication until that
  binding exists. The opt-in Rust 30m/60m/intraday paper evaluator, approvals,
  independent risk gate and QP01 order/ack/fill events are tested behavior,
  not an operating market workflow or executable strategy.
- The forward-observation command requires a Dhan saved-response lake, its
  staged original response bytes with matching SHA-256, request/retrieval
  metadata, dated session evidence and dataset-bound completion-timing evidence.
  It rejects synthetic/NSE lakes and missing original bytes. This is an
  internal consistency gate; hashes and labels alone cannot independently
  authenticate a provider response. No causally eligible forward observations
  or research results were produced. A new authentic October 15-16 response
  was saved on October 26; its timing and historical identity remain unverified.
- Native Windows lake publication was already fixed before this change by
  reusing the Dhan directory-sync helper. Current unmocked synthetic NTFS
  tests verify bytes, hashes, manifest read-back and interrupted publication.
  File flush and atomic publication work; Windows directory fsync remains
  unsupported and power-loss durability is not equivalent to POSIX.
  The combined acquisition/lake/pilot/forward suite passed 37 tests with a
  workspace basetemp; Rust paper/telemetry tests, focused Clippy, web
  typecheck/tests/lint/build, Node settings tests, Python Ruff and focused
  mypy also passed. Running `telemetry_demo` refused startup with exit 1 and
  created no requested journal.

## Next-phase Dhan research review, 2026-10-26

`docs/research-acquisition.md` now records the predeclared abnormal-volume
momentum question and the existing Rust 20-depth/QJ01 prospective path. The
private October 15-16 RELIANCE checkpoint was reverified offline: one original
response, 720 minute rows across two sessions, with completion timing still
unverified. No causally eligible forward-observation lake or live depth capture
was found. The prior 20-request approval supplies a count ceiling but no dated
instrument/window scope; no new Dhan request was made. The paper cockpit's
empty state explicitly says DATA UNAVAILABLE / NO TRADE.

## Read-only RELIANCE quote caller, 2026-10-26

`rust/crates/quant-telemetry/src/bin/telemetry_local.rs` now has an opt-in,
one-shot Dhan v2 standard-feed caller. It uses the existing QJ01 capture,
telemetry gateway and authenticated cockpit; no broker-order path is present.
`src/quant/apps/dhan_quote_preflight.py` checks the current Dhan master,
verified dated session, private native NTFS journal ACL and capacity before
connection. The caller additionally requires local clock health and Dhan
credentials. The token/client ID exist only in the in-memory TLS URL required
by Dhan, and connection failures are replaced with static errors. Expected
OI packets remain raw only; the quote feed does not publish inferred candles.

Offline tests passed: Rust market-data 11, engine 13, telemetry library 16,
caller binary 2; Python preflight 1 on native Windows. Focused Rust Clippy,
Python Ruff and mypy passed. The checked host clock command returns access
denied (0x80070005), and the checked calendar covers only October 12–16.
Startup is BLOCKED; no Dhan socket or genuine binary frame was used.
An opt-in local startup probe with a fixture UI secret exited 1 at
`local clock health unverified` before journal creation or credential loading.
The isolated gateway quote-to-cockpit test later passed 1/1 after an ordinary
same-path Cargo rebuild. It now asserts NO TRADE and absent paper state as
well as QJ01-before-telemetry ordering. The earlier unsigned test executable
was blocked by Enterprise signing policy; the rebuilt unsigned executable
was allowed, so future test-launch permission remains uncertain.

## Authorized October 27 observation trial handoff

The one-shot `telemetry_local --dhan-quote-readonly` caller now caps feed work
at 60 seconds and stops by 15:14 IST, with both a stop watch and an outer socket
timeout. It retains OBSERVE_ONLY / NO TRADE, Quote-17 NSE_EQ/2885, no reconnect,
and the existing QJ01-before-publication path. The ordinary-user PowerShell
launch and process-local secret cleanup are in `rust/docs/phase2c-cockpit.md`.
The ordinary-user empty `.local/paper-approvals.qp` was opened and reopened by
the existing DATA_ONLY caller without altering approvals; the native private
quote-journal and source/session preflight passed. A real socket is deliberately
left to the user's ordinary PowerShell under the single approved trial.

## Rustls caller startup correction, 2026-10-27

The approved ordinary-user attempt started the local gateway, then a Tokio
worker panicked because Rustls 0.23.46 had neither `ring` nor `aws_lc_rs`
enabled as its `CryptoProvider`. `quant-telemetry` now enables the existing
Rustls `ring` provider and installs it at the first line of `telemetry_local`
before any gateway or feed tasks. A failed installation returns a static error;
a separate worker monitor disconnects telemetry if the worker panics. The
private ordinary-user QJ01 remained zero bytes with no sidecars or records.
The repaired caller has only offline native tests; the prior single-attempt
authorization does not cover another authenticated connection.

## October 28 authorized OBSERVE_ONLY window

`data/config/reliance-observation-2026-10-28.json` is the separate, ignored
October 28 scheduled ordinary-session calendar, with same-day NSE holiday,
normal-hours and CAS source URLs and UNVERIFIED instrument-specific CAS
classification. `tools/repo-context.ps1` includes it in the explicit support
inventory. The dated masked-prompt quote procedure is superseded by the
single-start read-only cockpit; it grants no new live-feed authorization.
The existing Rust clock, session, private journal, identity,
credential, QP01 and NO TRADE gates are unchanged. At 00:03 IST the complete
preflight correctly blocked outside the session; ordinary-user W32Time root
dispersion briefly exceeded the 0.1-second gate, then returned below it by
00:05 IST. An ordinary-user Rust startup probe with Dhan credential variables
absent passed that clock gate and exited on the expected out-of-session source
preflight, before a gateway or authenticated socket. The clock is rechecked
at actual startup. No authenticated socket was opened by Codex.

## October 27 historical request and cockpit login, 2026-10-28

`src/quant/acquisition/dhan.py` supports explicit IST intraday request bounds
and records HTTP status/Date in existing private response metadata. The one
authorized October 27 RELIANCE request returned HTTP 401, with original 140
bytes preserved in quarantine and sanitized `DH-901 / Invalid_Authentication`.
There are zero authentic candles for this date from the request. No movement
distribution or causal claim is available. The login change is local only:
the gateway signs a seven-day cookie with a private NTFS key, and the cockpit
checks an existing cookie before asking for the key. Native DATA_ONLY login,
backend restart, cookie reuse and forget-device checks passed; no Dhan feed
was opened. See `docs/context/DECISIONS.md` for the response hash and tests.

## Single-start read-only cockpit, 2026-10-28

`tools/start-cockpit.ps1` now starts and supervises the existing Rust
`telemetry_local`, Vite and trading settings/status service from one
ordinary-user PowerShell. Ctrl+C stops the children it started. Occupied
ports block duplicate startup. It loads only the ignored private UI key;
there is no Dhan token load, authenticated refresh or automatic feed mode.
The existing signed-cookie login remains the browser session mechanism.
`src/quant/apps/cockpit_cache.py` verifies the pinned private October 15-16
RELIANCE Dhan response before `telemetry_local` loads its public historical
projection into an authenticated gateway route. `web/src/App.tsx` and
`TradingWorkspace.tsx` display its 720 one-minute rows and source coverage,
with explicit unavailable daily, October 27 and account data states. Cached
historical bars never enter the live engine or causal research path. Current
account/portfolio, orders, trades, ledger, daily candles and live quotes are
unavailable without their own authorization and evidence. Native startup,
local endpoint, duplicate-start and stop checks passed; actual browser reopen
could not be verified because the computer-use helper stopped before URL
selection. Details and limitations are in `docs/context/DECISIONS.md`.

## Approved account refresh, 2026-10-28

The existing `web/server/dhan-account.mjs` adapter and
`web/server/trading-service.mjs` now serve five approved read-only account
panels. `src/quant/apps/dhan_account_cache.py` reuses the private NTFS Dhan
write/ACL helpers for exact original response bytes and restart-safe
five-minute per-endpoint attempt reservations. The Node route delegates
browser-cookie validation to the existing Rust `/api/session` endpoint.
The launcher passes a private client ID only to the account service and
does not enable live feed or orders. Expired token blocks all Dhan requests;
401 stops refresh, while stale verified panel data remains labeled. Only
the October 15-16 authentic minute response (720 bars) exists in the
inspected `data/historical` archive; the reported 50,205 rows are not
established as local Dhan evidence. Daily Dhan response remains absent.
