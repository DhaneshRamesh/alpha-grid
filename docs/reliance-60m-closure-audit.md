# RELIANCE 60-minute volume candidate closure audit — 2026-11-02 IST

**STOP this candidate; retain DATA_ONLY / NO TRADE.** This is an independent, read-only reconciliation of the frozen [protocol](reliance-60m-frozen-protocol.md), [result note](reliance-60m-first-signal-result.md), pinned Dhan bars and saved ignored JSON (`ce65b9828d2f867e48ec5ca4803a4fcff0e79b0d136517a47104d64982ae80bd`). The source verifier rechecked the three originals (45,945 bars, 125 sessions). No Dhan request, fit, threshold change, paper action or source write occurred.

## Arithmetic and exact scope

The independent check reconstructed the first eligible unconditional, positive 15-minute-momentum and volume-confirmed observation from the original bar arrays, with a prior-20-session volume median and 60/120/300-second assumed publication delays. It recomputed the reference return from the chosen entry and decision+60-minute closes. For every B delay, 39 distinct dates selected one opportunity; the three other evaluation sessions contribute **zero** to the 42-session net mean. A and unconditional each selected 42 dates.

The saved model subtracts a fixed **8.268 bps** equal-₹100,000 buy/sell Dhan fee estimate and **40 bps** of assumed round-trip spread, slippage and latency on selected days. For example, at 60 seconds B's selected gross mean is +11.453232 bps; its selected-day net is −36.814768 bps; its reported 42-day mean is `(39/42) × −36.814768 = −34.185141` bps. Thus subtracting 48.268 directly from 11.453232 and comparing that result to the report uses the wrong denominator.

[Dhan's published retail intraday schedule](https://dhan.co/pricing/) supplies the brokerage, NSE transaction, SEBI, IPFT, GST, sell STT, buy stamp and rounding rates. A separate hypothetical **whole-share** calculation used each opportunity's actual reference entry and exit prices, quantity `floor(₹100,000 / entry)`, different buy and sell notionals, and rounded charges. These are bar-close references, not actual trades, quotes or contract notes. On August 3 at 60 seconds, B's first selected reference is 76 shares bought at ₹1,313.90 (₹99,856.40) and sold at ₹1,312.10 (₹99,719.60). The estimated charges are brokerage ₹40.00, NSE ₹6.13, SEBI ₹0.20, IPFT ₹0, GST ₹8.34, STT ₹25 and stamp ₹3, totaling **₹82.67** or **8.278888 bps of buy value**. Its gross reference return is **−13.699673 bps**, and its reference net after this fee and assumed 40 bps is **−61.978561 bps**. No fill is implied.

| Assumed delay | B selected gross mean | Fixed-fee 42-session net, saved | Whole-share mean fee on selected B opportunities | Whole-share 42-session net | Difference from saved |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 60 s | +11.453232 bps | −34.185141 bps | 8.315502 bps | −34.229250 bps | −0.044109 bps |
| 120 s | +9.840450 bps | −35.682725 bps | 8.312139 bps | −35.723711 bps | −0.040986 bps |
| 300 s | +6.741391 bps | −38.560422 bps | 8.315680 bps | −38.604697 bps | −0.044275 bps |

Whole-share fee differences reflect quantity truncation, differing sell values and rounding; they do not repair the negative result. Contract-note aggregation and actual execution remain unknown.

## Reproduced comparisons and uncertainty

The independent 42-date paired net comparison reproduced B minus A means **+12.798086 / +12.703146 / +12.175236 bps** at 60/120/300 seconds and the saved 2,000-resample intervals **[+0.317598,+26.468532] / [−0.674859,+26.771461] / [−1.752323,+26.453341]**. B minus unconditional means were **+12.519374 / +13.340424 / +11.012862 bps**, intervals **[−0.985557,+25.990207] / [+0.122906,+27.303132] / [−3.523962,+24.843999]**. The B versus NO TRADE intervals are B's net intervals: **[−45.016603,−22.412231] / [−46.038526,−25.070226] / [−48.228559,−28.175405]**. All use complete-session resampling, including three zero-selection B days, and the saved fixed seeds. The 39 B opportunities correspond to 39 distinct selected sessions; across-day dependence and prior inspection remain limitations. Whole-share fees change paired mean contrasts by less than 0.006 bps.

Using only earlier mature eligible sessions, independent Laplace-smoothed scoring reproduced B's favorable Brier/log loss against the unconditional probability on the *same 39 selected dates*: **0.095367/0.344342 vs 0.092069/0.330854** at 60 s; **0.113490/0.389425 vs 0.111022/0.379498** at 120 s; **0.113530/0.390785 vs 0.113078/0.388337** at 300 s. The adverse pairs were **0.030588/0.161002 vs 0.030042/0.159212**; **0.011865/0.113255 vs 0.007697/0.091057**; and **0.016149/0.133509 vs 0.006290/0.082338**. Lower is better; B is worse on both event scores at every delay. Favorable event counts were 4/39, 5/39 and 5/39; adverse counts 1/39, 0/39 and 0/39 at the frozen 48.268-bps threshold.

## Hypothetical break-even allowance

The signed headroom below is selected-opportunity mean gross return **minus the mean whole-share published-fee estimate**. A positive number is the *maximum combined additional round-trip spread, slippage and latency allowance at zero mean reference net*, before any uncertainty allowance. A negative number means the mean is below estimated fees even with zero extra friction. It is neither an observed cost nor executable profitability.

| Assumed delay | Unconditional headroom | Momentum A headroom | Volume B headroom |
| ---: | ---: | ---: | ---: |
| 60 s | −6.752 bps | −7.031 bps | **+3.138 bps** |
| 120 s | −9.069 bps | −8.433 bps | **+1.528 bps** |
| 300 s | −9.623 bps | −10.784 bps | **−1.574 bps** |

If the frozen **10-bps latency allowance** alone is retained, B has **no nonnegative spread/slippage budget at any delay**. The frozen combined 40-bps assumption exceeds the positive 60/120-s headrooms by 36.862/38.472 bps. The same headroom sign follows from the 42-session mean because missed days contribute zero. A lower confidence bound on B's own gross net would be still less favorable.

## Information decision

This candidate fails the frozen conservative-net rule, fails robust paired uncertainty across timing scenarios, and scores favorable/adverse probabilities worse than unconditional. The result rejects this **particular** long-only RELIANCE first-signal volume filter on an inspected August–September sample. It does not test every 60-minute instrument, feature, time or decision rule.

The smallest useful next *joint* evidence is prospective, timing-qualified **bid/ask prices and available size at the frozen candidate and matched momentum-baseline entry/exit references**, across independent dated sessions, with source identity and clock evidence. It would measure quoted spread and a liquidity bound while allowing a fresh paired gross comparison. Even this cannot prove actual slippage or fills. A timing contract or separately approved prospective availability probe is a prerequisite; the November 3 probe is still NO GO. More historical OHLCV can narrow an exploratory gross estimate but cannot recover publication time or execution cost. Observed synchronized spread/liquidity has higher information value for distinguishing a gross association from a cost barrier, but requires a separately authorized, valid capture and a fresh prospective sample. No further collection costs nothing and is the appropriate choice **for this stopped candidate now**. No new signal hypothesis is recommended until those gates justify the collection cost.

The saved source archive, failed experiment and frozen November specification remain unchanged. QJ01/QP01 invariants and DATA_ONLY / NO TRADE remain in force.
