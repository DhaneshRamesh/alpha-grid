# November 3 Dhan candle probe: offline preflight (2026-11-02 IST)

**Status: NO GO.** This document authorizes no Dhan request. The probe's
`--execute-read-only` mode must not be used until every item below is evidenced
and the operator separately approves the exact eight-request scope.

## Dated NSE evidence

- NSE circular [NSE/MSD/60315](https://nsearchives.nseindia.com/content/circulars/MSD60315.pdf)
  names the daily `NSE_CM_security_ddmmyyyy.csv.gz` CM MII security file on
  [All Reports](https://www.nseindia.com/all-reports). For this session the
  filename is `NSE_CM_security_03112026.csv.gz`. A later [NSE member circular](https://nsearchives.nseindia.com/content/circulars/MSD67344.pdf)
  instructs loading the updated master before trading hours; neither source
  promises an exact publication hour. The file must be obtained and checked
  before probe launch. An inaccessible URL is not proof that no file exists.
- Preserve original compressed bytes, URL, retrieval start/end UTC, HTTP
  status/headers if available, and SHA-256. Require a unique NSE cash `EQ`
  RELIANCE row with `FinInstrmId=2885`, `TckrSymb=RELIANCE`, `SctySrs=EQ`,
  `ISIN=INE002A01018`, `ElgbltyNrmlMkt=1`, and an explicit
  `ElgbltyClsgAuctnSsn` value. `1` means the verified continuous session
  closes 15:15; `0` means 15:30. Neither value can be presumed from October.
  Compare a fresh public [Dhan compact master](https://dhanhq.co/docs/v2/instruments/)
  for NSE/EQUITY/EQ/RELIANCE/2885; an NSE numeric ID alone does not prove
  Dhan's current mapping.
- Independently check the [NSE 2026 trading calendar](https://www.nseindia.com/resources/exchange-communication-holidays)
  and same-day notices for an open, undisturbed regular session. Review the
  filtered [NSE corporate-actions report](https://www.nseindia.com/companies-listing/corporate-filings-actions)
  for RELIANCE/EQ, retaining its original export and retrieval time. Inspect
  symbol, company, series, purpose, ex-date, record date and book-closure
  dates. An ex-date/effective action on November 3 is a stop condition; an
  unverified or unfilterable report is also a stop condition. A later
  publication must retain its actual receipt time and cannot establish what
  was known before an earlier probe slot.

## Minimal Mac evidence commands (public and offline only)

Run in a private terminal on November 3. These commands do not read a token.
The direct archive URL follows the repository's prior NSE file convention;
the official All Reports page is the publication source. Abort if the file
cannot be obtained and corroborated there. Select the **NSE Listed
securities** CM MII entry, not the separate BSE Exclusive entry.

```sh
cd /Users/dhaneshramesh/alpha-grid
set -e
umask 077
evidence_dir=data/historical/nov3-probe-preflight
mkdir -p "$evidence_dir"
date -u '+%Y-%m-%dT%H:%M:%SZ' > "$evidence_dir/nse-download-start.utc"
curl --fail --location --retry 0 --silent --show-error \
  --dump-header "$evidence_dir/nse-http-headers.txt" \
  'https://nsearchives.nseindia.com/content/cm/NSE_CM_security_03112026.csv.gz' \
  --output "$evidence_dir/NSE_CM_security_03112026.csv.gz"
date -u '+%Y-%m-%dT%H:%M:%SZ' > "$evidence_dir/nse-download-end.utc"
shasum -a 256 "$evidence_dir/NSE_CM_security_03112026.csv.gz" \
  > "$evidence_dir/nse.sha256"
date -u '+%Y-%m-%dT%H:%M:%SZ' > "$evidence_dir/dhan-master-start.utc"
curl --fail --location --retry 0 --silent --show-error \
  'https://images.dhan.co/api-data/api-scrip-master.csv' \
  --output "$evidence_dir/dhan-scrip-master.csv"
date -u '+%Y-%m-%dT%H:%M:%SZ' > "$evidence_dir/dhan-master-end.utc"
shasum -a 256 "$evidence_dir/dhan-scrip-master.csv" \
  > "$evidence_dir/dhan-master.sha256"
open 'https://www.nseindia.com/all-reports'
open 'https://www.nseindia.com/companies-listing/corporate-filings-actions'
```

Inspect the preserved NSE bytes without replacing them:

```sh
PYTHONPATH=src .venv/bin/python -B - <<'PY'
import csv, gzip
from pathlib import Path
p = Path('data/historical/nov3-probe-preflight/NSE_CM_security_03112026.csv.gz')
with gzip.open(p, 'rt', encoding='utf-8-sig', newline='') as stream:
    rows = [r for r in csv.DictReader(stream)
            if r.get('TckrSymb') == 'RELIANCE' and r.get('SctySrs') == 'EQ']
assert len(rows) == 1, 'unique RELIANCE/EQ row missing'
r = rows[0]
fields = ('FinInstrmId', 'TckrSymb', 'SctySrs', 'ISIN',
          'ElgbltyNrmlMkt', 'ElgbltyClsgAuctnSsn')
print({key: r.get(key) for key in fields})
assert [r.get(k) for k in fields[:5]] == [
    '2885', 'RELIANCE', 'EQ', 'INE002A01018', '1'
], 'identity or normal-market eligibility mismatch'
assert r.get('ElgbltyClsgAuctnSsn') in ('0', '1'), 'CAS flag missing'
PY
```

Record the Dhan public-master receipt and check it with the existing verifier:

```sh
PYTHONPATH=src .venv/bin/python -B - <<'PY'
import json
from datetime import datetime, UTC
from pathlib import Path
from quant.acquisition.dhan_instruments import MASTER_URL, verify_master_identity
from quant.acquisition.nse import file_sha256
d = Path('data/historical/nov3-probe-preflight')
master = d / 'dhan-scrip-master.csv'
manifest = d / 'dhan-scrip-master.manifest.json'
receipt = datetime.fromisoformat((d / 'dhan-master-end.utc').read_text().strip().replace('Z', '+00:00'))
manifest.write_text(json.dumps({
    'schema_version': 'dhan-instrument-master-v1',
    'source_url': MASTER_URL,
    'sha256': file_sha256(master),
    'retrieved_at': receipt.isoformat(),
}, sort_keys=True) + '\n')
identity = verify_master_identity(master, manifest, symbol='RELIANCE',
                                  security_id='2885', now=datetime.now(UTC))
print(identity.instrument, identity.master_sha256, identity.retrieved_at.isoformat())
PY
```

The generated receipt is bounded by the saved download-start/end UTC files;
it is not a claim that NSE or Dhan published at that instant. Create separate
date-scoped calendar and registry JSON only after reviewing the retained NSE
file, corporate-action export and trading notices. The existing offline
guards can then be checked without a token:

```sh
PYTHONPATH=src .venv/bin/python -B - <<'PY'
from pathlib import Path
from quant.apps.dhan_candle_availability import _verified_session, _verified_instrument
d = Path('data/historical/nov3-probe-preflight')
_verified_session(d / 'calendar.json')
_verified_instrument(d / 'registry.json')
print('dated calendar and registry accepted; external source review still required')
PY
```

For independent Mac time evidence, use the installed `/usr/bin/sntp` without
its `-s` or `-S` clock-setting options. Capture a fresh successful sample
before the 09:59:40 IST launch cutoff and another after the 15:45 sample:

```sh
set -e
evidence_dir=data/historical/nov3-probe-preflight
date -u '+%Y-%m-%dT%H:%M:%SZ' > "$evidence_dir/clock-before-start.utc"
sntp -d -t 3 time.apple.com > "$evidence_dir/clock-before.txt" 2>&1
date -u '+%Y-%m-%dT%H:%M:%SZ' > "$evidence_dir/clock-before-end.utc"
shasum -a 256 "$evidence_dir/clock-before.txt"
# After a separately authorized capture has finished:
date -u '+%Y-%m-%dT%H:%M:%SZ' > "$evidence_dir/clock-after-start.utc"
sntp -d -t 3 time.apple.com > "$evidence_dir/clock-after.txt" 2>&1
date -u '+%Y-%m-%dT%H:%M:%SZ' > "$evidence_dir/clock-after-end.utc"
shasum -a 256 "$evidence_dir/clock-after.txt"
```

Require a successful exchange, identifiable independent server, measured
offset and defensible uncertainty bound. Conservatively require
`abs(offset) + uncertainty <= 100 ms` at the first check; if the later check
fails, timing conclusions are inconclusive. A DNS error, zeroed debug fields
after failure, or local time-service status alone is not a measurement. The
probe additionally records UTC request start/finish and monotonic duration,
and stops after a wall-versus-monotonic step over 250 ms. It does **not**
measure independent clock offset or enforce the external NTP bound itself.

## Existing probe boundary and GO/NO GO

The no-network plan command is:

```sh
PYTHONPATH=src .venv/bin/python -B -m quant.apps.dhan_candle_availability
```

`capture()` checks exact request cap, nonempty local token, dated verified
calendar and identity registry, same date/start cutoff, and empty private
output before constructing the HTTP transport. It skips missed slots and
never retries. Received 200 or error body bytes are quarantined before
parsing, with SHA-256, exact endpoint/payload, receipt time and status; slot
metadata retains HTTP Date, UTC request bounds and monotonic elapsed time.
Network failure with no response body is recorded as such. The quarantine
sidecar does not retain all HTTP headers. Independent NTP measurements must
be retained separately and are not checked by `capture()`.

**NO GO** if any dated source, corporate-action review, fresh Dhan mapping,
successful bounded clock sample, active Data API entitlement, unexpired
private token, fresh empty output directory, or separate exact authorization
is absent. Current `capture()` guards only some of these conditions; its
`--approved-scope` flag is an operator assertion, not evidence. Do not run it
while external checks are missing. The proposed next-day `toDate` is an
unverified v2 behavior; if the first authorized response rejects it, preserve
the error and stop without a modified request.
