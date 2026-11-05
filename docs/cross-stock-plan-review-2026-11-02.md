# Cross-stock plan review — 2026-11-02 IST

**Feasibility verdict: CONTINUE with a three-stock read-only quote/depth
quality pilot after narrow collector fixes; NO GO for capture as the Mac code
stands. Defer the proposed 24 historical Dhan POSTs. DATA_ONLY / NO TRADE.**
This review did not change the original
[25-stock universe](cross-stock-universe-2026-11-02.json) or frozen
[research plan](cross-stock-opportunity-plan-2026-11-02.md). It made zero
authenticated calls, opened zero live sockets, fitted no model and sent no
paper intent or broker order.

## Genuine evidence and code inspection

The 20 original public October NSE CM UDiFF ZIPs were rehashed against the
manifest. Each of HDFCBANK `NSE_EQ/1333/INE040A01034`, RELIANCE
`NSE_EQ/2885/INE002A01018` and INFY `NSE_EQ/1594/INE009A01021` has one
matching EQ/STK row on every source date. Recomputed October median traded
values are ₹19.129bn, ₹17.360bn and ₹16.691bn respectively. The October 30
NSE MII security file uniquely matches each ID/ISIN, normal-market flag `1`
and CAS flag `1`; its original SHA-256 and the November 1 Dhan master SHA-256
match the manifest. These establish October liquidity and dated identity,
not November 4 eligibility, current spreads or historical Dhan mapping on
every prior date. The existing Dhan genuine historical source is RELIANCE
OHLCV only; no Mac live quote/depth QJ01 was found in the repo-local data
inventory. The frozen 25 IDs remain an as-of October 30 artifact.

`quant-market-data/dhan_standard.rs` strictly decodes the 8-byte standard
header and Quote-17 response code 4; it reports last price, trade quantity
and cumulative volume, not the bid/ask. `quant-engine/capture.rs` fsyncs a
scoped original standard binary frame with SHA-256 before decode or cockpit
publication. `quant-market-data/dhan20.rs` strictly decodes the separate
12-byte, 332-byte-side depth protocol; adjacent same-frame/same-ID/same-feed
bid/ask sides alone make a 20-level book. Dhan documents the depth sequence
as "to be ignored", and it supplies no exchange book timestamp. The
`quant-depth` capture/transport pair timestamps received frames before
decode and fsyncs normalized sides/books in segmented QJ01, but discards the
**original depth bytes**, including malformed, unsupported and disconnect
frames. Replay preserves the normalized event order; it cannot recover those
original depth bytes. The cockpit historical cache handles pinned OHLCV as
NO TRADE and has no combined live quote/depth research view.

The live standard path is currently **RELIANCE-only** in both
`telemetry_local.rs` and `read_model.rs`'s Quote-17 subscription; its Python
preflight also hardcodes RELIANCE. Its `require_clock_health()` rejects
non-Windows systems, and the default Python executable is a Windows path.
The depth CLI supports three IDs in one subscription but defaults to two
reconnects and has no dated identity, independent clock or automatic market
cutoff. Thus a four-socket Mac pilot cannot simply be launched unchanged.
The existing `OBSERVE_ONLY` publisher and QP01/risk gates are not a combined
research pipeline and must not receive exploratory predictions.

## Ranking and cost corrections for a later study

The frozen `r15/max(v15,t)` rank is **past movement**, not a forecast.
`(r15-C)/max(v15,t)` may prefer lower quoted-cost names, but subtracting a
cost from a past return does not demonstrate favorable future movement or
probability calibration. Historical 1-minute `r15` is point-in-time invalid
until Dhan candle completion/publication evidence exists; using live
received observations avoids that particular candle assumption only when
their timestamp, gap and provenance checks pass. October 30 universe ranks
cannot be substituted for the historical universe in earlier training folds.

The plan's `C` uses current full spread as a **proxy for both entry and
future exit**, plus incremental depth cost and an assumed 40 bps. The future
exit spread/depth cannot be known at decision time, and the proxy is not an
executable cost. For later descriptive accounting, calculate an entry
reference buy VWAP across displayed asks and an exit reference sell VWAP
across later displayed bids. `buy VWAP - entry mid` plus `exit mid - sell
VWAP` already contains both half-spreads **and** depth sweep beyond best
quotes; add whole-share published fees and separately assumed slippage and
latency only once. Do not add a separate full spread or another sweep to
that VWAP cost. If no valid later exit book exists, net is unknown. The
quality pilot will measure the observed distribution of entry spreads and
static depth bounds; it will not fit or evaluate this rank.

## Minimum implementation before approval

1. Append a raw-depth QJ01 event without changing existing Postcard variant
   tags. Reuse the standard raw-frame SHA-256/verify pattern: retain exact
   WebSocket binary bytes, UTC receipt, feed session and frame ID **before**
   parsing; link decoded side/book events to that frame. Preserve malformed,
   unsupported and disconnect bytes, fail on a hash, append, sync, capacity or
   replay fault, and exclude raw bytes from cockpit JSON export. Focused
   synthetic software tests should prove raw-before-decoded order, replay
   hash rejection and malformed-frame retention.
2. Parameterize the existing one-instrument standard quote collector and
   Quote-17 JSON from a strict allowlist of the three dated IDs. Keep three
   isolated quote processes/journals and loopback ports; one depth process
   subscribes to the same three IDs. This reuses the current one-ID capture
   instead of building a new multi-ID standard-feed pipeline. Keep the main
   8765 cockpit DATA_ONLY and any collector telemetry OBSERVE_ONLY / NO TRADE.
3. Add a Mac preflight adapter to the existing quote gate and a matching
   depth gate: same-day original NSE MII ID/ISIN, normal-market/CAS flags,
   exchange calendar/notices and corporate-action check; fresh public Dhan
   master; private storage; independent `sntp` offset **and uncertainty**
   before, during and after capture. Require `abs(offset)+uncertainty <=
   100 ms`, record monotonic/wall-clock brackets and stop on a clock step or
   bound failure. Never backdate a later file. Supply a valid Mac Python path.
4. Enforce a common dated hard stop, zero automatic reconnects and a
   cross-process abort on any feed/auth/identity/clock/storage failure. The
   existing standard `--online` mode can span a session but currently fails
   Mac clock preflight; non-online mode caps capture at 60 seconds. The depth
   CLI needs the hard stop and zero-reconnect setting. Keep QP01 ledgers
   isolated and empty; verify zero approval writes. No broker transport is
   introduced.

Recheck the focused decoder, journal, raw-depth capture and loopback-only
transport tests after these fixes, then offline replay the exact QJ01 frames
and their hashes before any live approval. There is no need for a new data
pipeline, model or cockpit panel for this quality question.

## Bounded future capture proposal — not authorized

| Item | Exact proposed scope |
| --- | --- |
| Session | **Wednesday 2026-11-04, 09:30:00–15:14:00 IST**, ordinary continuous market only. The NSE 2026 holiday list does not name November 4, but same-day notices and security evidence still control. No automatic reschedule if a prerequisite fails. |
| IDs | `NSE_EQ/EQUITY`: HDFCBANK `1333`/`INE040A01034`, RELIANCE `2885`/`INE002A01018`, INFY `1594`/`INE009A01021` only. Verify all three again on November 4 in original NSE MII and a fresh public Dhan master; require normal-market `1` and CAS status known. Stop by 15:14 even if one stock is non-CAS. |
| Connections | Maximum **four simultaneous and four total authenticated WebSocket attempts**: three `wss://api-feed.dhan.co` Quote-17 sockets, one per ID, plus one `wss://depth-api-feed.dhan.co/twentydepth` Depth-23 socket with exactly these three IDs in one JSON message. Zero retry/reconnect/fallback. No REST/account/other instrument, Full-21, 200-level feed or order. |
| Preflight | Operator-confirmed active Data API entitlement and local valid token; exact dated NSE calendar/notices/identity/CAS and corporate-action check; independent Mac clock bracket meeting the 100-ms bound; private journals with at least **5 GiB free**; code commit, configuration, exact subscription JSON and source hashes pinned before any connection. Do not persist credential-bearing URLs or tokens. |
| Retention | Three owner-private standard raw QJ01 files, **64 MiB cap each**; one owner-private segmented depth QJ01, **64 MiB per segment / 4 GiB total / 512 MiB minimum free**. Retain exact frames, per-frame SHA-256, journal checksums/segment manifest, receipt and monotonic clock brackets, redacted handshake/disconnect metadata, frame IDs, session/date/identity evidence, source hashes, code/config and post-run replay audit until explicit disposition. No cloud copy or automatic deletion. |
| Stop/teardown | Stop all four on the first auth/subscription rejection, wrong ID, malformed/unsupported frame, 30-second quote or 45-second depth idle timeout, disconnect, clock step, failed offset bound, queue/disk/fsync/replay error, or 15:14 cutoff. Close sockets, sync/close journals, record final clock bracket and hash/replay counts, mark incomplete intervals unknown. Existing main cockpit stays DATA_ONLY / NO TRADE. |

The official [Dhan standard feed](https://dhanhq.co/docs/v2/live-market-feed/)
allows up to five sockets per user; the separate
[20-level depth contract](https://dhanhq.co/docs/v2/full-market-depth/) allows
50 IDs per depth socket and says its wire sequence is to be ignored. Four
connections leave only one slot for any other Dhan session; preflight must
check for active competing connections or abort. A successful subscription
alone is not evidence of delivered frames.

## What the pilot can decide

For each genuine same-frame, same-ID bid/ask depth book with `0 < bid < ask`,
measure quoted spread as `10,000*(ask-bid)/((ask+bid)/2)` bps and reported
level-1/level-20 displayed quantity. At fixed ₹100,000 midquote notional,
compute `floor(100000/mid)` shares, whether all shares fit within 20
displayed levels on **both** sides, and the static ask/bid VWAP sweep if they
do. Record book completeness, unpaired sides, crossed/locked cases, quote
and depth receipt interarrival times, 09:30/mid/near-close coverage per ID,
unknown gaps, and quote-to-book receive skew. Pair only within the same
instrument/continuous session with both sides from one depth frame and an
immediately preceding committed quote no older than 1 second; report skew
and exclusion counts rather than declaring exchange-time simultaneity.

This measures **quoted** spread, displayed liquidity at receipt and local
timing/coverage quality. It cannot establish exchange-to-client latency
(depth has no exchange timestamp), gap-free exchange history (the depth
sequence is explicitly ignorable), future exit cost, market impact, queue
position, hidden liquidity, partial fills, slippage or a 30/60-minute
predictive effect. One session provides three instrument-day observations,
not independent economic validation.

| Choice | Information gained | Decision |
| --- | --- | --- |
| Three-stock joint quote/depth quality pilot | First real spread/depth/coverage and local timing evidence at three high-turnover dated IDs; can falsify collector viability and narrow cost assumptions. No fill or signal proof. | **Most justified next experiment after fixes and separate approval.** |
| Proposed 24 historical one-minute POSTs | Multi-stock candle schema, source coverage and descriptive movement distribution on already-selected October dates. No spread, depth, publication timing or executable-cost evidence; October selection is survivor-biased. | Defer. |
| No further collection | Preserves funds and avoids operational work; leaves cost versus signal uncertainty unresolved. | Valid if the collector fixes/approval cost is not justified. |

Offline validation actually run for this review: the three symbols' 20 dated
NSE rows, medians, original ZIP/MII/master SHA-256 and unique October 30
ID/ISIN/CAS mapping rechecked; `quant-market-data` **11/11** decoder tests,
the QJ01 raw-standard SHA tamper test **1/1**, and `quant-depth` capture
**4/4** tests passed. These are synthetic software tests, not real feed
evidence. The required PowerShell context Merkle check could not run on this
Mac (`powershell: command not found`). The November 3 availability probe and
frozen November experiment were not rescheduled or modified.
