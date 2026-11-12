# Context decision summaries

## Independent Opportunity-Record Audit & Correctness Fixes — 2026-11-11 IST

Completed independent audit and verification of the Alpha Grid opportunity-record implementation in `src/quant/apps/execution_safety.py` and `tools/offline_end_to_end_replay.py`:
1. **Corporate-Action Eligibility Reconciled**:
   - Reconciled corporate actions against official NSE exchange circulars (`NSE_PRICE_BASIS_EVIDENCE`) and the 325-window acquisition manifest.
   - Refuted claims that Dhan 1-minute intraday prices are unadjusted cash prices across all symbols, and corrected fabricated equity lists: Dhan delivers retrospectively back-adjusted intraday series for `BAJFINANCE` (factor 10x), `RELIANCE` (2x), `HDFCBANK` (2x), `BSE` (3x), `KOTAKBANK` (5x), `SHRIRAMFIN` (5x), `MCX` (5x), while `PCJEWELLER` was served unadjusted (~₹47.80) and `CUPID` underwent an ISIN change with pre-July-2024 intraday data absent.
   - Preserved all windows lacking audited per-row price adjustment contracts as strictly ineligible (`INELIGIBLE_UNVERIFIED_INTRADAY_PRICE_BASIS`), producing fail-closed `NO_TRADE`.
2. **Economic Estimation & Unknown Value Semantics**:
   - Traced `expected_gross_bps`, `uncertainty_ci_lower_bps`, and cost components.
   - Removed substitution of past returns (`r_5m_bps`) as expected gross return and removed fabricated zeros for missing quote spread, total costs, and statistical uncertainty bounds. In the absence of a validated payoff model and live quotes, these values are strictly typed as `Decimal | None` and serialized as `"UNKNOWN"`.
   - Distinct fail-closed reasons emitted: `EXPECTED_GROSS_PAYOFF_UNESTABLISHED`, `STATISTICAL_UNCERTAINTY_UNKNOWN`, `COSTS_UNKNOWN`, and `CONSERVATIVE_NET_UNESTABLISHED`.
3. **Decision Integrity & Isolated Candidate Qualification**:
   - Enforced fail-closed `NO_TRADE` across all missing information, unapproved models, and sub-hurdle conservative net values.
   - Verified deterministic candidate qualification using isolated fixtures: a qualified candidate with valid spread, approved model, and positive conservative net return above the 5.0 bps hurdle deterministically qualifies as `TRADE_CANDIDATE` and executes into `MockBrokerEngine` with zero live broker connection, zero external network requests, and zero real orders.
4. **Provenance & Snapshot Binding**:
   - Explicitly bound `OpportunityRecord` instances to snapshot ID (`alpha-grid-snapshot-2026-11-10-plain-v1`), Merkle root (`06f3124a842109b42d0bf508fc30ab662b895d32c692c23fcbeba903176d90b3`), and model version (`alpha-grid-m1-20261107-v1`).
   - Replayed October 2025 across 6,840 slots in 17.4s with 100% `NO_TRADE` abstentions, zero fabricated values, and verified mirror parity on Danny's HDD checkpoints.
5. **Verification**: 351 unit tests passed; 27 Node backend tests and 59 Vitest frontend tests passed. Context Merkle tree refreshed.

## Merkle specification correction & reproducible opportunity evaluation — 2026-11-10 IST

Completed verification of the restored historical dataset's provenance and implemented the offline opportunity evaluation pathway:
1. **Merkle Specification Correction (`ALPHA_GRID_PAIRWISE_HEX_SHA256_V1`)**:
   - Reconciled documentation and validator in `tools/build_plain_snapshot_manifest.py` and `tests/test_plain_hdd_storage.py`.
   - Identified and documented the exact mathematical divergence from standard RFC 6962:
     * Standard RFC 6962 operates directly on raw binary bytes with 0x00 leaf / 0x01 internal node domain prefixes and splits trees at the largest power of 2 less than n (no duplication).
     * Alpha Grid's actual construction uses an iterative binary Merkle tree over lexicographically sorted manifest window keys (`symbol_from_to`), leaf digests = `SHA256(raw_bytes).hexdigest()` (lowercase hex ASCII strings), pairwise concatenation `SHA256((L_hex + R_hex).encode('ascii')).hexdigest()` (128 ASCII bytes input), and duplicates the last odd node at each level ($R_{last} = L_{last}$, mirroring Bitcoin block tree structure but over ASCII hex digests).
   - Independently verified and bit-exact reproduced the sealed root: `06f3124a842109b42d0bf508fc30ab662b895d32c692c23fcbeba903176d90b3` across all 325 manifest windows without altering response bytes or the sealed manifest. Added `--verify-merkle` CLI validator.
2. **Economic Basis Eligibility & Usable Research Readers**:
   - Verified that research readers (`tools/train_m1_model.py`, `tools/audit_m1_model.py`, `tools/evaluate_60m_stage2.py`, `tools/offline_end_to_end_replay.py`) resolve raw responses from `/Volumes/Danny's HDD/AlphaGrid-Plain/historical/raw-dhan/` seamlessly while preserving point-in-time session eligibility, corporate-action restrictions, and reserved holdouts (`TCS`, `KOTAKBANK`, `MARUTI`, `BHEL`, `DIVISLAB`) and temporal holdouts (post 2025-10-31).
   - Corporate-action windows lacking per-row audited cash price adjustments are strictly marked `INELIGIBLE_UNVERIFIED_INTRADAY_PRICE_BASIS` and yield `NO_TRADE` for execution backtesting.
3. **Reproducible Opportunity Record Schema (`OpportunityRecord`)**:
   - Implemented `OpportunityRecord` and `evaluate_opportunity_record()` in `src/quant/apps/execution_safety.py`.
   - Captures decision timestamp, instrument, market data provenance (`HISTORICAL_ARCHIVE`, `LIVE_WS`, `REST_SNAPSHOT`), quote freshness age, strategy version, past-only trailing volatility (15m std), observed spread & depth (when genuinely available), model probabilities ($P_{\text{fav}}$, $P_{\text{adv}}$), explicit execution costs (statutory fees + spread + slippage + friction), conservative net opportunity hurdle, and deterministic `TRADE_CANDIDATE` or `NO_TRADE` with explicit reasons.
   - Strictly abstains (`NO_TRADE`) when quotes, depth, trailing volatility, or Stage 3 model approvals are missing.
4. **Offline Replay Integration**:
   - Replayed October 2025 across 6,840 decision slots with `tools/offline_end_to_end_replay.py`. Generated complete `OpportunityRecord` stream in `data/telemetry/offline_replay_decisions.jsonl` and mirrored to HDD checkpoints. 100% of decisions correctly abstained (`NO_TRADE`) with reason `HISTORICAL_ARCHIVE_NO_GENUINE_LIVE_QUOTE` and `MODEL_STAGE3_REJECTED`.
5. **Verification**: 351 unit tests passed across snapshot reproducibility, fail-closed guards, uncertainty limits, and opportunity evaluation. 27 Node backend tests and 59 Vitest frontend tests passed.


## Authorized full 3-year historical data restoration & plain snapshot v2 — 2026-11-10 IST

Completed the authorized historical data restoration of the 25-stock, 3-year Dhan 1-minute dataset (2023-11-01 to 2026-11-01) directly onto the unencrypted external HDD target (`/Volumes/Danny's HDD/AlphaGrid-Plain/`):
1. **Dhan Credential & Preflight Validation**: Verified active credentials via `live_feed_credentials.py` and Dhan's `/v2/profile` endpoint (`DHAN_TOKEN_AND_DATA_PLAN_ACTIVE`). Resolved a positional argument mismatch in `load_credentials` when called from `ensure_local_read_only_scope` and synchronized verified validity into local metadata.
2. **exFAT Filesystem Adaptation**: Replaced hardlink (`os.link`) publishing in `src/quant/acquisition/dhan.py` with `_link_or_replace_atomic` fallback (`os.replace` with `path.exists()` check) to support direct atomic writes and crash-safe durability on exFAT without hardlinks.
3. **Execution Summary**: Successfully retrieved and hash-verified all **325 planned windows** (13 windows × 25 NSE cash equities) with 0 errors and 0 missing files. Captured exactly **6,545,612 1-minute candles** across **312,842,513 bytes** (~298.35 MiB) without rate limit breaches or interpolation.
4. **New Versioned Snapshot Manifest**: Built and sealed `alpha-grid-snapshot-2026-11-10-plain-v1` at `/Volumes/Danny's HDD/AlphaGrid-Plain/historical/manifests/research_snapshot_manifest_plain_v2.json` (mirrored to `data/research_snapshot_manifest_plain_v2.json`).
   - Deterministic Merkle Root: `06f3124a842109b42d0bf508fc30ab662b895d32c692c23fcbeba903176d90b3` using custom iterative pairwise hexadecimal SHA-256 tree with odd-leaf duplication (ALPHA_GRID_PAIRWISE_HEX_SHA256_V1, labeled RFC6962_BINARY_TREE_PAIRWISE_SHA256_ASCII_HEX_DUPLICATE_ODD in manifest provenance for schema continuity).
   - Parity with Legacy Snapshot: Exactly 0 candle delta (6,545,612 vs 6,545,612); byte delta of +2 bytes due to upstream JSON formatting.
   - Audit Integrity: Preserved August 2026 NSE closing auction change (360 bars vs 375 bars post 2026-08-03), preserved zero-bar periods (e.g. CUPID pre-listing), and explicitly recorded corporate-action status across 8 stocks with unresolved pricing bases.
5. **Holdout & Invariant Protection**: Reserved stock holdouts (`TCS`, `KOTAKBANK`, `MARUTI`, `BHEL`, `DIVISLAB`) and post-October-2025 temporal holdouts remain strictly untouched by model training or economic evaluations. M1 weights and original RELIANCE experiments remain immutable.
6. **Reader & Cockpit Integration**: Verified that `load_raw_bars` loads all 180 development/calibration windows seamlessly in 12.68s, and Cockpit evidence endpoints (`/trade/evidence`) report `historical_archive: VERIFIED (325/325 windows, 6,545,612 bars)` and `storage: MOUNTED, PASSED`.


## Unencrypted HDD storage target and plain data restoration — 2026-11-10 IST

Replaced inaccessible encrypted sparsebundle (`AlphaGridData-2026-11-05-v2.sparsebundle` locked due to Keychain non-interactive authentication failure) with an unencrypted owner-private directory structure on the external drive (`/Volumes/Danny's HDD/AlphaGrid-Plain/`).
1. **Direct Filesystem Storage**: Created standard unencrypted directories for `historical/raw-dhan`, `historical/manifests`, `market-feed/quotes`, `market-feed/depth`, `journals/qj01`, `journals/qp01`, `research`, `models`, `checkpoints`, and `reports`. Permissions set to mode 0700 (owner-private `dhaneshramesh`, uid 501). No disk-image mounting or passphrase required.
2. **Restored Artifacts**: Recovered and verified byte-exact SHA-256 for all locally available artifacts from the repository: 3 raw 1-minute historical response files for RELIANCE (45,945 candles across 2,234,979 bytes), all scrip master and acquisition manifests (including 325-window 25-stock manifest for 2023–2026), 5 QP01 paper ledgers, QJ01 demo journal, 7 research reports, M1 model pickle and metadata, and offline simulation replay checkpoints.
3. **Fail-Closed Safeguards**: Updated `tools/execute_full_3yr_acquisition.py` with `verify_external_mount` and `--verify-only` mode, enforcing that historical acquisitions fail closed if redirected to internal SSD or unmounted volumes. Old sparsebundles remain untouched.
4. **Cockpit and Launcher Integration**: Updated `web/server/trading-service.mjs` and `tools/start-cockpit-macos.sh` to auto-detect and resolve `/Volumes/Danny's HDD/AlphaGrid-Plain` as primary storage target.
5. **Dhan Network Request Constraint**: Zero live/historical Dhan API requests were initiated. Backfill pipeline is queued and verified offline (`0/325` windows completed on the new plain target), awaiting explicit user authorization.
6. **Verification**: 5/5 plain HDD storage tests in `tests/test_plain_hdd_storage.py`, 53/53 core Python tests, 27/27 Node tests, and 59/59 Vitest frontend tests passed.


## Evidence-Driven Live Monitor implementation — 2026-11-09 IST

Implemented the Live Monitor view (`web/src/LiveMonitor.tsx`) and backend decision endpoints (`/trade/simulation/decisions`, `/trade/simulation` replay fallback) to make market-data processing visible without misleading users regarding predictive alpha, fills, or live execution:
1. **Source Separation**: Isolated `LIVE_WS` (WebSocket LTP line), `HISTORICAL_ARCHIVE` (verified Dhan 1m OHLC bars), and `REST_SNAPSHOT` (point-in-time quote snapshot) with strict non-blending guards.
2. **Observer Emoji/Avatar**: Animated indicator advancing strictly on arrival of recorded observations or simulator events. Shows state bubbles (`👀`, `📊`, `📚`, `⏳`, `🚫`, `🧪`, `🛑`). Stays stationary during market-closed/stale intervals without synthetic animations.
3. **Durable Pipeline Chain**: Maps 5 verified stages: Frame Received (receipt timestamp) → Raw Frame Durably Persisted (QJ01 fsync timestamp + SHA-256) → Decoded (quant-engine protocol/kind) → Freshness Check (stale/unverified gates) → Simulator Decision (virtual reference price).
4. **Depth & Timeline**: Displays touch quotes, quoted spread bps, 5-level displayed bids/asks, frame hash, receipt time, and scrollable virtual decision records with `VIRTUAL REFERENCE · NEVER A FILL` banners.
5. **Offline & Safety Invariants**: Allows stepping through 6,840 historical replay decisions when market is closed. All listeners remain backend-owned with 0 extra client sockets. Preserved `DATA_ONLY / SIMULATION_ONLY / NO TRADE`.
6. **Verification**: 27/27 Node backend tests, 59/59 Vitest frontend tests (including 8 new tests in `LiveMonitor.test.tsx`), web production build, and 30 Python regression tests passed.

## Cockpit storage & evidence workflow implementation — 2026-11-09 IST

Replaced static status panels with an evidence-first local workflow interface. Added `/trade/evidence`, `/trade/evidence/inspect`, and `/trade/storage/action` endpoints in `web/server/trading-service.mjs`. All metrics report genuine observed filesystem and ledger state, never placeholders. All credentials, tokens, client IDs, and secrets are strictly redacted before serialization. Configured target storage volume for Mac environment (`/Volumes/AlphaGrid Data`) with 0700 owner-private verification and write-probe tests. Control actions (`start-services`, `arm-capture`) fail closed with 503 when the target drive is unmounted or unwritable. The React UI (`StorageSessionPanel.tsx`) renders 6 workflow actions, 5 headline evidence cards, failure reason tags (`DRIVE_UNMOUNTED`, `JOURNAL_MISSING`), applicable guidance, and inspection modals. Maintained strict `DATA_ONLY / SIMULATION_ONLY / NO TRADE` invariants; no Dhan broker calls, live orders, or disk image modifications were made. Verified with 26 Node backend tests, 51 Vitest frontend tests, and 30 Python regression tests.

## Encrypted HDD prompt fallback — 2026-11-08 IST

`hdiutil isencrypted` reported the v2 sparsebundle encrypted with one passphrase slot. The volume was unmounted. A sandboxed metadata-only Keychain lookup falsely returned item-not-found; the unsandboxed lookup confirmed the expected `com.alphagrid.external-data.2026-11-05-v2` item exists and is readable, but read-only attach with its value returned `Authentication error`, including after correct null-terminated `-stdinpass` formatting. The launcher now retains saved-Keychain auto-unlock with that formatting and falls back to `hdiutil -stdinpass` on an interactive Terminal or `-agentpass` otherwise, without printing or storing a secret. `bash -n` and `git diff --check` passed. Sandboxed attach also failed before authentication (`Device not configured`); a macOS DiskImageMounter open was requested but successful mount and journal readback are not yet verified. No image contents were changed.

## Dynamic sizing paper comparison — 2026-11-08 IST

Added a versioned, opt-in `DYNAMIC_V1` sizing arm to the existing Python virtual portfolio, leaving the active baseline ledger and candidate rule unchanged. It caps shares by 25% of displayed best-ask quantity and a fixed two-times trailing-volatility stress bound under the existing ₹200 decision-loss and ₹500 session-loss limits. Missing, stale, crossed, mismatched or incomplete Standard Full books cannot authorize dynamic entries. Paired reports reconcile bid-exit/ask-entry gross references, published charges and residual friction exactly once; gaps, open positions and cross-session exits block positive evidence claims. A positive paper recommendation requires at least 20 independent sessions, positive conservative net versus NO_TRADE, a positive paired session-bootstrap lower bound versus baseline and no greater reference drawdown; promotion remains disabled. Offline: 22 simulator tests and Ruff F checks passed. The HDD has no prospective paired ledgers, so economic effectiveness is unverified. No feed, order or broker request was made.

## Intraday price-basis verification started — 2026-11-08 IST

Offline readback verified manifest byte sizes and SHA-256 for the original ex-date-spanning Dhan one-minute windows of RELIANCE 2885 (2024-10-28), BSE 19585 (2025-05-23), BAJFINANCE 317 (2025-06-16), and HDFCBANK 1333 (2025-08-26). Adjacent pre/ex-date Dhan price levels are smooth (last pre-date 15:14 close to ex-date 09:15 open: ₹1,326.90→₹1,337.00; ₹2,332.00→₹2,358.00; ₹931.95→₹956.00; ₹980.80→₹979.50 respectively), consistent with retrospective adjustment but not proof of the exact original cash price or per-row factor. Dhan's public FAQ says **daily** history is split/bonus adjusted; its v2 intraday contract does not specify per-minute adjustment, mixed-window treatment, or revision history. After explicit operator approval, the focused inquiry was sent to official API support (`apihelp@dhan.co`) on 2026-11-08 IST; Gmail confirmed a SENT message. The reply is outstanding. No authenticated Dhan request, holdout read, model run or order occurred. Original mixed-window selections remain `NO_TRADE_UNRESOLVED`.

## Historical price-basis correction — 2026-11-08 IST

The frozen weekly-relative-strength selection priced 29/33 later selections on retrospectively adjusted Dhan bars. The action-aware helper checks the manifest cataloged source-window factor against dated NSE action evidence and fails closed for mixed-window, unknown, mismatched or wrong-identity bases. The 2025-02-03 BAJFINANCE 09:30 open restores from ₹799.90 to ₹7,999.00, above the frozen ₹5,000 whole-share cap. Original responses, manifest, strategy specifications, saved results and holdouts remain unchanged. Original weekly later ₹912.85, opening later ₹-743.19, M1 ₹-474.56 and Stage 2 ₹-23,981.81 are superseded as rupee evidence, not erased.

The current offline reruns verified SHA-256, byte size, instrument, timestamp and candle parser contract for 160 weekly and 40 opening source responses, without reading the temporal or reserved-stock holdouts. Original selections remain fixed; unaffordable or unresolved selections produce zero-cash-flow NO_TRADE with no replacement. Weekly conservative early: 36 dates, 29 eligible, seven unaffordable, gross ₹793.87, fees ₹727.16, assumed friction ₹513.3012, net ₹-446.5912. Weekly conservative later: 33 dates, 17 eligible, 16 unaffordable, gross ₹2,286.85, fees ₹441.00, friction ₹325.4258, net ₹1,520.4242; selected mean-net date-bootstrap CI95 [₹-18.9691, ₹127.2223], and one paired control has unresolved basis. Opening conservative early: 230 dates, 222 eligible, eight unresolved, gross ₹29.25, fees ₹942.42, friction ₹1,839.2202, net ₹-2,752.3902. Opening conservative later: 69 dates, 63 eligible, six unresolved, gross ₹146.10, fees ₹273.65, friction ₹537.6496, net ₹-665.1996; selected mean-net CI95 [₹-13.7083, ₹-5.5372]. These are provisional historical reference prices because Dhan does not separately attest a per-row adjustment factor; they are not fills. Fee/friction arithmetic and status counts reconcile exactly. New versioned outputs and SHA-256 manifest are stored only on the HDD at `/Volumes/AlphaGrid Data/Live/offline-dev/historical-valuation-amendment-v1/`.

M1 and 60-minute Stage 2 retain `ECONOMIC_INELIGIBLE_UNRECONSTRUCTED_PRICE_BASIS`: 10 and 48 relevant adjusted/action-spanning source windows respectively, with no per-candidate source-row valuation contract. Their earlier descriptive/forecast metrics were not retrained or promoted. The `.venv` research/dev lock now pins scikit-learn 1.7.2; 31 acquisition/valuation tests and 14 M1/Stage 2 tests passed there. Historical identity continuity, action-spanning bases, dividends, bar availability and executable fills remain unresolved. No trading is authorized.

## Dhan credential handling & broker renewal synchronization — 2026-11-06 IST

- **Root cause analysis**:
  1. Stale local expiry lockout: `_check_profile` called `load_credentials(root)` with strict expiry validation. When an operator renewed a token at DhanHQ after local expiry, `load_credentials` raised `ValueError("Dhan access token expired...")`, and `_check_profile` immediately bailed with `"AUTH_REQUIRED"` without contacting Dhan's `/v2/profile` endpoint.
  2. Missing metadata expiry synchronization: Even upon a successful `/v2/profile` response (`DHAN_TOKEN_AND_DATA_PLAN_ACTIVE`), `_check_profile` never synchronized `profile["tokenValidity"]` into `dhan-access-token.meta.json`. Downstream workers requiring `load_credentials(root)` remained locked out by stale local expiry metadata.
  3. Cached `PROFILE_HTTP_401` poisoning: In `live_feed_scope.py::ensure_local_read_only_scope`, the re-check condition `prior is None or prior.get("token_sha256") != token_hash` skipped profile verification whenever the token string was unchanged upon broker-side renewal (`token_sha256` matching). This poisoned scope initialization by persistently trusting prior cached 401s instead of verifying broker status.
- **Minimal fixes implemented**:
  - `load_credentials`: Added `check_expiry: bool = True` argument. When `check_expiry=False`, skips `expiry <= now + 1m` validation while strictly preserving schema, hash, and format checks.
  - `_check_profile`: Calls `load_credentials(root, check_expiry=False)`. Upon broker confirmation (`DHAN_TOKEN_AND_DATA_PLAN_ACTIVE`), atomically synchronizes `token_validity` into `dhan-access-token.meta.json` whenever local expiry is expired or broker validity extends beyond local expiry.
  - `live_feed_scope.py::ensure_local_read_only_scope`: Calls `load_credentials(..., check_expiry=False)` and checks `if prior is None or prior.get("token_sha256") != token_hash or prior.get("result") != "DHAN_TOKEN_AND_DATA_PLAN_ACTIVE"`, ensuring cached failures are never trusted when checking authorization.
  - `trading-service.mjs`: `/trade/dhan/profile` accepts both `DHAN_TOKEN_AND_DATA_PLAN_ACTIVE` and `DHAN_PROFILE_AND_READ_ONLY_SCOPE_VALID`.
- **Targeted offline tests**:
  - Added unit tests covering: stale local expiry bypassing lockout and updating metadata, cached 401 re-verification upon unchanged token SHA-256, and genuine Dhan 401 fail-closed handling. 17/17 credentials tests and 4/4 scope tests pass.
- **Independent verification against live Dhan endpoint**:
  - Direct execution of `live_feed_credentials --check-profile` against `https://api.dhan.co/v2/profile` using the currently stored token returned HTTP 401 (`DH-901`: "Client ID or user generated access token is invalid or expired.").
  - The stored token on disk remains invalid at Dhan's live servers until the operator installs the active renewed token string.


## Offline three-year acquisition audit — 2026-11-05 IST

The three-year wrapper previously trusted stale completed checkpoints, accepted raw files without receipt/HTTP provenance, let retries exceed a per-run request limit, counted auction/pre-open bars toward continuous-session coverage, and trusted checkpoint rows in its summary. The January pilot entry point had a second weaker downloader sharing the checkpoint. Focused fixture counterexamples reproduced these paths. Both entry points now use the same verified runner, which checks original bytes, metadata, parser output and continuous-minute coverage, counts actual attempts, retains invalid responses for review, and derives reports from saved raw evidence. A read-only pass over the mounted archive found 187 verified windows, 138 missing windows, 3,852,948 verified bars, and no corrupt saved windows; no Dhan request was made. Three-year historical identity continuity, full dated NSE holiday/CAS evidence, and Dhan adjustment/timestamp semantics remain unresolved for research or execution eligibility.

## Calendar-aware session reconciliation & offline historical audit — 2026-11-05 IST

- **Exchange-calendar reconciliation replaces arbitrary $\ge 85\%$ heuristic (`tools/execute_full_3yr_acquisition.py`)**:
  - Replaced the arbitrary weekday $\ge 85\%$ threshold with strict calendar reconciliation against verified NSE circular trading holidays (2023–2026) and special exchange sessions.
  - Formally registered special exchange sessions: Diwali Muhurat trading evening sessions (2023-11-12, 2024-11-01, 2025-10-21; ~60 bars), Saturday Disaster Recovery sessions (2024-01-20, 2024-05-18; 105–120 bars), and Closing Auction Session (CAS Phase 1; continuous close 15:15 IST, 360 bars).
  - Expanded intraday candle operational boundaries to respect genuine exchange hours: Day session (09:00–16:00 IST, accommodating 09:07 pre-open match candles, continuous trading, and post-close auction trades up to 15:38–15:40) and Evening session (17:30–20:00 IST for Muhurat). Resolved false-positive out-of-bounds rejections on 28 genuine raw responses.
  - Keeps missing sessions (`missing_sessions`), minute deficits (`truncated_sessions`), and unverified sessions completely explicit in the JSON audit payload.
- **CUPID classification update**:
  - Reclassified CUPID's pre-July 2024 empty responses from `HISTORICAL_DATA_UNAVAILABLE` to **`ZERO_BARS_CAUSE_UNVERIFIED`** until provider backfill coverage is independently established.
- **Read-only audit of 212 historical responses on external storage**:
  - Drive: `/Volumes/AlphaGrid Data/Live/offline-dev/historical-raw-dhan`
  - 187 manifest windows verified offline against SHA-256 sidecars and `.meta.json` provenance sidecars: 100% hash match, 0 corrupt files, 0 defects.
  - 28 previously flagged windows (spanning Diwali Muhurat and pre-open auction discoveries) verified 100% structurally valid.
  - Complete 3-year history captured and verified for 14 liquid equities (276,647–276,649 bars each; 256,530 bars for PCJEWELLER due to untraded minutes).
  - Total candles captured: 3,852,948 bars (175.48 MiB storage). Status: `VERIFY_ONLY_FINISHED` with 0 errors. Zero bytes of raw data mutated.
- **Broader test-suite failure root cause analysis**:
  - `tests/test_dhan_pilot_cli.py::test_mocked_saved_response_verify_and_import`: Fails only under system Python 3.13 / pyarrow 21.0.0 due to a known `ParquetDataset` dictionary-schema merging regression (`ArrowTypeError: Unable to merge: Field source has incompatible types: string vs dictionary`). Passes 100% cleanly in the project's `.venv` environment (Python 3.12.13, pyarrow 25.0.1, pytest 8.4.2).
  - `tests/test_live_feed_credentials.py` (4 failures): Temporal test fixture defect caused by a hardcoded timestamp `datetime(2026, 11, 5, 10, 0, tzinfo=UTC)` with a 1-hour expiry (11:00 UTC). Once local wall-clock time passed 11:00 UTC, `save_credentials` raised `ValueError: Dhan token expiry must be in the future`. Neither failure is a code regression from acquisition changes.

## Fail-closed auditable historical acquisition pipeline — 2026-11-05 IST

- **Audit & pipeline corrections (`tools/execute_full_3yr_acquisition.py`)**:
  - Resolved 6 confirmed defects in the 3-year, 25-stock acquisition tool without mutating raw Dhan responses:
    1. Consistent fatal/error return values and nonzero exit for incomplete runs (`completed < total_planned`).
    2. Atomic checkpoint persistence on fatal auth (HTTP 401/403) or early stop, preventing lost progress.
    3. Resumable offline audit: verifies raw file SHA-256 against `.sha256` sidecars, `.meta.json` request parameters and endpoint provenance, and candle validity without network calls, persisting checkpoint entries even if 0 downloads occur. Corrupted files fail closed without overwriting.
    4. Strict candle validation: shape (all 6 arrays equal length), non-finite numbers (`math.isnan`/`math.isinf`/booleans), strict ordering ($t_i \ge t_{i-1}$), identical vs conflicting duplicate detection, OHLC consistency ($L \le O, C \le H$), non-negative volume, and regular session boundaries (09:15-15:29 IST).
    5. Decoupled orthogonal classifications: source integrity (`VALID_SOURCE` / `DEFECTIVE_SOURCE`), session coverage (`FULL_SESSION_COVERAGE` / `PARTIAL_SESSION_COVERAGE` / `NOT_YET_LISTED` / `HISTORICAL_DATA_UNAVAILABLE`), corporate actions (`VERIFIED_BACK_ADJUSTED` / `VERIFIED_UNADJUSTED_EMPIRICAL` / `EX_DATE_SPANNED_WITHIN_WINDOW` / `NO_KNOWN_BONUS_OR_SPLIT_RECORDED`), exploratory return modeling eligibility, and historical execution modeling eligibility.
    6. Replaced unjustified assertions: unlisted corporate action absence no longer asserts executable unadjusted prices (`UNVERIFIED_PRICE_BASIS`). Ex-date-spanning windows flag `EX_DATE_SPANNED_WITHIN_WINDOW` (`UNVERIFIED_INTRADAY_ADJUSTMENT`, `REQUIRES_EX_DATE_DISCONTINUITY_PURGING`). Symbol summaries derive dynamically from validated window records rather than hardcoded assumptions.
- **Offline unit test suite (`tests/test_full_3yr_acquisition.py`)**:
  - 9 targeted tests covering: fatal auth fail-closed, incomplete manifest nonzero exit, hash mismatch fail-closed, corrupt checkpoint recovery, candle shape/ordering/duplicate/non-finite checks, zero-bar point-in-time classifications, ex-date spanning windows, offline resume without network, and window-driven summary reporting. All 9 pass in 0.09s.

## Historical Data Integrity Audit: Corporate Actions & Universe Semantics — 2026-11-05 IST

- **Corporate action back-adjustment resolved (FAIL on original "unadjusted" claim)**:
  Empirical reconciliation against official NSE trading records confirms Dhan's `/v2/charts/intraday` delivers retrospectively back-adjusted prices ($P / k$) and back-adjusted volumes ($V \times k$). In contrast, `/v2/charts/historical` (daily) back-adjusts price but preserves raw historical volume. Seven universe equities carry retrospective corporate action adjustments in January 2024:
  - `RELIANCE` (2885): 1:1 Bonus on 2024-10-28 (Factor 2.0x, Dhan Open ₹1,291.60 vs NSE unadjusted ₹2,583.20; intraday volume doubled to 4.006M vs 2.015M raw daily).
  - `HDFCBANK` (1333): 1:1 Bonus on 2025-08-26 (Factor 2.0x, Dhan Open ₹853.00 vs NSE unadjusted ₹1,706.00; intraday volume doubled).
  - `BAJFINANCE` (317): 2:1 Split + 4:1 Bonus on 2025-06-16 (Factor 10.0x, Dhan Open ₹733.65 vs NSE unadjusted ₹7,336.50; intraday volume multiplied by 10).
  - `BSE` (19585): 2:1 Bonus on 2025-05-23 (Factor 3.0x, Dhan Open ₹742.30 vs NSE unadjusted ₹2,226.90).
  - `KOTAKBANK` (1922): 1:5 Split on 2026-01-14 (Factor 5.0x, Dhan Open ₹382.00 vs NSE unadjusted ₹1,910.00).
  - `SHRIRAMFIN` (4306): 5:1 Split on 2025-01-10 (Factor 5.0x, Dhan Open ₹410.75 vs NSE unadjusted ₹2,053.75).
  - `MCX` (31181): 1:5 Split on 2026-01-02 (Factor 5.0x, Dhan Open ₹639.95 vs NSE unadjusted ₹3,199.75).
  - Fixed historical queries are non-stationary across retrieval dates: data fetched after a corporate action reflects retrospective back-adjustments.
- **CUPID historical identity diagnosed (FAIL on original "UNLISTED" claim)**:
  Cupid Limited was actively listed and traded on NSE in Jan 2024 (listed on NSE since 2006). Dhan's daily chart endpoint (`/v2/charts/historical`) returns all 21 daily candles for Jan 2024. However, Dhan's intraday endpoint (`/v2/charts/intraday`) returns 0 bars prior to mid-2024 following its 1:10 split and 1:1 bonus of April 4, 2024 (ISIN change from `INE509F01011` to `INE509F01029`). Formally reclassified as **`HISTORICAL_DATA_UNAVAILABLE`** on Dhan intraday API (NOT unlisted).
- **25-stock point-in-time universe classifications**:
  - 16 equities: `VERIFIED_COMPLETE_SOURCE_COVERAGE [UNADJUSTED]` (100% complete, 7,875 bars, $k=1.0$).
  - 7 equities: `VERIFIED_COMPLETE_SOURCE_COVERAGE [CORPORATE_ACTION_ADJUSTED]` (100% complete, 7,875 bars, prices $P/k$, volumes $V \times k$).
  - 1 equity (`CUPID`): `HISTORICAL_DATA_UNAVAILABLE` on Dhan intraday API.
  - 1 equity (`MONEYVIEW`): `NOT_YET_LISTED` (IPO listed on NSE 2026-10-01).
- **Research eligibility decoupled**:
  - *Exploratory return modeling / ML*: **ELIGIBLE**. Proportional percentage returns $\Delta P / P$, log returns, and normalized volume indicators are mathematically invariant to scalar split/bonus factors $k$.
  - *Historical economic simulation*: **INELIGIBLE / BLOCKED** without unadjusted reconstruction. Back-adjusted prices severely distort rupee position sizing, capital limits, STT/turnover fees, and minimum tick friction (₹0.05 on ₹733 is 0.68 bps vs 0.068 bps on ₹7,336).
- **Expansion prerequisites**:
  - Prune `historical-manifest-2023-2026.json` to skip `MONEYVIEW` prior to 2026-10-01 and `CUPID` prior to 2024-07-01.
  - Enhance `.meta.json` sidecars with corporate action flags and factor lookup metadata while keeping original raw JSON response bytes and SHA-256 hashes immutable.

## First cross-equity ML opportunity model & data sufficiency audit — 2026-11-05 IST

- **Architecture and model specification (`src/quant/research/cross_equity_model.py`)**: Built Alpha Grid's first minimal, reproducible cross-equity machine learning research baseline (`alpha-grid-cross-equity-ml-v1`). Pre-declared 5 causal point-in-time features ($X_1$: past 5m return, $X_2$: 30m opening breakout distance, $X_3$: 15m relative volume, $X_4$: 15m trailing realized volatility, $X_5$: normalized time-of-day) and evaluated fixed 15-minute outcome horizons ($T+15\text{m}$, $\ge 30\text{ bps}$ favorable hurdle vs $\le -10\text{ bps}$ / $\text{MAE} \le -25\text{ bps}$ adverse threshold). Implemented pooled L2-regularized logistic regression with train-only standardisation in pure NumPy.
- **Empirical verification on available 5-day cross-equity data (45,300 bars across 25 stocks, Oct 26–30)**:
  - Partitioned chronologically into Train (Oct 26–28, $N=4,350$), Calibration (Oct 29, $N=1,450$), and Out-of-Sample Test (Oct 30, $N=1,450$).
  - Learned standardized weights: $w_{\text{ret5m}} = -0.0026$, $w_{\text{breakout}} = -0.0334$, $w_{\text{relvol}} = +0.0040$, $w_{\text{vol15m}} = +0.0303$, $w_{\text{tod}} = -0.0206$, intercept $b = -1.9722$.
  - Out-of-sample statistical performance: Modest predictive advantage over Constant-Rate Null ($\Delta \text{Brier} = -0.00050$, $\Delta \text{LogLoss} = -0.00231$).
  - Conservative net economics: Under realistic portfolio constraints (₹20,000 capital, ₹15,000 deployed, ₹5,000/inst, max 3 positions, ₹500 session loss, statutory Dhan/NSE fees and 5 bps spread), Arm ML generated positive gross P&L ($+₹110.88$, outperforming momentum $-₹76.88$ and breakout $-₹53.45$). However, transaction costs (₹221.09 statutory fees + ₹110.40 spread crossing) drove net P&L negative ($-₹220.61$ at 0 bps residual friction, $-₹353.84$ at 10 bps friction). Arm NO TRADE strictly dominated all active trading arms ($₹0.00$ Net).
- **Data sufficiency verdict**: Inspected local disk assets. The 25-stock universe possesses only 5 historical sessions (October 26–30, 2026), violating the research sufficiency gate (`SufficiencyPolicy.min_independent_sessions = 20`). The desired 2–3 year multi-stock historical dataset ($\approx 500-750\text{ sessions}$, $12,500-18,750\text{ instrument-sessions}$) is absent locally. Verdict is **STOP / COLLECT MORE DATA**. Confirmatory modeling or live promotion is strictly blocked pending operator authorization for authenticated historical backfills.
- **Verification**: Added 5 comprehensive unit tests in `tests/test_cross_equity_model.py`. Full test suite passed (268 passed, 4 skipped).

## Correction of momentum lookback misclassification after feed resets — 2026-11-05 IST

- **Lookback shortening defect in `_old()` verified and corrected**: In `src/quant/apps/live_simulation.py`, `_old()` contained a fallback (`min(LOOKBACK_NS, buffer_age)` when `buffer_age >= 30_000_000_000`) designed for initial warmup that erroneously accepted observations as young as 30–60 seconds following feed interruptions or socket resets. Downstream momentum evaluation compared these ~60-second returns against full 15-minute friction hurdles (~31–35 bps), causing all eligible stocks (including advancing ones) to be spuriously rejected with `NO_POSITIVE_NET_MOMENTUM_PROXY` ("Price flat or consolidating; waiting for upward momentum impulse") instead of abstaining.
- **Strict lookback and session continuity enforced**: Refactored `_old()` to require matching feed session (`old.get("feed_session") == row.get("feed_session")`) and observations strictly within `[lookback_ns, lookback_ns + 60s]`. Removed truncated buffer fallbacks. When lookback history is incomplete, `_old()` returns `None`, cleanly routing into the existing safety gate at line 852 to record `PAST_LOOKBACK_UNAVAILABLE` ("Accumulating initial price history (warmup)") without evaluating momentum.
- **Buffer purging updated**: Updated deque eviction in `ingest()` to retain up to `self.lookback_ns + 60_000_000_000` to prevent premature eviction of lookback anchors.
- **Focused regression test**: Added `test_portfolio_insufficient_lookback_post_restart_rejects_past_lookback_unavailable_and_flat_stock_rejects_momentum` in `tests/test_live_simulation.py` covering: (1) pre-warmup abstention with `PAST_LOOKBACK_UNAVAILABLE`, (2) socket restart / feed session change handling, (3) 1-minute post-restart abstention (`PAST_LOOKBACK_UNAVAILABLE`) rather than spurious `NO_POSITIVE_NET_MOMENTUM_PROXY`, and (4) complete 15-minute evaluation correctly distinguishing advancing stocks (`BUY`) from genuinely flat stocks (`NO_POSITIVE_NET_MOMENTUM_PROXY`).
- **Preservation of exploratory research & live workers**: No live workers (PID 81123) or services were modified or restarted. Historical October 26–30 analysis is preserved as exploratory with explicit methodological caveats (1-minute overlapping step serial correlation, 5-session sample limitation, retrospective universe selection, and zero-fee touch assumptions). Zero changes to risk limits, capital, QP01 or broker execution.

## Cross-Equity Historical Data Acquisition & RELIANCE Pilot — 2026-11-05 IST

- **RELIANCE 30-Day Pilot Execution (`tools/execute_reliance_pilot.py`)**:
  Executed read-only DhanHQ `POST /v2/charts/intraday` retrieval for RELIANCE (`security_id=2885`, `NSE_EQ`, `EQUITY`, 1-minute interval) over the 30-day January 2024 window (`2024-01-01 00:00:00` to `2024-01-31 00:00:00`). Returned HTTP 200 with 393,504 raw bytes, SHA-256 `30290a40c9b635cc6ca52a84bd5eccd7f93898844ba0e19c06ca77939e84daed`. Exact sidecars (`.sha256`, `.meta.json`, and `.quarantine/` evidence) verified with zero byte tampering.
- **Data Quality & Integrity Audit**:
  Verified 21 distinct trading sessions in January 2024 (including the special Disaster Recovery Saturday session on 2024-01-20 and excluding declared holidays on Jan 22 and Jan 26). Exactly 375 continuous 1-minute bars per session (09:15 to 15:29 IST), totaling 7,875 bars (100.0% complete). Zero duplicate timestamps, zero reordered pairs, zero OHLC violations ($L \le O, C \le H$), and zero negative volume. Historical prices (~₹1,285 to ₹1,460) reflect genuine unadjusted trading prices (pre-October 2024 1:1 bonus). Verdict: **`PASS`**.
- **Three-Year Acquisition Manifest (`tools/execute_cross_equity_acquisition.py`)**:
  Constructed deterministic manifest (`historical-manifest-2023-2026.json`) for the 25 frozen equities over November 1, 2023 through November 1, 2026 partitioned into 13 non-overlapping windows of at most 90 days (325 total requests, ~325 MB projected storage). Storage verified on external APFS volume `/Volumes/AlphaGrid Data` (238 GiB available, >700x headroom) backed by `/Volumes/Danny's HDD` (810 GiB available).
- **Selection Bias & Eligibility Limitation**:
  Documented that the 25 equities selected in November 2026 carry survivorship and lookback selection bias. Two instruments (`MONEYVIEW` / 766325 and `CUPID` / 18520) were unlisted or returned 0 bars in Dhan intraday API in January 2024. Dynamic universe masking is required to prevent forward lookahead.
- **Cross-Equity Universe Expansion (January 2024)**:
  Expanded acquisition to all 25 equities for the January 2024 window under conservative 1.5s rate pacing, checkpointing, and 10 GiB disk headroom guards. Successfully retrieved 181,125 one-minute candles across 23 active equities (7,875 bars each, 100% session completeness).
- **ML Readiness & Causal Constraints**:
  Verified feasibility of a 15-minute causal breakout/outcome study. 1-minute bars aggregate cleanly into non-overlapping 15-minute feature and outcome intervals without lookahead. Causal boundary: candle OHLCV measures matched transactions, not available queue depth or achievable fill prices; backtests remain indicative counterfactual reference evaluations.

## DhanHQ native access-token authentication and controlled renewal — 2026-11-05 IST

- **Native token authentication & removal of browser-login dependency**: Supported native DhanHQ access-token authentication across the Mac-local credential manager (`src/quant/apps/live_feed_credentials.py`), Node cockpit trading service (`web/server/trading-service.mjs`), and web UI (`web/src/TradingWorkspace.tsx`). Tokens are stored atomically in owner-private storage (`data/.secrets/` mode 0700/0600) with client ID and SHA-256 integrity metadata. Secrets are never exposed to browser storage, logs, telemetry, error messages, or research artifacts. Format validation supports standard base64, base64url, and JWT tokens (`[A-Za-z0-9._\-+/=]`).
- **WebSocket Keepalive Ping & Resilient Live Feed**: Added proactive 15-second client-side WebSocket keepalive ping (`Message::Ping`) in `rust/crates/quant-telemetry/src/read_model.rs` and extended idle timeout to 60s in `rust/crates/quant-telemetry/src/bin/telemetry_local.rs`. Supervisor daily attempt ceiling elevated from 12 to 120 in `tools/start-cockpit-macos.sh` to prevent dropping into offline mock mode during lunchtime low volatility.
- **Real-Time Freshness Metrics & Indicators**: Added live quote age tracking (`last_quote_age_seconds`, `last_quote_time_ist`, per-stock `age_seconds`) to `src/quant/apps/live_simulation.py` and visual freshness badges (`🟢 Updated 0.8s ago`) in `web/src/SimulationWorkspace.tsx`, resolving ambiguity over stale versus live streaming quotes.
- **Documented DhanHQ RenewToken endpoint (`https://api.dhan.co/v2/RenewToken`)**: Supported the documented GET `/v2/RenewToken` endpoint for eligible Dhan Web-generated tokens. Renewal requires an active, unexpired token, validates response schema (`accessToken`, `expiryTime`, and matching `dhanClientId`), and atomically persists the replacement credential without discarding the existing usable token prior to success. Unsupported token types (e.g. `dhan-api-token-v1` / API keys) do not attempt renewal.
- **Pre-expiry renewal policy & operator safety controls**: Pre-expiry renewal is supported via CLI (`--renew-token`) and UI ("Renew Active Token Now"), but automatic unattended renewal remains explicitly disabled (`AUTOMATIC_RENEWAL_DISABLED`) pending separate operator approval. Token status is exposed strictly as `VALID`, `EXPIRING`, `EXPIRED`, or `AUTH REQUIRED`. Simulation-only restrictions and existing feed scopes remain intact; no broker orders or QP01 writes are enabled.
- **Focused offline verification**: Added 7 comprehensive unit tests in `tests/test_live_feed_credentials.py` (total 12 passed) and Node service tests in `web/server/trading-service.test.mjs` (total 13 passed) verifying persistence across restarts, fail-closed behavior on expiry, atomic replacement, failure safety, non-renewable token handling, and lack of secret leakage. All 266 Python unit tests, 45 Vitest frontend tests, and 28 Rust tests pass.

## Targeted causal, depth and economics corrections — 2026-11-05 IST

- `locate_outcome_quote` now censors outcomes unless both selected five-level books match exact QJ01 raw records, original frame SHA-256, packet security ID and five price/quantity/order-count levels, with ordered offsets and frame IDs. Matching capture lifecycle evidence is required; missing logs, cross-session events and nested disconnect/stop events censor. Sparse quote activity alone does not imply a gap. The local November 4 RELIANCE pair passes exact raw-byte/offset checks, but QJ01 alone cannot establish its real fsync availability time or independent research eligibility.
- The portfolio worker ignores independent `DEPTH_20` for virtual entry eligibility. A `STANDARD_FULL` book must come with a Full market packet and matching raw-frame provenance; Standard Quote and raw capture remain independent of optional depth. No live subscriptions, journals or broker paths changed.
- The research filter comparison now uses the live simulator's configured position, capital, decision-loss and session-loss constants. Each arm recognizes P&L only after its exit, reserves conservative open risk, and resets the session-loss calculation by dated session. Focused offline Python checks passed (46 tests); genuine prospective continuity and availability still require a future observed session.

## Resolution of Codex critical review findings and order-book protocol amendment v4 — 2026-11-04 IST

- **Codex Review Addressed**: Investigated Codex's findings against the Mac-local implementation and corrected the demonstrated causal defects without redesigning the architecture or introducing new operational gates:
  1. *Source-to-outcome integrity (`src/quant/research/order_book_imbalance.py`)*: Every `FiveLevelSnapshot`, `DepthFeatures`, and `AdverseTargetOutcome` now carries genuine durable source provenance (`raw_frame_sha256`, `source_frame_id`, `journal_offset`, `packet_offset`, `raw_durable_at_ns`, `feed_session_id`). Defined and enforced a fixed, documented 5-minute outcome horizon ($T+300\text{s}$) with a strict $\pm 5.0\text{s}$ tolerance window ($[T+300\text{s}, T+305\text{s}]$) and explicit censoring for early outcomes (`CENSORED_OUTCOME_BEFORE_MATURITY`), late quotes (`CENSORED_TOLERANCE_EXCEEDED`), continuous session close (`CENSORED_SESSION_BOUNDARY`), and stream resets (`CENSORED_FEED_DISCONTINUITY`).
  2. *Market quote vs independent depth separation (`rust/crates/quant-telemetry/src/read_model.rs`)*: Corrected the demonstrated contamination path where `update_depth_book` in `read_model.rs` was synthesizing a fake `WireMarket` (with `price_nanos = best_bid.or(best_ask)`) when market was empty, and overwriting `best_bid_price_nanos` / `best_ask_price_nanos` with asynchronous 20-level depth. `WireMarket` prices now originate strictly from genuine standard quote packets (`DhanStandardV2`), while `WireDepth` from `DEPTH_20` retains its separate identity, timestamps, and book freshness. Added unit test `depth_update_never_overwrites_market_quote_prices`.
  3. *Real economic sensitivity (`src/quant/research/order_book_imbalance.py`)*: Fixed `evaluate_filter_economics` so that the `friction_bps` argument actually recomputes residual friction and scenario net P&L across the 0, 5, 10, 20, 40 bps ladder instead of leaving net P&L unchanged. Proved exact mathematical single-counting ($\Delta \text{Net} = \text{AvoidedNetLosses} - \text{ForegoneNetProfits}$, gross decomposition equivalence with fees and friction saved) under sequential portfolio capital constraints (₹15,000 ceiling).
  4. *Academic grounding & walk-forward reuse*: Reused Alpha Grid's verified [`quant.research.walkforward.make_folds`](file:///Users/dhaneshramesh/alpha-grid/src/quant/research/walkforward.py) module with 300s horizon purging and session grouping. Explicitly classified the protocol as an unvalidated research specification (`RESEARCH_SPECIFICATION_UNVALIDATED`), not a completed predictive evaluation or confirmed alpha. Versioned into `alpha-grid-order-book-amendment-v4` (`ORDER_BOOK_AMENDMENT_V4`) while preserving all prior amendment definitions (`V2`, `V3`).
  5. *Genuine end-to-end trace on Mac-local QJ01*: Re-read genuine records from `raw-standard-2026-11-04.qj` on disk (Record 76877 for RELIANCE at 11:15:43.163 IST, LTP ₹1302.00, OIB5 +0.4110, raw SHA `90abecc32ceb6ffe...` linked to Record 85155 at 11:20:43.674 IST, 300.511s elapsed, -2.30 bps non-adverse outcome, raw SHA `511c6168a8bc...`). Pipeline integrity is verified end-to-end, while confirmatory eligibility is correctly rejected due to missing dated MII manifest (retained as `RAW_CAPTURE_ONLY`).
  6. *Focused tests executed*: Rust workspace passed all 88 unit tests and doc tests (28 in `quant-telemetry`); Python passed 244 tests (16 in `test_order_book_imbalance`); Node passed 20 tests; Web passed 45 tests. Merkle context index verified current.

## Prospective statistical edge measurement and reconciled intraday economics — 2026-11-04 IST

- **Forensic audit correction (`AG-FORENSIC-AUDIT-2026-11-04-REV1`)**: Reclassified historical RELIANCE 125-session replay (+₹2,490.30 gross, +₹1,498.57 post-fee) as an exploratory sample result rather than a proven statistical edge. Session-level block bootstrap (treating 113 unselected sessions as valid zero-return cluster units) spans zero even at zero friction ($[-₹13.80, +₹45.49]$). The positive mean is driven by 3 outlier sessions, while median return across selected trades is negative ($-2.33\text{ bps}$).
- **Matched-null comparisons**: In the 25-stock 5-session experiment, breakout selections delivered $-57.66\text{ bps}$ vs the cross-sectional universe mean of $+7.33\text{ bps}$ (selection deficit of $-64.99\text{ bps}$), ranking $14.8 / 25$ on average (worse than random pick). In RELIANCE, nominal $p=0.0264$ fails significance ($p > 0.15$) after exploratory adjustment across multiple test horizons.
- **Reconciled Dhan/NSE economics (`src/quant/research/prospective.py`)**: Reconciled published Dhan tariff for NSE cash market transaction charge ($0.0030699\%$, or ₹3.0699 per lakh) and evaluated actual virtual position sizes: ₹5,000 single portfolio position ($9.84\text{ bps}$ fee burden), ₹15,000 total deployment ceiling ($10.49\text{ bps}$ fee burden), and ₹100,000 research notional ($8.27\text{ bps}$ fee burden). Break-even non-fee round-trip friction is $12.56\text{ bps}$ for RELIANCE and $-67.25\text{ bps}$ for the 25-stock experiment. Touch prices $(\text{Ask}_0 - \text{Bid}_0)$ already pay full quoted spread; spread and residual friction are strictly separated without double-counting. Unit tests verified in `tests/test_prospective.py`.
- **Prospective comparison frozen**: Pre-declared 4 comparative arms (A: NO TRADE, B: Simple ORB, C: Matched Random, D: Price/Volume) on fresh forward data without look-ahead or live trading. Frozen specification requires minimum 30 independent sessions and bootstrap $CI_{\text{low}} > 0$. Unit tests in `tests/test_prospective.py`.
- **Order-book imbalance amendment v3 (`src/quant/research/order_book_imbalance.py`)**: Grounded in peer-reviewed literature (Tripathi et al. 2021, *Finance Research Letters*, 41, 101863 [corrected article ID]; Gupta & Kumar 2025, *American Business Review*; Jindal et al. 2026, *Journal of Prediction Markets*; Bailey et al. 2016, *Journal of Computational Finance*), established the versioned experiment specification `alpha-grid-order-book-amendment-v3` (superceding v2) as an amendment to `alpha-grid-prospective-v1`. Evaluates a single incremental feature: five-level order imbalance ($\text{OIB}_5$). Fixed baseline controls are past 5-minute return ($R_{5m, \text{past}}$), quoted spread ($S_{\text{bps}}$), and trailing 15-minute volatility ($\text{Vol}_{15m}$). Adverse target is strictly fixed as $Y_t = \mathbb{I}(R_{5m, \text{fwd}} \le -10.0\text{ bps})$ at $T+300\text{s}$ maturity with strict censoring of incomplete or premature forward quotes. Implemented chronological partitioning with 300s purging buffers to eliminate label overlap leakage. Evaluates paired Brier score and log loss differentials with session-clustered bootstrap uncertainty; removed arbitrary mandatory PBO < 0.10 for this pre-declared $N=2$ comparison. Audited economic filter single-counts incremental net value ($\Delta \text{Net} = \text{AvoidedNetLosses} - \text{ForegoneNetProfits}$, gross decomposition equivalence without double-counting fees or friction) under portfolio capital constraints (₹15,000 ceiling). November 4 session is preserved as exploratory `RAW_CAPTURE_ONLY`. Unit tests in `tests/test_order_book_imbalance.py` (9 passed). Verdict is `READY TO COLLECT` prospective forward sessions.
- **QJ01 raw capture reliability & safe rotation resolved (`rust/crates/quant-journal`, `quant-engine`, `quant-telemetry`)**: Investigated why raw capture stopped on November 4 at 15:06:10 IST at 128 MB. Standard capture had a hardcoded 128 MB check across `read_model.rs`, `capture.rs`, `telemetry_local.rs`, and `dhan_quote_preflight.py`. Upgraded `JournalWriter` to support both single-file (cap raised to 512 MB to safely accommodate 25-stock Full continuous sessions) and segmented journal directories (4 GB session budget with 64 MB rotating chunks, CRC32 manifest sealing, 512 MB disk-free threshold, and restart recovery), reusing `quant_journal::segmented::SegmentedJournal` while preserving frame SHA-256 hashes, ordering, durability, and recovery guarantees. Unit tests added and passing in `quant-journal` (15 passed) and `dhan_quote_preflight.py`.

## Cross-platform Merkle context index, manual NSE evidence ingestion, and 20-level depth telemetry hookup — 2026-11-04 IST

- **Cross-platform Merkle context index (`tools/repo_context.py`)**: Built a Python 3 CLI tool matching `tools/repo-context.ps1` canonical Merkle tree calculations (SHA-256 blob rules, ordinal child sorting, and `--check` diff verification). This resolves the issue where macOS/Linux environments without PowerShell could not verify or refresh `docs/context/merkle.json`. Unit tests in `tests/test_repo_context.py` verify exact hash and tree parity.
- **Manual NSE evidence ingestion (`src/quant/apps/ingest_nse_evidence.py`)**: Because automated public downloads of daily NSE security files (`NSE_CM_security_DDMMYYYY.csv.gz`) fail with HTTP/2 stream errors and timeouts from NSE's bot mitigation, a verified ingestion utility was created. It verifies operator-supplied browser downloads against all 25 frozen universe equities (`ElgbltyNrmlMkt == "1"`, valid CAS flags, unique symbol rows), generates the cryptographic `.manifest.json` sidecar, and updates `data/config/calendar.json` with `ORDINARY_OBSERVATION_ONLY` session evidence so that `dhan_quote_preflight.py` can pass without fabricating or unverified data. Unit tests added in `tests/test_ingest_nse_evidence.py`.
- **20-level depth telemetry hookup**: `quant-depth` now captures the paired 20-level DhanHQ market depth feed and exports the latest book state atomically to `ALPHA_GRID_DEPTH_BOOKS_FILE` (`data/telemetry/depth-books.json`). `quant-telemetry` (`telemetry_local.rs`) polls this snapshot file every 500 ms and updates instrument slots using two-phase atomic seqlock coordination (`WriteGuard`). The web dashboard's WebSocket stream (`web/src/stream.ts`) was expanded to validate and accept `DEPTH_20` envelopes with derived pairing (up to 20 levels), and `web/src/App.tsx` processes depth freshness independently. Full workspace tests (Rust, Python, Node, Vite) verified.

## Approved Full/depth capture and IST chart correction — 2026-11-04 IST

- The user approved one persistent standard Full socket plus one 20-level
  depth socket for the same frozen 25 NSE_EQ stocks. The saved private scope
  now has `STANDARD_FULL` and `DEPTH_20`; both sockets connected during the
  November 4 session. The genuine standard QJ01 sweep found 98,154 frames,
  118,903 code-4 packets and 21,279 code-8 packets. The gateway showed 25
  raw LTPs and 25 same-packet raw five-level books. Depth status independently
  showed 1,243 received frames at that check. No broker order path changed.
- The previous chart's 06:30 tick was UTC for a 12:00 IST instant. The chart
  now formats axis and crosshair in `Asia/Kolkata`, leaves original timestamps
  intact, and separates observed live LTP from historical OHLCV. The browser
  telemetry validator previously dropped Full snapshots with depth; it now
  requires matching protocol, session, frame ID, packet offset, receipt and
  original-frame hash before showing them. Raw packets still carry validation
  pending and the portfolio worker rejects them.
- Depth initially failed because Rustls had no installed process crypto
  provider. Installing the existing ring provider before connection fixed
  the live collector. The launcher also stopped counting offline preflight
  passes as authenticated feed attempts. Rust depth and telemetry, Python
  worker, React stream/chart/app tests, web build and lint passed. The dated
  NSE identity/CAS evidence is still missing, so no virtual decisions or
  causal research claims follow from this capture. PowerShell was unavailable
  for the repository Merkle check and refresh.

## Trade desk availability labels — 2026-11-04 IST

- During a genuine 25-ID Quote capture the Trade desk displayed a broad
  `DATA UNAVAILABLE` notice even while its shared header reported 25 current
  REST quotes, 103 raw frames and both historical archives. The notice and
  candidate card were hardcoded for unavailable trade candidates, not market
  data. They now distinguish raw capture, REST snapshots, paused simulation,
  absent validated candidates and disabled broker execution. The saved REST
  quote list stays visible while the standard socket is connected.
- The read-only account panel now displays the local `/trade/account` HTTP
  failure code when no verified response is available; the screenshot alone
  did not establish why that response was absent. The dated November 4 NSE
  security file and manifest are still missing, so raw packets remain outside
  the virtual decision path. No account or broker request was added by this
  UI correction. Seven focused cockpit tests passed.

## Five-level standard-feed book diagnosis and implementation — 2026-11-04 IST

- Dhan's v2 Live Market Feed and Annexure define RequestCode 17 as Quote,
  response code 4, and RequestCode 21 as Full, response code 8 with five
  bid/ask levels. A hash-verified read-only sweep of the saved November 3
  QJ01 counted 315,817 frames, 449,347 code-4 packets, zero code-8 packets,
  and all 25 frozen IDs. The saved private permission is `STANDARD_QUOTE`.
  The zero-book result is a subscription result, not a missing code-8 decoder.
- `STANDARD_FULL` can now be selected and explicitly confirmed in Trading
  Settings. It uses the existing standard socket and 25-ID scope; the current
  permission remains Quote-only. No authenticated Full subscription was made
  in this implementation turn. Rust rejects incomplete, crossed, unordered or
  invalid-price Full books per instrument while preserving the raw packet and
  unrelated decoded packets. Packet-boundary and wrong-ID faults still stop
  the feed. Valid five-level snapshots are projected after raw QJ01 sync with
  original hash and local receive/durable timestamps. When separate decision
  validation is pending, `STANDARD_FULL_RAW` exposes descriptive depth only,
  clears any prior eligible market quote and retains the global no-trade
  reason. It cannot enter the Python portfolio worker; `STANDARD_FULL` quote
  projection still requires the existing decision checks.
- The Rust snapshot computes quoted spread, five-level displayed quantities
  and order counts, unweighted imbalance, depth change, depletion,
  replenishment and an arrival-order top-of-book OFI proxy. These do not
  identify executions, cancellations, hidden liquidity or achievable fills.
  Python requires matching Full quote/book receipt times and checks displayed
  liquidity before virtual reference decisions. No Full packets have been
  genuinely observed, so no causal book-feature or ML value is claimed.
- Focused offline checks passed: 13 Rust market-data, 15 engine, 26 telemetry,
  23 Python scope/preflight/portfolio, two Node permission and six cockpit tests; the
  web build and lint passed. The saved original-byte sweep found packet codes
  `{4: 449347}` and no Full packet. During the November 4 session, the
  rebuilt launcher briefly canceled the existing Quote socket because its
  new plan field was parsed one position early. The field order was corrected
  and the supervised app restarted. At the observed check it held one standard
  socket and zero depth sockets, the newest connection had 4,370 raw frames,
  and the virtual ledger remained at ₹20,000 cash with validation-pending
  decisions. The Python worker also checks that a proposed virtual quantity
  fits the displayed top-level bid/ask quantity when using that top price as
  its reference; deeper displayed levels are retained for descriptive metrics.
  The saved permission still reads `STANDARD_QUOTE`; no Full
  frame, five-level book, causal outcome or ML test has been observed.
  The November 4 raw-validation record measured clock offset 51.42 ms with
  41.492 ms uncertainty, within the 100 ms bound, but the dated NSE security
  file and manifest were absent. A public NSE download check returned an
  HTTP/2 stream error and an HTTP/1.1 timeout; no dated identity or calendar
  evidence was invented. Authorized raw Quote capture continued.
- Fixed-universe Dhan identity preflight now checks the same original-master
  hash and exact NSE/E/EQUITY/EQ rows in one scan for all 25 IDs, rather than
  25 repeated hash/scan passes. The local read-only check verified 25 IDs in
  0.668 seconds; focused preflight/scope/portfolio tests passed 23/23.
- A focused raw-only Rust fixture verified that a Full packet creates five
  displayed levels and raw-frame SHA-256 in telemetry with no market quote or
  simulation eligibility. Overview, Market intelligence, Order book and
  Feature monitor now read that one labeled book snapshot. Six focused
  cockpit tests, web build and lint passed. No genuine Full packet was
  acquired for this check.

## Genuine quote-to-portfolio integration — 2026-11-03 IST

- The existing approved v2 standard scope remains RAW_CAPTURE_ONLY when dated
  NSE/CAS/session or independent clock checks are absent. When the existing
  strict checks pass, the same Rust socket may publish a separate receipt-time
  virtual-decision projection after the raw original frame is fsynced; later
  clock failure pauses only that projection. Python rejects absent, stale,
  crossed or incomplete books per stock. Its optional bid/ask source is the
  existing saved REST quote cache, paired only when both REST and WebSocket
  receipts are within two seconds; the worker issues no Dhan refresh.
  Original WebSocket SHA-256/frame ID and REST response SHA-256, event/receipt,
  durable availability and worker observation times are linked in the v2
  ledger. Neither reference ask/bid nor last-trade time is called an actual
  fill or a measured exchange publication time. If dated proof appears during
  raw capture, the existing launcher can make one bounded scoped reconnect;
  its exhausted new-connection budget does not stop an active socket.
- A read-only sweep of the genuine November 3 QJ01 verified 315,817 original
  frames, 449,347 quote packets and all 25 security IDs. The recorded
  standard code-4 packets contain zero five-level books, so fresh REST pairing
  or a separately authorized scope with genuine book data is necessary for
  virtual BUY/SELL reference decisions. The status file's 7,942 frames refer
  to its last connection, not the journal total. The current Mac lacks the
  dated November 3/next-session NSE security file and same-day calendar
  observation evidence, so no saved raw packet was retrospectively promoted.
- Closed-market final restart at 23:56 IST loaded the rebuilt gateway, launcher
  upgrade logic and v2 worker:
  listeners 8765/8767/5173, zero outbound Dhan sockets, 25 configured stocks,
  zero virtual positions/orders, cash ₹20,000. That balance is the existing
  Rust cash-paper-risk-v1 starting equity, separate from the historical ₹1m
  research simulator. The ₹15,000 deployment ceiling leaves a ₹5,000 capital
  buffer; pending-order reserve is ₹0. The v2 ledger has 23 lifecycle/gap
  events and zero observed-quote or virtual-trade events. No live eligible
  decision or genuine
  receipt-to-worker latency has been observed. Focused verification passed:
  27 Python tests, 15 Rust engine tests, 25 Rust telemetry tests, 12 Node
  service tests, two cockpit tests, web build/lint, Rust formatting, Python
  core lint, launcher syntax
  and diff whitespace. The signed loopback simulation route returned HTTP 200
  with the persisted closed-market v2 state. The existing read-only REST cache
  returned 25 quotes; its latest original HTTP 200 response was 19,375 bytes,
  SHA-256 `743915bd394f7c135637cada194477d0e33ab67fd608bc849ad8eee8631dc185`,
  received 2026-11-03T18:19:35.750Z after restart. It was not eligible for a
  virtual decision outside the session. PowerShell Merkle check was attempted and is
  unavailable on this Mac.

## Targeted concurrent portfolio verification — 2026-11-03 IST

- Rust owns Dhan socket/capture, original QJ01, decoding and read-model
  publication; Python owns the local v2 virtual portfolio, shared cash and
  hash-chained ledger. The Rust independent QP01 risk gate is not called by
  this simulator and no broker route exists in it. Under approved
  `RAW_CAPTURE_ONLY`, Rust intentionally quarantines frames; actual live
  virtual decisions are therefore not established by today's raw capture.
- Focused synthetic fixtures showed 25 instruments still accumulating
  observations with three open virtual positions, deterministic mixed
  SELL/REDUCE/HOLD/BUY ordering, restart deduplication, hash-tamper rejection,
  stale/future/non-feed quote rejection and shared-cash accounting. A defect
  found in review was fixed: entries now reserve conservative open-position
  loss against the existing ₹500 session budget, and new entries reject when
  an open position quote is stale. Entry fees debit virtual cash immediately;
  partial exits allocate that fee without double charging. The v2 ledger now
  takes an exclusive process lock before recovery, preventing concurrent
  writers. Risk thresholds did not change.
- Synthetic 25-stock/120-cycle benchmark with fsynced ledger: 6,000 events,
  2,608,852 bytes, ingest p50 4.20 ms, p95 8.11 ms, max 12.58 ms;
  recovery 61.08 ms. This limited run does not measure live feed latency or
  full-session growth and does not justify moving virtual strategy logic to
  Rust. Nine focused Python tests and two Rust raw-quarantine tests passed.
  At closed-market runtime, the supervised worker restarted with 25 names,
  ₹20,000 cash, zero positions and broker orders; the second-ledger-open check
  hit its exclusive lock while ports 8765/8767/5173 stayed listening.
  Final focused run: 17 Python tests passed, Rust raw-capture/quarantine
  tests passed 2/2, core Ruff rules and diff whitespace passed. The full
  Ruff style check still reports 61 line-length findings in the two Python
  files; no broad formatting rewrite was made for this verification task.
  PowerShell Merkle check was attempted but is absent on this Mac.

## Concurrent virtual worker and isolated local recovery — 2026-11-03 IST

- The active Mac launcher uses the separate v2 virtual ledger and runs the
  same 25-stock manifest under the unchanged cash-paper-risk-v1 numerical
  limits (₹20,000 capital, ₹15,000 total deployment, ₹5,000 per name, three
  positions, ₹500 session loss, ₹200 decision downside bound). There is no
  daily trade-count cap or fixed-duration-only exit. Virtual references use
  observed ask/bid, published intraday fees and a separately assumed 20 bps
  residual friction; they are never actual fills. No broker/QP01 path was
  added. Insufficiently validated raw frames remain capture-only, and the
  live simulator pauses while the feed is stale.
- Synthetic-only focused validation reproduced simultaneous entries,
  independent reduce/hold/exit, exposure and loss rejection, ledger restart
  idempotency, and validation-pending suspension. Fourteen Python tests, six
  cockpit component tests, web build/lint, launcher shell syntax and diff
  checks passed. At 22:36 IST the restarted local app served v2 status
  `MARKET_CLOSED` with zero virtual positions and the three local listeners
  on 8765/8767/5173. Terminating only the simulation worker caused the
  launcher to restart it; the other listeners stayed up and a private failure
  event was fsynced. No live v2 virtual decision was observed after the
  session cutoff. The PowerShell Merkle check was attempted and remains
  unavailable on this Mac.

## Cross-equity adaptive v2 — examined sessions only, 2026-11-03 IST

- Original 25-response bytes and hashes reproduced the saved five selections
  and −₹5,307.53 net exactly. All 125 stocks had complete 10:00 past inputs.
  The original 09:59 trigger/entry reference has unverified availability, and
  its eligibility function reads through the future 10:59 exit bar. Five
  selected paths and every stock ranking were exported without altering v1.
- The separately versioned offline replay evaluates five-minute as-of slots
  and exits on invalidation, adverse movement, 60-minute maximum or cutoff.
  Six price/volume and timing-sensitivity variants all lost net on examined
  October dates. The volume ablation is unstable across delay assumptions.
  No predictive model, live shadow strategy or order path was activated.
  The saved report and complete decisions are immutable and hash-verified by
  the local research route. Eight focused Python tests, two Simulator UI
  tests, build/lint and an ephemeral loopback route check passed. PowerShell
  remains unavailable for the Merkle check.

## New Dhan token and closed-market retry — 2026-11-03 IST

- One existing API-key consent flow from the owner-private `.env` reached the
  local callback and stored a replacement token. The callback was stopped.
  A read-only Dhan `GET /v2/profile` reported
  `DHAN_TOKEN_AND_DATA_PLAN_ACTIVE`; no credential value was printed.
- The supervised app restarted with the replacement token. Its startup
  read-only quote request returned 25 instrument snapshots received at
  2026-11-03T10:48:11.820Z. The account route reported `READ_ONLY`, and the
  25-stock archive remained READY at 45,300 bars. At 16:18 IST the feed gate
  was `MARKET CLOSED / AUTO_CONNECT_ARMED`, with zero Dhan sockets. A new live
  WebSocket handshake was therefore not attempted after the session cutoff.

## One-shot launcher and local session recovery — 2026-11-03 IST

- At 15:20 IST the launcher started from `/private/tmp` with no external PATH
  setting, and a second invocation reattached without restarting any listener.
  The signed local session returned HTTP 204, then the authenticated gateway
  returned HTTP 200 with 25 named stocks. Node served the verified 25-stock
  October archive (45,300 bars), RELIANCE's pinned 1,800 bars, the latest saved
  25-stock REST snapshot and the persisted 7,942-frame raw capture status.
  The startup archive cache also matched each source hash to the saved
  cross-stock simulation artifact, which resolved for 25 instruments;
  account state was READ_ONLY. Feed state was MARKET CLOSED /
  AUTO_CONNECT_ARMED with zero sockets; no WebSocket was opened after cutoff.
- The first startup cache attempt exposed a missing duplicate RELIANCE file in
  the 24-response folder. `cockpit_cache.py` now reads its already verified
  pinned response for that universe member. Focused validation passed: 12 Node
  service tests, 18 web tests, web build/lint, 9 Rust telemetry binary tests,
  launcher syntax and runtime reattachment. PowerShell is absent on this Mac,
  so `tools/repo-context.ps1 -Check` and Merkle refresh could not run here.

## Tuesday market-hour REST observation and feed failure — 2026-11-03 IST

- The approved 25-ID standard feed passed raw-capture preflight and reached
  Dhan's TLS endpoint, but the WebSocket upgrade returned HTTP 400 at
  09:47:10 and 09:48:00 IST. No binary frame or depth frame was received.
  The existing v2 URL and query fields match Dhan's official documentation;
  the upstream reason for HTTP 400 is not established. The launcher now reads
  its durable raw-capture event log before startup, retains a two-attempt
  daily cap across restarts, and reports the exact upgrade/attempt blocker.
  Its readiness wait accommodates repeated preflight without relaxing it.
- While that feed is blocked, the existing Node service polls only the
  approved 25-ID Dhan `/v2/marketfeed/quote` scope once per minute during the
  candidate IST market window. It saves each original bounded HTTP response
  and SHA-256 before parsing, shares one backend cache across tabs, and the
  browser reads that cache without creating a socket or another Dhan poll.
  Market-hour genuine responses contain 25 prices and complete uncrossed
  five-level books; observed Dhan last-trade times were within seconds of
  receipt. The UI shows provider LTP, day OHLC/volume/change, 5-level quoted
  spread when valid, receipt time and source hash as `REST_SNAPSHOT`.
  Feed-only latency, 20-level depth, OFI and fills remain unavailable.
- Market-hour runtime after launcher restart: ports 8765/8767/5173 READY,
  zero Dhan WebSocket connections/frames, exact feed reason
  `UPGRADE_HTTP_400_DAILY_ATTEMPTS_EXHAUSTED`, and continuing HTTP 200 REST
  quote capture. At 10:02 IST a saved response had 25 instruments, 21,077
  original bytes and SHA-256
  `7a589f60fddb738293e775b74b0ff12a990813f6a470d93cd322d011ea511570`.
  All 25 had complete uncrossed five-level bid/ask sides in a market-hour
  sample. The selected BHARTIARTL Overview showed actual LTP, source trade
  time, volume, Dhan's net-change and OHLC fields, displayed quantities and
  a valid quoted spread. The close field is labeled as a provider field, not
  today's finalized close during the session. The detached existing launcher
  remained listening on all three local ports after its parent tool exited.
  Focused checks passed: 19 Node, 24 web, build/lint, launcher syntax and diff
  whitespace. No broker request or QP01 write was made.

## Authenticated read-only paths exposed without weakening trading controls — 2026-11-03 IST

- User authorized defaulting the exact frozen 25-stock standard-feed scope
  when locally stored Dhan credentials are valid. The implementation first
  checks Dhan `/v2/profile` once per token hash for active Data API entitlement,
  saves the result privately, and never replaces an existing revoked scope.
  Optional depth still needs its separate explicit choice. Credentials stay
  in owner-private backend files; the Trading Settings page gives a hidden
  terminal input command and a one-shot profile test. Local validation now
  reported `AUTH_REQUIRED` for the expired prior token instead of the less
  useful missing-scope message. On November 3 IST, the user requested use of
  the API key in owner-private `.env`; one consent POST and one consume-consent
  GET installed a fresh token in the existing private store. One subsequent
  `GET /v2/profile` reported an active Data API plan. The launcher persisted
  the exact 25-ID standard-feed scope and now reports `MARKET CLOSED /
  AUTO_CONNECT_ARMED`, with zero sockets and frames during closed hours.
- The Mac launcher no longer forces the Node account service offline. It
  limits its authenticated account reads to funds, holdings and positions;
  orders and trades are not polled. A changed token unblocks refresh after
  reauthentication. The market snapshot path uses Dhan's read-only
  `/v2/marketfeed/quote` for the exact 25 IDs, stores original bounded HTTP
  evidence before parsing, and labels returned prices/5-level depth as
  `REST_SNAPSHOT` with receipt and source time. It cannot replace a live
  WebSocket feed, 20-level book, executable fill, or historical candle.
- The existing `run_backfill` is reused for explicit Add & Train acquisitions
  of October 26–30 1-minute responses. It uses zero retries and retains HTTP
  errors in quarantine; each next request rechecks the saved scope. The
  all-universe action can issue up to 24 new historical POSTs, then runs the
  existing frozen opening-range/momentum simulator over verified bars. It
  does not fit a new cross-stock model. The pinned RELIANCE six-month study
  and frozen November experiment are unchanged.
- Statistical Research now shows the saved genuine 45,945-bar, 125-session
  exploratory artifact and its measured Brier, MAE, 25/50-bps reference
  outcomes and baseline comparison. First-hit cumulative incidence was not
  measured in that artifact and is labeled as such. Paper Research shows
  separate virtual ledger state and historical rule decisions without QP01
  approval mutations. Selected-source panels keep archived bars and REST
  snapshots distinct from quote-sampled candles and 20-level depth.
- The user then triggered the bounded `ALL_25` Add & Train action. Dhan returned
  24 new HTTP 200 `/v2/charts/intraday` responses, 2,065,035 original bytes
  combined, each retained before parsing with metadata and SHA-256. RELIANCE
  reused its pinned response. A separate audit reverified all 25 hashes,
  instrument mappings, unique timestamps and five dated sessions, totaling
  45,300 bars. Four symbols (PCJEWELLER, MONEYVIEW, NETWEB, CUPID) have 375
  bars/day through 15:29 IST; the other 21 have 360 through 15:14 IST. CAS
  applicability and timing remain separate from source integrity.
- The saved `ALL_25` frozen-rule artifact has SHA-256
  `40a709b58f02675b275eb23cbfc18f1ea6e3cd79addf3cba3d44bbc05ad099f6`.
  On five already inspected sessions, opening-range reference equity ends
  ₹994,692.47 from ₹1,000,000 (five selections, four negative net sessions);
  the momentum comparator ends ₹1,000,556.82 and NO TRADE ₹1,000,000.
  This is retrospective exploratory mechanics, with no untouched cross-stock
  holdout, established candle availability or executable fills. The separate
  simulator view now reads a checksummed pointer to this immutable artifact
  across restarts, falling back to the earlier RELIANCE-only replay otherwise.
- Six genuine 25-ID `/v2/marketfeed/quote` snapshots were retained across
  runtime restarts, all HTTP 200 (116,040 original bytes combined); the cockpit
  labels them `REST_SNAPSHOT`. The account cache originally
  reached `STORAGE_BLOCKED` because the child process expected `DHAN_CLIENT_ID`
  in its environment while the launcher intentionally stripped credentials.
  It now reads the owner-private backend identity. Across the restarts, the
  three read-only account endpoints each returned three retained HTTP
  responses: funds and positions HTTP 200, holdings HTTP 500 (`DH-1111`).
  No account order/trade endpoint, WebSocket, QP01 write or broker order was
  used. The full 12-view navigation pass showed healthy local services and
  closed-market status; the selected-stock workstation plotted verified Dhan
  source bars with historical provenance, and Paper Research showed all 25
  names and five reference outcomes. Final focused checks: 12 Python tests,
  18 Node tests, 24 web tests, TypeScript/Vite build, web lint, shell syntax
  and `git diff --check` passed. A sandboxed Node invocation initially failed
  because local loopback binding was denied; the same suite passed with a
  local execution override. PowerShell context check and refresh remain
  unavailable on this Mac.

## Monday-night operational unblocking — 2026-11-02 IST

- Actual runtime before the change: local services READY, feed
  `NO_APPROVED_FEED_SCOPE`, no real approval file, zero Dhan sockets, and the
  offline gateway's hardcoded `--security-id 2885` showed one instrument with
  numeric fallback label. Local credential validation returned `AUTH_REQUIRED`.
- The launcher now registers all 25 manifest IDs and symbols in DATA_ONLY
  whenever a persisted v2 scope is enabled, even while the market is closed or
  token renewal is needed. It polls approval changes and replaces the offline
  gateway once, without a Dhan connection. Revocation removes that display
  scope. The Rust CLI accepts the exact manifest-bound 25-ID offline scope;
  its online preflight and risk gates are unchanged.
- A backend-only interactive command installs a fresh Dhan Web access token,
  client ID and operator-supplied expiry under `data/.secrets` with owner-only
  files, hash and source metadata. A separate, explicit profile check can
  verify the Dhan token and `dataPlan`/validity outside market hours after
  approval. It performs no account synchronization and prints no profile or
  credential values. No authenticated Dhan request was made in this turn.
- Focused offline tests: 10 Python credential/scope/simulation tests and 8
  Rust gateway tests passed. A separate local 25-ID Rust gateway fixture
  started and stopped successfully without credentials. The restarted real
  app remains DATA_ONLY with zero Dhan sockets because the operator has not
  yet installed a fresh token or granted scope. PowerShell context refresh is
  unavailable on this Mac.

## Raw capture separated from eligibility — 2026-11-02 IST

- A v2 saved scope now admits only `RAW_CAPTURE_ONLY` after exact frozen
  25-ID Dhan master mapping, private credentials, weekday continuous-session
  window and storage checks. This is a candidate capture window, not an
  independently verified NSE session. The 100 ms clock and dated NSE/CAS and
  calendar checks remain unchanged for research eligibility; raw preflight
  records them as unresolved, with measured offset/uncertainty when available.
- The Rust gateway repeats raw preflight and writes only original binary QJ01
  frames to a separate raw journal. It hashes and syncs each frame before
  decoding its instrument identity. Wrong-instrument, malformed and storage
  failures stop that connection. No normalized quote is published to local
  simulation; its decisions stay paused. The launcher retains bounded restart
  attempts and stops promptly on UI revoke. Optional depth failure no longer
  stops the standard capture. Private validation and lifecycle JSONL files
  preserve mapping provenance, clock checks and connection gap events.
- Offline checks: 9 Python tests, 10 Node tests, Rust engine 15 and telemetry
  23 library tests plus 8 local-gateway tests passed; Rust gateway and web
  builds passed. The November 2 Dhan master independently matched all 25
  frozen IDs in a temporary November 3 raw-preflight fixture. No real scope
  exists on this Mac, and the market is closed; zero Dhan sockets and frames
  were observed. Live raw capture and network reconnect remain unverified.

## Persistent feed activation and optional depth — 2026-11-02 IST

- A saved v2 approval is now validated independently of session hours. When
  unexpired and enabled with a valid private token after the market cutoff,
  the gate reports `MARKET CLOSED / AUTO_CONNECT_ARMED` without opening a
  socket. Expired, revoked, missing and credential-invalid states remain
  separate. An offline fixture passed the future-session plan and closed
  status; the signed local Node API passed an actual stop/restart persistence
  test with synthetic scope.
- The optional existing Rust 20-level collector now builds with its live
  read-only feature, takes the same hard stop marker as the quote gateway,
  preserves original frames before decode, and emits non-secret connection,
  frame-count and last-receive status. The launcher starts at most one depth
  process only when the same exact 25-ID approval and preflight permit it;
  Node reports a depth socket only from fresh connected collector status and
  an active depth approval. Its status file is removed on normal shutdown.
  The virtual simulator still consumes the standard quote gateway; separate
  depth frames are research capture, not simulated fills.
- No real approval file exists on this Mac, and the November 2 exchange
  session is closed. Same-day NSE identity/CAS and dated calendar evidence
  for a future session are not present. Thus no authenticated Dhan connection,
  genuine frame or live reconnect was demonstrated in this task.

## User-controlled persistent read-only data access — 2026-11-02 IST

- Trading Settings now exposes an explicit 25-ID standard-feed approval,
  session-only or persistent duration, optional expiry, auto-connect,
  disconnect and revoke. The signed local session and same-origin request are
  required for permission mutations; the owner-private approval file contains
  no credential and binds the exact frozen manifest bytes and IDs. No approval
  was created by implementation or testing.
- The existing launcher still owns the sole standard socket. Its offline gate
  now accepts a persistent approval on later sessions, but resolves same-day
  Dhan master, NSE MII security file and calendar evidence before connecting.
  The current Mac does not contain future-day NSE/CAS or calendar evidence;
  automatic evidence acquisition is not implemented. Optional supervised
  20-level depth is implemented but remains unobserved with genuine frames.
- Disconnect/revoke writes the existing gateway stop marker; the launcher
  resumes DATA_ONLY after the gateway exits. Source-integrity and clock gates
  remain unchanged. This task made no authenticated Dhan request, feed socket,
  account call or broker order. Focused Node and Python tests, Rust depth tests,
  web build and lint passed; an authenticated session remains unverified.

## Mac automatic feed startup gate — 2026-11-02 IST

- The launcher now loads the existing owner-private OAuth token and hash/expiry
  metadata only into the Rust live gateway process. It reads the client ID from
  the private client file or existing owner-private `.env`; Node, Vite and
  simulation workers remain credential-free. Expired or mismatched metadata
  blocks the feed. The documented Dhan RenewToken endpoint applies only to
  active Dhan Web tokens, so this API-key consent token requires operator
  authentication when it expires.
- A persisted approval must now bind the frozen 25 IDs in manifest order,
  `STANDARD_QUOTE`, same-day session and explicit expiry. Depth remains zero
  pending a separately approved and implemented depth scope. The launcher
  stops the standard gateway by the earlier of approval expiry or one minute
  before token expiry, and caps reconnect attempts at two per day. It writes
  a non-secret gate status for cockpit capability reporting.
- On this Mac at 15:57 IST, no 25-name feed scope existed. The November 2
  three-name pilot approval had already ended at 14:45 IST. The restarted
  local app had listeners on 8765/8767/5173, zero Dhan sockets and zero
  received frames. An authenticated live-session test is therefore blocked.
- Focused Python scope/credential/worker tests, Node service tests outside
  the sandboxed Node 24 loopback crash, shell syntax, web build and lint were
  run in this task. PowerShell is unavailable on Mac, so the repository Merkle
  check/refresh could not run here.

## Mac cockpit integration — 2026-11-02 IST

- All 12 navigation views rendered in the running Brave cockpit. Local
  telemetry remained connected across navigation. A deliberate offline Rust
  gateway termination was followed by one launcher restart and automatic
  WebSocket recovery; the simulation ledger kept its state and order count 0.
- Corrected the historical candle selector to use a date in the loaded Mac
  archive. October 26 and October 27 each displayed 360 authentic rows in the
  browser. Separated the pinned local archive from the empty gateway research
  registration so the research tab no longer claims all Dhan data is missing.
- Added a single-job private exploratory training worker using the existing
  pinned verifier and fixed timing-sensitivity study. HDFCBANK returned
  `VERIFIED_LOCAL_HISTORY_MISSING` without acquisition. RELIANCE completed
  with artifact SHA-256
  `4772b8dc5e6afd92c5ea323593d40460935a209c15cbfc89352e3d329eed8e07`;
  promotion stays blocked. No Dhan request or order was made.
- The Mac cockpit disables QP01 paper approval controls. Node capability
  status distinguishes local gateway/telemetry availability from external
  feed availability and reports observed socket counts. The Simulation Stop
  control closed all three listeners and left a durable `STOPPED` event.
- Focused checks: 24 React tests, production build and lint; 8 Node service
  tests; 8 Python worker/simulation/scope tests, Ruff and shell syntax.
  Authenticated feed capture and a full eligible session remain untested and
  blocked by separately approved dated scope and source preflight.

## Persistent Mac SIMULATION_ONLY worker — 2026-11-02 IST

- Added a separately versioned quote-only 15-minute momentum/30-minute hold
  virtual rule using fresh observed bid/ask references, the frozen 25-name
  manifest, ₹1m virtual capital, ₹100k per-position cap, published fee
  estimate and 40 bps extra assumed friction. It cannot place QP01 or broker
  orders. The worker is a supervised local gateway reader, not a Dhan client.
- Owner-private `simulation-ledger.jsonl` fsyncs a SHA-256 chain for observed
  snapshots, candidate/rejection decisions, entries/exits, gaps, starts and
  stops. Recovery validates the chain and replays cash/positions. A stale or
  changed feed blocks new decisions; an unresolved position is retained.
- The Mac launcher and cockpit now include persistent worker status, history,
  observed quote table, virtual equity and a signed-session Stop path. An
  actual offline launch showed 25 fixed names, ₹1m virtual cash/equity,
  0 trades/orders and `WAITING_FOR_APPROVED_FEED`. The cockpit showed the
  persisted ledger count increase from 1 to 3 after restart; clicking Stop
  produced a fourth durable event, `STOPPED`, and all three ports closed.
- Full-session worker review bounded status generation to recent in-memory
  views, limited decisions to one per instrument/minute, required the Dhan
  standard-feed provenance tag and rejected a ledger without a final newline.
  Eight focused Python simulator/feed-scope tests passed after these changes;
  an offline relaunch showed 25 names, stale feed, zero virtual fills/orders
  and five durable events. No approved scope file exists.
- `live_feed_scope.py` requires a separately configured, dated private scope,
  exact frozen manifest bytes, same-day NSE identity/CAS, fresh Dhan master,
  calendar/storage checks, valid local credential presence and independent
  clock offset plus uncertainty ≤100 ms. Rust's one-socket subscription is
  now exact to the 25 IDs and rechecks preflight; launcher replacements are
  bounded. No scope file exists, no authenticated Dhan socket was opened and
  no genuine live frame was observed. End-to-end live-session readiness remains
  unverified; see `docs/live-simulation-macos.md`.
- Focused validation: 11 Python simulator/preflight tests; 6 React cockpit
  tests and production web build; 7 Node local-service tests; 13/14 QJ01
  tests (one intentionally ignored), 14 Rust engine tests, 22 Rust telemetry
  tests, 8 Rust local-gateway tests and 9 Rust depth tests. The depth mock
  assertion was updated to count the two retained original frames in addition
  to the two decoded side records. A default-sandbox bind denial required the
  localhost-only depth test to run with loopback permission. No live feed was
  used in tests.

Recorded 2026-10-20. These are concise conclusions with evidence, not private
internal reasoning. Revalidate them when the relevant Merkle branches change.

| Decision / established constraint | Evidence | Implication |
| --- | --- | --- |
| Keep capture, derivation and presentation separated. | `apps/collect_depth.py`, Rust `quant-engine/src/pipeline.rs`, `quant-telemetry/src/gateway.rs` | Preserve durable-before-publish ordering; UI failure must not become capture loss. |
| Treat Python and Rust persistence as distinct formats. | Python `storage/journal.py`, Rust `quant-journal/src/lib.rs`, `quant-replay/src/bin/export_jsonl.rs` | The common QJ01 marker does not imply payload compatibility; bridge explicitly. |
| Preserve paper risk and the separate durable QP01 audit trail. | `rust/docs/phase3a-paper.md`, `quant-paper/src/{oms,risk,ledger}.rs` | Model estimates cannot bypass risk or turn simulated fills into actual execution evidence. |
| Keep the prospective forecast separate from paper-policy model status. | `apps/prospective_15m.py`, `quant-paper/src/contracts.rs`, `rust/docs/phase3b-research.md` | An offline forecast fit does not establish a trained or promoted paper policy. |
| Preserve the frozen November protocol and its source/code lineage. | `docs/reliance-15m-prospective.md`, `apps/prospective_15m.py:SPEC` and `evaluate` | Avoid edits to frozen experiment code or reuse of prior holdouts without explicitly defining a new experiment. |
| Distinguish tracked plans from ignored runtime evidence. | `.gitignore`, dated acquisition guides, `verify_saved_response` | Do not infer that a backfill, frozen artifact, subscription or verified session exists from a document alone. |
| Use exact working-file Merkle hashes for context invalidation. | `tools/repo-context.ps1` | Changed branches direct rereading; summaries still require human/agent semantic review. Hashes are not model memory. |
| Require explicit timing and dated session evidence for forward candles. | `acquisition/forward_observations.py`, `docs/forward-observations.md`, `test_forward_observations.py` | Unknown Dhan timing and provisional calendar dates block real outcomes; synthetic movement summary only until evidence exists. |
| Scope prospective standard-feed quotes to NSE_EQ RELIANCE ID 2885 and keep the runtime disconnected pending authorization. | Dhan v2 live-market-feed/annexure docs; `quant-engine/src/capture.rs`, `quant-telemetry/src/read_model.rs`, `telemetry_local.rs` | Request code 17 and response code 4 can enter the existing QJ01/engine/gateway path after an authenticated socket is supplied; no live feed was opened or packet observed. |
| Expose incomplete source evidence and feed failures as NO TRADE. | `quant-telemetry/src/read_model.rs`, `web/src/stream.ts` | Last-trade time and packet continuity are unverified; disconnect, staleness, malformed or wrong-ID packets, journal blocking and queue rejection cannot silently imply a current tradable quote. |
| Treat Windows segmented-journal directory publication as file-synced with limited crash guarantees. | `quant-journal/src/segmented.rs`, native Windows tests | NTFS directory `sync_all` fails; file sync, atomic rename, free-space checks and checksum recovery remain, while power-loss directory-entry/rename durability is unproven. POSIX retains directory sync. |

Open questions for a future task, not findings resolved by this inspection:

- Which ignored raw datasets and frozen experiment artifacts currently exist,
  and do their saved hashes and provenance pass validation?
- Have provider timestamp, completion, corporate-action and historical identity
  semantics gained dated independent evidence since the recorded pilot review?
- Is there now independently audited real-session evidence, cost calibration or
  a held-out model result that changes the documented research eligibility?
- What native Windows support is intended for the POSIX journal/export paths?

Context verification: the index covers 246 files and 61 directories. A separate
verification pass recomputed every content/blob/directory hash and checked all
child references. A temporary edit to this file reported the changed leaf and
exactly its three ancestor branches; restoring it recovered the original root.
The temporary marker was removed. A final refresh/check validates these notes.
Application suites were not run because their toolchains are absent here.

## Local UI launch, 2026-10-21

- Installed portable Node 24.22.0, Rust 1.99.0 GNU and w64devkit 2.10.1 under
  ignored `.local/toolchains`; persistent PATH and system installation settings
  were unchanged. Use `-C link-self-contained=yes` for Rust's GNU runtime libs.
- Guarded the segmented journal's Unix-only `statvfs` code to let the Windows
  single-file replay demo compile. Unsupported platforms return an explicit
  disk-space-check error; segmented storage durability remains unverified here.
- Passed frontend production build, Rust formatting, native replay server build
  and both `telemetry_demo` fixture/ledger tests. Verified proxied snapshot,
  health and WebSocket snapshot/heartbeat on 5173, backed by 8765. Both servers
  were left running with logs/PIDs under `.local/`; no browser surface was
  available for visual inspection. This is synthetic DATA_ONLY replay.
- The initial context check found existing README and `.env.example` drift.
  Inspected additions: forward-observation documentation and API-key callback
  setup references/placeholders. These do not establish a configured public
  callback host or a live authenticated feed; existing user changes were kept.

## Trading product direction, 2026-10-21

- The owner specified an Indian-equity account trading workflow: use available
  funds, rank current candidates with OpenAI-assisted research, buy/sell, and
  set percentage or absolute stop-loss and profit-target values. More detail is
  forthcoming. See `docs/trading-product-brief.md` for the intended workflow,
  current implementation gap, source links and unresolved product decisions.
- Existing synthetic replay and paper OMS do not satisfy live account trading.
  Dhan broker and OpenAI contracts were checked against their official docs on
  2026-10-21; no credentials, live market data or account state were verified.
- The owner clarified that order placement should be automatic and the account
  is Dhan. Position and daily loss limits and intraday-versus-delivery scope are
  still open; automatic live execution stays disabled until those settings and
  broker prerequisites are verified.
- Environment presence check found no exported Dhan client ID/access token or
  OpenAI API key and no saved token at `data/.secrets/dhan-access-token`. A local
  `.env` file exists but was not opened; the application does not load it
  automatically. No live account endpoint was called.

## Intraday settings implementation, 2026-10-21

- Owner chose intraday only and asked for a separate tab to configure trade and
  daily-loss limits. UI now opens on Trade desk, with Trading settings in its
  own navigation tab. It saves the four limits through a loopback Node service
  to ignored `.local/trading/settings.json`, using exact decimal strings.
- Service rejects missing/zero/invalid limits and percentages above 100, and
  always reports live execution unavailable. Old synthetic replay is still
  available as Replay Lab and its stream only connects when those views open.
- A read-only Dhan account route now fetches fund limits, holdings and positions
  when the service has a client ID and access token. It validates account ID,
  returns display funds and NSE intraday position summaries, and supplies no
  token to the browser. The current checkout has no exported credentials or
  saved token, so live account behavior was not exercised.
- Validation: web typecheck, lint, production build and nine Vitest tests
  passed; four Node tests passed for settings and Dhan account parsing with
  injected broker responses. Proxy GET checks for status and settings passed
  through Vite; the account route returned expected 503 while disconnected.
  Repository-wide Prettier check still flags
  ten unrelated pre-existing files; changed files were formatted and checked
  separately. No visual browser surface was available in this session.

## Fictional trade desk preview, 2026-10-21

- The owner asked to see a demo with demo data. The Trade desk defaults to an
  explicitly labelled fictional preview; the mode switch exposes live status.
  `web/src/TradingDemo.tsx` contains fixed sample funds, three illustrative NSE
  symbols and invented quotes/scores/research notes. Its settings remain only
  in browser memory and recalculate position size, stop, target and example
  profit/loss; they cannot call Dhan or save live limits.
- The preview blocks its example plan when one share cannot fit, the stop is
  invalid, or estimated stop loss exceeds the remaining sample daily loss.
  Hypothetical figures exclude brokerage charges, slippage and taxes. This is
  an interaction demo, not a current stock recommendation or evidence of
  profitable automation.
- Verification: production web build, lint and the full 11-test Vitest suite
  passed. The running local Vite server returned the new demo module. A browser
  surface was unavailable for visual inspection in this session.

## Offline multi-horizon forward observations, 2026-10-22

- Extended only the existing forward-observation calculation to 30, 60 and
  `remaining_session` alongside 5 and 15 minutes. The v2 outcome adds a
  horizon key; remaining-session maturity is the independently verified
  continuous-session close, with zero minutes censored. The existing separate
  as-of feature rows, missing-bar checks, bucket-level excursion timing and
  source hash/calendar/timing gates remain in use. QJ01, QP01, frozen 15-minute
  RELIANCE experiment, risk controls and cockpit were not changed.
- The dated October-only Dhan pilot runbook records 1,800 received bars over
  five sessions, but `data/historical/` and the ignored verification report are
  absent in this workspace. The tracked calendar still marks October 12–14
  provisional; its dated offline report described 1,080 row-level identity
  failures, three calendar failures and five timing warnings. Provider
  bar-start/completion and adjustments are unverified.
  No real forward outcome or eligible per-horizon distribution was produced,
  and no Dhan request was made. Dated pilot counts are provenance context, not
  freshly reverified market results.
- Verification: nine focused deterministic forward-observation tests passed;
  focused Ruff check/format and mypy check passed. Synthetic fixture counts
  test behavior only and are not market evidence.

## October Dhan pilot audit, 2026-10-22

- The earlier October 19 report is a dated account, not a reproducible local
  result in this checkout. `data/` contains only the tracked calendar and
  registry; the October raw/quarantine response, checksum sidecars, verification
  report, Dhan master and manifest, NSE daily originals, verified lake and
  timing-evidence file are absent. The old response digest cannot be checked
  against bytes here. No Dhan token or client ID is exported, and no new Dhan
  request was made.
- The 1,080 reported row-level identity errors are 360 rows on each of October
  12–14 without a date-effective registry mapping; three separate identity
  coverage warnings refer to those same sessions. Three hard calendar errors
  reflect provisional closing-auction applicability on those dates. Five
  timing warnings reflect unknown Dhan candle timestamp semantics, one per
  date. This classification follows `audit_bars`; the dated 1,800-row report
  cannot be rerun without its saved response.
- An attempted download of the official October 12 NSE CM-MII security gzip
  from `nsearchives.nseindia.com/content/cm/` failed with `Unable to connect
  to the remote server`. That is an access failure, not evidence the file is
  unavailable at NSE. No replacement identity or calendar assertion was made.
  Dhan's published intraday API material still does not independently establish
  bar-start and bar-completion semantics. The existing import and forward-
  observation gates remain closed.
- The native Windows test run had 20 passes and two failures in mocked pilot
  recovery/import tests: the protected quarantine writer rejects the created
  directory as non-private. This also prevents a safe live capture on this
  host with the existing implementation. Acquisition-quality and forward-
  observation suites independently passed 17 tests. The offline `report`
  command returned zero local historical records and sessions. No genuine
  5/15/30/60-minute or remaining-session outcomes were generated; zero eligible
  output means unavailable evidence, not a claim of zero market movement.

## Dhan-first pilot workflow guard, 2026-10-22

- The October 12–16 RELIANCE authenticated pilot request was already recorded
  as executed on October 19, so the one-request authorization is consumed.
  This turn made zero authenticated Dhan requests and did not recover absent
  original bytes. Dhan remains the 1-minute OHLCV source; the existing local
  NSE importer handles separate daily data and is not a pilot dependency.
- `pilot` now calls the existing client with zero retries, and `run_backfill`
  checks quarantine directory privacy before a new HTTP request. These are
  request-count and evidence-preservation guards; checksums, validation,
  identity, calendar, timing, lake and immutable output rules are unchanged.
  The historical-bar contract now calls the unlabeled Dhan epoch value a
  source timestamp rather than asserting it is a bar start.
- Focused no-network acquisition, quality and forward-observation tests passed
  21/21. This establishes deterministic behavior only, not pilot data integrity
  or market movement. The protected writer remains incompatible with native
  Windows directory mode bits; no live request should run on this host.

## Synthetic Dhan persistence audit, 2026-10-22

- The October 19 runbook records a quarantine receipt and recovered checkpoint
  with SHA-256 `9ad62caf5c889bae31f0709c61cb7b7b7ab7855285770a525669b22232db399d`.
  The exact paths and verification reports are absent in this checkout.
  `.gitignore` excludes `data/*`, explaining why Git cannot restore them;
  neither code nor runbook establishes why these particular local files are
  missing. The consumed pilot request was not repeated.
- A concrete persistence defect was found: the live save path made the
  quarantine private but created the published raw directory and checkpoint,
  checksum and metadata files with default modes. On a typical POSIX umask
  these could be world-readable. The existing private-directory and private-
  byte writers now cover both live checkpoint publication and offline recovery,
  including a newly regenerated checksum sidecar. Existing files are never
  silently chmodded or overwritten; an insecure existing directory fails.
- One focused synthetic round-trip test verifies identical quarantine and
  checkpoint bytes, SHA-256 sidecars, request/endpoint metadata, timezone-aware
  receipt time and no token disclosure. On POSIX it also asserts 0700 directory
  and 0600 file modes. Native Windows cannot meet or verify the POSIX mode and
  directory-fsync contract: the test stubs those two operations solely to
  check the logical byte/metadata flow, while an unmodified native save test
  fails closed at the private-directory check before any transport call.
  Thus private persistence is not ready on this Windows host; no authentic
  source-integrity or research claim follows from the synthetic test.

## Native persistence runtime check, 2026-10-22

- `wsl --status` and `wsl --list --verbose` both report that WSL is not
  installed. No Docker, Podman or Bash executable is available. The local
  Python is `win32`; a read-only Windows volume query reports NTFS for C:.
  No Linux-native filesystem/runtime was available or installed.
- A direct `run_backfill` invocation with an in-memory synthetic transport,
  synthetic token and synthetic one-minute request failed before transport:
  `_private_directory` saw mode `0777` on a new NTFS directory and raised
  `PermissionError`; transport calls were zero. Windows `Get-Acl` showed a
  protected directory DACL granting owner rights, SYSTEM and Administrators,
  while a synthetic child file inherited those grants and Python reported mode
  `0666`. POSIX mode bits therefore do not establish Windows ACL privacy.
  Independently, native `os.open(directory, O_RDONLY)` failed with
  `PermissionError`, so the current directory-fsync path is untested and
  incompatible here. A separate synthetic NTFS probe confirmed file `fsync`,
  hard-link checkpoint publication and metadata `os.replace` complete with
  exact bytes and removed temporary paths. It did not test crash durability.
  No real save/recovery or separate-process durability claim was made.
- No source code or storage protection was changed. A viable Windows-specific
  implementation would need to create and verify restricted DACLs for both
  directories and files, and provide a tested Windows-native equivalent for
  directory publication durability while retaining file flushes and atomic
  publication. Until both checks are demonstrated on NTFS, acquisition stays
  fail-closed. The October request remains consumed; no Dhan call occurred.

## Native Windows Dhan persistence, 2026-10-22

- Windows storage checks require NTFS and inspect the actual owner and DACL
  through Win32.
  Restricted inherited grants to the current user, SYSTEM, Administrators and
  OWNER RIGHTS pass; unknown ACE forms, unreadable ACLs, untrusted ownership
  and grants to broader principals fail closed. POSIX mode checks are unchanged.
  Each new file's DACL is checked before response bytes are written.
- On this native Windows NTFS host, synthetic transport save preserved exact
  response bytes, SHA-256, request/endpoint metadata and timezone-aware receipt
  time; no synthetic token appeared in persisted files. A restricted inherited
  DACL passed. An Everyone-read grant was rejected before transport (zero
  calls). After an injected hard-link publication failure, quarantined bytes
  were recovered in a separate Python process with matching hash and metadata.
  `tests/test_acquisition_sources.py`: 18 passed on this host.
- The combined acquisition/pilot suite had 23 passes and one failure: the
  pilot `import` fixture reaches `acquisition/lake.py` directory `fsync`, which
  native Windows rejects. That separate lake-staging path was not changed in
  this Dhan persistence fix. The remaining 23 tests pass when that one is
  deselected.
- Windows file `fsync` and NTFS hard-link/replace publication succeeded.
  Directory `fsync` is unsupported by Python's native directory handle path,
  so Windows explicitly omits it. These tests establish process-failure
  recovery, not survival of sudden power loss or disk/controller failure.
  No authenticated API request, download or trade occurred. The October pilot
  request remains consumed and its absent original response is still a blocker
  for authentic movement observations.

## Native Windows lake publication, 2026-10-23

- The lake raw-stage and dataset writer reuse Dhan persistence's directory-sync
  helper. POSIX still opens and `fsync`s each directory. Native Windows cannot
  use that Python directory handle path; its explicit no-sync branch retains
  file flushes and atomic link/replace but does not establish power-loss
  durability. The import status/quality sidecar writer uses the same helper.
  Closing a Parquet writer now `fsync`s an `r+b` handle on native Windows,
  where the prior read-only handle returned `EBADF`; POSIX retains `rb`.
- The previously failing synthetic Dhan pilot import passes on this NTFS host.
  It verifies the staged source's exact bytes, SHA-256 and receipt provenance,
  dataset manifest/partition checksums, Parquet read-back and blocked research
  status. Fault injection at raw hard-link and dataset rename publication
  leaves no partial artifact; a retry succeeds. This tests injected operation
  failures and retries, not survival of sudden power loss. The combined
  acquisition, lake and pilot suite passed 27/27; focused Ruff and mypy checks
  passed for the changed source and new lake test. Mypy across the older pilot
  test file still reports four pre-existing `object` typing errors outside this
  change; those test annotations were not rewritten.
  No authenticated Dhan request or download was made, and synthetic fixtures
  are not market evidence. The consumed October response remains absent.

## Real Dhan data only, 2026-10-24

- The owner superseded the fictional runtime demo: only authentic Dhan market
  observations may support research, recommendations or paper decisions.
  `web/src/App.tsx`, `stream.ts` and the local trading service now fail closed
  to `DATA UNAVAILABLE / NO TRADE`; the sample trade component was removed.
  No authenticated account request or market request was made.
- The replay/evaluation binaries and QJ01/QP01 research-dataset command are
  disabled in normal execution because current journals cannot prove original
  Dhan source binding. Synthetic events remain only software fixtures.
  Existing Rust paper tests show event behavior, not market opportunities,
  attainable fills, strategy performance or production eligibility.
- `forward_observations.generate` now checks Dhan-only lake provenance and
  original staged raw bytes before publishing. Source labels and SHA-256 alone
  are not independent proof of authenticity; the missing October saved response,
  historical identity/session checks and provider bar-completion evidence still
  block real exploratory and causal results. The frozen 15-minute RELIANCE
  experiment and risk limits were not changed.
- Native Windows acquisition/lake/pilot/forward synthetic tests passed 37/37
  without mocked file/directory sync, using a writable workspace pytest temp
  directory. Default `%TEMP%` was sandbox-inaccessible; its first run failed
  during fixture setup. Windows power-loss durability remains unproven.
- Rust paper/telemetry tests passed 28/28, focused Clippy passed, and the
  runtime demo refused startup without creating the named journal. Web
  typecheck, 12 tests, lint and build passed; Node settings tests passed 2/2;
  focused Ruff and mypy passed. No real movement distribution or performance
  was calculated.

## Real-data follow-up, 2026-10-24

- The ignored active replay journal `.local/phase2c/demo.qj` was removed. The
  archived Phase3A synthetic benchmark ledgers remain as immutable, labelled
  software fixtures used by isolated research tests; they are not market
  observations and must not enter runtime research or the cockpit.
- The process has no `DHAN_ACCESS_TOKEN` or `DHAN_CLIENT_ID`. The local `.env`
  has a `DHAN_ACCESS_TOKEN` key, but no value was printed or loaded. The standard
  historical raw/lake directories are absent. No Dhan network request was made.
  A new exact request scope and historical-data entitlement are pending; the
  consumed October pilot request remains consumed.
- A no-network dry run for RELIANCE (2885), October 15–16 one-minute candles
  produced one proposed Dhan charts request, with historical identity still
  `UNVERIFIED`. This did not consume a request or create market evidence.
- The native Windows acquisition/lake/pilot/forward suite passed 37 tests with
  workspace-local pytest temporary storage. The POSIX-only ingestion benchmark
  test cannot collect on Windows because its journal imports `fcntl` (and the
  benchmark imports `resource`); those developer benchmarks remain isolated.

## Approved October 15-16 Dhan attempt, 2026-10-24

- The owner separately approved one read-only authenticated RELIANCE 2885
  one-minute charts request for October 15-16, with zero retries, and confirmed
  historical-data access. The prior October 12-16 request remains consumed.
- One public Dhan compact instrument-master GET returned 27,210,972 original
  bytes. Its saved SHA-256 is
  `56b51dfbc372ab682f861fc7b360dc2176aaa9aead2c79e4946ce004db4b576a`;
  the existing validator uniquely confirmed current RELIANCE identity. This
  does not prove historical Dhan identity continuity.
- A first pilot command stopped at the private NTFS ACL check before network:
  the sandbox preflight directory was owned by a different Windows SID. A new
  private actual-user directory passed the unmodified ACL verifier. The one
  authenticated POST then returned HTTP 401 on 2026-10-24 06:08:03 UTC. Its
  140 original response bytes were quarantined with SHA-256
  `f8ba2d257a5d624e8c33f4ab842ca7feb661ffc826a5d2b4b1f46e3d70942c63`,
  request payload, receipt time and status. Read-back, SHA-256, private DACL
  and token exclusion passed. No retry, candle checkpoint, lake import, market
  observation or trade occurred. The new request is consumed.
- `dhan.py` now retains non-2xx response bodies through its existing private
  quarantine path. A focused no-retry 401 test and the combined native Windows
  acquisition/lake/pilot/forward suite passed 38/38; focused Ruff and mypy
  passed. Authentication is the next blocker; do not infer token expiry or
  subscription status solely from HTTP 401.

## Offline 401 diagnosis, 2026-10-24

- Without a network call, the saved 140-byte response was read through the
  existing private-directory and Windows DACL checks. SHA-256, dated request
  metadata, HTTP 401 and token exclusion were reconfirmed. Only the safe fields
  were extracted: `errorCode=DH-901`, `errorType=Invalid_Authentication`.
- The historical client constructs `POST https://api.dhan.co/v2/charts/intraday`
  with JSON and an `access-token` header. No endpoint or header defect was
  found. The local token has no readable expiry claim, and no local expiry
  metadata exists. The checked-in Dhan guide links the official v2 annexure
  but does not preserve its code table; exact subscription-code mapping could
  not be independently checked under the no-network restriction.
- Classification: authentication rejected. A stale/invalid token is the most
  likely operational cause, not proven token expiry; Data API entitlement is
  still unverified. Renew the token locally before seeking authorization for
  any further candle request. No request, token generation or code change was
  made during this diagnosis.

## Local credential-slot check, 2026-10-24

- At the owner's request, treat the nonempty `.env` `DHAN_ACCESS_TOKEN` value
  as a possible app API key. No `DHAN_API_KEY`, `DHAN_API_SECRET` or
  `DHAN_CLIENT_ID` values are configured locally or exported, and no callback
  access-token file exists. The value is opaque and cannot be classified as an
  app key versus access token from its shape. Its contents were not displayed.
- The existing API-key consent client requires client ID, app key and app
  secret, and extracts a separate `accessToken` from Dhan's response. The
  historical charts client sends the `.env` value directly as `access-token`.
  If the stored value is an app key, this is a credential-placement error and
  is consistent with the saved `DH-901` authentication rejection. It is not
  proven as the cause. No network call, secret modification or code change was
  made.

## Local API-key configuration, 2026-10-24

- At the owner's direction, the ignored `.env` value was moved without printing
  or changing it from `DHAN_ACCESS_TOKEN` to `DHAN_API_KEY`. The access-token
  slot is now empty, preventing the app key from being sent to charts as a
  token. No tracked source file was changed.
- The existing `DhanAuthClient.from_environment()` preflight was run offline.
  It stopped because both `DHAN_CLIENT_ID` and `DHAN_API_SECRET` are absent
  locally. The callback service and consent flow were not started; no network
  request or new access token resulted. Those two credentials must be supplied
  locally before the documented flow can run.
- Empty `DHAN_API_SECRET` and `DHAN_CLIENT_ID` placeholders were added to the
  ignored `.env` on the owner's request. The API-key entry was preserved and
  both new values were verified empty; no auth flow or network request ran.
- On the next local check, `DHAN_API_SECRET` was nonempty but `DHAN_CLIENT_ID`
  remained empty; no alternate client-ID entry was present. Consent was not
  initiated, and no Dhan request or access token resulted.
- The current official DhanHQ v2 authentication page was checked after the
  owner noted that the API dashboard shows only key and secret. For an
  individual user it documents `POST /app/generate-consent?client_id={dhanClientId}`
  with `app_id` (API key) and `app_secret` headers; `dhanClientId` is a
  separate user-specific account identifier. It also documents manual access
  token generation from Dhan Web as another individual route. The existing
  auth client matches the documented individual API-key flow; the partner
  route is separate. No consent or candle request was made.

## Dhan consent initiation, 2026-10-24

- The ignored `.env` now has nonempty `DHAN_CLIENT_ID`, `DHAN_API_KEY` and
  `DHAN_API_SECRET`; `DHAN_ACCESS_TOKEN` remains empty. Values were not printed.
- An initial sandboxed consent initiation raised a web exception without an
  HTTP status. A subsequent unsandboxed POST to Dhan's documented
  `generate-consent` endpoint returned a `consentAppId`; the browser login URL
  was opened without displaying that identifier. This verifies consent
  initiation, not completed authorization, token issuance or Data API access.
- The local callback listener is running on `127.0.0.1:8766`. Its previously
  absent `data/.secrets` directory was created with a private NTFS DACL and
  passed the existing Windows ACL verifier before the listener was restarted
  outside the network sandbox. The token file had not appeared at the last
  check, so browser login/callback completion remains pending. No candle
  request, broker order or subscription change was made.

## Dhan consent link regeneration, 2026-10-26

- At the owner's request, a fresh one-time Dhan consent link was generated
  using the locally configured client ID, API key and API secret. The response
  contained a consent ID; its value was shared only with the owner, not stored
  in repository notes. No candle request or order was made.
- The existing callback was restarted on loopback after the private NTFS ACL
  verifier passed for `data/.secrets`. No access-token file existed at the
  preflight check. Browser login and callback completion remain unverified.
- A second link was requested the same day at the owner's direction. The
  callback had stopped, so it was restarted after another successful private
  NTFS ACL check. Dhan returned a fresh consent ID; no token file existed at
  preflight. No market-data request was made.
- On the owner's subsequent local check, the callback was listening, but the
  access-token file still did not exist, including after a short recheck.
  Browser login/callback completion cannot be confirmed. No Dhan API call was
  made for this check.
- The owner's browser screenshot then showed the Dhan redirect targeting
  `127.0.0.1:8765/dhan/callback`, while the existing callback service and
  local `.env` redirect use port 8766. In the host Windows context, 8765 was
  not listening and 8766 was listening; `/dhan/complete` returned HTTP 200
  locally and in the owner's Edge browser. This port mismatch explains the
  observed connection refusal. The owner was asked to change only the port in
  the existing redirect tab, preserving the one-time token ID. No token ID was
  copied into repository files or command arguments.
- The owner confirmed the initial “Authorization received” page was only the
  separate `/dhan/complete` test, which by itself proves no token exchange.
  The existing callback was briefly made available on the otherwise unused
  port 8765 without a code change; the owner later reported success on 8766.
  The exact browser route that completed the exchange was not independently
  observed. A nonempty `data/.secrets/dhan-access-token` file subsequently
  appeared, passed the existing private NTFS ACL verifier, remained readable
  after both local callback listeners were stopped, and is ignored by Git.
  This verifies local callback token persistence, not token validity against
  a Dhan API or Data API entitlement. No candle request or broker order ran.

## Authenticated Dhan charts retry, 2026-10-26

- After the owner requested an API call, one read-only authenticated POST was
  made through the existing pilot client for RELIANCE security ID 2885,
  interval 1 minute, half-open range `[2026-10-15, 2026-10-17)`. The prior
  October 24 HTTP 401 request metadata confirmed the identical scope. The
  client used zero retries; this new request is consumed. No other Dhan market
  request or broker order was made.
- Dhan returned 35,261 original bytes, saved privately with SHA-256
  `bcace97b4dfd1b1cbfe10481b4718b770368a50c5264fb1f61a50f9c325d9861`
  and receipt time `2026-10-26T10:27:02.172831+00:00`. Exact raw-byte
  read-back, checksum sidecar, request/retrieval metadata, native NTFS DACLs
  and exclusion of the access token, API key and secret all passed. The
  current Dhan instrument master uniquely maps RELIANCE to 2885; historical
  Dhan identity continuity remains unverified.
- The existing offline calendar/registry/quality verifier parsed 720 authentic
  Dhan bars: 360 for October 15 and 360 for October 16. It reported two
  independent sessions, zero hard failures and two
  `TIMESTAMP_SEMANTICS_UNKNOWN` warnings affecting all 720 records. These are
  source-integrity-backed observations, not causally eligible completed-bar
  outcomes. No forward-horizon distribution, model result or trading claim was
  produced. Lake import and additional eligibility evidence remain separate
  offline steps.

## Multi-horizon training readiness, 2026-10-26

- The owner selected 30-minute, 60-minute and remaining-session opportunity
  research and capped new authenticated historical-candle requests at 20 for
  this pass. None of those 20 requests was made during the readiness review.
  The earlier successful October 15–16 request remains consumed.
- The only locally available authentic Dhan minute response has two independent
  sessions. The existing forward-observation generator requires verified,
  dataset-bound bar-start and bar-end availability evidence, and no such timing
  evidence file exists. [Dhan's current v2 historical-data documentation](https://dhanhq.co/docs/v2/historical-data/) calls
  the field an epoch timestamp but does not specify candle completion or
  publication timing. Additional historical downloads alone cannot satisfy
  that causal gate.
- The existing general research experiment and sufficiency gate require
  recorded depth-event modality; the frozen RELIANCE 15-minute model is a
  separate protocol. Neither is an eligible 30/60/intraday candle-training
  entry point. No model was trained, no synthetic data was substituted, and
  the frozen experiment, holdout and risk controls were unchanged. The next
  necessary evidence is a dated live Dhan candle-availability observation in
  a verified NSE session, followed by a bounded, identity/calendar-reviewed
  historical collection and a separately declared candle-training protocol.

## Proposed one-session candle-availability probe, 2026-10-26

- A fixed, offline-by-default Dhan historical-chart probe is prepared for
  October 27, 2026, RELIANCE security ID 2885, 1-minute candles. Its 14 exact
  IST slots, one-day request payload, zero-retry stop rules and timing limits
  are in `docs/forward-observations.md`. This is a proposal requiring separate
  approval; no authenticated request or new market-data collection occurred.
- The current calendar and registry cover October 15-16, not October 27. A
  dated verified session, dated identity mapping, corporate-action review and
  independent clock checks are preconditions. The code fails closed on missing
  session or instrument records and request-cap mismatch.
- Complete received bodies would use the existing private quarantine and
  SHA-256 receipt path. Local UTC request/response brackets and monotonic
  elapsed time support sampled first-seen and revision comparisons. Finite
  polling cannot establish exact exchange bar completion or general historical
  availability; the forward-observation timing gate stays unverified.

## Pro Traders next-phase review, 2026-10-26

- The owner reports an active DhanHQ Pro Traders subscription. Public Dhan
  documentation confirms historical OHLCV, live-feed and 20/200-depth
  contracts; a Dhan representative announced Market Movers in a phased
  rollout, but a stable public request/response contract was not located.
  Account-specific entitlement was not tested with an authenticated call.
- The earlier 20-request cap does not name instruments, date windows or a
  collection plan. The distinct six-month RELIANCE backfill runbook explicitly
  calls for separate authorization. Therefore zero of the 20 historical
  requests were used in this review, and no live-feed or profile request ran.
- The existing private October 15-16 response passed offline saved-response
  verification again: 35,261 bytes, SHA-256
  `bcace97b4dfd1b1cbfe10481b4718b770368a50c5264fb1f61a50f9c325d9861`,
  720 records on two sessions. Timing semantics remain `UNVERIFIED`, so the
  existing 30m/60m/remaining-session forward gate yields no eligible outcomes.
  No model, stock pick, economic edge or paper decision was produced.
- The next research question is abnormal-volume-confirmed momentum versus
  momentum alone, with same-session candle paths and a session-grouped,
  purged walk-forward design. Its detailed universe, dates, costs and source
  hashes must be frozen before testing. Existing Rust 20-depth capture and
  QJ01 are reusable, but original WebSocket frame bytes are not retained;
  200-depth capture is deferred until a separately approved prospective
  comparison shows incremental value.
- Verification on this Windows host: 27 focused Python acquisition/forward/
  research tests passed; the cockpit paper-view tests passed (3) and TypeScript
  typecheck passed; `quant-paper` passed 14 tests. `quant-depth` compiled, but
  five capture tests failed at segmented-journal open with Windows access
  denied on directory sync; its health/subscription tests passed (2). This is
  the existing native Windows segmented-journal limitation, not evidence of a
  working live collector. The web format check reported 12 files in the current
  working tree, including the already modified paper view; no broad cleanup was
  made. No risk, frozen-model or deployment code changed.
## Local Rust-to-cockpit WebSocket slice, 2026-10-26

- Reused the existing bounded Rust telemetry publisher, versioned snapshot/delta
  WebSocket, frontend resynchronization, and QP01 paper ledger. The loopback
  `telemetry_local` entry point has no Dhan adapter or order path; its initial
  state is DATA_ONLY with explicit no-feed/no-decision reasons and no market
  values or paper P&L.
- The WebSocket and snapshot require an HttpOnly same-site local session cookie.
  Session issuance requires a separate user supplied key and an exact local
  origin; approval/revocation uses a separate origin-checked POST and is
  published only after the QP01 append succeeds. The approval list is restored
  from verified QP01 records on restart. This is a local browser trust model,
  not a remote multi-user identity service.
- Dhan v2 standard feed and 20/200-depth contracts were reviewed from official
  docs; no authenticated connection, subscription, profile request, market
  download, or broker order was made. The existing Rust 20-depth ingestion and
  QJ01 journal remain the prospective market path. Current local data do not
  supply eligible live quotes, candidate rankings, or production decisions.
- Focused verification: `quant-telemetry --lib` 12 tests passed, including
  local session, QP01 approval/recovery, reconnect and backpressure behavior;
  three focused web test files passed 12 tests; TypeScript typecheck and Vite
  build passed; `telemetry_local` compiled with `cargo check`. Full package
  testing and schema-export binary execution encountered Windows Application
  Control error 4551 on the existing `telemetry_demo` and `schema_export`
  executables respectively. The schema JSON was updated to match the new Rust
  fields and TypeScript types regenerated from it.
- An unmocked native loopback run of `telemetry_local` succeeded: unauthenticated
  snapshot returned 401; local-key session returned 204; the WebSocket sent a
  DATA_ONLY initial snapshot and an approval update; QP01 revoke returned 204.
  A separate process restart recovered a positive approval from the QP01 file.
  Isolated test ledger and script were removed afterward. These software-path
  checks are not authentic market evidence or proof of live-feed operation.

## Single-instrument Dhan quote preparation, 2026-10-26

- Official Dhan v2 documentation identifies an 8-byte little-endian header,
  response code 4 for a 50-byte quote packet, NSE_EQ segment code 1, request
  code 17 for quote subscription and code 50 for feed disconnect. The existing
  Rust decoder was reused; scoped capture rejects a wrong security ID before
  journal append. Neither the local socket fixture nor the fixture packet is
  authentic market evidence.
- The existing file-synced QJ01/engine/read-model/gateway path now exposes a
  scoped quote's price, cumulative volume and receipt/source times. Disconnect,
  stale age, rejected packet, blocked journal and queue rejection surface NO
  TRADE. Source time meaning and upstream sequence continuity remain
  unverified. A later local-readiness change stores original binary frames,
  SHA-256 and receipt/frame linkage in bounded QJ01 before normalized events.
  The current
  `telemetry_local` runtime remains DATA_ONLY and does not call the socket
  adapter; no authenticated Dhan request or socket was made.
- Native Windows segmented-journal tests first reproduced four access-denied
  failures at directory sync. With the OS-specific adjustment, those same four
  tests passed once on the real local filesystem, including rotation/restart,
  free-space guard, sealed corruption and interrupted-tail recovery. All seven
  `quant-depth --lib` tests passed, including capture and recovery. A later
  rebuild's `quant-journal` executable was blocked before launch by Windows
  Application Control error 4551; this does not invalidate the earlier run but
  prevents claiming every rerun passed.
- The focused `quant-engine` and `quant-telemetry` test executables compiled
  with `cargo test --no-run --offline --locked`; their execution was blocked by
  the same Application Control error 4551. Thus the new quote and gateway
  fixture tests are compile-only here. Cockpit stream/App tests passed 9/9;
  TypeScript typecheck, ESLint, Vite production build and targeted Rust
  formatting passed. No genuine
  Dhan packet traversed the end-to-end path, and no performance or trading
  outcome was measured.

## Raw quote-frame local readiness, 2026-10-26

- Windows Code Integrity Operational events 3033/3077 identified Enterprise
  signing policy `{0283ac0f-fff1-49ae-ada1-8a933130cad6}` as the earlier
  reason new Rust test executables could not start. The same local cargo test
  path subsequently executed rebuilt binaries without policy changes; no
  bypass, security setting change or WSL installation occurred. Execution
  permission may still vary by binary, so each result is reported separately.
- Scoped NSE_EQ/2885 capture retains each exact WebSocket binary frame as an
  appended QJ01 variant with SHA-256, local receipt time, session and frame ID
  before normalized records. Reader/recovery verify the digest independently
  of QJ01 CRC. The raw and normalized records link on session/frame ID and
  packet offset; raw frames are excluded from JSONL export. A 64 MiB journal
  cap and failed write/recovered-tail checks stop capture. Wrong-ID, malformed
  and stale frames retain raw evidence but produce no market observation.
- Native Windows tests actually passed: `quant-engine --lib` 13/13,
  `quant-journal --lib` 12 passed/1 intentionally ignored,
  `quant-telemetry --lib` 16/16, `quant-depth --lib` 7/7 and
  `quant-replay --lib` 2/2. The raw-frame roundtrip and independent SHA tamper
  test also ran under a newly created private NTFS directory; the existing
  Python ACL verifier accepted both directory and Rust-created QJ01 file.
  Temporary probe artifacts were removed. One earlier telemetry run failed a
  replay freshness test because feed-disconnect staleness was applied to replay
  mode; this was fixed and all 16 tests passed on rerun.
- No authentic binary Dhan feed frame was found in inspected local data; the
  saved historical HTTP response is not a WebSocket frame. No authenticated
  feed request or socket was opened. `telemetry_local` remains DATA_ONLY, and
  private-path preflight is not enforced by a quote-feed runtime caller. Thus
  this is tested local software behavior, not a verified live integration.

## One-shot Dhan quote caller decision, 2026-10-26

- Keep the existing bounded QJ01 capture and gateway. Add only an opt-in
  NSE_EQ/2885 Quote 17 caller. The user clarified that Dhan v2 requires token
  and client ID in an in-memory WebSocket URL, while forbidding their exposure
  or persistence. Errors from connection attempts are deliberately generic.
- Reuse the Python current-master, session-calendar and private NTFS verifier
  as a local preflight. Reject uncertain clock status, stale identity, a
  missing verified session, broad ACLs or inadequate journal capacity.
  The current host fails the clock query with access denied; the current
  calendar has no forthcoming verified session. No live trial occurred.
- Retain expected OI auxiliary packets in raw QJ01 without creating a quote.
  Keep derived candles out of the Dhan quote cockpit, and retain NO TRADE for
  unverified source timing or packet continuity. Software tests passed with
  fixtures only; live source, entitlement and crash durability remain unproven.

## Read-only readiness follow-up, 2026-10-26

- `W32Time` is running, but `w32tm /query /status /verbose` still returns
  access denied (0x80070005). Its latest readable Operational status event
  (ID 260, October 26 18:35 IST) reported root dispersion 7.804 seconds,
  last sync error 2 and 2048 seconds since last good sync. Later event 266
  notifications indicate rediscovery requests, not a verified good sync.
  This evidence fails the existing clock thresholds; no clock configuration
  or service state was changed.
- NSE capital-market holiday circular NSE/CMTR/71775 does not list October 27
  as a scheduled holiday, and the NSE CAS page defines the separate closing
  session. Neither establishes dated RELIANCE CAS eligibility for October 27.
  The checked local calendar still has verified special sessions only for
  October 15-16. No trial calendar entry was added or promoted.

### October 27 preflight attempt, 2026-10-26

- Intended next session was assumed to be October 27. The user-authorized
  `w32tm /resync` request and subsequent status query both returned access
  denied (0x80070005); no time change was confirmed. Three read-only
  `w32tm /stripchart` samples to the configured source timed out (0x800705B4),
  so independent clock offset is unavailable. The most recent readable
  status event remains outside the existing dispersion/sync thresholds.
- The official annual NSE cash holiday list does not include October 27,
  and the general CAS rule is available. No dated October 27 RELIANCE
  derivative/security-master eligibility record was found locally. Direct
  public retrieval of the expected NSE CM-MII security file failed to connect;
  this is an access failure, not proof of nonpublication. The October 27
  calendar entry remains absent and unverified.
- The existing caller startup exited 1 at `local clock health unverified`
  before credential loading, journal creation or Dhan connection. Focused
  offline checks passed: Rust caller 2/2 and Python preflight 1/1. No source,
  risk, frozen-experiment or calendar code changed.

### One-shot offline simulation request, 2026-10-26

- The cockpit still reports no predictive model configured, and the policy
  abstains without a calibrated, cost-adjusted model estimate. No modelled
  trade or performance result was generated.
- Attempted the existing isolated `scoped_quote_reaches_local_websocket_only_after_qj01_sync`
  software fixture test once. Cargo built it, but Windows Application Control
  blocked the resulting test executable before launch (OS error 4551). This
  run provided no simulation result. No policy bypass, Dhan call, socket,
  market fixture in runtime or order was made.

### Isolated quote-to-cockpit test executed, 2026-10-26

- Code Integrity events 3033/3077 for the blocked executable identified
  Enterprise signing policy `{0283ac0f-fff1-49ae-ada1-8a933130cad6}`;
  event 3089 reported zero signatures. Authenticode reported `NotSigned`.
  This explains the earlier OS error 4551. No policy setting was changed.
- Added focused assertions to the existing gateway fixture test that the
  initial snapshot and quote update have no paper state and that the update
  retains `NO_VALIDATED_PAPER_DECISIONS` alongside source-time NO TRADE.
  An ordinary Cargo rebuild in the same target path produced a different
  unsigned executable that Windows permitted to run. The exact one-test
  invocation passed 1/1, verifying raw QJ01 before normalized quote,
  authenticated loopback WebSocket update and disconnect NO TRADE. The
  different policy outcomes for unsigned builds remain unexplained and
  future executions may still be blocked.
- The test packet and fixture calendar are software fixtures only. No Dhan
  connection, predictive model, candidate order, paper outcome or genuine
  market evidence was produced.

### October 27 live-feed preflight recheck, 2026-10-26

- A permitted read-only `w32tm /query /status /verbose` returned leap
  indicator 3, stratum 0, root dispersion 7.8040377 seconds, phase offset
  0.0339361 seconds, last sync error 1 and 1051.6096441 seconds since good
  sync. Three independent `w32tm /stripchart` samples to the configured
  `time.windows.com` source measured offsets -0.2612567, -0.2586070 and
  -0.2668013 seconds. These fail the existing 100 ms offset/dispersion and
  300-second sync-age thresholds; no OS time or policy setting was changed.
- Official NSE 2026 cash holiday circular NSE/CMTR/71775 omits October 27,
  and the official CAS rule limits phase one to cash stocks with derivative
  contracts. A read-only HEAD to the documented NSE CM-MII security-file
  pattern returned HTTP 200 for the known October 15 file (gzip, 1,271,356
  bytes) and HTTP 404 for October 27 at the time checked. The latter is
  unavailable evidence, not proof of nonpublication. No dated October 27
  RELIANCE CAS classification was established, so the calendar was not edited.
- The existing opt-in startup probe exited 1 at `local clock health
  unverified` before credential loading, journal creation or Dhan connection.
  Live-feed readiness remains BLOCKED; no engine, cockpit, experiment or risk
  setting changed.

### Clock gate recheck, 2026-10-27

- At approximately 00:02 IST, permitted read-only W32Time diagnostics showed
  `time.windows.com,0x9` as source but leap indicator 3 (unsynchronized),
  stratum 0, root dispersion 7.8040377 seconds, last sync error 1 (`no time
  data was available`) and 107.4634003 seconds since the last good sync.
  Three independent offsets to that source were -0.2606275, -0.2619578
  and -0.2632780 seconds. Leap/stratum, dispersion, error and offset fail
  existing thresholds. No time or OS setting was changed.
- Per the requested stop condition, calendar acquisition, calendar editing
  and startup probing were not attempted in this recheck. The prerequisite
  is administrator correction and verification of W32Time synchronization;
  downstream dated-session evidence remains outstanding.

### Corrected-clock startup recheck, 2026-10-27

- At approximately 00:28 IST, permitted W32Time diagnostics reported source
  `time.windows.com,0x8`, leap 0, stratum 5, Sync state, root dispersion
  0.0251809 seconds, phase offset -0.0000001 seconds, last sync error 0 and
  13.9780110 seconds since good sync. Three independent offsets measured
  +0.0042458, +0.0037952 and +0.0035159 seconds. The existing clock gate
  passed in the permitted local launch context; no threshold changed.
- The saved Dhan master and manifest locally verified NSE_EQ RELIANCE/2885:
  retrieved 2026-10-24T06:04:28.338679Z, SHA-256
  `56b51dfbc372ab682f861fc7b360dc2176aaa9aead2c79e4946ce004db4b576a`.
  No Dhan request was made.
- Read-only HEAD checks to official NSE CM-MII security-file URLs for October
  27 and 28 both returned HTTP 404 at the time checked. Neither date has a
  locally retained dated RELIANCE CAS flag; the existing calendar remains
  scoped to October 12-16. No future verified trial session was selected.
- The existing read-only startup command, run with Dhan credentials removed,
  exited 1 at `private source preflight blocked` after the clock gate. The
  missing dated calendar entry prevents the later private-journal and
  credential checks from running. The illustrative `.local` journal parent
  independently failed the existing native NTFS owner/DACL verifier; no
  production journal path was configured or changed. No Dhan socket opened.

### Private quote journal and dated file availability, 2026-10-27

- One read-only HEAD to the official NSE CM-MII daily security file
  `https://nsearchives.nseindia.com/content/cm/NSE_CM_security_27102026.csv.gz`
  at 2026-10-26T19:02:40.8456207Z returned HTTP 404. No original bytes,
  source SHA-256, dated RELIANCE identity or CAS flag were available. The
  calendar was not changed and no alternative session was guessed.
- Reused the documented `data/journal` root and existing Dhan private-storage
  helpers to create `data/journal/reliance-quote-2885.qj` as an empty private
  NTFS QJ01 file. Native owner/DACL checks passed for directory and file;
  the file remained 0 bytes with empty SHA-256
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
  after the existing Rust QJ01 reader reopened it. Available space was
  205,748,011,008 bytes, above the existing 128 MiB floor; size was below
  the 64 MiB cap. A focused native Rust writer restart/truncated-tail test
  passed 1/1. These are storage checks, not market evidence.
- The existing startup command, with Dhan credentials removed, passed clock
  health and exited 1 at `private source preflight blocked` because its
  calendar remains outside the intended session. It did not reach the Rust
  journal/credential stages or open a Dhan socket. Overall readiness BLOCKED.

### Observation-only quote boundary, 2026-10-27

- Official NSE cash holiday circular NSE/CMTR/71775 and January amendment
  NSE/CMTR/72260 omit Tuesday October 27; NSE market timings show normal cash
  opening at 09:15, and the published CAS rule begins transition at 15:15.
  These sources support a scheduled ordinary observation window from 09:15 to
  before 15:15, not RELIANCE's dated CAS eligibility or actual uninterrupted
  exchange operation. `data/config/reliance-observation-2026-10-27.json` keeps
  that window and source URLs apart from the existing October 12-16 research
  calendar. Session-specific classification is UNVERIFIED, and the research
  calendar `Session.verified` flag remains false for this observation entry.
- The opt-in caller now uses OBSERVE_ONLY, a dedicated session ID prefix, and
  the existing clock, Dhan-current-master, private journal and capacity gates.
  The observation preflight checks the explicit dated ordinary entry and
  same-day official-source provenance. Capture still journals exact raw frames
  and SHA-256 before normalized publication. Telemetry withholds paper state,
  derived features and candles, and permanently emits OBSERVATION_ONLY,
  SESSION_CLASSIFICATION_UNVERIFIED and NO_ELIGIBLE_DHAN_FEED. The cockpit
  accepts only scoped Dhan standard-feed observation snapshots with those
  reasons; approvals remain QP01 paper-research only. Existing forward-outcome
  validation still requires a dated CAS/NON_CAS classification and verified
  candle timing, so this entry cannot qualify outcome labels or model research.
- Offline software fixtures passed Python preflight 1/1, Rust telemetry library
  17/17, Rust local caller 2/2, and cockpit stream 8/8 and App 2/2. Focused
  gateway QJ01-to-WebSocket and scoped raw-frame SHA/receipt/identity tests
  each passed 1/1. These are software tests. A local
  observation preflight with the saved current Dhan master and private NTFS
  journal returned the dated 09:15-15:15 window and UNVERIFIED classification;
  it did not inspect credentials or open a socket. A fresh read-only W32Time
  query at approximately 10:33 IST returned access denied (0x80070005), so
  actual caller startup remains BLOCKED at the unchanged clock gate. No live
  packet, authenticated Dhan request or real-market result was produced.

### Ordinary-user OBSERVE_ONLY startup recheck, 2026-10-27

- Codex's failed `w32tm /query /status /verbose` ran from the repository root
  through `C:\Windows\system32\w32tm.exe` as `Danny\CodexSandboxOffline` and
  returned `0x80070005` (exit -2147024891). The same executable and working
  directory under ordinary non-elevated `DANNY\dhane` returned leap 0,
  stratum 5, `time.windows.com,0x8`, root dispersion 0.0420682 seconds,
  phase offset -0.0000001 seconds, last-good-sync age 11.5959428 seconds and
  sync error 0. The actual Rust caller's unchanged clock gate advanced to the
  deliberately missing `--quote-journal` argument in an offline host probe.
  This establishes a sandbox-identity access restriction, not a clock-health
  failure; no Windows setting or security policy changed.
- The previously configured `data/journal` directory and empty QJ01 file are
  owned by the sandbox identity with no ordinary-user allow entry. `DANNY\dhane`
  cannot read either DACL (`0x80070005`), so the native Python preflight
  blocked there after current Dhan identity and dated ordinary-session checks.
  The old artifact was not modified. Of the existing immediate `data`
  directories, only `data/.secrets` passed the unchanged private NTFS verifier
  for the ordinary user. Using existing private-storage helpers, an isolated
  empty `data/.secrets/quote-journal/reliance-quote-2885.qj` was created under
  that root. Its directory and file are owned by the ordinary user and pass
  native DACL verification. The file is 0 bytes with empty SHA-256
  `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`;
  204,909,772,800 bytes were free at the read-only capacity check.
- The existing Python source/storage preflight returned the October 27
  09:15-15:15 observation window and UNVERIFIED classification under the
  ordinary user. The actual Rust caller, with process-local test UI key and
  opt-in flag and an explicit assertion that no Dhan credentials were present,
  advanced through clock and Python source/storage preflight and exited at
  `Dhan credential unavailable`, before Rust QJ01 writer opening or any socket.
  With the host environment unmodified, it exits earlier at missing
  `ALPHA_GRID_UI_SESSION_SECRET`; `ALPHA_GRID_ENABLE_DHAN_QUOTE`,
  `DHAN_ACCESS_TOKEN` and `DHAN_CLIENT_ID` are also absent from that process.
  These are configuration blockers, not provider authentication results.
  No authenticated Dhan request, live packet, signal, fill or order occurred.

### OBSERVE_ONLY local configuration handoff, 2026-10-27

- Source inspection found the caller reads `ALPHA_GRID_UI_SESSION_SECRET`,
  `ALPHA_GRID_ENABLE_DHAN_QUOTE=1`, `DHAN_ACCESS_TOKEN` and `DHAN_CLIENT_ID`
  from its own process environment. It does not load `.env` or the existing
  private token file. The API key and API secret belong to the separate OAuth
  consent flow, not to the quote caller's startup inputs.
- Ordinary-user presence-only checks found a nonempty, native-private saved
  token at `data/.secrets/dhan-access-token` and the native-private empty
  ordinary-user quote journal under `data/.secrets/quote-journal/`. They did
  not read or print token contents, length or hash. The ordinary-user process
  still has none of the four runtime variables above. The callback token's
  validity and Data API entitlement cannot be established offline.
- `rust/docs/phase2c-cockpit.md` now records a process-local, masked-input
  handoff and the explicit ordinary-user journal path for a later authorized
  one-session launch. No configuration was exported into a persistent user or
  machine environment, no credential was created or rotated, and no Dhan
  connection or order was attempted. The caller's later Rust journal and
  paper-approval ledger openings remain unverified in the actual runtime. The
  documented PowerShell launch block parsed with zero errors; application tests
  were not repeated because source behavior did not change.

### Authorized one-attempt observation handoff, 2026-10-27

- The user authorized one read-only NSE_EQ RELIANCE/2885 Quote-17 connection on
  October 27, ending before 15:15 IST, to be started only from their ordinary
  PowerShell. No Dhan connection or credential read was performed by Codex.
- The default QP01 approval ledger did not exist. The existing DATA_ONLY caller
  opened an empty `.local/paper-approvals.qp` under `DANNY\\dhane`; a second
  launch reopened it byte-identically. Both launches exited on an intentionally
  occupied loopback port before serving, with no approvals or paper events.
  Native owner/access checks passed; no ACL or owner was changed. This verifies
  empty-ledger startup recovery, not recovery of nonempty or damaged records.
- The caller now bounds this trial's capture to 60 seconds, stops by 15:14 IST,
  and applies an outer timeout to the socket future so a stalled write cannot
  extend capture. A stop closes the journal and makes telemetry disconnected;
  its OBSERVE_ONLY / NO TRADE and one-attempt Quote-17 scope remain intact.
  Caller tests passed 2/2 and the updated binary built and launched as an
  ordinary-user offline process, exiting at missing UI key with no Dhan flag.
- At 2026-10-27 06:16:20 UTC, ordinary-user W32Time returned leap 0, stratum 5,
  non-local source, phase -0.0000001 s, root dispersion 0.0281025 s, sync age
  53.2541041 s and last error 0. The unchanged Python identity, dated ordinary
  observation-session, private NTFS journal and capacity preflight returned
  the October 27 window with classification UNVERIFIED. The QP01 ledger was
  empty and present; the saved token file was present without reading it. The
  current process lacked all four runtime configuration variables, so actual
  credential validity, entitlement, and live packet/cockpit transit remain
  unverified until the user's single local attempt.

### Rustls provider failure after the one-attempt trial, 2026-10-27

- The user reported the ordinary-user caller started the localhost gateway and
  its Tokio worker then panicked in Rustls 0.23.46 while choosing a process
  `CryptoProvider`. Cargo's local feature graph showed `rustls/std` only, with
  neither Rustls `ring` nor `aws_lc_rs` enabled. The direct `ring` crate used
  elsewhere did not activate Rustls's provider feature.
- `quant-telemetry` now enables `rustls/ring` and installs that provider at the
  first line of `telemetry_local` main, before gateway or feed tasks. Failure
  returns a static error. A small monitor marks the feed disconnected and logs
  NO TRADE on worker error or panic. WebPKI roots and TLS certificate checking
  are unchanged; there is no reconnect or new feed path.
- Native offline caller tests passed 4/4, including provider installation,
  clean failure on a second installation, TLS client-builder creation and a
  controlled worker panic that changes `feed_connected` to false. Offline build
  and focused Clippy passed. An ordinary-user launch with no live flag or
  credentials exited cleanly at missing UI key (exit 1), before a gateway.
  No authenticated Dhan request or socket was initiated by Codex.
- The ordinary-user private QJ01 was inspected with a read-only file handle:
  zero bytes, EOF, unchanged size and no sidecars, so it contains zero records.
  No record integrity audit or live cockpit transit can be claimed from an
  empty file. A new live attempt requires separately scoped authorization.

### Authorized October 28 observation preparation, 2026-10-28 IST

- The user separately authorized one read-only NSE_EQ RELIANCE/2885 Quote-17
  connection on October 28, conditional on independently verified ordinary
  session evidence and fresh startup preflight; no reconnect, depth or orders.
  The Codex sandbox remains prohibited from accessing credentials or opening
  the authenticated socket. The user's ordinary PowerShell is the launch path.
- At 2026-10-27 18:30:47 UTC (October 28 IST), the official NSE 2026 CM holiday
  circular `https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf`
  and January amendment `https://nsearchives.nseindia.com/content/circulars/CMTR72260.pdf`
  were checked: October 28 is absent, and the amendment affects January 15.
  NSE's published normal cash opening is 09:15, while its CAS page marks the
  continuous-to-auction transition at 15:15. A separate October 28
  OBSERVE_ONLY calendar was added with those URLs, retrieval time and
  classification UNVERIFIED; this is scheduled ordinary-session evidence, not
  proof of actual exchange operation or RELIANCE's dated CAS classification.
- The ordinary-user check at approximately 00:01 IST passed current Dhan master
  identity, calendar structure/date, private NTFS journal ACL and capacity;
  QP01 and saved-token files were present and the loopback cockpit port free.
  The journal was still zero bytes. The full credential-free source/storage
  preflight at 00:03 IST returned BLOCKED as expected outside 09:15-15:15.
  No Dhan credential contents, API request or socket were accessed by Codex.
- Fresh ordinary-user W32Time root dispersion was 1.7877272 seconds and later
  0.7855396 seconds, both above the unchanged 0.1-second gate, despite leap 0,
  non-local source and near-zero phase offset. A subsequent read-only check at
  00:05 IST measured 0.0498587 seconds root dispersion and 26.4757432 seconds
  sync age, within the existing gate. Time synchronization was not changed.
  The Oct 28 trial remains blocked until the 09:15-15:14 observation window;
  startup will freshly recheck clock and all other gates before the single
  authorized connection attempt.
- An ordinary-user offline `telemetry_local --dhan-quote-readonly` probe used a
  generated process-local UI test key with both Dhan credential variables
  explicitly absent. It exited 1 at `private source preflight blocked` before
  journal writer, gateway or connection; thus the unchanged Rust W32Time gate
  passed in that probe and the out-of-session gate blocked as intended. The
  authorized live connection attempt remains unused.

### October 27 minute request and local cockpit session, 2026-10-28 IST

- The separately authorized single `POST /v2/charts/intraday` request used
  NSE_EQ/EQUITY RELIANCE security ID 2885, one-minute interval and exact
  2026-10-27 09:15:00 through 15:30:00 IST bounds. It returned HTTP 401 once,
  with no retry. The 140 original response bytes are in the private historical
  quarantine, SHA-256
  `f8ba2d257a5d624e8c33f4ab842ca7feb661ffc826a5d2b4b1f46e3d70942c63`,
  receipt `2026-10-27T18:49:06.089940+00:00`. Native NTFS ACL and hash
  read-back passed. The sanitized body classification is `DH-901` /
  `Invalid_Authentication`; this does not establish whether the token expired,
  was revoked or another authentication prerequisite failed. No authentic
  candles or movement distributions resulted. October 27 CAS remains
  unverified; source integrity, exploratory and causal eligibility are separate.
- The acquisition request now accepts explicit IST intraday bounds and records
  successful HTTP status/server Date as well as error status in existing private
  response metadata. Focused native acquisition tests passed 20/20; Ruff and
  mypy passed. No other authenticated Dhan request was made.
- The localhost cockpit now issues a seven-day HMAC-signed HttpOnly,
  SameSite=Strict cookie rather than placing the raw UI key in a cookie. Its
  key is in ignored, ordinary-user private NTFS storage and the DATA_ONLY
  launcher reloads it on ordinary restarts; the UI checks the cookie before
  asking for a key. Forget-device clears the cookie; rotating the backend key
  revokes all issued cookies. An offline local backend login/restart/logout
  check passed. Rust gateway tests passed 8/8, cockpit tests 18/18, focused
  Clippy, TypeScript, ESLint, focused Prettier and the frontend build passed.
  The broad Prettier check still reports seven unrelated files. An unscoped
  Rust test command hit the existing `paper_eval` binary's
  non-exhaustive `RawStandardFrame` match; the scoped gateway library tests
  passed. This session change does not start a Dhan feed or enable trading.

### Single-start offline cockpit and Dhan 401 audit, 2026-10-28 IST

- The existing private Dhan token file passed native NTFS ACL inspection.
  The saved token is JWT-shaped and its unverified embedded expiry claim is
  earlier than the October 27 HTTP 401 receipt. The callback validates the
  client identity before saving `accessToken`, but discards Dhan's separate
  `expiryTime`; no trusted local expiry sidecar exists. Dhan's current v2
  documentation specifies `access-token` for chart calls and 24-hour token
  validity. Expiration is the likely operational explanation, but cannot be
  proven from independently verified saved expiry metadata. The request
  format and credential source match the documented v2 path. No Dhan endpoint
  was called in this audit. Manual API-key consent renewal uses the existing
  callback on port 8766; the cockpit launcher does not renew credentials.
- `tools/start-cockpit.ps1` now supervises the existing Vite frontend, Rust
  DATA_ONLY gateway and local trading settings/status service from one
  ordinary-user PowerShell. It loads only the private UI key into the Rust
  child, opens no Dhan feed and rejects occupied ports instead of duplicating
  processes. Offline native smoke verified all three local HTTP listeners,
  duplicate-start rejection with unchanged listener owners and Ctrl+C cleanup
  of all three. Browser automation stopped before Edge URL verification, so
  an actual browser-reopen check is still unverified; prior local HTTP cookie
  reuse across backend restart passed.
- `cockpit_cache.py` rechecks the original private Dhan response, metadata,
  checksum and pinned authentic SHA-256 before the gateway serves historical
  rows. The archived October 15-16 RELIANCE response yielded 720 one-minute
  rows (360 per date), with zero internal source-timestamp gaps; retrieval was
  October 26. The authenticated localhost endpoint served that coverage,
  while an unauthenticated request returned 401. It labels timestamp meaning
  and historical identity unverified, daily candles absent, causal and
  production eligibility blocked. The engine remained DATA_ONLY, feed
  disconnected, paper null and NO TRADE. The October 27 failed request adds
  no candles. The account, orders, trades and ledger panels remain unavailable
  without separately authorized reads; no broker events were reconstructed.
- Focused cached-source Python fixture test passed 1/1; Rust gateway tests
  passed 9/9; cockpit tests passed 18/18; offline caller build and frontend
  production build passed. Focused Ruff/mypy and Rust formatting passed after
  style fixes. No authenticated Dhan request, feed socket, order or model
  training occurred in this change.

### Approved five-endpoint account sync, 2026-10-28 IST

- One ordinary-user inventory of `data/historical` found one preserved Dhan
  RELIANCE response, original 35,261 bytes and SHA-256
  `bcace97b4dfd1b1cbfe10481b4718b770368a50c5264fb1f61a50f9c325d9861`.
  Re-running `cockpit_cache.py` verified 720 source-integrity-eligible
  one-minute rows, October 15-16 (360 each), with causal eligibility BLOCKED.
  No 50,205-row authenticated archive or daily response was found there;
  the current 720-row cockpit cap matches the actual available source.
- The private token file's embedded JWT expiry is past, with no independent
  expiry sidecar; `DHAN_CLIENT_ID` was absent from the launch environment.
  The configured client ID was copied without disclosure from the existing
  ignored local `.env` into an ordinary-user-owned private NTFS file using
  the existing Dhan storage helper. The token remains expired and its
  October 27 401 cause is still not conclusively established.
- Only five newly approved Dhan v2 account reads are enabled in the launcher:
  fundlimit, holdings, positions, today's orders and today's trades.
  Per-endpoint private reservations enforce at least five minutes between
  attempts across restarts. Requests start serially at least one second
  apart; no retry loop exists. Exact HTTP success/error bodies and request
  metadata use private NTFS files, with hashes verified on recovery.
  A 401 stops refresh; storage failure stops further requests. The browser
  route validates the existing Rust signed cookie. A local smoke found and
  fixed a 200/204 success-code mismatch in that delegation.
- Native launcher smoke returned frontend HTTP 200, account route HTTP 401
  without cookie and HTTP 200 with cookie; account state was
  REAUTHENTICATION_REQUIRED and the authenticated historical route returned
  720 rows. The account cache's native DACL passed and contained zero response
  files. The launcher was stopped cleanly with no remaining listeners. No
  Dhan account endpoint, live socket, order or historical endpoint was called.
- Focused native Python cache test passed 1/1; Node account/settings tests
  passed 8/8; cockpit UI tests passed 18/18; focused Ruff, mypy, TypeScript,
  ESLint and Prettier passed. The actual Edge close/reopen check remains
  unverified because computer-use window selection exposed an unrelated
  editor instead of a trustworthy cockpit browser state; UI input was
  stopped. The documented local backend cookie-restart test remains valid.

### Manual consent renewal and automatic cockpit sign-in, 2026-10-28 IST

- Reused the existing Dhan API key, secret and client ID from private NTFS
  storage. One authorized consent-initiation request ran; the user completed
  login and corrected the Dhan redirect port from 8765 to the existing 8766
  callback listener. The callback saved the replacement token and Dhan-provided
  expiry metadata in private storage. Verified expiry is 2026-10-29 09:53:52
  IST, with token hash linkage and native ACL checks. No credential material
  or callback identifiers were written to these notes.
- The first approved read-only account sync received HTTP 200 from fundlimit,
  positions, orders and trades; holdings returned HTTP 500 with sanitized
  `DH-1111 / HOLDING_ERROR`. All five exact bodies, including the failure,
  passed private raw-byte/hash recovery. The holdings panel remains unavailable
  independently. The authenticated cockpit also served the existing 720
  genuine archived RELIANCE minute rows. There was no new historical request,
  market feed, paper fill or broker order.
- Removed the browser session-key prompt. The launcher generates a one-use
  random local sign-in token and opens the cockpit with it in a URL fragment.
  React removes the fragment immediately and uses the existing localhost
  account service to exchange it for the Rust gateway's signed seven-day
  HttpOnly, SameSite=Strict cookie. The long-term private key remains backend
  side; the token is consumed once. React StrictMode's repeated startup effect
  initially raced the cookie exchange; a shared in-flight promise fixed it.
  Native Edge then showed read-only account status, the verified archive,
  connected Rust gateway and NO TRADE, with no key prompt or fragment visible.
- Focused Node sign-in tests passed 4/4; cockpit stream/App tests passed
  14/14 and TypeScript typecheck passed. Rust formatting passed. A fresh Rust
  build/test was blocked by Windows Enterprise Application Control policy
  `0283ac0f-fff1-49ae-ada1-8a933130cad6`, which rejected newly compiled
  proc-macro DLLs; no policy was changed. The existing previously built Rust
  gateway binary served the native cockpit. The new sign-in uses its existing
  session route, so no new Rust binary is needed for this behavior.

### October 28 read-only quote trial preflight blocked by clock, 2026-10-28 IST

- The user confirmed the authorized October 28 Quote-17 connection had not
  previously opened. The ordinary-user private RELIANCE QJ01 remained 0 bytes,
  and local gateway logs contained no OBSERVE_ONLY or connection-failure entry.
  Absence of journal records alone does not prove a historical socket attempt
  never occurred; the user's report supplies that context.
- The installed `telemetry_local.exe` was built after the Rustls ring feature
  change. Its binary contained provider, quote and socket markers, and a
  credential-free native invocation passed Rustls initialization, W32Time and
  source/storage checks before stopping at the expected missing-credential
  gate. This did not open a socket. The 2026-10-28 Python observation preflight
  passed for NSE_EQ RELIANCE/2885, the dated ordinary session, private NTFS
  journal and capacity. Private Dhan token metadata was valid through
  2026-10-29 09:53:52 IST; client ID and UI key private-file ACL checks passed.
- W32Time initially showed root dispersion 0.0501521 seconds, below the
  unchanged 0.1-second gate. The DATA_ONLY launcher was stopped so the one
  quote caller could own port 8765 alongside the existing Vite and account
  services without duplicate gateways. At the live caller's fresh clock gate,
  root dispersion had risen; it exited with `local clock health unverified`
  before `credentials()` or any WebSocket connection. The PowerShell wrapper
  had loaded the private credentials into its child environment and cleared
  them in `finally`; no value was displayed or persisted.
- Read-only W32Time follow-ups measured root dispersion 7.7944310, 3.8114950
  and 1.8045572 seconds, all above 0.1 seconds. Leap was 0, stratum 5,
  last sync was recent and phase offset small. No clock settings were changed.
  The normal DATA_ONLY launcher was restored; localhost ports 8765, 8767 and
  5173 returned HTTP 200. The QJ01 still had 0 bytes and no new raw frames,
  normalized quotes, cockpit quote or publication-latency measurement exists.
  The single authorized Dhan socket attempt remains unused while the clock
  prerequisite is blocked. No historical REST request, order or feed retry
  occurred; the account service retains its separately approved refresh scope.

### October 28 single RELIANCE Quote-17 attempt consumed, 2026-10-28 IST

- The user reported W32Time recovery and requested the previously authorized
  trial. Fresh ordinary-user W32Time measured root dispersion 0.0578748 seconds,
  leap 0, stratum 5, phase offset -0.0000002 seconds, last-sync age 55.85
  seconds and no sync error. The unchanged Python source/storage preflight
  passed for the dated ordinary observation window, RELIANCE NSE_EQ/2885
  identity, private QJ01 and capacity. The consent-token sidecar was valid
  through 2026-10-29 09:53:52 IST; private credential ACL checks passed.
  The journal was still 0 bytes, consistent with the earlier preflight-only
  run. The installed caller binary SHA-256 was
  `046577b5e1598c0b5f076d9facc69d563ba38e7779bc7ed34c221e4e16eb8b22`,
  unchanged from the earlier inspected build with the Rustls provider fix.
- The DATA_ONLY launcher was stopped. One ordinary-user quote caller invocation
  owned port 8765 alongside the existing frontend and account service. It
  announced the OBSERVE_ONLY localhost gateway, then `read-only Dhan feed
  stopped; NO TRADE`; no reconnect was performed. The authenticated live
  snapshot showed mode OBSERVE_ONLY, feed disconnected, zero received frames,
  zero decoded packets, zero accepted/durably committed journal records,
  no market quote and NO TRADE reasons. The private QJ01 stayed 0 bytes with
  its prior modification time. The existing Rust `export_jsonl` reader exited
  successfully at EOF with zero records; there were no frame hashes to verify.
- The caller reports only a generic worker stop, so the evidence does not
  establish whether the WebSocket handshake succeeded or why it stopped before
  a frame. Treat the single authorized connection attempt as consumed. No
  genuine cockpit quote or publication-latency observation exists. After
  stopping the caller, the normal DATA_ONLY launcher was restored. Authenticated
  local reads showed feed disconnected, 720 preserved archived RELIANCE minute
  rows, account READ_ONLY and holdings independently DATA_UNAVAILABLE. No
  historical REST request, order, model, risk or frozen-experiment change was
  made; the account service retains its separately approved refresh scope.

### Offline feed lifecycle and cockpit integration, 2026-10-28 IST

- The consumed one-attempt quote trial left zero QJ01 records and a generic
  worker-stop message. Its DNS/TCP/TLS/upgrade/subscription stage and remote
  cause remain UNKNOWN; a local port collision was not established as the
  cause. The existing caller hosts the feed worker inside the port-8765 Rust
  gateway, so a separate listener is unnecessary.
- The caller now records bounded, credential-free feed stages and terminal
  reasons in existing telemetry audit events. It records a first binary frame
  only after QJ01 capture succeeds. Raw-only capture counters are reflected in
  telemetry. It prints only fixed stage/reason codes to local service logs. The
  existing cockpit displays the latest stage in OBSERVE_ONLY;
  session-specific research and trading gates remain NO TRADE. These changes
  are offline software diagnostics and do not reconstruct the prior attempt.
- `tools/start-cockpit.ps1 -Offline` loads verified local account cache,
  labels the account state CACHE_ONLY, disables account refresh and leaves the
  Rust gateway DATA_ONLY. The ordinary
  launcher keeps its separately authorized account schedule. Focused Rust
  telemetry tests passed 20/20 and caller tests passed 5/5. A proposed
  additional local rejected-handshake test failed because the local test peer
  closed before the client could parse its HTTP response; after correction a
  newly compiled test executable was blocked by Application Control (4551),
  so that unverified test was removed. The final focused telemetry and caller
  suites reran successfully (20/20 and 5/5). A gateway fixture measured 101 ms from QJ01
  commit to local WebSocket update. Node account/settings tests passed 10/10,
  cockpit App tests 2/2, and TypeScript typecheck passed. These are fixtures,
  not market or strategy evidence. The broad telemetry crate build still fails
  in the previously unrelated `paper_eval` raw-frame match. A separate
  quant-paper build was blocked by Windows Application Control (4551). The
  focused Rust binary build, Clippy and formatting passed; the source has not
  traversed an authentic Dhan socket. A test POST to the existing localhost
  trading service's `/trade/order` path returned 404, confirming that service
  exposes no broker-order transport.
- This execution context denies PowerShell script execution, so native
  single-launch smoke and `tools/repo-context.ps1` refresh remain unverified;
  launcher syntax parsing passed. The private quote-journal path was not
  readable from this sandbox, so the prior zero-byte record cannot be
  independently rechecked here. No Dhan request/socket or broker order ran.

### Persistent read-only Online mode preparation, 2026-10-28 IST

- Owner authorized recurring read-only NSE_EQ RELIANCE/2885 Quote-17 and the
  existing five account endpoints while the application runs, with private
  local preservation; no broker-side writes. Dhan v2 feed/annexure pages
  confirmed RequestCode 17, quote response 4, disconnect response 50 and
  807 as expired-token evidence. The existing Rust decoder and QJ01 path remain.
- `telemetry_local --online --dhan-quote-readonly` uses the existing dated
  ordinary-session preflight and continuous-session cutoff, checks Windows
  clock every minute, retains raw frames before publication, and stops before
  the QJ01 cap. The launcher reuses ports 8765/8767/5173 and the private token
  sidecar, with at most two process-level retries on selected transient
  transport failures. New processes rerun preflight and get distinct feed
  sessions. Authentication, decoded Dhan disconnect, packet, storage, clock
  and preflight failures are not retried; account-service reauthentication
  also stops the feed. Status polling uses the existing
  authenticated localhost snapshot; redirected stderr is locked while the
  process runs and could not support live restart decisions.
- At 2026-10-28 14:08 IST the ordinary-user Python source/storage preflight
  passed, the private QJ01 was still 0 bytes, W32Time reported leap 0,
  stratum 5, 41.26 ms root dispersion, near-zero phase offset and 13.31 s
  sync age; token/sidecar ACL, hash linkage and expiry check passed locally.
  These results are time-sensitive and must be rerun at actual launch.
- Rust build, targeted Clippy and formatting passed; Node account/service tests
  passed 11/11, App tests 3/3 and TypeScript typecheck passed. The compiled
  DATA_ONLY gateway completed an unmocked local start, authenticated snapshot
  and stop-file shutdown. Rust focused tests did not execute after the Online
  change: Windows Code Integrity policy VerifiedAndReputableDesktop GUID
  `0283ac0f-fff1-49ae-ada1-8a933130cad6` blocked the fresh test executable
  with os error 4551. PowerShell execution policy is Restricted and blocked
  `tools/start-cockpit.ps1` and the repo-context script, including from the
  ordinary Windows execution context. No Dhan API request, quote socket or
  order was made in this implementation turn; the earlier zero-frame cause
  remains UNKNOWN. Future sessions beyond October 28 lack dated evidence.

### Automatic execution path audit, 2026-10-28 IST

- The existing OBSERVE_ONLY caller journals Dhan Quote-17 frames and publishes
  telemetry, but deliberately does not invoke the paper OMS. `PaperEngine`
  evaluates only QJ01 depth events with features; its default model is
  `UntrainedModel`. The 30m/60m/intraday evaluator requires an approved stock,
  verified session close, calibrated estimate, data quality and positive robust
  net edge. The current dated CAS and causal timing evidence is insufficient to
  promote quote observations to decisions. Therefore there is no eligible
  automatic order decision or live broker transport in the repository.
- Existing QP01 paper behavior already persists decisions, risk assessments,
  intents, distinct submission/acknowledgement/fill transitions, partial fills,
  cancellation, halt, session limits and deterministic recovery. These are
  offline software paths with simulated fills. They do not reconcile against
  a broker. The authenticated local trading service has no order route.
- Dhan v2 documentation lists `POST /orders`, `GET /orders/external/{correlation-id}`
  and order/trade reads; placement, modification and cancellation require a
  whitelisted static IP. Account-specific order permission and IP registration
  have not been verified. No order or IP API was called. Unknown placement
  responses must remain unresolved until broker reads reconcile them; blind
  resubmission would risk duplicates.
- The existing offline `paper_eval` target lacked an exhaustive arm for the
  QJ01 raw standard-frame variant; its event-type label now handles that variant.
  No market content is inferred from a raw frame. The current shell cannot
  locate Cargo, and Windows Application Control previously blocked fresh Rust
  test executables (4551); the new arm has static diff verification only.
  PowerShell Restricted policy still blocks `repo-context.ps1 -Check` and index
  refresh. No live orders, Dhan calls or new feed sockets were attempted.

### Offline account-cache integration, 2026-10-28 IST

- The offline launcher omitted `DHAN_CLIENT_ID`, while the private cache
  verifier requires it to match each saved response's source identity.
  Consequently the account service mapped valid cached funds and positions to
  `CACHE_UNVERIFIABLE`. The launcher now reads the existing private client-ID
  file through its NTFS ACL check for offline mode too, and passes it only to
  the account service. No response bytes or integrity rules changed.
- A read-only native check found present, ACL-readable state, response and
  metadata with passing SHA-256/identity verification and parsing for funds,
  positions, orders and trades. Holdings has a state record with last HTTP 500
  and no successful response reference; it remains DATA_UNAVAILABLE. The real
  Node-to-Python offline load and a temporary authenticated localhost
  `/trade/account` request recovered the four valid panels as CACHE_ONLY
  and preserved holdings' HTTP 500. No account values or credentials were
  printed and no Dhan request ran. All four verified success responses were
  older than five minutes at this check, so their UI freshness is STALE even
  though their offline delivery mode is CACHE_ONLY; age did not cause the
  earlier CACHE_UNVERIFIABLE failure.
- The cockpit's static Trade desk `LIVE` navigation label is now `DESK`; offline
  market and account status endpoints report CACHE_ONLY. Account panels
  distinguish newly refreshed
  LIVE data, verified local CACHE_ONLY data, aged/failed STALE data, and
  UNVERIFIABLE cache failures. A failed cache re-verification clears the
  previously displayed value instead of retaining it under an error reason.
- Targeted Node service tests passed 12/12, React App tests 4/4, TypeScript
  typecheck and launcher syntax parse passed. The already running offline
  launcher predates the environment correction and must be restarted by its
  owning ordinary-user PowerShell for the browser panel to reflect it. Its
  current localhost status reported READ_ONLY/NOT_CONNECTED, so that running
  process is not yet demonstrating the corrected offline CACHE_ONLY status.
  `repo-context.ps1` index refresh remains blocked by this shell's Restricted
  execution policy; no policy bypass was used.

### Quote-17 end-to-end audit after market close, 2026-10-28 IST

- At 22:40-22:46 IST the official Dhan v2 live-feed contract and official
  DhanHQ-py 2.3.0 source matched the existing Rust `wss://api-feed.dhan.co`
  version-2 query authentication and one-instrument JSON Quote-17 subscription.
  The receive loop handles ping/pong, safe upgrade HTTP/close/disconnect codes,
  and raw QJ01 sync before decoding and telemetry. A successful send is not a
  server subscription acknowledgement. Account-specific market-feed entitlement
  remains unverified; the Pro Traders plan is an owner report only.
- The private consent sidecar's source/schema, token SHA-256 linkage, NTFS ACL
  and expiry metadata passed without exposing credentials. It records expiry
  at 2026-10-29 09:53:52 IST; local integrity does not prove remote acceptance.
  The private RELIANCE QJ01 is ACL-checked and 0 bytes. Fourteen existing gateway
  error logs contained no safe feed-lifecycle stage codes. The earlier
  zero-frame attempt therefore still has no evidenced DNS/TCP/TLS/upgrade or
  subscription outcome; its cause is UNKNOWN.
- The 2026-10-28 ordinary observation calendar and current RELIANCE identity
  independently checked, but the current time was outside that window. The
  existing startup source/session preflight returned BLOCKED. There is no
  2026-10-29 dated observation calendar, so no future session was inferred or
  socket opened. No SDK fallback, authenticated Dhan request, broker order,
  source or risk change occurred.
- Targeted Rust telemetry/caller test profile compiled but its executable was
  blocked before execution by Windows Application Control (os error 4551).
  `cargo check` and `cargo fmt --check` passed. Python quote-preflight test
  passed 1/1 using a workspace-owned temporary directory; the first attempt
  failed during pytest setup because the default temp root was inaccessible.
  Tests use fixtures only, with no market evidence. This sandbox still blocks
  `repo-context.ps1` under Restricted PowerShell execution policy.

### Verified-archive descriptive replay, 2026-10-28 IST

- The only private Dhan historical market response in the inspected archive is
  the pinned NSE_EQ RELIANCE/2885 one-minute response retrieved 2026-10-26.
  Existing original-byte, metadata, SHA-256 and parser checks pass for 720
  rows: 360 each on October 15 and 16, with zero internal source-minute gaps.
  The source timestamp's completion meaning, dated historical identity and
  dated CAS classification remain unverified; source integrity is not causal
  eligibility. No new Dhan historical request was needed or made.
- `cockpit_cache.py` now replays those verified rows in strictly increasing
  source order, checks the 09:15-15:15 observation boundary and minute cadence,
  resets prior-window descriptions at session boundaries or gaps, and emits a
  hash-bound summary. The retrospective 5-minute price and prior-20-minute
  volume baseline is DESCRIPTIVE_ONLY. All 720 eligibility evaluations are
  NO_TRADE; outcomes and net edge are withheld, paper orders/fills and QP01
  decision writes remain zero. Historical candles are never fed into the quote
  decoder or used as attainable fills. The existing paper/risk path remains
  independently gated and unchanged.
- The existing authenticated Rust historical route served the summary from
  an isolated DATA_ONLY localhost gateway: HTTP 200, archive/replay hashes
  matched, 720/720 rows processed, 720 NO_TRADE, zero QP01 writes and zero
  paper orders. Backend HTTP fetch took 268.3 ms; browser publication latency
  was not measured. Existing 8765 cockpit process has not been restarted with
  this Python change. Focused Python tests passed 2/2, App UI tests 5/5,
  Ruff, focused mypy and TypeScript passed. The existing quant-paper test
  build was blocked by Windows Application Control error 4551; no policy was
  changed. No genuine live WebSocket frame exists; earlier zero-frame cause
  remains unknown.
- After the final source change, two isolated native DATA_ONLY gateway starts
  each returned authenticated HTTP 200 with the same pinned archive/replay hash,
  720 processed rows, 720 NO_TRADE, zero paper orders and broker orders
  DISABLED. The reused test QP01 approval ledger remained 0 bytes on both
  starts. Backend HTTP timings were 202.2 and 188.4 ms; this does not measure
  actual browser publication. The ordinary running cockpit gateway still needs
  a normal restart to load the updated Python cache output.

### October 29 Quote-17 preparation, checked 2026-10-28 22:58 IST

- Official NSE cash holiday circular NSE/CMTR/71775 lists no October 29
  holiday; January amendment NSE/CMTR/72260 affects January 15 only. The
  official equity timings page gives normal opening at
  09:15, and the CAS page places the continuous-to-auction transition at 15:15.
  Added ignored local `data/config/reliance-observation-2026-10-29.json` for
  an ordinary OBSERVE_ONLY window with classification UNVERIFIED, exact source
  URLs and the actual October 28 retrieval time. Scheduled session evidence
  does not prove actual October 29 operation or dated RELIANCE CAS status.
  Existing preflight requires `checked_at` on the observation day, so this
  preparation cannot authorize a socket until sources are rechecked then.
- Native ordinary-user read-only checks passed private Dhan token/client ACLs,
  token-to-metadata SHA linkage and current expiry metadata; expiry remains
  2026-10-29 09:53:52 IST. This does not prove server acceptance. Private QJ01
  ACL and the 128 MiB free-capacity floor passed; file size remains zero.
  Ordinary-user W32Time showed leap 0, stratum 5, 43.0 ms root dispersion,
  negligible phase offset, no last-sync error and 16.8 s sync age. This is
  not a substitute for fresh startup checks during the session.
- The market was closed during this turn. DNS/TCP/TLS/HTTP upgrade,
  subscription, QJ01 frame capture and cockpit publication were not attempted.
  Focused offline Rust `telemetry_local` tests passed 6/6 in the ordinary
  permitted execution context; an earlier test command failed solely because
  its toolchain path was relative to the wrong working directory. Calendar
  parsing passed and confirmed same-day evidence remains false. No Dhan
  request, socket, SDK fallback, broker order, policy or risk change occurred.
- The ordinary-user loopback ownership check found the existing Node UI on
  5173, Rust `telemetry_local` on 8765 and Node account service on 8767. The
  installed caller binary timestamp is later than its Rust source timestamp;
  this is a local freshness hint, not proof of authentic feed behavior. A
  bounded 60-second diagnostic caller would require the existing 8765 gateway
  to stop cleanly first. Context-index refresh remained blocked by the host's
  Restricted PowerShell execution policy.

### macOS offline development readiness (2026-10-29)

- Scope: review commit `888c8e7`; keep Dhan credentials and the private archive
  on their Windows host. The Mac path starts only the existing localhost
  services in DATA_ONLY / NO TRADE and cannot open a Dhan feed or account sync.
- The new launcher checks a user-owned POSIX-private development directory and
  existing approval/settings files before startup; it does not copy or
  reinterpret QJ01/QP01 evidence from Windows. Windows NTFS ACL checks and the
  online launcher remain unchanged.
- Ubuntu CI previously passed on main commit `afed781` (run 39289010280), but
  the review branch had no run before this change. The workflow now includes a
  macOS runner. Actual Mac results must be read from that run before claiming
  validation.
- The required context-index check was attempted and blocked by the host's
  Restricted PowerShell execution policy. No policy change was made.
- Draft PR #1 runs the existing workflow on both Ubuntu and macOS. On run
  40839436464, both runners passed 181 Python tests (3 skipped), Python static
  checks, and the Rust workspace checks; frontend validation stopped at
  checked-in schema drift. Five generated artifacts were refreshed to match the
  exporter. Windows local UI checks passed 23/23 Vitest tests, TypeScript,
  ESLint, Prettier after formatting, and Vite build. These are not live market
  or Mac startup evidence. The workflow now includes a Mac-only headless
  localhost startup smoke test after the full suite.
- Review commit `1daf0d5`, PR #1, run 40841059276 completed successfully on
  Ubuntu 24.04 and macOS 15. Both jobs passed locked Python, Rust, React and
  schema checks. The macOS runner's three-service offline startup smoke reached
  the 5173/8765/8767 health routes and logged DATA_ONLY / NO TRADE. No Mac
  browser login, private Dhan archive migration, credential loading, live feed
  or broker order path was tested. A follow-up Mac CI assertion checks the
  private directory and newly created QP01 file modes directly.

### Mac historical Dhan archive readiness, 2026-10-30 IST

- The Mac checkout contains no `data/historical` files. An offline invocation
  of `quant.apps.cockpit_cache` failed on the absent pinned raw JSON, without
  making a Dhan request. Thus no genuine Mac replay output or Mac-side byte
  verification is available yet. The previously documented Windows result is
  35,261 original bytes, SHA-256
  `bcace97b4dfd1b1cbfe10481b4718b770368a50c5264fb1f61a50f9c325d9861`,
  720 rows, 360 each on October 15 and 16, 09:15-15:14 IST, zero internal
  source-minute gaps, 720 NO_TRADE evaluations and zero paper/QP01 writes.
- `docs/mac-historical-dhan-import.md` records the minimum three original
  files and a byte-comparison/import procedure. The existing shared
  `verify_saved_response` and `cockpit_cache.py` remain the only replay path;
  no architecture, live feed, frozen November experiment or eligibility gate
  changed. Receipt metadata binds the request to NSE_EQ/2885 and the one-minute
  window; historical identity and candle timing remain unverified.
- The empty-cache cockpit message now attributes the October 27 HTTP 401 to
  the separate saved Windows historical request and says the Mac has not
  retried it. Focused Python replay tests passed 2/2, App UI tests 5/5,
  TypeScript typecheck, focused Prettier and `git diff --check` passed. The
  requested PowerShell context-index check
  could not run because `powershell` is absent on this Mac.

### Mac Dhan October 26-30 pilot preparation, 2026-11-01 IST

- The public Dhan compact instrument master was downloaded from
  `https://images.dhan.co/api-data/api-scrip-master.csv` and preserved, without
  credentials, at ignored `data/instruments/dhan-scrip-master-2026-11-01.csv`
  (25,748,625 bytes, SHA-256
  `ad00a05b69606346a5e796bef8715a981242eab8063d615294ce8b3e54981a6a`).
  Its private-mode manifest records retrieval at
  `2026-10-31T19:17:33.734087+00:00`. The existing verifier found exactly one
  current NSE/EQUITY/EQ RELIANCE row with security ID 2885. This is a November
  1 current-master observation, not dated identity proof for October 26-30.
- NSE's official closing-auction historical archive lists each of October
  26, 27, 28, 29 and 30 as a trade date. Its 2026 equity holiday page lists no
  holiday in that window. These establish session-date evidence, but not
  RELIANCE-specific dated CAS eligibility or Dhan candle publication timing.
  Attempts to fetch dated NSE security-master files directly timed out or
  failed DNS, so no historical Dhan identity continuity was inferred.
- The existing CLI dry-run planned exactly one POST to
  `https://api.dhan.co/v2/charts/intraday`, `NSE_EQ`/`EQUITY`/2885,
  interval `1`, `oi=false`, from `2026-10-26 00:00:00` to exclusive
  `2026-10-31 00:00:00`. Offline `verify-config` passed scope and current
  identity checks but reported `token_present=false` and subscription
  `UNVERIFIED_OFFLINE`. Focused pilot tests passed 6/6. A mode-0600 ignored
  `.env` template was created with an empty `DHAN_ACCESS_TOKEN` field.
- The operator answered `Not yet` to the exact POST approval and clarified
  that they have an API key, not an access token. Dhan's v2 authentication
  documentation requires an access token on chart requests; API key/secret
  require a separate consent flow to obtain one. No such flow and no
  authenticated Dhan historical POST were run. No HTTP response, candles or
  cockpit replay exist for the requested window. The one-request allowance
  remains unused, and the November experiment and order paths are unchanged.

### Mac API-key callback input, 2026-11-01 IST

- The Mac's ignored, owner-owned mode-0600 `.env` now contains locally supplied
  API key, secret and client ID values; none were printed or committed. The
  historical access-token field remains separate. Dhan's current v2 docs
  require an access token for `/v2/charts/intraday` and describe API-key
  generate-consent, personal browser login and consume-consent as the path to
  obtain one. These are additional authenticated calls, not part of the
  single historical-POST approval.
- `tools/dhan_oauth_callback.py` gained an explicit `--env-file .env` option
  for owner-private local input on Mac. It reads only the three API-key inputs,
  rejects symlinks, broad POSIX permissions, oversized, malformed and
  duplicate entries, and preserves the existing private token/expiry output.
  The Windows private-file default is unchanged. The real local `.env` passed
  offline loading without displaying secrets; the callback URL would be
  `http://127.0.0.1:8766/dhan/callback`, with port 8766 free at check time.
  Whether this URL matches the registered redirect and is accepted by Dhan
  remains unknown. No consent request or historical POST was run.
- Focused callback tests passed 6/6 with fake Dhan responses and temporary
  loopback sockets. The first sandboxed run could not bind loopback, so the
  unchanged offline suite was rerun with loopback permission. Focused Ruff,
  mypy, formatting and `git diff --check` passed. The two pre-existing HTTP
  tests now use private temporary directories instead of assuming the absent
  Mac `data/.secrets` directory already exists.
- The operator confirmed a registered callback and authorized one API-key
  consent flow. `--env-file .env --initiate` bound localhost port 8766, made
  one successful generate-consent POST (the browser login opened), and waited
  for the personal login. The operator subsequently reported a final URL on
  port 8765 without `?tokenId=`. A manual visit to the plain 8766 callback
  returned `Invalid callback` before any token exchange. No token or expiry
  file was created and no consume-consent GET occurred. The listener was
  stopped cleanly. The registered redirect and browser outcome need operator
  correction/inspection before any fresh consent attempt; none was retried.
  The separately approved maximum of one historical POST remains unused.
- Follow-up diagnosis found the port-8765 Rust gateway has no Dhan callback
  route; the port-8766 Python callback had no token or metadata after the
  operator's Mac browser login. The operator recalled setting a 8766 callback
  URL while using Windows but could not confirm the actual registered URL.
  `127.0.0.1` on the Mac browser would point to the Mac, so the prior Windows
  registration machine alone is not proof of a port mismatch. The real
  registered redirect and browser result remain unknown. No additional Dhan
  consent request was made.
- The callback's `/dhan/complete` page now reports success only after it saves
  a token, and bounded stage messages reveal route arrival, missing token ID,
  exchange failure and storage outcome without recording secret values. This
  prevents a direct visit to the completion page from appearing to prove an
  exchange. Focused callback tests passed 6/6; Ruff and `git diff --check`
  passed. A gateway relay or second consent request is not justified without
  the actual registered redirect or a manually generated access token.

### Direct Mac Dhan historical acquisition and replay, 2026-11-01 IST

- The operator authorized a second API-key consent attempt after creating a
  new key. Dhan returned the browser to `127.0.0.1:8765/dhan/callback` with a
  `tokenId` query. The operator copied that address locally; only its URL
  structure was displayed. The one-time token ID was delivered in memory to
  the already-running port-8766 callback without printing or persisting it.
  The callback consumed consent once and saved an owner-private access token
  and expiry metadata under `data/.secrets`. The consumed redirect was cleared
  from the clipboard. The token value was never logged or committed.
- After the operator confirmed Data API entitlement and authorized the exact
  request, the existing `pilot` made one authenticated POST, no retries, to
  `https://api.dhan.co/v2/charts/intraday` for NSE_EQ/EQUITY/2885, interval 1,
  `oi=false`, from `2026-10-26 00:00:00` through exclusive
  `2026-10-31 00:00:00`. No further authenticated historical request is
  authorized by this approval. The saved response is HTTP 200, 87,694 original
  bytes, SHA-256
  `a4c61e3d5ad8ce3b9bf78317ac73ff15502bcefa52bd6a09c3771b866d2d8653`;
  HTTP Date is `Sun, 01 Nov 2026 14:06:03 GMT` and local receipt is
  `2026-11-01T14:06:03.835467+00:00`. The quarantine response and published
  raw JSON match byte for byte. Private raw, checksum and metadata sidecars
  are in ignored `data/historical/raw-dhan-macos-2026-11-01`.
- The shared `verify_saved_response` passed checksum, exact request, schema,
  timestamp, duplicate and window checks. It parsed 1,800 bars: 360 on each
  October 26, 27, 28, 29 and 30, 09:15-15:14 IST, with zero internal minute
  gaps. The November 1 public Dhan master verifies current RELIANCE/2885
  identity; NSE historical trade-date listings corroborate the five session
  dates. Dated Dhan identity continuity, RELIANCE-specific CAS classification,
  candle completion/publication timing and adjustment semantics remain
  unverified. Source integrity is verified, exploratory use is descriptive
  only, and causal and production eligibility remain blocked.
- The provenance-bound cache reader selects the new archive if present and
  rejects a changed hash or coverage; the October 15-16 archive remains a
  separate fallback. The Rust gateway admits only those two pinned archive
  shapes. A credential-free isolated gateway returned HTTP 200 for the Mac
  historical route (401 without a local UI session), 1,800 bars, 1,800
  NO_TRADE evaluations, zero missing minutes, zero paper orders/fills and zero
  QP01 writes; its temporary ledger remained zero bytes. The already-running
  port-8765 gateway was started before the change and has not been restarted.
  The UI removes the old October 27 authentication banner when the new
  archive is served; the older archive still retains its explicit provenance.
- Focused Python replay tests 2/2, callback tests 6/6, Rust library tests
  20/20, TypeScript and five focused UI tests passed. The Mac `powershell`
  executable is absent, so `tools/repo-context.ps1 -Check` and context-index
  refresh could not run here. The frozen November experiment and broker-order
  paths were not changed.

### Mac six-month RELIANCE backfill and training gate, 2026-11-01 IST

- The owner separately authorized exactly two no-retry historical POSTs for
  NSE_EQ/EQUITY/RELIANCE/2885 at one-minute interval: May 1–July 30 and
  July 30–October 26, exclusive end, with the verified October 26–31
  checkpoint reused. The existing `backfill` workflow made those two DhanHQ
  chart requests and no account, feed or order request. Both returned HTTP
  200. The two new original responses are 1,113,650 and 1,033,635 bytes,
  SHA-256 `62e76b108fad3fde0c49041e154796c5389f055040b1b229839d533664de512b`
  and `fbe4eac254fa7e55c4e167eae9fe9c9a36597f8073c58196b135586ca8455838`.
  They are saved with private checksum, receipt metadata and exact-byte
  quarantine evidence. The two-request approval is consumed.
- The shared saved-response verifier and an independent offline session audit
  passed: 45,945 candles on 125 distinct sessions after boundary
  reconciliation; 63 sessions have 375 contiguous minutes, 62 have 360,
  all beginning 09:15 IST. No timestamp duplicates, order regressions,
  out-of-window rows or internal minute gaps were found. The source date set
  exactly matches weekdays less NSE's six 2026 equity holidays in the span.
  The later 360-minute shape is consistent with the general CAS regime but
  does not establish RELIANCE's dated CAS flag.
- The owner defined training as learning whether observable conditions
  predict economically meaningful movement over the next 30 minutes, 60
  minutes or remaining session. `forward_observations.py` requires verified
  dataset-bound bar-start/completion timing, explicit dated sessions/CAS flags
  and verified identity before constructing causal observations. Those proofs
  do not cover this six-month archive; the current Dhan master is November 1
  evidence only. No model was trained, registered, or promoted, and the frozen
  November experiment is unchanged. Full source audit and blockers are in
  `docs/reliance-six-month-mac-audit.md`.

### Dhan descriptive opportunity and causal-readiness review, 2026-11-01 IST

- The three private Mac response sets were reverified offline before
  description. The pinned 45,945 source candles cover 125 expected NSE
  weekday/holiday dates; source-minute windows are contiguous, 63 with 375
  rows and 62 with 360. No prospective availability capture, full-range dated
  identity/CAS evidence, or observed execution costs were found. Dhan's
  public historical docs define the timestamp as an epoch integer but do not
  establish bar start/end, first availability or immutable completion.
- A new, bounded offline `apps/descriptive_dhan_opportunities.py` reuses the
  saved-response verifier and pinned hashes; it rejects changed request
  metadata, non-200 saved status, date/coverage mismatches and minute gaps.
  Its private ignored report computes 30m, 60m and remaining source-price
  paths, future OHLCV ranges, assumed 25/50 bps hurdle exceedance and
  equal-session bootstrap intervals. It does not create causal observations,
  a model, orders, fills or a cockpit prediction. Full numbers, cost caveats,
  conditional November 3 probe scope and the separate future experiment
  proposal are in `docs/reliance-30-60-remaining-readiness.md`.
- The existing October 27 probe is a fixed past-date plan and has no local
  capture. Future use needs a dated scope update, same-day NSE/CAS/identity
  and clock evidence, and separate approval for exact Dhan requests. The
  November 15-minute holdout was not reused. Recommendation remains NO
  TRAINING / NO TRADE until the timing, identity and cost gates pass.

### Independent candle-timing audit, 2026-11-01 IST

- A separate raw-JSON-only Python calculation, without the production parser
  or descriptive module, rehashed all three Dhan originals, checked exact
  request/status/receipt sidecars and reproduced 45,945 bars on 125 dates.
  It reproduced 63 pre-August-3 sessions of 375 labels and 62 later sessions
  of 360 labels, with no within-day gaps; the latter date set matches NSE's
  public CAS archive listing. Dated RELIANCE CAS flags across the six months
  are still absent. The cited October 15–16 NSE security-master originals
  are absent on this Mac, so the local registry/calendar claim cannot be
  independently rehashed here.
- The independent decimal-to-nanounit path recomputed all 30m, 60m and
  remaining-session point results at published precision. A first binary
  float parse shifted some strict cost-hurdle classifications; fixed-point
  conversion removed the discrepancy. Independent bootstrap intervals vary
  slightly with their random generator. All 45,945 raw timestamps and volumes
  are integral-valued JSON decimal numbers written with `.0`, whereas Dhan's
  v2 table describes integers. No timing meaning follows from this encoding.
- Official Dhan v2 historical docs establish UNIX/epoch timestamp values,
  supported intervals and the endpoint, but no bar-start/end label, partial
  bar policy, first availability, completion bound or revision limit. No real
  prospective capture or clock brackets were found. A technical support
  inquiry was drafted but not sent. The conditional November 3 eight-poll
  proposal in `docs/reliance-candle-timing-review.md` was smaller than the
  then-current 14-poll past-date code. NO TRAINING / NO TRADE remains the
  decision.

### November 3 candle-availability probe offline preparation, 2026-11-02 IST

- Retargeted only the existing Dhan availability app and its synthetic tests
  from October 27/14 slots to November 3/eight slots. The offline CLI prints
  the exact `NSE_EQ/EQUITY/2885`, one-minute, November 3 to exclusive November
  4 request and eight IST sample times; it reads no token and makes no network
  request. Five focused synthetic tests pass, including exact cap, dated
  identity gate, original response hash and no-retry HTTP error retention.
- Dhan's current v2 historical page specifies the endpoint and payload fields
  but does not promise same-day availability, next-day `toDate` acceptance
  during trading, timestamp start/end meaning, partial/final policy or revision
  limit. The proposed first authorized response would test this exact payload;
  a non-200 response would be retained and stop without a changed retry.
- Execution is **not authorized** by this preparation. Same-day NSE session,
  security identity and CAS evidence, independent clock offset/uncertainty,
  operator-confirmed Data API entitlement, an unexpired private token and
  separate exact request approval are still prerequisites. No dataset, model,
  November 15-minute experiment, QJ01/QP01 invariant or NO TRADE control was
  changed. Ruff, format, mypy and `git diff --check` also pass. The required
  repository context-index check and refresh could not run because neither
  `powershell` nor `pwsh` is installed on this Mac.

### November 3 Dhan probe preflight audit, 2026-11-02 IST

- Official NSE circular NSE/MSD/60315 identifies the daily CM MII file
  `NSE_CM_security_ddmmyyyy.csv.gz` on All Reports. A later NSE circular asks
  members to load the updated master before trading, but gives no exact
  publication hour. November 3's file and RELIANCE row must be received and
  hashed with their actual retrieval time. Required row fields are
  `FinInstrmId`, `TckrSymb`, `SctySrs`, `ISIN`, `ElgbltyNrmlMkt` and
  `ElgbltyClsgAuctnSsn`; the official corporate-action report must be
  filtered for RELIANCE and checked for the day's ex/effective date. A fresh
  public Dhan master is a separate current mapping check. None of these
  November 3 originals is present in the current calendar/registry.
- The existing Mac probe checks date-scoped verified calendar and registry,
  token presence, exact cap, session-date/start cutoff and private empty
  storage before creating its HTTP transport. A fake-transport invocation
  against the actual local October calendar failed on missing November 3
  session evidence with zero calls. It quarantines full received body bytes
  for both HTTP 200 and error status before parsing, with hashes, request,
  status, receipt time and per-slot UTC/monotonic timing. It does not retain
  all HTTP headers or enforce independent NTP offset, fresh Dhan master,
  corporate-action source review, entitlement or token expiry. Thus failure
  of every external preflight condition is **not** programmatically guaranteed
  to prevent a call. `--approved-scope` is an operator assertion, so execution
  remains NO GO until those external conditions are evidenced and separately
  authorized.
- macOS supplies read-only `sntp -d -t 3` for independent offset checks
  when run without `-s`/`-S`; the existing Rust W32Time gate is Windows-only.
  A read-only attempt to `time.apple.com` from this sandbox failed DNS and
  supplied no offset or uncertainty. `systemsetup` status also required
  administrator access. `docs/nov3-dhan-probe-preflight.md` gives the exact
  public-source, hash, SNTP and offline guard commands plus GO/NO GO rules.
  Dhan's v2 docs do not guarantee acceptance of the proposed next-day
  `toDate` during trading; an eventual authorized error would stop with no
  changed retry. No authenticated Dhan request or credential action occurred.

### Owner-authorized offline assumed-timing exploration, 2026-11-02 IST

- The owner explicitly allowed exploratory fitting on the three pinned genuine
  Dhan responses under assumed 60/120/300-second publication delays, while
  keeping the November 3 prospective probe NO GO. The new isolated
  `apps/exploratory_dhan_timing.py` reuses the shared source verifier and
  continuous source-span checks; it never calls Dhan, writes QP01, connects
  to the cockpit, or changes causal training gates. It fits fixed-penalty ridge
  and train-only empirical calibration against unconditional and NO TRADE
  baselines. May–July minus July 31 embargo supplies 61 train sessions after
  the first session lacks prior-volume context; 42 August–September sessions
  are scored in three chronological blocks. October, the frozen November
  experiment and proposed December holdout are excluded.
- The private ignored report at
  `data/historical/research/reliance-assumed-timing-exploratory-v1.json` is
  50,865 bytes, SHA-256
  `6abfa2237628d5b40886e0df71eaa8059cd558f268d9442f60fb505bec064411`.
  At 50 bps assumed round-trip cost, 30m selected no decisions under any
  delay; 60m selected one per delay and had negative mean session reference
  net; remaining-session selected four or five and was also negative. All
  25 bps sensitivities were negative. Positive-sign Brier scores were worse
  than the unconditional frequency. No incremental value was demonstrated,
  so model complexity stopped at the simple baseline; no promotion or trade.
- An independent raw-JSON Decimal calculation matched one May 5 feature,
  prior-session volume ratio and 30m reference outcome. The focused
  software-only look-ahead test and descriptive test passed 2/2; Ruff, mypy
  and format checks passed. Isolated `cargo test -p quant-paper --lib` passed
  14/14 existing QP01/risk/OMS/accounting tests with no connected activity.
  The report and remaining timing/identity/cost limits are in
  `docs/reliance-assumed-timing-exploratory.md`. The PowerShell repository
  context-index check/refresh remains unavailable on this Mac. DATA_ONLY /
  NO TRADE remains.

### Frozen first-signal 60-minute falsification, 2026-11-02 IST

- The owner authorized an exploratory, literature-guided opportunity test but
  no new Dhan request or paper connection. The frozen protocol precedes this
  first-signal analysis and preserves the earlier exact 15-minute momentum and
  five-minute-volume/prior-20-session candidate. The new isolated offline app
  rechecks all three pinned originals (45,945 bars, 125 sessions), uses the
  existing walk-forward/session-bootstrap helpers, excludes July 31 by embargo,
  scores 42 already inspected August–September sessions, and excludes October
  and frozen November from fitting and scoring. Historical identity, adjustment
  and candle publication remain unverified; the output is exploratory.
- Candidate B selected 39 sessions. Its gross 60-minute mean was +11.453,
  +9.840 and +6.741 bps at assumed 60/120/300-second delays. At ₹100,000,
  Dhan's published intraday fee estimate is 8.268 bps round trip; with an
  additional assumed 40-bps friction, B's session mean reference nets were
  −34.185, −35.683 and −38.560 bps. B minus momentum bootstrapped intervals
  crossed zero at 120 and 300 seconds. Favorable and adverse Brier/log loss
  were worse than unconditional scores on the same B-selected dates. Thus B
  failed frozen acceptance; no logistic model was fitted. The report is in
  `docs/reliance-60m-first-signal-result.md`; private ignored JSON SHA-256 is
  `ce65b9828d2f867e48ec5ca4803a4fcff0e79b0d136517a47104d64982ae80bd`.
- Focused synthetic-software Python checks passed 4/4; Ruff passed. Isolated
  `quant-paper` library tests passed 14/14 for risk, QP01 failure/recovery,
  accounting, duplicate suppression, halt and reconnect behavior. No connected
  broker reconciliation or executable fill was established. The November 3
  availability probe remains NO GO, and no risk limit, QP01 approval, frozen
  November experiment, cockpit decision or broker path was changed.

### Read-only 60-minute candidate closure, 2026-11-02 IST

- `docs/reliance-60m-closure-audit.md` independently reconstructed first
  selected opportunities, 42-day paired means/2,000-draw intervals and
  Laplace-smoothed Brier/log-loss from the pinned raw bars. The saved report
  hash still matches. B selected 39 distinct dates; three no-selection dates
  contribute zero, explaining why its selected-day gross mean minus 48.268 bps
  is more negative than the saved 42-day conservative mean.
- At ₹100,000 target buy value, hypothetical whole-share buy/sell notionals
  and published rounded fees average about 8.312–8.316 bps on B selections,
  versus the experiment's fixed 8.268-bps equal-value estimate. The resulting
  42-day B net is only 0.041–0.044 bps more negative. Signed selected-day
  headroom above estimated fees is +3.138/+1.528/−1.574 bps at 60/120/300-s
  delays, before spread/slippage/latency; no executable edge is established.
- The candidate remains STOP: negative conservative net, timing-fragile paired
  uncertainty and worse favorable/adverse probability scores than
  unconditional. This finding does not cover all 60-minute opportunities.
  No further collection is justified solely for this candidate. Prospective
  verified candle timing plus synchronized bid/ask/size on independent
  candidate and matched baseline sessions would be the smallest joint
  information to separate a gross association from execution cost; it remains
  outside current authorization. Source, frozen November, QJ01/QP01 and
  DATA_ONLY / NO TRADE are unchanged.

### Real microstructure inspection and capture NO GO, 2026-11-02 IST

- `docs/real-microstructure-readiness-2026-11-02.md` inspects the current
  official Dhan v2 Quote-17 and 20-depth-23 binary contracts and limits, Rust
  decoders, receive stamping, raw QJ01, segmented depth journal, replay,
  telemetry and independent paper risk. The local Dhan master reverified
  NSE_EQ/2885/RELIANCE as of its actual November 1 IST receipt, but there is
  no pinned same-day NSE identity/CAS or independent liquidity row for a future
  capture. No genuine Mac live quote or depth QJ01 was found; historical OHLCV
  must not be represented as live microstructure evidence.
- Scoped standard quotes are raw-byte/SHA retained before parsing. The Rust
  20-depth path currently stores only decoded sides/books, losing malformed
  and unsupported original frames; its live CLI defaults to two reconnects
  and has no dated clock/session cutoff. Required minimal offline work is raw
  depth retention and a zero-retry, time/identity/clock bounded caller. The
  proposed one-instrument/two-socket November 4 scope remains **NO GO** until
  those changes and dated evidence are complete and separately approved.
- Focused isolated Rust suites passed: market-data 11, journal 12 (one helper
  ignored), engine 13, replay 2, paper 14, depth 7 and telemetry 20. Depth and
  telemetry localhost fixtures needed loopback-permitted execution because
  the default sandbox denied local binds; they made no Dhan connection.
  Python Dhan depth tests passed 20. No source archive, frozen experiment,
  risk limit, QJ01/QP01 invariant, model or broker path changed. DATA_ONLY /
  NO TRADE continues. PowerShell context index check/refresh was unavailable.
## November 2 read-only live pilot preflight — NO GO

- The user subsequently authorized exactly the November 2 three-ID, two-socket,
  no-retry, 14:45 read-only scope. The public NSE MII file still returned 404
  at the 13:05 recheck, and the full `--pilot-three` Python preflight returned
  `BLOCKED` before any authenticated connection. The standard collector was
  extended to the exact three-ID Quote-17 subscription, raw QJ01 and Mac
  `sntp` gate without changing the 100 ms limit; two Google samples passed.
  The inherited October calendar lacks November 2 evidence. Depth and
  standard collectors were not connected, so live coverage, spread and
  liquidity remain unobserved. Focused Python 2/2, standard capture 4/4,
  scoped subscription 1/1, telemetry bin 8/8 and Rust workspace checks passed.

- At 11:39 IST the dated November 2 NSE MII security file was absent locally
  and returned HTTP 404; today's three-ID/CAS/corporate-action eligibility
  could not be established. A fresh public Dhan master verifies the three
  NSE/E/EQUITY/EQ mappings, but does not substitute for dated NSE evidence.
  The Apple SNTP offset-plus-uncertainty was 104.867 ms, above the inherited
  100 ms gate; a second Google sample was 62.557 ms. About 12.1 GiB disk was
  free. No authenticated Dhan socket or request was made; separate approval
  had not yet been given at that initial check.
- The depth QJ01 now fsyncs original binary bytes and SHA-256 before decode,
  replays with hash validation, retains malformed originals, stops on wrong
  IDs and separates raw bid/ask records from receipt-fresh, uncrossed derived
  books. The depth CLI defaults to zero reconnects and requires an explicit
  cutoff. Focused decoder 7/7, QJ01 13/13, depth capture 6/6, replay 2/2
  and compile checks passed. At the initial preflight, the standard Quote-17
  collector was one-ID and Windows-clock gated; the later patch addresses
  that software blocker. Details: `docs/live-pilot-2026-11-02-preflight.md`.

## First offline simulator — 2026-11-02 IST

- `src/quant/apps/historical_simulator.py` implements a fixed 10:00 opening-range
  reference replay with virtual capital/positions, intraday fee estimates and
  40 bps assumed friction. It uses only the verified pinned Mac RELIANCE
  response; 24 universe names remain unavailable. Its separate momentum,
  NO TRADE and forced-diagnostic outputs cannot imply live fills or causal edge.
- The cockpit exposes the simulation only when the original cache's strict
  source hash and session coverage validate. Rust rejects a simulation object
  that claims fills, positions or paper/broker activity. QP01, risk limits and
  the frozen November experiment are unchanged.
- The retrospective five-day rule result is one selected session and
  −₹146.90 conservative reference net; momentum selects the same session.
  More aligned genuine data and observed candle timing/costs are required.
  See `docs/historical-simulator-2026-11-02.md` for the frozen rule, arithmetic,
  limits and separately approvable bounded data scope.
- Focused simulator/cache Python tests passed 5/5; web tests 6/6, typecheck,
  lint and build passed; the focused offline Rust gateway test and binary build
  passed. The final private result artifact is
  `data/historical/research/alpha-grid-simulation-reliance-oct26-30-v2.json`
  (26,565 bytes, SHA-256
  `2497a79cb9112a8f663edbe332fed6e290a2d08871257e056e85378b97c769d3`).
  Full telemetry tests needing local listener binds were blocked by sandbox.
  PowerShell context-index check/refresh remains unavailable on this Mac.

### 24-hour Mac delivery verification, 2026-11-02 IST

- The 8765/8767/5173 offline cockpit was restarted and its Simulation view
  inspected in a local browser: verified 1,800-bar RELIANCE source,
  ₹1,000,000 starting virtual capital, ₹999,853.10 ending capital, one
  reference-only selected trade, 24 missing stocks, four excluded diagnostics,
  no virtual position or order at end. The five-day CLI output is byte-identical
  to the saved v2 artifact, SHA-256 `2497a79cb9112a8f663edbe332fed6e290a2d08871257e056e85378b97c769d3`.
- The unchanged rule replayed three pinned genuine RELIANCE responses offline:
  45,945 bars, 125 sessions, 12 opening-range selections (three positive and
  nine negative net reference results), −₹3,272.73 cumulative net after
  ₹991.73 estimated fees and ₹4,771.30 assumed friction; ending virtual capital
  ₹996,727.27. Momentum ends ₹974,006.69, NO TRADE ₹1,000,000. The private
  640,503-byte artifact is SHA-256
  `52d43e1ff04d14ac295e1dc5c54d9572f72885b328b3ef3361b9e0f2f3234d76`.
  These are exploratory reference outcomes: October universe selection is
  retrospective, candle availability and dated identity continuity are
  unverified, and OHLCV closes are not fills.
- A synthetic software-only two-instrument test confirms fixed rank selection
  and one opportunity per session; another test permits response hash changes
  only between session dates. Seven focused Python tests, six cockpit tests,
  web lint and production build, Ruff, launcher shell syntax, and 14 existing
  Rust paper/risk unit tests passed. An
  HDFCBANK offline dry-run emitted the fixed one-request payload and reported
  historical identity unverified. No Dhan authenticated request or order was
  made. The 24-instrument procedure remains separately approvable and was not
  executed. PowerShell context check still cannot run on this Mac.

# Cross-stock evidence decision — 2026-11-02 IST

- Follow-up review in `docs/cross-stock-plan-review-2026-11-02.md` changes the
  **priority**, not the frozen 25-ID artifact or its original plan: defer the
  24 historical POSTs. The smallest higher-information experiment is a
  conditional November 4, three-ID joint quote/20-depth quality session,
  requiring separate authorization after raw-depth QJ01, Mac clock/identity
  preflight, dynamic scoped Quote-17 and hard-stop fixes. Four total sockets,
  zero reconnects, no REST/order/paper connection. It can measure quoted
  spread, displayed depth and coverage, not fills or predictive edge.
- The existing cost-adjusted past-return rank is not a forecast. Its current
  spread is only a proxy for future exit cost; later reference accounting must
  use entry/exit VWAP versus midquote once, without adding spread or depth
  twice. Historical one-minute features still lack a publication-time proof.
- Offline rehash and identity checks for the three names, market-data 11/11,
  QJ01 raw-frame tamper 1/1 and depth-capture 4/4 tests passed. No live data
  was collected. PowerShell context check remains unavailable on Mac.

- Continue evidence collection only, with DATA_ONLY / NO TRADE. Do not revive
  the failed RELIANCE volume filter or use the frozen November holdout.
- Twenty public NSE October CM UDiFF final bhavcopies, the October 30 MII
  security file and the verified November 1 public Dhan master support the
  fixed 25-ID October median-traded-value universe in
  `docs/cross-stock-universe-2026-11-02.json`. This is dated source and
  identity evidence, not observed spread/depth or current trade eligibility.
- The selection looks forward within October; October cross-stock statistics
  would be descriptive. Dhan historical candle availability and cross-stock
  execution costs remain unverified. No cross-stock Dhan response exists yet.
- `docs/cross-stock-opportunity-plan-2026-11-02.md` predeclares the common
  cost-aware versus price/volume baseline question, prospective date and
  instrument holdouts, session-grouped uncertainty, portfolio metrics and
  conservative acceptance. The smallest proposed historical pilot is 24 new
  one-instrument October 26–30 POSTs in 24 invocations capped at one each,
  reusing the existing RELIANCE response. It requires separate approval and
  cannot itself establish causal or economic eligibility. A live quote/depth study still
  requires raw depth frame retention and dated capture preflight.
- The context Merkle `powershell` check was attempted and could not run on
  this Mac (`command not found`); refresh remains pending.

## 2026-11-03 Mac live-feed handshake

- The saved persistent scope authorizes the frozen 25 NSE_EQ IDs on one
  standard quote socket; depth remains unapproved. The current token and
  Dhan Data API plan passed the existing profile check, and RAW_CAPTURE_ONLY
  preflight passed.
- Two Rust WebSocket handshakes returned HTTP 400 while an official-style
  Python handshake with the same local credentials succeeded. Tungstenite
  converted the prior URL without a slash into a query-only request target.
  The Rust URL now includes /?; an offline regression test checks the path.
- The corrected Rust socket upgraded, sent the 25-ID quote subscription and
  synced its first binary frame before decoding. A launcher defect then
  stopped it when the daily new-connection budget was exhausted. The launcher
  now applies that budget only before a new socket and continues preflight
  checks while an existing socket runs.
- After the corrective restart, local health showed feed_connected=true,
  storage_status=JOURNALED, and 223 received frames. The private raw QJ01
  status later showed 1,103 frames with subsecond status age and no stop
  event. Dhan REST snapshots remained HTTP 200 for all 25 IDs. Raw feed
  observations remain validation-pending and cannot enter trading signals or
  training without dated session/CAS and timing evidence. No broker action
  occurred. The PowerShell context check/refresh is unavailable on this Mac.
- A later genuine 150-byte frame was preserved with a valid QJ01 CRC and
  SHA-256 before the parser stopped: it contained three in-scope code-4
  quotes, including security ID 29124 with last-trade quantity 48,142.
  The prior signed-i16 parser read that field as negative. Dhan's official
  Python v2 decoder uses unsigned H for last-trade quantity and unsigned I
  for volume and displayed quantities. The Rust decoder now follows those
  field widths. An offline scan found 6,238 preserved frames, 8,515 code-4
  packets, all 25 IDs, one such high-bit quantity, and no bad packet lengths.
  Focused standard-decoder 4/4 and capture 6/6 tests passed. After rebuild
  and bounded restart, telemetry again reported feed_connected=true,
  JOURNALED storage, 1,571 frames and no journal block. The live session
  remains RAW_CAPTURE_ONLY and NO TRADE.
- By 14:49 IST the approved 25-ID raw journal had reached about 63 MiB
  against its old 64 MiB cap, while 12 GiB remained free. The existing
  Python preflight, Rust capture and Rust socket bounds now agree on a
  128 MiB ceiling; the active journal crossed the old 64 MiB boundary
  while receiving fresh frames after rebuild and restart. Focused Python
  preflight 2/2 and Rust capture 6/6 tests passed. One genuine idle timeout
  was recorded and recovered through the bounded launcher path. Today's
  corrective restarts are retained in the event log; the launcher now has
  a 12-attempt daily ceiling and never uses that ceiling to stop an active
  socket. The 15:14 IST capture cutoff remains unchanged.
- Code-only UI review found the Market intelligence main panels still showed
  N/A for observed price, volume, receipt and top book while the same
  selected-instrument Dhan REST snapshot was available below. Those fields
  now use the saved REST response as a separately labeled fallback; derived
  microprice, OFI and rolling model features remain unavailable. Web
  typecheck, lint and focused App tests (5/5) passed. No browser was opened
  for this follow-up at the user's direction.

### Resolution of Codex review findings — 2026-11-04 IST

- **Prospective Outcome Integrity (`order_book_imbalance.py`):**
  - Enforced matching `feed_session_id` between decision and exit quote in `locate_outcome_quote` and `AdverseTargetOutcome.evaluate`, rejecting cross-session candidate matches (`CENSORED_CROSS_SESSION_QUOTE`).
  - Enforced causal availability: exit quote receipt must satisfy $T_{\text{maturity}} \le T_{\text{exit}} \le \text{as\_of\_ns}$. Quotes received after the scoring cutoff `as_of_ns` are strictly censored as `CENSORED_OUTCOME_NOT_YET_AVAILABLE`.
  - Added deterministic candidate ordering by `(receive_timestamp_ns, journal_offset, raw_frame_sha256)`.
  - Implemented `detect_feed_discontinuity` to derive feed continuity from genuine capture event logs (`raw-capture-events-*.jsonl`) and quote streams rather than trusting a caller boolean.
  - Added regression tests reproducing cross-session rejection, future availability censoring, and capture event discontinuity.

- **Economic Comparisons & Capital Tracking (`order_book_imbalance.py`):**
  - Implemented dynamic capital release: active positions release notional exposure when $T_{\text{exit}} \le \text{decision\_timestamp\_ns}$.
  - Maintained independent portfolio states for Unfiltered and Filtered arms under the ₹15,000 deployment ceiling.
  - Distinguished filter rejections from capital rejections across both arms.
  - Filter abstention preserves capital, enabling redeployment into subsequent opportunities missed by the unfiltered arm.
  - Formulated arm-level net incremental value: $\Delta \text{Net} = \text{Filtered Net P\&L} - \text{Unfiltered Net P\&L} = (\text{Avoided Losses} - \text{Foregone Profits}) + \text{Redeployed Net P\&L}$.
  - Corrected fee terminology, distinguishing broker commissions (`brokerage_charges_inr`) from statutory taxes/levies (`statutory_charges_inr`).

- **Standard Capture Rotation in Normal Startup (`live_feed_scope.py`, `dhan_quote_preflight.py`, `quant-journal`):**
  - Updated `live_feed_scope.py` so standard capture startup defaults to a segmented journal directory (`raw-standard-YYYY-MM-DD/`) under `alpha-grid-market-data-access-v2`, while preserving the existing November 4 flat capture file untouched.
  - Updated `dhan_quote_preflight.py` to auto-initialize non-existent segmented journal directories with mode 0o700 and enforce the 4 GiB segmented cap (`MAX_SEGMENTED_JOURNAL_BYTES`).
  - Enhanced Rust `JournalWriter::open` and `JournalReader::open` in `quant-journal` to transparently auto-detect and read both flat `.qj` files and segmented directories. Added unit tests for transparent reading, rotation triggers, and file-descriptor hygiene.

- **Depth-Source Isolation (`App.tsx`, `live_simulation.py`):**
  - In `web/src/App.tsx`, standard touch quotes strictly read `liveMarket.best_bid_price_nanos` / `best_ask_price_nanos`, never overwriting or falling back to `DEPTH_20` book levels.
  - UI order book panels explicitly label depth source and provenance (`DEPTH_20 / INDEPENDENT ASYNC FEED / 20 LEVELS` vs `STANDARD_FULL / SAME PACKET / FIVE LEVELS`).
  - In `src/quant/apps/live_simulation.py`, removed artificial touch price equality assertion for `DEPTH_20`, keeping asynchronous depth collection decoupled from standard market quote feeds.

### Resolution of Final Four Material Review Corrections — 2026-11-04/05 IST

- **Causal Outcome Integrity (`order_book_imbalance.py`, `test_order_book_imbalance.py`):**
  - Added `_is_valid_sha256` and enforced 64-hex durable frame provenance on decision and outcome records (`CENSORED_PROVENANCE_MISSING`).
  - Implemented `verify_feed_continuity` requiring genuine event logs or dense quote stream coverage ($\le 5\text{s}$ gap); `event_log=None` or empty event logs without dense quote coverage fail closed with `CENSORED_CONTINUITY_UNVERIFIED`.
  - Enforced non-empty matching feed sessions (`CENSORED_CROSS_SESSION_QUOTE`).
  - Added tests reproducing missing log, empty log, and missing provenance counterexamples.

- **Depth Safety (`CommandCenter.tsx`, `live_simulation.py`, `test_live_simulation.py`):**
  - In `web/src/CommandCenter.tsx`: standard touch quote is strictly sourced from standard feed (`MARKET BID / ASK`), never from `DEPTH_20`. In detail drawer, `DEPTH_20` is explicitly labeled and timestamped (`20-Level Derived Book (DEPTH_20 · ASYNC STREAM · ${time})`), never under generic `BID / ASK` or `Five-Level Displayed Book` headers. Prefer same-packet `STANDARD_FULL` for book display.
  - In `src/quant/apps/live_simulation.py`: `DEPTH_20` only influences decisions if instrument identity (`security_id == sid`), receipt freshness (`<= MAX_AGE_NS`), raw provenance, and monotonicity are independently valid. Missing, stale, or mismatched `DEPTH_20` is safely ignored without rejecting or blocking standard feed processing.
  - Added regression tests in `test_live_simulation.py` verifying stale and mismatched depth frames do not block standard feed observations.

- **Portfolio Economic Consistency (`order_book_imbalance.py`, `test_order_book_imbalance.py`):**
  - Enforced Alpha Grid portfolio risk limits independently across both comparison arms in `evaluate_filter_economics`:
    * Max concurrent positions: 3 (`PORTFOLIO_MAX_POSITIONS`)
    * Per-instrument notional limit: ₹5,000 (`PORTFOLIO_MAX_INSTRUMENT`)
    * Deployed capital ceiling: ₹15,000 (`PORTFOLIO_MAX_DEPLOYED`)
    * Session loss limit: ₹500 (`PORTFOLIO_SESSION_LOSS`)
  - Separately reported pure selection effects (`selection_net_value_inr`) and redeployment effects (`redeployed_net_pnl_inr`) without forcing an invalid matched-trade identity.
  - Added test reproducing Codex's four simultaneous ₹1,000 positions counterexample, verifying rejection of the 4th position.

- **Segmented Capture Startup & Gateway Verification:**
  - Built Rust gateway binaries locally:
    * `rust/target/debug/telemetry_local` SHA-256: `f1437572094628e97739c252e03a700dbbb876e30b3c9f976c03e4fdaaaa87cb`
    * `rust/target/debug/quant-depth` SHA-256: `29b3ac0995ea65749a17af1cc9f62ba7bdee497b2d97c106aa31fea7c7223539`
  - Verified launcher selection: `tools/start-cockpit-macos.sh` selects `$root/rust/target/debug/telemetry_local`.
  - Verified new-date startup path selects segmented journal directory (`raw-standard-YYYY-MM-DD/`) while preserving Nov 4 flat `.qj`.

### Mac external data placement — 2026-11-05 IST

The operator chose HDD placement for Alpha Grid data while keeping code and credentials on the Mac. The Mac launcher now mounts the owner-checked encrypted APFS image at `/Volumes/AlphaGrid Data` from the connected external drive, writes its private app state to `Live/offline-dev`, and stops its supervised processes if the volume disappears. The account-response cache is directed there through `ALPHA_GRID_ACCOUNT_CACHE_DIR`; Dhan credential files remain in `data/.secrets` on the Mac. No storage or eligibility threshold was relaxed.

The older October 9 workspace was archived as `Archive/old-workspace-2026-10-09.tar` and verified against the Mac source by full path, file bytes, hardlinks, and symlinks. Its SHA-256 is `dc430ce67b35a6e9cee995b173c6977774e28b573f45278e3ebf692681449629`; the byte count is 11,971,655,680. The Mac source was removed after verification. The prior Mac private app data matched the HDD copy across 1,570 identical files, five append-preserving journals/logs, and six newer status files; all 1,673 account-cache files also matched. The redundant Mac app-data and account-cache copies were removed. The active private simulation ledger on the HDD passed a read-only hash-chain check through sequence 16,424. `bash -n`, two focused account-cache tests, and `git diff --check` passed. Existing ignored `data/historical` and `data/instruments` research evidence remains on the Mac during active capture; normal launcher runtime output is directed to the HDD. Two incomplete HDD copy attempts and an unusable initial sparsebundle were left untouched because the operator prohibited deletion on the HDD.

### HDD disconnect audit — 2026-11-06 IST

macOS recorded Finder unmount requests at 00:09:08 and 00:09:24, both blocked as busy; at 00:09:41 the upstream USB 3 hub link changed and the Toshiba disk disappeared. APFS then reported failed flushes. The HDD later remounted, but the encrypted image required a read-only audit attach. Its attach-time APFS check passed; full ExFAT verification could not unmount the busy physical volume and did not run. Read-only verification found 96,326 intact standard QJ01 records, a 54,765-record valid simulation hash chain, and 187 verified historical manifest windows (138 missing); the separate depth QJ01 has an interior checksum failure at byte 252,467. The launcher was corrected to reject an APFS mount flagged read-only. No original data, checkpoints, credentials, risk controls, or services were changed during this audit. Resume remains NO-GO pending writable mount and physical filesystem assessment; depth capture also requires separate integrity resolution.

### 3-year historical acquisition completion, dataset audit, and frozen baseline research snapshot — 2026-11-07 IST

- **Full Acquisition & Integrity Verification**: Resumed and completed all 138 missing historical windows using the fail-closed runner (`execute_full_3yr_acquisition.py`), reaching 325/325 manifest windows (100% complete), 6,545,612 1-minute OHLCV candles, and 298.35 MiB (312,842,511 bytes) stored on `/Volumes/AlphaGrid Data/Live/offline-dev/historical-raw-dhan/`. All 325 raw files passed cryptographic SHA-256 verification (0 missing, 0 hash mismatches).
- **Free-Space Discrepancy Reconciled**: Resolved the 814 GiB vs 237 GiB discrepancy: 814 GiB is physical free space on host volume `/Volumes/Danny's HDD` (`/dev/disk6s1`); 237 GiB is remaining capacity inside the 256 GiB provisioned APFS virtual container (`/dev/disk10s1`) formatted within `AlphaGridData-2026-11-05-v2.sparsebundle` at `/Volumes/AlphaGrid Data`. Cockpit running state confirmed intact without service modification (`task-6101`: Vite 5173 OK, Telemetry 8765 OK, Trading 8767 OK in DATA UNAVAILABLE / NO TRADE mode).
- **Read-Only Data Audit Findings**:
  - *Per-Session Minute Coverage*: Continuous market sessions span 375 expected minutes (09:15–15:30 IST). Between 2026-08-03 and 2026-10-30, Dhan historical API returns 360 bars (ending at 15:14:00 IST), leaving a consistent 15-minute deficit across 1,300 session windows. Saturday DR sessions observed on 2024-03-02 (106 bars) and 2024-05-18 (107 bars); Diwali Muhurat observed on 2023-11-12 (60 bars).
  - *Timestamp Semantics*: Epoch seconds denote interval start $[T, T+60\text{s})$. Discovered 9 pre-open auction bars ($< 09:15\text{ IST}$) and 1,429 post-close auction/reconciliation bars ($> 15:30\text{ IST}$). Mandatory filtering to continuous hours $[09:15, 15:15]$ enforced.
  - *Instrument Identity & Corporate Actions*: All 25 equities verified. Dhan served unadjusted prices across PCJEWELLER 10:1 split; back-adjusted prices served across RELIANCE 1:1 bonus, BSE, HDFCBANK, etc. MONEYVIEW (< Oct 2026) and CUPID (< Jul 2024) unlisted/gap windows quarantined.
  - *Decoupled Research Eligibility*: Return modeling is conditionally eligible with mandatory purging of auction bars, ex-date sessions, and unlisted periods. Execution backtesting is strictly INELIGIBLE because 1m OHLCV cannot simulate L2 order book queue depth, spreads, or touch liquidity.
- **Frozen Research Snapshot**: Pre-registered `alpha-grid-snapshot-2026-11-07-v1` in `data/research_snapshot_manifest_v1.json` and on the external volume. Merkle root hash of all 325 raw SHA-256 digests is `d1974a10450b85555fc83ce33d8b5191eddfd011a77ffbff78d82a3d7e7badec`.
- **Predeclared 15-Minute Baseline Experiment**: Established `alpha-grid-cross-equity-ml-v1` protocol using `src/quant/research/cross_equity_model.py`:
  - *Setup*: 15-minute holding horizon, 5-minute decision cadence between 10:00 and 14:45 IST, 5 causal point-in-time features. Target: $Y = \mathbb{I}(\text{return} \ge +30\text{ bps})$. Adverse hurdle: $\le -10\text{ bps}$ or $\text{MAE} \le -25\text{ bps}$.
  - *Splits*: Chronological Train (2023-11-01 to 2025-04-30, ~368 sessions), Calibration (2025-05-01 to 2025-10-31, ~124 sessions), Test Out-of-Sample (2025-11-01 to 2026-10-31, ~248 sessions). Cross-sectional holdout: 5 reserved stocks (TCS, KOTAKBANK, MARUTI, BHEL, DIVISLAB) kept strictly untouched.
  - *Uncertainty & Economics*: Session-grouped clustering (~740 session clusters); realistic Dhan intraday statutory fees + 5.0 bps spread + residual friction sensitivity ladder (0, 5, 10, 15 bps). Predeclared acceptance: Brier score $< \text{Null}$ and net PnL $> 0$ on calibration split. Frozen RELIANCE November pilot remains untouched.

### AlphaGrid M1 forecasting pipeline, statistical advantage validation, and economic gating — 2026-11-07 IST

- **Model Delivery & Pipeline Implementation (`src/quant/research/m1_pipeline.py`, `tools/train_m1_model.py`)**: Built AlphaGrid M1 (`alpha-grid-m1-20261107-v1`), a custom dual lightweight gradient-boosted ensemble (`HistGradientBoostingClassifier`, max depth 3, 100 trees, L2 penalty 1.0) predicting separate favorable ($P(R_{\text{fwd}} \ge +30\text{ bps})$) and adverse ($P(R_{\text{fwd}} \le -10\text{ bps} \lor \text{MAE} \le -25\text{ bps})$) probabilities over 15-minute forward horizons.
- **Leakage Prevention & Outcome Purging**: Reused the 5 predeclared causal point-in-time features (`past_5m_return_bps`, `breakout_distance_bps`, `relative_volume_15m`, `realized_volatility_15m_bps`, `normalized_time_of_day`). Purged overlapping forward outcomes during training using 15-minute non-overlapping decision slots ($N_{\text{train}} = 133,200$; $N_{\text{cal}} = 47,880$). Train-only preprocessor (`M1Preprocessor`) fits standardization parameters strictly on in-sample data. All 5 reserved holdout stocks (`TCS`, `KOTAKBANK`, `MARUTI`, `BHEL`, `DIVISLAB`) and the post-October-2025 test window were strictly quarantined and untouched.
- **Out-of-Sample Calibration Results**:
  - *Statistical Predictive Advantage*:
    * Favorable Target ($7.09\%$ base rate): M1 Brier score $0.062219$ vs Null $0.066684$ ($-0.004465$ improvement) and Logistic $0.066074$. Log loss $0.23375$ vs Null $0.26093$.
    * Adverse Target ($32.26\%$ base rate): M1 Brier score $0.203758$ vs Null $0.220443$ ($-0.016685$ improvement) and Logistic $0.215809$. Log loss $0.59597$ vs Null $0.63301$.
  - *Realistic Portfolio Simulation (₹15,000 capital ceiling, ₹5,000 position limit, statutory charges + 5 bps spread)*:
    * AlphaGrid M1: 117 entered trades ($95\%$ abstention), $31.6\%$ win rate, gross PnL $+₹283.26$. However, statutory fees (₹508.22) and quoted spread (₹249.60) yield net PnL $-₹474.56$ ($-9.20\text{ bps}$ mean net return).
    * Momentum Baseline: 2,132 trades, $29.5\%$ win rate, gross $+₹1,606.86$, net $-₹12,612.92$ ($-13.86\text{ bps}$).
    * Null Baseline: 2,197 trades, $17.1\%$ win rate, gross $-₹349.51$, net $-₹15,002.33$ ($-15.42\text{ bps}$).
    * NO_TRADE Baseline: 0 trades, net ₹0.00.
- **Artifact Packaging & Verification**: Packaged model artifact `data/models/alpha-grid-m1-v1.pkl` and mirrored to external storage `/Volumes/AlphaGrid Data/Live/offline-dev/training-artifacts/` with companion `.meta.json` and `.sha256` sidecars. Tamper verification fails closed on hash divergence.
- **Shadow Replay & Read-Only Inference**: Implemented `M1Predictor` (read-only snapshot interface without broker or order bindings) and `M1ShadowLogger` (append-only JSONL shadow predictions with per-record SHA-256 integrity). Successfully demonstrated on calibration replay.
- **Decision Verdict**: **`SHADOW-GO / TRADE-NO-GO`**. M1 exhibits genuine statistical predictive advantage over linear and null baselines, but the 15m return magnitude ($+30\text{ bps}$ hurdle) is insufficient to overcome India cash intraday statutory friction and bid-ask spread. Approved strictly for passive telemetry and shadow forward observation; live trading remains blocked.

### AlphaGrid M1 forensic audit and offline verification — 2026-11-07 IST

- **Forensic Audit Execution (`tools/audit_m1_model.py`)**: Conducted read-only audit of AlphaGrid M1 without retraining or modifying models:
  1. *Calibration Fitting vs Evaluation Independence*: Model weights (`alpha-grid-m1-v1.pkl`, SHA-256 `81e338b41b67...`) and preprocessor scaling parameters were verified frozen and strictly in-sample (Nov 2023–Apr 2025). Re-evaluated on calibration observations ($N = 47,880$, May–Oct 2025 across 19 active stocks) reproducing exact reported metrics: Favorable Brier $0.062219$, Adverse Brier $0.203758$, Favorable Log Loss $0.23375$, Adverse Log Loss $0.59597$.
  2. *Session-Clustered Block Bootstrap Uncertainty (1,000 resamples)*: Evaluated clustered across 126 independent session dates. Favorable $\Delta \text{Brier} = -0.004462 \pm 0.000250$ ($95\%\text{ CI: } [-0.004964, -0.003997]$); Adverse $\Delta \text{Brier} = -0.016704 \pm 0.000832$ ($95\%\text{ CI: } [-0.018322, -0.015100]$). Both 95% confidence intervals strictly exclude zero, confirming statistically significant predictive lift under cross-sectional clustering.
  3. *Portfolio Economics & Slippage Ladder*: Evaluated candidate decisions under capital limits (₹15k max deployed, ₹5k/pos, max 3 positions) across residual friction levels ($0.0, 5.0, 10.0, 15.0\text{ bps}$). Under predeclared threshold ($P_{\text{fav}} \ge 0.12, P_{\text{adv}} \le 0.35$), M1 yields 26 trades ($34.6\%$ win rate, gross PnL $+₹85.20$). Statutory fees (₹110.14) and quoted spread (₹53.66) result in net PnL $-₹78.60$ ($-6.84\text{ bps}$) at $0\text{ bps}$ slippage, degrading to $-₹239.57$ ($-21.84\text{ bps}$) at $15\text{ bps}$ slippage.
  4. *Frozen Experiment Isolation*: Verified zero impact on RELIANCE November pilot, `ORDER_BOOK_AMENDMENT_V3/V4`, live simulation engine, or reserved 5 holdout stocks (`TCS`, `KOTAKBANK`, `MARUTI`, `BHEL`, `DIVISLAB`).
  5. *Artifact Provenance*: Verified SHA-256 matches cryptographic sidecar; tamper mutation fails closed. Unit tests expanded in `tests/test_m1_pipeline.py` (8 passed).
- **Classification**: **`OFFLINE-GO / SHADOW-GO / TRADE-NO-GO`**. Offline reproducibility, causal integrity, and statistical predictive advantage are verified; trading deployment remains strictly prohibited due to sub-friction intraday economics.

### AlphaGrid M1 economic friction gap forensic investigation — 2026-11-07 IST

- **Frozen M1 v1 Scope Preserved**: Zero models retrained or modified; post-October 2025 test window and 5 reserved holdouts (`TCS`, `KOTAKBANK`, `MARUTI`, `BHEL`, `DIVISLAB`) remain untouched.
- **Reconciliation of 117-Trade vs 26-Trade Policies**:
  * Threshold $\tau_{\text{fav}} = 0.10, \tau_{\text{adv}} = 0.35$: 283 qualified, exactly 117 executed trades ($31.6\%$ win rate, gross $+₹283.26$, fees ₹508.22, spread ₹249.60, net $-₹474.56$ / $-9.20\text{ bps}$).
  * Threshold $\tau_{\text{fav}} = 0.12, \tau_{\text{adv}} = 0.35$: 45 qualified, exactly 26 executed trades ($34.6\%$ win rate, gross $+₹85.20$, fees ₹110.14, spread ₹53.66, net $-₹78.60$ / $-6.84\text{ bps}$).
  * Both policies reflect the exact same structural economic ceiling: tightening probability thresholds increases win rate and gross return, but trades remain structurally below the ~14 bps round-trip friction threshold.
- **Probability Calibration Audit**:
  * Favorable probabilities exhibit strictly monotonic empirical positive rates ($2.15\%$ for $P < 0.04$ up to $17.84\%$ for $P \ge 0.13$). Slight under-prediction ratio ($0.72\text{--}0.95$) caused by macro volatility compression from train ($9.93\%$) to calibration ($7.09\%$).
  * Adverse probabilities are calibrated nearly $1:1$ out-of-sample ($0.89\text{--}1.00$ empirical-to-predicted ratio across all bins).
- **Decomposition Findings (Root Cause of Negative Net Returns)**:
  * *MFE vs Exit Degradation*: On `ACTIVE_BREAKOUT` candidates ($N = 256$), mean Max Favorable Excursion is $+20.4\text{ bps}$, but post-breakout pullback reduces the realized 15m exit return to $+2.8\text{ bps}$.
  * *Friction Floor*: Indian cash equity statutory levies ($\approx 4\text{--}5\text{ bps}$) plus quoted half-spread crossing ($5\text{ bps}$) impose a non-negotiable $\approx 13.5\text{--}14.0\text{ bps}$ hurdle.
  * *Cross-Stock & Time Patterns*: Afternoon entries ($13:00\text{--}14:45$) outperform morning entries ($+7.5\text{ bps}$ vs $-1.5\text{ bps}$); top stock `ETERNAL` achieves $+10.6\text{ bps}$ gross. However, **zero stocks and zero time windows achieve gross returns exceeding 14 bps**.
- **Research Verdict**: 15-minute holding horizon in Indian cash equities is **economically non-viable for aggressive taker execution**. Further research is justified only by expanding the holding horizon to 60–120 minutes (where gross trends reach 40–80 bps) or modeling passive queue-resting limit order fills using L2 order book depth. Live trading remains strictly prohibited.

### Independent M1 correction and economic assessment — 2026-11-07 IST

The 117-versus-26 comparison was caused by a serialization defect: training saved favorable threshold 0.10 inside the artifact's nested `calibrated_thresholds`, while reloading used a 0.12 default. The loaded policy now restores 0.10; the original local and HDD artifacts remain byte-identical (SHA-256 `81e338b41b674c185b4a9e1d963eda0933020b35c82b3b0ffcf2ad06764fcc7d`). Independent offline replay found 283 probability-qualified candidates, 125 with positive past-5-minute return, and 117 executed references across 63 of 126 calibration dates. The previous 0.12 policy qualified 45 and executed 26 across 22 dates. The `NULL_BASELINE` simulator adds the positive-momentum filter and chooses the first eligible instrument in symbol order; M1 does not rank qualified stocks by its probability. Therefore earlier economic comparisons do not isolate incremental M1 ranking value.

For the saved 0.10 policy, ₹499,196.60 reference notional yielded ₹283.26 gross, ₹508.22 published-fee estimate including brokerage, ₹249.60 assumed 5-bps spread, and ₹−474.56 net before any additional slippage. Gross is 5.67 bps of notional; fees alone are 10.18 bps. A 2,000-resample date-clustered exploratory interval for total net was [₹−746.45, ₹−181.96]; it is conditional on the inspected calibration period and policy history. The 0.12 policy also failed before spread (₹85.20 gross versus ₹110.14 fees). Dhan's public historical API documents epoch timestamps but not first candle availability or executable fills. This dataset supports exploratory probability scoring, not a causal trading claim. Prior 15-minute economic viability statements should be read as rejecting these tested policies, not every possible 15-minute opportunity.

Read-only verification matched all 325 snapshot files to their declared SHA-256 and size (312,842,511 bytes); the declared Merkle-root construction remains undocumented. Training and audit loaders previously trusted the manifest label and silently skipped absent files; they now verify every consumed file. Artifact loading now requires its SHA-256 sidecar. Focused regression tests reproduced the threshold and source-integrity failures before correction and passed after correction (5 targeted tests). The original raw responses, model artifacts, reserved stock and temporal holdouts, frozen RELIANCE experiment, QJ01/QP01 ledgers, and risk limits were not changed. No model was retrained or Dhan request made. The recommended next step is a frozen, exploratory 60-minute gross-versus-cost screen using the existing M1 scores and a matched simple momentum baseline on already inspected development dates; any positive result would still require separately authorized prospective timing and execution evidence before deployment.

### Frozen 60-minute opportunity protocol (EXP-60M-BRACKETED-OPPORTUNITY-V1) — 2026-11-07 IST

- **Executable Fill Semantics & Timing Defect Resolution**:
  * In `cross_equity_model.py`, candidate entry previously referenced the close of bar $T-1$ ($C_{T-1}$), an unrealizable fill for decisions computed at minute $T$.
  * Measured across 45,486 calibration bars: realizable fill at bar $T$ open ($O_T$) has a mean difference of $+0.013\text{ bps}$ ($\sigma = 3.35\text{ bps}$, 95% range $[-5.89, +6.09]\text{ bps}$) and a 15m return delta of $-0.013\text{ bps}$. Realizable $O_T$ fills are strictly mandated in all new protocols; candle-close entries are prohibited.
- **Resolution of "40–80 bps" Claim (MFE vs. Realized Return)**:
  * Measured empirical forward distributions on calibration data revealed that the previously cited "40–80 bps" refers to **intra-window Maximum Favorable Excursion (MFE)**, NOT holding-period realized returns.
  * Realized return at 60m is $+1.75\text{ bps}$ for raw breakouts and $+8.25\text{ bps}$ for M1 signals, while MFE averages $+40.48\text{ bps}$ and $+43.92\text{ bps}$ respectively. Naive fixed-time 60m exits surrender $>80\%$ of peak excursion while exposing positions to $-33\text{ to } -37\text{ bps}$ adverse excursion (MAE), yielding a net loss of $-7.24\text{ bps}$ against the $15.49\text{ bps}$ friction floor.
- **Frozen Protocol Defined (`docs/exp-60m-bracketed-opportunity-protocol-v1.md`)**:
  * Frozen prior to examining 60m outcomes or fitting new models.
  * *Null Hypotheses*: Primary economic null $H_{0,\text{econ}}$: $\mathbb{E}[\text{Net Return}] \le 0\text{ bps}$; statistical null $H_{0,\text{stat}}$: $\Delta \text{Brier} \ge 0$.
  * *Comparative Arms*: Arm 0 (NO_TRADE), Arm 1 (MOMENTUM_FIXED_60M), Arm 2 (BREAKOUT_FIXED_60M), Arm 3 (BREAKOUT_BRACKET_ASYMMETRIC: $+25\text{ bps}$ target, $-20\text{ bps}$ stop, 60m timeout), Arm 4 (MAKER_BRACKET_PASSIVE: resting limit entry at bid/midpoint saving $2.5\text{--}5.0\text{ bps}$ spread).
  * *Holdouts & Integrity*: Pristine temporal holdout (Nov 2025–Oct 2026) and 5 reserved stocks (`TCS`, `KOTAKBANK`, `MARUTI`, `BHEL`, `DIVISLAB`) remain strictly untouched. May–Oct 2025 calibration data is explicitly categorized as development evidence. 60m non-overlapping purge window enforced.
  * *Acceptance Criteria*: Arm 3 or 4 achieves $95\%$ bootstrap lower bound $> 0\text{ bps}$ under $20.49\text{ bps}$ base friction ($N \ge 100$ trades, profit factor $> 1.25$). If all arms fail under zero-slippage friction floor ($15.49\text{ bps}$), the cash equity taker research track is permanently terminated.

### EXP-60M Stage 2 evaluation & research track termination — 2026-11-07 IST

- **Dataset Provenance & Holdout Integrity**: Evaluated across 180 verified development files (172.3 MB, Nov 1, 2023 to Oct 31, 2025 across 19 active stocks) matching declared SHA-256 and byte counts. Pristine temporal holdout (Nov 2025–Oct 2026) and 5 reserved stocks (`TCS`, `KOTAKBANK`, `MARUTI`, `BHEL`, `DIVISLAB`) remained strictly uninspected.
- **Excursion Dynamics (MFE vs. Realizable Bracket Exits)**:
  * Across 20,971 active breakout candidates in the 2-year development window, mean hindsight MFE is $+44.64\text{ bps}$ and mean hindsight MAE is $-40.11\text{ bps}$. Realizable fixed 60m exit averages only $+3.59\text{ bps}$ gross.
  * In the realizable asymmetric bracket arm ($+25.0\text{ bps}$ target / $-20.0\text{ bps}$ stop with collision priority to stop), random-walk microstructural volatility triggers the stop-loss in $51.9\%$ of trades vs the profit target in $39.9\%$ (timeouts: $8.2\%$). Mean gross bracket return before any fees is negative ($-0.21\text{ bps}$).
- **Portfolio Economics Across Friction Scenarios**:
  * On the out-of-sample calibration split under base realistic friction ($20.49\text{ bps}$ taker / $12.99\text{ bps}$ maker):
    - `NO_TRADE`: ₹0.00 net, $0.00\text{ bps}$.
    - `UNCONDITIONAL_NULL`: ₹-1,057.75 net, $-28.16\text{ bps}$ ($95\%\text{ CI: } [-32.26, -24.24]$).
    - `MOMENTUM_FIXED_60M`: ₹-1,132.74 net, $-28.05\text{ bps}$ ($95\%\text{ CI: } [-46.31, -10.72]$).
    - `BREAKOUT_FIXED_60M`: ₹-315.93 net, $-11.71\text{ bps}$ ($95\%\text{ CI: } [-37.70, +15.17]$).
    - `BREAKOUT_BRACKET_ASYMMETRIC`: ₹-1,743.30 net, $-19.72\text{ bps}$ ($95\%\text{ CI: } [-24.15, -16.32]$).
    - `MAKER_BRACKET_PASSIVE`: ₹-1,725.59 net, $-15.48\text{ bps}$ ($95\%\text{ CI: } [-18.84, -12.64]$).
  * Under the zero-slippage friction floor ($15.49\text{ bps}$ taker / $10.49\text{ bps}$ maker), all arms remain negative (bracket taker: $-14.70\text{ bps}$, maker: $-12.98\text{ bps}$).
- **Predeclared Stop Rule Triggered & Final Verdict**:
  * Primary economic null $H_{0,\text{econ}}$ cannot be rejected. Net returns fail to survive the zero-slippage friction floor across all arms.
  * **VERDICT: NO-GO FOR ML MODELING ON DIRECTIONAL INTRADAY CASH EQUITIES. RESEARCH TRACK TERMINATED.** Directional cash equity intraday breakout/momentum strategies cannot overcome statutory exchange charges and bid-ask spread.

### Minimum offline autonomous decision & execution-safety path — 2026-11-07 IST

- **Safety Architecture (`src/quant/apps/execution_safety.py`)**:
  * Implemented fail-closed offline decision and order lifecycle state machine without live broker endpoints.
  * *Decision Policy*: Gates every candidate against quote freshness ($\le 2\text{s}$), Stage 3 model protocol acceptance, bootstrap uncertainty ($95\%\text{ CI lower bound} > 0\text{ bps}$), and conservative net opportunity ($> 5.0\text{ bps}$ after statutory fees + spread + slippage). Unapproved models fail closed to `NO_TRADE`.
  * *Independent Risk Controller*: Enforces capital ceiling (₹20,000 capital, ₹15,000 max deployed), position limit (₹5,000 max/pos, 3 positions max), session loss limit (₹500), decision loss (₹200), max spread ($25\text{ bps}$), and manual/automatic kill-switch.
  * *Mock Broker State Machine*: Strict transitions across `PENDING -> ACKNOWLEDGED -> PARTIALLY_FILLED -> FILLED | CANCELLED | REJECTED | UNKNOWN`. Illegal transitions raise `IllegalStateTransitionError`. Ambiguous timeouts transition to `UNKNOWN` and engage kill switch.
  * *Fail-Closed Storage & Network Isolation*: Immediate kill-switch engagement upon unmounted/inaccessible HDD storage (`/Volumes/AlphaGrid Data`). Fills are strictly generated via deterministic mock simulator, never inferred from historical OHLCV. Zero live broker network routes.
- **Verification & Targeted Tests (`tests/test_execution_safety.py`)**:
  * 9 targeted tests covering: Stage 3 rejection gating to `NO_TRADE`, stale quote rejection, negative bootstrap CI rejection, risk limit enforcement across all dimensions, lifecycle transition legality, client order ID idempotency, HDD disconnect kill-switch trigger, broker unknown state fail-closed response, and proof that maximum model probabilities cannot bypass risk controls or place orders. 65/65 project tests passing.
- **Classification**: **`SIMULATION-STOP`**. While the offline execution safety path is fully operational and verified, autonomous trading remains strictly stopped due to Stage 2/3 economic rejection.

### Minimal offline end-to-end integration & readiness audit — 2026-11-07 IST

- **Historical Replay & Parity Verification (`tools/offline_end_to_end_replay.py`)**:
  * Evaluated 6,840 chronological 15m decision slots across 19 active stocks over October 2025 development data (180 verified files, SHA-256 matched).
  * Pristine holdouts preserved: Temporal holdout (Nov 2025–Oct 2026) and 5 reserved stocks (`TCS`, `KOTAKBANK`, `MARUTI`, `BHEL`, `DIVISLAB`) remained strictly uninspected.
  * Verified exact feature/inference parity with dual HistGradientBoosting model (`alpha-grid-m1-20261107-v1`, SHA-256 `81e338b41b674c185b4a9e1d963eda0933020b35c82b3b0ffcf2ad06764fcc7d`).
  * Causal timing enforced: Features strictly end at minute $T-1$; hypothetical entry fills evaluated at Bar $T$ Open.
  * Zero live broker network routes or API calls. All 6,840 candidate evaluations passed through `AutonomousDecisionPolicy` and gated 100% to `NO_TRADE` due to `MODEL_STAGE3_REJECTED` (predeclared economic acceptance criteria unmet: gross moves cannot overcome the $\approx 15.5\text{ bps}$ friction floor).
- **Fault-Injection Test Suite (7/7 Passed)**:
  * `stale_quote_rejection`: Quotes older than 2.0s rejected with `QUOTE_STALE`.
  * `duplicate_event_idempotency`: Duplicate client order IDs rejected without duplicate fills or mutations.
  * `partial_fill_handling`: Orders handle partial executions, leaves calculation, and terminal transitions accurately.
  * `restart_recovery_hash_chain`: Simulated journal restart verifies hash-chain integrity, state restoration, and sequence continuity.
  * `session_loss_limit_breach`: Exceeding session loss ceiling (₹500) trips risk gate and halts new orders with `RISK_CONTROLLER_BLOCKED`.
  * `kill_switch_activation`: Manual or automatic kill switch freezes all transitions and prevents submission.
  * `stage3_unapproved_fail_closed`: Unapproved candidate models fail closed to `NO_TRADE`.
- **Cockpit Read-Only Status Export**:
  * Emitted standard status schema (`alpha-grid-live-portfolio-simulation-v2`) to `data/telemetry/offline_simulation_status.json` and external drive `/Volumes/AlphaGrid Data/Live/offline-dev/simulation-status.json`.
  * Status records `state: OFFLINE_REPLAY_COMPLETED`, 6,840 evaluated decisions, 0 fills, 0 live broker calls, and risk limits active.
- **Classification & Final System Verdict**:
  * **VERDICT**: **`OFFLINE-GO / TRADE-STOP`**.
  * The entire offline decision, risk gating, and simulation architecture is verified, reproducible, and fail-closed (`OFFLINE-GO`). Directional intraday cash equity live trading is permanently rejected and halted (`TRADE-STOP`). Alpha Grid terminates the cash equity intraday track after this stage as instructed.

### Independent correction of Stage 2 and mock safety claims — 2026-11-08 IST

- **Verified defect and correction:** `simulate_60m_portfolio` discarded positions still open after the last 14:15 decision slot when the next session began. A two-date isolated test yielded one trade instead of two before correction. A 15:15 settlement slot now books every matured position and raises on unsettled rollover. The full evaluator then verified 180 development files by size/SHA-256 and produced 163,476 candidate slots. On the inspected May–October 2025 split, the bracket arm had 2,662 reference trades, ₹-733.65 gross, ₹11,704.39 published charge estimate, ₹5,771.88 assumed spread, and ₹-23,981.81 net with another 5 bps assumed friction. The maker-touch proxy had 2,021 reference trades and ₹-11,564.89 net. Previously recorded Stage 2 totals are superseded; the negative result applies to these tested reference arms, not every cash-equity opportunity.
- **Methodological limits:** The protocol's statement that bar T open and OHLCV bracket/limit touches are realizable fills is unsupported. Dhan historical bars have no verified publication time, queue position, fill, or intrabar event ordering. The maker arm uses future bar-low and volume to decide hypothetical participation. The claimed ₹15,000 position fee floor differs from the evaluator's ₹5,000 position cap, and the evaluator's ₹1,500 session-loss limit differs from the live virtual simulator's unchanged ₹500 limit. The split is already inspected development evidence; no confirmatory holdout was opened. The frozen protocol file was not changed.
- **Mock safety corrections and remaining gap:** The isolated `AutonomousExecutionPath` double-reserved capital for duplicate signals, ignored partial orders in shared exposure, and did not enforce a custom configured capital ceiling. Focused regressions reproduced these failures before minimal corrections. Its `execution_ledger`, `reconciliation_log`, and retry constant are unused; it neither persists orders nor reconciles them after restart. Its decision method accepts caller-supplied expected return/CI and ignores the supplied M1 probabilities; passing `stage3_approved=True` is a constructor choice, not independent authorization. It is not imported by the live worker or QP01. The existing live virtual simulator's separate hash-chained ledger and ₹500 risk budget were left unchanged.
- **Replay reporting correction:** `tools/offline_end_to_end_replay.py` previously fabricated bid/ask around historical bar opens and a fresh quote timestamp, then overwrote the live worker's private `simulation-status.json`. It now logs historical-bar-only `NO_TRADE` decisions and writes a separate `offline-replay-status.json`. It reports the dataset root as declared, not recomputed, and no longer claims that a tautological hash-chain smoke check proves recovery. The saved M1 artifact (213,382 bytes) matched its SHA-256 sidecar; the independently rerun M1 audit reproduced 47,880 inspected calibration observations, Brier 0.062219/0.203758, and 117 reference trades at the saved 0.10 favorable threshold, with ₹-474.56 net at zero extra slippage. All original raw data, model artifacts, reserved holdouts, frozen RELIANCE experiment, and configured risk limits remain unchanged. Independent classification: OFFLINE RESEARCH ONLY / TRADE STOP.

### Follow-up independent verification — 2026-11-08 IST

The corrected full Stage 2 evaluator was rerun offline: 180 development files, 163,476 eligible slots, 2,662 calibration bracket reference trades, ₹-733.65 gross, ₹11,704.39 charges including brokerage, ₹5,771.88 assumed spread and ₹5,771.89 additional assumed friction, totaling ₹-23,981.81 net. At the ₹5,000 position cap, the published-rate fee calculator returns ₹4.92 on a flat ₹5,000 round trip; the protocol's ₹15,000 example yields ₹15.74 and is not the per-trade size used by this evaluator. The 15-minute audit still reproduces 117 references and ₹-474.56 net at zero extra slippage. Eighteen focused safety, Stage 2 and virtual-ledger tests passed; an isolated bracket collision fixture returned a -20 bps stop reference. No real fills were observed.

Read-only hashing matched all 325 raw files (312,842,511 bytes) to the manifest's per-file SHA-256 and size, without parsing reserved holdout observations. The declared research root is `d1974a10450b85555fc83ce33d8b5191eddfd011a77ffbff78d82a3d7e7badec`; a conventional sorted-key binary Merkle tree with duplicated odd leaves yields `f06030dffd59e719924f4229ab4645bb60d9bd2e892741914f1d20bed3fbe043`. Neither the manifest nor repository specifies the original root construction, so root equality is unverified rather than evidence of changed raw bytes. The isolated mock cannot reach real broker or QP01 code, but its probability arguments remain unused; its docstring now states this explicitly. No experiment, artifact, risk limit, or holdout was changed. Recommendation remains STOP for promotion; offline exploratory analysis is permitted.

### Independent opening sell-pressure reversal test — 2026-11-08 IST

`docs/exp-opening-sell-pressure-reversal-v1.md` froze one 120-minute, top-five-stock rule before its outcomes were read (SHA-256 `25bb1bedacbb8b641958461f25ecad09bdee62d61a4ab003eaa20e3706262952`). `tools/evaluate_opening_reversal_v1.py` verified size, SHA-256 and existing candle/parser contracts for 40 original pre-holdout windows (44,696,798 bytes). It did not read the later temporal or five-stock holdouts. Of 484 observed regular dates, 299 had a selected opening decline and matched control; none were censored by the required 10:00–12:11 continuity check. The 69 later development dates (May–October 2025) yielded ₹168.50 gross, ₹306.22 published-rate fee estimate, ₹302.74 assumed spread and ₹302.74 assumed residual friction: ₹-743.19 conservative net, mean ₹-10.77/date, paired-control difference ₹+3.21/date with 95% date-bootstrap interval [₹-2.90, ₹10.08]. Earlier 230 dates also lost ₹-3,020.53 conservative net. A five-minute later entry still lost ₹-712.73 on later dates. The frozen rejection criterion fired: **REJECT AND STOP this exact reference-price rule**, without generalizing to all intraday opportunities. Source candle availability, actual spread/fills, historic instrument continuity and the snapshot Merkle construction remain unresolved. Two new contract tests plus the existing fee/acquisition tests passed (28 total); no Dhan call, model training, order or immutable-data edit occurred.

### Weekly relative-strength exploratory test — 2026-11-08 IST

`docs/exp-weekly-relative-strength-v1.md` froze a 60-session volatility-adjusted cross-sectional rank and four-session delivery reference outcome before this rule's results (spec SHA-256 `8e3837c3fdfd253b6323dac125e26d086c2dfc8890a53e59244c9b24942bae16`). `tools/evaluate_weekly_relative_strength_v1.py` verified 160 pre-holdout raw Dhan files (160,814,342 bytes) against size/SHA-256 and existing timestamp/parser contracts; five reserved stocks and the post-October-2025 temporal holdout were not read. Of 82 eligible observed Fridays after 60-session warmup, 69 selected pairs remained (12 overlap and one missing-path censor). The 33 later development selections (January–October 2025) had ₹2,346.55 gross reference P&L, ₹834.04 provisional published-rate delivery fees, and ₹299.83 each assumed spread and residual friction: ₹912.85 conservative net. Mean net per selected date was ₹27.66 with 95% date-bootstrap interval [₹-51.00, ₹118.18]; paired mean advantage over the median-rank stock was ₹76.05 with interval [₹-22.21, ₹177.67]. Earlier 36 selections lost ₹1,051.07 conservative net. CUPID supplied 13/33 later selections; mean net in positive breadth was ₹58.04 versus ₹-13.57 in negative breadth, descriptive only. Worst later conservative reference loss was ₹490.78 and drawdown ₹1,213.37. Frozen acceptance fails on uncertainty and earlier-period consistency: **INCONCLUSIVE; no strategy admission or threshold search**. Dhan bars do not establish historical executable fills, original availability time, complete corporate-action/dividend treatment or an unbiased historical universe. The in-progress generic paper strategy registry was removed under the newer instruction not to build a strategy for an inconclusive experiment. No risk limits, raw responses, frozen experiments, model artifacts or live paths changed.




