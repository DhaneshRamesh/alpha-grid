# Depth-ingestion capacity investigation

## What the Utho pilot actually measured

The 65.4 ms median was an **unauthenticated GET `https://api.dhan.co/v2/`** with 300 HTTP 200 responses. It was not a Dhan order, authenticated profile, market-data packet, or broker response-latency measurement. The old result says `https_connection_reused: true` because the code reused a Python `HTTPSConnection` object; it did **not** record socket reconnects, so physical connection reuse in the pilot is **UNVERIFIED**. The revised probe counts actual `connect()` calls and marks each request as reused or newly connected.

All elapsed times used `perf_counter_ns`, but the old phase boundaries differed: DNS timed `getaddrinfo`; TCP timed `create_connection` **including DNS**; TLS timed only `wrap_socket` after DNS/TCP; HTTPS timed request through full body read, including DNS/TCP/TLS if that connection had closed. New TCP probes resolve first, then time only `connect`. New results still need the per-request connection flag to separate warm HTTPS from reconnects. Do not add the phase medians or infer one-way network delay.

The storage probe wrote **2,000 raw 4,096-byte `J` buffers**, not journal frames or 20-level depth. It did one `os.write` and one `os.fsync` per buffer. Its 153.69 records/s took approximately 13.013 seconds: summed fsync time was 12.894 seconds, about **99.1%** of wall time, while summed write time was 0.065 seconds. The measured bottleneck was this deliberately per-record fsync pattern on that VM/filesystem. It does **not** establish production journal or full collector capacity. The existing collector defaults to `FsyncPolicy.ALWAYS` and synchronously journals each event before downstream output.

Pilot DNS/TCP/HTTPS each had 300 successful samples, TLS had 299/300; storage had 2,000. At 300, p99 represents roughly three tail observations and p99.9 is nearly the maximum, so neither establishes a stable tail. At 2,000, p99 has roughly 20 tail observations and p99.9 roughly two. Repeat across longer, market-hour sessions before using these tails for provider selection.

## Reproducible offline load test

`python -m infra.benchmark.ingestion_load` feeds immutable, normalized **20 bid and 20 ask level snapshots** for 20 instruments (configurable up to 50) into a bounded queue and the real `EventJournal`. `collector` mode also runs the real feed-health event observer and the collector's JSON serialization/flush path, with `/dev/null` as downstream sink. `journal_only` isolates journal cost. Dhan wire decoding, WebSocket buffering and downstream backpressure are outside this offline test. The deployed collector currently consumes synchronously; the benchmark queue explicitly measures overload rather than claiming to be its implementation. Synthetic sequence numbers rise per instrument; this assumes ordered snapshots and does not claim Dhan supplies a contiguous sequence or individual order events. Producer arrival uses monotonic scheduling. Each case has a distinct journal session. A queue-full or disk error is reported as `failed`, exits the CLI with status 2, and records accepted, durably committed, uncommitted and rejected counts separately. No overwrite path exists. Raw per-event write, fsync and event-to-durable latencies are retained in the fsynced `.json.trials.jsonl` file after every trial; the final JSON contains compact summaries.

```bash
sudo apt-get update && sudo apt-get install -y python3.12-venv
python3.12 -m venv /tmp/alpha-grid-uv-bootstrap
/tmp/alpha-grid-uv-bootstrap/bin/pip install uv==0.10.8
AG_UV=/tmp/alpha-grid-uv-bootstrap/bin/uv
"$AG_UV" sync --extra dev --locked --python python3.12
mkdir -p benchmark-data/ingestion
"$AG_UV" run --locked --extra dev python -m infra.benchmark.ingestion_load \
  --root benchmark-data/ingestion \
  --output benchmark-data/ingestion/results-$(date -u +%Y%m%dT%H%M%SZ).json \
  --rates 100 500 1000 5000 10000 --duration-seconds 60 \
  --repetitions 3 --queue-limit 2000 --instrument-count 20 \
  --sync-interval-ms 50 --group-bytes 262144 \
  --pipeline-mode collector \
  --provider local --region local --plan measured-host
```

Run a separate `--pipeline-mode journal_only` sweep with a new output name to isolate the journal. The CLI exits 2 if any case overloads, while preserving results and journals. On a real candidate, avoid interpreting benchmark output as live Dhan feed capacity until wire decoding, scheduling and observed market rates are measured.

Use the **same lockfile, filesystem, parameters, feed event shape and version** on a future authorized VM. Record CPU/VM plan and repeat randomized policy/rate trials; avoid other disk jobs. The 60-second, three-repetition schedule is for sustained validation, not a completed Utho result. The input target rate is distinct from observed accepted and durable rates; compare all three. The `peak_rss_bytes` metric is process high-water RSS and may include earlier cases in one process; Linux also reports current before/after RSS. Process CPU percent is relative to one core. Disk bytes per million are extrapolated raw journal bytes, excluding filesystem allocation and backups. Per-event observation arrays themselves consume memory and CPU; for high-rate confirmation, compare with a lean production-style run as well.

| Policy | Durable acknowledgement | Tradeoff / crash window |
| --- | --- | --- |
| `every_event` | Return from each `append` after its fsync | Lowest uncommitted window; one fsync per event. |
| `periodic` | Fsync when oldest pending event reaches the configured interval, plus final flush | Events accepted into memory or appended before sync remain **uncommitted**. Interval is a target, not a hard guarantee during scheduler/disk stalls. |
| `group_bytes` | Fsync at the configured accumulated byte threshold or interval, plus final flush | Same uncommitted risk; byte batching reduces fsync frequency at high arrival rates. |

Buffered modes use the journal's `ON_CLOSE` policy plus explicit group sync. It commits a dirty prior-date file before switching partitions and commits a dirty current file on safe shutdown. All policies fail closed on `sync` failure. A completed case reopens and scans the journal; durable count must equal recovered count. Existing and new tests cover abrupt process exit, incomplete final frame repair, non-tail corruption detection, cross-date sync, `EIO`, `ENOSPC`, slow fsync and a full queue. The crash test uses `os._exit` after two synced full records plus an incomplete tail, then proves recovery keeps both full records. A failed sync cannot acknowledge pending events even if the kernel later happens to persist them. Failures need operational alerting before a live feed is connected; the benchmark exposes status and exit code but is not the production collector.

## Local development-machine smoke run

One 2-second trial per policy/rate ran on a **Mac development machine**, not Utho. Both modes used version `ingestion-0.5.0` and the same parameters; raw outputs are `benchmark-data/ingestion-journal-v05.json.trials.jsonl` and `benchmark-data/ingestion-collector-v05.json.trials.jsonl`, with compact `.json` summaries (ignored by Git). Short durations, OS caching, unknown contention and low tail sample counts make these capacity smoke tests, not infrastructure evidence.

| Mode / policy | 5,000/s target | 10,000/s target | 10,000/s p99 event-to-durable |
| --- | --- | --- | ---: |
| Journal only / every event | 5,000 durable/s, queue max 8 | **FAILED**: queue full, one rejected | 199.5 ms before failure |
| Journal only / periodic | 4,999 durable/s, queue max 10 | 9,998 durable/s, queue max 22 | 50.0 ms |
| Journal only / group bytes | 5,000 durable/s, queue max 4 | 9,999 durable/s, queue max 151 | 23.7 ms |
| Collector path / every event | **FAILED**: queue full, one rejected | **FAILED**: queue full, one rejected | 449.4 ms before failure |
| Collector path / periodic | **FAILED**: queue full, one rejected | **FAILED**: queue full, one rejected | 458.4 ms before failure |
| Collector path / group bytes | **FAILED**: queue full, one rejected | **FAILED**: queue full, one rejected | 454.7 ms before failure |

At 100, 500 and 1,000/s, all policies and both modes completed with no rejection. The raw journal used approximately **1.18 GB per million complete 20-level events**. Journal-only group and periodic sync reached the 10,000/s input target locally, while every-event fsync filled the queue. The collector path filled its queue already at 5,000/s under all policies. This suggests work beyond journal append/fsync limits this local end-to-end path, but the test does not separate health processing from JSON/output cost. Local fsync p99 is sub-millisecond in most cases, compared with Utho's earlier 4 KiB fsync p99 of 14.35 ms. For the 100/s case (200 samples), p99.9 is not statistically meaningful. Longer independent runs are required for tails and steady-state headroom. No result here proves Utho's 20-level capacity.

## Readiness gates

- **Data collection:** measure a real entitled feed's busiest sustained and burst rates per instrument across market open, midday and close. Capacity must exceed those measured peaks with agreed headroom, zero unreported rejection, bounded queue and durable-latency tails, successful crash recovery, enough disk retention and alerting on any journal failure. Until live feed rates and a 20-level Utho run exist, readiness is **UNKNOWN**.
- **Live execution:** remains **UNKNOWN**. This benchmark does not measure authenticated Dhan API latency, market-feed completeness under load, shared CPU contention during NSE hours, stable outbound reserved IPv4, failover or order-path timing. No order calls are made.

The development extras now include pandas, websockets and DhanHQ; `uv.lock` pins the resolved dependency graph. `uv sync --extra dev --locked` installs the full test environment without ad hoc packages. Run `uv run --locked --extra dev pytest`, `uv run --locked --extra dev ruff check .`, and `uv run --locked --extra dev mypy src` on a clean host.
