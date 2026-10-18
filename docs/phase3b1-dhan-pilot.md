# Dhan-only first historical pilot (DATA_ONLY)

Status on 2026-10-18: **subscription INACTIVE per operator; authenticated
pilot NOT AUTHORIZED**. No historical chart call has been sent. The NSE
importer remains available but is not being used in this acquisition phase.

## Operator activation

1. Sign in at [Dhan Web](https://web.dhan.co/) yourself. Go to **My Profile
   → DhanHQ Trading APIs**. Dhan's [support page](https://dhan.co/support/platforms/dhanhq-api/how-can-i-access-live-market-data-through-dhan/)
   identifies this as the place to manage and check the Data API subscription.
   Review the checkout total, applicable tax, balance, renewal and cancellation
   terms; activate the subscription yourself only if you choose to.
   [Dhan states](https://dhan.co/support/platforms/dhanhq-api/how-does-the-dhanhq-data-api-subscription-work/)
   ₹499 plus applicable taxes, renewing every 30 days. The actual checkout
   total is **UNKNOWN** until shown in Dhan Web.
2. Confirm **Data API Active** and note validity and renewal date. A trading
   account or free Trading API access does not establish Data API entitlement.
3. In **My Profile → Access DhanHQ APIs**, generate a manual **Access Token**.
   [Dhan documents](https://dhanhq.co/docs/v2/authentication/) 24-hour
   validity. Put it only in the local `DHAN_ACCESS_TOKEN` environment
   variable, never in chat, shell history, Git or logs. No API key/secret or
   order API is used here.
4. Separately confirm the active subscription and authorize the exact
   one-instrument read-only pilot. No authenticated command runs before then.

## Current identity and proposed request

The unauthenticated [Dhan compact instrument master](https://dhanhq.co/docs/v2/instruments/)
was fetched from `https://images.dhan.co/api-data/api-scrip-master.csv`
on 2026-10-18 at 14:51 UTC. The ignored local file
`data/instruments/dhan-scrip-master-2026-10-18.csv` has SHA-256
`3fb7fb8db91ebe799c4f0715997e1f79bf0e68afa71b121cb77a29346017faf9`.
Its unique `NSE/E/EQUITY/EQ/RELIANCE` row identifies **2885**, RELIANCE
INDUSTRIES LTD. This is current listing evidence, not proof of historical ID
continuity. The documentation example ID 1333 is not used.

Proposed pilot: **one POST** to
`https://api.dhan.co/v2/charts/intraday`, `NSE_EQ`, `EQUITY`,
security ID `2885`, interval `1`, half-open dates
`[2026-10-12, 2026-10-17)`. This is a completed Monday–Friday window;
holiday and special-session evidence remains unverified. Raw JSON, SHA-256
and token-free retrieval metadata will be saved under
`data/historical/raw`. The dry-run estimates at most **1,875**
calendar-day one-minute slots and an illustrative **240,000 bytes** at an
assumed 128 bytes/bar. Actual response and Parquet sizes are unknown.
The code paces at one request/second and uses bounded retries;
[Dhan documents](https://dhanhq.co/docs/v2/) five Data API requests/second.

These **offline commands have run** without credentials or network access:

```bash
.venv/bin/python -m quant.apps.acquisition dry-run \
  --security-id 2885 --symbol RELIANCE \
  --start 2026-10-12 --end 2026-10-17 --interval 1 \
  --master data/instruments/dhan-scrip-master-2026-10-18.csv \
  --master-manifest data/instruments/dhan-scrip-master-2026-10-18.manifest.json
.venv/bin/python -m quant.apps.acquisition verify-config \
  --security-id 2885 --symbol RELIANCE \
  --start 2026-10-12 --end 2026-10-17 --interval 1 \
  --master data/instruments/dhan-scrip-master-2026-10-18.csv \
  --master-manifest data/instruments/dhan-scrip-master-2026-10-18.manifest.json
```

`verify-config` only reports token presence; it cannot prove subscription
activity offline. The following **prepared command has NOT run**. It requires
the active subscription, an unexpired local token and fresh explicit
authorization. Refresh the public master and its manifest if older than seven
days at that point.

```bash
.venv/bin/python -m quant.apps.acquisition pilot \
  --security-id 2885 --symbol RELIANCE \
  --start 2026-10-12 --end 2026-10-17 --interval 1 \
  --master data/instruments/dhan-scrip-master-2026-10-18.csv \
  --master-manifest data/instruments/dhan-scrip-master-2026-10-18.manifest.json \
  --subscription-confirmed --pilot-authorized --execute-read-only
```

## Verification and research boundary

After an approved response, `verify` checks original bytes, SHA, request
metadata, OHLCV array lengths, increasing timestamps and session/identity
quality using an independently sourced `data/config/calendar.json` and
dated `data/config/registry.json`. The current chart response schema has no
independent instrument ID or interval field; metadata binds it to the
request, while timestamp bar-start/bar-end meaning and adjustment policy
remain **UNVERIFIED**. A provisional weekday calendar is not holiday proof.

```bash
.venv/bin/python -m quant.apps.acquisition verify \
  --security-id 2885 --symbol RELIANCE \
  --start 2026-10-12 --end 2026-10-17 --interval 1 \
  --master data/instruments/dhan-scrip-master-2026-10-18.csv \
  --master-manifest data/instruments/dhan-scrip-master-2026-10-18.manifest.json \
  --registry data/config/registry.json --calendar data/config/calendar.json
.venv/bin/python -m quant.apps.acquisition import \
  --security-id 2885 --symbol RELIANCE \
  --start 2026-10-12 --end 2026-10-17 --interval 1 \
  --master data/instruments/dhan-scrip-master-2026-10-18.csv \
  --master-manifest data/instruments/dhan-scrip-master-2026-10-18.manifest.json \
  --registry data/config/registry.json --calendar data/config/calendar.json \
  --code-commit "$(git rev-parse HEAD)"
.venv/bin/python -m quant.apps.acquisition report \
  --security-id 2885 --symbol RELIANCE \
  --start 2026-10-12 --end 2026-10-17 --interval 1
```

`import` preserves content-addressed original bytes and publishes
Parquet/Zstd with lineage and quality sidecars. A second independent output
directory can reproduce canonical hashes and normalized rows. Only completed
minute candles can be research features; future candles are labels and
same-bar barrier ordering remains AMBIGUOUS. Minute candles are not tick
trades, 20-level depth, broker fills or millisecond trajectories. No model
is trained or promoted by acquisition.

Later, with separate approval, five selected equities over 6–12 months
would need roughly **15–25** existing 90-day request windows. A
calendar-day upper bound is **341,250–684,375** minute slots and roughly
**44–88 MB** at the illustrative 128 bytes/bar; actual rows, storage,
missing sessions, processing cost and subscription charges must be
re-estimated after the pilot. Valid saved windows are reused. A future
read-only live-depth capture would reuse the Rust standard and 20-level
decoders; no live capture, Utho change or order functionality starts here.
