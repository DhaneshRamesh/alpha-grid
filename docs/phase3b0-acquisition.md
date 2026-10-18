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
Daily Parquet/Zstd files pack instruments by source/year;
intraday files use `source/interval/year/month/security_id` partitions.
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

The active Dhan-only workflow is in [the pilot runbook](phase3b1-dhan-pilot.md).
The local NSE importer remains available but is not being pursued.

Run a no-auth dry-run with a current master-verified security ID:

```bash
python -m quant.apps.acquisition dry-run --security-id '<VERIFIED-ID>' --symbol RELIANCE --start 2025-01-01 --end 2025-04-01 --interval 1
```

No raw licensed data belongs in Git (`data/` is ignored). Source-specific
acquisition is independent of the existing depth journal and paper ledger.

## Offline operator runbook

1. Freeze a registry JSON with `version` and `records`. Each record requires
   `instrument` (`venue`, `segment`, `security_id`, `symbol`, optional `isin`),
   `name`, `effective_from`, optional exclusive `effective_to`,
   `listing_status`, `source_manifest_version`, and optional
   `corporate_actions` (`effective_date`, `kind`, `source_reference`). Review
   historical mappings before import; unresolved identity aborts.
2. Freeze a universe JSON with `version`, `selection_asof`, `security_ids`,
   and a predeclared `selection_rule`. Selection date cannot follow the file's
   trading date. Freeze a calendar JSON with `version`, `holidays` ISO dates,
   and `special` sessions (`day`, `opens`, `closes`, `kind`). Supply an official
   complete session schedule for any real completeness claim.
3. Manually obtain one authorized NSE cash bhavcopy file and record its actual
   HTTPS source URL and download time. Check rights under the applicable NSE
   agreement; this code never downloads archives. Import one daily file:

```bash
python -m quant.apps.import_nse_archive \
  --archive data/incoming/bhavcopy.csv.zip --format UDIFF_CM \
  --source-url '<actual-HTTPS-file-URL>' \
  --downloaded-at '<actual-ISO-8601-time-with-offset>' \
  --registry data/config/registry.json --universe data/config/universe.json \
  --calendar data/config/calendar.json --dataset-id cm-2025-01-02-v1 \
  --code-commit "$(git rev-parse HEAD)" --output data/lake/cm-2025-01-02-v1
```

4. Inspect `manifest.json`, `verify_dataset`, sibling `*-status.json` and
   `*-quality.json`, and quality warnings. Hard failures abort publication. The one-day
   importer is intentionally bounded; multi-year import uses the streaming
   `write_dataset` API on pre-sorted, deduplicated bars. Do not interpret the
   example date or universe as an approved acquisition target.
5. The local read-only telemetry demo can load the status file with
   `--acquisition-status data/lake/cm-2025-01-02-v1-status.json`. The cockpit
   displays checksum verification and blocks depth-dependent model families.
   It never displays credentials. Without a status file the endpoint reports
   `UNVERIFIED` and `NOT_TRAINED`.

No authenticated Dhan backfill is invoked by either CLI command. A separately
authorized run would call `run_backfill(..., enabled=True)` with a locally
configured `DHAN_ACCESS_TOKEN`, then parse immutable response files and
validate entitlements, timestamp meaning, adjustment and quality before lake
publication. Do not log or store the token in a dataset.
`iter_saved_backfill(requests, paths)` verifies every saved SHA-256 and
streams one 90-day response window at a time, collapsing identical boundary
bars and rejecting conflicts. An interrupted sidecar write can be recovered
from a fully published, valid original response without another API call.

## Disabled 5–10 instrument live-depth pilot sizing (D6)

`LiveCaptureProfile` is disabled and DATA_ONLY. A future profile requires five
to ten preselected NSE cash IDs, independent standard/20-level feed monitoring,
QJ01, receive and supplied source clocks, reconnect/gap evidence, and session
integrity verification. No default securities are selected. Account
entitlements, actual payload semantics (snapshots versus deltas), peak
per-instrument rates, authentication validity, license, storage and host costs
must be measured and approved before capture. Aggregated depth does not reveal
individual order additions, cancellations, or exact queue position.

For transparent **assumption-only** sizing, the prior [Utho Bangalore
synthetic full-depth trial](../infra/benchmark/utho-bangalore-trial-20261014.md)
measured about 516 bytes/event for Rust QJ01 and 1,182 bytes/event for the
Python journal under its respective serialization and batching. It is not a
live Dhan rate sample. At an *assumed* combined peak of 100 events/s per
instrument across both feeds and 6.25 hours/session:

| Instruments | Assumed events/session | Rust raw/session at 516 B/event | Python raw/session at 1,182 B/event |
| ---: | ---: | ---: | ---: |
| 5 | 11.25 million | 5.805 GB | 13.298 GB |
| 10 | 22.5 million | 11.610 GB | 26.595 GB |

Twenty retained sessions with two copies would require about 232 GB/532 GB
for the five-instrument Rust/Python examples, before filesystem overhead,
indexes, research Parquet, backups and bursts. For ten instruments, double
those figures. These are deliberately not disk recommendations. The old
Bangalore synthetic run handled 10,000/s with Rust group commit and 1,000/s
with Python group commit, but did not include live decoding or feed behavior;
the actual live headroom is **UNKNOWN**. A 50 ms fsync target also leaves a
nonzero crash-loss window for accepted but uncommitted records.

Recompute with measured future peak rate and measured bytes/event:

```bash
python -m quant.apps.estimate_capture --instruments 5 \
  --assumed-peak-rate-per-instrument 100 --assumed-bytes-per-event 516 \
  --retention-sessions 20 --copies 2
```

Before any pilot, record open/mid/close feed counts and 20-level message
size across several sessions, compare accepted/kernel-written/durable and
recovered counts, inspect p99.9 fsync and queue tails, establish off-host
copy and integrity checks, and verify account data access and ongoing cost.
No real-data, subscription, alpha or live execution readiness claim exists.
