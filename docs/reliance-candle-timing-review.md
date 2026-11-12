# Dhan one-minute candle timing review — 2026-11-01 IST

**Decision: historical source integrity is verified; causal candle availability
is not. NO TRAINING / NO TRADE.** No authenticated Dhan call or credential
renewal was made for this review. The frozen November 15-minute experiment and
cockpit controls were not changed.

## Independent raw-response reproduction

An independent Python pass read the three original JSON byte streams directly,
without importing the Alpha Grid parser or descriptive research module. It
checked SHA-256 against the pinned values, `.sha256` and HTTP-200 receipt
metadata; checked exact NSE_EQ/EQUITY/2885/1-minute request bodies, parallel
array lengths, ascending unique epoch values and IST request bounds; converted
decimal prices to nanounits; then recomputed same-session close paths and
future high/low extrema with ordinary loops. The original source hashes and
sizes remain listed in [the six-month audit](reliance-six-month-mac-audit.md).

The independent pass found **45,945 rows on 125 dates**, including 63 dates
before August 3 with 375 successive labels from 09:15–15:29 IST and 62 dates
from August 3 with 360 successive labels from 09:15–15:14 IST. It found no
duplicate, out-of-window or internal-gap label. There are 42,195, 38,445 and
45,820 overlapping same-session 30m, 60m and remaining-session paths.

| Horizon | Median absolute close move | Mean long favorable / adverse high-low range | Session-mean fraction exceeding assumed 25 / 50 bps |
| --- | ---: | ---: | ---: |
| 30m | 14.0366 bps | +19.0681 / −20.3187 bps | 26.3693% / 6.3730% |
| 60m | 20.3774 bps | +26.4887 / −28.6661 bps | 41.9583% / 14.7708% |
| Remaining continuous span | 32.9653 bps | +41.5910 / −53.6647 bps | 58.2694% / 36.2087% |

These point values reproduce the [exploratory report](../data/historical/research/reliance-may-oct-2026-descriptive-v2.json)
at its published precision. An initial direct binary-float parse put a few
exactly-at-hurdle close moves on the wrong side of strict `> 25/50` bps.
Converting JSON decimal prices to nanounits, as the shared parser does,
removed that discrepancy. Independent whole-session bootstrap intervals
shift slightly with a different random generator; the point event rates are
unchanged. This is a numerical precision issue, not a missing candle.

The raw responses encode each timestamp and volume as JSON numbers with a
`.0` fractional spelling, although their values are integral. [Dhan's v2
historical table](https://dhanhq.co/docs/v2/historical-data/) describes both
fields as integers. The parser accepts only integral values; the wire-format
difference should be clarified with Dhan rather than normalized into a timing
claim.

## Session and CAS evidence by regime

| Scope | Source observation | Independent public evidence | What remains unresolved |
| --- | --- | --- | --- |
| May 4–July 31, 63 sessions | 23,625 rows, 375 labels each, 09:15–15:29 IST | Date set equals weekdays less May 1, May 28 and June 26 on [NSE's 2026 equity holiday calendar](https://www.nseindia.com/resources/exchange-communication-holidays). | No dated RELIANCE security master for each day on this Mac; no provider label/completion contract. |
| August 3–October 30, 62 sessions | 22,320 rows, 360 labels each, 09:15–15:14 IST | Date set matches the August–October trade dates on [NSE's CAS archive](https://www.nseindia.com/static/reports/closing-auction-session-historical-data). [NSE's CAS rules](https://www.nseindia.com/static/products-services/closing-auction-session) put CAS-eligible cash stocks into a separate session from 15:15 and leave non-CAS stocks in continuous trading to 15:30. | The archive listing proves exchange dates, not RELIANCE-specific CAS eligibility. Full-range dated security-master flags are absent locally. No CAS auction bars were used. |
| October 15–16 subset | 720 rows within the new six-month responses, 360 each | Local `data/config/calendar.json` and `registry.json` record dated NSE identity/CAS checks for these two days. | Their cited original `NSE_CM_security_*.csv.gz` files are absent on this Mac, so this review could not independently rehash them. This subset does not establish other dates. |

All 125 source dates match the NSE weekday/holiday calendar. Count and
boundary agreement are useful checks, but they do not prove a timestamp is a
bar-start label or that an OHLCV value was available at a hypothetical
decision time. The later 360-row source shape is consistent with the CAS
regime; it is not a dated security flag. Corporate-action and Dhan adjustment
semantics remain unverified.

## Dhan timing contract: established and missing

[Dhan v2 historical documentation](https://dhanhq.co/docs/v2/historical-data/)
establishes the `POST /v2/charts/intraday` OHLCV endpoint, one-minute interval,
up-to-90-day request window and epoch `timestamp` field. [Dhan release
notes](https://dhanhq.co/docs/v2/releases/) say the v2 historical field is
UNIX/epoch rather than the earlier Julian convention. Neither source states
whether the label denotes candle start or end, the first time an in-progress
bar can appear, any completion/publication delay, or a limit on later
revisions. A November retrieval of May–October bars supplies a receipt time,
not their original publication times. The HTTP `Date` header is not a candle
publication timestamp. No real prospective probe capture, independent clock
offset check or request/response timing brackets were found locally. Two
identical sampled responses would show only sampled stability, never permanent
completion.

### Draft inquiry to Dhan technical support — prepared, not sent

> Subject: DhanHQ v2 intraday one-minute candle timestamp and availability contract
>
> For `POST /v2/charts/intraday`, `NSE_EQ` / `EQUITY`, `securityId=2885`,
> `interval="1"`, please confirm the effective version/date of the following:
> (1) Does each UNIX `timestamp` label the start or end of its one-minute
> interval, and in which timezone is the interval interpreted? (2) Can a
> current, incomplete candle appear in a response, and what is the earliest
> possible appearance relative to the interval end? (3) When is OHLCV
> guaranteed complete and available to an API caller, if any bound exists?
> (4) Can a candle change later in the same session or on subsequent days;
> are corporate actions/backfills applied retrospectively? (5) Are post-15:15
> closing-auction values included or separated for CAS-eligible cash stocks?
> (6) Can `toDate` be the next calendar day while the current trading
> session is still in progress, or must an ongoing-session query end no later
> than the current time?
> The saved responses encode integral timestamp and volume values with `.0`
> in JSON although the documentation calls them integers; is that wire
> format expected? A statement that no timing or immutability guarantee is
> offered would also be useful. No account details are needed for this answer.

## Conditional prospective observation proposal — not authorized

Use the existing `dhan_candle_availability.py` capture/quarantine/report
workflow, now scoped to **Tuesday, November 3, 2026**. To distinguish
plausible publication delays with the fewest informative samples, propose
**eight maximum, zero-retry identical POSTs** at these IST slots:

`09:59:55`, `10:00:02`, `10:00:10`, `10:00:30`, `10:01:02`, `10:02:02`,
`10:05:00`, `15:45:00`.

Inspect epoch labels corresponding to 09:59, 10:00 and 10:01 IST. A pre-10:00
sample gives an absence/presence baseline. Later samples distinguish first
sampled appearance by +2s, between +2/+10s, +10/+30s, +30/+62s,
+62/+122s, +122/+300s or after +300s. The +5m and after-close samples test
for observed OHLCV revisions. They cannot give exact first publication time,
prove no unsampled change or establish a universal completion guarantee. If
responses straddle adjacent slots or latency is too high, record a wider
inconclusive interval; never substitute a guessed point time.

The exact request would be `POST https://api.dhan.co/v2/charts/intraday` with
`{"securityId":"2885","exchangeSegment":"NSE_EQ","instrument":"EQUITY","interval":"1","oi":false,"fromDate":"2026-11-03 00:00:00","toDate":"2026-11-04 00:00:00"}`.
No other instrument, endpoint, account API, broker order or retry is in this
proposal. The code and synthetic fixtures now specify eight November 3 slots;
the offline plan and five focused tests pass. This review makes **zero** such
requests and does not renew a token. The v2 intraday docs describe `toDate` as
the end of the desired range but do not promise acceptance of a next-day
boundary while the session is in progress. If authorized, the first saved
HTTP response tests this exact payload; an error stops the probe without a
modified retry.

Preflight: retain a same-day NSE trading/session report, dated NSE security
master with RELIANCE/2885/EQ and CAS flag, current Dhan public master, and
corporate-action check without backdating. Obtain independent local time
service and NTP offset/uncertainty evidence before and after; do not start if
offset or uncertainty exceeds 100 ms. Use a fresh owner-private output
directory. Save each complete original HTTP body before parsing with SHA-256,
exact request, status, server `Date`, UTC request-start/response-end and
monotonic elapsed time. Keep independent clock records alongside it. Bound
first-seen intervals by those brackets plus clock and network uncertainty.
Skip missed slots, keep at least one second between request starts, and stop
on changed/missing identity or CAS evidence, closed/disrupted session,
clock step over 250 ms, HTTP error, transport failure, invalid schema or
private-storage failure. Never silently add a ninth request or move to a new
date. Confirm active Data API entitlement through operator-provided evidence
and an unexpired token held privately on the Mac. These external evidence
checks are not inferred from a CLI flag; they remain unfulfilled as of this
offline review. A failed or thin capture stays descriptive/inconclusive.

## Next action

Send the drafted timing-contract inquiry through the operator's normal Dhan
support channel. A dated, explicit provider answer about start/end meaning,
partial bars and completion/revisions would reduce the largest causal
uncertainty without consuming a Dhan data request. If that contract is absent
or incomplete, seek a separate approval for the exact eight-poll proposal
after the November 3 preflight. Until then, keep the existing historical
archive available as **EXPLORATORY / NOT CAUSALLY ELIGIBLE** and preserve
NO TRADE.
