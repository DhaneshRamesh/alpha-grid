# Utho Mumbai Basic 1: first temporary benchmark

**State:** DATA_ONLY. No Utho VM has been created or measured. Do not deploy until an itemized checkout quote establishes the cap below and the owner explicitly approves that exact cap. This pilot uses one ordinary public IPv4, no reserved IP, no NAT Gateway, no backup, no extra volume, and no trading service.

## One manual action before a spending request

Sign in or register at the [Utho console](https://console.utho.com/login.php). Open the cloud-server deployment form, choose **Mumbai**, **Basic 1 shared CPU** (2 vCPU, 4 GB RAM, 80 GB NVMe), **Ubuntu 24.04 LTS**, and one standard public IPv4 if offered. Stop at the final price review; **do not click Deploy**. If the plan is absent in Mumbai, report that and stop.

Capture only these non-sensitive facts from the checkout, billing page, or Utho support response:

1. Hourly VM price and currency, or the actual monthly price; whether an hourly option applies to this plan in Mumbai; rounding and any minimum charge or commitment.
2. Whether the 80 GB boot disk and ordinary public IPv4 are included; any mandatory IPv4, disk, network, account, or setup charge; whether backup and NAT can remain off.
3. GST rate and whether each quoted line is before or after tax.
4. What remains billable after **Delete** (including public IPv4, disk, backup, snapshot), and whether deletion stops compute billing immediately.
5. Account can actually deploy this shape in Mumbai, but **do not** provide login, payment details, API keys, Dhan credentials, or an unredacted billing screenshot.

The [public price page](https://utho.com/pricing) lists ₹1,794/month and says hourly billing is available. That is **not** an account-specific hourly checkout quote. Actual short-run cost is **UNKNOWN** until the above facts are supplied.

### Spending cap to approve before deployment

Budget **at most three billable VM hours** for the first baseline run, including setup and artifact transfer. The exact approval request must state the quoted VM price, mandatory additions, GST, and the total cap in INR. Use the provider's billing unit and round *up*. If hourly billing and no minimum are confirmed:

`cap = 3 × (hourly VM + hourly mandatory IPv4/disk/network) + fixed mandatory fees + GST on taxable lines`

If the plan has a monthly minimum, the cap includes the **full monthly minimum**, even if the VM is deleted after one hour. If any mandatory charge, deletion rule, or tax treatment is unknown, keep the cap **UNKNOWN** and do not deploy. The one-session market-open/midday/close or three-session follow-up needs its **own** duration and approved cap; this first cap authorizes only the short baseline pilot. Never infer prorating from the public monthly price.

## Ubuntu 24.04 commands, after approval and VM creation

SSH using the username, key and public IPv4 shown in the Utho console. Run this on the VM. Keep the SSH session open or run inside `tmux`; the result path must be new for each run. These commands require no Dhan account and invoke no order endpoint.

```bash
sudo apt-get update
sudo apt-get install -y git python3 python3-venv chrony tmux
git clone https://github.com/DhaneshRamesh/alpha-grid.git
cd alpha-grid
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
umask 077
mkdir -p benchmark-data/results
git rev-parse HEAD > benchmark-data/benchmark-commit.txt
date -u --iso-8601=seconds > benchmark-data/host-started-utc.txt
chronyc tracking > benchmark-data/clock-status.txt
.venv/bin/python -m infra.benchmark.run_benchmark run \
  --provider Utho --region Mumbai --plan Basic-1 \
  --journal-dir benchmark-data --network-samples 300 --interval 1 \
  --cpu-seconds 120 --storage-samples 2000 \
  --output "benchmark-data/results/utho-baseline-$(date -u +%Y%m%dT%H%M%SZ).json"
```

The baseline records DNS, TCP, TLS, persistent unauthenticated HTTPS, actual CPU model, sustained compute, CPU steal/throttle, scheduling jitter, journal append/fsync and interrupted-write recovery, and outbound IPv4 from two independent HTTPS reflectors. Its `/v2/` HTTPS status may be non-200 because this is an unauthenticated timing probe. The ordinary public IPv4 is **observed egress**, not evidence of a persistent/static outbound address. Do not pass `--static-ip-supported` or `--expected-egress-ip` for this pilot. Do not pass an unverified `--monthly-cost`.

For an **optional, separate** market-hours run, install the feed extra and configure Dhan credentials on the VM yourself. The token is read without shell echo or command-line arguments. The 20 sample instruments come from the repo's `.env.example`; use only instruments covered by the account's depth entitlement. The authenticated probe performs read-only `GET /v2/profile` at one-second spacing, and the WebSocket feed only subscribes to depth. If no entitlement or no messages, the result is a partial JSON and the command exits nonzero; preserve that file.

```bash
.venv/bin/python -m pip install -e '.[dev,dhan]'
read -r -p 'Dhan client ID: ' DHAN_CLIENT_ID
read -r -s -p 'Dhan access token: ' DHAN_ACCESS_TOKEN; echo
export DHAN_CLIENT_ID DHAN_ACCESS_TOKEN
export QUANT_DEPTH_INSTRUMENTS="$(sed -n 's/^QUANT_DEPTH_INSTRUMENTS=//p' .env.example)"
.venv/bin/python -m infra.benchmark.run_benchmark run \
  --provider Utho --region Mumbai --plan Basic-1 \
  --journal-dir benchmark-data --network-samples 180 --interval 1 \
  --api-samples 180 --api-interval 1 --cpu-seconds 120 \
  --storage-samples 2000 --feed-seconds 180 --stall-seconds 5 \
  --output "benchmark-data/results/utho-feed-$(date -u +%Y%m%dT%H%M%SZ).json"
unset DHAN_CLIENT_ID DHAN_ACCESS_TOKEN
```

Schedule authenticated runs in verified NSE trading sessions. Market-open, midday and close on at least three sessions are needed for a later provider comparison, with a separately approved spend. Record the same instrument set, commit and probe parameters in each window. The benchmark stores UTC sample times and monotonic elapsed durations; exchange timestamps are not one-way network latency. A 300-sample baseline gives an exploratory p99; its p99.9 is too sparse for an execution decision.

## Preserve, assess, and delete

From the local machine, replace `VM_USER`, `VM_IPV4`, and `SSH_KEY` with the console's values. Save raw JSON and the commit/clock metadata **before** deleting the VM:

```bash
mkdir -p benchmark-data/utho-pilot
scp -i SSH_KEY 'VM_USER@VM_IPV4:~/alpha-grid/benchmark-data/results/*.json' benchmark-data/utho-pilot/
scp -i SSH_KEY 'VM_USER@VM_IPV4:~/alpha-grid/benchmark-data/*txt' benchmark-data/utho-pilot/
```

If the console supplies a non-home working directory or root login, adjust the remote path. Check that copied files are nonempty and valid JSON. Raw results are local, ignored by Git, and should be kept private. **Delete** the temporary VM in the Utho console, then inspect the resource/billing inventory for remaining public IPs, volumes, backups, snapshots, or NAT gateways. Remove any pilot leftovers and record the final billed amount. Stopping a VM is not a substitute for deletion unless its billing behavior is confirmed.

| Gate | First-pilot decision rule |
| --- | --- |
| Durability | Interrupted-write recovery must pass; fsync failure or missing raw JSON is **FAIL**. Record fsync p50/p95/p99 and maximum. |
| Network | Record p50/p95/p99, maximum and failures for each probe. Repeated connection failures are **FAIL**; latency suitability remains **UNKNOWN** until later comparison and market-hour data. |
| Compute | Record CPU model, sustained throughput, steal/throttle and scheduler jitter p99. Sustained throttling or large stalls require investigation; no fixed execution threshold is inferred from one VM. |
| Feed | With entitlement: zero events or a missing subscribed instrument is **FAIL** for that run; record stalls, reconnects and interarrival. Without entitlement: **UNKNOWN**. An inactive instrument's quiet period alone does not prove packet loss. |
| Cost | **PASS** only if actual bill is within the approved cap and deletion ends recurring pilot charges; otherwise **FAIL/UNKNOWN** as evidence allows. |
| Static outbound IPv4 | **UNKNOWN** in this ordinary-IP pilot. Do not qualify Utho for live execution from this result; reserved-IP persistence and outbound behavior need a later authorized test. |

This pilot cannot identify a provider winner or authorize live trading. Once run, it provides a real Utho baseline; every unavailable market-data metric remains **UNKNOWN**.
