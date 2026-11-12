"""Offline software fixture checks; these rows are never market evidence."""

import json
from dataclasses import replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quant.acquisition.dhan import parse_response, run_backfill
from quant.apps.cockpit_cache import REQUEST, descriptive_replay, read_cached_reliance


def test_cached_history_requires_matching_archived_hash(tmp_path: Path) -> None:
    stamps = [
        int(
            datetime(
                2026, 10, day, 9, minute, tzinfo=ZoneInfo("Asia/Kolkata")
            ).timestamp()
        )
        for day, minute in ((15, 15), (15, 17), (16, 15))
    ]
    raw = json.dumps(
        {
            "timestamp": stamps,
            "open": [100, 101, 102],
            "high": [102, 103, 104],
            "low": [99, 100, 101],
            "close": [101, 102, 103],
            "volume": [10, 12, 13],
        }
    ).encode()

    class FixtureTransport:
        def post_json(self, url: str, payload: dict[str, object], token: str) -> bytes:
            return raw

    path = run_backfill(
        (REQUEST,),
        output_dir=tmp_path / "private",
        enabled=True,
        transport=FixtureTransport(),
        token="fixture-only",
        max_retries=0,
        sleep=lambda _: None,
    )[0]
    result = read_cached_reliance(path, sha256(raw).hexdigest())
    assert result["coverage"] == {"2026-10-15": 2, "2026-10-16": 1}
    assert result["source_timestamp_internal_gaps"] == {
        "2026-10-15": 1,
        "2026-10-16": 0,
    }
    assert result["causal_eligibility"] == "BLOCKED"
    replay = result["replay"]
    assert replay["processed_bars"] == replay["no_trade_evaluations"] == 3
    assert replay["missing_source_minutes"] == 1
    assert "SOURCE_MINUTES_MISSING" in replay["reasons"]
    assert replay["paper_orders"] == replay["qp01_records_written"] == 0
    assert replay["latest_retrospective_baseline"] is None
    with pytest.raises(ValueError, match="source integrity"):
        read_cached_reliance(path, "0" * 64)


def test_descriptive_replay_is_past_ordered_and_never_paper_eligible() -> None:
    start = int(
        datetime(2026, 10, 15, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()
    )
    raw = json.dumps(
        {
            "timestamp": [start + 60 * i for i in range(22)],
            "open": [100 + i for i in range(22)],
            "high": [101 + i for i in range(22)],
            "low": [99 + i for i in range(22)],
            "close": [100 + i for i in range(22)],
            "volume": [10 + i for i in range(22)],
        }
    ).encode()
    digest = sha256(raw).hexdigest()
    bars = tuple(parse_response(raw, REQUEST))
    replay = descriptive_replay(bars, digest)
    assert replay["retrospective_baseline_samples"] == 2
    assert replay["decision"] == "NO_TRADE"
    assert replay["outcomes"] == "WITHHELD_UNVERIFIED_COMPLETION"
    assert replay["paper_eligible"] == replay["paper_orders"] == 0
    assert replay["broker_orders"] == "DISABLED"
    first_ready = descriptive_replay(bars[:21], digest)["latest_retrospective_baseline"]
    assert first_ready == {
        "source_timestamp_ns": str((start + 20 * 60) * 1_000_000_000),
        "prior_only_5m_close_change_bps": "434.783",
        "volume_vs_prior_20m_median": "1.579",
    }
    with pytest.raises(ValueError, match="session boundary"):
        descriptive_replay(
            (replace(bars[0], source_timestamp_ns=(start - 60) * 1_000_000_000),),
            digest,
        )
    with pytest.raises(ValueError, match="cadence"):
        descriptive_replay(
            (
                bars[0],
                replace(bars[1], source_timestamp_ns=(start + 61) * 1_000_000_000),
            ),
            digest,
        )
