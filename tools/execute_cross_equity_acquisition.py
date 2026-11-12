"""Cross-equity historical acquisition and validation for the approved 25 NSE cash universe."""

from __future__ import annotations

from datetime import date
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from quant.acquisition.contracts import CashInstrument
from quant.acquisition.dhan import (
    HistoricalRequest,
    HistoricalTransport,
    _write_private_bytes,
    plan_requests,
)
from quant.apps.live_feed_credentials import load_credentials
from tools.execute_full_3yr_acquisition import run_acquisition

def build_manifest(instruments: list[dict], start: date, end: date) -> dict[str, Any]:
    inst_objs = tuple(CashInstrument("NSE", "NSE_EQ", i["security_id"], i["symbol"]) for i in instruments)
    requests = plan_requests(inst_objs, start, end, interval_minutes=1)
    
    windows = []
    for r in requests:
        windows.append({
            "security_id": r.instrument.security_id,
            "symbol": r.instrument.symbol,
            "venue": r.instrument.venue,
            "segment": r.instrument.segment,
            "start": r.start.isoformat(),
            "end": r.end.isoformat(),
            "interval_minutes": r.interval_minutes,
            "endpoint": r.endpoint,
            "payload": r.payload(),
            "days": (r.end - r.start).days,
        })

    return {
        "schema_version": "alpha-grid-historical-manifest-v1",
        "universe_version": "alpha-grid-cross-stock-universe-v1",
        "universe_size": len(instruments),
        "target_start": start.isoformat(),
        "target_end": end.isoformat(),
        "total_planned_requests": len(requests),
        "requests_per_instrument": len(requests) // len(instruments),
        "windows": windows,
        "selection_bias_note": (
            "The 25 equities were selected in November 2026 based on market capitalization "
            "and liquidity. Using them retrospectively introduces survivorship and lookback selection bias. "
            "They must NOT be classified as an unbiased historical universe."
        ),
    }


def execute_batch(
    requests: list[HistoricalRequest],
    output_dir: Path,
    token: str,
    checkpoint_file: Path,
    delay_seconds: float = 1.5,
    max_requests: int | None = None,
    transport: HistoricalTransport | None = None,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """Use the same audited resume, request budget, and raw verification as 3-year runs."""
    if not requests:
        return {"executed_this_run": 0, "attempted_this_run": 0, "total_completed": 0, "results": [], "status": "COMPLETE"}
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(output_dir, 0o700)
    windows = [{
        "security_id": req.instrument.security_id,
        "symbol": req.instrument.symbol,
        "venue": req.instrument.venue,
        "segment": req.instrument.segment,
        "start": req.start.isoformat(),
        "end": req.end.isoformat(),
        "interval_minutes": req.interval_minutes,
        "endpoint": req.endpoint,
        "payload": req.payload(),
    } for req in requests]
    manifest = {
        "schema_version": "alpha-grid-historical-manifest-v1",
        "universe_size": len({req.instrument.security_id for req in requests}),
        "total_planned_requests": len(windows),
        "windows": windows,
    }
    encoded = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
    manifest_file = output_dir / f"batch-manifest-{hashlib.sha256(encoded).hexdigest()[:16]}.json"
    if manifest_file.exists():
        if manifest_file.read_bytes() != encoded:
            raise ValueError("saved batch manifest differs from requested scope")
    else:
        _write_private_bytes(manifest_file, encoded)
    summary = run_acquisition(
        output_dir, manifest_path=manifest_file, checkpoint_path=checkpoint_file,
        token=token, transport=transport, max_requests=max_requests,
        delay_seconds=delay_seconds, sleep=sleep,
    )
    checkpoint = json.loads(checkpoint_file.read_text())
    results = [checkpoint.get(
        f"{req.instrument.security_id}-1-{req.start}-{req.end}",
        {"symbol": req.instrument.symbol, "security_id": req.instrument.security_id,
         "status": "PENDING", "listed": False},
    ) for req in requests]
    for item in results:
        item["listed"] = item.get("bars_count", 0) > 0 and item.get("status") == "COMPLETED"
    return {
        "executed_this_run": summary["executed"],
        "attempted_this_run": summary["attempted"],
        "total_completed": summary["total_completed"],
        "results": results,
        "status": summary["status"],
    }


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    volume = Path("/Volumes/AlphaGrid Data")
    if not volume.is_mount():
        print("FAIL-CLOSED: Encrypted Alpha Grid data volume is not mounted.")
        return 1
    target_dir = volume / "Live/offline-dev/historical-raw-dhan"
    target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(target_dir, 0o700)
    checkpoint_file = target_dir / "acquisition-checkpoint.json"

    token, client_id, expiry = load_credentials(root)
    print(f"Loaded credentials for client: {client_id[:2]}****{client_id[-2:]}")

    # Load 25-stock universe
    universe_path = root / "docs/cross-stock-universe-2026-11-02.json"
    universe = json.loads(universe_path.read_text())["instruments"]

    # Manifest for 3-year history: Nov 1, 2023 to Nov 1, 2026
    start_date = date(2023, 11, 1)
    end_date = date(2026, 11, 1)
    manifest = build_manifest(universe, start_date, end_date)
    manifest_path = target_dir / "historical-manifest-2023-2026.json"
    if manifest_path.exists():
        if json.loads(manifest_path.read_text()) != manifest:
            raise ValueError("existing full acquisition manifest differs; refusing overwrite")
    else:
        _write_private_bytes(manifest_path, (json.dumps(manifest, indent=2) + "\n").encode())
    print(f"Saved full acquisition manifest to: {manifest_path}")
    print(f"Total planned windows across 25 stocks: {manifest['total_planned_requests']}")

    # Mode 1: First execute the January 2024 pilot expansion across all 24 remaining equities
    print("\n--- Step 5 Phase A: January 2024 30-Day Expansion for Remaining 24 Equities ---")
    pilot_requests = [
        HistoricalRequest(
            CashInstrument("NSE", "NSE_EQ", i["security_id"], i["symbol"]),
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
            interval_minutes=1,
        )
        for i in universe
    ]
    pilot_res = execute_batch(
        pilot_requests,
        target_dir,
        token,
        checkpoint_file,
        delay_seconds=1.5,
    )
    print(f"\nPhase A Completed: {pilot_res['executed_this_run']} new requests executed.")

    # Audit Phase A
    coverage = {}
    for r in pilot_res["results"]:
        sym = r["symbol"]
        coverage[sym] = {
            "security_id": r["security_id"],
            "bars": r.get("bars_count", 0),
            "sha256": r.get("sha256", "N/A"),
            "status": r.get("status"),
            "listed": r.get("listed", False),
        }

    cov_path = target_dir / "jan2024-universe-coverage-report.json"
    cov_path.write_text(json.dumps(coverage, indent=2))
    os.chmod(cov_path, 0o600)
    print(f"Saved January 2024 universe coverage report to: {cov_path}")

    # Summary table
    print("\n--- January 2024 Universe Coverage Summary ---")
    listed_count = sum(1 for v in coverage.values() if v["listed"])
    unlisted_count = sum(1 for v in coverage.values() if not v["listed"])
    total_bars = sum(v["bars"] for v in coverage.values())
    print(f"Listed instruments active in Jan 2024: {listed_count} / {len(universe)}")
    print(f"Unlisted / zero-bar instruments: {unlisted_count} (e.g. MONEYVIEW)")
    print(f"Total verified 1-minute candles captured: {total_bars:,}")

    return 0 if pilot_res["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    sys.exit(main())
