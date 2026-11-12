# Cross-equity adaptive v2 — exploratory only (2026-11-03)

The code in `src/quant/apps/cross_equity_adaptive.py` reverified all 25 original
Dhan responses, their sidecar checksums and hashes in the immutable ALL_25 job.
It reproduced the saved fixed-hour JSON exactly: 45,300 bars across five
October 26–30 sessions and 125 complete 10:00 stock decisions. The fixed
baseline selected five references: gross −₹2,917.10, published fee estimates
₹410.91, one assumed 40 bps execution allowance ₹1,979.52, net −₹5,307.53.
Three of five gross references lost; ETERNAL's positive gross move became a
net loss after costs. The first close at or below the opening high after entry
was 10:05, 10:55, none, 10:03 and 10:10 IST for the five selections in order.
The full 61-label paths per selection are retained separately.

The original rule uses the 09:59 close both to trigger its 10:00 selection and
as the entry reference, assumes its availability at 10:00, and checks that the
future 10:59 exit bar exists before admitting a stock. All 25 happen to have
that exit bar on all five dates, so this future-availability condition did not
change the saved selections, but it is a code-level look-ahead defect. The
25-stock manifest was assembled with October 30 evidence; its earlier-session
membership is retrospective. Candle availability, historical identity
continuity and actual fills remain unverified.

Adaptive v2 is a separate offline research mode. It evaluates all available
stocks every five minutes from 10:00–14:40 IST. Under the stated start-label
hypothesis, the latest eligible bar ended before the decision by a scenario
delay of 60, 120 or 300 seconds; the first later minute close is a reference,
never a fill. A price-only baseline requires an opening-high breakout and a
positive trailing 15-minute return after *one* fee plus 40 bps allowance.
The price-plus-volume variant also requires recent 15-minute volume at least
equal to the preceding 15 minutes. The proxy only ranks candidates; it is not
a calibrated expected return, and its costs are not added again to realized
net. Past-only volatility, relative cross-stock return, comparable-time prior
session volume and price-position features are logged, but not arbitrarily
weighted. Historical quoted spread, depth and verified index context are
missing, so their fields remain null. No favorable/adverse probabilities are
claimed or used for selection.

Virtual positions exit on an as-of close below the opening high, a 200 bps
adverse move, 60 minutes, or the 14:40 decision cutoff, using the next minute
close reference. Existing ₹100,000 simulator notional and three daily entries
are retained; the default QP01 ₹500 session-loss amount and 200 bps downside
assumption are used as independent virtual limits without changing QP01. One
virtual position is allowed. The complete decisions log records CANDIDATE,
REJECT, SELECT, HOLD, EXIT and NO TRADE for every tested slot.

| Assumed delay | Price-only net | Price + volume net | Volume increment |
| --- | ---: | ---: | ---: |
| 60 s | −₹4,572.26 | −₹3,104.12 | +₹1,468.14 |
| 120 s | −₹3,839.84 | −₹3,084.59 | +₹755.25 |
| 300 s | −₹2,815.29 | −₹4,557.87 | −₹1,742.58 |

All six variants lose against NO TRADE. The five-session paired bootstrap
interval for volume's mean incremental net includes zero at 60 and 300 s;
at 120 s it is [₹0.00, ₹453.15] per session and remains too weak for an
economic or causal claim. The October dates were already examined, timing
delays are assumptions, costs lack measured historical spread, and there is
no untouched chronological test. No ML model was fit. The v1 live worker and
frozen November experiment remain unchanged; v2 is visible only as a labeled
historical replay in the existing Simulator tab. Raw live packets remain
quarantined from research and shadow decisions.

Private immutable files are under
`~/Library/Application Support/AlphaGrid/offline-dev/research-cross-equity-adaptive-v2/`:
`fixed-audit-v2.json`, `selected-minute-paths-v2.json`,
`adaptive-report-v2.json`, `all-decisions-v2.json`, and checksum manifests.
The original saved ALL_25 result and Dhan responses were not rewritten.

Verification: 8 focused Python tests, 2 Simulator UI tests, web build/lint,
the local Node research route against the hashed real artifacts, and
`git diff --check` passed. No authenticated Dhan call, socket, order or model
training occurred in this experiment. PowerShell is absent on this Mac, so
the repository Merkle script could not run.
