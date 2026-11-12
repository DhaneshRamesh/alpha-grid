# Protocol: EXP-60M-BRACKETED-OPPORTUNITY-V1

**Status: FROZEN EXPLORATORY PROTOCOL (2026-11-07 IST)**  
**Classification: DATA_ONLY / NO LIVE TRADING / NO BROKER ORDERS**  
**Author: Senior Quantitative Researcher & Systems Architect**

---

## 1. Executive Context & Objective

Prior research on AlphaGrid M1 (15-minute horizon) demonstrated statistically significant predictive lift (out-of-sample Brier reduction $\Delta = -0.004462$, $p < 0.001$ under session-clustered bootstrap), but **negative net trading returns** across all friction scenarios ($-6.84\text{ bps}$ to $-9.20\text{ bps}$ net). 

The empirical cause of this failure is structural:
1. **Unavoidable Taker Friction Floor**: In NSE cash equities with ₹15,000 position sizing, published Dhan intraday brokerage and statutory levies amount to **$10.49\text{ bps}$**, and quoted bid-ask spread crossing costs **$5.00\text{ bps}$**, establishing an inescapable friction floor of **$15.49\text{ bps}$** round-trip.
2. **Horizon-Return Compression**: At a 15-minute horizon, gross realized returns average $+5.5$ to $+7.4\text{ bps}$, structurally insufficient to clear $15.5\text{ bps}$ of friction.
3. **Excursion Pullback**: At 60 minutes, gross Maximum Favorable Excursion (MFE) averages $+40.5\text{ bps}$ (breakouts) to $+43.9\text{ bps}$ (M1 signals). However, naive fixed time-based exit at minute 60 allows price to fade back to a mean realized return of only $+1.75\text{ to } +8.25\text{ bps}$, producing a net loss of $-7.24\text{ bps}$.

**Objective of EXP-60M-BRACKETED-OPPORTUNITY-V1**:  
Establish whether capturing intra-window favorable excursions via **predeclared asymmetric bracketed exits** (profit target $\ge +25\text{ bps}$, stop loss $\le -20\text{ bps}$) or **passive maker queue fills** achieves positive risk-adjusted net trading return over realistic costs, before examining 60-minute outcomes.

---

## 2. Predeclared Hypotheses & Nulls

* **Primary Economic Null ($H_{0,\text{econ}}$)**:  
  No predeclared execution arm generates an out-of-sample mean per-trade net return greater than zero after realistic Dhan brokerage, statutory fees, and quoted spread:
  $$\mathbb{E}[\text{Net Return}_{\text{trade}}] \le 0\text{ bps}$$
* **Secondary Statistical Null ($H_{0,\text{stat}}$)**:  
  Point-in-time causal features provide no out-of-sample Brier score or Log Loss reduction over the unconditional base rate for predicting favorable excursion events ($\text{MFE}_{60\text{m}} \ge 25\text{ bps}$):
  $$\Delta \text{Brier} = \text{Brier}_{\text{model}} - \text{Brier}_{\text{base}} \ge 0$$
* **Directional Alternative**:  
  Predeclared asymmetric bracketed exits capture intra-window momentum excursions while cutting left-tail adverse excursion, yielding an out-of-sample mean net return $\ge +5.0\text{ bps}$ under realistic friction.

---

## 3. Causal Point-in-Time Features

For any candidate decision at minute $T$ (e.g., 10:00:00 IST), all features are strictly derived from minute bars completed at or before $T-1$ (09:59:59 IST). No contemporaneous bar $T$ data or forward bars are visible:

1. `r_5m_bps`: Trailing 5-minute return: $\frac{C_{T-1} - C_{T-6}}{C_{T-6}} \times 10,000$.
2. `r_15m_bps`: Trailing 15-minute return: $\frac{C_{T-1} - C_{T-16}}{C_{T-16}} \times 10,000$.
3. `breakout_dist_bps`: Distance relative to session 30-minute opening high ($[09:15, 09:44]$): $\frac{C_{T-1} - H_{\text{open}}}{H_{\text{open}}} \times 10,000$.
4. `rel_vol_15m`: Volume in $[T-15, T-1]$ divided by half of volume in $[T-45, T-16]$.
5. `vol_15m_bps`: Standard deviation of 1-minute close returns over $[T-15, T-1] \times 10,000$.
6. `tod_norm`: Normalized time of day: $\frac{\text{Minutes from 09:15 IST}}{375.0}$.
7. `opening_range_width_bps`: $\frac{H_{\text{open}} - L_{\text{open}}}{O_{09:15}} \times 10,000$.

---

## 4. Executable Fill Semantics & Eliminating Unrealizable Fills

* **Entry Timing**:  
  * Market taker orders are filled at **Bar $T$ Open ($O_T$)**, representing the earliest executable price following signal computation at the close of $T-1$.
  * Filling at Bar $T-1$ close ($C_{T-1}$) is **strictly prohibited**.
* **Exit Timing & Minute-Bar Order Simulation**:  
  For bracketed orders over window $[T, T+59]$:
  * In each minute $t \in [T, T+59]$:
    * If high $H_t \ge \text{Target Price} = O_T \times (1 + \frac{25}{10000})$: filled at Target Price ($+25.0\text{ bps}$ gross).
    * If low $L_t \le \text{Stop Price} = O_T \times (1 - \frac{20}{10000})$: filled at Stop Price ($-20.0\text{ bps}$ gross).
    * If both $H_t \ge \text{Target}$ and $L_t \le \text{Stop}$ within the same 1-minute bar, conservative convention assumes the **Stop Loss triggered first**.
    * If neither target nor stop triggers by minute $T+59$, position exits at **$C_{T+59}$** (timeout close).

---

## 5. Predeclared Comparative Arms

All arms operate under identical portfolio constraints (₹15,000 max per position, 3 max concurrent positions, ₹15,000 max deployed capital, ₹1,500 daily session loss limit):

1. **Arm 0: NO_TRADE Baseline** (Benchmark): Complete abstention; 0 trades, 0 PnL.
2. **Arm 1: MOMENTUM_FIXED_60M** (Reference Taker):  
   Enter at $O_T$ if $r_{15\text{m}} \ge 35\text{ bps}$. Exit at $C_{T+59}$ (fixed 60m timeout).
3. **Arm 2: BREAKOUT_FIXED_60M** (Reference Taker):  
   Enter at $O_T$ if `breakout_dist_bps` $> 0$ and $r_{15\text{m}} > 0$. Exit at $C_{T+59}$ (fixed 60m timeout).
4. **Arm 3: BREAKOUT_BRACKET_ASYMMETRIC** (Primary Test Taker):  
   Enter at $O_T$ if `breakout_dist_bps` $> 0$ and $r_{15\text{m}} > 0$. Exit via $+25\text{ bps}$ target, $-20\text{ bps}$ stop, or 60m timeout.
5. **Arm 4: MAKER_BRACKET_PASSIVE** (Primary Test Maker):  
   Resting limit bid at $O_T - 2.5\text{ bps}$ (midpoint/bid, eliminating half-spread). Filled only if low $L_T \le \text{Limit Price}$ and volume $\ge$ order size. Exit via Arm 3's asymmetric bracket.

---

## 6. Official Fee Schedule & Friction Scenarios

Evaluated on ₹15,000 position sizing using `quant.research.prospective.compute_dhan_intraday_fees`:
* **Brokerage**: $0.03\%$ capped at ₹20 $\implies$ ₹4.50 buy + ₹4.50 sell = ₹9.00 ($6.00\text{ bps}$).
* **STT**: $0.025\%$ on sell, rounded to nearest rupee = ₹4.00 ($2.67\text{ bps}$).
* **NSE Transaction Charge**: $0.0030699\%$ on turnover = ₹0.92 ($0.61\text{ bps}$).
* **SEBI & IPFT Turnover**: ₹10/crore + ₹0.01/crore = ₹0.03 ($0.02\text{ bps}$).
* **GST**: $18\%$ on (brokerage + txn + sebi) = ₹1.79 ($1.19\text{ bps}$).
* **Stamp Duty**: $0.003\%$ on buy = ₹0.00 (quantized to ₹0 on ₹15k).
* **Total Statutory + Brokerage Fee**: **₹15.74 / 10.49 bps**.

### Friction Scenarios Evaluated
1. **Scenario 1 (Friction Floor / Zero Slippage)**:  
   * Taker: $10.49\text{ bps}$ fees + $5.00\text{ bps}$ spread = **$15.49\text{ bps}$**.
   * Maker: $10.49\text{ bps}$ fees + $0.00\text{ bps}$ spread = **$10.49\text{ bps}$**.
2. **Scenario 2 (Base Realistic Slippage)**:  
   * Taker: $10.49\text{ bps}$ fees + $5.00\text{ bps}$ spread + $5.00\text{ bps}$ slippage = **$20.49\text{ bps}$**.
   * Maker: $10.49\text{ bps}$ fees + $0.00\text{ bps}$ spread + $2.50\text{ bps}$ slippage = **$12.99\text{ bps}$**.
3. **Scenario 3 (Conservative Stress)**:  
   * Taker: $10.49\text{ bps}$ fees + $10.00\text{ bps}$ spread + $10.00\text{ bps}$ slippage = **$30.49\text{ bps}$**.

---

## 7. Universe, Date Partitions & Holdout Integrity

### Universe
* **19 Active Modeling Equities**: `RELIANCE`, `HDFCBANK`, `INFY`, `ICICIBANK`, `BHARTIARTL`, `SBIN`, `LT`, `AXISBANK`, `BAJFINANCE`, `M&M`, `TECHM`, `HCLTECH`, `SHRIRAMFIN`, `BSE`, `MCX`, `PCJEWELLER`, `ETERNAL`, `NETWEB`, `CUPID`.
* **5 Reserved Stock Holdouts (STRICTLY PRESERVED & UNTOUCHED)**:  
  `TCS`, `KOTAKBANK`, `MARUTI`, `BHEL`, `DIVISLAB`. Zero analysis, zero modeling.

### Temporal Partitions
* **Split 1 (In-Sample Fitting / Feature Baseline)**: November 1, 2023 to April 30, 2025 (~360 sessions).
* **Split 2 (Development Evidence / Tuning)**: May 1, 2025 to October 31, 2025 (126 sessions).  
  *Explicit declaration*: Because this split was inspected for diagnostic exploratory metrics, it is **development evidence**, not a pristine holdout.
* **Split 3 (Pristine Temporal Holdout — STRICTLY LOCKED)**: November 1, 2025 to October 31, 2026. Zero inspection, zero fitting.

### Session & Mask Criteria
* Standard continuous sessions only (09:15 to 15:30 IST).
* Exclude special Disaster Recovery sessions (2024-01-20, 2024-05-18) and Muhurat evening sessions.
* Mask corporate action ex-dates per symbol.
* Minimum 30 valid opening bars $[09:15, 09:44]$.
* Valid decision times: Every 15 minutes from 10:00 to 14:15 IST (18 slots/session). Decisions after 14:15 IST are excluded to ensure the full 60m window matures before the 15:15 IST CAS boundary.
* **Purging**: 60-minute non-overlapping purge window between sequential decisions on the same symbol during model training.

---

## 8. Statistical Uncertainty Protocol

* Block bootstrap resampled strictly by **calendar session date** ($B = 2,000$ draws, fixed seed `20261107`).
* Reported statistics:
  * Mean per-trade net return (bps) with 95% bootstrap confidence interval.
  * Win rate (% of trades with net return $> 0$).
  * Profit factor: $\frac{\sum \text{Gross Gains}}{\sum \text{Gross Losses}}$.
  * Maximum single-session drawdown (INR).
  * Favorable excursion Brier score lift and Log Loss lift.

---

## 9. Predeclared Acceptance & Stop Criteria

### Acceptance Criteria (Proceed to Shadow Execution Engine)
1. **Rejection of $H_{0,\text{econ}}$**: Primary Test Arm (Arm 3 or Arm 4) achieves mean net return with a **95% session-clustered bootstrap lower bound $> 0.0\text{ bps}$** under Scenario 2 (Base Realistic Slippage).
2. **Statistical Lift**: Out-of-sample Brier score reduction $\Delta \text{Brier} < -0.005$ with $95\%$ CI entirely below zero.
3. **Execution Sample**: Minimum $N \ge 100$ executed trades across $\ge 30$ independent sessions.
4. **Risk Profile**: Profit factor $> 1.25$ and maximum session loss within the ₹1,500 risk limit.

### Stop Criteria (Permanent Halt of Cash Equity Taker Track)
1. If **no arm** achieves mean net return $> 0.0\text{ bps}$ even under Scenario 1 (Zero Slippage / Friction Floor of $15.49\text{ bps}$).
2. If adverse excursion (MAE) triggers the $-20\text{ bps}$ stop-loss before the $+25\text{ bps}$ target in $\ge 60\%$ of trades.
3. If Maker fill probability in Arm 4 is $< 20\%$, rendering passive queue capture unviable.
*Consequence of Stop Trigger*: Formally declare that NSE cash equity intraday breakout/momentum cannot overcome exchange fees and bid-ask spread; close the cash equity taker research track and document the finding in `docs/context/DECISIONS.md`.

