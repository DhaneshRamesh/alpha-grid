# RELIANCE May–October 2026 Dhan minute archive (Mac)

Status on 2026-11-01 IST: **source bytes verified; descriptive research only;
predictive training blocked by the existing evidence gates.** The owner
authorized exactly two new authenticated, no-retry DhanHQ historical POSTs,
confirmed active Data API entitlement, and defined the training objective as
testing whether observable conditions predict economically meaningful movement
over the next 30 minutes, 60 minutes or remaining session. Both requests
completed with HTTP 200. No Dhan request remains authorized by that two-POST
approval.

## Saved source manifest

All files below are owner-private and Git-ignored. Each original `.json` has
adjacent `.sha256` and `.meta.json` sidecars, plus an exact-byte quarantined
original and receipt metadata in `.quarantine/`. The existing
`verify_saved_response` accepted all three raw/sidecar sets. The request body
for each uses `NSE_EQ`/`EQUITY`/security ID `2885`, `interval="1"`, `oi=false`,
and the listed IST midnight boundaries to
`POST https://api.dhan.co/v2/charts/intraday`.

| Inclusive start / exclusive end | HTTP | Original bytes | SHA-256 | Candles | Sessions | Receipt UTC |
| --- | ---: | ---: | --- | ---: | ---: | --- |
| 2026-05-01 / 2026-07-30 | 200 | 1,113,650 | `62e76b108fad3fde0c49041e154796c5389f055040b1b229839d533664de512b` | 22,875 | 61 | 2026-11-01 14:19:54.592572 |
| 2026-07-30 / 2026-10-26 | 200 | 1,033,635 | `fbe4eac254fa7e55c4e167eae9fe9c9a36597f8073c58196b135586ca8455838` | 21,270 | 59 | 2026-11-01 14:19:56.534521 |
| 2026-10-26 / 2026-10-31, previously saved | 200 | 87,694 | `a4c61e3d5ad8ce3b9bf78317ac73ff15502bcefa52bd6a09c3771b866d2d8653` | 1,800 | 5 | 2026-11-01 14:06:03.835467 |

The two new response paths are
`data/historical/raw-dhan-macos-2026-11-01/2885-1-2026-05-01-2026-07-30.json`
and
`data/historical/raw-dhan-macos-2026-11-01/2885-1-2026-07-30-2026-10-26.json`.
The saved October path ends `2885-1-2026-10-26-2026-10-31.json`.
The public Dhan master retrieved on November 1 remains separately preserved
at `data/instruments/dhan-scrip-master-2026-11-01.csv` with its manifest and
SHA-256 `ad00a05b69606346a5e796bef8715a981242eab8063d615294ce8b3e54981a6a`.
It proves current identity, not identity continuity across the archive.

## Offline verification

- Reconciled total: **45,945 source candles on 125 independent dates**. There
  are 63 dates with 375 bars, 09:15–15:29 IST, and 62 with 360 bars,
  09:15–15:14 IST. No date has an internal minute gap. The shorter regime
  starts in August, consistent with NSE's general closing-auction change; it
  does not by itself verify RELIANCE's dated CAS classification.
- Each response uses seconds-valued timestamps. Array lengths and schema
  passed the shared parser. There are no out-of-order pairs, timestamp
  duplicates, out-of-window rows or cross-window boundary conflicts. Original
  quarantine bytes equal the published raw bytes and the sidecar hashes.
- The 125 source dates exactly match weekdays from May 1 through October 30
  after removing the six holidays listed on NSE's 2026 equity calendar in that
  span (May 1, May 28, June 26, September 14, October 2 and October 20).
  The first observed session is May 4 and the last October 30.
- [Dhan's historical API documentation](https://dhanhq.co/docs/v2/historical-data/)
  specifies up to 90 days per intraday request and calls `timestamp` an epoch
  value, without establishing when a bar is complete or published. The
  [NSE equity holiday schedule](https://www.nseindia.com/resources/exchange-communication-holidays)
  and [closing-auction archive](https://www.nseindia.com/static/reports/closing-auction-session-historical-data)
  support dates and the general regime, not the provider's timestamp semantics.

## Training decision

The existing forward-observation generator requires dataset-bound evidence
that timestamps label bar starts and candles are complete at bar end, an
explicitly verified session for each date including post-August CAS
applicability, and a verified dated instrument mapping. The current Mac archive
has none of those full-range proofs. The current registry and calendar cover
only October 12–16, and the public November 1 Dhan master cannot be
backdated. The corporate-action adjustment policy is also unresolved.
The dated NSE security-master originals referenced by that local configuration
are not present on this Mac for independent rehashing.

Therefore the 30-minute, 60-minute and remaining-session objective is **not
eligible for causal training or an economic-edge claim**. No model was fitted,
registered or connected to the cockpit; no future OHLCV was presented as a
quote, tick or executable fill. The frozen November experiment and all order
paths remain unchanged. The next evidence step is a dated instrument and CAS
review for the full span plus a provider contract or prospective observation
for candle completion/publication timing. A separate, predeclared training and
holdout protocol can then use these exact source hashes.

The proposed protocol, after those gates pass, keeps May–July's 63 sessions
for fitting, August–September's 42 for model selection and October's 20 as an
untouched chronological holdout. Each as-of feature must be computed only
from bars independently known to be complete and available by that decision
time. The three labels are separate 30-minute, 60-minute and remaining
continuous-session close-to-close movements; labels crossing a session close
are ineligible. A price-movement threshold and an independently sourced cost
model must be fixed before fitting to define “economically meaningful.”
Session-level evaluation and a simple no-signal baseline must accompany any
model result. The historical OHLCV paths cannot establish executable fills.
