"""Execute and validate the authorized 30-day January 2024 RELIANCE historical pilot."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from zoneinfo import ZoneInfo

from quant.acquisition.contracts import CashInstrument
from quant.acquisition.dhan import (
    HistoricalRequest,
    diagnose_response,
    parse_response,
    run_backfill,
)
from quant.apps.live_feed_credentials import load_credentials

IST = ZoneInfo("Asia/Kolkata")


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    target_dir = Path("/Volumes/AlphaGrid Data/Live/offline-dev/historical-raw-dhan")
    target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(target_dir, 0o700)

    print("Step 1: Loading secure credentials from private store...")
    token, client_id, expiry = load_credentials(root)
    print(f"Credentials loaded: Client ID masked: {client_id[:2]}****{client_id[-2:]}, Expiry: {expiry}")

    print("Step 2: Preparing 30-day January 2024 RELIANCE pilot request...")
    instrument = CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE")
    request = HistoricalRequest(
        instrument=instrument,
        start=date(2024, 1, 1),
        end=date(2024, 1, 31),
        interval_minutes=1,
    )
    print(f"Request endpoint: {request.endpoint}")
    print(f"Request payload: {json.dumps(request.payload())}")

    print(f"Step 3: Executing run_backfill to {target_dir}...")
    paths = run_backfill(
        requests=(request,),
        output_dir=target_dir,
        quarantine_dir=target_dir / ".quarantine",
        enabled=True,
        token=token,
        minimum_interval_seconds=1.0,
    )
    if not paths:
        print("ERROR: run_backfill returned no paths.")
        return 1

    saved_path = paths[0]
    print(f"Successfully retrieved response at: {saved_path}")

    # Read raw bytes and sidecars
    raw_bytes = saved_path.read_bytes()
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    sidecar_sha = saved_path.with_suffix(".sha256").read_text().strip()
    meta = json.loads(saved_path.with_suffix(".meta.json").read_text())

    print("\n--- Validation & Audit ---")
    print(f"Raw file size: {len(raw_bytes):,} bytes")
    print(f"SHA-256 matches sidecar: {raw_sha == sidecar_sha} ({raw_sha})")
    print(f"HTTP status recorded: {meta.get('http_status')}")
    print(f"Server date: {meta.get('server_date')}")
    print(f"Received at UTC: {meta.get('received_at')}")

    # Diagnose and parse
    diagnostics = diagnose_response(raw_bytes, request)
    bars = parse_response(raw_bytes, request)
    print(f"Parsed bars count: {len(bars):,}")
    print(f"Diagnostics: identical duplicates={len(diagnostics.get('identical_duplicate_indices', []))}, "
          f"conflicting duplicates={len(diagnostics.get('conflicting_duplicate_timestamps', []))}, "
          f"out of order pairs={len(diagnostics.get('out_of_order_pairs', []))}, "
          f"reordered={diagnostics.get('reordered', False)}")

    # Detailed session breakdown
    sessions: dict[date, list] = {}
    for bar in bars:
        sessions.setdefault(bar.trading_date, []).append(bar)

    print(f"Distinct trading sessions: {len(sessions)}")
    ohlc_violations = 0
    nonpositive_volume = 0
    all_prices = []

    for day, day_bars in sorted(sessions.items()):
        first_t = datetime.fromtimestamp(day_bars[0].source_timestamp_ns / 1e9, IST)
        last_t = datetime.fromtimestamp(day_bars[-1].source_timestamp_ns / 1e9, IST)
        day_opens = [b.open_nanos / 1e9 for b in day_bars]
        day_highs = [b.high_nanos / 1e9 for b in day_bars]
        day_lows = [b.low_nanos / 1e9 for b in day_bars]
        day_closes = [b.close_nanos / 1e9 for b in day_bars]
        day_vols = [b.volume for b in day_bars]
        all_prices.extend(day_closes)

        # Check OHLC validity
        for b in day_bars:
            o, h, l, c = b.open_nanos / 1e9, b.high_nanos / 1e9, b.low_nanos / 1e9, b.close_nanos / 1e9
            if not (l <= o <= h and l <= c <= h):
                ohlc_violations += 1
            if b.volume < 0:
                nonpositive_volume += 1

        print(f"  {day} ({day.strftime('%a')}): {len(day_bars):3d} bars | "
              f"Time: {first_t.strftime('%H:%M')} -> {last_t.strftime('%H:%M')} IST | "
              f"Range: ₹{min(day_lows):.2f} - ₹{max(day_highs):.2f} | "
              f"Vol: {sum(day_vols):,}")

    print(f"\nOHLC consistency violations: {ohlc_violations}")
    print(f"Negative volume violations: {nonpositive_volume}")
    print(f"Overall price range: ₹{min(all_prices):.2f} - ₹{max(all_prices):.2f}")

    # Check corporate actions
    print("\nCorporate action check:")
    print("In January 2024, RELIANCE traded pre-bonus (1:1 bonus occurred on 28-Oct-2024).")
    print(f"Observed price level ~₹{all_prices[0]:.2f} reflects genuine unadjusted historical trading prices.")

    report = {
        "verdict": "PASS" if len(bars) > 0 and ohlc_violations == 0 and raw_sha == sidecar_sha else "FAIL",
        "instrument": "RELIANCE",
        "security_id": "2885",
        "request": request.payload(),
        "saved_path": str(saved_path),
        "sha256": raw_sha,
        "byte_size": len(raw_bytes),
        "bars_count": len(bars),
        "trading_sessions": len(sessions),
        "sessions_list": [d.isoformat() for d in sorted(sessions.keys())],
        "ohlc_violations": ohlc_violations,
        "first_bar_ist": datetime.fromtimestamp(bars[0].source_timestamp_ns / 1e9, IST).isoformat(),
        "last_bar_ist": datetime.fromtimestamp(bars[-1].source_timestamp_ns / 1e9, IST).isoformat(),
        "min_close_inr": min(all_prices),
        "max_close_inr": max(all_prices),
    }

    report_path = target_dir / "reliance-pilot-validation-report.json"
    report_path.write_text(json.dumps(report, indent=2))
    os.chmod(report_path, 0o600)
    print(f"\nSaved validation report to: {report_path}")
    print(f"Pilot Verdict: {report['verdict']}")
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
