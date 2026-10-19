# RELIANCE six-month Dhan backfill plan (DATA_ONLY)

Status: **offline preparation only**. No new authenticated chart request has
been made. Dhan's [intraday API](https://docs.dhanhq.co/api/v2/historical-data/get-intraday-historical)
limits each request to 90 calendar days. The existing October 12–16 response
remains the original source for that week; its SHA-256 is
`9ad62caf5c889bae31f0709c61cb7b7b7ab7855285770a525669b22232db399d`.

| Inclusive start | Exclusive end | Days | Status |
| --- | --- | ---: | --- |
| 2026-04-01 | 2026-06-30 | 90 | New read-only request |
| 2026-06-30 | 2026-09-28 | 90 | New read-only request |
| 2026-09-28 | 2026-10-12 | 14 | New read-only request |
| 2026-10-12 | 2026-10-17 | 5 | Verified cached response, 1,800 rows |

All requests use `POST https://api.dhan.co/v2/charts/intraday` with
`securityId=2885`, `exchangeSegment=NSE_EQ`, `instrument=EQUITY`,
`interval="1"`, `oi=false`, and `fromDate`/`toDate` at `00:00:00` on the
listed boundaries. The locally saved
`data/historical/backfill-plan-2026-10-19.json` contains the exact payloads.
These windows cover April 1 through October 16 with zero calendar-day
overlap. The reader rejects response timestamps outside each request and
checks matching boundary timestamps for conflicting OHLCV values.

The cached response occupies **87,883 bytes**, or **48.82 bytes/bar**. At
that measured density and an illustrative 375 slots per calendar day, the
three new checkpoints would use about **3.55 MB**; quarantined originals
would roughly double that to **7.10 MB**, plus metadata and any failed
responses. This is a sizing scenario, not a byte cap or a trading-calendar
forecast. The April–October span crosses market-session regimes; the October
pilot calendar must not be applied to earlier dates.

## Offline plan and future authorized command

Run the dry run first; it verifies cached bytes, checksum, request metadata,
normalization provenance and retrieval time without looking up a token:

```bash
.venv/bin/python -m quant.apps.acquisition dry-run \
  --security-id 2885 --symbol RELIANCE \
  --start 2026-04-01 --end 2026-10-17 --interval 1 \
  --window 2026-04-01:2026-06-30 \
  --window 2026-06-30:2026-09-28 \
  --window 2026-09-28:2026-10-12 \
  --window 2026-10-12:2026-10-17 \
  --output-dir data/historical/raw \
  --master data/instruments/dhan-scrip-master-2026-10-18.csv \
  --master-manifest data/instruments/dhan-scrip-master-2026-10-18.manifest.json
```

**Do not run the following command without separate approval for its three
new authenticated requests.** Once approved, with a valid token configured
locally, the corresponding read-only command is:

```bash
.venv/bin/python -m quant.apps.acquisition backfill \
  --security-id 2885 --symbol RELIANCE \
  --start 2026-04-01 --end 2026-10-17 --interval 1 \
  --window 2026-04-01:2026-06-30 \
  --window 2026-06-30:2026-09-28 \
  --window 2026-09-28:2026-10-12 \
  --window 2026-10-12:2026-10-17 \
  --output-dir data/historical/raw \
  --master data/instruments/dhan-scrip-master-2026-10-18.csv \
  --master-manifest data/instruments/dhan-scrip-master-2026-10-18.manifest.json \
  --max-new-requests 3 \
  --subscription-confirmed --backfill-authorized --execute-read-only
```

The dated master must still satisfy the existing freshness check at execution;
if it expires, replace both `--master` and `--master-manifest` with a newly
retrieved public Dhan master and manifest. A new observation must never be
backdated. The command makes at most three new application-level POST attempts
per run, paces them at one per second and makes no order request. A failed
response remains in private quarantine. On interruption, rerun the dry run: completed
checkpoints are verified and skipped. A raw checkpoint missing its checksum
or retrieval metadata blocks reuse; restore it from its quarantined original
offline before resuming.

## Source contract and research hold

Maintain separate status for (1) original-byte integrity, (2) dated cash
instrument identity, (3) holiday/CAS session applicability, (4) timestamp
label and completed-bar availability, (5) intraday corporate-action policy,
and (6) research/model eligibility. The [pilot review](phase3b1-dhan-pilot.md)
documents the remaining uncertainties. Do not publish a research-eligible
Parquet dataset until the relevant date-specific evidence and Dhan semantics
are established. Provisional offline inspection may use the checksummed raw
files in a separate local workspace with assumptions recorded.

The operator can send Dhan support this inquiry, without credentials:

> For `POST /v2/charts/intraday`, `NSE_EQ` one-minute equity candles, please
> confirm: (1) Does `timestamp` label the bar's start or end? (2) When is the
> latest candle guaranteed complete? (3) Are closing-auction executions
> included, and if so how are they represented? (4) Are intraday OHLC prices
> adjusted for splits, bonuses or other corporate actions, including
> retrospective changes? (5) Is `securityId` the unchanged exchange security
> ID for historical `NSE_EQ` requests? Please provide the effective date or
> version of each answer.

Once the source contract is established, initial noise-aware research can
compare completed-bar volatility-normalized returns, time-of-day-normalized
relative volume, causal momentum versus mean-reversion state, one-sided EWMA
and Kalman features, and session/volatility-regime context. This plan neither
trains a model nor makes a profitability or trading claim.
