# RELIANCE 30m/60m/remaining-session research readiness

Update, 2026-11-02 IST: the owner authorized **offline exploratory training**
under explicit 60/120/300-second post-close availability assumptions. The
earlier NO TRAINING decision below is a dated state, superseded only for this
isolated exploratory work. Its source-timing and production gates remain
closed. Results are in `docs/reliance-assumed-timing-exploratory.md`. The
November 3 probe is still NO GO; its current code is eight slots, not the
older 14-slot proposal below.

Status, 2026-11-01 IST: **EXPLORATORY / NOT CAUSALLY ELIGIBLE. NO TRAINING / NO TRADE.**
This report does not alter the frozen November 15-minute experiment.

## Source and evidence

The Mac has three authentic, private Dhan `POST /v2/charts/intraday`
responses for May 1–October 30, 2026, under
`data/historical/raw-dhan-macos-2026-11-01/`. The two most recent authorized
requests both returned HTTP 200. The third October 26–30 response was reused,
not requested again. Exact original bytes, request parameters, SHA-256,
receipts and parser checks are in [the six-month audit](reliance-six-month-mac-audit.md).
The October 15–16 Windows archive is absent on this Mac. No prospective
candle-availability capture or frozen November model artifact was found in
the local ignored data inspected for this task.

The saved arrays contain 45,945 one-minute candles on 125 dates. All source
dates match weekdays less [NSE's 2026 equity holidays](https://www.nseindia.com/resources/exchange-communication-holidays).
Each source date is contiguous from 09:15 IST through either 15:29 (63
pre-August sessions) or 15:14 (62 later sessions), with no auction extension
used. This is source coverage, not independent proof that RELIANCE was
CAS-eligible on each later date. The local dated NSE identity/CAS registry
and calendar cover October 15–16 only. The November 1 Dhan master verifies
current, not six-month historical, identity. Corporate-action treatment and
Dhan adjustment policy remain unverified.
The original dated NSE security-master files cited by the October 15–16
configuration are absent on this Mac, so those two flags were not rehashed
in the later independent timing review.

[Dhan's historical API](https://dhanhq.co/docs/v2/historical-data/) documents
an epoch `timestamp` but does not establish whether it labels bar start or
end, when a candle first appears, when its OHLCV is complete, or whether
later corrections can occur. Historical retrieval in November cannot answer
those questions. The existing October 27 prospective probe was never
captured: it has only a fixed plan, synthetic tests and a previously saved
HTTP 401 from a separate historical request. No local clock-offset, network
latency or request/response bracket evidence exists for a one-minute candle
near its putative decision time. The Dhan response `Date` header is a receipt
diagnostic, not a publication timestamp.

## Descriptive source paths

Reproduce from the pinned raw evidence with
`PYTHONPATH=src .venv/bin/python -B -m quant.apps.descriptive_dhan_opportunities
--output data/historical/research/NEW-NAME.json` (output is private,
new-only). The current [machine-readable result](../data/historical/research/reliance-may-oct-2026-descriptive-v2.json)
is Git-ignored. The calculation uses source order and intact minute windows;
30m/60m paths stop at the same continuous-session end, and the remaining
path ends at that final source candle. Entry and exit are reference closes.
Future candle highs/lows give long favorable/adverse *ranges* with unknown
within-minute order. Nothing represents a quote, tick, fill or trade.

| Retrospective horizon | Overlapping windows / sessions | Median absolute close move | 90th percentile absolute move | Mean long favorable / adverse range | Gross absolute close move >25 bps | >50 bps |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 30m | 42,195 / 125 | 14.04 bps | 41.52 bps | +19.07 / −20.32 bps | 26.37% [24.03, 28.75] | 6.37% [5.21, 7.61] |
| 60m | 38,445 / 125 | 20.38 bps | 59.70 bps | +26.49 / −28.67 bps | 41.96% [38.67, 45.22] | 14.77% [12.46, 17.21] |
| Remaining continuous session | 45,820 / 125 | 32.97 bps | 111.74 bps | +41.59 / −53.66 bps | 58.27% [53.49, 62.80] | 36.21% [31.22, 41.34] |

Bracketed ranges are 95% percentile intervals from 5,000 fixed-seed
resamples of **whole-session event fractions**. Events within a session
overlap heavily and are not independent opportunities. The longer remaining
horizon includes early-day decisions with many hours of exposure, so its
higher exceedance fraction is not directly comparable to a fixed 30m window.
Median signed close moves were −2.36, −4.57 and −6.90 bps respectively;
directional selection has not been established. These findings show that
source reference prices sometimes move beyond an assumed hurdle. They do
not show that observable conditions predict the movement, or that either
direction could be selected and executed profitably.

**Observed cost inputs:** none. The archive has no bid/ask spread, brokerage,
taxes, slippage, queue position or fills. **Assumed scenarios:** 25 and 50
bps total round trip, fixed before this calculation and used solely as gross
price-movement hurdles. Neither value is a measured Dhan or NSE cost.
Subtracting either from an absolute price move would not produce a strategy
P&L. No conservative positive net advantage has been demonstrated.

## Conditional prospective availability plan

The smallest new observation that addresses the most important uncertainty
is a bounded Dhan-only poll on **Tuesday, November 3, 2026**, conditional on
same-day NSE session, RELIANCE identity, CAS status, corporate-action and
clock verification. The existing `dhan_candle_availability.py` implementation
is hardcoded to past October 27; update and test only its dated scope before
any later execution. This document authorizes **zero** requests.

If those prerequisites and separate approval are obtained, use only
`POST https://api.dhan.co/v2/charts/intraday`, `NSE_EQ`/`EQUITY`/2885,
`interval="1"`, `oi=false`, from `2026-11-03 00:00:00` to exclusive
`2026-11-04 00:00:00`. Retain the existing maximum 14 IST slots:
09:59:55, 10:00:02, 10:00:07, 10:00:20, 10:00:45, 10:00:55, 10:01:02,
10:01:07, 10:01:20, 10:01:45, 10:02:02, 10:02:20, 10:05:00 and 15:45:00.
Track the epoch labels around 09:59, 10:00 and 10:01. Skip missed slots;
make no catch-up calls or retries. Save complete original bodies and their
hashes before parsing; stop on HTTP, transport, clock or schema failure.
Capture independent clock offset/uncertainty before and after the window,
UTC request-start/response-end, monotonic elapsed time and server `Date`
diagnostics. First-seen is only bounded by sampled request/response intervals
and prior absence, widened by clock/network uncertainty. Compare later OHLCV
for revisions; unchanged samples cannot prove permanent completion. A single
session still cannot establish a general provider guarantee.

## Separate future experiment specification (proposal only)

Hypothesis: a small, predeclared set of **past-only**, completed-candle
conditions improves the *net reference* value of selecting a direction and
horizon over a no-condition frequency baseline and NO TRADE. Null: no
session-grouped improvement after costs. Candidate inputs are trailing
1/5/15-minute returns and volatility, time-of-day-relative volume computed
only from preceding training sessions, and time remaining in the verified
continuous session. Do not add spreads or depth features absent from the
source. The decision time is the independently evidenced publication-safe
time of the latest completed candle plus a fixed delay; it cannot be inferred
from the historical epoch label. Fixed outcomes are same-session reference
close moves at +30 and +60 minutes; the third is the final continuous-session
reference close. Windows crossing that close or a source gap are censored.
High/low excursions are secondary diagnostics, never executable outcomes.

Proposed fitting: May–July's 63 sessions. Calibration and model selection:
August–September's 42 sessions, with chronological walk-forward folds,
whole-session separation, one-session embargo and label-overlap purging.
October has already been inspected descriptively here and is not an unseen
test. November belongs to the existing frozen experiment and is excluded
entirely. Reserve **December 2026**, after the new protocol is frozen, as
the unseen prospective test, requiring at least 15 independently verified
complete sessions. Use one simple regularized baseline and a no-condition
frequency baseline; no hyperparameter, feature, threshold or horizon search
on December. The 50 bps round-trip scenario is the primary assumed cost;
25 bps is sensitivity. Independently observed spread/fees/slippage and a
documented executable cost model are prerequisites to an actual net claim.

Primary score: mean per-session directional reference return after the
primary assumed cost, counting abstentions as zero, compared with NO TRADE
and the simple baseline. Report coverage, downside, calibration, and
session-bootstrap 95% intervals. Control the family of three horizon tests
with Holm-adjusted two-sided 5% tests; require a positive lower confidence
bound on the net contrast and no material adverse-regime failure. Reject or
stop on source/hash/identity/calendar/timing failure, missing or revised
candles, cost-model failure, fewer than 15 unseen sessions, holdout leakage,
or no robust positive net contrast. All such cases yield **NO TRADE**. Even
a positive reference result cannot become a broker or paper-fill claim without
separate executable market evidence and approval.

The present evidence fails the source-timing and dated-identity gates and has
no observed cost model. Therefore the experiment is worth *preparing* to
answer an economically relevant question, but **not worth fitting or trading
yet**. Obtain a provider timing contract or the bounded prospective
observation first; then complete dated NSE/identity and cost reviews before
freezing this separate experiment.
