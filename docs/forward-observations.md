# Point-in-time forward candle observations

`python -m quant.apps.forward_observations` reads a locally verified OHLCV lake.
It makes no market-data request. The source lake and its hashes remain unchanged.
The new output directory holds independent `observations.jsonl` and
`outcomes.jsonl`, a descriptive `summary.json`, and their SHA-256 manifest.

```sh
python -m quant.apps.forward_observations \
  --lake data/historical/VERIFIED-LAKE \
  --calendar data/config/VERIFIED-CALENDAR.json \
  --timing-evidence data/config/VERIFIED-TIMING.json \
  --as-of-ns 1790000000000000000 \
  --output data/experiments/forward-observations-NEW
```

The timing evidence is a separate, dated local review, scoped to the lake hash
and calendar version. Its JSON shape is:

```json
{
  "source_dataset_sha256": "64-character verified lake digest",
  "calendar_version": "matching calendar version",
  "source": "dated evidence reference",
  "bar_start_verified": true,
  "complete_at_bar_end_verified": true
}
```

The flags are assertions to be supported by independent source evidence; the
program cannot prove them from candle prices. A valid file must not be written
to bypass unresolved provider semantics. In particular, the documented Dhan
pilot data has `UNVERIFIED` bar timing and no established completion
guarantee, so it is blocked. The current October calendar is verified for
RELIANCE on October 15–16 only; its October 12–14 sessions are provisional.
No eligible real summary can be produced from this checkout's tracked files.
On 2026-10-22, the earlier completed pilot's raw checkpoint and verification
report are also absent from this workspace. The dated pilot runbook records
1,800 saved bars but unresolved identity/calendar and candle-timing questions;
those counts are not forward outcomes. No authenticated request was repeated.

Each candle must be 1-minute, marked `BAR_START_VERIFIED`, in a dated explicit
verified continuous session, and linked to a source checksum in the verified
lake manifest. The source bar is visible only at its end. The configured
`as-of` timestamp limits both observations and forward outcomes. A later bar
cannot appear in the observation feature object. The first bar of a session,
and a bar immediately after a gap, have
`INELIGIBLE_NO_CONTIGUOUS_PRIOR`. Every other observed decision has independent
outcome records for 5, 15, 30 and 60 minutes plus the remaining verified
continuous session. The latter matures at the session close, so its
`horizon_minutes` varies by decision. A decision at the close has zero forward
minutes and is `CENSORED_SESSION_END`. Fixed horizons crossing the close are
censored; no window enters a closing auction or a later session.
From August 3, 2026 onward, every dated session must explicitly identify
`CAS_CONTINUOUS_TRADING` with a 15:15 close or
`NON_CAS_CONTINUOUS_TRADING` with a 15:30 close. Generic weekday hours and
an unclassified `REGULAR` session cannot establish auction applicability.

Sample observation (illustrative synthetic values):

```json
{"schema_version":"forward-observations-v2","observation_id":"2885:2026-08-04:1785815220000000000","instrument":{"venue":"NSE","segment":"NSE_EQ","security_id":"2885","symbol":"RELIANCE"},"session":"2026-08-04","session_kind":"CAS_CONTINUOUS_TRADING","decision_timestamp_ns":1785815220000000000,"latest_bar_start_ns":1785815160000000000,"eligibility":"ELIGIBLE","source":{"name":"SYNTHETIC_TEST","sha256":"..."},"features":{"last_completed_close_nanos":100000000000,"last_completed_volume":101,"prior_close_return":0.0}}
```

Sample separate outcome, joined by `observation_id`:

```json
{"schema_version":"forward-observations-v2","observation_id":"2885:2026-08-04:1785815220000000000","horizon":"5","horizon_minutes":5,"maturity_timestamp_ns":1785815520000000000,"state":"MATURED","signed_return":0.01,"favorable_excursion":0.02,"adverse_excursion":-0.02,"time_to_favorable_extreme_minutes":1,"time_to_adverse_extreme_minutes":1,"extreme_time_precision":"ONE_MINUTE_BUCKET_NOT_TICK_TIME","provenance":"future_candle_ranges_no_execution_or_fill_claim"}
```

The sample timestamps are illustrative identifiers. Actual timestamps come
from the verified candle and calendar records. Return is the final future
candle close versus the last completed decision close. Excursions use the
maximum high and minimum low over the future window, signed relative to that
same close. Tied extremes select the earliest 1-minute candle bucket. Their
ordering within a candle and exact times are unknown; high/low values are not
executable ticks or fills.

The `horizon` key is `"5"`, `"15"`, `"30"`, `"60"` or
`"remaining_session"`; `horizon_minutes` is the actual forward duration.
Outcome states are `PENDING` before all required future bars are complete,
`MATURED` after an intact same-session window, `CENSORED_MISSING_CANDLE` for a
gap, and `CENSORED_SESSION_END` when a fixed horizon crosses the verified
close or a remaining-session window has no future minute. Censored/pending
numeric fields are null. Missing candles are never
interpolated. Duplicate/out-of-order timestamps, bars outside the evidenced
session, mismatched manifest hashes, unknown timing and mismatched calendar
versions fail the run. The source lake's existing exclusions remain in its
manifest; this output counts ineligible observed decisions separately.
Multi-instrument lake partitions are regrouped by instrument only after each
instrument's original timestamp order and uniqueness pass validation.

The summary counts positive, negative and flat **signed close-to-close**
returns by horizon, gives a median for each direction, and reports mean
favorable/adverse candle excursions. These are overlapping exploratory paths,
not independent trades, optimized signals or profitability estimates.
The 20-candle synthetic fixture produces 14 matured 5-minute outcomes:
4 positive and 10 negative; 5 remain pending. At 15 minutes it produces
4 matured flat outcomes and 15 pending. The 30-, 60-minute and
remaining-session rows are pending at this fixture's cutoff. This is a
deterministic behavior example, not an NSE market distribution. No eligible real observations were
available here for a market estimate.
Historical ID continuity, adjustments, auction applicability and provider
timestamp/completion semantics still need dated external evidence for a real
run. QJ01/QP01 ledgers and the frozen 15-minute experiment are untouched.

## Proposed Dhan candle-availability observation (not executed)

`python -m quant.apps.dhan_candle_availability` prints the fixed plan without
reading a token or making a request. This separate, opt-in probe uses the
existing Dhan historical client, parser and private response quarantine. It
must not run until the exact scope below is separately approved. A previous
request cap for a training pass does not authorize this new session.

- **Conditional session:** Tuesday 2026-10-27, RELIANCE NSE cash equity Dhan
  security ID 2885. Before any request, save date-specific evidence that NSE
  actually opens, whether the closing auction applies, a date-scoped verified
  calendar, a dated identity registry, and a corporate-action review. The
  existing October 15-16 registry/calendar do not cover October 27. If any
  prerequisite is missing or the day is disrupted, abort; do not substitute a
  new date or instrument.
- **Identical payload per poll:** read-only `POST /v2/charts/intraday`,
  `exchangeSegment=NSE_EQ`, `instrument=EQUITY`, `securityId=2885`,
  `interval=1`, `oi=false`, `fromDate=2026-10-27 00:00:00`,
  `toDate=2026-10-28 00:00:00` (IST date window). Do not call account, order,
  live-feed or other endpoints.
- **IST request slots (14 maximum):** 09:59:55, 10:00:02, 10:00:07,
  10:00:20, 10:00:45, 10:00:55, 10:01:02, 10:01:07, 10:01:20, 10:01:45,
  10:02:02, 10:02:20, 10:05:00 and 15:45:00. Observe epoch-labelled rows
  around 09:59, 10:00 and 10:01. A label is not assumed to be a bar start or
  completion time. Missed slots are skipped; there are zero retries or
  catch-up requests. Stop on HTTP error, invalid response, transport failure
  or a local wall-clock step over 250 ms. The last slot is after the expected
  closing-auction window and is a comparison anchor, not proof of finality.
- **Clock and latency:** before the first slot and after the last, retain dated
  local time-service status plus an independent NTP offset/uncertainty check.
  Do not run if the initial clock offset or uncertainty exceeds 100 ms; mark
  timing inconclusive if the final check fails. Keep these clock records beside
  the capture with private permissions. Each poll records UTC request-start
  and response-end wall times, monotonic elapsed time, schedule, HTTP status,
  and server `Date` header when supplied. The latter is only a diagnostic;
  it may have one-second resolution or come from an intermediary. Bound
  availability by request/response intervals, widened by clock-offset and
  network-delay uncertainty. Do not assign a point estimate from the HTTP
  response time.
- **Evidence and interpretation:** each complete received body is saved as
  exact original bytes with SHA-256, endpoint, identical request payload,
  retrieval timestamp and status in private immutable files. Offline report
  rereads hashes and provenance before comparing OHLCV values. First-seen is
  the earliest *sampled* response containing a target label, bracketed by
  its request/response interval and the last earlier absence. An observed
  revision is a change between saved responses. Observed stability starts at
  the first of at least two matching saved values in the final unchanged run,
  with an after-close match shown separately. Unsampled revisions and later
  corrections remain possible; exact stable completion is unverified.

After separate approval and preflight evidence, the explicit execution guard
requires `--execute-read-only --approved-scope --max-requests 14 --calendar
DATED.json --registry DATED.json`; the token stays in a local environment
variable and is never recorded. Use a fresh private output directory. The
offline `--report` mode uses saved responses only. Any result is an
exploratory one-session provider-timing observation. It cannot by itself set
`complete_at_bar_end_verified`, confer historical causal eligibility, train a
model or change the frozen 15-minute experiment.
