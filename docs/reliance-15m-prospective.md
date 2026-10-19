# RELIANCE 15-minute prospective experiment, frozen 2026-10-19

This is a **provisional reference-price forecast**, not a trading model or a
research-approved dataset. The prior five-minute experiment failed to beat its
null. September–October 2026 has already been examined and is excluded from
selection, fitting, diagnostics and validation here. November 2026 is the first
prospective evaluation period; no November outcomes were available at freeze.

The executable specification is `SPEC` in
`src/quant/apps/prospective_15m.py`. The immutable training artifact records
its SHA-256, both relevant code hashes, Git commit and all four saved Dhan
response hashes. Replaying the same artifact and evidence uses no random model
fit; the only random step is a fixed-seed session bootstrap.

## Fixed protocol

- Fit on April–July only: 73 usable sessions after 10 training sessions warm the
  causal time-of-day volume baseline. August's 21 sessions are development
  context only. Never use September–October here.
- Retain the existing raw features: current one-minute and trailing five-minute
  volatility-normalized log returns, plus log relative volume versus preceding
  training sessions at that minute. A stamped candle becomes provisionally
  available at `t+2` minutes. The source has not guaranteed that convention.
- Compare reference closes stamped `t+3` and `t+18` in the same ordinary
  09:15–15:15 IST continuous session. Exclude auction bars and incomplete
  sessions. The August 3 change from 375 to 360 observed minutes remains a
  structural-regime limitation even with the common window.
- Classes, in order: return `< -20.6` bps; `[-20.6, +20.6]` bps; return
  `> +20.6` bps. The null is the three April–July class frequencies.
- Standardize each feature with April–July population mean and standard
  deviation (replace a zero standard deviation with one). Fit one multinomial
  softmax regression with neutral as reference class. Minimize **summed**
  negative log likelihood plus `0.5 × 10 × sum(nonintercept coefficients²)`;
  intercepts are unpenalized. No feature, penalty or probability cutoff search.
- Per-example multiclass Brier is `sum((p_k - 1[y=k])²)` over all three
  classes, range 0–2. Average examples within each November session, then
  average sessions equally. The primary contrast is model minus null.
  Resample complete session contrasts 5,000 times with seed 20261019; use the
  2.5th and 97.5th percentiles. Only an upper bound below zero supports
  improvement. Otherwise the result is inconclusive or worse.
- All November probabilities and their feature/label timestamps are saved in
  new-only JSONL; reports and frozen models also use new-only paths. A replay
  cannot overwrite them. Score only after a complete, checksummed November
  response and an independently evidenced full November session-date list.

## Separate economic check

Illustrative round-trip costs for a ₹5,000 position are fixed at 20.6 bps
centrally, with 15.6 and 30.6 bps sensitivities. The artifact freezes the
April–July mean reference return *within each of the three classes*. November
class probabilities weight those training-only magnitude assumptions to give
an ex-ante expected reference return; each cost is then subtracted. This is a
provisional magnitude approximation, not expected executable P&L. No
probability threshold, selected subset, order simulation or trading rule is
evaluated. Actual bid/ask, fills and adjustment semantics remain unverified.

## Commands and stop conditions

After code review/commit, freeze once from the existing verified responses:

```sh
.venv/bin/python -m quant.apps.prospective_15m freeze \
  --raw-root data/historical/raw \
  --frozen data/experiments/reliance-15m-v1/frozen-model.json
```

Future evaluation requires the existing read-only acquisition workflow to save
`data/historical/raw/2885-1-2026-11-01-2026-12-01.json`, its `.sha256` and
`.meta.json`, with receipt **after November ends**. No acquisition is authorized
by this document. It also requires a local JSON file such as
`data/experiments/reliance-15m-v1/november-calendar.json` containing a dated,
independently verified source and the exact ordinary session dates:

```json
{"verified": true, "source": "DATED_OFFICIAL_EXCHANGE_CALENDAR_REFERENCE", "sessions": ["2026-11-DD"]}
```

The example date is a placeholder, not a calendar claim. Supply the actual
complete list before evaluation. Then run exactly:

```sh
.venv/bin/python -m quant.apps.prospective_15m evaluate \
  --raw-root data/historical/raw \
  --frozen data/experiments/reliance-15m-v1/frozen-model.json \
  --calendar data/experiments/reliance-15m-v1/november-calendar.json \
  --output-dir data/experiments/reliance-15m-v1/november-2026
```

Stop on changed source or code hashes, missing or incomplete November response,
unverified or mismatched calendar dates, fewer than 15 ordinary sessions,
noncontinuous 360-bar common sessions, or an existing output path. Do not
repair these conditions by silently filling bars or changing the specification.
Provider timestamp, adjustment and historical identity uncertainties remain
promotion blockers regardless of the score.
