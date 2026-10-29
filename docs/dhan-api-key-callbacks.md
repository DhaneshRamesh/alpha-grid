# Dhan API-key callbacks

This local service is separate from Alpha Grid's market-data acquisition and
paper ledgers. It has no order-placement code. It binds 127.0.0.1:8766; the
existing Rust telemetry gateway uses port 8765. Neither a public hostname
nor a deployment is configured in this checkout.

## Dhan contracts

Dhan's [API-key authentication guide](https://dhanhq.co/docs/v2/authentication/)
describes: generate consent with POST /app/generate-consent, browser login,
a redirect containing tokenId, then GET /app/consumeApp-consent with app_id
and app_secret headers. The callback implements the final step, checks the
returned dhanClientId, and stores the access token and Dhan-provided expiry
metadata in ignored, private `data/.secrets/`. It never returns either to the
browser or stdout.

Dhan's [postback guide](https://dhanhq.co/docs/v2/postback/) specifies a raw
JSON POST containing order updates. It documents no signature, shared secret,
or source-verification method. A matching dhanClientId is only a consistency
check. The endpoint stores exact accepted body bytes in ignored
data/.secrets/dhan-postbacks/ with SHA-256, receipt time, and
origin_verified=false. These records are quarantined; they do not update
orders, fills, positions, QP01, or risk state. Payloads over 64 KiB,
malformed JSON, duplicate JSON keys, unknown status, and wrong client IDs
are rejected.

## Local setup

The existing API key, secret and client ID are kept in ordinary-user-owned
private NTFS files `data/.secrets/dhan-api-key`, `dhan-api-secret` and
`dhan-client-id`. The callback verifies their DACLs and loads them in memory;
it does not load `.env`. Start exactly one consent attempt from the repository
root in an ordinary PowerShell:

    .venv\Scripts\python.exe -B tools\dhan_oauth_callback.py --initiate

This binds the callback first, makes one documented generate-consent POST,
and opens the official Dhan browser login. Complete the login and any OTP
yourself. The service never prints identifiers or callback URLs.

The browser redirect to /dhan/callback consumes tokenId once. On success the
browser sees a generic completion page. To make the saved access token
available to an acquisition command in your own terminal, load it without
printing it:

    $env:DHAN_ACCESS_TOKEN = [IO.File]::ReadAllText(
      (Resolve-Path 'data/.secrets/dhan-access-token')
    ).Trim()

## Manual renewal after the October 27 HTTP 401

The saved October 27 chart error was `DH-901 / Invalid_Authentication`.
The old private saved token has an embedded JWT expiry claim before the request,
but that callback version retained only `accessToken`, not Dhan's separate
`expiryTime`. The current callback now saves Dhan's `expiryTime` in a private
checksum-linked sidecar. The old token's signature and server validity were not checked
offline, so expiration is likely but not conclusively established. File
modification time is not authoritative token-expiry evidence. The one chart
request is consumed; do not retry it under that authorization.

For manual renewal, use the `--initiate` flow above. Verify that the
registered redirect reaches the existing local callback on port 8766.
Do not paste a `tokenId` or token into chat or logs. Stop the callback
service after completion. This is a manual procedure;
the Alpha Grid cockpit launcher never generates or renews Dhan credentials.
Any new historical, portfolio, order, trade or ledger read still needs its
own approved request scope.

Local endpoint checks can send synthetic data to the two paths. They do not
contact Dhan. Tests use an injected fake auth response.

## URLs and public delivery

Local test URLs are:

| Field | Local URL |
| --- | --- |
| Redirect URL | http://127.0.0.1:8766/dhan/callback |
| Postback URL | http://127.0.0.1:8766/dhan/postback |

Dhan explicitly says postbacks are not delivered to localhost or 127.0.0.1;
do not register the local postback URL expecting live delivery. Dhan's
documentation does not establish whether its API-key form accepts a loopback
redirect. Its browser login must run on the same machine for a loopback
redirect to reach this service.

For usable Dhan URLs, first deploy an HTTPS reverse proxy on a hostname you
control, route both paths to this loopback service, suppress query-string and
body logging, and set ALPHA_GRID_PUBLIC_BASE_URL=https://your-host in the
service process. The service derives both URLs from that origin and rejects
non-HTTPS or loopback public origins. Setting the variable alone does not
deploy a service or prove Dhan can reach it. No reverse proxy, DNS, TLS, or
public host is provided by this repository.

Because Dhan documents no postback signature, public postbacks remain
unverified even after deployment. Keep them quarantined until independent
authentication evidence or a documented provider verification mechanism is
available. Do not treat them as confirmed fills or orders.
