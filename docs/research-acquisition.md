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
