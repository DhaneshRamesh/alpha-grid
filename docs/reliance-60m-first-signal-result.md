# RELIANCE first-signal 60-minute falsification — 2026-11-02 IST

**Recommendation: STOP this candidate. DATA_ONLY / NO TRADE.** The volume filter has a larger gross reference move than first positive-momentum signals, but fails the frozen conservative net, timing-sensitivity and probability-scoring rules. No logistic model was fitted, no prediction was connected to QP01, and no authenticated Dhan call or broker order occurred.

## Frozen question, genuine data and eligibility

The [protocol](reliance-60m-frozen-protocol.md) was written before this first-signal analysis. It uses the earlier [preregistered definition](research-acquisition.md): positive trailing 15-minute close momentum; candidate adds five completed minute volumes ≥2× the median same bucket sum from the prior 20 source sessions. First eligible signal per session; an unconditional first eligible long reference and NO TRADE are comparators. Exact decision, outcome, fit/evaluation and cost rules are in the frozen protocol. [Jegadeesh and Titman (1993)](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.1993.tb04702.x) concerns 3–12 month momentum; it motivates a falsifiable momentum question but supplies no 15-minute or 60-minute intraday guarantee.

The [offline code](../src/quant/apps/reliance_60m_falsification.py) called the existing pinned raw-response verifier and session coverage check. All three original HTTP 200 response hashes matched the protocol; 45,945 original bars formed 125 intact sessions. This provides source integrity, not Dhan candle-publication timing, historical identity continuity, RELIANCE-specific CAS applicability, adjustment continuity, or execution prices. The 42 August–September dates are already inspected exploratory data. Prior May–July sessions plus matured earlier evaluation dates supplied expanding, session-grouped probability fits; July 31 was excluded and the existing walk-forward splitter applied label maturity and a one-day embargo. October was checked for source integrity but omitted from scoring; frozen November data was not used. No signal with an immature 60-minute window was scored. First-signal B selected 39 of 42 evaluation sessions (one opportunity each); A and unconditional selected 42. Fifteen late B signal observations were censored per delay before searching the same day for an eligible one.

The source spans 63 sessions before the August 3 market-structure change (observed 09:15–15:29 continuous minute labels) and 62 sessions from August 3 onward (observed 09:15–15:14 labels). Of the latter, 42 August–September sessions were scored and 20 October sessions were source-checked only. The prior-20-volume references may cross the August 3 boundary; the pre/post shapes were verified separately in the existing six-month audit, while RELIANCE's dated CAS classification remains unresolved. Jul 31 is part of the source and can supply prior observable volume, but its outcomes are embargoed from probability fitting.

The assumed timestamp is bar **start**; separate assumed publication delays are 60, 120 and 300 seconds after inferred completion. Entry and exits are future completed-bar **close references**. The 60-minute exit is decision+60 minutes, the 30-minute and final continuous-session outcomes are secondary descriptions on the *same selected decision*. Future highs/lows describe ranges only. These assumptions cannot establish causal eligibility or fills.

## Charges and assumed market friction

[Dhan's current retail intraday equity schedule](https://dhan.co/pricing/) publishes brokerage min(₹20, 0.03% of trade value) per order; NSE transaction 0.0030699% of turnover; SEBI 0.0001%; IPFT 0.0000001%; GST 18% of applicable charges; sell STT 0.025%; and buy stamp 0.003%. STT and stamp round to rupees; other components to paise. Equal-value buy/sell estimates below are approximate, exclude optional auto-square-off, and are not contract notes.

| Equal buy/sell value | Brokerage | NSE transaction | SEBI | IPFT | GST | STT | Stamp | Estimated total | Total bps |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ₹10,000 | ₹6.00 | ₹0.61 | ₹0.02 | ₹0.00 | ₹1.19 | ₹3 | ₹0 | ₹10.82 | 10.820 |
| ₹100,000 | ₹40.00 | ₹6.14 | ₹0.20 | ₹0.00 | ₹8.34 | ₹25 | ₹3 | ₹82.68 | 8.268 |
| ₹500,000 | ₹40.00 | ₹30.70 | ₹1.00 | ₹0.00 | ₹12.91 | ₹125 | ₹15 | ₹224.61 | 4.492 |

At ₹100,000, separately **assumed** round-trip spread/slippage/latency are 5/10/5 bps (base, 28.268 bps including fees) and 10/20/10 bps (conservative, **48.268 bps** including fees). None was measured. Fee-only is an optimistic sensitivity, not a feasible execution bound.

## Chronological evaluation

Means below are equal-session bps; a missing signal contributes zero. Bootstrap intervals resample complete sessions 2,000 times. There is one selected opportunity per candidate per day, so 39 B opportunities are 39 independent *session groups*, although price paths and market regimes remain dependent across days.

| Assumed delay | Candidate | Sessions selected / 42 | Gross 60m mean | Fee-only net | Base net | Conservative net, 95% session interval |
| ---: | --- | ---: | ---: | ---: | ---: | --- |
| 60 s | Unconditional | 42 | 1.563 | −6.705 | −26.705 | −46.705 [−58.511, −32.379] |
| 60 s | Momentum A | 42 | 1.285 | −6.983 | −26.983 | −46.983 [−58.857, −33.968] |
| 60 s | Volume B | 39 | 11.453 | 2.958 | −15.614 | −34.185 [−45.017, −22.412] |
| 120 s | Unconditional | 42 | −0.755 | −9.023 | −29.023 | −49.023 [−59.837, −35.882] |
| 120 s | Momentum A | 42 | −0.118 | −8.386 | −28.386 | −48.386 [−59.284, −35.923] |
| 120 s | Volume B | 39 | 9.840 | 1.460 | −17.111 | −35.683 [−46.039, −25.070] |
| 300 s | Unconditional | 42 | −1.305 | −9.573 | −29.573 | −49.573 [−60.132, −37.546] |
| 300 s | Momentum A | 42 | −2.468 | −10.736 | −30.736 | −50.736 [−61.712, −39.362] |
| 300 s | Volume B | 39 | 6.741 | −1.418 | −19.989 | −38.560 [−48.229, −28.175] |

B minus A conservative daily-net means were +12.798, +12.703 and +12.175 bps for 60/120/300 s; paired session-bootstrap intervals were **[+0.318,+26.469]**, **[−0.675,+26.771]** and **[−1.752,+26.453]**. B improves the relative point estimate, but the 120/300 s lower limits cross zero and B's own net interval remains entirely negative. B's three chronological 14-session block mean net bps were −31.250/−31.175/−40.131 at 60 s; −31.413/−35.229/−40.406 at 120 s; −37.437/−39.137/−39.107 at 300 s. This is not an untouched test or a multiplicity-adjusted discovery.

B minus unconditional conservative daily-net means were +12.519, +13.340 and +11.013 bps; paired whole-session intervals were **[−0.986,+25.990]**, **[+0.123,+27.303]** and **[−3.524,+24.844]**. The contrast fails the 60/300 s uncertainty requirement as well.

## Movement and probability scoring

For B, 60m gross p05/median/p95 were **−31.583/+7.063/+74.949 bps** at 60 s; **−34.976/+3.924/+69.096** at 120 s; **−33.273/−2.298/+80.198** at 300 s. With the frozen 48.268-bps event threshold, favorable counts were 4/39, 5/39, 5/39, with favorable mean moves +90.015, +77.768, +74.506 bps. Adverse counts were 1/39, 0/39, 0/39; the single adverse move was −57.200 bps. Zero observed adverse events in 39 selections is weak tail evidence. Future high/low range distributions are in the private report and are not fill estimates. B's secondary 30m gross means were +5.915/+5.494/+5.479 bps; remaining-session means +4.678/+3.609/+1.619 bps. Both are descriptive, with no independent selection.

Each score uses a Laplace-smoothed event probability fitted only on earlier eligible sessions. The unconditional probability is scored on the **same selected days**, allowing a like-for-like Brier/log-loss comparison. B's favorable Brier/log-loss versus unconditional were 0.0954/0.3443 vs 0.0921/0.3309 at 60 s; 0.1135/0.3894 vs 0.1110/0.3795 at 120 s; and 0.1135/0.3908 vs 0.1131/0.3883 at 300 s. B's adverse Brier/log-loss versus unconditional were 0.0306/0.1610 vs 0.0300/0.1592; 0.0119/0.1133 vs 0.0077/0.0911; and 0.0161/0.1335 vs 0.0063/0.0823. In all scenarios both event scores are worse. Mean predicted versus observed favorable rates for B were 0.158 vs 0.103; 0.171 vs 0.128; 0.129 vs 0.128. Predicted versus observed adverse rates were 0.104 vs 0.026; 0.107 vs 0; 0.125 vs 0. Poor calibration and sparse tail events do not justify a logistic fit.

## Software safety and reproduction

The focused synthetic **software-only** test checks that B cannot select before 20 prior sessions and that changing a future close changes only its outcome; a second test checks half-up STT rounding. Focused Python tests passed **4/4**, Ruff passed, and isolated `cargo test -p quant-paper --lib` passed **14/14**. Existing Rust tests exercise paper-intent risk rejection (stale/spread/uncalibrated/loss/exposure), duplicate suppression, cancellation, fee/accounting, QP01 recovery and failed-sync poisoning, reconnect behavior and halt/session-end controls. No integration with exploratory predictions was made. This suite does not prove a connected broker reconciliation flow or executable pricing.

Reproduce offline with:

```sh
PYTHONPATH=src .venv/bin/python -B -m quant.apps.reliance_60m_falsification \
  --output data/historical/research/NEW-PRIVATE-NAME.json
```

The ignored owner-private report is `data/historical/research/reliance-60m-first-signal-v1.json`, SHA-256 `ce65b9828d2f867e48ec5ca4803a4fcff0e79b0d136517a47104d64982ae80bd`. It holds daily reference nets, aggregate probability scores, source metadata, cost components, and movement ranges. The pinned raw responses and app reproduce individual choices. This study made **zero** Dhan requests.

The repository context notes were updated. The required `tools/repo-context.ps1 -Check` command could not run because neither `powershell` nor `pwsh` is installed on this Mac, so `docs/context/merkle.json` was not refreshed.

Remaining blockers to shadow prediction are independently established candle first availability and revision behavior, dated full-range instrument identity and corporate-action treatment, RELIANCE-specific CAS eligibility, observed spread/slippage/latency and a new untouched prospective evaluation. The November 3 probe remains NO GO pending its separate preflight and authorization. Connected paper or execution additionally requires an independently validated live feed, broker reconciliation and order path, and explicit promotion/authorization. The existing November 15-minute experiment, risk limits and NO TRADE controls were not changed.
