# Utho Mumbai pilot, 2026-10-11

Single temporary Ubuntu 24.04 VM: 2 vCPU, 4 GB RAM, 80 GB EBS, AMD EPYC-Milan virtual CPU. Standard public IPv4 `95.134.162.214`; no reserved IP or NAT gateway. Benchmark runner 0.2.0 ran outside NSE trading hours. Raw JSON is retained locally at `benchmark-data/utho-pilot-20261011/benchmark-data/results/utho-baseline-20261011T171752Z.json` (ignored by Git).

| Measurement | p50 | p95 | p99 | Maximum | Failures |
| --- | ---: | ---: | ---: | ---: | ---: |
| DNS (ms) | 1.414 | 2.146 | 56.001 | 62.059 | 0/300 |
| TCP connect (ms) | 79.006 | 86.643 | 104.161 | 1436.792 | 0/300 |
| TLS handshake (ms) | 81.422 | 89.614 | 91.826 | 536.490 | 1/300 |
| Persistent HTTPS (ms) | 65.400 | 67.873 | 70.369 | 334.856 | 0/300 |
| Journal fsync (ms) | 6.005 | 9.680 | 14.354 | 214.171 | 0/2000 |

Scheduler jitter p99 was 0.205 ms and p99.9 was 0.600 ms across 7,972 samples. CPU steal fraction was approximately 0.094%; no cgroup throttling events occurred. Sustained 30-second single-thread SHA256 throughput was approximately 310,804 hashes/s. Journal append throughput was 153.69 records/s; interrupted-write recovery passed. Two independent public IP reflectors agreed on the outbound IPv4.

Authenticated Dhan API latency, live depth continuity, instrument coverage, feed timestamp semantics, and static IP persistence remain **UNKNOWN**. No account credentials or live feed entitlement were available; NSE was closed. This single pilot cannot establish a provider winner or live-execution suitability.

Fresh-VM verification: 72 pytest tests passed after installing `websockets` and `pandas` in addition to `.[dev,dhan]`; Ruff and mypy passed. The current `dev` extra alone omits these dependencies for the full test suite.

The VM and attached EBS were deleted after the run; both inventories were empty. Utho showed ₹1.05 usage and a wallet decrease from ₹500 to ₹498.76 (₹1.24 including GST) at teardown. Final billing settlement may update this amount.
