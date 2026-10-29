# Context decision summaries

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
