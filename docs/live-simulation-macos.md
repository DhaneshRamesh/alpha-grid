# Mac persistent simulation, 2026-11-02

Start: `PATH=/opt/homebrew/opt/node@24/bin:$PATH bash tools/start-cockpit-macos.sh`.
The existing 8765 Rust gateway, 8767 local service, 5173 React cockpit and
`quant.apps.live_simulation` worker stay under one launcher. The launcher
opens the local browser. The Simulation tab's Stop button creates a private
stop marker; the gateway closes its journal and all children exit. Ctrl+C
uses the same stop path. The UI and saved history work without a feed.
The launcher also supervises `quant.apps.local_training_worker` for one
bounded exploratory job at a time. Statistical research's Add & Train form
uses the pinned local RELIANCE archive; missing stock history is BLOCKED.
Completed artifacts live in `training-artifacts/` below the private directory
and are never promoted to simulation or paper execution.

The worker uses the fixed `docs/cross-stock-universe-2026-11-02.json` and
reads only the authenticated *local* gateway snapshot after Rust's QJ01
raw-frame fsync and decode. It has no Dhan or broker client. Its separately
versioned rule samples fresh bid/ask quotes, requires a 15-minute past midquote
comparison, at most 10 bps current quoted spread and at least 15 bps positive
midquote move, then ranks by that move. It selects at most one position at a
time and one decision per instrument/minute, three positions per day, with
₹1,000,000 virtual starting capital and ₹100,000
reference buy-notional cap. Entries use observed ask references, exits use
observed bid references after 30 minutes on the same date. Published intraday
fee estimates and an additional assumed 40 bps round-trip allowance reduce
virtual cash. Observed quotes are not fills. An unresolved position remains
open if a fresh same-day exit quote never arrives; no close is invented.

Each received gateway observation, candidate, rejection, virtual cash
movement, gap, start and stop is fsynced to the owner-private hash-chained
`~/Library/Application Support/AlphaGrid/offline-dev/simulation-ledger.jsonl`.
Restart validates and replays that ledger. Corrupt or partial records block
startup. A same-arrival-ordinal replay cannot duplicate a virtual trade.
`simulation-status.json` is an atomic UI read model; the ledger is the source
of truth. Its recent trade, decision and equity views are kept bounded in
memory while the complete ledger remains on disk. `/trade/simulation` and
`/trade/simulation/stop` require the existing
cockpit session. Neither endpoint contains credentials or writes QP01.

## Dated feed gate

Trading Settings now provides a signed-session Market Data Connection section.
An explicit approval creates owner-private `feed-scope.json` with schema
`alpha-grid-market-data-access-v2`, the exact frozen 25-ID manifest hash,
standard quote feed, session-only or persistent duration, optional expiry and
auto-connect state. Disconnect and revoke use the gateway stop marker.
Persistent permission does not skip same-day identity, CAS, calendar, clock,
credential and storage gates. The launcher expects dated public evidence at
`data/instruments/dhan-scrip-master-YYYY-MM-DD.{csv,manifest.json}`,
`data/instruments/NSE_CM_security_DDMMYYYY.{csv.gz,manifest.json}` and the
existing `data/config/calendar.json`. Automatic acquisition of that dated
evidence is not yet implemented. Optional depth approval is now available
when the existing Rust `quant-depth` binary has been built with its
`live-readonly` feature. It shares the same dated scope, clock and stop gate;
its original frames remain a separate QJ01 research archive. The gateway's
order-book view does not yet consume that separate depth journal.

**No feed scope is approved or configured by this implementation.** The
launcher stays DATA_ONLY without a private `feed-scope.json`. After a separate
explicit approval of the exact dated 25-name NSE_EQ Quote-17 scope, an operator
may create that owner-private (mode 0600) file in the directory above. Its
required fields are:

```json
{
  "schema_version": "alpha-grid-approved-quote-scope-v1",
  "authorization_confirmed": true,
  "session_date_ist": "YYYY-MM-DD",
  "purpose": "SIMULATION_ONLY",
  "standard_connections": 1,
  "depth_connections": 0,
  "authorized_feeds": ["STANDARD_QUOTE"],
  "authorized_security_ids": ["exact 25 security IDs in manifest order"],
  "expires_at_ist": "YYYY-MM-DDT15:14:00+05:30",
  "universe_sha256": "SHA-256 of the exact frozen manifest bytes",
  "master": "repository-relative current public Dhan master path",
  "master_manifest": "repository-relative master manifest path",
  "calendar": "repository-relative dated calendar path",
  "nse_security": "repository-relative same-day NSE MII security file path",
  "nse_manifest": "repository-relative same-day NSE retrieval/hash manifest path"
}
```

The launcher rechecks this gate while running. It requires the exact frozen
25 IDs and ISINs, a dated same-day NSE identity/normal-market/CAS file, current
public Dhan mapping, independently supported ordinary-session evidence,
private journal capacity, a mode-0600 OAuth token and matching hash/expiry
metadata in `data/.secrets`, an owner-private client ID in either
`data/.secrets/dhan-client-id` or `.env`, and `sntp` offset plus
uncertainty within 100 ms. Rust repeats clock and source preflight before the
authenticated connection and while online. At most one standard socket is
open. A failed feed process is replaced at most twice on that dated session,
and each replacement repeats preflight; gaps block new virtual decisions.
The launcher stops capture at the earlier approved expiry or 15:14 IST.
`feed-gate-status.json` reports only a reason code and checked time to the
local backend; credentials never enter the UI process. OAuth consent tokens
require renewed operator authentication on expiry: Dhan's documented
`RenewToken` is limited to active tokens generated from Dhan Web and cannot
be assumed to renew a token from this API-key consent flow. The optional 20-level depth feed
is disabled here; no depth evidence was needed for this fixed quote-only rule.
No account endpoint, order path, QP01 simulation write or paid service is used.

The Mac currently has no approved 25-name scope, no verified same-day 25-name
NSE MII/CAS evidence and no genuine standard/depth QJ01 frames. No authenticated
socket was opened. Historical RELIANCE replay remains a separate verified
exploratory view. A full live session and bounded reconnection behavior still
require a separately authorized real observation to validate end to end.
