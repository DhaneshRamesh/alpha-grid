# Utho Bangalore DATA_ONLY trial, 2026-10-14

This trial used the existing temporary Utho VM `990003141` at repository commit
`6bbe532f751b7afd3e8b344a15bef4a96ff83fed`. It is a **Bangalore**
measurement; the earlier Mumbai pilot is separate. No Dhan credentials, live
market data, or order endpoint were used.

## Host and validation

Ubuntu 24.04.3, two virtual `Intel Core Processor (Broadwell, IBRS)` CPUs,
3.8 GiB usable RAM, and a 77 GiB ext4 root filesystem on an 80 GB virtual
disk. `lscpu` reported a Microsoft hypervisor. NTP was synchronized. Two
independent HTTPS reflectors observed outbound IPv4 `103.146.242.168`, equal
to the VM's inbound address; address persistence across restart, replacement,
or deletion was not tested. The VM used a standard public IP, without a
reserved IP or NAT gateway. The live Utho rate displayed ₹2.14/hour before
18% GST (₹2.5252/hour including GST); observed usage is recorded below.

The clean Ubuntu setup script installed the pinned Rust 1.99.0 toolchain and
Python dependencies from `uv.lock`. On this VM, `python -m pytest -q` passed
83 tests. Cargo passed 12 tests with one intentionally ignored fixture. Ruff
lint and format, mypy, rustfmt, Clippy with warnings denied, and the Rust
release build all exited zero. Logs and exact commands are in the raw archive.

## Unloaded network and storage baseline

The network target was `api.dhan.co:443`. HTTPS measured read-only public
`GET /v2/`, **not** an authenticated Dhan API call or order latency. It made
100 requests, of which 99 reused the initial connection; all returned HTTP
200. TCP timing excluded DNS; TLS timing started after TCP connection; HTTPS
covered request through body read and could include connection setup on a new
socket. All elapsed times used monotonic clocks.

| Probe (ms) | n | p50 | p95 | p99 | max | failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DNS | 100 | 1.859 | 2.721 | 28.118 | 33.842 | 0 |
| TCP connect | 100 | 21.998 | 28.395 | 34.223 | 35.387 | 0 |
| TLS handshake | 100 | 27.964 | 36.470 | 39.394 | 47.570 | 0 |
| Persistent HTTPS | 100 | 38.409 | 41.931 | 161.302 | 192.156 | 0 |
| 4 KiB append fsync | 2,000 | 1.100 | 1.790 | 2.733 | 9.338 | 0 |

The 4 KiB test did one fsync per record and reached 797 records/s. It is not
the full-depth journal throughput. Interrupted final-write recovery passed.
The baseline scheduler-jitter p99 was 0.297 ms (p99.9 1.259 ms, 2,264
samples); CPU steal fraction was 0.095%, with no observed cgroup throttling.
The 30-second single-thread SHA256 sample median was 65,024 hashes/s. One
hundred network observations are too few for a persuasive p99.9 claim.

## Matched ingestion workload

The existing Python `journal_only` runner and release-mode Rust `quant-bench`
ran sequentially. Each generated full 20-bid/20-ask snapshots for 20
instruments, with target rates 100, 500, 1,000, 5,000 and 10,000 events/s.
Each policy/rate ran for 60 seconds, three repetitions, with a 2,000-event
queue, 50 ms periodic sync target, 262,144-byte size trigger, and seed
`20261011`. Runs used `every_event`, `periodic`, and `group_bytes` durability
policies. Trials were shuffled separately by each language's RNG. Offered,
accepted, kernel-written, fsync-confirmed durable and recovered counts must
be read separately. A queue-full result is a **failed trial**, even if its
accepted prefix was recovered intact.

Python completed all three repetitions at 100 and 500 events/s under every
policy. At 1,000 events/s, both buffered policies completed all three, while
per-event fsync failed all three with a full queue. At 5,000 and 10,000
events/s, every Python policy reported queue rejection. All 45 Python cases
had recovered counts equal to durable counts; the CLI exited 2 to report the
overload cases. Median successful-trial event-to-durable p99 at 1,000/s was
52.01 ms for both buffered policies. The median 1,000/s group-commit journal
size was 1.182 GB per million events.

Rust completed all three repetitions at 100, 500 and 1,000 events/s under
every policy. Periodic and size-based group commit also completed all three
repetitions at 5,000 and 10,000 events/s; per-event fsync failed all three at
both high rates with explicit queue rejection. Every completed Rust case
matched accepted, kernel-written, durable and recovered counts. Failed cases
recovered exactly their durable prefix. The CLI exited 1 to report overload.

| Implementation and policy | Highest target with 3/3 complete | Next tested target | Median p99 event-to-durable at highest passing rate | Median p99 fsync at highest passing rate |
| --- | ---: | --- | ---: | ---: |
| Python every event | 500/s | 1,000/s: 3/3 failed | 5.40 ms | 2.28 ms |
| Python periodic | 1,000/s | 5,000/s: 3/3 failed | 52.01 ms | 6.63 ms |
| Python group bytes | 1,000/s | 5,000/s: 3/3 failed | 52.01 ms | 6.00 ms |
| Rust every event | 1,000/s | 5,000/s: 3/3 failed | 17.36 ms | 1.35 ms |
| Rust periodic | 10,000/s | No higher rate tested | 54.19 ms | 7.44 ms |
| Rust group bytes | 10,000/s | No higher rate tested | 52.85 ms | 8.09 ms |

Each latency is the median of three **per-trial percentile estimates**, not
a pooled percentile. At 10,000/s with Rust group commit, the median per-trial
p50/p95/p99/p99.9/max event-to-durable latencies were 28.04/50.37/52.85/
57.29/68.15 ms. The corresponding median fsync p99/p99.9 were 8.09/10.30
ms. Peak queue occupancy was at most 203 of 2,000 at that rate; zero events
were rejected. Median process CPU time was 18.2% of one core and peak RSS
was about 90 MB, though RSS is the high-water mark for the full multi-case
process. Its median journal size was 518 MB per million events.
The matched Python 1,000/s group-commit case used 1,182 MB per million
events, versus Rust's 516 MB. The smaller Postcard frame, buffered write
path, different timestamp precision and other serialization differences
prevent a language-only performance claim.

The 50 ms commit interval is a scheduling target, **not** a guaranteed
durability bound. Accepted buffered events can be lost on a crash before
fsync. Tests cover abrupt exit, truncated tail, checksum corruption, write
failure and queue saturation, and the trial's complete cases replayed their
durable counts; this does not prove power-loss behavior for every filesystem
or the durability of a newly created directory entry.

## Reproduction and scope

The complete commands, start/end UTC times, exit codes, raw per-trial samples,
checks, host details and journals are in the preserved archive. The workload
was invoked with these identical parameters after the fresh setup and release
build:

```sh
.venv/bin/python -m infra.benchmark.ingestion_load \
  --root benchmark-data/utho-bangalore-20261014/ingestion/python-journals \
  --output benchmark-data/utho-bangalore-20261014/ingestion/python-ingestion.json \
  --rates 100 500 1000 5000 10000 --duration-seconds 60 \
  --repetitions 3 --queue-limit 2000 --instrument-count 20 \
  --sync-interval-ms 50 --group-bytes 262144 \
  --pipeline-mode journal_only --seed 20261011 \
  --provider Utho --region Bangalore --plan Basic-1
rust/target/release/quant-bench --all --duration 60 --repetitions 3 \
  --seed 20261011 --queue-limit 2000 --instrument-count 20 \
  --sync-interval-ms 50 --group-bytes 262144 \
  --provider Utho --region Bangalore --plan Basic-1 \
  --journal-dir benchmark-data/utho-bangalore-20261014/ingestion/rust-journals \
  --output benchmark-data/utho-bangalore-20261014/ingestion/rust-ingestion.json
```

This proves that the Rust **implementation** handled this synthetic,
full-depth workload on this one VM. It does not establish sufficient live
feed headroom: Dhan's actual peak subscribed-event rate and decode workload
were not measured. Data-journal correctness and crash recovery passed the
implemented tests, but live DATA_ONLY feed capture readiness remains
**UNKNOWN**. Live execution readiness is also **UNKNOWN**: there was no
authenticated Dhan API, market-hours feed, fixed-IP lifecycle test, or order
path measurement. No strategy or broker order was run.

## Artifact and resource status

The VM-side archive `/root/utho-bangalore-20261014.tar.gz` and the Mac-side
copy `work/utho-bangalore-20261014/utho-bangalore-20261014.tar.gz` have the
same SHA-256:

```text
b1cb71ff61bcc349a1bd3ebcd5824b6e9769548aec4e91634c2079043b97c5bd
```

The Mac extraction verified all 166 files against `MANIFEST.sha256`, with
zero mismatches. The archive contains both raw trial JSONL files, summary
JSON, the full journal trees, network baseline, host information, check logs,
exit codes, run scripts, and UTC timestamps. Large generated data is ignored
by Git; only this report and the README link are committed.

Utho's Current Usage page showed this VM at **₹4.46 excluding GST** after
2 h 5 min of operation, at approximately 12:39 IST on 14 October. Including
18% GST, that observation is approximately **₹5.26**. The account-wide
₹20.20 usage displayed at the same time includes **₹15.74 from earlier
servers** and must not be charged to this Bangalore trial. Usage may settle
later. At the displayed hourly rate, 72 hours would cost about ₹181.81
including GST if no extra charges arise; this is a projection, not a bill.

**Resource status:** the user instructed us to retain VM `990003141`, so it
remains active and billable. No extra block volume, elastic IP or snapshot was
created for this trial. The previous 72-hour trial limit runs from its
creation at 10:34:01 IST on 14 October to 10:34:01 IST on 17 October 2026.
Do not delete this VM without a new explicit user instruction. Confirm a
revised duration and spending limit before continuing beyond the trial limit.
