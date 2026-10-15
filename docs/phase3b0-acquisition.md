# Historical acquisition contract (DATA_ONLY)

This milestone is offline by default. The documented `quant.apps.acquisition`
command only plans requests. It never reads a credential or makes a network
connection. Authenticated collection requires a separate approval and an
explicit `run_backfill(..., enabled=True)` call; no trading API is present.

## Sources and terms checked 2026-10-15

- [Dhan historical charts](https://dhanhq.co/docs/v2/historical-data/):
  `POST /v2/charts/historical` returns daily OHLCV with an exclusive `toDate`;
  `POST /v2/charts/intraday` supports 1, 5, 15, 25 and 60 minute candles,
  at most 90 days per request, and says up to five years for active
  instruments. Availability and subscription entitlement remain unverified.
  Dhan's example uses epoch seconds; intraday timestamp meaning (bar start or
  bar end), adjustment status, and clock precision need empirical validation.
- [NSE UDiFF format catalogue](https://www.nseindia.com/static/resources/forms-formats-members)
  and [public report listing](https://www.nseindia.com/all-reports) identify
  CM UDiFF bhavcopy archives. Local import needs the exact file format/schema
  version and a recorded source URL. This is daily exchange data, never ticks.
- [NSE data use policy](https://www.nseindia.com/static/market-data/nse-data-policy)
  restricts use and redistribution according to applicable agreements.
  [Historical data subscriptions](https://www.nseindia.com/static/market-data/eod-historical-data-subscription)
  are separate. Local import is supported; automated archive fetching and
  public redistribution remain disabled pending rights review.

## Typed contract

`ohlcv-v1` has NSE cash identity (`venue`, `segment`, `security_id`, symbol,
optional ISIN), inclusive trading date, interval minutes (null for daily),
optional UTC source-timestamp nanoseconds (bar-start/end meaning unverified),
price in exact INR nanounits, integer
volume, source name and immutable source SHA-256. Optional turnover and
delivery quantity retain exchange fields. Historical bars have no quote,
spread, depth, order event, execution fill or QJ01/QP01 decision claim.

Request windows are half-open `[start, end)` and at most 90 calendar days.
Plans contain only public request metadata and an approximate bar upper bound.
The initial universe is explicitly supplied by the operator; there is no
preselected performance-based universe. The registry later preserves its
effective-date identity and source manifest.

The local NSE importer accepts declared `UDIFF_CM` or `LEGACY_CM` CSV/ZIP
files and rejects unknown column layouts. UDiFF field names are based on the
NSE published catalogue; a real archive from the current release still needs
schema validation before any bulk import. The caller supplies an as-of
identity resolver because a ticker or ISIN alone cannot establish Dhan's
historical security ID. Both source URL and download time are explicit
metadata; no archive is fetched by this code.

## Point-in-time quality contract

Registry records have inclusive `effective_from`, exclusive `effective_to`,
the source manifest version, listing status, and referenced corporate actions.
Unresolved or overlapping mappings stop identity resolution. A versioned
universe records its selection date and predeclared selection rule.

The injectable NSE calendar defaults to the conventional 09:15–15:30 IST
weekday schedule, with explicit holiday and special-session overrides. This
default is **not** proof of a complete historical holiday calendar: load an
official dated calendar before claiming complete coverage. Quality audits
check identity, duplicates, timestamp order, session boundaries, tick
precision, large price gaps, missing sessions and (only when timestamp meaning
is verified) missing intraday bars. Missing sessions are `UNKNOWN` unless
separate evidence marks provider outage or confirmed inactivity. Price gaps
around recorded corporate actions are warnings until an adjustment source is
verified. No missing observations are synthesized.

## Storage and replay

`stage_raw_file` places original bytes under
`data/raw/<source>/<SHA-256>` and checks existing copies before reuse.
`write_dataset` consumes pre-sorted, deduplicated bars in bounded batches.
Its Parquet/Zstd paths are
`normalized/source=<source>/interval=<daily|Nm>/year=YYYY/month=MM/security_id=<id>/part-N.parquet`.
The publication directory is new and appears only after all parts and the
manifest are written. `verify_dataset` checks every part's hash and the
canonical manifest digest. A byte-identical input produces the same dataset
SHA even if the creation time differs. Source files and lake outputs stay
under ignored `data/`; Git contains only code and synthetic tests.

The manifest records source hashes, date coverage, universe, calendar,
adjustment policy, quality exclusions, code commit, acquisition mode and
partitions. Raw sources remain the evidence of record; Parquet is replayable.

## Separate candle research pathway

`ohlcv_research.py` emits candle-only feature/label Parquet and a lineage
manifest. It accepts intraday candles only after their source timestamp is
externally verified as bar start. A decision occurs after the bar completes
plus a declared publication lag; the feature window contains only completed
past candles. Daily candles require a verified post-close publication time
callback and an injected session calendar. Future candles appear only in
labels. If one future candle touches profit and stop barriers, its first
crossing is `AMBIGUOUS` and the label is unusable for first-hit training.
Missing forward bars are censored. These are market ranges, never executable
fills or tick-level event paths.

Research manifests count instruments, sessions, censoring, outcomes and a
conservative non-overlap effective-sample estimate. Regime coverage,
corporate-action certainty and train/test separation remain unknown or not
performed. The existing depth sufficiency gate explicitly rejects an OHLCV
modality. Neither imported historical candles nor this research dataset
promotes a model or changes paper decisions.

Run a no-auth dry-run:

```bash
python -m quant.apps.acquisition --security-id 1333 --symbol RELIANCE --start 2025-01-01 --end 2025-04-01 --interval 1
```

No raw licensed data belongs in Git (`data/` is ignored). Source-specific
acquisition is independent of the existing depth journal and paper ledger.
