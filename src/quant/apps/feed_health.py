"""Read the collector's latest health snapshot without contacting a broker."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quant.domain import FeedStatus
from quant.health.monitor import HealthThresholds


def main() -> None:
    parser = argparse.ArgumentParser(description="Read NSE feed quality status")
    parser.add_argument("--instrument", help="symbol or security ID")
    args = parser.parse_args()
    path = Path(os.environ.get("QUANT_HEALTH_STATUS_PATH", "data/feed_health.json"))
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("instruments"), list):
            raise ValueError("invalid health status document")
        observed = datetime.fromisoformat(data["observed_at"])
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("health status timestamp lacks timezone")
        if data.get("status") not in {status.value for status in FeedStatus}:
            raise ValueError("invalid health status state")
        max_age = HealthThresholds.from_env(os.environ).status_max_age_seconds
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"health status unavailable: {exc}\n")
    age = (datetime.now(UTC) - observed).total_seconds()
    stale_file = age > max_age or age < -max_age
    data["status_file_stale"] = stale_file
    data["status_file_age_seconds"] = round(age, 3)
    if args.instrument:
        selected = [
            item
            for item in data["instruments"]
            if item["symbol"] == args.instrument
            or item["instrument_id"] == args.instrument
        ]
        if not selected:
            parser.exit(2, f"instrument not found: {args.instrument}\n")
        data["instruments"] = selected
        data["missing_instruments"] = [
            item["symbol"] for item in selected if item["missing"]
        ]
        data["overall_status"] = data["status"]
        rank = {"healthy": 0, "degraded": 1, "stale": 2, "disconnected": 3}
        data["status"] = max(
            (item["status"] for item in selected), key=rank.__getitem__
        )
    if stale_file:
        data["status"] = FeedStatus.DISCONNECTED.value
    print(json.dumps(data, sort_keys=True))
    if (
        data["status"] == FeedStatus.DISCONNECTED.value
        or data["status"] == FeedStatus.STALE.value
    ):
        raise SystemExit(2)
    if data["status"] == FeedStatus.DEGRADED.value:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
