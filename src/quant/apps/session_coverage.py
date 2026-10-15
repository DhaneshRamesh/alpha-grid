"""Inspect incremental local archive coverage without network access."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from quant.acquisition.calendar import load_calendar
from quant.acquisition.coverage import session_coverage
from quant.acquisition.registry import load_registry, load_universe


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only NSE session coverage")
    parser.add_argument("--lake-root", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw"))
    parser.add_argument("--calendar", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            session_coverage(
                lake_root=args.lake_root,
                raw_root=args.raw_root,
                calendar=load_calendar(args.calendar),
                registry=load_registry(args.registry),
                universe=load_universe(args.universe),
                start=args.start,
                end=args.end,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
