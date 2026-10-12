# Rust DATA_ONLY foundation

The Python collector remains the reference implementation. This workspace has no
Dhan decoder, credentials, order endpoints, or trading path. `quant-core` defines
fixed-point market events, `quant-market-data` validates synthetic 20-level
packets, `quant-orderbook` holds full snapshots, `quant-journal` writes framed
checksummed records, `quant-replay` preserves journal order and receive-time
intervals, and `quant-bench` drives a bounded Tokio channel into the journal.

Prices are INR × 10^9 integers; timestamps are Unix nanoseconds. Exchange
timestamps remain absent in synthetic events. This avoids treating source clocks
as measured network latency. Full snapshots never imply individual order events.

## Reproducible build and tests

Install the stable Rust toolchain on Ubuntu 24.04, then from the repository root:

```sh
cargo test --locked --workspace --manifest-path rust/Cargo.toml
cargo fmt --all --manifest-path rust/Cargo.toml --check
cargo clippy --locked --workspace --all-targets --manifest-path rust/Cargo.toml -- -D warnings
cargo build --locked --release --manifest-path rust/Cargo.toml -p quant-bench
```

Run all target rates under three durability policies. Keep journals and raw
measurements in a scratch directory with enough free disk space:

```sh
rust/target/release/quant-bench --all --duration 10 --queue-limit 2000 \
  --provider utho --plan basic-1 --region mumbai \
  --journal-dir benchmark-data/rust-journals \
  --output benchmark-data/rust-ingestion.json
```

The output records every append, durable-write, and fsync latency in nanoseconds,
along with offered, accepted, rejected, durable and recovered counts, CPU time,
peak RSS, queue depth, file bytes and summary percentiles. A case fails if
accepted, durable and recovered counts disagree. The channel never overwrites
events: a full queue rejects the next event, stops input, and reports failure,
matching the Python benchmark's overload behavior. Compare the **actual source
rate**, queue occupancy and latency tails.

Run the Python reference with `--pipeline-mode journal_only` and the same rates,
duration, queue limit, 20 instruments, 50 ms periodic commit, and 262144-byte
size trigger; see `infra/benchmark/ingestion.md`. Rust `EveryEvent` matches
Python `every_event`, `Periodic` matches `periodic`, and `GroupBytes` matches
`group_bytes`. Both acknowledge durable events only after successful fsync.
The journal formats differ, so report serialized bytes separately. Build time
is excluded from benchmark timing.

For a deterministic overload check, add `--queue-limit 1
--simulate-slow-sync-ms 100`. The injected delay is reported separately and
is **not** included in measured fsync time. The runner must reject a full
queue visibly; it may persist all events accepted before the failure.

The Rust journal recovers an incomplete or corrupt final frame by truncating
only that frame. It rejects detected interior corruption. Each append is held
in memory until its group commit; process death can lose that explicit
uncommitted window (target 50 ms, subject to scheduling and storage stalls).
`EveryEvent` has one fsync per event. The benchmark records that exposure using
`max_uncommitted_events`, and stores complete 20-level events. It is a synthetic
capacity test, not evidence of real Dhan feed throughput or live execution
readiness.
