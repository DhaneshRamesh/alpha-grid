# First offline trading simulator — 2026-11-02 IST

## Frozen exploratory rule and accounting

`src/quant/apps/historical_simulator.py` is `SIMULATION_ONLY`. Starting virtual
capital is ₹1,000,000; at most ₹100,000 reference notional is committed to one
cash-equity instrument per session, using whole shares. The frozen 25-name,
October 30 universe and its dated ID manifest are in
`docs/cross-stock-universe-2026-11-02.json`. There is no live feed, QP01 write,
broker path or actual fill. The failed RELIANCE volume filter and November
experiment remain separate.

Assume, only for exploratory replay, that Dhan's minute timestamp denotes the
start of the bar and a completed bar is available by the next minute. The
opening range is the maximum high of labels 09:15–09:44 IST. At 10:00, use
only labels through 09:59. A close above that high qualifies. Rank qualifying
names by the close's excess over that high in basis points; tie-break by the
frozen security ID. Choose one, or NO TRADE. Use the 09:59 close as an entry
*reference* and the 10:59 close as an exit *reference*. Require every minute
from 09:15 through 10:59. The simple momentum comparison uses a positive
09:44-to-09:59 close return, ranked by that return, with the same reference
exit. Forced diagnostics pick the largest such past move on days without a
breakout; they are excluded from equity, rule selection and comparison.

Gross reference P/L is `shares × (exit close − entry close)`. Estimated NSE
intraday fees use [Dhan's published pricing](https://dhan.co/pricing/) on
distinct buy and sell notionals, with the documented rounding approximated in
code. Assumed execution friction is 40 bps of buy notional for the round trip.
Virtual cash falls by reference buy notional at entry and returns to ending
equity after the reference exit; the virtual position closes each session.
No close, high, low or subsequent open is asserted to be an executable fill.
Capital utilization is reference buy notional divided by initial capital;
drawdown uses end-of-session virtual equity. Five sessions receive a
session-bootstrap interval. This is a mechanics check, not an untouched
cross-stock performance evaluation.

## Verified local result

The Mac-pinned original RELIANCE Dhan response has SHA-256
`a4c61e3d5ad8ce3b9bf78317ac73ff15502bcefa52bd6a09c3771b866d2d8653`,
1,800 one-minute bars, and five complete October 26–30 sessions. The CLI
verifies the original response through `verify_saved_response` before replay.
Only RELIANCE has a genuine matching archive; 24 names are unavailable.
The retrospective universe was selected after these sessions, so these rows
cannot supply a protected holdout or causal cross-stock result. Candle
availability, dated identity continuity and executable prices are unproven.

The rule selects October 26 only: gross reference +₹332.00, fee estimate
₹82.62, assumed friction ₹396.28, conservative reference net −₹146.90.
Five-day virtual equity ends ₹999,853.10, max end-of-day drawdown ₹146.90,
mean session net −₹29.38 and mean utilization 1.9814%. The momentum baseline
selects the same sole session, so paired difference is zero. NO TRADE remains
₹1,000,000. Four other sessions are explicit excluded diagnostics; 120
instrument-session rows have no genuine archive. The five-session bootstrap
95% interval on mean rule net is [−₹88.14, ₹0.00]. No profit claim follows.

## Expanded offline demonstration, 2026-11-02

`--six-month` reuses the three pinned original-response verifications and
125-session coverage check before the same fixed simulator rule. The private
result is `data/historical/research/alpha-grid-simulation-reliance-may-oct-2026-exploratory.json`
(640,503 bytes, SHA-256
`52d43e1ff04d14ac295e1dc5c54d9572f72885b328b3ef3361b9e0f2f3234d76`).
The three source SHA-256 values are pinned in
`src/quant/apps/descriptive_dhan_opportunities.py`. Their 45,945 original
candles cover 125 complete sessions. Source hashes may change only between
session dates. The cockpit remains on its stricter five-day pinned replay;
this larger result is available through the CLI.

The opening-range rule selects 12 sessions: gross reference +₹2,490.30,
estimated fees ₹991.73, assumed 40 bps friction ₹4,771.30, net −₹3,272.73.
Virtual capital ends ₹996,727.27 with ₹4,422.20 maximum daily-equity
drawdown. Three selected net results are positive and nine negative. The
session-bootstrap 95% interval for mean daily net is [−₹58.23, +₹5.47].
Momentum selects 59 sessions and ends ₹974,006.69; its interval is
[−₹274.80, −₹142.04]. NO TRADE ends ₹1,000,000. The paired rule-minus-momentum
mean daily net interval is [+₹122.93, +₹243.25], which does not make either
rule economically profitable. All 113 forced diagnostics remain excluded
from selection and equity. The fixed October 30 universe was selected using
inspected dates, while only RELIANCE has OHLCV, so this is neither an unbiased
cross-stock backtest nor a causal or executable performance estimate.

## Missing genuine data and bounded acquisition procedure

The previously frozen Stage A scope in
`docs/cross-stock-opportunity-plan-2026-11-02.md` is the bounded procedure:
24 one-instrument, no-retry Dhan historical POSTs for October 26–30, 2026,
reusing the existing verified RELIANCE response. Each request has
`exchangeSegment=NSE_EQ`, `instrument=EQUITY`, `interval=1`, `oi=false`,
`fromDate=2026-10-26 00:00:00`, exclusive `toDate=2026-10-31 00:00:00`.
The 24 fixed symbol/ID pairs are HDFCBANK/1333, INFY/1594,
ICICIBANK/4963, BHARTIARTL/10604, BSE/19585, TCS/11536,
PCJEWELLER/29124, KOTAKBANK/1922, AXISBANK/5900, ETERNAL/5097,
SBIN/3045, BAJFINANCE/317, LT/11483, MONEYVIEW/766325, M&M/2031,
NETWEB/17433, CUPID/18520, TECHM/13538, BHEL/438, DIVISLAB/10940,
SHRIRAMFIN/4306, HCLTECH/7229, MCX/31181 and MARUTI/10999.
An actual offline HDFCBANK dry-run on November 2 returned
`DRY_RUN_NO_NETWORK`, one `NEW_REQUEST_REQUIRED`, and the exact payload
above; it verified the saved November 1 Dhan master hash but still reported
`historical_identity=UNVERIFIED`.

For each pair, the existing offline command shape is:

```bash
PYTHONPATH=src .venv/bin/python -m quant.apps.acquisition dry-run \
  --security-id 1333 --symbol HDFCBANK \
  --start 2026-10-26 --end 2026-10-31 --interval 1 \
  --window 2026-10-26:2026-10-31 \
  --output-dir data/historical/raw-cross-stock-oct-2026 \
  --master data/instruments/dhan-scrip-master-2026-11-01.csv \
  --master-manifest data/instruments/dhan-scrip-master-2026-11-01.manifest.json
```

After separate approval, an operator would replace `dry-run` with `backfill`
and add `--max-new-requests 1 --subscription-confirmed
--backfill-authorized --execute-read-only` for each fixed pair, stopping the
entire sequence on the first error. The existing backfill path has zero
automatic retries, original-response/HTTP-error retention and checksum
verification. Before any request, independently verify the dated NSE
identity, corporate actions and normal/CAS session status; verify the Dhan
master freshness and active Data API entitlement. The current November 1
master is current mapping evidence only, not October identity continuity.
No 24-request acquisition is authorized or executed by this sprint. This
retrospective coverage pilot would still be exploratory; later prospective
evaluation needs separately reserved sessions and measured timing and costs.

## Validation and controls

Run `PYTHONPATH=src .venv/bin/python -m quant.apps.historical_simulator`
for stdout JSON, or add `--output` with a new path under ignored
`data/historical/research/` to retain a private file. The CLI refuses an
unverified or non-pinned archive. Add `--six-month` for the three-response
125-session replay. Focused Python accounting/source tests,
web simulation view tests, web typecheck/lint/build and the offline Rust
gateway cache test are the validation gates. Rust tests requiring local
listener binds cannot run under the present sandbox. The running cockpit
needs its ordinary local restart to load changed Rust/web code.
