"""Generate offline forward candle observations from a verified local lake."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from quant.acquisition.calendar import load_calendar
from quant.acquisition.forward_observations import TimingEvidence, generate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--calendar", type=Path, required=True)
    parser.add_argument("--timing-evidence", type=Path, required=True)
    parser.add_argument("--as-of-ns", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    evidence = TimingEvidence(**json.loads(args.timing_evidence.read_text()))
    report = generate(
        args.lake,
        args.raw_root,
        args.output,
        load_calendar(args.calendar),
        evidence,
        as_of_ns=args.as_of_ns,
    )
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
