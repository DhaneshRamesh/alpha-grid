"""Read one previously verified Dhan archive for the local observation cockpit."""

from __future__ import annotations

import json
import os
import stat
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
MAC_ROOT = Path("data/historical/raw-dhan-macos-2026-11-01")
MAC_REQUEST = HistoricalRequest(
    CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE"),
    date(2026, 10, 26),
    date(2026, 10, 31),
    1,
)
MAC_EXPECTED_SHA256 = "a4c61e3d5ad8ce3b9bf78317ac73ff15502bcefa52bd6a09c3771b866d2d8653"
MAC_COVERAGE = {f"2026-10-{day}": 360 for day in range(26, 31)}
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
                        (
                            Decimal(bar.close_nanos - prior_closes[0])
                            * 10_000
                            / Decimal(prior_closes[0])
                        ).quantize(Decimal("0.001"))
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


def read_cached_reliance(
    path: Path,
    expected_sha256: str,
    *,
    request: HistoricalRequest = REQUEST,
    expected_coverage: dict[str, int] | None = None,
    unavailable_date: str | None = "2026-10-27",
) -> dict[str, object]:
    """Fail closed on missing private evidence; never use a fixture at runtime."""
    if os.name == "nt":
        for item in (
            path.parent,
            path,
            path.with_suffix(".sha256"),
            path.with_suffix(".meta.json"),
        ):
            _verify_windows_private_acl(item)
    else:
        for item in (
            path.parent,
            path,
            path.with_suffix(".sha256"),
            path.with_suffix(".meta.json"),
        ):
            info = item.lstat()
            expected_type = stat.S_ISDIR if item == path.parent else stat.S_ISREG
            if (
                not expected_type(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
            ):
                raise PermissionError("cached Dhan source is not owner-private")
    audit = verify_saved_response(request, path)
    if audit.sha256 != expected_sha256 or audit.retrieved_at is None:
        raise ValueError(
            "cached Dhan source integrity or retrieval provenance unavailable"
        )
    bars = audit.bars
    if not bars or len(bars) > 2_000:
        raise ValueError("cached Dhan source size unavailable")
    counts = Counter(bar.trading_date.isoformat() for bar in bars)
    if expected_coverage is None:
        coverage_ok = set(counts) == {"2026-10-15", "2026-10-16"}
    else:
        coverage_ok = dict(counts) == expected_coverage
    if not coverage_ok:
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
    simulation: dict[str, object] | None = None
    if audit.sha256 == MAC_EXPECTED_SHA256:
        from quant.apps.historical_simulator import UNIVERSE_PATH, simulate

        universe = json.loads(UNIVERSE_PATH.read_text())
        simulation = simulate({"RELIANCE": bars}, universe)
    return {
        "schema_version": "dhan-historical-cache-v1",
        "status": "AVAILABLE",
        "source": "DHAN_HISTORICAL",
        "instrument": "NSE_EQ:2885:RELIANCE",
        "response_sha256": audit.sha256,
        "retrieved_at": audit.retrieved_at.isoformat(),
        "coverage": counts,
        "source_timestamp_internal_gaps": gaps,
        **({"unavailable_date": unavailable_date} if unavailable_date else {}),
        "bar_timestamp_semantics": "UNVERIFIED",
        "historical_identity": "CURRENT_MASTER_ONLY",
        "source_integrity": "VERIFIED",
        "exploratory_eligibility": "DESCRIPTIVE_ONLY",
        "causal_eligibility": "BLOCKED",
        "production_eligibility": "BLOCKED",
        "daily_status": "NO_AUTHENTIC_DAILY_RESPONSE",
        "replay": descriptive_replay(bars, audit.sha256),
        **({"simulation": simulation} if simulation is not None else {}),
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
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--cross-stock-symbol")
    parser.add_argument("--private-dir", type=Path)
    args = parser.parse_args()
    if args.cross_stock_symbol:
        if args.private_dir is None:
            raise ValueError("private archive directory required")
        from quant.acquisition.contracts import CashInstrument
        from collections import Counter

        manifest = json.loads(Path("docs/cross-stock-universe-2026-11-02.json").read_text())
        rows = [row for row in manifest["instruments"] if row["symbol"] == args.cross_stock_symbol]
        if len(rows) != 1:
            raise ValueError("symbol outside frozen universe")
        row = rows[0]
        if row["symbol"] == "RELIANCE":
            # The frozen universe reuses the pinned response; no duplicate
            # RELIANCE file was written to the 24-response acquisition folder.
            pinned = response_path(MAC_REQUEST, MAC_ROOT)
            print(json.dumps(read_cached_reliance(
                pinned, MAC_EXPECTED_SHA256, request=MAC_REQUEST,
                expected_coverage=MAC_COVERAGE, unavailable_date=None,
            )))
            return
        request = HistoricalRequest(
            CashInstrument("NSE", "NSE_EQ", row["security_id"], row["symbol"], row["isin"]),
            date(2026, 10, 26), date(2026, 10, 31), 1,
        )
        path = response_path(request, args.private_dir / "historical-25-oct26-30")
        for item in (path.parent, path, path.with_suffix(".sha256"), path.with_suffix(".meta.json")):
            info = item.lstat()
            expected = stat.S_ISDIR if item == path.parent else stat.S_ISREG
            if not expected(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise PermissionError("cross-stock archive storage not owner-private")
        audit = verify_saved_response(request, path)
        if audit.retrieved_at is None or not audit.bars or len(audit.bars) > 2_000:
            raise ValueError("verified cross-stock archive unavailable")
        bars = audit.bars
        coverage = dict(Counter(bar.trading_date.isoformat() for bar in bars))
        print(json.dumps({
            "schema_version": "dhan-historical-cache-v1", "status": "AVAILABLE",
            "source": "DHAN_HISTORICAL", "instrument": f"NSE_EQ:{row['security_id']}:{row['symbol']}",
            "response_sha256": audit.sha256, "retrieved_at": audit.retrieved_at.isoformat(),
            "coverage": coverage, "source_integrity": "VERIFIED",
            "exploratory_eligibility": "DESCRIPTIVE_ONLY", "causal_eligibility": "BLOCKED",
            "bar_timestamp_semantics": "UNVERIFIED", "historical_identity": "CURRENT_MASTER_ONLY",
            "bars": [{"timestamp_ns": str(bar.source_timestamp_ns), "open_nanos": str(bar.open_nanos),
                      "high_nanos": str(bar.high_nanos), "low_nanos": str(bar.low_nanos),
                      "close_nanos": str(bar.close_nanos), "volume": str(bar.volume)} for bar in bars],
        }))
        return
    mac_path = response_path(MAC_REQUEST, MAC_ROOT)
    if mac_path.exists():
        cache = read_cached_reliance(
            mac_path,
            MAC_EXPECTED_SHA256,
            request=MAC_REQUEST,
            expected_coverage=MAC_COVERAGE,
            unavailable_date=None,
        )
    else:
        cache = read_cached_reliance(response_path(REQUEST, ROOT), EXPECTED_SHA256)
    print(json.dumps(cache))


if __name__ == "__main__":
    main()
