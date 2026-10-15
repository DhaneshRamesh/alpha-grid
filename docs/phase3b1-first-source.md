# First genuine market dataset checkpoint (DATA_ONLY)

Status on 2026-10-16: **WAITING_FOR_REAL_SOURCE**. No authorized NSE cash
bhavcopy was present in the repository's ignored data directory or the
operator's Downloads directory. Existing CSV fixtures and Utho synthetic
captures are not exchange observations. No archive was downloaded and no
authenticated Dhan request was made.

## Operator's next action

1. Obtain **one** authorized "CM-UDiFF Common Bhavcopy Final (zip)" from
   [NSE All Reports](https://www.nseindia.com/all-reports), subject to the
   applicable [NSE data use policy](https://www.nseindia.com/static/market-data/nse-data-policy).
   Save the untouched ZIP at `data/incoming/<actual-filename>.csv.zip`.
   Record the **actual file URL**, actual download time with timezone, and
   trading date. The reports listing URL is not a substitute for the file URL.
   Share the file and provenance with the auditor; do not share credentials.
2. Compare the ZIP member's **actual** headers with the published CM UDiFF
   [format catalogue](https://www.nseindia.com/static/resources/forms-formats-members).
   The current importer's declared UDiFF names are a contract to test, not a
   verified claim about this particular release. Check date, EQ series, ISIN,
   price units, volume, turnover and delivery semantics.
3. Create `data/config/registry.json`, `universe.json`, and `calendar.json`
   from dated, independently sourced evidence as described in
   [the acquisition runbook](phase3b0-acquisition.md). A present-day Dhan
   security master does not prove historical security ID continuity. Record
   unresolved mappings and corporate actions; an incomplete holiday or
   special-session calendar remains UNVERIFIED.

The data directory is ignored by Git. Preserve the original file unchanged.
These commands operate locally and make no network requests:

```bash
shasum -a 256 'data/incoming/<actual-filename>.csv.zip'
.venv/bin/python -m quant.apps.import_nse_archive \
  --archive 'data/incoming/<actual-filename>.csv.zip' --format UDIFF_CM \
  --source-url '<actual-HTTPS-file-URL>' \
  --downloaded-at '<actual-ISO-8601-time-with-offset>' \
  --registry data/config/registry.json --universe data/config/universe.json \
  --calendar data/config/calendar.json --dataset-id 'cm-<YYYY-MM-DD>-v1' \
  --code-commit "$(git rev-parse HEAD)" \
  --output 'data/lake/cm-<YYYY-MM-DD>-v1' --raw-root data/raw
```

Inspect the immutable raw copy at `data/raw/NSE_UDIFF_CM/<SHA-256>`, the
dataset's `manifest.json`, and sibling `*-quality.json` and
`*-status.json`. The import aborts on hard quality errors. Source bytes and
Parquet hashes are distinct from **authenticity**, licensing, calendar and
mapping verification. These remain UNVERIFIED until the evidence is reviewed.
The local status never calls a synthetic file genuine.

## Independent reproducibility audit

Import the same untouched source and exact config into a second new output
directory with the **same dataset ID and code commit**. Keep the two outputs
independent; do not copy a Parquet tree. Compare canonical `dataset_sha256`,
source SHA-256, partition checksums and quality issue lists. Read both Parquet
trees and compare sorted normalized rows, including integer price units,
volumes, dates and identities. A retry to an existing output independently
rebuilds a temporary candidate and rejects a changed canonical hash. If
publication stopped after the dataset appeared but before status/quality
sidecars, rerunning the same command safely recreates them.

## Incremental sessions and coverage

After the first archive passes the independent audit, import **one file per
trading date** with a unique `cm-<YYYY-MM-DD>-v1` output directory. Each
immutable directory is a session partition containing its own Parquet files,
source manifest and quality report; the per-session Parquet writer still packs
instruments together. Duplicate dates in a coverage range fail. Repeated
same-source imports to the same directory verify the full canonical digest.
Preserve each source URL, download time and SHA; never overwrite an original.
Compare daily `*-quality.json` issue counts and codes as a trend, with
corporate-action and identity changes reviewed separately.

```bash
.venv/bin/python -m quant.apps.session_coverage \
  --lake-root data/lake --raw-root data/raw \
  --calendar data/config/calendar.json --registry data/config/registry.json \
  --universe data/config/universe.json \
  --start '<YYYY-MM-DD>' --end '<EXCLUSIVE-YYYY-MM-DD>'
```

Coverage reports `imported` (local dataset and raw checksums intact),
`unverified` (dataset exists but original/source provenance is missing),
`ineligible` (point-in-time identity or universe selection invalid), and
`missing` (expected session without a dataset). These are technical
categories, **not** proof that an imported source is genuine. The injectable
calendar is provisional until official dated holiday and special-session
evidence is supplied. A partial staging directory is ignored; resume by
rerunning the same import. The date range is half-open.

## Disabled Dhan historical pilot

After the user confirms an active Data API entitlement and cost, approves
one historically valid NSE equity security ID, a one-session date window and
authenticated read-only access, run only the existing offline planner first:

```bash
.venv/bin/python -m quant.apps.acquisition \
  --security-id '<APPROVED-ID>' --symbol '<APPROVED-SYMBOL>' \
  --start '<YYYY-MM-DD>' --end '<NEXT-YYYY-MM-DD>' --interval 1 \
  --output-dir data/historical/raw
```

The planner does not read a token or contact Dhan. The actual request remains
disabled. Before enabling it in a separately approved task, check account
subscription and charges, token validity **locally**, permitted use, API
limits, point-in-time identity and session dates. Dhan's
[historical API](https://dhanhq.co/docs/v2/historical-data/) documents
one-minute candles and up to 90 days per request;
[Dhan's API errors](https://dhanhq.co/docs/v2/annexure/) distinguish missing
Data API access from authentication errors. A trading account alone proves
neither entitlement nor an active subscription.

For the later approved single request, preserve raw response bytes and SHA,
verify response arrays and timestamp timezone/bar-start-or-end meaning
against an independently known session, check adjustment policy and OHLCV
units, then convert through the existing OHLCV Parquet path. Keep bar-start
semantics UNVERIFIED until measured. Daily bars support daily studies;
one-minute bars support minute studies. Neither supplies historical 20-level
depth, individual order events, or millisecond fill paths. Features use only
completed bars and ambiguous same-bar barrier order stays AMBIGUOUS. No
dataset automatically trains or promotes a model.
