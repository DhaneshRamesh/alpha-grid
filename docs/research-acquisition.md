# Opt-in read-only market-data acquisition plan

No acquisition begins in this milestone. Before any authenticated session,
obtain separate user approval for the instrument list, subscription, host,
spend, retention and teardown plan. Keep the platform DATA_ONLY; do not enable
broker order routes or modify Dhan account settings from this runbook.

1. Select an initial NSE cash-equity universe using observed liquidity,
   spreads, depth completeness and security-ID mapping. Record exact venue,
   exchange segment and security ID; freeze a versioned instrument manifest.
   Exclude corporate-action ambiguity until adjusted reference data exists.
2. Capture the standard quote feed and 20-level depth feed concurrently in
   QJ01 with receive nanoseconds, supplied source timestamps, feed session,
   sequence/packet metadata and quality events. Confirm actual payload
   semantics, whether snapshots or deltas, and clock precision. Aggregated
   depth cannot recover individual order events or actual queue position.
3. Use an independently maintained NSE session calendar, including holidays
   and special sessions. Record open/mid/close coverage and a gap/reconnect
   audit per instrument. Exclude disconnections, timestamp regressions, stale
   spans and malformed records according to a predeclared policy; never fill
   silent intervals with synthetic prices.
4. Retain raw QJ01 as the source of truth, with monitored fsync, checksums,
   local capacity and verified off-host copy. Produce replayable, versioned
   Parquet research partitions and record source checksums, code version,
   configuration, calendars and quality exclusions. Keep PII/credentials out
   of datasets.
5. Size storage from measured event rate and record-size distributions, not
   the synthetic fixture. Illustration only: at 100–10,000 events/s for a
   6.25-hour session, 2.25–225 million events/day; at an assumed 1 KiB raw
   record, 2.25–225 GB/day before archive redundancy and Parquet. Measure the
   actual peak, compressibility and journal amplification before choosing
   disks or retention. Alert well before free capacity is exhausted.
6. Obtain read-only broker fee/contract-note evidence for the applicable cash
   equity product, actual tick/spread/impact distributions and validated
   timestamp synchronization before interpreting simulated net utility.
7. Freeze a chronological study protocol and final holdout before inspecting
   model performance. Adequacy depends on independent session count,
   instrument/regime coverage, effective sample size after overlapping
   horizons, both favorable/adverse event counts, censoring, and confidence
   interval precision. No arbitrary number of days automatically suffices.

The opt-in collector should stop visibly on a journal error, queue overflow or
unexplained feed gap and preserve the exact raw evidence for audit. Capturing
real data does not authorize model promotion, paper-policy changes or orders.

## Next-phase evidence gate (reviewed 2026-10-26)

The owner reports an active DhanHQ Pro Traders Data API subscription. Account
entitlement and validity have not been independently checked in this review;
there was no authenticated profile or market-data request. Dhan's published
[historical-chart contract](https://dhanhq.co/docs/v2/historical-data/) covers
one-minute OHLCV with epoch timestamps and a 90-day request window, but does
not establish when a current candle becomes complete. The published
[live-feed contract](https://dhanhq.co/docs/v2/live-market-feed/) describes
binary WebSocket quote/trade packets. The
[full-depth contract](https://dhanhq.co/docs/v2/full-market-depth/) describes
separate 20-level and 200-level WebSockets: at most 50 instruments per
20-level connection and one per 200-level connection. Depth side packets lack
an exchange timestamp. A [Dhan representative's October 2026 statement](https://madefortrade.in/t/data-api-plans-changed-200-market-depth-available-only-in-new-plan/94719)
announces Market Movers in a phased Pro Traders rollout. A stable, public
Market Movers request/response contract was not independently located, so it
must not be wired into ranking yet.

The previous training-pass approval capped *new historical-candle requests* at
20 but did not specify instruments, dates or windows. None of those 20 is
documented as spent. The older RELIANCE April-October backfill is a distinct
proposal that explicitly requires separate approval for three requests. A
numeric cap alone does not authorize choosing those windows, the prospective
October 27 availability probe, a profile call or a WebSocket subscription.
Thus this review made zero Dhan requests.

The only currently accessible authentic Dhan chart checkpoint is the October
15-16 RELIANCE response: 35,261 original bytes, SHA-256
`bcace97b4dfd1b1cbfe10481b4718b770368a50c5264fb1f61a50f9c325d9861`,
720 one-minute rows, 360 on each date. The existing saved-response verifier
rechecked its bytes, digest, payload and retrieval timestamp offline. This is
source-integrity-backed *exploratory coverage*, one instrument and two
sessions. Historical Dhan identity continuity, intraday corporate-action
handling and candle completion remain unverified. There is no eligible
30m/60m/remaining-session forward distribution, net-edge estimate or
production recommendation. The cockpit must continue to show NO TRADE.

### First price/volume question, declared before any eligible test

The motivated question is whether positive trailing momentum accompanied by
unusually high volume precedes a better **after-cost** 30-minute, 60-minute
or remaining-session path than momentum alone. Before evaluating it, freeze
the dated NSE cash-equity ID universe, source hashes, session/CAS calendar,
corporate-action policy, candle-availability evidence, cost schedule, exact
training and untouched test dates in a separate experiment manifest. Do not
reuse the frozen RELIANCE November holdout. This is a question and analysis
procedure, not a trained signal or stock recommendation.

- At a decision time established by verified source publication, use only
  completed earlier 1-minute bars. Baseline A is positive trailing 15-minute
  close-to-close momentum. Candidate B adds a current five-completed-minute
  volume sum at least twice the median for the same clock bucket across the
  prior 20 eligible sessions for that instrument. Require complete lookback;
  missing bars, identity changes or unknown adjustments give NO TRADE. Limit
  to the first qualifying observation per instrument per session so later
  same-session triggers do not masquerade as independent trials.
- Reuse the existing forward-observation generator's signed close outcome,
  favorable/adverse candle excursions, time-to-extreme, maturity and censoring
  for 30m, 60m and remaining continuous session. Auction or session-crossing
  windows are excluded. Candle highs/lows are research ranges, not attainable
  fills; same-bar stop/target ordering is unknown. Keep the simple momentum
  baseline and no-trade baseline beside B; do not optimize thresholds on the
  test period.
- Group all instruments on each exchange day into one fold unit. Reuse the
  chronological walk-forward splitter with label-horizon purging and at least
  a one-calendar-day embargo between fit/selection and later test decisions.
  Aggregate by session before using the existing session bootstrap; show
  candidate counts, censoring, down moves, uncertainty and regime coverage.
  No edge is actionable until the lower uncertainty bound exceeds *conservative*
  round-trip fees, observed spread, slippage and an adverse-cost scenario.
  With no genuine cost or timing evidence, net edge is unknown and NO TRADE.

### Prospective depth question

After separate live-feed approval, start with a small, frozen NSE cash-equity
ID set and the existing Rust standard-feed decoder, 20-level decoder,
bounded capture and QJ01 journal. Stamp each received frame before parsing,
record reconnects/gaps and clock-offset evidence, and preserve original frame
bytes and checksums alongside normalized QJ01 records before treating the
prospective data as immutable source evidence. The current QJ01 depth events
contain normalized sides/books, not the original WebSocket frame bytes; this
raw-frame evidence gap must be closed before a source-integrity claim. Pair
quote and depth observations only when their receive-time uncertainty and
session IDs permit it. Defer 200-level capture until 20-level coverage and a
predeclared comparison against price/volume-only and standard-feed baselines
show a measurable incremental benefit. No depth observation establishes
individual queue position or executable fills.
