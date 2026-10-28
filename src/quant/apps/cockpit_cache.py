"""Read one previously verified Dhan archive for the local observation cockpit."""

from __future__ import annotations

import json
import os
from collections import Counter, deque
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from statistics import median_low

from quant.acquisition.calendar import IST
from quant.acquisition.contracts import CashInstrument, HistoricalBar
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
MINUTE_NS = 60_000_000_000


def descriptive_replay(
    bars: tuple[HistoricalBar, ...], source_sha256: str
) -> dict[str, object]:
    """Replay archived bars in source order; never turn OHLC into a quote or fill."""
    prior_closes: deque[int] = deque(maxlen=5)
    prior_volumes: deque[int] = deque(maxlen=20)
    previous_stamp: int | None = None
    previous_day: date | None = None
    gaps = 0
    baseline_samples = 0
    latest_baseline: dict[str, str] | None = None
    for bar in bars:
        stamp = bar.source_timestamp_ns
        if (
            stamp is None
            or stamp % 1_000_000_000
            or (previous_stamp is not None and stamp <= previous_stamp)
        ):
            raise ValueError("historical replay source order invalid")
        observed = datetime.fromtimestamp(stamp // 1_000_000_000, IST)
        if (
            observed.date() != bar.trading_date
            or not time(9, 15) <= observed.time() < time(15, 15)
            or bar.source != "DHAN_HISTORICAL"
            or bar.source_sha256 != source_sha256
            or bar.timestamp_semantics != "UNVERIFIED"
        ):
            raise ValueError("historical replay source or session boundary invalid")
        if bar.trading_date != previous_day:
            prior_closes.clear()
            prior_volumes.clear()
        elif previous_stamp is not None:
            delta = stamp - previous_stamp
            if delta % MINUTE_NS:
                raise ValueError("historical replay minute cadence invalid")
            if delta != MINUTE_NS:
                gaps += delta // MINUTE_NS - 1
                prior_closes.clear()
                prior_volumes.clear()
        if len(prior_closes) == 5 and len(prior_volumes) == 20:
            reference_volume = median_low(prior_volumes)
            if prior_closes[0] > 0 and reference_volume > 0:
                latest_baseline = {
                    "source_timestamp_ns": str(stamp),
                    "prior_only_5m_close_change_bps": str(
                        (Decimal(bar.close_nanos - prior_closes[0]) * 10_000
                         / Decimal(prior_closes[0])).quantize(Decimal("0.001"))
                    ),
                    "volume_vs_prior_20m_median": str(
                        (Decimal(bar.volume) / Decimal(reference_volume)).quantize(
                            Decimal("0.001")
                        )
                    ),
                }
                baseline_samples += 1
        prior_closes.append(bar.close_nanos)
        prior_volumes.append(bar.volume)
        previous_stamp = stamp
        previous_day = bar.trading_date
    if not bars or previous_stamp is None or previous_day is None:
        raise ValueError("historical replay has no verified source bars")
    reasons = [
        "CANDLE_COMPLETION_UNVERIFIED",
        "DATED_IDENTITY_UNVERIFIED",
        "CAS_CLASSIFICATION_UNVERIFIED",
        "NET_EDGE_UNAVAILABLE",
    ]
    if gaps:
        reasons.append("SOURCE_MINUTES_MISSING")
    return {
        "mode": "HISTORICAL_REPLAY",
        "status": "COMPLETE_NO_TRADE",
        "source_sha256": source_sha256,
        "processed_bars": len(bars),
        "total_bars": len(bars),
        "latest_session": previous_day.isoformat(),
        "latest_source_timestamp_ns": str(previous_stamp),
        "missing_source_minutes": gaps,
        "retrospective_baseline_samples": baseline_samples,
        "latest_retrospective_baseline": latest_baseline,
        "baseline_eligibility": "DESCRIPTIVE_ONLY",
        "outcomes": "WITHHELD_UNVERIFIED_COMPLETION",
        "decision": "NO_TRADE",
        "no_trade_evaluations": len(bars),
        "reasons": reasons,
        "paper_eligible": 0,
        "paper_orders": 0,
        "paper_fills": 0,
        "qp01_records_written": 0,
        "broker_orders": "DISABLED",
    }


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
        "replay": descriptive_replay(bars, audit.sha256),
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
