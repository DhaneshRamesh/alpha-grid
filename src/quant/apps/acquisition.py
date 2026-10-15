"""Offline acquisition planning CLI. No network without explicit opt-in code path."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from quant.acquisition.contracts import CashInstrument
from quant.acquisition.dhan import dry_run, plan_requests


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline Dhan historical request plan")
    parser.add_argument("--security-id", action="append", required=True)
    parser.add_argument("--symbol", action="append", required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--interval", type=int, default=1, help="minute interval")
    parser.add_argument("--daily", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("data/historical/raw"))
    args = parser.parse_args()
    if len(args.security_id) != len(args.symbol):
        parser.error("one --symbol is required per --security-id")
    universe = tuple(
        CashInstrument("NSE", "NSE_EQ", sid, symbol)
        for sid, symbol in zip(args.security_id, args.symbol, strict=True)
    )
    requests = plan_requests(
        universe, args.start, args.end, None if args.daily else args.interval
    )
    print(json.dumps(dry_run(requests, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
