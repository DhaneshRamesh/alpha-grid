# Utho Rust trial checkpoint — 2026-10-12

## Post-KYC deployment attempt — 2026-10-12, about 23:06 IST

The account holder reported KYC status **Verified**. The signed-in dashboard
no longer showed the earlier KYC prompt, but neither dashboard nor Account
Settings displayed an explicit approval badge, so independent KYC approval
remains unverified. The authorized deployment was attempted once after a fresh
preflight: Mumbai, hourly Premium AMD Basic 1 (2 vCPU/4 GB), Ubuntu 24.04,
80 GB NVMe root, default public IPv4, quantity one, trial security group, and
the matching trial SSH key. The console quoted ₹1.96/hour compute plus
₹0.44/hour storage, ₹2.40/hour before applicable tax. No paid extras were
selected. SSH ingress was limited to the operator's then-current
`106.222.203.84/32` address.

The Deploy button showed **Deploying...** and returned to **Deploy Now** after
about 20 seconds without a visible error, status code, or request ID. No
second submission was made. Cloud Instances and EBS still show zero resources;
Activity Logs show no new server order. Current Usage remains ₹1.06 from the
prior pilot, so observed new trial usage is ₹0. There are no VM measurements
or Rust/Python comparisons from this attempt. The 72-hour trial clock has not
started. Do not retry until Utho identifies the rejection or supplies a
sanitized error/request ID. Do not create an account-wide API token.

Suggested support request: “Our account holder reports KYC Verified. A single
Mumbai hourly deployment on 12 October around 23:06 IST (Ubuntu 24.04,
2 vCPU/4 GB, 80 GB root disk, standard IPv4, SSH key) returned from
‘Deploying...’ to the form without an error. No Cloud Instance, EBS volume, or
activity-log order appeared. Please confirm the account's deployment
eligibility and provide the sanitized failure reason, HTTP status and request
ID. Please do not create a VM on our behalf.”

**Status:** no new VM was created. The signed-in deployment form returned to
"Deploy Now" after repeated submissions without an error message. Utho activity
logs contain no new cloud-server order. The Cloud Instances and EBS inventories
show zero current resources. The old server `#990002991` has a recorded deletion
at 2026-10-11 23:15 IST with a 72-hour deletion hold; Current Usage labels it
"Active" but shows zero minutes running and ₹0 new charges. Treat this
inconsistent status as a console issue, not evidence of a running VM.

On 2026-10-12, the dashboard displayed **Complete your KYC verification**.
Its verification dialog reported `Status: pending` and directed the account
holder through PAN and video KYC with Digio. This is a plausible deployment
blocker, but the console did not explicitly connect KYC to the failed request.
The support center has zero tickets. No sanitized HTTP error code or request ID
was exposed by the console, so both remain **UNVERIFIED**. Stop deployment
retries until the account holder completes KYC or Utho support confirms the
reason. Do not handle identity documents or create account-wide API tokens.

The account holder later reported KYC completed. Approval status has not been
independently checked in this validation task. No deployment retry was made.

The dashboard resource inventory shows zero cloud servers, block storage,
elastic IPs and snapshots. It does show six VPCs and two firewalls, including
the trial firewall; these are not evidence of a running trial VM. Trial spend
remains ₹0. No teardown timer is needed while no VM exists.

Configured trial: Mumbai, Ubuntu 24.04, Premium AMD Basic 1 (2 vCPU / 4 GB),
80 GB NVMe root EBS, hourly, default public IPv4, one instance, SSH key
`alpha-grid-utho-trial-20261012`, and security group
`alpha-grid-trial-20261012`. SSH inbound is limited to the development host's
then-current `/32` public address; update it if that address changes. The
console quoted ₹1.96/hour compute plus ₹0.44/hour root storage, or ₹2.40/hour
before 18% GST. No paid reserved IP, NAT, backup, snapshot or extra volume was
selected. The prefilled `UTHODEPLOY` coupon was removed after the first two
failed submissions; a subsequent submission still did not create a server.

The approved trial cap is ₹250 including tax and 72 hours from deployment.
Because no deployment occurred, trial expenditure is ₹0 and the 72-hour clock
has not started. Utho Current Usage shows ₹1.06 before GST from the previous
pilot; it must not be charged to this new trial. Wallet balance is not a cap.

An account-wide write API token was considered as an independent teardown
safeguard. Automatic approval review rejected creating it because its scope is
broader than the trial. No token was created. The safe fallback was active
manual monitoring and same-session teardown. Do not leave a new VM unattended
without a verified teardown path.

The Rust workspace is committed at `1f293bb` and pushed to `main`. Local Mac
smoke artifacts (not Utho measurements) are under `work/rust-smoke.json` and
`work/python-smoke.json.trials.jsonl`. They verify that both runners execute;
their concurrent runs and different journal encodings do not support a speed
comparison. Utho Rust and Python performance remain **UNMEASURED**.

To resume in a separate authorized deployment task, verify that the reported
KYC completion has been approved. If a later deploy still fails, ask Utho
support why this account's Mumbai hourly deployment does not place an order
and request a sanitized status code or request ID. Do not submit another
deployment while the blocker is unknown.
Before any retry, verify the Cloud Instances and EBS inventories are still
empty, refresh the SSH `/32` source address, and confirm the ₹2.40/hour quote.
Once a VM exists, record its resource IDs, deployment time, safety deadline and
public IPv4, then run the commands in `rust/README.md` and the Python reference
commands in `infra/benchmark/ingestion.md`. Export and checksum the raw files
before deleting the VM and its storage.
