# Phase 3A local offline replay artifacts

These are three sequential **local macOS arm64** release-binary runs on the
same synthetic 962-record QJ01 fixture. No live feed, Dhan account, broker
order, Utho VM or cloud resource was used. The exact CPU model was not
available. The app servers and build/test processes were stopped during the
runs. Rates vary with local filesystem and operating-system scheduling.

Each `runN/` contains `summary.json`, four raw per-record `.jsonl.gz` files
and four complete checksummed QP01 paper ledgers (`.qp.gz`). `demo.qj.gz` is
the identical source market journal for all runs. `SHA256SUMS` hashes the
compressed files and JSON summaries. The paths embedded in `summary.json`
are the original local paths; use this directory for archived artifacts.

Verify and inspect:

```sh
shasum -a 256 -c docs/benchmarks/phase3a-isolated-2026-10-15/SHA256SUMS
gzip -dc docs/benchmarks/phase3a-isolated-2026-10-15/run1/risk_filtered.jsonl.gz | head -n 1
```

To reproduce, run the release `paper_eval` command in
[`rust/docs/phase3a-paper.md`](../../../rust/docs/phase3a-paper.md) with a new
output directory for each run. `benchmark_version` is
`phase3a-paper-eval-v1`. Every JSONL row includes the source record index,
source and measurement timestamps, policy, event type and monotonic elapsed
nanoseconds for journal read, feature update, candidate, risk, OMS, each QP01
write, paper snapshot and telemetry publication. QJ01 read includes decoding;
it does **not** include live feed decode or QJ01 capture fsync. No browser
reception latency is measured. The summary reports nearest-rank p50, p95,
p99 and maximum, with p99.9 only at 1,000 or more samples. Per-transition
QP01 write samples number 240 for abstaining policies and 500 for the two
trading policies, so their p99.9 is unavailable.
