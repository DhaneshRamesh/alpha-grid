"""Read one previously verified Dhan archive for the local observation cockpit."""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import date
from pathlib import Path

from quant.acquisition.contracts import CashInstrument
from quant.acquisition.dhan import (
    HistoricalRequest,
    _verify_windows_private_acl,
    response_path,
    verify_saved_response,
)

ROOT = Path("data/historical/raw-dhan-2026-10-24")
REQUEST = HistoricalRequest(
    CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE"),
    date(2026, 10, 15),
    date(2026, 10, 17),
    1,
)
EXPECTED_SHA256 = "bcace97b4dfd1b1cbfe10481b4718b770368a50c5264fb1f61a50f9c325d9861"


def read_cached_reliance(path: Path, expected_sha256: str) -> dict[str, object]:
    """Fail closed on missing private evidence; never use a fixture at runtime."""
    if os.name == "nt":
        for item in (
            path.parent,
            path,
            path.with_suffix(".sha256"),
            path.with_suffix(".meta.json"),
        ):
            _verify_windows_private_acl(item)
    audit = verify_saved_response(REQUEST, path)
    if audit.sha256 != expected_sha256 or audit.retrieved_at is None:
        raise ValueError(
            "cached Dhan source integrity or retrieval provenance unavailable"
        )
    bars = audit.bars
    if not bars or len(bars) > 1_000:
        raise ValueError("cached Dhan source size unavailable")
    counts = Counter(bar.trading_date.isoformat() for bar in bars)
    if set(counts) != {"2026-10-15", "2026-10-16"}:
        raise ValueError("cached Dhan session coverage mismatch")
    gaps: dict[str, int] = {}
    for day in counts:
        stamps = [
            bar.source_timestamp_ns
            for bar in bars
            if bar.trading_date.isoformat() == day
        ]
        if any(stamp is None for stamp in stamps):
            raise ValueError("cached Dhan source timestamp unavailable")
        ordered = sorted(int(stamp) for stamp in stamps if stamp is not None)
        gaps[day] = sum(
            max(0, (later - earlier) // 60_000_000_000 - 1)
            for earlier, later in zip(ordered, ordered[1:], strict=False)
        )
    return {
        "schema_version": "dhan-historical-cache-v1",
        "status": "AVAILABLE",
        "source": "DHAN_HISTORICAL",
        "instrument": "NSE_EQ:2885:RELIANCE",
        "response_sha256": audit.sha256,
        "retrieved_at": audit.retrieved_at.isoformat(),
        "coverage": counts,
        "source_timestamp_internal_gaps": gaps,
        "unavailable_date": "2026-10-27",
        "bar_timestamp_semantics": "UNVERIFIED",
        "historical_identity": "CURRENT_MASTER_ONLY",
        "source_integrity": "VERIFIED",
        "exploratory_eligibility": "DESCRIPTIVE_ONLY",
        "causal_eligibility": "BLOCKED",
        "production_eligibility": "BLOCKED",
        "daily_status": "NO_AUTHENTIC_DAILY_RESPONSE",
        "bars": [
            {
                "timestamp_ns": str(bar.source_timestamp_ns),
                "open_nanos": str(bar.open_nanos),
                "high_nanos": str(bar.high_nanos),
                "low_nanos": str(bar.low_nanos),
                "close_nanos": str(bar.close_nanos),
                "volume": str(bar.volume),
            }
            for bar in bars
        ],
    }


def main() -> None:
    print(
        json.dumps(read_cached_reliance(response_path(REQUEST, ROOT), EXPECTED_SHA256))
    )


if __name__ == "__main__":
    main()
