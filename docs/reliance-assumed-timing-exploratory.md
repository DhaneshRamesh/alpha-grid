# RELIANCE assumed-timing exploratory study — 2026-11-02 IST

**Decision: STOP at the simple baseline. No incremental economic advantage was
demonstrated; no model promotion, connected paper trading or broker action.**
The November 3 availability probe remains NO GO.

## Source and protocol

The offline [study code](../src/quant/apps/exploratory_dhan_timing.py) reuses
`descriptive_dhan_opportunities.load_verified_bars` and
`validated_sessions`. It rechecks all three original Dhan response bytes,
sidecars, HTTP 200 statuses and pinned SHA-256 values before fitting:

| Request window | Original SHA-256 |
| --- | --- |
| May 1–July 30 | `62e76b108fad3fde0c49041e154796c5389f055040b1b229839d533664de512b` |
| July 30–October 26 | `fbe4eac254fa7e55c4e167eae9fe9c9a36597f8073c58196b135586ca8455838` |
| October 26–31 | `a4c61e3d5ad8ce3b9bf78317ac73ff15502bcefa52bd6a09c3771b866d2d8653` |

The verified source has 45,945 bars on 125 dates. October was inspected in
earlier descriptive work and was **excluded** from fitting and scoring;
November's frozen 15-minute experiment and December's proposed future holdout
were untouched. The fitted ridge and no-condition baselines use May–July,
excluding July 31 as a full-session embargo. The first May session cannot have
prior-session volume reference, leaving 61 training sessions. The 42
August–September sessions are scored chronologically in three 14-session
blocks. No parameter, feature or threshold was chosen by their outcomes.

Assume that each epoch label starts a 60-second candle, then separately assume
publication **60, 120 or 300 seconds after its close**. At each candidate,
features end at the latest bar allowed by that assumption: trailing 1/5/15
minute returns, 15-minute return volatility, volume relative to prior
training sessions at the same minute, and continuous minutes remaining. The
entry **reference** is the first full candle close after the hypothetical
decision, adding a full future-bar latency. Outcomes use the decision+30m,
decision+60m, or final observed continuous-session close. Cases crossing the
end are censored; source gaps are rejected. All entry/exit closes and future
high/low adverse ranges are **reference prices, not executable quotes, ticks,
fills or portfolio P&L**. A fixed ridge penalty of 1 is used. The
no-condition comparator uses the training mean; NO TRADE is zero. A direction
is selected only when its fitted mean exceeds the assumed round-trip cost of
50 bps (primary) or 25 bps (sensitivity). Abstentions count as zero.

## Chronological August–September results

Values below are mean per-session **hypothetical reference net bps**, after
the assumed cost. The 95% interval is a whole-session bootstrap of model
minus no-condition baseline. It is exploratory because August–September have
been used for this review; there is no unseen confirmatory test.

| Assumed delay | Horizon | Train / eval rows | 50 bps selected | 50 bps net | 95% contrast interval | 25 bps selected / net |
| ---: | --- | ---: | ---: | ---: | --- | --- |
| 60 s | 30m | 20,069 / 13,188 | 0 | 0.0000 | [0, 0] | 4 / −0.0108 |
| 60 s | 60m | 18,239 / 11,928 | 1 | −0.0109 | [−0.0328, 0] | 6 / −0.0098 |
| 60 s | remaining | 21,777 / 14,364 | 5 | −0.0219 | [−0.0495, 0] | 974 / −1.3415 |
| 120 s | 30m | 20,008 / 13,146 | 0 | 0.0000 | [0, 0] | 2 / −0.0082 |
| 120 s | 60m | 18,178 / 11,886 | 1 | −0.0119 | [−0.0356, 0] | 6 / −0.0105 |
| 120 s | remaining | 21,716 / 14,322 | 4 | −0.0172 | [−0.0433, 0] | 981 / −1.3680 |
| 300 s | 30m | 19,825 / 13,020 | 0 | 0.0000 | [0, 0] | 1 / −0.0075 |
| 300 s | 60m | 17,995 / 11,760 | 1 | −0.0119 | [−0.0356, 0] | 5 / −0.0176 |
| 300 s | remaining | 21,533 / 14,196 | 4 | −0.0194 | [−0.0498, 0] | 991 / −1.3368 |

The 30m and 60m ridge mean-absolute errors were worse than the unconditional
training-mean forecast in every delay scenario. Remaining-session ridge MAE
was lower, but its selected reference outcomes failed the primary net test.
Train-only empirical score-quintile probabilities also had worse positive-sign
Brier scores than the unconditional training frequency in all nine scenarios
(ridge-calibrated 0.2482–0.2505 versus baseline 0.2474–0.2497). The private
machine-readable report has every bin, selected adverse candle range, three
chronological evaluation blocks, fitted coefficients, source hash and cost
sensitivity. None of those coefficients is registered or served as a model.

## Reproduction and independent check

```sh
PYTHONPATH=src .venv/bin/python -B -m quant.apps.exploratory_dhan_timing \
  --output data/historical/research/NEW-PRIVATE-NAME.json
```

The existing ignored, owner-private report is
`data/historical/research/reliance-assumed-timing-exploratory-v1.json`:
50,865 bytes, SHA-256
`6abfa2237628d5b40886e0df71eaa8059cd558f268d9442f60fb505bec064411`.
An independent `json.loads(parse_float=Decimal)` check against original raw
arrays reproduced the first May 5 eligible 60-second-delay 30m row:
one-minute return −6.81663258 bps, prior-session relative volume
0.9547840671 and future reference move −13.61655773 bps. The focused
software-only timing test changes a future close and confirms that the
decision features stay unchanged while the outcome changes.

The isolated `quant-paper` Rust library suite passed 14/14 tests. Existing
tests exercise independent spread, staleness, uncalibrated-model and loss
rejections; order cancellation and duplicate suppression; QP01 append,
recovery and failed-sync behavior; simulated fees, accounting, session halt
and reconnect paths. It uses temporary software fixtures, not Dhan bars or
connected paper activity. No QP01 or cockpit artifact was written by this
study.

## Limits and next evidence

The start-label convention and all three publication delays are mathematical
assumptions, not measured Dhan behavior. Full-range historical RELIANCE
identity, CAS applicability, corporate-action adjustment and actual costs
remain unresolved. No spread, slippage, fees, marketable price, quote, order
or fill was observed. Overlapping candidate outcomes are not independent
trades; session bootstrap does not repair missing execution evidence. The
August–September evaluation is exploratory, and October is already inspected.
The first useful prospective evidence remains a dated provider timing contract
or the separately approved November 3 capture after its NO GO preflight is
resolved. Nothing here changes the causal or connected-paper gates.
