# Engineering Fixes Log — 2026-11-04 IST

This document records actionable engineering fixes implemented across the Alpha Grid repository. It is updated progressively as changes are made.

---

## 1. Cross-Platform Python Merkle Context Index (`tools/repo_context.py`)

### Problem
- The context validation rule in `AGENTS.md` requires running `powershell -NoProfile -File tools/repo-context.ps1 -Check` to detect repository changes against `docs/context/merkle.json`.
- On macOS and Linux development hosts, PowerShell (`powershell` / `pwsh`) is not installed by default. This previously prevented running `-Check` or refreshing `merkle.json`, leaving the index stale after recent updates.

### Implementation
- Created `tools/repo_context.py` with 100% parity to `tools/repo-context.ps1`:
  - Evaluates all Git-tracked files via `git ls-files --cached` plus the explicit `$supportFiles` list.
  - Implements the exact canonical rules:
    - File content: SHA-256 in lowercase hex.
    - Blob rule: `SHA-256(UTF-8("blob\0" + content_sha256))`.
    - Tree rule: `SHA-256(UTF-8("tree\0" + concat(kind + "\0" + name + "\0" + sha256 + "\n")))` in ordinal child order.
    - Manifest root: Merkle root of `.`.
  - Added CLI flag `--check` which compares against `docs/context/merkle.json` and outputs detailed diffs:
    - `ADDED <path>`
    - `CHANGED <path>`
    - `REMOVED <path>`
    - `BRANCH <dir>`
    - `REMOVED BRANCH <dir>`
    - Current root hash.
  - Created unit tests in `tests/test_repo_context.py` covering hash calculation and Merkle tree generation.
- Added `tools/repo_context.py` to `SUPPORT_FILES` in both `tools/repo-context.ps1` and `tools/repo_context.py`.
- Updated `AGENTS.md` to reference `python tools/repo_context.py --check` on macOS/Linux alongside `powershell -NoProfile -File tools/repo-context.ps1 -Check` on Windows.

### Verification
- Ran `.venv/bin/python -m pytest tests/test_repo_context.py`: 2 passed in 0.10s.
- Ran `python3 tools/repo_context.py --check`: successfully inventoried tracked files, detected all recent November 1–4 additions/modifications and deleted branches without errors.
- Refreshed index with `python3 tools/repo_context.py`: successfully indexed 316 files with root `dda86ac7295ff1974132e1e739c49463d7e163ece3f6424ea47a544342a7a848`. Subsequent `python3 tools/repo_context.py --check` returned exit code 0 (`Context index current`).

---

## 2. Manual NSE Evidence Ingestion Tool (`apps/ingest_nse_evidence.py`)

### Problem
- Automated downloads of daily NSE Listed securities MII files (`NSE_CM_security_DDMMYYYY.csv.gz`) from `nsearchives.nseindia.com` fail with HTTP/2 stream errors (`curl` code 92) and timeouts (`curl` code 28).
- Without today's dated MII security file and `.manifest.json` sidecar in `data/instruments/`, strict preflight fails (`[Errno 2] No such file or directory: '.../NSE_CM_security_04112026.manifest.json'`), causing `virtual_decision_eligible` to remain `false` and pausing `apps/live_simulation.py` with `SIMULATION_PAUSED_VALIDATION_PENDING`.

### Implementation
- Built `src/quant/apps/ingest_nse_evidence.py` to allow the operator to ingest a browser-downloaded `NSE_CM_security_DDMMYYYY.csv.gz` from the official NSE site (`https://www.nseindia.com/all-reports`):
  - Validates GZIP integrity and parses header schema (`FinInstrmId`, `TckrSymb`, `SctySrs`, `ISIN`, `ElgbltyNrmlMkt`, `ElgbltyClsgAuctnSsn`).
  - Verifies exact coverage of all 25 frozen universe stocks:
    - Unique row match per symbol.
    - Normal market eligibility (`ElgbltyNrmlMkt == "1"`).
    - Closing auction eligibility flag valid (`ElgbltyClsgAuctnSsn` in `{"0", "1"}`).
  - Computes SHA-256 and byte size.
  - Copies atomically to `data/instruments/NSE_CM_security_DDMMYYYY.csv.gz`.
  - Generates verified sidecar `data/instruments/NSE_CM_security_DDMMYYYY.manifest.json` with retrieval timestamp, source URL, sha256, byte count, and instrument inventory.
  - Updates `data/config/calendar.json` with the session entry (`ORDINARY_OBSERVATION_ONLY`, `09:15–15:15 IST`, `verified=false`) and populates `observation_evidence` matching `dhan_quote_preflight.py` validation requirements.
- Created unit tests in `tests/test_ingest_nse_evidence.py` verifying successful ingestion, calendar updating, and preflight pass via `verify_dated_nse_security`.

### Verification
- Ran `.venv/bin/python -m pytest tests/test_ingest_nse_evidence.py`: 2 passed in 0.08s.
- Tested CLI help and error handling on incomplete files.

---

## 3. 20-Level Depth Telemetry Hookup to Cockpit Dashboard

### Problem
- The DhanHQ 20-level market depth feed was captured by `quant-depth` into binary `.qj` archives, but depth books were not routed into `quant-telemetry` or the web dashboard.
- The web frontend's depth ladder (`DepthTable` in `web/src/App.tsx`) was restricted to 5 levels from `STANDARD_FULL` packets, and `validDepth` in `web/src/stream.ts` rejected any depth book without source `STANDARD_FULL` or with != 5 levels.
- This created a telemetry gap: live 20-level order books captured in real time were not visible on the operator's trading cockpit.

### Implementation
- **Depth Capture State Export (`rust/crates/quant-depth`)**:
  - Added `latest_books: Arc<parking_lot::Mutex<HashMap<String, DepthEvent>>>` to `Capture`.
  - Added `--depth-books <PATH>` CLI argument to `quant-depth` (configured via env var `ALPHA_GRID_DEPTH_BOOKS_FILE`).
  - Upon receiving and validating paired 20-level depth frames, `Capture` updates the in-memory map and atomically flushes JSON snapshots (`depth-books.json.tmp` -> `depth-books.json`) using atomic file rename.
- **Telemetry Read Model Integration (`rust/crates/quant-telemetry`)**:
  - Added `update_depth_book(&self, key: &str, depth: WireDepth) -> bool` to `TelemetryShared` in `rust/crates/quant-telemetry/src/read_model.rs`.
  - Coordinated slot updates using `WriteGuard::new(self)` to maintain seqlock consistency across concurrent WebSocket client reader threads.
  - Added CLI argument `--depth-books <PATH>` to `telemetry_local.rs` with a 500 ms asynchronous polling loop that ingests `depth-books.json` and updates instrument slots.
- **Process Orchestration (`src/quant/apps/live_feed_credentials.py`)**:
  - Exported `ALPHA_GRID_DEPTH_BOOKS_FILE` (`data/telemetry/depth-books.json`) in `launch_cockpit_macos`.
  - Provided `--depth-books <PATH>` to both `telemetry_local` and `quant-depth`.
- **Frontend Streaming & Visualization (`web/src/stream.ts`, `web/src/App.tsx`)**:
  - Updated `validDepth` in `web/src/stream.ts` to accept `DEPTH_20` envelopes with up to 20 bid/ask levels when `derived_pairing === true`.
  - Updated `depthFresh` in `web/src/App.tsx` to handle depth timestamps independently.
  - Added unit test in `web/src/stream.test.ts` for 20-level depth envelope validation.

### Verification
- **Rust Workspace**:
  - Ran `cargo test --manifest-path rust/Cargo.toml`: all 11 crates passed (including seqlock atomic unit tests in `quant-telemetry`).
  - Ran `cargo fmt --all --manifest-path rust/Cargo.toml -- --check`: passed cleanly.
- **Web Dashboard**:
  - Ran `npm test --prefix web`: 33/33 tests passed (including `DEPTH_20` stream tests).
  - Ran `npm run build --prefix web` (`tsc -b && vite build`): passed cleanly with 0 TypeScript errors.
  - Ran `npm run lint --prefix web`: passed with 0 errors.
  - Ran `node --test web/server/*.test.mjs`: 20/20 passed.
- **Full Python Test Suite**:
  - Ran `.venv/bin/python -m pytest -q tests/`: 224 passed, 3 skipped in 10.68s.

---

## 4. 20-Level Depth Metrics Synthesis & Multi-View Cockpit Integration

### Problem
- In `quant-depth`, paired 20-level order books did not generate book summary metrics (`quoted_spread_bps`, `five_level_imbalance`, total quantities, order counts).
- In `CommandCenter.tsx`, the 10-stock arena cards previously required `item?.depth?.receive_timestamp_ns === received` (exact receipt timestamp matching between the independent standard quote and depth WebSockets), causing depth spread and imbalance to show "N/A" for 20-depth instruments.
- In `App.tsx`, the Ten-Stock Liquidity Radar, `MarketIntelligence`, and `FeatureMonitor` views strictly checked `depth?.source?.startsWith("STANDARD_FULL")`, omitting `DEPTH_20` packets from displaying book metrics and microstructure details.

### Implementation
- **Telemetry Read Model Book Metrics Computation (`rust/crates/quant-telemetry/src/read_model.rs`)**:
  - Updated `update_depth_book` so that when an incoming `DEPTH_20` book does not yet contain precomputed `book_metrics`, it calculates:
    - Quoted spread in basis points: `(ask - bid) * 10,000 / mid`.
    - 5-level displayed bid and ask quantities and cumulative order counts.
    - 5-level order book imbalance: `(bid_qty - ask_qty) / (bid_qty + ask_qty)`.
  - Stored within `WireBookMetrics` inside the slot's `WireDepth`.
- **CommandCenter Independent Depth Handling (`web/src/CommandCenter.tsx`)**:
  - Updated depth book matching to evaluate depth receipt freshness independently (`depthLive`), allowing 20-level books to display spread and imbalance on the 10-stock cards.
- **Cockpit Views Expansion (`web/src/App.tsx`)**:
  - Updated the Ten-Stock Liquidity Radar table to accept both `STANDARD_FULL` and `DEPTH_20`.
  - Updated `MarketIntelligence` and `FeatureMonitor` to utilize `DEPTH_20` order books for the Book & Microstructure and Five-Level Displayed Book panels.

### Verification
- **Rust Workspace**:
  - Ran `cargo test --manifest-path rust/Cargo.toml --package quant-telemetry`: all 27 tests passed.
  - Ran `cargo fmt --all --manifest-path rust/Cargo.toml -- --check`: passed cleanly.
- **Web Frontend**:
  - Ran `npm test --prefix web`: all 33 unit tests passed.
  - Ran `npm run build --prefix web`: passed with 0 TypeScript errors.

---

## 5. End-to-End Socket, Bid/Ask & Microstructure Feature Integration

### Problem
- Bids and asks from the 20-level depth feed were stored in `snapshot.depth`, but were not propagated to `snapshot.market.best_bid_price_nanos` and `snapshot.market.best_ask_price_nanos`.
- The Python portfolio simulation worker (`apps/live_simulation.py`) only accepted 5-level books from `STANDARD_FULL` packets and rejected or failed to process `DEPTH_20` live books.
- In `CommandCenter.tsx`, stock cards displayed spread and imbalance but lacked explicit top-of-book `BID / ASK` prices, and the arena was fixed to 10 stocks without an option to view all 25 stocks in the frozen universe.
- In `App.tsx` (`Overview`, `CandleWorkstation`, `OrderBook`, `FeatureMonitor`), bid, ask, and spread computations relied on synthetic features or were restricted to 5-level packets rather than seamlessly utilizing live WebSocket 20-level depth books.
- In `TradingWorkspace.tsx`, only REST quotes were listed under Market Data without real-time streaming WebSocket prices and bid/ask ranges.

### Implementation
- **Telemetry Read Model Bid/Ask Propagation (`rust/crates/quant-telemetry/src/read_model.rs`)**:
  - Updated `update_depth_book` to propagate `best_bid_price_nanos`, `best_ask_price_nanos`, and `depth_receive_timestamp_ns` into `snapshot.market`.
  - Initialized `snapshot.market` with valid provenance if depth frames arrive prior to standard quote frames.
  - Implemented `Default` for `WireQuality` in `protocol.rs`.
- **Live Simulation 20-Level Depth Ingestion (`src/quant/apps/live_simulation.py`)**:
  - Updated `_step_instrument` to validate and accept `DEPTH_20` live books with derived pairing and up to 20 levels.
  - Verifies descending bid prices, ascending ask prices, positive liquidity, and uncrossed top-of-book, setting `book_source="LIVE_WS"`.
  - Added unit test `test_portfolio_v2_accepts_twenty_level_depth_books` in `tests/test_live_simulation.py`.
- **Command Center Gamified UI Enhancements (`web/src/CommandCenter.tsx`)**:
  - Added explicit live `BID / ASK` price readout on each Stock Arena card.
  - Added a toggle between **Focus 10** and **All 25 stocks** in the observed universe.
- **Cockpit Views Bid/Ask Integration (`web/src/App.tsx`)**:
  - **Overview**: Connected `Best bid`, `Best ask`, and `Midprice` to `depth.bids[0]` and `depth.asks[0]`.
  - **Candle Workstation**: Added live top-of-book `BID`, `ASK`, and basis-point `SPREAD` to the live observation header.
  - **Order Book**: Removed `full` restriction from spread calculation so that `DEPTH_20` order books calculate and display spread immediately.
  - **Feature Monitor**: Connected Microprice, Relative spread, Imbalance, and OFI to live depth book metrics.
- **Trade Desk Live WebSocket Streaming (`web/src/TradingWorkspace.tsx`)**:
  - Added real-time live WebSocket quote and bid/ask streaming list alongside the cached REST snapshot.

### Verification
- **Rust Workspace**:
  - All 27 tests in `quant-telemetry` passed.
  - `cargo fmt --all --manifest-path rust/Cargo.toml -- --check` passed cleanly.
- **Python Workspace**:
  - `pytest tests/test_live_simulation.py`: all 15 tests passed.
  - `.venv/bin/python -m pytest -q tests/`: 225 passed, 3 skipped.
- **Node & Web Frontend Workspace**:
  - `node --test web/server/*.test.mjs`: all 20 tests passed.
  - `npm test --prefix web`: all 33 tests passed.
  - `npm run build --prefix web`: passed with 0 errors.



---

## 6. End-to-End Verification, Live Sockets & Build Fixes

### Problem & Fixes
- **`quant-depth` Feature Flag Scope (`rust/crates/quant-depth/src/main.rs`)**:
  - Restored conditional import for `PathBuf` under `#[cfg(feature = "live-readonly")]` to allow compiling `quant-depth` both with standard features and with `--features live-readonly` with zero compiler warnings.
- **Frontend Key Warning (`web/src/App.tsx`, `web/src/App.test.tsx`)**:
  - In `web/src/App.tsx`, updated the instrument `<select>` options to fallback to `key={item.instrument.key ?? item.instrument.security_id}`, preventing `key={undefined}` console warnings during test fixture rendering.
  - In `web/src/App.test.tsx`, provided explicit `key` attributes in mock instrument snapshots.

### Comprehensive Test Suite Results
1. **Rust Workspace (`cargo test --manifest-path rust/Cargo.toml`)**:
   - `quant-telemetry`: 27 passed, 0 failed.
   - `quant-depth`: 10 passed, 0 failed.
   - `quant-engine`: 15 passed, 0 failed.
   - `quant-journal`: 13 passed, 0 failed (1 crash test ignored per design).
   - `quant-bench`: 2 passed, 0 failed.
   - `quant-inference`: 1 passed, 0 failed.
   - `feature_bench`: 1 passed, 0 failed.
   - `paper_eval`: 1 passed, 0 failed.
   - `telemetry_demo`: 2 passed, 0 failed.
   - `telemetry_local`: 9 passed, 0 failed.
   - Formatting: `cargo fmt --manifest-path rust/Cargo.toml --all -- --check` clean.
2. **Python Workspace (`.venv/bin/python -m pytest -q tests/`)**:
   - `225 passed, 3 skipped in 6.56s`.
3. **Node Server Workspace (`node --test web/server/*.test.mjs`)**:
   - `20 passed, 0 failed in 410ms`.
4. **Web Frontend Vitest (`npm test --prefix web`)**:
   - `33 passed, 0 failed in 2.65s`.
5. **Web Production Build (`npm run build --prefix web`)**:
   - `tsc -b && vite build` clean (dist/index.html, dist/assets/index-*.css, dist/assets/index-*.js built with zero errors).

### Live Sockets & Feed Health Audit
- **Telemetry Gateway (`ws://127.0.0.1:8765/ws` & `http://127.0.0.1:8765/api/snapshot`)**:
  - Live session authenticated via signed `alpha_grid_session` cookie and origin check.
  - Subscribed instruments: 25/25 NSE equities.
  - LTP availability: 25/25 instruments.
  - Best bid availability: 25/25 instruments.
  - Best ask availability: 25/25 instruments.
  - 5-level depth book availability: 25/25 instruments.
  - Total frames captured: >224,000 frames (0 malformed, 0 reconnects).
- **Trading Service (`http://127.0.0.1:8767`)**:
  - `/trade/capabilities`: READY (local service READY, gateway READY, telemetry READY, feed LIVE CAPTURE, 25/25 NSE securities, broker execution BLOCKED).
  - `/trade/simulation`: Active portfolio simulation v2 in paused validation pending state, recording verifiable GAP decisions upon feed state transitions.
---

## 7. Automated Browser UI & Cockpit Visual Verification

### Verification Summary
- Launched headless/remote Chrome connected via DevTools Protocol (`ws://127.0.0.1:9222`) with `DevToolsActivePort` configured as a regular file.
- Used the `browser` subagent to navigate to `http://127.0.0.1:5173` and inspect the Alpha Grid Cockpit.
- High-resolution visual screenshots captured for:
  1. `cockpit_focus10.png`: Command Center Top 10 tactical stock arena.
  2. `cockpit_all25.png`: Command Center full 25-stock universe view.
  3. `cockpit_orderbook.png`: Deep Level 2 Order Book with 5-level paired bids/asks, order counts, depletion/replenishment metrics, and raw SHA-256 frame hashes.
- Verified that all widgets, streaming quotes, bid/ask spreads, and cockpit navigation tabs render cleanly with zero UI defects.

---

## 8. Gamified Command Center (Phase 1 React UI + Existing Backend Integration)

### Implementation Overview
Transformed Alpha Grid's default cockpit view into a high-density, real-time strategy-game-style trading Command Center ("F1 telemetry × real-time strategy game × quantitative trading terminal") connected directly to existing backend Rust telemetry and Python portfolio simulation endpoints:
- **Design System (`web/src/styles.css`)**:
  - Deep navy background (`#0B1220`), layered dark panels (`#152238`), electric cyan accents (`#38BDF8`), teal (`#2DD4BF`), amber (`#FBBF24`), coral (`#FB7185`), slate (`#94A3B8`).
  - Crisp typography, responsive high-density layouts, subtle glow borders, smooth transitions, and accessibility compliance (`@media (prefers-reduced-motion: reduce)`).
- **Global Session HUD (`web/src/CommandCenter.tsx`)**:
  - Top persistent status strip showing live Indian Standard Time (IST) and NSE session state (`REGULAR SESSION`, `PRE-MARKET`, `MARKET CLOSED`).
  - Mode indicators: `DATA_ONLY` / `SIMULATION_ONLY` / `NO TRADE`, Dhan connection state, quote subscription tier, feed freshness (>95% fresh indicator), monitored universe count (25 total / fresh count).
  - Virtual portfolio equity, cash balance, open position slots (0/3), session risk budget utilization, and Python worker heartbeat.
- **Market Radar (2D Scatter / Radar)**:
  - 2D SVG scatter plot evaluating relative price movement ([-2.5%, +2.5%]) vs quoted spread ([0 bps, 25 bps]).
  - Proportional volume bubble markers colored by spread tier (tight cyan/teal vs wide amber).
  - Interactive selection synchronizing stock inspection across all panels.
  - Strict provenance: stocks lacking 2D coordinates are grouped in an informative "Awaiting 2D depth projection" footer rather than plotted with invented coordinates.
- **Opportunity Leaderboard**:
  - Displays decision engine evaluation across monitored symbols.
  - Categorizes candidates by genuine status (`WATCHING`, `CANDIDATE`, `REJECTED`, `STALE`, `INSUFFICIENT DATA`).
  - Surfaces recorded evidence, valid latest observations, selection/rejection reasons (e.g. `SIMULATION_PAUSED_VALIDATION_PENDING`), and risk constraint compliance without client-side ranking fabrication.
- **Stock Arena**:
  - High-density tactical cards for the 10 focus stocks (`RELIANCE`, `HDFCBANK`, `ICICIBANK`, `INFY`, `TCS`, `BHARTIARTL`, `SBIN`, `AXISBANK`, `LT`, `ETERNAL`) plus a toggle for all 25 universe stocks.
  - Displays live top-of-book `BID / ASK` quotes, price change %, quoted spread in basis points, book imbalance %, and mini rolling SVG price sparklines computed from genuine observed ticks.
  - Clicking any card opens a tactical **Stock Detail Drawer** with 5-level displayed L2 order book, order counts, microstructure depletion/replenishment metrics, and direct jump shortcuts to Candle Workstation and L2 Order Book.
- **Active Missions**:
  - Displays open virtual positions as animated mission cards with entry prices, quantity, duration, unrealized P&L, and hold/reduce/exit states.
  - When no positions are held, renders a tactical 100% Cash Preservation state explaining active portfolio risk defense without fake trade prompts.
- **Risk Shield (Portfolio Defense)**:
  - Visual portfolio risk gauges: virtual capital deployed (₹15,000 max), session loss limit (₹500 limit), position slots (0/3), max drawdown, and active risk guardrails (`PORTFOLIO V2 ACTIVE`, `MAX 3 POSITIONS`, `NO OVERNIGHT RISK`, `STRICT STOP LOSS`).
- **Decision Timeline**:
  - Chronological real-time event feed combining telemetry audit frames with virtual portfolio ledger decisions.
  - Filter tabs (`ALL`, `DECISIONS`, `FEED`) displaying timestamps, symbol badges, decision types, and machine/audit logs with expandable inspection.

### Verification & Test Suite
- **Web Unit Tests (`web/src/CommandCenter.test.tsx`)**:
  - Created 11 test suites covering Global Session HUD, 10 focus stocks population, 2D Market Radar and selection, Active Missions, Risk Shield, Opportunity Leaderboard, Decision Timeline with filter tabs, price change math, Sparkline SVG rendering, and NSE IST session calculation.
  - Ran `npm test --prefix web`: **44 passed across 7 test files, 0 failed in 9.73s**.
- **Web Production Build**:
  - Ran `npm run build --prefix web`: clean build with zero TypeScript errors.
- **Node Server Tests**:
  - Ran `node --test web/server/*.test.mjs`: **20 passed, 0 failed in 882ms**.
- **Python Test Suite**:
  - Ran `.venv/bin/python -m pytest -q tests/`: **225 passed, 3 skipped in 6.56s**.
- **Rust Workspace**:
  - Ran `cargo test --manifest-path rust/Cargo.toml`: all crates passed (100%).
- **Live Browser Visual Audit**:
  - Inspected on running Vite instance (`http://127.0.0.1:5173`) using automated headless Chrome with visual captures archived in artifact directory.

---

## 9. Final Integration Corrections (NSE Session & Instrument-Aware CAS, Approval Scope Audit, Gateway Activation & Snapshot Integrity)

### Implementation Overview
Addressed final integration corrections prior to Phase 2, maintaining strict data integrity, zero broker orders, and preservation of the existing UI and portfolio engine:
- **Instrument-Aware NSE Session & CAS Classification (`web/src/CommandCenter.tsx`, `web/src/styles.css`)**:
  - Replaced naive 09:15–15:30 single clock range with instrument-aware CAS Phase 1 derivation based on dated NSE MII security evidence (`docs/cross-stock-universe-2026-11-02.json`):
    - `CAS_ELIGIBILITY_MAP`: Identifies the 21 derivative-enabled cash equities (`cas=1`, e.g. RELIANCE, HDFCBANK) vs 4 non-CAS equities (`cas=0`: PCJEWELLER, MONEYVIEW, NETWEB, CUPID).
    - `getInstrumentSessionStatus(casFlag, now)`:
      - 09:15–15:15 IST: Continuous trading for all equities (`CONTINUOUS`).
      - 15:15–15:30 IST: CAS-eligible equities enter `CAS AUCTION` (Phase 1 closing auction 15:15–15:35 IST); non-CAS equities remain in `CONTINUOUS` trading until 15:30 IST. If CAS applicability is unverified, displays `UNKNOWN` rather than misclassifying.
      - 15:30–15:35 IST: CAS equities conclude auction; non-CAS equities transition to `CLOSED`.
      - 09:00–09:15 IST: `PRE-OPEN`.
      - Off-hours and weekends: `CLOSED`.
    - `getNseSessionStatus(now)`: Global HUD displays `NSE CONTINUOUS` (09:15–15:15), `NSE DUAL / CAS` (15:15–15:30), `NSE CAS AUCTION` (15:30–15:35), `NSE PRE-OPEN`, or `NSE CLOSED`.
    - Wired per-instrument session and CAS eligibility status into `StockArena` card headers (`.inst-session-tag.cas`, `.inst-session-tag.unknown`) and `StockDetailDrawer`.
- **Zero Positions Messaging Correction (`web/src/CommandCenter.tsx`)**:
  - Corrected `ActiveMissions` empty-state wording. When zero positions exist due to pending preflight validation or raw observation mode, the UI displays **"Zero Virtual Positions · Decisions Paused"** and explains that zero positions reflects regulatory and integrity constraints, rather than presenting cash preservation as a trading outperformance.
- **Approval Scope Audit & Provenance Verification**:
  - Forensically inspected `$HOME/Library/Application Support/AlphaGrid/offline-dev/feed-scope.json`, `paper-approvals.qp`, and capture validation records.
  - Identified that on-disk configuration (`"authorized_feeds": ["STANDARD_FULL", "DEPTH_20"]`, timestamped `2026-11-04T05:45:04.883Z`) originates from `market-data-access.mjs`, while explicit operator signed QP01 ledger entry in `paper-approvals.qp` remains 0 bytes (absent). No new scopes, socket expansions, or permission alterations were made.
- **Snapshot Integration & Gateway Activation Verification**:
  - Verified that genuine 5-level Full packets (`STANDARD_FULL_RAW`) and 20-level depth snapshots (`DEPTH_20`) maintain separate receive timestamps, source labels, and packet provenance frames (`WireProvenance`), preventing asynchronously received books from being falsely represented as from a single instant.
  - Confirmed the running gateway process (PID 40658 from 11:23 AM) is currently capturing genuine Full packets (>295k frames). To avoid interrupting an active valid capture, the launcher will activate the rebuilt debug binary (built at 13:30 IST, SHA-256 `23e112c8...`) with `--depth-books` integration upon the next clean supervised startup.

### Verification & Test Results
- **Web Frontend Suite (`web/src/CommandCenter.test.tsx`, `App.test.tsx`, etc.)**:
  - **45 passed across 7 test files, 0 failed in 4.58s** (`npm test --prefix web`).
  - Added dedicated unit tests for `getInstrumentSessionStatus` covering CAS-eligible, non-CAS, unknown, and off-hours behavior across all session intervals.
- **Web Production Build**:
  - Clean build in 211ms (`npm run build --prefix web`) with zero TypeScript errors.
- **Node Server Tests**:
  - **20 passed, 0 failed in 319ms** (`node --test web/server/*.test.mjs`).
- **Python Backend Tests**:
  - **225 passed, 3 skipped in 6.13s** (`.venv/bin/python -m pytest -q tests/`).
- **Rust Workspace Tests**:
  - **All 11 crates passed (100%)** (`cargo test --manifest-path rust/Cargo.toml`).
- **Context Merkle Tree**:
  - Synchronized via `python3 tools/repo_context.py` and validated with `--check`.
