"""Execute and validate the full three-year, 25-stock, 1-minute historical acquisition."""

from __future__ import annotations

from datetime import UTC, date, datetime, time as dt_time, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
from typing import Any
import urllib.error
import uuid
from zoneinfo import ZoneInfo

from quant.acquisition.contracts import CashInstrument
from quant.acquisition.dhan import (
    HistoricalRequest,
    HistoricalTransport,
    diagnose_response,
    parse_response,
    plan_requests,
    response_path,
    run_backfill,
    _write_private_bytes,
    _write_private_json,
)
from quant.apps.live_feed_credentials import load_credentials

IST = ZoneInfo("Asia/Kolkata")
MIN_DISK_HEADROOM_BYTES = 10 * 1024 * 1024 * 1024  # 10 GiB guard

# Authoritative NSE trading holidays (weekdays closed)
NSE_TRADING_HOLIDAYS: frozenset[str] = frozenset({
    # 2023 (post-Nov 1)
    "2023-11-14",  # Diwali Balipratipada
    "2023-11-27",  # Guru Nanak Jayanti
    "2023-12-25",  # Christmas
    # 2024
    "2024-01-22",  # Ayodhya Ram Mandir Consecration (Declared exchange holiday)
    "2024-01-26",  # Republic Day
    "2024-03-08",  # Mahashivratri
    "2024-03-25",  # Holi
    "2024-03-29",  # Good Friday
    "2024-04-11",  # Id-Ul-Fitr
    "2024-04-17",  # Ram Navami
    "2024-05-01",  # Maharashtra Day
    "2024-05-20",  # Lok Sabha Elections (Mumbai)
    "2024-06-17",  # Bakri Id
    "2024-07-17",  # Moharram
    "2024-08-15",  # Independence Day
    "2024-10-02",  # Mahatma Gandhi Jayanti
    "2024-11-01",  # Diwali Laxmi Pujan (Day session closed; evening Muhurat only)
    "2024-11-15",  # Guru Nanak Jayanti
    "2024-11-20",  # Maharashtra Assembly Election
    "2024-12-25",  # Christmas
    # 2025
    "2025-02-26",  # Mahashivratri
    "2025-03-14",  # Holi
    "2025-03-31",  # Id-Ul-Fitr
    "2025-04-10",  # Mahavir Jayanti
    "2025-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2025-04-18",  # Good Friday
    "2025-05-01",  # Maharashtra Day
    "2025-06-07",  # Bakri Id
    "2025-08-15",  # Independence Day
    "2025-08-27",  # Ganesh Chaturthi
    "2025-10-02",  # Mahatma Gandhi Jayanti
    "2025-10-21",  # Diwali Laxmi Pujan (Day session closed; evening Muhurat only)
    "2025-10-22",  # Diwali Balipratipada
    "2025-11-05",  # Guru Nanak Jayanti
    "2025-12-25",  # Christmas
    # 2026
    "2026-01-26",  # Republic Day
    "2026-03-03",  # Holi
    "2026-03-20",  # Id-ul-Fitr
    "2026-03-27",  # Ram Navami
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Ambedkar Jayanti
    "2026-05-01",  # Maharashtra Day
    "2026-05-28",  # Bakri Id
    "2026-06-26",  # Moharram
    "2026-10-02",  # Mahatma Gandhi Jayanti
    "2026-10-20",  # Dussehra
    "2026-11-10",  # Diwali Balipratipada
    "2026-11-24",  # Guru Nanak Jayanti
    "2026-12-25",  # Christmas
})

# NSE Special Trading Sessions (weekend or non-standard hours)
NSE_SPECIAL_SESSIONS: dict[str, dict[str, Any]] = {
    "2023-11-12": {"kind": "DIWALI_MUHURAT", "expected_bars": 60, "desc": "Diwali Muhurat Trading (18:15-19:15 IST)"},
    "2024-01-20": {"kind": "SATURDAY_DISASTER_RECOVERY", "expected_bars": 105, "desc": "Saturday DR Live Trading (2 mini sessions)"},
    "2024-05-18": {"kind": "SATURDAY_DISASTER_RECOVERY", "expected_bars": 120, "desc": "Saturday DR Live Trading (2 mini sessions)"},
    "2024-11-01": {"kind": "DIWALI_MUHURAT", "expected_bars": 60, "desc": "Diwali Muhurat Trading (18:00-19:00 IST)"},
    "2025-10-21": {"kind": "DIWALI_MUHURAT", "expected_bars": 60, "desc": "Diwali Muhurat Trading (evening)"},
}

# Date- and instrument-specific CAS Phase 1 continuous trading definitions:
# Evidenced in data/config/calendar.json: Phase 1 applies to cash stocks with derivative contracts.
# RELIANCE is verified for CAS Phase 1 on October 15 and 16, 2026.
# For verified CAS instruments on evidenced dates, continuous trading ends at 15:15 IST (360 continuous bars);
# orders from 15:15 to 15:35 IST enter the separate Closing Auction Session.
# For all non-CAS equities or unevidenced dates, continuous trading ends at 15:30 IST.
CAS_VERIFIED_INSTRUMENTS: dict[str, set[str]] = {
    "RELIANCE": {"2026-10-15", "2026-10-16"},
}

# Permitted exchange operational session time windows:
# Day session: 09:00 to 16:00 (Pre-open 09:00-09:08 discovery, continuous 09:15-15:30, closing auction/post-close up to 16:00)
# Evening session: 17:30 to 20:00 (Diwali Muhurat trading)
DAY_SESSION_START = dt_time(9, 0)
DAY_SESSION_END = dt_time(16, 0)
EVENING_SESSION_START = dt_time(17, 30)
EVENING_SESSION_END = dt_time(20, 0)
CORPORATE_ACTIONS: dict[str, list[dict[str, Any]]] = {
    "RELIANCE": [
        {"type": "BONUS", "ratio": "1:1", "factor": 2.0, "ex_date": "2024-10-28", "intraday_back_adjusted": True}
    ],
    "HDFCBANK": [
        {"type": "BONUS", "ratio": "1:1", "factor": 2.0, "ex_date": "2025-08-26", "intraday_back_adjusted": True}
    ],
    "BAJFINANCE": [
        {"type": "SPLIT_AND_BONUS", "ratio": "2:1_SPLIT_4:1_BONUS", "factor": 10.0, "ex_date": "2025-06-16", "intraday_back_adjusted": True}
    ],
    "BSE": [
        {"type": "BONUS", "ratio": "2:1", "factor": 3.0, "ex_date": "2025-05-23", "intraday_back_adjusted": True}
    ],
    "KOTAKBANK": [
        {"type": "SPLIT", "ratio": "1:5", "factor": 5.0, "ex_date": "2026-01-14", "intraday_back_adjusted": True}
    ],
    "SHRIRAMFIN": [
        {"type": "SPLIT", "ratio": "5:1", "factor": 5.0, "ex_date": "2025-01-10", "intraday_back_adjusted": True}
    ],
    "MCX": [
        {"type": "SPLIT", "ratio": "1:5", "factor": 5.0, "ex_date": "2026-01-02", "intraday_back_adjusted": True}
    ],
    "PCJEWELLER": [
        # Empirical finding: Dhan Daily API back-adjusted PCJEWELLER by 10x, but Dhan Intraday API served raw unadjusted prices (~47.80)
        {"type": "SPLIT", "ratio": "10:1", "factor": 10.0, "ex_date": "2024-12-16", "intraday_back_adjusted": False}
    ],
    "CUPID": [
        {"type": "SPLIT_AND_BONUS", "ratio": "1:10_SPLIT_1:1_BONUS", "factor": 20.0, "ex_date": "2024-04-04", "intraday_back_adjusted": True},
        {"type": "BONUS", "ratio": "4:1", "factor": 5.0, "ex_date": "2026-03-09", "intraday_back_adjusted": True},
    ],
}


def get_available_space(path: Path) -> int:
    statvfs = os.statvfs(path)
    return statvfs.f_bavail * statvfs.f_frsize


def load_manifest_windows(path: Path) -> list[dict[str, Any]]:
    """Reject a manifest whose displayed scope differs from the requests sent."""
    manifest = json.loads(path.read_text())
    windows = manifest.get("windows")
    if manifest.get("schema_version") != "alpha-grid-historical-manifest-v1" or not isinstance(windows, list) or not windows:
        raise ValueError("historical manifest schema or windows invalid")
    seen: set[tuple[str, str, str]] = set()
    identities: dict[str, str] = {}
    for w in windows:
        req = HistoricalRequest(
            CashInstrument(w["venue"], w["segment"], w["security_id"], w["symbol"]),
            date.fromisoformat(w["start"]), date.fromisoformat(w["end"]), 1,
        )
        key = (req.instrument.security_id, w["start"], w["end"])
        if (key in seen or not 0 < (req.end - req.start).days <= 90
                or w.get("interval_minutes", 1) != 1
                or w.get("endpoint", req.endpoint) != req.endpoint
                or w.get("payload", req.payload()) != req.payload()
                or identities.get(req.instrument.security_id, req.instrument.symbol) != req.instrument.symbol):
            raise ValueError("historical manifest contains duplicate or mismatched request")
        seen.add(key)
        identities[req.instrument.security_id] = req.instrument.symbol
    if manifest.get("total_planned_requests", len(windows)) != len(windows):
        raise ValueError("historical manifest request count mismatch")
    if manifest.get("universe_size", len(identities)) != len(identities):
        raise ValueError("historical manifest universe size mismatch")
    return windows


def determine_window_ca_status(symbol: str, start: date, end: date) -> dict[str, Any]:
    """Determine corporate action adjustment factor and classification for this specific window."""
    events = CORPORATE_ACTIONS.get(symbol, [])
    if not events:
        return {
            "corporate_action_status": "NO_KNOWN_BONUS_OR_SPLIT_RECORDED",
            "adjustment_factor": 1.0,
            "events": [],
            "evidence_basis": "UNVERIFIED_PRICE_BASIS",
        }

    applicable_factor: float | None = 1.0
    active_events = []
    has_ex_date_span = False

    for ev in events:
        ex_d = date.fromisoformat(ev["ex_date"])
        if start < ex_d < end:
            has_ex_date_span = True
            active_events.append({**ev, "window_position": "EX_DATE_SPANNED_WITHIN_WINDOW"})
        elif end <= ex_d:
            if ev.get("intraday_back_adjusted", True):
                if applicable_factor is not None:
                    applicable_factor *= ev["factor"]
                active_events.append({**ev, "window_position": "PRIOR_TO_EX_DATE_BACK_ADJUSTED"})
            else:
                active_events.append({**ev, "window_position": "INTRADAY_NOT_BACK_ADJUSTED_EMPIRICAL"})
        else:
            active_events.append({**ev, "window_position": "POST_EX_DATE_NATIVE"})

    if has_ex_date_span:
        return {
            "corporate_action_status": "EX_DATE_SPANNED_WITHIN_WINDOW",
            "adjustment_factor": None,
            "events": active_events,
            "evidence_basis": "UNVERIFIED_INTRADAY_ADJUSTMENT",
        }

    if not any(ev.get("intraday_back_adjusted", True) for ev in events):
        return {
            "corporate_action_status": "VERIFIED_UNADJUSTED_EMPIRICAL",
            "adjustment_factor": 1.0,
            "events": active_events,
            "evidence_basis": "EMPIRICAL_INTRADAY_UNADJUSTED",
        }

    if applicable_factor is not None and applicable_factor > 1.0:
        return {
            "corporate_action_status": "VERIFIED_BACK_ADJUSTED",
            "adjustment_factor": applicable_factor,
            "events": active_events,
            "evidence_basis": "CONFIRMED_RETROSPECTIVE_SPLIT_OR_BONUS",
        }

    return {
        "corporate_action_status": "VERIFIED_UNADJUSTED_POST_ACTION",
        "adjustment_factor": 1.0,
        "events": active_events,
        "evidence_basis": "POST_CORPORATE_ACTION_NATIVE_PRICES",
    }


def validate_candle_data(raw_bytes: bytes, req: HistoricalRequest) -> dict[str, Any]:
    """Rigorously validate raw candle response against shape, ordering, OHLCV, and bounds."""
    try:
        data = json.loads(raw_bytes)
    except Exception as exc:
        return {
            "valid": False,
            "bars": 0,
            "error": f"JSONDecodeError: {exc}",
            "shape_valid": False,
            "ohlc_violations": 0,
            "non_finite_count": 0,
            "negative_volumes": 0,
            "duplicate_timestamps": 0,
            "conflicting_duplicates": 0,
            "unordered_timestamps": 0,
            "out_of_bounds": 0,
            "unique_sessions": [],
        }

    if not isinstance(data, dict):
        return {
            "valid": False,
            "bars": 0,
            "error": "Root JSON entity is not an object",
            "shape_valid": False,
            "ohlc_violations": 0,
            "non_finite_count": 0,
            "negative_volumes": 0,
            "duplicate_timestamps": 0,
            "conflicting_duplicates": 0,
            "unordered_timestamps": 0,
            "out_of_bounds": 0,
            "unique_sessions": [],
        }

    fields = ("timestamp", "open", "high", "low", "close", "volume")
    if any(name not in data or not isinstance(data[name], list) for name in fields):
        return {
            "valid": False,
            "bars": 0,
            "error": "Missing or non-list candle fields",
            "shape_valid": False,
            "ohlc_violations": 0,
            "non_finite_count": 0,
            "negative_volumes": 0,
            "duplicate_timestamps": 0,
            "conflicting_duplicates": 0,
            "unordered_timestamps": 0,
            "out_of_bounds": 0,
            "unique_sessions": [],
        }

    stamps = data["timestamp"]
    opens = data["open"]
    highs = data["high"]
    lows = data["low"]
    closes = data["close"]
    volumes = data["volume"]

    bar_count = len(stamps)
    if any(len(arr) != bar_count for arr in (opens, highs, lows, closes, volumes)):
        return {
            "valid": False,
            "bars": bar_count,
            "error": "Candle array lengths are unequal",
            "shape_valid": False,
            "ohlc_violations": 0,
            "non_finite_count": 0,
            "negative_volumes": 0,
            "duplicate_timestamps": 0,
            "conflicting_duplicates": 0,
            "unordered_timestamps": 0,
            "out_of_bounds": 0,
            "unique_sessions": [],
        }

    if bar_count == 0:
        return {
            "valid": True,
            "bars": 0,
            "shape_valid": True,
            "ohlc_violations": 0,
            "non_finite_count": 0,
            "negative_volumes": 0,
            "duplicate_timestamps": 0,
            "conflicting_duplicates": 0,
            "unordered_timestamps": 0,
            "out_of_bounds": 0,
            "price_min": None,
            "price_max": None,
            "volume_sum": 0,
            "unique_sessions": [],
        }

    ohlc_violations = 0
    non_finite_count = 0
    neg_vols = 0
    out_of_bounds = 0
    dup_stamps = 0
    conflicting_dups = 0
    unordered_stamps = 0

    seen_stamps: dict[int, tuple[float, float, float, float, float]] = {}
    prev_s: int | None = None
    unique_dates = set()
    session_bar_counts: dict[str, int] = {}
    continuous_session_bar_counts: dict[str, int] = {}
    continuous_bars = 0
    pre_open_bars = 0
    post_close_bars = 0
    muhurat_bars = 0

    for s, o, h, l, c, v in zip(stamps, opens, highs, lows, closes, volumes, strict=True):
        # Type & finiteness check
        if isinstance(s, bool) or not isinstance(s, (int, float)) or not math.isfinite(s):
            non_finite_count += 1
            continue
        s_int = int(s)
        if s != s_int or s_int % 60:
            non_finite_count += 1
            continue

        num_vals = (o, h, l, c, v)
        if any(isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) for val in num_vals):
            non_finite_count += 1
            continue

        # Monotonicity & duplicates
        if prev_s is not None and s_int < prev_s:
            unordered_stamps += 1

        if s_int in seen_stamps:
            dup_stamps += 1
            if seen_stamps[s_int] != (float(o), float(h), float(l), float(c), float(v)):
                conflicting_dups += 1
        else:
            seen_stamps[s_int] = (float(o), float(h), float(l), float(c), float(v))

        prev_s = s_int

        # OHLC consistency
        if not (l <= o <= h and l <= c <= h and l <= h):
            ohlc_violations += 1
        if v < 0:
            neg_vols += 1

        # Date & session boundary
        try:
            dt = datetime.fromtimestamp(s_int, IST)
        except (OverflowError, OSError, ValueError):
            non_finite_count += 1
            continue
        d = dt.date()
        d_str = d.isoformat()
        unique_dates.add(d_str)
        session_bar_counts[d_str] = session_bar_counts.get(d_str, 0) + 1

        if not (req.start <= d < req.end):
            out_of_bounds += 1

        # Permitted exchange session operational times:
        # Day session (pre-open order discovery, continuous, closing auction): 09:00 to 16:00 IST
        # Evening special session (Muhurat): 17:30 to 20:00 IST
        t = dt.time()
        is_day_time = DAY_SESSION_START <= t < DAY_SESSION_END
        is_evening_time = EVENING_SESSION_START <= t < EVENING_SESSION_END
        if not (is_day_time or is_evening_time):
            out_of_bounds += 1
        elif is_evening_time:
            muhurat_bars += 1
        elif t < dt_time(9, 15):
            pre_open_bars += 1
        elif t >= (dt_time(15, 15) if d_str in CAS_VERIFIED_INSTRUMENTS.get(req.instrument.symbol, set()) else dt_time(15, 30)):
            post_close_bars += 1
        else:
            continuous_bars += 1
            continuous_session_bar_counts[d_str] = continuous_session_bar_counts.get(d_str, 0) + 1

    is_structurally_valid = (
        ohlc_violations == 0
        and non_finite_count == 0
        and neg_vols == 0
        and dup_stamps == 0
        and unordered_stamps == 0
        and out_of_bounds == 0
    )
    parser_error = None
    if is_structurally_valid:
        try:
            if len(parse_response(raw_bytes, req)) != bar_count:
                parser_error = "parser changed candle count"
        except (ValueError, TypeError, OverflowError) as exc:
            parser_error = str(exc)
    is_structurally_valid = is_structurally_valid and parser_error is None

    return {
        "valid": is_structurally_valid,
        "parser_error": parser_error,
        "bars": bar_count,
        "shape_valid": True,
        "ohlc_violations": ohlc_violations,
        "non_finite_count": non_finite_count,
        "negative_volumes": neg_vols,
        "duplicate_timestamps": dup_stamps,
        "conflicting_duplicates": conflicting_dups,
        "unordered_timestamps": unordered_stamps,
        "out_of_bounds": out_of_bounds,
        "continuous_bars_count": continuous_bars,
        "pre_open_bars_count": pre_open_bars,
        "post_close_bars_count": post_close_bars,
        "muhurat_bars_count": muhurat_bars,
        "has_auction_bars": (pre_open_bars > 0 or post_close_bars > 0),
        "price_min": min(lows) if non_finite_count == 0 and bar_count > 0 else None,
        "price_max": max(highs) if non_finite_count == 0 and bar_count > 0 else None,
        "volume_sum": sum(volumes) if non_finite_count == 0 and bar_count > 0 else 0,
        "unique_sessions": sorted(list(unique_dates)),
        "session_bar_counts": session_bar_counts,
        "continuous_session_bar_counts": continuous_session_bar_counts,
        "first_timestamp_ist": datetime.fromtimestamp(int(stamps[0]), IST).isoformat() if bar_count > 0 and non_finite_count == 0 else None,
        "last_timestamp_ist": datetime.fromtimestamp(int(stamps[-1]), IST).isoformat() if bar_count > 0 and non_finite_count == 0 else None,
    }


def reconcile_exchange_sessions(
    symbol: str,
    start: date,
    end: date,
    bars: int,
    session_bar_counts: dict[str, int],
    continuous_session_bar_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Exchange-calendar-aware session reconciliation replacing crude weekday heuristic."""
    expected_dates: list[str] = []
    curr = start
    while curr < end:
        curr_str = curr.isoformat()
        if curr_str in NSE_SPECIAL_SESSIONS or (curr.weekday() < 5 and curr_str not in NSE_TRADING_HOLIDAYS):
            expected_dates.append(curr_str)
        curr += timedelta(days=1)
    if bars == 0:
        if symbol == "MONEYVIEW" and end <= date(2026, 10, 1):
            cov_status = "NOT_YET_LISTED"
        elif symbol == "CUPID" and end <= date(2024, 7, 1):
            cov_status = "ZERO_BARS_CAUSE_UNVERIFIED"
        else:
            cov_status = "ZERO_BARS_EMPTY"
        return {
            "coverage_classification": cov_status,
            "expected_sessions": expected_dates,
            "observed_sessions": [],
            "missing_sessions": expected_dates,
            "truncated_sessions": [],
            "special_sessions_observed": [],
            "unexpected_sessions": [],
            "cas_semantics_unverified": False,
            "unresolved_timestamp_semantics": "DHAN_UNRESOLVED_AUCTION_OR_UNTRADED_MINUTES",
        }

    expected_set = set(expected_dates)
    observed_set = set(session_bar_counts.keys())

    missing_sessions = sorted(list(expected_set - observed_set))
    unexpected_sessions = sorted(list(observed_set - expected_set))
    truncated_sessions = []
    special_observed = []

    cas_dates = CAS_VERIFIED_INSTRUMENTS.get(symbol, set())
    potential_cas_dates = {"2026-10-15", "2026-10-16"}
    cas_semantics_unverified = False

    for d_str, count in session_bar_counts.items():
        if d_str in NSE_SPECIAL_SESSIONS:
            spec = NSE_SPECIAL_SESSIONS[d_str]
            special_observed.append({
                "date": d_str,
                "kind": spec["kind"],
                "bars": count,
                "expected": spec["expected_bars"],
            })
            if count < spec["expected_bars"]:
                truncated_sessions.append({
                    "date": d_str,
                    "observed": count,
                    "expected": spec["expected_bars"],
                    "deficit": spec["expected_bars"] - count,
                })
        elif d_str in cas_dates:
            # Date- and instrument-specific verified CAS Phase 1: continuous trading closes at 15:15 IST (360 bars)
            special_observed.append({
                "date": d_str,
                "kind": "CAS_PHASE_1_CONTINUOUS",
                "bars": count,
                "expected": 360,
            })
            continuous_count = continuous_session_bar_counts.get(d_str, 0) if continuous_session_bar_counts is not None else count
            if continuous_count < 360:
                truncated_sessions.append({
                    "date": d_str,
                    "observed": continuous_count,
                    "expected": 360,
                    "deficit": 360 - continuous_count,
                })
        else:
            # Regular continuous session: expected 375 bars (09:15 - 15:29)
            if d_str in potential_cas_dates and symbol != "RELIANCE":
                cas_semantics_unverified = True
            expected_regular = 375
            continuous_count = continuous_session_bar_counts.get(d_str, 0) if continuous_session_bar_counts is not None else count
            if continuous_count < expected_regular:
                truncated_sessions.append({
                    "date": d_str,
                    "observed": continuous_count,
                    "expected": expected_regular,
                    "deficit": expected_regular - continuous_count,
                })

    # Coverage classification:
    if missing_sessions:
        cov_status = "PARTIAL_COVERAGE_MISSING_SESSIONS"
    elif truncated_sessions:
        cov_status = "PARTIAL_COVERAGE_INCOMPLETE_MINUTES"
    elif unexpected_sessions:
        cov_status = "UNVERIFIED_SESSION_COVERAGE"
    else:
        cov_status = "FULL_SESSION_COVERAGE"

    return {
        "coverage_classification": cov_status,
        "expected_sessions": expected_dates,
        "observed_sessions": sorted(list(observed_set)),
        "missing_sessions": missing_sessions,
        "truncated_sessions": truncated_sessions,
        "special_sessions_observed": special_observed,
        "unexpected_sessions": unexpected_sessions,
        "cas_semantics_unverified": cas_semantics_unverified,
        "unresolved_timestamp_semantics": "DHAN_UNRESOLVED_AUCTION_OR_UNTRADED_MINUTES",
    }


def determine_session_coverage(
    symbol: str,
    start: date,
    end: date,
    bars: int,
    unique_sessions: list[str] | dict[str, int],
) -> str:
    """Classify session coverage via calendar reconciliation."""
    if isinstance(unique_sessions, list):
        # Adapt list of dates to session_bar_counts assuming standard day
        bar_counts = {}
        for d in unique_sessions:
            if d in NSE_SPECIAL_SESSIONS:
                bar_counts[d] = NSE_SPECIAL_SESSIONS[d]["expected_bars"]
            elif d in CAS_VERIFIED_INSTRUMENTS.get(symbol, set()):
                bar_counts[d] = 360
            else:
                bar_counts[d] = 375
    else:
        bar_counts = unique_sessions

    recon = reconcile_exchange_sessions(symbol, start, end, bars, bar_counts)
    return recon["coverage_classification"]


def determine_research_eligibility(
    bars: int,
    is_valid: bool,
    cov_status: str,
    ca_status: str,
    has_auction_bars: bool = False,
    cas_semantics_unverified: bool = False,
) -> tuple[str, str]:
    """Decouple exploratory return-model eligibility from historical execution eligibility."""
    if bars == 0:
        return "INELIGIBLE_NO_DATA", "INELIGIBLE_NO_DATA"

    if not is_valid:
        return "INELIGIBLE_DEFECTIVE_DATA", "INELIGIBLE_DEFECTIVE_DATA"

    # Exploratory Return Modeling
    if ca_status == "EX_DATE_SPANNED_WITHIN_WINDOW":
        return_elig = "REQUIRES_EX_DATE_DISCONTINUITY_PURGING"
    elif cov_status in (
        "PARTIAL_SESSION_COVERAGE",
        "PARTIAL_COVERAGE_MISSING_SESSIONS",
        "PARTIAL_COVERAGE_INCOMPLETE_MINUTES",
        "UNVERIFIED_SESSION_COVERAGE",
    ):
        return_elig = "ELIGIBLE_PARTIAL_WINDOW_MASKING_REQUIRED"
    elif has_auction_bars:
        return_elig = "ELIGIBLE_AUCTION_CANDLES_MASKING_REQUIRED"
    else:
        return_elig = "ELIGIBLE"

    # Historical Execution Model
    if ca_status == "VERIFIED_BACK_ADJUSTED":
        exec_elig = "INELIGIBLE_BACK_ADJUSTED_REQUIRES_RECONSTRUCTION"
    elif ca_status == "EX_DATE_SPANNED_WITHIN_WINDOW":
        exec_elig = "INELIGIBLE_EX_DATE_DISCONTINUITY"
    elif cov_status in (
        "PARTIAL_SESSION_COVERAGE",
        "PARTIAL_COVERAGE_MISSING_SESSIONS",
        "PARTIAL_COVERAGE_INCOMPLETE_MINUTES",
        "UNVERIFIED_SESSION_COVERAGE",
    ):
        exec_elig = "INELIGIBLE_INCOMPLETE_OR_UNVERIFIED_SESSION_COVERAGE"
    elif has_auction_bars:
        exec_elig = "INELIGIBLE_AUCTION_BARS_REQUIRE_AUCTION_EXECUTION_MODEL"
    elif cas_semantics_unverified:
        exec_elig = "UNVERIFIED_CAS_TIMESTAMP_SEMANTICS"
    elif ca_status == "NO_KNOWN_BONUS_OR_SPLIT_RECORDED":
        exec_elig = "UNVERIFIED_PRICE_BASIS"
    elif ca_status in ("VERIFIED_UNADJUSTED_EMPIRICAL", "VERIFIED_UNADJUSTED_POST_ACTION"):
        exec_elig = "ELIGIBLE_RAW_UNADJUSTED"
    else:
        exec_elig = "UNVERIFIED_PRICE_BASIS"

    return return_elig, exec_elig


def verify_existing_checkpoint_file(
    req: HistoricalRequest,
    target_path: Path,
) -> dict[str, Any]:
    """Verify existing raw file SHA-256, sidecar, metadata identity, and candle validity."""
    sha_path = target_path.with_suffix(".sha256")
    meta_path = target_path.with_suffix(".meta.json")

    if not target_path.exists():
        return {"status": "MISSING", "source_integrity": "MISSING"}

    raw = target_path.read_bytes()
    computed_sha = hashlib.sha256(raw).hexdigest()

    # Verify sidecar sha
    if not sha_path.exists():
        return {"status": "MISSING_SIDECAR_SHA", "source_integrity": "DEFECTIVE_SOURCE"}
    sidecar_sha = sha_path.read_text().strip()
    if sidecar_sha != computed_sha:
        return {
            "status": "CORRUPT_HASH_MISMATCH",
            "source_integrity": "DEFECTIVE_SOURCE",
            "computed_sha": computed_sha,
            "sidecar_sha": sidecar_sha,
        }

    if not meta_path.exists():
        return {"status": "MISSING_METADATA", "source_integrity": "DEFECTIVE_SOURCE"}
    try:
        meta = json.loads(meta_path.read_text())
        received_at = datetime.fromisoformat(meta["received_at"])
        if (
            received_at.tzinfo is None
            or meta.get("schema_version") != "dhan-chart-raw-v1"
            or meta.get("endpoint") != req.endpoint
            or meta.get("method") != "POST"
            or meta.get("request") != req.payload()
            or meta.get("sha256") != computed_sha
            or meta.get("http_status") != 200
        ):
            return {"status": "CORRUPT_METADATA_MISMATCH", "source_integrity": "DEFECTIVE_SOURCE"}
        if "normalization" in meta:
            diagnostics = diagnose_response(raw, req)
            expected = {
                "reordered": diagnostics["reordered"],
                "sorted_original_indices": diagnostics["sorted_original_indices"],
                "collapsed_duplicate_indices": diagnostics["identical_duplicate_indices"],
            }
            if meta["normalization"] != expected:
                return {"status": "CORRUPT_METADATA_MISMATCH", "source_integrity": "DEFECTIVE_SOURCE"}
    except Exception:
        return {"status": "CORRUPT_METADATA_JSON", "source_integrity": "DEFECTIVE_SOURCE"}

    # Strict candle validation
    val = validate_candle_data(raw, req)
    sym = req.instrument.symbol
    recon = reconcile_exchange_sessions(
        sym, req.start, req.end, val["bars"], val.get("session_bar_counts", {}),
        val.get("continuous_session_bar_counts", {}),
    )
    cov_status = recon["coverage_classification"]
    ca_info = determine_window_ca_status(sym, req.start, req.end)
    return_elig, exec_elig = determine_research_eligibility(
        val["bars"],
        val["valid"],
        cov_status,
        ca_info["corporate_action_status"],
        has_auction_bars=val.get("has_auction_bars", False),
        cas_semantics_unverified=recon.get("cas_semantics_unverified", False),
    )

    return {
        "status": "COMPLETED" if val["valid"] else "INVALID_CANDLE_DATA",
        "path": str(target_path),
        "sha256": computed_sha,
        "sha256_match": True,
        "byte_size": len(raw),
        "bars_count": val["bars"],
        "coverage_classification": cov_status,
        "session_reconciliation": recon,
        "source_integrity": "VALID_SOURCE" if val["valid"] else "DEFECTIVE_SOURCE",
        "corporate_action_status": ca_info["corporate_action_status"],
        "corporate_action_factor": ca_info["adjustment_factor"],
        "exploratory_return_model_eligibility": return_elig,
        "historical_execution_model_eligibility": exec_elig,
        "validation": val,
    }


def run_acquisition(
    target_dir: Path,
    manifest_path: Path | None = None,
    checkpoint_path: Path | None = None,
    token: str | None = None,
    transport: HistoricalTransport | None = None,
    max_requests: int | None = None,
    delay_seconds: float = 1.5,
    verify_only: bool = False,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(target_dir, 0o700)
    quarantine_dir = target_dir / ".quarantine"
    quarantine_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(quarantine_dir, 0o700)

    checkpoint_file = checkpoint_path or (target_dir / "acquisition-checkpoint.json")
    manifest_file = manifest_path or (target_dir / "historical-manifest-2023-2026.json")

    if not manifest_file.exists():
        raise FileNotFoundError(f"Missing manifest file at {manifest_file}")

    try:
        planned_windows = load_manifest_windows(manifest_file)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Corrupt manifest file: {exc}") from exc

    # Load or recover checkpoint safely
    completed: dict[str, dict] = {}
    if checkpoint_file.exists():
        try:
            completed = json.loads(checkpoint_file.read_text())
            if not isinstance(completed, dict):
                raise ValueError("Checkpoint is not a dictionary")
        except Exception as exc:
            # Safely preserve corrupted checkpoint as evidence
            corrupt_target = quarantine_dir / f"corrupt-checkpoint-{uuid.uuid4().hex}.json"
            _write_private_bytes(corrupt_target, checkpoint_file.read_bytes())
            print(f"WARNING: Corrupt checkpoint safely quarantined to {corrupt_target}. Starting clean recovery.")
            completed = {}

    def save_checkpoint() -> None:
        _write_private_json(checkpoint_file, completed)

    # First pass: Resume-verify existing raw files on disk
    recovered_count = 0
    disk_defects: set[str] = set()
    for w in planned_windows:
        sid = w["security_id"]
        sym = w["symbol"]
        start_d = date.fromisoformat(w["start"])
        end_d = date.fromisoformat(w["end"])
        key = f"{sid}-1-{w['start']}-{w['end']}"
        target_path = target_dir / f"{sid}-1-{w['start']}-{w['end']}.json"

        req = HistoricalRequest(
            instrument=CashInstrument(w["venue"], w["segment"], sid, sym),
            start=start_d,
            end=end_d,
            interval_minutes=1,
        )

        audit_res = verify_existing_checkpoint_file(req, target_path)
        if audit_res["status"] == "COMPLETED":
            record = {
                "key": key,
                "symbol": sym,
                "security_id": sid,
                "start": w["start"],
                "end": w["end"],
                **audit_res,
            }
            if completed.get(key) != record:
                completed[key] = record
                recovered_count += 1
        elif audit_res["status"] != "MISSING":
            # Keep defective raw bytes in place and block reacquisition.
            record = {
                "key": key,
                "symbol": sym,
                "security_id": sid,
                "start": w["start"],
                "end": w["end"],
                **audit_res,
            }
            completed[key] = record
            disk_defects.add(key)
        elif completed.get(key, {}).get("status") == "COMPLETED" or key not in completed:
            completed[key] = {
                "key": key, "symbol": sym, "security_id": sid,
                "start": w["start"], "end": w["end"], **audit_res,
            }

    # Persist recovered checkpoint entries even if no new downloads occur
    save_checkpoint()

    if verify_only:
        completed_valid = sum(
            completed.get(f"{w['security_id']}-1-{w['start']}-{w['end']}", {}).get("status") == "COMPLETED"
            for w in planned_windows
        )
        return {
            "executed": 0,
            "errors": len(planned_windows) - completed_valid,
            "total_completed": completed_valid,
            "total_planned": len(planned_windows),
            "fatal": False,
            "status": "VERIFY_ONLY_FINISHED",
        }

    # Verify credentials before download
    active_token = token
    if not active_token:
        root = Path(__file__).resolve().parent.parent
        active_token, client_id, expiry = load_credentials(root)

    executed_count = 0
    attempted_count = 0
    errors_count = 0
    fatal_error = False

    for idx, w in enumerate(planned_windows):
        sid = w["security_id"]
        sym = w["symbol"]
        start_d = date.fromisoformat(w["start"])
        end_d = date.fromisoformat(w["end"])
        key = f"{sid}-1-{w['start']}-{w['end']}"

        # If already completed and valid, skip
        if key in completed and completed[key].get("status") == "COMPLETED":
            continue

        # If existing record has corrupt hash/metadata, fail closed (do not silently overwrite)
        if key in disk_defects or completed.get(key, {}).get("status") == "INVALID_RESPONSE_QUARANTINED":
            print(f"ABORT: Window {key} has corrupted existing file. Fail-closed without overwriting.")
            errors_count += 1
            continue

        if max_requests is not None and attempted_count >= max_requests:
            print(f"Reached batch execution limit of {max_requests} requests.")
            break

        # Check disk space headroom
        avail_space = get_available_space(target_dir)
        if avail_space < MIN_DISK_HEADROOM_BYTES:
            print(f"FATAL: Disk space below 10 GiB headroom ({avail_space / 1024**3:.2f} GiB). Aborting.")
            fatal_error = True
            break

        req = HistoricalRequest(
            instrument=CashInstrument(w["venue"], w["segment"], sid, sym),
            start=start_d,
            end=end_d,
            interval_minutes=1,
        )

        print(f"[{idx+1}/{len(planned_windows)}] Fetching {sym:<12} ({sid}) for {w['start']} -> {w['end']}...")

        req_success = False
        last_error = None
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            if max_requests is not None and attempted_count >= max_requests:
                break
            attempted_count += 1
            try:
                paths = run_backfill(
                    requests=(req,),
                    output_dir=target_dir,
                    quarantine_dir=quarantine_dir,
                    enabled=True,
                    token=active_token,
                    transport=transport,
                    minimum_interval_seconds=max(0.0001, delay_seconds),
                    max_retries=0,
                    sleep=sleep,
                )
                saved_path = paths[0]
                audit_res = verify_existing_checkpoint_file(req, saved_path)
                record = {
                    "key": key,
                    "symbol": sym,
                    "security_id": sid,
                    "start": w["start"],
                    "end": w["end"],
                    **audit_res,
                }
                completed[key] = record
                if audit_res["status"] == "COMPLETED":
                    executed_count += 1
                    req_success = True
                    print(f"    -> OK: {audit_res['bars_count']:,} bars (Cov: {audit_res['coverage_classification']})")
                else:
                    print(f"    -> INVALID: {audit_res['status']}; original response retained.")
                break

            except urllib.error.HTTPError as exc:
                err_msg = str(exc)
                last_error = err_msg
                if exc.code in (401, 403):
                    print(f"FATAL: Auth/Entitlement error HTTP {exc.code}. Stopping immediately.")
                    completed[key] = {
                        "key": key,
                        "symbol": sym,
                        "security_id": sid,
                        "start": w["start"],
                        "end": w["end"],
                        "status": "FATAL_AUTH_ERROR",
                        "error": err_msg,
                    }
                    fatal_error = True
                    break
                if exc.code == 429:
                    backoff = 10.0 * attempt
                    print(f"       Rate limited (429). Backing off for {backoff:.1f}s...")
                    sleep(backoff)
                else:
                    sleep(3.0 * attempt)

            except ValueError as exc:
                err_msg = str(exc)
                last_error = err_msg
                completed[key] = {
                    "key": key, "symbol": sym, "security_id": sid,
                    "start": w["start"], "end": w["end"],
                    "status": "INVALID_RESPONSE_QUARANTINED", "error": err_msg,
                }
                print(f"    -> INVALID: {err_msg}; original response retained where available.")
                break

            except Exception as exc:
                err_msg = str(exc)
                last_error = err_msg
                code_text = err_msg.removeprefix("Dhan chart HTTP ")
                http_code = (
                    int(code_text)
                    if err_msg.startswith("Dhan chart HTTP ") and code_text.isdigit()
                    else None
                )
                if http_code in (401, 403):
                    print(f"FATAL: Auth/Entitlement error: {err_msg}. Stopping immediately.")
                    completed[key] = {
                        "key": key,
                        "symbol": sym,
                        "security_id": sid,
                        "start": w["start"],
                        "end": w["end"],
                        "status": "FATAL_AUTH_ERROR",
                        "error": err_msg,
                    }
                    fatal_error = True
                    break
                if attempt < max_retries and (max_requests is None or attempted_count < max_requests):
                    sleep((10.0 if http_code == 429 else 3.0) * attempt)

        if fatal_error:
            errors_count += 1
            save_checkpoint()
            break

        if not req_success:
            errors_count += 1
            if completed.get(key, {}).get("status") not in ("INVALID_CANDLE_DATA", "INVALID_RESPONSE_QUARANTINED", "CORRUPT_HASH_MISMATCH", "CORRUPT_METADATA_MISMATCH"):
                completed[key] = {
                    "key": key,
                    "symbol": sym,
                    "security_id": sid,
                    "start": w["start"],
                    "end": w["end"],
                    "status": "ERROR",
                    "error": last_error or "Request budget exhausted",
                }

        save_checkpoint()

    completed_valid = sum(
        completed.get(f"{w['security_id']}-1-{w['start']}-{w['end']}", {}).get("status") == "COMPLETED"
        for w in planned_windows
    )

    status_str = "COMPLETE" if (completed_valid == len(planned_windows) and errors_count == 0 and not fatal_error) else "INCOMPLETE"
    if fatal_error:
        status_str = "FATAL"

    return {
        "executed": executed_count,
        "attempted": attempted_count,
        "errors": errors_count,
        "total_completed": completed_valid,
        "total_planned": len(planned_windows),
        "fatal": fatal_error,
        "status": status_str,
    }


def generate_summary_report(target_dir: Path, manifest_path: Path | None = None) -> dict[str, Any]:
    manifest_file = manifest_path or (target_dir / "historical-manifest-2023-2026.json")
    matrix_file = target_dir / "full-3yr-coverage-matrix.json"

    planned_windows = load_manifest_windows(manifest_file)

    by_symbol: dict[str, list[dict]] = {}
    for w in planned_windows:
        sym = w["symbol"]
        by_symbol.setdefault(sym, []).append(w)

    universe_summary = []
    total_candles = 0
    total_bytes = 0
    total_completed_reqs = 0

    for sym, win_list in by_symbol.items():
        sid = win_list[0]["security_id"]
        sym_bars = 0
        sym_bytes = 0
        sym_windows_completed = 0
        sym_missing_windows = []
        sym_cov_classes = set()
        sym_ca_statuses = set()
        sym_return_eligs = set()
        sym_exec_eligs = set()

        window_records = []
        for w in win_list:
            key = f"{sid}-1-{w['start']}-{w['end']}"
            req = HistoricalRequest(
                CashInstrument(w["venue"], w["segment"], sid, sym),
                date.fromisoformat(w["start"]),
                date.fromisoformat(w["end"]),
                1,
            )
            audit = verify_existing_checkpoint_file(req, target_dir / f"{key}.json")
            if audit["status"] == "COMPLETED":
                rec = {"key": key, "symbol": sym, "security_id": sid, **audit}
                sym_windows_completed += 1
                total_completed_reqs += 1
                bars = rec.get("bars_count", 0)
                b_size = rec.get("byte_size", 0)
                sym_bars += bars
                sym_bytes += b_size
                total_candles += bars
                total_bytes += b_size
                sym_cov_classes.add(rec.get("coverage_classification", "UNKNOWN"))
                sym_ca_statuses.add(rec.get("corporate_action_status", "UNKNOWN"))
                sym_return_eligs.add(rec.get("exploratory_return_model_eligibility", "UNKNOWN"))
                sym_exec_eligs.add(rec.get("historical_execution_model_eligibility", "UNKNOWN"))
                window_records.append(rec)
            else:
                sym_missing_windows.append(f"{w['start']}->{w['end']} (PENDING)")

        # Derive symbol classification strictly from validated window records
        if sym_windows_completed == 0:
            pt_status = "NO_VALID_WINDOWS_COMPLETED"
            sym_return_summary = "INELIGIBLE_NO_DATA"
            sym_exec_summary = "INELIGIBLE_NO_DATA"
        elif sym_windows_completed < len(win_list):
            pt_status = "INCOMPLETE_ACQUISITION"
            sym_return_summary = "INCOMPLETE_PENDING_WINDOWS"
            sym_exec_summary = "INCOMPLETE_PENDING_WINDOWS"
        elif sym_cov_classes == {"NOT_YET_LISTED"}:
            pt_status = "NOT_YET_LISTED_FULL_PERIOD"
            sym_return_summary = "INELIGIBLE_NOT_YET_LISTED"
            sym_exec_summary = "INELIGIBLE_NOT_YET_LISTED"
        elif "NOT_YET_LISTED" in sym_cov_classes and sym_bars > 0:
            pt_status = "LISTED_DURING_PERIOD_PARTIAL_HISTORY"
            sym_return_summary = "PARTIALLY_ELIGIBLE_POST_LISTING"
            sym_exec_summary = "UNVERIFIED_PRICE_BASIS"
        elif any(c.startswith("ZERO_BARS") for c in sym_cov_classes) or "HISTORICAL_DATA_UNAVAILABLE" in sym_cov_classes:
            pt_status = "LISTED_EARLY_INTRADAY_ZERO_BARS_CAUSE_UNVERIFIED"
            sym_return_summary = "PARTIALLY_ELIGIBLE_POST_GAP_ONLY" if sym_bars > 0 else "INELIGIBLE_EARLY_DATA_CAUSE_UNVERIFIED"
            sym_exec_summary = "INELIGIBLE_EARLY_DATA_CAUSE_UNVERIFIED"
        else:
            pt_status = "VERIFIED_COMPLETE_ACQUISITION"
            if "REQUIRES_EX_DATE_DISCONTINUITY_PURGING" in sym_return_eligs:
                sym_return_summary = "REQUIRES_EX_DATE_DISCONTINUITY_PURGING"
            elif sym_return_eligs == {"ELIGIBLE"}:
                sym_return_summary = "ELIGIBLE"
            else:
                sym_return_summary = "PARTIAL_WINDOW_MASKING_REQUIRED"
            ineligible = {value for value in sym_exec_eligs if value.startswith("INELIGIBLE_")}
            if len(ineligible) == 1:
                sym_exec_summary = next(iter(ineligible))
            elif ineligible:
                sym_exec_summary = "INELIGIBLE_MULTIPLE_WINDOW_RESTRICTIONS"
            elif "UNVERIFIED_PRICE_BASIS" in sym_exec_eligs:
                sym_exec_summary = "UNVERIFIED_PRICE_BASIS"
            elif sym_exec_eligs == {"ELIGIBLE_RAW_UNADJUSTED"}:
                sym_exec_summary = "ELIGIBLE_RAW_UNADJUSTED"
            else:
                sym_exec_summary = "UNVERIFIED_PRICE_BASIS"
        row = {
            "symbol": sym,
            "security_id": sid,
            "point_in_time_status": pt_status,
            "windows_planned": len(win_list),
            "windows_completed": sym_windows_completed,
            "total_bars": sym_bars,
            "total_bytes": sym_bytes,
            "missing_windows": sym_missing_windows,
            "observed_coverage_classes": sorted(list(sym_cov_classes)),
            "observed_ca_statuses": sorted(list(sym_ca_statuses)),
            "exploratory_return_model_eligibility": sym_return_summary,
            "historical_execution_model_eligibility": sym_exec_summary,
            "windows": window_records,
        }
        universe_summary.append(row)

    report = {
        "schema_version": "alpha-grid-full-3yr-coverage-matrix-v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "evaluation_period": "2023-11-01 to 2026-11-01",
        "universe_size": len(by_symbol),
        "total_planned_requests": len(planned_windows),
        "total_completed_requests": total_completed_reqs,
        "total_candles": total_candles,
        "total_bytes": total_bytes,
        "universe_summary": universe_summary,
    }

    tmp_mat = matrix_file.with_suffix(".tmp")
    tmp_mat.write_text(json.dumps(report, indent=2))
    os.chmod(tmp_mat, 0o600)
    os.replace(tmp_mat, matrix_file)

    print("\n=================== 3-YEAR ACQUISITION SUMMARY ===================")
    print(f"Total Requests Completed: {total_completed_reqs} / {len(planned_windows)}")
    print(f"Total 1-Minute Candles Captured: {total_candles:,}")
    print(f"Total Storage Consumed: {total_bytes / 1024 / 1024:.2f} MiB")
    print(f"{'Symbol':<12} {'SID':<8} {'Windows':<10} {'Total Bars':<12} {'Return Modeling':<20} {'Execution Backtest':<32}")
    print("-" * 96)
    for row in universe_summary:
        print(f"{row['symbol']:<12} {row['security_id']:<8} {row['windows_completed']}/{row['windows_planned']:<7} {row['total_bars']:<12,d} {row['exploratory_return_model_eligibility']:<20} {row['historical_execution_model_eligibility']:<32}")
    print("=" * 96)
    print(f"Full 3-year matrix saved to: {matrix_file}\n")
    return report


def main() -> int:
    volume = Path("/Volumes/AlphaGrid Data")
    if not volume.is_mount():
        print("FAIL-CLOSED: Encrypted Alpha Grid data volume is not mounted.")
        return 1
    target_dir = volume / "Live/offline-dev/historical-raw-dhan"
    target_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(target_dir, 0o700)

    print("=== Alpha Grid: Executing Complete 3-Year Historical Acquisition ===")
    res = run_acquisition(target_dir, delay_seconds=1.5)
    print(f"\nAcquisition run finished: {res['executed']} requests executed, {res['errors']} errors, total completed: {res['total_completed']}/{res['total_planned']}")

    generate_summary_report(target_dir)

    # Fail closed: exit code 0 ONLY IF 100% complete and 0 errors and not fatal
    is_complete = (res["total_completed"] == res["total_planned"] and res["errors"] == 0 and not res["fatal"])
    if not is_complete:
        print("FAIL-CLOSED: Acquisition is incomplete or encountered errors.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
