"""Offline pilot disk arithmetic; all rate inputs must be explicit assumptions."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from quant.acquisition.capture_plan import estimate_capacity


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline DATA_ONLY capture sizing")
    parser.add_argument("--instruments", type=int, required=True)
    parser.add_argument("--assumed-peak-rate-per-instrument", type=float, required=True)
    parser.add_argument("--assumed-bytes-per-event", type=int, required=True)
    parser.add_argument("--session-hours", type=float, default=6.25)
    parser.add_argument("--retention-sessions", type=int, default=20)
    parser.add_argument("--copies", type=int, default=2)
    args = parser.parse_args()
    result = estimate_capacity(
        instruments=args.instruments,
        peak_events_per_second_per_instrument=args.assumed_peak_rate_per_instrument,
        bytes_per_event=args.assumed_bytes_per_event,
        session_hours=args.session_hours,
        retention_sessions=args.retention_sessions,
        copies=args.copies,
    )
    print(json.dumps(asdict(result), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
