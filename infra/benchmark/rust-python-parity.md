# Rust/Python DATA_ONLY ingestion comparison contract

No new Utho VM was created for this validation. The local Mac runs verify
functionality, not Utho capacity. Use the pinned Rust toolchain, `rust/Cargo.lock`,
`uv.lock`, Ubuntu setup script, and CI workflow for repeatable builds.

## Matched workload

Both sources emit round-robin full 20-bid/20-ask snapshots for 20 instruments
(configurable to 50), with the same prices, quantities, order counts, security
IDs, and per-instrument sequence progression. Exchange timestamps are absent.
Rust stores prices as INR × 10⁹ integers and nanosecond timestamps; Python uses
`Decimal` prices and `datetime` receive timestamps with microsecond precision.
Both use monotonic producer scheduling at 100, 500, 1,000, 5,000 and 10,000
events/s, a bounded 2,000-event handoff, and an event-to-durable clock beginning
at queue handoff and ending after successful fsync. Run the same 60-second
duration, three repetitions, 50 ms sync target and 262,144-byte group trigger.
The CLIs shuffle trial order independently with the same seed but different RNG
implementations; compare matched policy/rate trials, not their execution order.

| Property | Python `journal_only` | Rust `quant-bench` |
| --- | --- | --- |
| Frame | Sorted compact JSON, big-endian length/CRC32, session/date partitions | Postcard, little-endian length/CRC32, one file per case |
| Write before fsync | One `os.write` loop per event | Buffered in memory, one `write_all` per group |
| `every_event` | Complete write plus fsync per event | Complete write plus `sync_data` per event |
| `periodic` | Oldest pending event reaches 50 ms | Oldest pending queue event reaches 50 ms |
| `group_bytes` | Written bytes reach 262,144 or age reaches 50 ms | Buffered bytes reach 262,144 or age reaches 50 ms |
| Consumer | Dedicated Python thread | Tokio task, blocking journal calls |
| Raw output | Per-trial fsynced JSONL plus summary | Per-trial fsynced JSONL plus summary |

The Rust and Python journals have different byte volume, syscall counts,
timestamp precision, memory exposure, and serialization CPU cost. Their
throughput compares these **implementations**, not Rust versus Python in
isolation. The Python collector mode additionally runs health observation and
JSON downstream output; compare Rust only to Python `journal_only` unless the
same downstream work is implemented in Rust. Neither includes Dhan wire decode
or live WebSocket ingress.

`offered`/`attempted` means the producer reached an event; `accepted` means a
bounded queue accepted it; `journal_buffered` (Rust) means append succeeded;
`persisted_to_kernel` means a complete frame was written to the kernel's file
cache; `durable` means fsync succeeded; `recovered` means a scan after closure
found it. Kernel-cache writes are **not** durable acknowledgements. The Rust
buffered journal can lose accepted events on process death before group commit;
Python has already written those frames, but without fsync they still lack a
power-loss guarantee. The 50 ms policy is a target, not a hard upper bound
during stalls. Alert on rejected events, write/sync errors, truncated recovery,
or any accepted/durable/recovered mismatch.

## Failure evidence and remaining limits

| Failure | Current test/evidence |
| --- | --- |
| Full queue and slow writer | Rust `slow_sync_exposes_bounded_queue_failure`; Python `test_slow_fsync_and_bounded_queue_fail_loudly`. Both exit with a visible failure. |
| Write and fsync failure | Rust fault-injection tests poison the writer and never acknowledge a failed commit; Linux CI also writes to `/dev/full`. Python tests inject EIO/ENOSPC. |
| Abrupt exit and incomplete tail | Rust child exits without destructors and recovery retains only synced prefix; Python child exits via `os._exit` with incomplete tail. |
| Checksum corruption | Rust final-frame repair and interior rejection tests; Python journal tests cover tail and interior damage. Rust reports repaired tail bytes. |
| Ordering and shutdown | Rust replay and `close()` tests; Python journal/replay tests. |

The tests prove controlled failure paths, not all filesystem or power-failure
behaviors. A crash immediately after `fsync` of a newly created journal file
may still need directory-entry durability verification on the target filesystem.
No live feed rate, Dhan payload completeness, Utho scheduler jitter, or
provider storage tail is measured here. Keep live-execution readiness UNKNOWN.

## Clean Ubuntu 24.04 run

```sh
bash infra/benchmark/setup_ubuntu.sh
source "$HOME/.cargo/env"
.venv/bin/python -m pytest -q
cargo test --locked --workspace --manifest-path rust/Cargo.toml
cargo build --locked --workspace --release --manifest-path rust/Cargo.toml
rust/target/release/quant-bench --all --duration 60 --repetitions 3 \
  --queue-limit 2000 --instrument-count 20 --sync-interval-ms 50 \
  --group-bytes 262144 --provider local --plan measured-host --region local \
  --journal-dir benchmark-data/rust-journals \
  --output benchmark-data/rust-ingestion.json
.venv/bin/python -m infra.benchmark.ingestion_load \
  --root benchmark-data/python-journals \
  --output benchmark-data/python-ingestion.json \
  --rates 100 500 1000 5000 10000 --duration-seconds 60 \
  --repetitions 3 --queue-limit 2000 --instrument-count 20 \
  --sync-interval-ms 50 --group-bytes 262144 \
  --pipeline-mode journal_only \
  --provider local --plan measured-host --region local
```

Use unique output paths on every host/run. Preserve both `.trials.jsonl` files,
their journals, and SHA-256 checksums before teardown. A nonzero benchmark exit
is a finding; inspect the already-fsynced raw trials. No order API is involved.
