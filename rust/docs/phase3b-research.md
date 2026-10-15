# Phase 3B: offline statistical research

This package is **offline research only**. No Dhan login, cloud VM, order API,
market subscription, running-session model update, paper-policy replacement or
live execution is part of this milestone. QJ01 and QP01 bytes/formats are
unchanged. The current paper decision model remains `NotTrained`.

## Reproducible dataset

The Rust bridge streams existing Postcard QJ01 records to JSONL with a source
record index; it never alters the journal. The Python builder verifies every
QP01 length/checksum/schema, joins each paper decision to the exact QJ01 depth
record by **session, index, instrument and receive timestamp**, rejects a
future processing/depth timestamp, and writes separate feature and label
Parquet files with Zstd. The manifest records source SHA-256 hashes, canonical
row hashes, dataset hash, schema, horizon/censor policy, coverage and
sufficiency reasons. Output directories must be new. `uv.lock`, `Cargo.lock`,
Rust 1.99 and Node 24 pin reproducible environments.

From the repository root, after `uv sync --extra dev --locked`:

```sh
cargo build --locked --release -p quant-replay --bin export_jsonl --manifest-path rust/Cargo.toml
mkdir -p .local/phase3b
gzip -dc docs/benchmarks/phase3a-isolated-2026-10-15/demo.qj.gz > .local/phase3b/demo.qj
rust/target/release/export_jsonl .local/phase3b/demo.qj > .local/phase3b/demo.jsonl
uv run --locked --extra dev python -m quant.apps.build_research_dataset \
  --market-jsonl .local/phase3b/demo.jsonl \
  --paper-qp docs/benchmarks/phase3a-isolated-2026-10-15/run1/risk_filtered.qp.gz \
  --output-dir .local/phase3b/dataset-NEW --source-kind SYNTHETIC
```

The exporter reads a single QJ01
file; segmented journals must first be replayed in manifest order using the
existing segmented reader. It intentionally fails on a corrupt source frame.
The Python builder currently holds exported rows in memory; multi-session
production scale needs a streaming/partitioned pass before large capture.
Parquet columns are typed by Arrow inference. Raw QJ01 remains authoritative.

The 962-record committed fixture contains **one artificial market session**.
It yields 240 decision rows and 1,200 horizon rows at 5/15/30/60/180 seconds.
Three Phase 3A replays of it are not independent sessions. The builder marks it
`NOT_TRAINED`; no performance statistic is published. The row count/hash may
change when the research schema changes. The explicit source kind is provenance,
not proof that a capture is real. The CLI offers only `SYNTHETIC` and
`REAL_UNVERIFIED`; independently audited capture/quality evidence is needed
before an experiment can be marked `REAL_VERIFIED` in a controlled workflow.

## Causal and label rules

The decision context is serialized at its QP01 decision source record. Its
market, feature and health snapshots must have processing times no later than
the QJ01 source receive time. Features include quoted price/book, relative
spread, microprice displacement, top/multilevel imbalance, snapshot OFI,
rolling price volatility/returns and explicit volume/freshness flags. Volume
activity must be excluded when the volume interval is incomplete. Quote-sampled
LTP is **not** a verified exchange trade, and `Depth` is an aggregated paired
snapshot, not order additions/cancellations.

Future `Price`/`Quote` LTP observations from the **same instrument and session**
and with later source index are used only for labels. For horizon H, the price
return is the last observed price at/before H divided by the decision-time
quote price minus one. It is available only when a later observation witnesses
coverage at/after H within the configured maximum observation gap. There is
no interpolation, fictitious tick, assumed barrier fill or guaranteed exit
price. MFE = max observed price minus entry price; MAE = min observed price
minus entry price over the observed window. First favorable/adverse barrier
is the earliest **observed** crossing. A first hit stays known even if the
recording later ends. If no hit is observed, a `NEITHER` label requires full
horizon coverage. A disconnect, session end, excessive observation gap or
recording end creates right censoring. Conflicting prices at the same receive
timestamp create an ambiguous label. A jump between samples may hide an
intra-interval crossing, so all barrier outcomes describe the observed path
only. Receive clocks order observations; exchange timestamp semantics and
clock sync are unverified.

## Reference mathematics and eligibility

The configurable gate checks verified source provenance, independent sessions,
usable outcome count, forward coverage, price/book quality and, when a model
uses volume, volume completeness. The default 20 sessions/500 usable labels/30 events per competing cause
are **minimum engineering guards**, not a declaration of statistical power;
confidence precision, effective sample size, regimes and selection breadth
still determine adequacy. `ConditionalBaseline.fit` refuses to run without a
passing gate; `known_answer_fixture=True` exists only for deterministic tests
and emits `TEST_FIXTURE_UNVALIDATED`.

- Empirical conditional: equal-weight outcomes within a predeclared scaled
  radius. KNN: equal-weight k nearest rows. Kernel: Gaussian weights
  `exp(-||x-z||² / (2h²))`; weighted mean/quantiles/event fraction. All scaling
  uses training rows only. Event fractions are **not calibrated probabilities**.
- Ridge: minimize `||y-Xβ||² + λ||β_nonintercept||²`; point forecast only.
  Feature dimension is capped at six. Missing/nonfinite inputs abstain.
- Competing risks: at event time t with n(t) at risk, event counts dA/dB,
  cumulative incidence increments `S(t-) dA/n`, `S(t-) dB/n`; survival becomes
  `S(t-) [1-(dA+dB)/n]`. Censors leave the risk set after that time. True
  no-hit at H is administrative complete horizon, distinct from censoring.
  These estimates assume independent censoring. Complete-case Brier is a
  limited diagnostic and can be biased when censoring depends on state.
- Walk-forward: chronological independent sessions, rolling or expanding
  training, validation, final test; purge rows whose outcome horizon plus
  embargo meets the next split. The offline runner uses a predeclared config
  and the final fold only. It does not certify validation or tune on test.
- Uncertainty: resample **sessions**, not adjacent ticks. The reported interval
  is percentile bootstrap of session-mean net outcome; expected shortfall is
  the empirical worst 5% of session outcomes. These require enough independent
  sessions and are descriptive under regime shifts. A single session returns
  unavailable.
- Local-level and dynamic-regression Kalman references propagate variance by
  elapsed seconds. OU uses an AR(1) fit only when sampling intervals are
  approximately regular: `κ=-log(φ)/Δt`; innovation variance and delta-method
  standard error are reported. Irregular quote sampling is rejected rather
  than treated as i.i.d. CUSUM and volatility regime are past-only references.
  State-space identifiability, observation noise and stationarity are unproven.
- Conservative utility in INR nanounits is expected gross minus fixed fees,
  spread, slippage, impact, liquidity, adverse/fill allowances, downside and
  uncertainty penalties. NO TRADE yields zero. Missing/uncalibrated forecasts,
  nonpositive utility and failed deterministic risk approval all abstain.
  Fee assumptions remain provisional until broker contract-note calibration.

The registry stores immutable, hashed model metadata and an explicit reversible
status history. Promotion requires a separate held-out evidence bundle with a
different dataset hash and disjoint sessions. A second explicit review is
required for `SHADOW_ELIGIBLE`. Metadata alone cannot prove genuine market
provenance or independence; human audit is still required. The bounded Rust
linear adapter and Python reference agree on the shared fixture score 2.5.
It checks version, feature schema, instrument, validity window, horizon and
finite values. Its raw score is **not** a calibrated probability. The test
artifact is `TRAINED_UNVALIDATED`; no adapter is connected to the paper policy.

Offline adaptive-learning rows keep state/action, unknown behavior propensity,
simulated fills/outcomes, risk and quality flags, censoring and next state.
Existing deterministic paper actions have no logged stochastic propensities or
sufficient counterfactual action coverage; off-policy evaluation is unsupported.

## Cockpit and current evidence

Run the local demo with `--research-manifest
.local/phase3b/dataset-NEW/manifest.json` to expose `GET /api/research`. The
gateway reads a precomputed manifest at startup; it does not train, scan QJ01
or run statistics in the capture process. Without a manifest it reports
`NOT_TRAINED` and zero available rows. The research workspace displays exact
sufficiency reasons; calibration, cumulative incidence, feature sensitivity,
net-return sensitivity and baseline metrics remain `N/A` until independently
validated artifacts provide them. A synthetic manifest is labelled **TEST
FIXTURE / UNVALIDATED**.

Current status: dataset and mathematical interfaces implemented; empirical
statistical validation **BLOCKED** by no verified real sessions, no confirmed
cost calibration, no independent holdout and no defensible live-feed rate.
Positive net alpha and real-money profitability are **UNKNOWN**.
