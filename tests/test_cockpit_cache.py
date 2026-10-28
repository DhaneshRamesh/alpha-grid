"""Offline software fixture checks; these rows are never market evidence."""

import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quant.acquisition.dhan import run_backfill
from quant.apps.cockpit_cache import REQUEST, read_cached_reliance


def test_cached_history_requires_matching_archived_hash(tmp_path: Path) -> None:
    stamps = [
        int(
            datetime(2026, 10, day, 9, minute, tzinfo=ZoneInfo("Asia/Kolkata"))
            .timestamp()
        )
        for day, minute in ((15, 15), (15, 17), (16, 15))
    ]
    raw = json.dumps({
        "timestamp": stamps,
        "open": [100, 101, 102], "high": [102, 103, 104],
        "low": [99, 100, 101], "close": [101, 102, 103], "volume": [10, 12, 13],
    }).encode()

    class FixtureTransport:
        def post_json(self, url: str, payload: dict[str, object], token: str) -> bytes:
            return raw

    path = run_backfill(
        (REQUEST,), output_dir=tmp_path / "private", enabled=True,
        transport=FixtureTransport(), token="fixture-only", max_retries=0,
        sleep=lambda _: None,
    )[0]
    result = read_cached_reliance(path, sha256(raw).hexdigest())
    assert result["coverage"] == {"2026-10-15": 2, "2026-10-16": 1}
    assert result["source_timestamp_internal_gaps"] == {
        "2026-10-15": 1, "2026-10-16": 0,
    }
    assert result["causal_eligibility"] == "BLOCKED"
    with pytest.raises(ValueError, match="source integrity"):
        read_cached_reliance(path, "0" * 64)
