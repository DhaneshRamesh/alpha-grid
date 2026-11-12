# Cross-stock opportunity discovery — frozen inspection plan (2026-11-02 IST)

**CONTINUE evidence collection only; DATA_ONLY / NO TRADE.** No cross-stock
Dhan response, live quote/depth frame, trained model, paper intent or order was
created in this review. The closed RELIANCE volume-confirmation result and the
frozen November 15-minute experiment remain untouched. The 1–2% daily net
return on *total capital* is an aspiration to measure against, never a forced
trade rule or assumed edge.

## Dated starting universe

The fixed 25-ID manifest is [cross-stock-universe-2026-11-02.json](cross-stock-universe-2026-11-02.json).
It pins the original ZIP hashes, byte counts, source URLs and retrieval times
for **20** [NSE CM UDiFF final bhavcopies](https://www.nseindia.com/all-reports)
(October 1–30 trading dates), plus the October 30 NSE CM MII security file and
the existing November 1 [Dhan public instrument master](https://dhanhq.co/docs/v2/instrument-list/).
All 20 ZIP members have the stated trade date. Among 2,226 eligible ranked
rows, selection takes the top 25 by median `TtlTrfVal` in INR. Eligibility
requires NSE `CM/NSE/STK/EQ/F1` on every date, a constant `FinInstrmId` and
`INE` ISIN, unique October 30 MII normal-market eligibility, and a unique
matching `NSE/E/EQUITY/EQ` Dhan security ID. `INF` fund/ETF ISINs are excluded.
This is **October historical traded-value evidence**, not a claim of current
spread, available depth, future liquidity or causal backtest eligibility.

| Rank | Symbols and Dhan IDs |
| --- | --- |
| 1–5 | HDFCBANK 1333; RELIANCE 2885; INFY 1594; ICICIBANK 4963; BHARTIARTL 10604 |
| 6–10 | BSE 19585; TCS 11536; PCJEWELLER 29124; KOTAKBANK 1922; AXISBANK 5900 |
| 11–15 | ETERNAL 5097; SBIN 3045; BAJFINANCE 317; LT 11483; MONEYVIEW 766325 |
| 16–20 | M&M 2031; NETWEB 17433; CUPID 18520; TECHM 13538; BHEL 438 |
| 21–25 | DIVISLAB 10940; SHRIRAMFIN 4306; HCLTECH 7229; MCX 31181; MARUTI 10999 |

The MII October 30 CAS flag is `0` for PCJEWELLER, MONEYVIEW, NETWEB and
CUPID; it is `1` for the other 21. This is dated **October 30** only. Each
future session needs its own NSE MII identity, normal-market and CAS flags,
independent exchange calendar/notices and corporate-action check. A Dhan
master retrieved after a historical date cannot establish its earlier mapping.
The October selection has look-ahead for October 1–30, so any October
cross-stock analysis is explicitly descriptive and survivor-selected.

## Research question fixed before fitting

At fixed 10:00, 11:00, 12:00, 13:00 and 14:00 IST decision times, among
eligible stocks, does a **cost-aware rank** using only the last committed
quote/book's spread and displayed depth improve 60-minute favorable/adverse
probabilities and conservative total-capital net outcomes over a price/volume
rank? Economic rationale: a gross movement is useful only when entry/exit
friction and executable-size constraints leave room for it. Null: no
incremental proper-score or after-cost session-level advantage. This is not
the failed RELIANCE volume-confirmation candidate; no 20-session volume
threshold or filter is reused.

Compare (0) NO TRADE (zero return and utilization), (1) unconditional
per-instrument/per-clock event rates with equal eligible selection, and (2)
a price/volume-only rank. Define `r15` as the past 15-minute return in bps,
`v15` as its realized volatility in bps and `t` as one tick in bps. Require
the ₹100,000 reference share count to be at most 1% of *past* 15-minute
traded shares. Rank baseline (2) by `r15 / max(v15, t)`, with only positive
scores eligible. Record same-clock prior-session relative volume as a
continuous descriptive covariate; the failed 20-session volume-confirmation
threshold is not reused. Candidate rank is `(r15 - C) / max(v15, t)`, with
`C` equal to published round-trip fees plus the current quoted full-spread
proxy, current displayed-depth sweep cost **beyond best quotes** and the separately
assumed 40-bps slippage/latency allowance. Only positive scores are eligible.
This *past-movement proxy* is a frozen rank, not a predicted return or fill.
Tie-break by security ID; allow one opportunity per decision time, at most
one open reference position, and NO TRADE if data or costs are insufficient.
No feature or threshold search is allowed.

All features must be available by the actual decision receipt time, with
validated clock uncertainty, current identity, complete lookbacks and
same-instrument quote/book pairing. Use *only* preceding completed 1-minute
bars if a prospective availability contract or probe eventually supports
their use; otherwise rely on committed live observations and label historical
bars exploratory. Standard Dhan Quote-17 LTP is not bid/ask. No candle high,
low or next open is treated as a fill. Exclude stale, crossed, unpaired,
gapped or reconnect-ambiguous books; preserve source hashes and exclusion
counts. Separate ordinary continuous trading from CAS/auction; censor any
window crossing the applicable dated boundary.

Primary outcome: signed 60-minute reference-midquote movement, favorable,
neutral and adverse relative to an instrument/size-specific hurdle. Secondary
descriptive outcomes: 30 minutes and remaining **continuous** session.
Report conditional movement distributions, event probability and magnitude,
time/censoring, Brier score, log loss and calibration. If the future research
training gate passes, use the existing one-dimensional `ConditionalBaseline`
with fixed `knn`, `k=20` on each frozen score (separate favorable and adverse
binary fits), trained on earlier sessions only. Score both on the *same*
eligible instrument-decision set; no rank is treated as a calibrated
probability before that fit and independent calibration. Reference net subtracts
the [published Dhan cash-equity fee components](https://dhan.co/pricing/) at
actual whole-share notionals, observed entry/exit quoted spread and a
displayed-depth sweep at the fixed size. Keep an additional, separately
assumed adverse slippage and latency allowance, with at least the prior
40-bps combined scenario and a doubled stress scenario. The future exit book
is an outcome-side cost observation, never an entry feature. Quoted/depth
costs cannot prove fill price, market impact or actual slippage. A missing or
invalid cost estimate gives NO TRADE and an unknown net result.

## Evaluation and acceptance

Before any fitting, freeze the exact source hashes, clock/candle availability
rule, corporate-action/adjustment policy, cost schedule, features and outcome
definition, security-ID split, calendar and chronological dates in an
experiment manifest. For a prospective study, reserve ranks 5/10/15/20/25
(BHARTIARTL, AXISBANK, MONEYVIEW, BHEL, MARUTI) as untouched **instrument
holdouts**; fit/calibrate on the other 20 only. Use the first 60 independently
eligible future sessions for fit, next 20 for calibration, and final 20 for
evaluation, with those dates fixed when the prospective schedule exists.
The frozen November experiment's data/holdout is excluded. Reconstitute each
past day's eligible universe from dated exchange identity, CAS and corporate
actions; count delisted and changed-ID exclusions. Do not backfill the
October 30 universe into older folds and call it survivor-free.

Reuse `quant.research.walkforward.make_folds` with full-horizon purge and at
least one exchange-session embargo. Group **all stocks on one exchange date**
as one uncertainty unit; overlapping decisions/labels are not independent.
Compare candidate and baselines on identical eligible dates, including
zero-selection days. Use paired session resampling and report selection count,
independent dates, held-out-stock and regime breakdowns, uncertainty for
event probabilities and economic deltas, and multiple-testing scope. Reject
the candidate unless held-out instruments and final dates show better
favorable/adverse Brier and log loss **and** the 95% lower paired-session
bound on incremental conservative *total-capital* daily net return exceeds
zero under base and adverse costs. A result with no valid costs or timing is
unresolved, not accepted. Stop on material source/calendar/identity gaps.

For the portfolio reference, size whole shares within existing paper/risk
limits; debit full fees and costs, mark unused capital at zero, and count
simultaneous opportunities as competition for the same capital. Report daily
net return on starting total capital, capital utilization, turnover, peak to
trough drawdown, worst session, session expected shortfall and constraint
breaches. Independently require zero violations of the current QP01/risk
limits and kill-switch policy. Compare the 1–2% aspiration with the **full
capital** result and its uncertainty; do not optimize trade frequency toward
that target. NO TRADE remains a valid baseline and result.

## Smallest bounded next acquisition, requiring separate approval

**Stage A is a source/coverage pilot, not a causal or economic test.** Reuse
the verified RELIANCE October 26–30 response. Propose exactly **24** new,
no-retry authenticated `POST https://api.dhan.co/v2/charts/intraday` requests,
one for each of the other 24 manifest IDs, each with `exchangeSegment=NSE_EQ`,
`instrument=EQUITY`, `interval="1"`, `oi=false`, `fromDate="2026-10-26
00:00:00"`, `toDate="2026-10-31 00:00:00"` (exclusive). No account, quote,
depth, subscription or order request is in this scope. The existing acquisition
`backfill` CLI requires one security ID per run. Use **24 fixed
instrument-scoped invocations**, each preceded by an offline dry-run and
guarded by `--max-new-requests 1`. Stop the entire sequence on its first
failure; the total ceiling is 24 new POSTs. No call is authorized here.
Persist every original HTTP response, including errors,
request body, receipt/metadata, byte count and SHA-256, then verify schema,
date/ID, unique timestamps and independently dated normal/CAS coverage.
Stop on authentication, entitlement, identity, calendar or source-integrity
failure without a retry or scope change. Because the universe was selected
using these dates, outputs are exploratory, and the pilot cannot resolve
candle publication, spread or fills.

Before any later 25-ID joint live study, the existing depth collector must
retain raw frames before decode and enforce dated session/clock/identity
preflight and a bounded stop (see
[microstructure readiness](real-microstructure-readiness-2026-11-02.md)).
One future-session **read-only** quote plus 20-level depth quality capture
would then use exactly these 25 NSE_EQ IDs, one standard socket and one depth
socket (below Dhan's [50-ID 20-level cap](https://dhanhq.co/docs/v2/full-market-depth/)),
with raw QJ01, hashes, receipt clocks and teardown. It would measure spread,
displayed depth and coverage, not a predictive effect. A full 100-session
prospective sample is needed for the frozen train/calibration/evaluation
design; that larger collection needs its own approval and cost/capacity review.
No live socket or historical POST was opened for this plan.

**Present status:** the only verified Dhan research source is RELIANCE's
45,945 historical bars on 125 sessions, with unresolved candle publication
timing and no real quote/depth cost observation. Therefore cross-stock
favorable/adverse, net, drawdown and generalization results are **not yet
measurable**. The October public NSE evidence materially improves universe
selection and instrument identity; the 24-response pilot would test
cross-stock historical coverage only. The highest economic information gain
after source-integrity fixes comes from prospective spreads/depth paired with
future movement, with NO TRADE until those gates pass.
