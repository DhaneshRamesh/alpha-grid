# Indian execution VPS research (11 October 2026)

This is a read-only comparison plan. No server has been provisioned or measured for
this project. A cloud region in Mumbai does **not** establish low latency to Dhan's
actual broker and feed front ends. Broker routing, CPU contention, clock quality,
and fsync tails need measurements on the candidate machines during NSE sessions.
Prices below are public list prices, before tax and promotions, where verifiable.
`?` means the current Mumbai-specific value or entitlement needs an account quote.

## Documented facts

| Provider / physical region | Candidate plan | CPU / allocation / x86_64 | RAM / disk / network | Stable IPv4 | Public cost and billing | Limits and reliability |
| --- | --- | --- | --- | --- | --- | --- |
| [OCI India West, Mumbai](https://www.oracle.com/apac/cloud/public-cloud-regions/) | `VM.Standard.E4.Flex`, 1 OCPU (2 vCPU), 8 GB, 50 GB balanced boot volume | 3rd Gen AMD EPYC, x86_64; 1 physical core with two SMT threads on a multitenant VM | 8 GB; network block volume; [up to 1 Gbps/OCPU](https://blogs.oracle.com/cloud-infrastructure/announcing-oracle-cloud-compute-e4-platform-on-third-gen-amd-epyc-processors) | [Reserved public IPv4](https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/managingpublicIPs.htm); verify current IP SKU before purchase | [India list](https://www.oracle.com/in/cloud/price-list/): ₹3.535165/hour compute; ₹2,580.67 at 730 h. 50 GB balanced volume: ₹203.03/month (50 × [₹2.4363975 storage + 10 × ₹0.1624265 VPU](https://www.oracle.com/in/cloud/price-list/)); **₹2,783.70/month excluding possible IP charge**. First [10 TB outbound/month free](https://www.oracle.com/cloud/networking/virtual-cloud-network/pricing/). | [Boot volume minimum 50 GB](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm); [stopped standard VM compute billing pauses](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/resource-billing-stopped-instances.htm), storage continues. Confirm exact [compute SLA](https://www.oracle.com/in/cloud/sla/) for the shape. No local NVMe in this plan. |
| [Akamai/Linode Mumbai core](https://techdocs.akamai.com/cloud-computing/docs/how-to-choose-a-data-center) | `G6 Dedicated 4 GB`, 2 vCPU, 80 GB SSD; also test `Linode 4 GB` shared as collector cost floor | Dedicated G6 vCPU on legacy hardware, exact host CPU generation not guaranteed; x86_64. Shared plan has 2 shared vCPUs. | 4 GB; 80 GB SSD; [40/4 Gbps advertised network and 4 TB transfer](https://www.akamai.com/cloud/pricing/cpu) | [Reserved IPv4](https://techdocs.akamai.com/cloud-computing/docs/reserved-ips); region-specific charge ? | [Global list](https://www.akamai.com/cloud/pricing/cpu): G6 $0.054/hour, $36/month; shared $0.036/hour, $24/month. **Mumbai all-in ?** because reserved IP and regional price require confirmation; published overage $0.005/GB in standard regions. | [Dedicated cores can sustain load](https://techdocs.akamai.com/cloud-computing/docs/dedicated-cpu-compute-instances); stopped VM billed while allocated ([billing docs](https://techdocs.akamai.com/cloud-computing/docs/understanding-how-billing-works)). Verify Mumbai plan availability and SLA. |
| [Vultr Mumbai](https://blogs.vultr.com/Namaste-India-Vultr-has-landed-in-Mumbai) | `vhp-2c-4gb` Optimized Cloud Compute candidate; confirm exact plan, disk and capacity in Mumbai console | Dedicated vCPU [Optimized](https://docs.vultr.com/product-overviews/optimized-cloud-compute); Mumbai launch cited 3rd Gen AMD EPYC; current host generation ?, x86_64 | 4 GB; NVMe category, exact included GB/network ? | [Reserved IPv4](https://docs.vultr.com/products/network/reserved-ips/provisioning) | Exact matching VM price ?; [reserved IP $0.004/hour (~$3/month)](https://docs.vultr.com/support/products/network/are-reserved-ips-free). A published **1 vCPU/4 GB/30 GB** dedicated floor is [$0.045/hour](https://docs.vultr.com/product-overviews/optimized-cloud-compute), but it does not meet the 2-vCPU requirement. Outbound allowance/overage for selected Mumbai plan ?. | [Stopped instances still billed](https://docs.vultr.com/support/platform/billing/how-am-i-billed-for-my-servers); non-GPU compute capped at 672 billed hours/month per current docs. Regional price can differ; verify SLA and IP availability. |
| [Utho Mumbai](https://utho.com/cloud-hosting-provider) | `Basic 1` shared CPU; also quote a 2-vCPU dedicated plan | 2 shared vCPU; host CPU generation and x86_64 image compatibility must be checked on the actual VM. [Dedicated tier advertises AMD EPYC/Intel Xeon](https://utho.com/dedicated-cpu), but that does not specify Basic 1 hardware. | [4 GB, 80 GB NVMe, 1 TB transfer](https://utho.com/pricing); exact port speed and overage ? | [Reserved IPv4 in Mumbai](https://utho.com/reserved-ip); verify it is the *outbound* source address | [₹1,794/month Basic 1 list](https://utho.com/pricing), storage included; hourly rate, reserved IP cost and bandwidth overage ?; **all-in > or = ₹1,794** before tax | [Single-instance SLA is 99.5%](https://utho.com/sla), although marketing pages say 99.99% for multi-zone/region. Stopped-VM billing and CPU contention policy ?. Strong low-cost collector candidate; execution tier must be measured. |
| [Dhan Cloud](https://dhan.co/support/platforms/dhanhq-api/what-are-the-charges-for-deploying-my-algo-on-dhan-cloud/) | Exact 2 vCPU/4–8 GB plan ? | CPU, isolation, architecture ? | Region, disk and network ? | Stable *egress* IPv4 and portability ? | No public rate card; [portal estimator](https://dhan.co/support/platforms/dhanhq-api/what-are-the-charges-for-deploying-my-algo-on-dhan-cloud/) requires login. Hourly/monthly/storage/IP/egress ? | Exclude from comparable ranking until documented specifications, static egress IP, export access and benchmark execution are confirmed. |
| [AWS Mumbai `ap-south-1`](https://docs.aws.amazon.com/ec2/latest/instancetypes/ec2-instance-regions.html) | `m8a.large`, 2 vCPU/8 GiB + 50 GiB gp3 | [5th Gen AMD EPYC, x86_64](https://aws.amazon.com/ec2/instance-types/m8a/); virtualized cores, not tenancy-dedicated | EBS-only; [up to 12.5 Gbps network](https://aws.amazon.com/ec2/instance-types/m8a/) | Elastic IPv4 | Mumbai VM and gp3 GB/hour prices ? from public dynamic calculator. [Public IPv4 $0.005/hour ($3.65/730 h)](https://aws.amazon.com/vpc/pricing/); outbound billed by regional tier. **All-in ?** | Compute stops billing after stop; EBS and EIP persist/bill. [Single-instance EC2 SLA 99.5%](https://aws.amazon.com/compute/sla/). Benchmark reference, not assumed winner. |
| [Google Cloud Mumbai `asia-south1`](https://cloud.google.com/about/locations) | `n2-standard-2`, 2 vCPU/8 GiB + 50 GiB `pd-balanced` | N2 Intel x86_64; generation can vary by zone/host, shared physical host | Network-attached balanced SSD; [up to 10 Gbps egress for 2 vCPU N2](https://cloud.google.com/compute/docs/general-purpose-machines#n2_machines) subject to network limits | [Static external IPv4](https://docs.cloud.google.com/compute/docs/ip-addresses) | [Google's machine price page](https://cloud.google.com/products/compute/pricing/general-purpose) lists prices by selected region; capture **Mumbai** VM, disk, IP and egress line items in calculator before scoring. **All-in ?** | Stopped CPU/RAM billing stops; disk/static IP persist/bill. [Single instance Premium Tier SLA 99.9%](https://docs.cloud.google.com/compute/docs/ip-addresses). Benchmark reference. |

The OCI estimate is arithmetic on Oracle's India list and assumes 730 hours, a paid
balanced boot volume, no free-tier credits and no tax. It is **not** an invoice quote.
Akamai's $36/$24 are published plan rates, not validated Mumbai all-in totals.
CPU model, effective clock and storage latency are measured by the harness because
plan branding does not promise a specific physical host. Network specifications
are capacity ceilings, not evidence of Dhan path latency.

### Other India-based providers checked

| Provider | Finding | Decision |
| --- | --- | --- |
| [Cyfuture Cloud](https://cyfuture.cloud/virtual-machine) | Published 2-vCore/4 GB/40 GB NVMe VM starts at $14.56/month; [Navi Mumbai facility](https://cyfuture.cloud/data-center-mumbai) and [dedicated VPS IP](https://cyfuture.cloud/linux-vps-hosting) are advertised. The plan page does not establish that this exact price is available in Mumbai, that the IP is reserved and portable, or that the outbound source address stays fixed. CPU model/allocation and hourly billing also need a quote. | Request a Mumbai VM + stable-egress-IP quote; benchmark if confirmed. Do not rank on the teaser price. |
| [E2E Cloud](https://docs.e2enetworks.com/api/myaccount/network/reserve-ip/post-reserve-ips/) | Current reserved-IP API docs say **Mumbai has been discontinued for this API**; Delhi and Chennai are supported. The response shows ₹199 excluding GST only as an **example**, not a live quote. [CPU pricing changed in August 2026](https://docs.e2enetworks.com/docs/myaccount/billing/pricing-update-july-2026/). | No verified Mumbai reserved-IP candidate. Consider Delhi/Chennai only if actual Dhan path tests justify it; retrieve a current calculator quote. |
| [Yotta Yntraa](https://www.yotta.com/wp-content/uploads/2023/12/Yntraa.pdf) | Provider brochure lists Mumbai cloud VMs and IP service, but a current 2-vCPU plan, reserved-egress-IP behavior, public price and small-VM SLA were not verifiable. | Quote-only watchlist. |

Utho is the strongest additional India-based candidate: it documents Mumbai,
a 2-vCPU/4-GB/80-GB plan, and a reserved-IP product. Confirm that Basic 1 is
available in the selected Mumbai zone. Its ₹1,794 figure is a **compute-plan floor**, not a
verified all-in monthly bill. A reserved **inbound** IP is insufficient for broker
whitelisting unless the provider confirms it is also the outbound source IP.

## Measurements still required

| Provider | Region | Plan | CPU type | Dedicated/shared | RAM | Static IP | Monthly all-in cost | TCP p50/p99 | TLS p50/p99 | API p50/p99 | Feed delay p50/p99 | CPU jitter p99 | fsync p99 | Reliability notes | Collector score | Executor score |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| OCI | Mumbai | E4 Flex 1 OCPU/8 GB | AMD EPYC E4 | multitenant VM | 8 GB | yes | ≥₹2,783.70 + unverified IP/tax | — | — | — | — | — | — | — | — | — |
| Akamai | Mumbai | G6 Dedicated 4 GB | host TBD | dedicated vCPU | 4 GB | yes | ≥$36 + reserved IP/tax | — | — | — | — | — | — | — | — | — |
| Vultr | Mumbai | `vhp-2c-4gb` | host TBD | dedicated vCPU | 4 GB | yes | ? + $3 IP | — | — | — | — | — | — | — | — | — |
| Utho | Mumbai | Basic 1 | host TBD | shared vCPU | 4 GB | reserved; egress unverified | ≥₹1,794 + IP/tax | — | — | — | — | — | — | — | — | — |
| Dhan Cloud | ? | ? | ? | ? | ? | ? | ? | — | — | — | — | — | — | — | — | — |
| AWS | Mumbai | m8a.large | AMD EPYC Gen 5 | shared host | 8 GiB | yes | ? + $3.65 IP | — | — | — | — | — | — | — | — | — |
| GCP | Mumbai | n2-standard-2 | Intel, host varies | shared host | 8 GiB | yes | ? | — | — | — | — | — | — | — | — | — |

The Dhan 20-level wire feed normally lacks an exchange timestamp; the feed-delay
cell must remain empty when it does. TCP failures are **not** packet-loss estimates.
Its documented header sequence should not be assumed contiguous; packet-loss
measurement remains unavailable until upstream semantics are confirmed. Wall-clock
feed delay should be compared only when chrony/NTP reports synchronization and an
offset within 5 ms. A direct feed jitter distribution is reported per instrument.

## Benchmark procedure

On each **temporary** Ubuntu 24.04 candidate, run identical code and parameters on
the same intended journal filesystem. The network probe independently times DNS,
TCP, TLS, and a read-only GET of `/v2/` over a persistent HTTPS connection. HTTP
status may be 401/404; it is recorded, not interpreted as request success. Optional
public WSS URLs contain no credentials. The optional Dhan sample uses the project's
read-only depth adapter, measures first WebSocket connection, disconnects, stalls,
per-instrument interarrival and exchange-to-receive delay where supplied.

The host probe records kernel, CPU model/clock, CPU steal, cgroup throttling,
scheduler jitter, SHA-256 single-thread throughput, context switches, memory,
uptime, hypervisor visibility and clock status/offset where `chronyc` is installed.
The storage probe writes 4 KiB sequential records and fsyncs each record, reporting
p50/p90/p95/p99/p99.9/max in milliseconds. It deletes only its own temporary file.
Results contain no token, URL query or response body. JSON and Zstd Parquet output
are supported; compare consumes JSON. The benchmark does not place orders.

Run one 2–5 minute sample in each IST window on **at least two trading days**:
08:30–09:15 pre-open, 09:15–09:45 open, 10:00–14:45 normal, 14:45–15:30 close.
Use an external scheduler and exclude NSE holidays. Repeat more days for confidence.
Sampling p99.9 with 30 network probes is weak; for final evidence use thousands of
observations per window. Do not compare a subscribed live feed to an inactive one.

### Fresh Ubuntu commands

```bash
sudo apt update
sudo apt install -y git python3 python3-venv chrony
git clone https://github.com/DhaneshRamesh/alpha-grid.git
cd alpha-grid
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,dhan]'
mkdir -p benchmark-data/results
.venv/bin/python -m infra.benchmark.run_benchmark run \
  --provider OCI --region Mumbai --plan E4-Flex-1OCPU-8GB \
  --journal-dir benchmark-data --network-samples 300 \
  --storage-samples 2000 --cpu-seconds 120 \
  --output "benchmark-data/results/oci-$(date -u +%Y%m%dT%H%M%SZ).json"
```

Change only `--provider`, `--region`, `--plan` and the filename on the other
servers. Supply `--monthly-cost` only from a complete current bill estimate in a
common currency, and `--static-ip-supported` only once reserved **egress** IPv4
is verified. To measure the read-only Dhan depth feed, set `DHAN_CLIENT_ID`,
`DHAN_ACCESS_TOKEN` and `QUANT_DEPTH_INSTRUMENTS` in the process environment, then
add `--feed-seconds 180 --stall-seconds 5`. Do not put secrets in command arguments
or results. Parquet output uses a `.parquet` filename; compare JSON files with:

```bash
.venv/bin/python -m infra.benchmark.run_benchmark compare benchmark-data/results/*.json
```

The two weighted scores have independent weights for network latency, network
jitter, CPU consistency, fsync persistence, reliability, reserved IP and monthly
cost. Collector weights are `18/16/10/20/18/8/10`; executor weights are
`28/20/18/7/17/7/3`. Bandwidth and surplus cores/RAM get no score. Scoring
withholds a candidate until two days, all four windows, 30+ network and feed
observations per window, a measured exchange timestamp, verified clock, reserved
IPv4 and all-in cost exist. Scores use the **worst window's p99**, not averages;
review raw tails and failure counts alongside scores. These thresholds/weights are
research policy, not a claim that any provider is fit for live orders.

**Best value, best latency, cheapest acceptable, best data collector and best
future live executor: undetermined.** No candidate has real path measurements yet.
