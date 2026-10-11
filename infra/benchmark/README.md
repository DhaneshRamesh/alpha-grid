# Mumbai infrastructure verification and benchmark (11 October 2026)

**Next operation:** [one-VM Utho Mumbai pilot](utho_pilot.md). It starts with an account-specific checkout quote and a short baseline run; it does not require other-provider quotes.

**Status: no provider VM has been provisioned, billed or measured. No production deployment or order API is involved.** Published prices are inputs to an experiment, not a winner. `UNVERIFIED` means no current official source or live account evidence establishes the claim. The same Git commit and command parameters must run on every VM.

## Utho Basic 1: eight decision questions

| Question | Evidence and current answer |
| --- | --- |
| ₹1,794 plan in Mumbai today? | [Pricing](https://utho.com/pricing) lists Basic 1, 2 shared vCPU, 4 GB RAM, 80 GB NVMe, 1 TB transfer at ₹1,794/month. [Reserved IP page](https://utho.com/reserved-ip) illustrates Mumbai zone 2, but neither proves **Basic 1 capacity in Mumbai now: UNVERIFIED**. Confirm in authenticated console before renting. |
| Complete monthly bill? | Included 80 GB disk and 1 TB transfer are documented. [Terms §16.13](https://utho.com/term-condition) say fees exclude GST. Reserved IP fee, backup/snapshot billing basis, overage price, extra disk, exact GST on invoice and hourly/minimum charges are **UNVERIFIED**. Formula: `(₹1,794 + reserved IP + backup + extra disk + overage + optional NAT) × (1 + applicable GST rate)`. [NAT Gateway](https://utho.com/nat-gateway) advertises ₹3.74/hour plus transfer, or ₹2,730.20/730 hours before transfer and tax; Basic 1 plus NAT is already ₹4,524.20 before IP, backup, transfer and tax. [Simple pricing](https://utho.com/simple-pricing) says no egress overage/minimum spend, which conflicts with the 1 TB plan allowance and NAT transfer charge: obtain an itemized quote. |
| Direct reserved IP also outbound source? | [Reserved IP docs](https://utho.com/reserved-ip) describe assignment and stable connectivity, not outbound SNAT. **UNVERIFIED.** Two HTTPS reflectors must agree with the reserved address; verify again against the Dhan destination or with Utho support. |
| Stable egress without NAT Gateway? | **UNVERIFIED.** A dedicated NAT product is documented but does not prove it is required for a public VM. Do not include the NAT charge or omit it from a final quote until the VM path is tested. |
| IP across restart, stop/start, replacement? | [Reserved IP docs](https://utho.com/reserved-ip) explicitly describe unassign/reassign and retention across deletion/recreation. Restart and stop/start behavior are **UNVERIFIED**; test all three lifecycle events and ensure the *outbound* address stays the same. |
| CPU model and allocation? | Basic 1 is **shared CPU** per [pricing](https://utho.com/pricing). Exact host model and clock are **UNVERIFIED**. The [dedicated tier](https://utho.com/dedicated-cpu) markets AMD EPYC/Intel Xeon; it does not establish Basic 1 hardware. Collect `/proc/cpuinfo` on every host. |
| Throttling/fair-use/sustained load? | [Shared CPU page](https://utho.com/shared-cpu) calls cores burstable and the host multi-tenant; numeric sustained entitlement, throttling and fair-use limits are **UNVERIFIED**. Measure cgroup throttling, CPU steal, continuous single-thread throughput and scheduler tails; request written policy if shortlisted. |
| Single-instance SLA and exclusions? | [June 2026 SLA](https://utho.com/sla): **99.5% monthly** for one compute instance, 99.99% only for multi-AZ regional deployments. Credits require incident email within 24 hours of discovery and rebate request within two days after month-end; downtime starts at the email timestamp. Excludes customer software/configuration, third-party/internet/DNS failures outside Utho, scheduled/customer-requested offline backups, misuse, force majeure and other listed causes. Remedy is future invoice credit, not cash. This does not promise feed completeness or order execution. |

A 99.5% month permits roughly 3.65 hours of unavailability in a 730-hour month. For a collector, durable journal and gap alerts reduce data-loss risk but cannot reconstruct broker packets absent during outage. For an eventual sole executor, the weaker SLA and shared CPU make Utho unsuitable **until** measured contention, fallback and failover design are reviewed.

## Final shortlist and cost evidence

All figures are public list floors, before tax; they are **not** full monthly operating costs. Cost requires VM + persistent IP + disk + backups + expected egress/overage + tax, in a common currency on the same quote date. Include any Dhan data subscription separately if applicable.

| Candidate | Documented base | Required missing bill items | Current standing |
| --- | --- | --- | --- |
| [Utho Mumbai Basic 1](https://utho.com/pricing) | ₹1,794/month including 80 GB/1 TB; shared CPU | Mumbai stock, reserved IPv4, backups, extra network, GST, possible NAT | Budget candidate; **all-in UNVERIFIED**. |
| [OCI Mumbai E4 Flex 1 OCPU/4 GB](https://www.oracle.com/in/cloud/price-list/) | Like-for-like 2-vCPU/4-GB target: ₹2.388625/OCPU-hour + 4 × ₹0.1433175/GB-hour = ₹2.961895/hour compute; ×730 = ₹2,162.18. 50 GB balanced boot volume ≈₹203.03/month; subtotal ≈₹2,365.22 | Reserved IP SKU/charge, backup, egress beyond allowance, GST, current shape capacity | Value alternative; recalculate from current quote before scoring. [OCI defines 1 x86 OCPU as two vCPUs](https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm). [Public IP docs](https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/managingpublicIPs.htm) allow direct instance internet access with reserved IPv4, public subnet and internet gateway. |
| [Akamai/Linode Mumbai G6 Dedicated 4 GB](https://www.akamai.com/cloud/pricing/cpu) | [Public plan API](https://api.linode.com/v4/linode/types/g6-dedicated-2): $0.054/hour, $36/month; 2 dedicated vCPU, 80 GB disk, 4 TB transfer. [Mumbai availability API](https://api.linode.com/v4/regions/in-bom-2/availability) reported this type available on 11 October 2026. | Account-specific provisioning rights and live stock, [reserved IP](https://techdocs.akamai.com/cloud-computing/docs/reserved-ips) fee, backup, tax/FX/overage. [Akamai lists India GST at 18%](https://techdocs.akamai.com/cloud-computing/docs/tax-information) for taxable invoices. | Independent dedicated CPU comparison; account quote pending. |
| [Dhan Cloud](https://dhan.co/support/platforms/dhanhq-api/what-are-the-charges-for-deploying-my-algo-on-dhan-cloud/) | Pay-per-use; estimator requires developer-portal login | Exact VM/region, deployment of this Python collector, persistent journal/export, egress IPv4, price | **Unranked** until deployment capability and a bill quote are established. |
| [AWS Mumbai](https://aws.amazon.com/vpc/pricing/) | Optional reference; public IPv4 $0.005/hour before VM/disk/network/tax | Current Mumbai instance, EBS, backup and transfer quote | Benchmark only if budget permits. |

Vultr Mumbai can replace Akamai if G6 is unavailable. Its [reserved IP](https://docs.vultr.com/support/products/network/are-reserved-ips-free) is $0.004/hour; exact Mumbai VM price/capacity still needs a quote. No further provider discovery is needed.

### Outbound/static IPv4 compatibility

| Provider | Persistent reservation | Direct VM outbound source | Survives replacement | NAT requirement |
| --- | --- | --- | --- | --- |
| Utho | [Reserved/reassignable](https://utho.com/reserved-ip) | **UNVERIFIED** | Documented reassignability; live proof pending | **UNVERIFIED** |
| OCI | [Reserved/reassignable](https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/managingpublicIPs.htm) | Supported by direct public-subnet + internet-gateway design; live proof pending | Documented reassignability | No NAT for public instance per docs |
| Akamai | [Reserved in region](https://techdocs.akamai.com/cloud-computing/docs/reserved-ips) | **UNVERIFIED** | Documented within region | **UNVERIFIED** |
| Vultr | [Reserved](https://docs.vultr.com/product-overviews/reserved-ips) | **UNVERIFIED** | Documented failover | **UNVERIFIED** |
| Dhan Cloud | **UNVERIFIED** | **UNVERIFIED** | **UNVERIFIED** | **UNVERIFIED** |
| AWS | [Elastic IP](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/elastic-ip-addresses-eip.html) | Needs live proof for chosen routing | Reassociate within region | No NAT for public instance |

Dhan [requires static IP whitelisting for **order placement**](https://dhanhq.co/docs/v2/authentication/), with a seven-day change restriction; read-only market-data tests do not set or use this whitelist. The two reflector check verifies ordinary HTTPS egress, not necessarily Dhan-specific routing. Verify after VM restart, stop/start and IP reassignment before any execution design decision.

## Identical temporary-instance experiment

Rent only after obtaining all-in quotes. Use the same Ubuntu 24.04 image class, Python package commit, journal filesystem, Dhan instruments, measurement counts and IST windows. Run on **at least three distinct NSE trading sessions**, each covering opening 09:15–09:45, midday 10:00–14:45 and closing 14:45–15:30 IST; check holiday calendar. Keep the VM available over the sessions only as billing requires. A full benchmark can take 10–15 minutes; begin early enough to finish inside the window. Run Utho, OCI and Akamai/Vultr concurrently in each window to control market activity. Record exact VM size, zone, CPU model, image, cost quote, reserved IP, route and benchmark commit. Compare only like Dhan entitlements and instruments.

Use 300+ network and authenticated API probes per window for p99 and failure counts. p99.9 from 300 samples is only an interpolated tail and **not statistically persuasive**; use thousands of observations across repeated windows if it influences a decision. Report p50/p95/p99/p99.9/max with sample count and failures. Read-only profile GET is spaced at least one second, well below Dhan's [non-trading API limit](https://dhanhq.co/docs/v2/). Store raw per-sample UTC time and monotonic elapsed values. Clock status is recorded, but [Dhan 20-level feed](https://dhanhq.co/docs/v2/full-market-depth/) does not establish an exchange timestamp or a contiguous sequence usable for one-way network latency/loss. Feed interarrival gaps and reconnects are diagnostics, affected by market activity; compare per instrument. TCP failures are not packet loss.

### Commands on each candidate

```bash
sudo apt update && sudo apt install -y git python3 python3-venv chrony
git clone https://github.com/DhaneshRamesh/alpha-grid.git
cd alpha-grid
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,dhan]'
mkdir -p benchmark-data/results
# Set DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN and QUANT_DEPTH_INSTRUMENTS
# in a protected local environment, never in shell history, arguments or output.
.venv/bin/python -m infra.benchmark.run_benchmark run \
  --provider Utho --region Mumbai --plan Basic-1 \
  --journal-dir benchmark-data --network-samples 300 --api-samples 300 \
  --interval 1 --api-interval 1 --storage-samples 2000 --cpu-seconds 120 \
  --feed-seconds 180 --stall-seconds 5 \
  --expected-egress-ip RESERVED_IPV4_FROM_PROVIDER \
  --output "benchmark-data/results/utho-$(date -u +%Y%m%dT%H%M%SZ).json"
.venv/bin/python -m infra.benchmark.run_benchmark compare benchmark-data/results/*.json
```

Change only provider, region, plan, reserved IP and output prefix on the other VMs. Add `--monthly-cost`, `--currency INR` and `--static-ip-supported` **only** after a complete cost quote and outbound IP lifecycle verification; otherwise comparison remains ineligible. Keep credentials out of command arguments. The benchmark performs GET `/v2/profile`, discards its account response, uses the existing read-only Dhan depth adapter, and has no order path. `benchmark-data/` is ignored by Git. Encrypt and restrict raw outputs because feed observations and host IPs may be sensitive.

For a live feed run, missing Dhan credentials or instruments now fail before any probe. A run with zero feed events or no successful authenticated profile response saves its partial result and exits with an error, so unattended scheduling can flag it. At `--interval 1 --api-interval 1`, three simultaneous candidates make at most roughly six REST requests per second from these probes, leaving room under Dhan's [20-per-second non-trading limit](https://dhanhq.co/docs/v2/). Check other account activity and the [five-WebSocket connection limit](https://dhanhq.co/docs/v2/full-market-depth/) before concurrent runs.

### Raw results and gates

Each JSON record has `schema_version=2`, `benchmark_version`, UTC `started_at`/`finished_at`, NSE `market_date`/`market_window`, and candidate provider/region/plan/currency/all-in monthly cost. `network.observations` records DNS/TCP/TLS/persistent HTTPS phase, UTC sample time, monotonic elapsed milliseconds, HTTP status/error; `authenticated_api.observations` records profile latency/status without body or token. `egress_ipv4` records two-reflector agreement and reserved-IP match. `feed.observations` and `connection_events` show per-instrument arrival and connection transitions; `system.observations` has scheduler jitter and SHA-256 rate; `storage.observations` has append/fsync times plus throughput and interrupted-record recovery. Aggregates include p50/p90/p95/p99/p99.9/max, counts and failures. Zstd Parquet output is also available for individual runs; `compare` reads JSON.

Ranking is withheld until three complete trading sessions, 30+ feed and network/API observations per window, verified reserved outbound IPv4, successful journal recovery, unchanged candidate metadata and a full all-in monthly cost in one currency. Cost scoring is relative to the compared candidates; equal costs score neutrally. Review **worst window** tails, failures, CPU steal/throttle, reconnects and instrument gaps before treating any numeric rank as meaningful. Distinct market update rates make feed interarrival an integrity signal, not pure network latency. Any non-200 authenticated response counts as failure.

### Short experiment cost

Three providers × three sessions × three windows, with ~10–15 minutes per run, means ~4.5–6.75 **active benchmark hours per VM**. If VMs and reserved IPs remain allocated across three consecutive days, budget roughly **72 billable hours per provider**, plus storage/IP/backup/egress and tax. At published rates this is only a lower bound: OCI 1 OCPU/4 GB compute ≈₹213.26/72 h plus volume; Akamai G6 ≈$3.89/72 h plus reserved IP; Utho hourly/minimum charge **UNVERIFIED**. A Utho NAT Gateway held 72 h would add ₹269.28 plus transfer before tax. Actual experiment cost remains **UNVERIFIED** until quotes and deletion/stop billing rules are captured. Set spending caps, remove temporary resources promptly, and keep the journal output before deletion.

## Decision today

- **Best budget market-data collector: UNDETERMINED.** Utho has the lowest documented base price among the primary shortlist, but Mumbai capacity, all-in bill, reserved outbound IP and data-gap tails remain unverified. Its shared CPU and 99.5% single-instance SLA must be judged against measured stalls and recovery.
- **Best eventual live-trading executor: UNDETERMINED.** Compare dedicated CPU or better-isolated plans and total cost only after equivalent Dhan-path measurements. A more expensive VM wins only if sustained p99/p99.9 jitter, disconnects and failure reduction justify the difference; no order latency or production suitability has been measured.

**Blockers:** Utho written price/IP/CPU policy and live Mumbai stock; complete cross-provider quotes; valid Dhan read-only entitlement; verified reserved-IP lifecycle and egress; three NSE sessions of raw measurements. No production deployment or live orders are authorized by this benchmark.
