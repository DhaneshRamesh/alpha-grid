# Utho Rust trial checkpoint — 2026-10-12

**Status:** no new VM was created. The signed-in deployment form returned to
"Deploy Now" after repeated submissions without an error message. Utho activity
logs contain no new cloud-server order. The Cloud Instances and EBS inventories
show zero current resources. The old server `#990002991` has a recorded deletion
at 2026-10-11 23:15 IST with a 72-hour deletion hold; Current Usage labels it
"Active" but shows zero minutes running and ₹0 new charges. Treat this
inconsistent status as a console issue, not evidence of a running VM.

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

To resume, inspect the exact error from Utho's Deploy Now action or ask Utho
support why this account's Mumbai hourly deployment does not place an order.
Before any retry, verify the Cloud Instances and EBS inventories are still
empty, refresh the SSH `/32` source address, and confirm the ₹2.40/hour quote.
Once a VM exists, record its resource IDs, deployment time, safety deadline and
public IPv4, then run the commands in `rust/README.md` and the Python reference
commands in `infra/benchmark/ingestion.md`. Export and checksum the raw files
before deleting the VM and its storage.
