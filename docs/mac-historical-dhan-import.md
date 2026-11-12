# Mac import of the verified RELIANCE historical response

Status on 2026-10-30: the Mac checkout has no `data/historical` files. These
instructions describe an owner-approved transfer; they do not authorize or
perform one. No credentials, account cache, QP01 records, quarantine responses,
instrument-master download, or lake output are needed for this descriptive
replay.

## Exact file manifest

Copy these three original files from the Windows repository, preserving their
contents and basename, into the same relative directory on the Mac:

| Relative path under repository root | Purpose |
| --- | --- |
| `data/historical/raw-dhan-2026-10-24/2885-1-2026-10-15-2026-10-17.json` | Original Dhan chart HTTP body, reported as 35,261 bytes on Windows. Expected SHA-256: `bcace97b4dfd1b1cbfe10481b4718b770368a50c5264fb1f61a50f9c325d9861`. |
| `data/historical/raw-dhan-2026-10-24/2885-1-2026-10-15-2026-10-17.sha256` | Original checksum sidecar. Its text must equal the response SHA-256. |
| `data/historical/raw-dhan-2026-10-24/2885-1-2026-10-15-2026-10-17.meta.json` | Original `dhan-chart-raw-v1` receipt and request provenance, including timezone-aware `received_at`, endpoint, method, payload, response hash, and any normalization record. |

The response JSON has no independent instrument ID or interval field. The
original metadata binds it to the exact `NSE_EQ` / `EQUITY` / security ID
`2885` / one-minute request for `[2026-10-15, 2026-10-17)`; the reader pins its
response hash and labels the RELIANCE symbol using that request contract.
This establishes source and request integrity, not independently dated Dhan
instrument identity or candle completion semantics.

## Import and offline verification

1. On Windows, identify the three files at the paths above and record each
   file's `Get-FileHash -Algorithm SHA256` and byte length. Transfer only those
   files after approval, by an owner-selected mechanism that preserves bytes.
   Do not edit or regenerate any sidecar.

   ```powershell
   $base = 'data/historical/raw-dhan-2026-10-24/2885-1-2026-10-15-2026-10-17'
   foreach ($suffix in '.json', '.sha256', '.meta.json') {
     $file = "$base$suffix"
     Get-Item -LiteralPath $file | Select-Object FullName, Length
     Get-FileHash -LiteralPath $file -Algorithm SHA256
   }
   ```

2. On the Mac, stop and restart the already running offline cockpit after
   placing the files. From the repository root, set private POSIX permissions
   on the imported directory and three files, then compare `shasum -a 256`
   and `stat -f %z` for **each** file against the Windows inventory. The raw
   JSON must also match the pinned hash above and 35,261 bytes. Keep the
   ignored `data/historical` directory out of Git.

   ```sh
   umask 077
   chmod 700 data/historical data/historical/raw-dhan-2026-10-24
   chmod 600 data/historical/raw-dhan-2026-10-24/2885-1-2026-10-15-2026-10-17.{json,sha256,meta.json}
   shasum -a 256 data/historical/raw-dhan-2026-10-24/2885-1-2026-10-15-2026-10-17.{json,sha256,meta.json}
   stat -f '%N %z bytes' data/historical/raw-dhan-2026-10-24/2885-1-2026-10-15-2026-10-17.{json,sha256,meta.json}
   ```
3. Run the existing credential-free reader from the repository root:

   ```sh
   umask 077
   .venv/bin/python -m quant.apps.cockpit_cache > /tmp/alpha-grid-reliance-replay.json
   ```

   The command fails closed if the response, checksum or metadata is absent,
   the hash or request provenance differs, timestamps cannot be parsed, or
   the pinned archive is not the one supplied. The output is a local
   projection of the original OHLCV bars and descriptive replay, not a live
   market feed. Inspect `response_sha256`, `coverage`,
   `source_timestamp_internal_gaps`, and `replay` in the JSON before using it.
   Expected results for the pinned archive are 720 parsed rows, 360 on each
   of October 15 and 16, source minutes from 09:15 through 15:14 IST on each
   date, zero internal source-minute gaps, 720 `NO_TRADE` evaluations, and
   zero paper orders, fills, and QP01 writes. If any differ, keep historical
   data unavailable and investigate the original files.
4. Restart `tools/start-cockpit-macos.sh` normally in DATA_ONLY mode. The
   existing gateway invokes this same reader at startup and publishes the
   historical projection through its authenticated local route. No Dhan
   request is involved. The cached candles remain descriptive only and never
   become live quotes, exchange ticks, fills, or frozen November experiment
   inputs.

The October 27 HTTP 401 is a separate, previously saved Windows request. Its
error response is unnecessary for this import and has no candles. The cockpit
mentions its historical provenance only when the archive is unavailable.
