# November 2 live observation pilot — offline preflight at 11:39 IST

**NO GO. No authenticated Dhan socket, historical API request, subscription,
paper intent or broker order was made.** The proposed capture scope is exactly
HDFCBANK `NSE_EQ/1333`, RELIANCE `NSE_EQ/2885`, INFY `NSE_EQ/1594`, at most
one standard Quote-17 socket and one 20-level Depth-23 socket, zero retries,
and hard stop by 14:45 IST. Separate execution approval was granted after
this initial preflight; it does not override failed evidence gates.

## Dated source and host checks

- [NSE's 2026 holiday and equity-hours page](https://www.nseindia.com/resources/exchange-communication-holidays)
  does not list Monday November 2 as an equity holiday; ordinary cash hours
  are 09:15–15:30. That does not replace a same-day notice/session check.
- The October 30 original NSE MII security file and October bhavcopies in the
  frozen universe establish earlier IDs/ISINs and liquidity only. The required
  November 2 public NSE MII file
  `https://nsearchives.nseindia.com/content/cm/NSE_CM_security_02112026.csv.gz`
  was absent locally and returned **HTTP 404** at preflight. Its publication
  time is unknown. Today's `FinInstrmId`, EQ series, normal-market and CAS
  flags, and same-day corporate-action applicability remain unverified. A
  search result is not proof of absence of corporate actions. NSE's current
  corporate-action display lists INFY's ₹26 dividend ex-date as November 3;
  this is not a November 2 identity/CAS file.
- The existing `data/config/calendar.json` remains the October 12–16
  RELIANCE CAS research calendar and has no November 2 observation evidence.
  The existing quote preflight therefore cannot certify today's session.
- Fresh public [Dhan instrument master](https://images.dhan.co/api-data/api-scrip-master.csv)
  original bytes are retained at `data/instruments/dhan-scrip-master-2026-11-02.csv`
  with retrieval manifest. UTC retrieval was `2026-11-02T06:04:10.939451+00:00`,
  size 25,854,564 bytes, SHA-256
  `83c59ef4b2a6234cdda916f0955a9cd7f374989236bb24a5ffc37e5587bd0acd`.
  The existing verifier uniquely confirms NSE/E/EQUITY/EQ IDs 1333, 2885 and
  1594 for the three symbols. This is current Dhan mapping, not dated NSE
  evidence or proof of historical continuity.
- The Mac had about 12.1 GiB free at 11:39 IST, above the earlier pilot's
  proposed 5 GiB preflight threshold. Private token storage and credential
  variable names exist; token contents were not read or printed. Its local
  metadata reports expiry 19:28 IST, but server validity and Data API/depth
  entitlement were not tested and remain operator-confirmation gates.
- Independent `sntp` observations were Apple `+20.398 ±84.469 ms` and Google
  `+20.720 ±41.837 ms`. The Apple `|offset|+uncertainty=104.867 ms` exceeds
  the existing 100 ms criterion; Google gives 62.557 ms. Capture needs a fresh,
  passing, consistently bounded pre/post bracket and step monitoring.

## Offline code corrections and evidence

The standard collector already persists scoped original Quote-17 binary bytes
and SHA-256 in QJ01 before decode; it is still RELIANCE-only and Windows-clock
gated. The depth collector previously fsynced only decoded sides/books. It
now appends a `RawDepthFrame` QJ01 variant **after** existing Postcard tags,
fsyncs exact binary bytes with SHA-256, UTC receive timestamp, feed session
and frame ID before decoding, and verifies the hash on replay. Malformed and
unsupported frames remain raw evidence and stop capture. Unknown IDs stop
after raw retention. The existing segmented byte/disk bounds remain; the live
depth CLI now defaults to zero reconnects and requires an explicit UTC cutoff.

Depth bid and ask packets are independently retained. A derived book is
emitted only for the same ID/session with receipt skew at most one second,
positive displayed price/quantity at all 20 levels and `best bid < best ask`.
Stale, incomplete and crossed combinations are counted and excluded. This
pairing is a local receipt-time approximation, not an atomic exchange book;
the depth wire format has no reliable exchange timestamp/loss sequence.
No quote-to-book cross-socket alignment or spread/liquidity measurement from
genuine frames occurred because there are no live frames.

Focused offline tests passed: Dhan 20-depth decoder 7/7, QJ01 13/13 with one
helper ignored, depth capture 6/6, replay 2/2; Rust workspace and live-feature
compile checks passed. No authenticated socket was opened in these tests.

## Initial remaining gates at 11:39 IST

1. Obtain and preserve **today's** original NSE identity/CAS and corporate-action
   evidence, with publication/retrieval time. Do not backdate an EOD file or
   use October 30 as November 2 identity.
2. Finish a two-instrument-scope correction to the standard Quote-17 collector:
   its current one-ID subscription cannot carry all three instruments in the
   permitted single socket. Bind both collectors to the same exact preflighted
   three-ID allowlist; enforce a common 14:45 hard stop, a fresh Mac clock
   bracket and an all-socket abort on any failed integrity condition.
3. Confirm the exact read-only Data API and 20-depth entitlement with the
   operator, validate private credential availability without disclosure,
   recheck free space, then present the two-socket subscription and capture
   locations for separate approval. If the prerequisites do not complete by
   14:45 IST, today's pilot expires; do not silently move it to another date.

The existing simulator, QP01/risk limits and frozen November experiment are
unchanged. DATA_ONLY / NO TRADE remains mandatory.

## Authorized-scope recheck at 13:05 IST

The operator approved the same November 2 three-ID, two-socket, zero-retry,
14:45 read-only scope. The original NSE file was checked again and still
returned HTTP 404; no dated exchange ID/CAS evidence or November 2 calendar
record exists locally. A complete invocation of the existing Python preflight
with `--pilot-three`, today's fresh Dhan master, today's required NSE paths and
the local calendar returned `{"status":"BLOCKED"}` before journal creation.
No authenticated call or socket was attempted.

The existing standard collector has now been scoped to exactly the approved
three IDs in one Quote-17 subscription, with original-frame QJ01 retention,
Mac Python fallback, and a session-start-derived 14:45 cutoff. Its Mac clock
gate parses `sntp time.google.com` and retains the **100 ms**
`|offset| + uncertainty` limit. Two fresh Google samples measured
`+10.450 ±52.030 ms` and `+12.440 ±53.982 ms`, both within that limit.
Apple and Cloudflare probes had uncertainty greater than the accepted bound
and were not used as passing measurements. Capture still requires a fresh
pre/post clock bracket. The depth collector's deadline is explicit and
zero-retry, but cross-process teardown and common dated evidence were not
validated for a live run.

Focused offline checks after the standard collector change: Python quote
preflight 2/2; standard capture 4/4; exact three-ID subscription 1/1; local
telemetry bin 8/8; Rust workspace compiled. These software fixtures are not
market evidence. The unverified NSE/CAS/session and corporate-action gates
make the authorized pilot **NO GO**, irrespective of remaining time before
14:45. No date shift or scope expansion is authorized.
