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

The repository pins Rust 1.99.0 and its Clippy/rustfmt components in
`rust-toolchain.toml`; `rust/Cargo.lock` pins crates. On a fresh Ubuntu 24.04
host, run `bash infra/benchmark/setup_ubuntu.sh`. From the repository root:

```sh
cargo test --locked --workspace --manifest-path rust/Cargo.toml
cargo fmt --all --manifest-path rust/Cargo.toml -- --check
cargo clippy --locked --workspace --all-targets --manifest-path rust/Cargo.toml -- -D warnings
cargo build --locked --workspace --release --manifest-path rust/Cargo.toml
```

Run all target rates under three durability policies. Keep journals and raw
measurements in a scratch directory with enough free disk space:

```sh
rust/target/release/quant-bench --all --duration 60 --repetitions 3 \
  --seed 20261011 --queue-limit 2000 --instrument-count 20 \
  --sync-interval-ms 50 --group-bytes 262144 \
  --provider local --plan measured-host --region local \
  --journal-dir benchmark-data/rust-journals \
  --output benchmark-data/rust-ingestion.json
```

Each trial is written and fsynced to `rust-ingestion.json.trials.jsonl` before
the next trial; the final JSON is a convenient complete index. Existing output
files are never overwritten. Raw samples include append, event-to-durable and
fsync latency in nanoseconds. Counts distinguish offered, queue accepted,
journal buffered, completely written to the kernel, fsync acknowledged and
recovered events. A case fails if accepted, written, durable and recovered
counts disagree. The channel never overwrites
events: a full queue rejects the next event, stops input, and reports failure,
matching the Python benchmark's overload behavior. Compare the **actual source
rate**, queue occupancy and latency tails.

Run the Python reference with `--pipeline-mode journal_only` and the same rates,
duration, queue limit, 20 instruments, 50 ms periodic commit, and 262144-byte
size trigger; see `infra/benchmark/ingestion.md`. Rust `EveryEvent` matches
Python `every_event`, `Periodic` matches `periodic`, and `GroupBytes` matches
`group_bytes`. Both acknowledge durable events only after successful fsync.
The journal formats and write batching differ, so report serialized bytes and
CPU separately. A direct language speed claim remains unsupported. See
`infra/benchmark/rust-python-parity.md` for the exact comparison contract and
failure evidence. Build time is excluded from benchmark timing.

For a deterministic overload check, add `--queue-limit 1
--simulate-slow-sync-ms 100`. The injected delay is reported separately and
is **not** included in measured fsync time. The runner must reject a full
queue visibly; it may persist all events accepted before the failure.

The Rust journal recovers an incomplete or corrupt final frame by truncating
only that frame, reports truncated bytes, and emits a warning. The benchmark
fails on unexpected truncation. It rejects detected interior corruption. Each append is held
in memory until its group commit; process death can lose that explicit
uncommitted window (target 50 ms, subject to scheduling and storage stalls).
`EveryEvent` has one fsync per event. The benchmark records that exposure using
`max_uncommitted_events`, and stores complete 20-level events. It is a synthetic
capacity test, not evidence of real Dhan feed throughput or live execution
readiness.
