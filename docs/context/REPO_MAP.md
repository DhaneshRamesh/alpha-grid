# Alpha Grid context map

## Restored 3-year historical dataset & plain snapshot v2 — 2026-11-10 IST

The 25-stock, 3-year Dhan 1-minute historical dataset (Nov 1, 2023 – Nov 1, 2026) is fully restored on unencrypted storage at `/Volumes/Danny's HDD/AlphaGrid-Plain/historical/raw-dhan/`:
- **325 Windows Restored**: 100% of planned windows across all 25 NSE cash equities completed with 0 errors and 0 missing files. Total 6,545,612 1-minute candles across 312.84 MB (312,842,513 bytes).
- **Snapshot Manifest v2**: `alpha-grid-snapshot-2026-11-10-plain-v1` created at `/Volumes/Danny's HDD/AlphaGrid-Plain/historical/manifests/research_snapshot_manifest_plain_v2.json` and mirrored to `data/research_snapshot_manifest_plain_v2.json`. Deterministic binary Merkle root SHA-256: `06f3124a842109b42d0bf508fc30ab662b895d32c692c23fcbeba903176d90b3` using custom pairwise hex-SHA256 tree with odd-leaf duplication (`ALPHA_GRID_PAIRWISE_HEX_SHA256_V1`).
- **Opportunity Evaluation Pathway**: `OpportunityRecord` and `evaluate_opportunity_record()` in `src/quant/apps/execution_safety.py` enforce fail-closed checks across provenance, quote freshness, Stage 3 validation, past volatility, execution costs, and uncertainty lower bounds. Missing quotes, unestablished gross payoff models, and absent confidence intervals are cleanly represented as `UNKNOWN` rather than synthetic zeros. Bound to verified snapshot ID (`alpha-grid-snapshot-2026-11-10-plain-v1`) and Merkle root (`06f3124a842109b42d0bf508fc30ab662b895d32c692c23fcbeba903176d90b3`). Integrated into `tools/offline_end_to_end_replay.py` and Cockpit `/trade/simulation/decisions`.
- **Dataset Parity**: 0 candle delta vs legacy snapshot (6,545,612 bars). Preserves August 2026 NSE closing auction change (360 bars vs 375 bars post 2026-08-03) and 8 corporate action discontinuities with unresolved pricing bases.
- **Reader & Cockpit Access**: `tools/train_m1_model.py::load_raw_bars` loads all 180 development/calibration windows seamlessly in 12.68s. Cockpit shared evidence API (`/trade/evidence`) verifies 325/325 windows and 6,545,612 bars.
- **Preservation & Holdout Gates**: Reserved holdout stocks (`TCS`, `KOTAKBANK`, `MARUTI`, `BHEL`, `DIVISLAB`) and temporal holdouts (post 2025-10-31) remain strictly untouched. M1 model artifacts, simulation ledgers, and original RELIANCE experiments remain immutable.


## Plain HDD storage target & unencrypted data restoration — 2026-11-10 IST

Alpha Grid supports direct unencrypted external storage on the connected HDD (`/Volumes/Danny's HDD/AlphaGrid-Plain/`), bypassing the locked encrypted sparsebundle (`AlphaGridData-2026-11-05-v2.sparsebundle`):
- **Plain Directory Structure**: Standard unencrypted directories for `historical/raw-dhan/`, `historical/manifests/`, `market-feed/quotes/`, `market-feed/depth/`, `journals/qj01/`, `journals/qp01/`, `research/`, `models/`, `checkpoints/`, and `reports/` with 0700 permissions. Requires no disk-image mounting or encryption passphrases; directly browseable in macOS Finder.
- **Fail-Closed External Volume Guards**: `tools/execute_full_3yr_acquisition.py` includes `verify_external_mount(path)` ensuring raw historical downloads only target active `/Volumes/` mount points. Auto-detects plain HDD target, exposes `--verify-only` mode, and fails closed against internal SSD paths.
- **Cockpit & Service Integration**: `web/server/trading-service.mjs` (`resolveStorageTargetPath`) and `tools/start-cockpit-macos.sh` auto-detect `/Volumes/Danny's HDD/AlphaGrid-Plain` as the primary storage target, verifying capacity and mount status.
- **Restored Local Artifacts**: Re-established byte-exact SHA-256 for RELIANCE 1-minute historical raw responses (45,945 candles across 2.23 MB), 19 instrument master/manifest files (including 325-window 2023–2026 manifest), 5 QP01 ledgers, QJ01 demo journal, 7 research reports, M1 model pickle and metadata, and offline simulation replay checkpoints.
- **Zero Live/Backfill Calls**: Historical 3-year backfill (325 windows) is validated offline as pending (`0/325` completed) without initiating unauthorized Dhan API requests. Preserves `DATA_ONLY / SIMULATION_ONLY / NO TRADE`.


## Evidence-Driven Live Monitor — 2026-11-09 IST

`web/src/LiveMonitor.tsx` implements an evidence-driven observer view making actual market data and simulator processing visible without prediction or trading claims:
- **Strict Source Isolation**: Enforces explicit labels (`LIVE_WS`, `HISTORICAL_ARCHIVE`, `REST_SNAPSHOT`) without blending, fabricating, or relabeling sources. Live WS observations render as genuine received ticks, archived OHLCV renders from verified Dhan 1-minute bars, and REST snapshots render point-in-time quotes.
- **Observer Avatar**: Compact animated avatar advancing ONLY on real timestamps and durable events. Displays state bubbles for Watching quote (`👀`), Computing volatility (`📊`), Reading Standard Full book (`📚`), Lookback unavailable (`⏳`), NO TRADE (`🚫`), Virtual decision evaluated (`🧪`), and Feed stale/closed (`🛑`).
- **5-Step Evidence Pipeline**: Visualizes genuine timestamps and hashes across: Frame Received → Raw Frame Durably Persisted (QJ01 fsync) → Decoded (quant-engine) → Freshness/Provenance Check → Simulator Decision (virtual reference only, never a fill).
- **Observed Depth & Touch Details**: Beside chart, displays observed bid/ask, quoted spread (bps), 5-level displayed depth table, freshness age (ms), source-frame SHA-256 prefix, receipt time, and depth source status.
- **Scrollable Decision Timeline**: Displays chronological virtual decisions, rejections, size-policy results, and explicit `VIRTUAL REFERENCE · NEVER A FILL` warnings. Supports offline historical replay stepping through 6,840 slots.
- **Backend-Owned Listeners**: Zero additional browser sockets created; reuses single backend telemetry gateway stream and REST endpoints. Preserves `DATA_ONLY / SIMULATION_ONLY / NO TRADE`.
`web/server/trading-service.mjs` exposes `GET /trade/simulation/decisions` and offline replay fallback in `GET /trade/simulation`. Focused tests in `web/src/LiveMonitor.test.tsx` (8/8 passed) and `web/server/trading-service.test.mjs` (27/27 passed).

## Cockpit Storage and Evidence Workflow — 2026-11-09 IST

`web/server/trading-service.mjs` exposes a shared evidence and inspection API:
- `GET /trade/evidence`: Reports observed backend metrics for external storage volume, QJ01 live/captured journal status, historical 3-year archive manifest, virtual simulation ledger, and research model artifacts. Automatically strips sensitive tokens, client IDs, secrets, and auth headers using `redactSensitive()`.
- `GET /trade/evidence/inspect?target=storage|qj01|historical|simulation|research`: Returns underlying diagnostic metadata and recent records with a clear security redaction banner.
- `POST /trade/storage/action`: Handles 6 operator workflow actions: `connect`, `verify`, `start-services`, `arm-capture`, `stop-safely`, and `replay`. Enforces fail-closed precondition gating (`503 DRIVE_UNMOUNTED`) when target storage is unmounted or unwritable.
`tools/start-cockpit-macos.sh` targets an unencrypted owner-private data volume (`ALPHA_GRID_DATA_VOLUME="${ALPHA_GRID_DATA_VOLUME:-/Volumes/AlphaGrid Data}"`) without requiring disk images, verifying owner-private permissions (0700) and writable mount state.
`web/src/StorageSessionPanel.tsx` implements the evidence-first "Storage & Session" control panel, integrated in CommandCenter and available as a dedicated navigation tab (`views` icon `⛁`). Displays live mount state, capacity, QJ01 records, archive status, virtual cash, and research provenance, with explicit failure reasons (`DRIVE_UNMOUNTED`, `JOURNAL_MISSING`) and modal inspection drawers. Preserves strict `DATA_ONLY / SIMULATION_ONLY / NO TRADE` posture.

## Opt-in paired paper sizing — 2026-11-08 IST

`src/quant/apps/live_simulation.py --portfolio-v2 --sizing-comparison` runs the existing 25-stock virtual portfolio twice against one local snapshot stream, with separate hash-chained baseline and `DYNAMIC_V1` ledgers. The fixed dynamic rule only shrinks positions using a fresh same-packet Standard Full five-level ask, observed spread and trailing 15-minute mid-quote volatility; invalid or insufficient inputs abstain. `--report-sizing-comparison` verifies paired source/opportunity streams and reference accounting, then reports session-grouped uncertainty without claiming fills or activating a strategy. The mode is opt-in and is not launched by the Mac startup script. `tests/test_live_simulation.py` covers sizing, abstention, risk accounting, recovery and session rollover; 22 tests passed offline. No genuine prospective pair ledgers exist yet.

## AlphaGrid M1 forecasting model & inference pipeline — 2026-11-07 IST

`src/quant/research/m1_pipeline.py` implements Alpha Grid's offline dual gradient-boosted forecasting model (`alpha-grid-m1-20261107-v1`). Predicts separate favorable ($R_{\text{fwd}} \ge +30\text{ bps}$) and adverse ($R_{\text{fwd}} \le -10\text{ bps} \lor \text{MAE} \le -25\text{ bps}$) outcome probabilities across 15-minute forward horizons using 5 past-bar features. Uses chronological Train (Nov 2023–Apr 2025, 133,200 non-overlapping samples) and Calibration (May 2025–Oct 2025, 47,880 samples) partitions while excluding the 5 reserved holdouts (`TCS`, `KOTAKBANK`, `MARUTI`, `BHEL`, `DIVISLAB`) and post-October-2025 test observations. Outperforms Null and Logistic baselines in Brier score and log loss on calibration data, but remains NO-GO for trading due to negative net reference returns and unverified candle availability. Exports artifacts with SHA-256 sidecars to `data/models/` and `/Volumes/AlphaGrid Data/Live/offline-dev/training-artifacts/`. Exposes `M1Predictor` (read-only snapshot inference) and `M1ShadowLogger` (append-only JSONL shadow logger); neither is wired to orders. Training runner in `tools/train_m1_model.py`; focused unit tests in `tests/test_m1_pipeline.py`.

The November 7 independent review found that M1's saved favorable threshold (0.10) previously reloaded as the 0.12 default; the resulting 26-trade audit was a different policy from the saved 117-trade run. The loader now restores the saved threshold. Training and audit loaders now verify each consumed source file's byte length and SHA-256 against the snapshot manifest; they do not recompute its declared Merkle root. Entry fills are now formally required at bar T open rather than unrealizable bar T-1 close. Stage 2 of the frozen 60-minute bracketed opportunity protocol (`docs/exp-60m-bracketed-opportunity-protocol-v1.md`) evaluated across 180 verified development files (163,476 candidate slots) confirmed that realizable bracket exits (+25/-20 bps) and maker entries remain net negative (-13 to -20 bps) against the ~15.5 bps friction floor. Predeclared stop criteria triggered; directional intraday cash equity ML research track is terminated. Minimum offline execution safety path implemented in `src/quant/apps/execution_safety.py` enforces fail-closed order lifecycle and risk boundaries (`test_execution_safety.py`). `tools/offline_end_to_end_replay.py` executes chronological deterministic replay across October 2025 Dhan observations through M1, verifying causal timing, model parity, 7/7 fault-injection scenarios, and fail-closed NO_TRADE abstentions, exporting read-only telemetry to `data/telemetry/offline_simulation_status.json`. System status is OFFLINE-GO / TRADE-STOP. See `DECISIONS.md` for full metrics.

## Auditable fail-closed 3-year historical acquisition tool — 2026-11-05 IST

`tools/execute_full_3yr_acquisition.py` executes and audits the 25-equity, 3-year (2023-2026, 325 windows) 1-minute historical acquisition. Enforces fail-closed semantics: atomic checkpoint persistence, strict nonzero exit on incomplete runs, offline resume-verification of raw SHA-256 digests against `.sha256` sidecars and request metadata without overwriting, and strict candle validation (shape, ordering, non-finite values, OHLCV consistency, and session bounds). Decouples source integrity, exploratory return-model eligibility (invariant to scalar splits), and historical execution-model eligibility (distinguishing back-adjusted, unadjusted, and unverified price bases). Report classifications derive purely from validated window records rather than hardcoded symbol assumptions. Comprehensive offline unit test suite in `tests/test_full_3yr_acquisition.py`.

The November 5 offline audit additionally checks receipt/HTTP metadata, actual attempt budgets, parser agreement, continuous-minute counts, and raw-derived reporting. As of November 7, all 325 manifest windows (6,545,612 candles, 312.8 MB) are verified complete on `/Volumes/AlphaGrid Data/Live/offline-dev/historical-raw-dhan` with 0 missing files and 0 hash errors. The research dataset is sealed under `data/research_snapshot_manifest_v1.json` (`alpha-grid-snapshot-2026-11-07-v1`, Merkle root SHA-256 `d1974a10450b85555fc83ce33d8b5191eddfd011a77ffbff78d82a3d7e7badec`). See `DECISIONS.md` for research audit findings, masks, and baseline specifications.

## Cross-equity ML research model & sufficiency audit — 2026-11-05 IST

`src/quant/research/cross_equity_model.py` implements Alpha Grid's minimal, reproducible cross-equity opportunity research framework (`alpha-grid-cross-equity-ml-v1`). Features 5 causal point-in-time metrics (past 5m return, 30m breakout distance, 15m relative volume, realized volatility, normalized time-of-day), fixed 15m outcome horizons, and pure NumPy regularized logistic regression. Evaluates 5 comparative arms (Null, Momentum, Breakout, ML, NO TRADE) with realistic Dhan/NSE statutory fee modeling and portfolio capital competition. Unit tests in `tests/test_cross_equity_model.py`. Sufficiency audit confirms local data is limited to 5 sessions (Oct 26–30), requiring operator authorization for 2–3 year historical backfill.

## Momentum lookback misclassification correction — 2026-11-05 IST

`src/quant/apps/live_simulation.py` `_old()` requires exact feed session continuity and observations strictly within `[lookback_ns, lookback_ns + 60s]`. Truncated lookback buffers (< 15m) return `None`, cleanly routing into `PAST_LOOKBACK_UNAVAILABLE` abstention rather than spuriously evaluating 30–60s returns against 15m friction hurdles. `ingest()` history purge retains `lookback_ns + 60s`.

## Mac external data location — 2026-11-05 IST

The Mac launcher keeps current private app data, QJ01 captures, saved REST/account responses, training artifacts, and simulation ledgers in `/Volumes/AlphaGrid Data/Live/offline-dev` on an encrypted image backed by the connected HDD. It mounts with a saved Keychain item when available, otherwise prompts for the image passphrase in an interactive Terminal or macOS SecurityAgent; it stops local services on disconnection. The HDD archive at `Archive/old-workspace-2026-10-09.tar` is a verified byte-preserving copy of the removed 11 GB older workspace. Code, credentials, and the existing ignored `data/historical` and `data/instruments` source evidence remain on the Mac; use the active launcher for new runtime data. See `DECISIONS.md` for verification and the HDD no-deletion constraint.

## Targeted capture reliability and order-book amendment v3 — 2026-11-04 IST

`rust/crates/quant-journal` extends `JournalWriter` with automatic directory detection and seamless delegation to `SegmentedJournal` (4 GB capacity, 64 MB chunk rotation) alongside single-file journal capacity raised to 512 MB. This resolves the QJ01 raw capture halt observed on November 4 at 15:06 IST when `STANDARD_FULL` (packet code 8, 5-level book) reached the legacy 128 MB ceiling, preserving frame hashes, durability, and recovery guarantees. Python preflight (`dhan_quote_preflight.py`) and Rust telemetry (`quant-telemetry`, `quant-engine`) inspect active writer capacities accordingly.
`src/quant/research/prospective.py` reconciles NSE cash transaction charges to Dhan's published uniform tariff of 0.0030699% (₹3.0699 per lakh turnover), updating fee models for ₹5,000, ₹15,000, and ₹100,000 virtual deployments.
`src/quant/research/order_book_imbalance.py` audits `evaluate_filter_economics` to enforce strict single-counting of avoided trade P&L, fees saved, missed entries, dynamic capital release on position exit, and redeployment value without double-counting spread or statutory charges, while tracking sequential portfolio capital constraints (₹15,000 ceiling). Outcomes in `locate_outcome_quote` enforce exact feed session identity, causal availability ($T_{\text{maturity}} \le T_{\text{exit}} \le \text{as\_of}$), and deterministic ordering. Capture startup rotates into segmented journal directories via `quant-journal`, with transparent reader support across flat and segmented formats.
Tripathi et al. (2021) *Finance Research Letters* article identifier is corrected to **101863** (formerly 101831). All research updates are formally pre-registered in `ORDER_BOOK_AMENDMENT_V3` (`alpha-grid-order-book-amendment-v3`) preserving the frozen November RELIANCE holdout and exploratory November 4 status.

## Engineering fixes: Cross-platform Merkle, NSE ingestion, and 20-level depth telemetry — 2026-11-04 IST

`tools/repo_context.py` provides cross-platform Merkle tree context validation and refresh (`python tools/repo_context.py [--check]`), maintaining full compatibility with `tools/repo-context.ps1` for non-Windows environments.
`src/quant/apps/ingest_nse_evidence.py` ingests manual browser downloads of official daily `NSE_CM_security_DDMMYYYY.csv.gz` files, verifying integrity and normal market eligibility across all 25 frozen universe stocks, generating verified `.manifest.json` sidecars and updating `calendar.json` observation evidence.
`quant-depth` exports verified 20-level depth books to `data/telemetry/depth-books.json` via `--depth-books`, polled by `telemetry_local.rs` using atomic seqlock updates and streamed to the web cockpit (`web/src/stream.ts`), allowing real-time depth inspection on the trading dashboard.

## Live Full and depth capture, chart time display — 2026-11-04 IST

The operator explicitly approved a persistent read-only `STANDARD_FULL` and
`DEPTH_20` scope for the frozen 25 NSE_EQ IDs. The private scope now records
both feeds and the launcher has one external socket per feed. A read-only
QJ01 sweep during the session counted 98,154 original standard frames,
118,903 code-4 Quote packets and 21,279 code-8 Full packets. The local
gateway snapshot showed 25 raw observed prices and 25 `STANDARD_FULL_RAW`
same-packet five-level books. The depth collector's private status showed
1,243 received frames and a connected socket at that check. These are
capture observations, not decision-eligible quotes or simulated fills.

`read_model.rs` now publishes genuine raw LTP and optional Full books with
frame hash, receipt and durability metadata after QJ01 sync. The browser
snapshot validator accepts only a Full book paired to its market packet by
protocol, feed session, frame, offset, SHA-256 and receipt timestamp. Raw
packets retain `RAW_CAPTURE_VALIDATION_PENDING`; the Python portfolio worker
rejects them. The Candle workstation has a separate live LTP line and keeps
historical Dhan OHLCV as source bars. TradingView's default UTC axis caused
06:30 to be shown for a 12:00 IST source instant; custom tick and crosshair
formatters now label instants in IST without rewriting archive timestamps.

The depth collector installs Rustls' crypto provider before its live socket.
The launcher counts actual network attempts rather than offline preflight
passes toward its daily budget. Depth failure is reported separately from
standard capture. Dated NSE security/CAS evidence remains unavailable, so
the live observations remain descriptive and simulation is paused. Focused
tests passed for Rust depth/telemetry, Python live worker and web stream,
chart and app; web build and lint passed. Cross-platform Merkle checking and refresh is now handled via `tools/repo_context.py`.

## Standard Full packet depth, 2026-11-04 IST

The genuine November 3 raw QJ01 has 315,817 frames and 449,347 response-code-4
Quote packets across all 25 IDs, with no code-8 Full packets. Its saved scope
authorizes `STANDARD_QUOTE`, so missing books were caused by RequestCode 17.
The existing standard socket now also supports RequestCode 21 under the
approved `STANDARD_FULL` scope. The Rust decoder validates all five price, quantity and
order-count levels; a bad book becomes unavailable for that instrument without
discarding other decoded packets. Rust syncs the original QJ01 frame before
publishing any book, hash and timestamps. With unresolved decision validation,
it exposes `STANDARD_FULL_RAW` descriptive depth and a clearly labeled raw
LTP observation, with neither eligible for portfolio decisions;
the portfolio worker still sees validation pending and cannot ingest it. With
all decision gates satisfied, `STANDARD_FULL` also supplies a same-packet
quote. The local snapshot carries five-level spread, displayed liquidity,
imbalance and receipt-ordered depth-change/OFI proxies. The Python v2 worker verifies that
the book and quote share a receipt and checks displayed quantity for virtual
reference decisions. Overview, Market intelligence, Order book and Feature
monitor show the same book with its validation state; the simulator view uses
only eligible observations. Raw capture still runs when decision-validation inputs are
missing; the saved Full and depth scope was approved on November 4.

## Eligible quote projection into virtual portfolio, 2026-11-03 IST

`apps/live_feed_scope.py` first admits the existing approved 25-ID raw capture.
It reuses the dated NSE/master/calendar and independent 100 ms clock checks
for a separate virtual-decision projection; failure leaves the same raw socket
running without publishing decision inputs. Rust `StandardCapture` fsyncs each
original QJ01 frame, then `TelemetryPublisher` may publish its decoded quote
with raw SHA-256, frame ID, packet offset, receipt and durability timestamps.
The gateway marks validation-pending when that projection is unavailable and
can downgrade/recover it as clock evidence changes. The Python v2 worker reads
only this local gateway snapshot. Because the approved Dhan code-4 feed has
LTP but no bid/ask, it uses the existing Node GET cache of original Dhan REST
quote responses only when the book and Rust packet receipts are each under
two seconds old and instrument IDs match. No new Dhan poll is made by the
worker. It stores both hashes, availability and decision times with each
observation; missing/crossed books reject only the affected instrument.
The existing launcher can make one bounded scoped reconnect if dated proof
arrives after raw capture started; exhausting its new-connection budget does
not stop an already running socket.
`SimulationWorkspace` shows book source and receipt-to-worker delay. See the
latest decision note for genuine archive coverage and live blockers.

## Concurrent local simulation and service recovery, 2026-11-03 IST

`tools/start-cockpit-macos.sh` now starts `apps/live_simulation.py --portfolio-v2`
with Node, Rust gateway, training worker and Vite. The active simulator keeps
up to three concurrent virtual positions under the existing cash-paper-risk-v1
default capital/exposure/loss limits. Past-only, validated Dhan standard-feed
quotes can lead to independent BUY, HOLD, REDUCE and SELL decisions without a
daily trade-count cap. The separate private `simulation-portfolio-v2.jsonl`
is hash-chained and fsynced before state changes; restart replays it and
deduplicates quote/decision IDs. RAW_CAPTURE_ONLY packets with unresolved
research validation stay out of this decision path. The UI reads the v2
positions, virtual cash, cost-basis equity, decisions and costs. The earlier
single-position historical adaptive replay remains an archived research
artifact. Non-gateway local workers receive bounded restart attempts; a
failed optional worker does not terminate the remaining services. Private
`service-events.jsonl` records failures.
Rust owns the approved socket, original-frame QJ01, decoder, instrument
routing, market-state projection and gateway. Python owns the v2 virtual
portfolio decisions, accounting, risk-budget reservation and exclusive-ledger
recovery; it does not call QP01 or broker routes. In `--raw-capture-only`, Rust
quarantines packets and does not publish normalized quotes to that Python
worker. This is the current end-to-end research-validation boundary.

## Cross-equity adaptive v2 exploratory replay, 2026-11-03 IST

`src/quant/apps/cross_equity_adaptive.py` rechecks the immutable ALL_25
response hashes, reproduces the frozen five-trade simulator, exports all 125
10:00 rankings and five complete selected minute paths, and runs a separate
every-five-minute adaptive reference replay for 60/120/300-second assumed
availability delays. It writes private immutable audit, decision and report
artifacts. `web/server/trading-service.mjs` verifies their hashes and serves
the report to the existing Simulator tab. All six exploratory variants are
net negative; no model was fitted or live worker replaced. See
`docs/cross-equity-adaptive-v2-2026-11-03.md` for results and limits.

## One-shot Mac cockpit startup, 2026-11-03 IST

`tools/start-cockpit-macos.sh` sets its own Node 24 PATH and repository working
directory. A repeat invocation reattaches to a ready supervised instance and
opens the plain loopback URL; crashed Alpha Grid listeners are replaced only
after checking their command paths. The local Node service offers a loopback,
same-origin, custom-header sign-in that issues the existing Rust-signed
HttpOnly cookie, so a browser refresh needs no URL fragment. At startup it
checks and caches all 25 saved October archives, including the original pinned
RELIANCE response, loads the newest hash-verified saved REST quote, and makes
one read-only quote refresh for a still-valid scope even after market close. The
capability API reports the ALL_25 count and most recent raw frame count even
after the market feed closes. Account sync and market-hour quote polling remain
backend-owned. See `DECISIONS.md` for runtime validation and limitations.

## Market-hour REST fallback, 2026-11-03 IST

`web/server/trading-service.mjs` now owns a one-minute, session-window
read-only `/v2/marketfeed/quote` poll for the approved frozen 25 IDs. It shares
the saved original response and parsed current snapshot with the signed
cockpit route; the browser polls that local cache without creating additional
Dhan requests or sockets. The React views label
these measurements `REST_SNAPSHOT` and keep feed-only metrics distinct.
`tools/start-cockpit-macos.sh` checks the dated raw-capture event log so the
bounded live WebSocket attempt ceiling survives launcher restarts. On
November 3 the Rust request-path and unsigned-quantity decoder defects were
fixed. The approved 25-ID standard socket now captures original frames in
the private raw QJ01 while REST provides separately labeled current prices
and five-level books. The daily journal bound is 128 MiB with preflight
storage checks; raw observations remain validation-pending and NO TRADE.

## Read-only Dhan integration, 2026-11-03 IST

`tools/start-cockpit-macos.sh` now always registers the frozen 25 IDs and
symbols with the offline gateway. With a valid owner-private local token and
client ID, `apps/live_feed_scope.py --auto-local-scope` performs one cached
`GET /v2/profile` check for that token and, only when Dhan reports an active
Data API plan, writes the exact persistent standard-feed scope. A revoked
file is never recreated automatically. The existing raw-capture preflight and
research/production eligibility remain separate. During closed hours the
gate reports `MARKET CLOSED / AUTO_CONNECT_ARMED` only after valid scope and
credential checks; no socket is opened.

The local Node service now loads the private credential files, refreshes only
the three read-only account endpoints (`fundlimit`, `holdings`, `positions`),
and exposes an authenticated one-shot profile test and bounded 25-ID REST
market-quote snapshot. Complete REST response bytes, receipt, HTTP status,
request and SHA-256 are kept under the private `offline-dev/rest-snapshots`
directory before parsing. The React cockpit distinguishes REST snapshots,
archived candles and live WebSocket observations. The Mac Paper view reads the
separate virtual simulation ledger and does not send QP01 mutations. The
Candle workstation plots original historical OHLCV at the provider's source
labels when live quote-sampled bars are absent, with unverified label
semantics displayed beside the chart.

`apps/local_training_worker.py` now accepts a frozen-universe ticker or
`ALL_25`. It reuses the pinned RELIANCE October 26–30 response and the existing
Dhan acquisition writer to request at most one no-retry 1-minute intraday
response per missing instrument for that same five-day interval. Saved raw
bytes, metadata and checksums are verified before the existing frozen rule is
replayed. This is an exploratory source/replay job with no newly fitted
cross-stock model. `apps/cockpit_cache.py --cross-stock-symbol` serves only
verified acquired local responses for selected-instrument historical views.
The completed `ALL_25` job also writes an owner-private, checksummed
`cross-stock-simulation.latest.json` pointer. The Node
`/trade/cross-stock-simulation` route checks its immutable artifact and
25 response hashes; the existing Simulator view uses that saved cross-stock
replay while keeping the earlier RELIANCE-only replay as its fallback.
The earlier 45,945-bar RELIANCE exploratory artifact is displayed from its
saved bytes in Statistical Research; absent first-hit incidence remains
explicitly unmeasured.

Runtime on November 3 IST: local services listen on 8765/8767/5173, the
gateway registers all 25 manifest names/IDs, and the Dhan profile check
reports an active Data API plan for the newly stored token. The saved scope
is `AUTO_CONNECT_ARMED` while the market is closed, with zero sockets/frames.
Six genuine 25-ID quote responses and 24 genuine historical intraday
responses have been retained and checked; the 25-stock October replay is
exploratory. The account cache shows funds and positions from read-only HTTP
200 responses, while holdings returned HTTP 500 (`DH-1111`). No live feed connection or
causal performance eligibility has been established. See `DECISIONS.md` for
counts and limitations.

## Closed-market activation display and token check, 2026-11-02

The Mac launcher now asks `apps/live_feed_scope.py --display-only` for an
unexpired, enabled, exact 25-ID permission even when credentials or market
hours block capture. It starts the offline Rust gateway with those IDs and the
frozen manifest, so the gateway's instrument names and dropdown reflect the
approved universe without opening Dhan. The private token loader accepts the
existing OAuth-consent token or an operator-installed Dhan Web token with
truthful source and expiry metadata. The explicit `--check-profile` CLI makes
one read-only `GET /v2/profile` only after a saved scope and valid local token;
it prints only a status, never account fields. This check is not account sync
and is never automatic on approval. See `DECISIONS.md`.

## Quarantined read-only capture, 2026-11-02

The persistent v2 25-ID approval now permits `RAW_CAPTURE_ONLY` during a
weekday 09:15–15:14 IST candidate continuous-session window after verifying
the owner-private token, the frozen manifest against a checksummed Dhan master
at most seven days old, and private journal capacity. The launcher and Rust
gateway repeat the raw preflight. Original standard frames go only to a
separate `raw-standard-YYYY-MM-DD.qj`; no normalized quote is published to the
simulator. Missing dated NSE identity/CAS, dated calendar classification, or
clock precision remain explicit unresolved conditions in the private
`raw-capture-validation-YYYY-MM-DD.jsonl`. Capture lifecycle events and last
raw receipt are retained separately. Research and production gates remain
unchanged; this is a quarantined observation path. See `DECISIONS.md`.

## Market-data activation, 2026-11-02

Trading Settings' signed local permission API persists an exact, revocable
25-ID scope. `apps/live_feed_scope.py` now checks saved authorization apart
from market hours, allowing an honest closed-market armed state. During a
verified session, the Mac launcher starts one Rust standard gateway and,
only if explicitly approved, one existing `quant-depth` live read-only
collector. Both use backend-only credentials, the same preflight and stop
marker. Depth status reports observed socket/frame state without claiming
gateway order-book integration. See `docs/context/DECISIONS.md` for tests and
remaining dated-evidence blockers.

## Persistent market-data permission, 2026-11-02

`web/server/market-data-access.mjs` backs Trading Settings' signed-session
Market Data Connection controls. It stores a private exact 25-ID standard-feed
approval, duration, expiry, auto-connect and revoke state. The existing Mac
launcher polls `apps/live_feed_scope.py`, which can carry a persistent
approval into a later session only after current Dhan/NSE/calendar/clock and
storage preflight. Optional depth uses the existing collector when explicitly
approved and available. Automatic dated public-evidence acquisition remains
blocked; no authenticated feed ran in this implementation task.

## Mac cockpit integration, 2026-11-02

The Mac launcher also supervises `apps/local_training_worker.py`. The local
Node service exposes session-protected `/trade/capabilities` and
`/trade/training`; Research now submits a single bounded exploratory job for
the pinned RELIANCE archive and reports missing 25-name history as BLOCKED.
The artifact is private, immutable and never promoted. The shared capability
strip reports local service, gateway, telemetry, Dhan feed, socket counts,
archive, simulation and training states. The Mac App disables QP01 paper
approval controls. Candle workstation defaults to an actually available
historical session, fixing an empty table when the Mac archive starts October
26. See `docs/context/DECISIONS.md` for runtime results.

## Persistent Mac simulation worker, 2026-11-02

`tools/start-cockpit-macos.sh` now supervises `apps/live_simulation.py` with
the existing Rust gateway, Node local service and React cockpit. The worker
reads only authenticated local post-QJ01 snapshots, persists an independent
hash-chained simulation ledger under the user's private offline-dev directory,
and recovers positions/cash by replaying it. The Simulation view shows worker
status and an authenticated Stop control. `apps/live_feed_scope.py` admits a
future exact dated 25-name quote scope only after separate operator approval,
same-day NSE/Dhan/calendar/storage evidence and a 100 ms clock gate. Rust's
standard subscription is constrained to the frozen 25-ID manifest; depth is
disabled for this quote-only rule. With no approved scope, the launcher and
saved history stay offline and NO TRADE. See `docs/live-simulation-macos.md`.
`apps/live_feed_credentials.py` verifies the existing private token (OAuth, DhanHQ API access token, or renewed web token), its SHA-256 hash, client ID integrity, and expiry, supports atomic renewal via `/v2/RenewToken`, and supplies it only to the live Rust gateway. The approval names exact IDs, feed and expiry; the launcher publishes a non-secret gate status and supervises the gateway through that expiry.

## November 2 Mac live pilot preflight

`docs/live-pilot-2026-11-02-preflight.md` records the authorized, narrower
one-standard-plus-one-depth scope and its continuing NO GO: no same-day NSE
MII identity/CAS file (HTTP 404) or dated November 2 calendar evidence.
A fresh public Dhan master verifies today's three Dhan mappings only. The
depth collector now journals original binary frames with SHA-256 before
decoding, pairs independently received sides within one second only when
uncrossed and complete, defaults to zero reconnects and requires an explicit
cutoff. The standard collector now accepts only the exact three-ID Quote-17
subscription, preserves raw QJ01, checks Mac `sntp` at the unchanged 100 ms
bound and derives a 14:45 cutoff. The complete pilot preflight returned
`BLOCKED`; no authenticated Dhan socket was opened.

## Cross-stock opportunity discovery (2026-11-02 IST)

The subsequent inspection-only
`docs/cross-stock-plan-review-2026-11-02.md` preserves that fixed universe
and frozen plan but **defers** the 24 historical POSTs as the next acquisition.
The three top-ranked dated IDs (HDFCBANK 1333, RELIANCE 2885, INFY 1594)
were rechecked against 20 original NSE ZIPs, the October 30 MII security file
and the Dhan master. Current Mac live capture is NO GO: standard Quote-17 is
RELIANCE-hardcoded and Windows-clock-gated; depth QJ01 lacks original binary
frames and a hard session stop. The review specifies narrow fixes and a
separately approvable November 4 one-session, three-quote-plus-one-depth
quality pilot. No authenticated request/socket or code behavior change was
made. Focused offline decoder/journal/depth tests passed.

`docs/cross-stock-universe-2026-11-02.json` pins 20 original October NSE CM
UDiFF final bhavcopies, the October 30 NSE MII security file, and the existing
November 1 Dhan master. Stable NSE ID/INE ISIN, normal-market eligibility and
October median traded value yield a fixed 25-ID starting universe. The four
October 30 non-CAS flags and all source hashes/retrieval times are retained.
`docs/cross-stock-opportunity-plan-2026-11-02.md` freezes one cost-aware
cross-sectional research question, baseline/evaluation/portfolio gates and a
separately approvable 24-POST historical source/coverage pilot. The October
selection cannot be used as an unbiased October backtest. No cross-stock Dhan
data, live quote/depth capture, new model or order was produced. The failed
RELIANCE candidate, frozen November experiment, QJ01/QP01 and DATA_ONLY /
NO TRADE remain unchanged. PowerShell is not installed on this Mac, so the
context Merkle check/refresh is unavailable.

## macOS offline development path (2026-10-29)

`tools/start-cockpit-macos.sh` starts the existing Rust gateway, Node account
service, and Vite cockpit on 8765/8767/5173 in offline DATA_ONLY / NO TRADE
mode. It requires a local Python 3.12 environment, a built `telemetry_local`,
installed web dependencies, and Node 24. It removes Dhan credentials from the
child environments and does not load the Windows private archive. Mac-local
QP01 approvals and settings use the current user's private
`~/Library/Application Support/AlphaGrid/offline-dev` directory, with owner and
POSIX mode checks. The existing Windows launcher remains the online path.

The October 30 historical-data transfer proposal in
`docs/mac-historical-dhan-import.md` was superseded by direct Mac acquisition.
The new ignored `data/historical/raw-dhan-macos-2026-11-01` archive contains
the original October 26-30 NSE_EQ/2885 one-minute response, checksum and
receipt metadata (HTTP 200, 87,694 bytes, SHA-256
`a4c61e3d5ad8ce3b9bf78317ac73ff15502bcefa52bd6a09c3771b866d2d8653`).
`cockpit_cache.py` reuses `verify_saved_response` and a pinned hash, requires
360 bars on each of the five dates, and prefers the Mac archive when present.
The October 15-16 Windows path and bytes are unchanged. `gateway.rs` admits
only these two pinned archive/coverage pairs. An isolated DATA_ONLY gateway
served the Mac replay with 1,800 NO_TRADE decisions and no orders, fills or
approval writes. The already-running gateway on port 8765 predates these
changes and requires a later restart to show the new archive in that process.
The later, separately authorized six-month May 1–October 30 backfill added
two new raw responses alongside that existing archive, for a verified total of
45,945 candles on 125 sessions. See `docs/reliance-six-month-mac-audit.md` for
the hashes and training blockers. The existing forward-observation gate has
not admitted this archive to predictive training.
`apps/descriptive_dhan_opportunities.py` now makes a pinned, offline-only
source-path summary for 30m, 60m and remaining continuous-session windows.
It requires the three original response hashes and intact session coverage,
then writes a private exploratory report with session-grouped uncertainty.
`docs/reliance-30-60-remaining-readiness.md` records the results, unavailable
cost observations, conditional future probe and separate proposed experiment.
The later owner-authorized offline `apps/exploratory_dhan_timing.py` fits one
fixed ridge baseline under 60/120/300-second assumed availability delays,
scores August–September chronologically and keeps its private report and
coefficients exploratory. `docs/reliance-assumed-timing-exploratory.md` records
the negative net result. No model is registered or connected to paper/cockpit.
The separately frozen first-signal 60-minute falsification in
`docs/reliance-60m-frozen-protocol.md` and
`docs/reliance-60m-first-signal-result.md` reuses pinned raw-response checks,
the existing session walk-forward splitter and session bootstrap. Its isolated
`apps/reliance_60m_falsification.py` compares unconditional, preregistered
15-minute momentum and 20-prior-session volume confirmation under three
assumed delays and published Dhan fee estimates. The volume candidate failed
the conservative net, delay-robust uncertainty and probability-score gates;
no logistic fit, model registration or paper connection followed. The ignored
private JSON holds the daily reference outcomes. This is exploratory on an
already inspected period; all causal gates and DATA_ONLY / NO TRADE persist.
The read-only `docs/reliance-60m-closure-audit.md` independently reconstructs
selected close references, per-opportunity whole-share tariff estimates,
paired session resampling and probability scores from the pinned originals.
It reconciles the saved 42-session net accounting, quantifies the narrow
hypothetical spread/slippage headroom and closes this particular candidate;
no archive, research model, QJ01/QP01 or experiment behavior changed.
The inspection-only `docs/real-microstructure-readiness-2026-11-02.md` checks
the Rust standard quote and 20-level depth decoders, capture, segmented QJ01,
replay, telemetry and paper gates against current official Dhan v2 contracts.
Mac-local genuine live quote/depth frames were not found. Standard scoped raw
frames are retained before decode; the depth path currently retains decoded
sides/books only, and its live CLI lacks a dated session/clock cutoff and
defaults to two reconnects. The report specifies the smallest source-integrity
and bounded-runtime fixes before a separately approved, one-instrument,
two-socket observation can be considered. No live scope was authorized or run.
`docs/reliance-candle-timing-review.md` independently recomputes the source
counts and descriptive point statistics from raw JSON, audits the pre/post
August 3 session shapes against NSE dates, separates those checks from
RELIANCE-specific CAS eligibility, and drafts an eight-poll future probe and
Dhan support inquiry. The probe has not run; its offline plan now specifies
November 3 and eight observations, pending dated preflight and separate
authorization. `docs/nov3-dhan-probe-preflight.md` records the official NSE
source, exact Mac evidence commands and the currently unmet clock/identity
and operator gates. The probe's CLI does not enforce every external gate.

For the separate Mac Dhan API-key consent path, `tools/dhan_oauth_callback.py`
accepts an explicit private `--env-file .env` containing client ID, API key
and secret, then uses its existing browser callback and private token store.
It does not run as part of the offline cockpit. Its separately authorized
consent flow saved a private access token. The operator separately authorized
the one historical POST, which has now been consumed; do not rerun it.

`.github/workflows/validate.yml` now runs the same locked Python, Rust, and
React validation on Ubuntu and macOS. The prior review commit `888c8e7` had no
CI run on its branch because the workflow listens only to main pushes and pull
requests. A successful older main-branch Ubuntu run is not evidence for this
review commit or for macOS. The Mac job also starts the three existing services
without a browser or Dhan credentials and checks their loopback health routes.

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
The cockpit loads it through the existing verified cache. A bounded retrospective
replay computes past-window price/volume descriptions and explicit NO TRADE
counts; it does not publish quotes, outcomes, paper fills or QP01 decisions.
It remains outside the causal research dataset.

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
| Dhan availability probe | `apps/dhan_candle_availability.py`, `docs/reliance-candle-timing-review.md` | Offline plan by default; guarded, fixed eight-poll November 3 historical-chart observation proposed for separate approval. It preserves original responses and reports sampled first-seen/revisions without claiming candle completion or causal eligibility; `test_dhan_candle_availability.py` uses synthetic software fixtures only. |
| Statistical research | `src/quant/research/` | Dataset joins, labels/censoring, baselines, prospective measurement (`prospective.py`), five-level depth imbalance (`order_book_imbalance.py`), Kalman/OU, competing risks, walk-forward, session bootstrap, utility, inference and registry; `test_research_*.py`, `test_prospective.py`, `test_order_book_imbalance.py`. |
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

## First offline simulator, 2026-11-02

`src/quant/apps/historical_simulator.py` verifies the pinned genuine Mac
RELIANCE Dhan response through the existing acquisition verifier and performs
deterministic `SIMULATION_ONLY` reference replay. `src/quant/apps/cockpit_cache.py`
adds its result to the authenticated historical cache; the Rust telemetry
gateway guards that provenance and `web/src/SimulationWorkspace.tsx` displays
virtual equity, decisions, ledger and cost comparison. Synthetic accounting
tests are in `tests/test_historical_simulator.py`; UI test is in
`web/src/SimulationWorkspace.test.tsx`. The frozen rule, actual result and
missing aligned cross-stock data are documented in
`docs/historical-simulator-2026-11-02.md`.
The same CLI also accepts `--six-month`, verifies all three pinned Mac
RELIANCE responses through the shared acquisition verifier and the 125-session
coverage audit, and replays the unchanged rule. Response SHA transitions are
admitted only between trading dates. The October 26–30 cockpit cache remains
separately pinned. The Mac launcher now `exec`s Vite in its supervised child,
so ordinary shutdown reaches that listener.

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
