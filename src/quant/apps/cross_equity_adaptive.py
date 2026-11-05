"""Versioned, offline cross-equity reference replay; never a trading signal."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median_low
from typing import Any

from quant.acquisition.calendar import IST
from quant.acquisition.contracts import CashInstrument, HistoricalBar
from quant.acquisition.dhan import HistoricalRequest, response_path, verify_saved_response
from quant.apps.cockpit_cache import MAC_EXPECTED_SHA256, MAC_REQUEST, MAC_ROOT
from quant.apps.historical_simulator import (
    CAPITAL, FRICTION_BPS, MAX_REFERENCE_NOTIONAL, UNIVERSE_PATH, _paise,
    _rupees, _session_bars, published_intraday_fees, simulate,
)
from quant.research.uncertainty import bootstrap_session_net

VERSION = "alpha-grid-cross-equity-adaptive-v2"
START = time(10, 0)
LAST_ENTRY = time(14, 0)
FINAL_DECISION = time(14, 40)
SLOT_MINUTES = 5
MAX_HOLD_MINUTES = 60
MAX_ENTRIES_PER_SESSION = 3  # Existing live simulator ceiling.
SESSION_LOSS_LIMIT = Decimal("500")  # Existing QP01 default; independent virtual cap.
ADVERSE_BPS = Decimal("200")  # Existing QP01 downside assumption.
DELAYS = (60, 120, 300)


def load_verified(private: Path) -> tuple[dict, dict[str, tuple[HistoricalBar, ...]], dict]:
    """Recheck original bytes and match every response to the immutable ALL_25 job."""
    universe = json.loads(UNIVERSE_PATH.read_text())
    pointer = json.loads((private / "cross-stock-simulation.latest.json").read_text())
    artifact_path = private / "training-artifacts" / pointer["artifact_name"]
    raw = artifact_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != pointer["sha256"]:
        raise ValueError("saved cross-stock artifact hash mismatch")
    artifact = json.loads(raw)
    fixed = artifact["frozen_rule_simulation"]
    if (artifact.get("source") != "EXPLORATORY_ARCHIVE" or
            artifact.get("fitted_model") is not None or
            len(universe.get("instruments", [])) != 25 or
            len(artifact.get("source_responses", {})) != 25):
        raise ValueError("saved cross-stock artifact provenance invalid")
    result: dict[str, tuple[HistoricalBar, ...]] = {}
    for row in universe["instruments"]:
        symbol = row["symbol"]
        if symbol == "RELIANCE":
            request, folder = MAC_REQUEST, MAC_ROOT
        else:
            request = HistoricalRequest(
                CashInstrument("NSE", "NSE_EQ", row["security_id"], symbol, row["isin"]),
                date(2026, 10, 26), date(2026, 10, 31), 1,
            )
            folder = private / "historical-25-oct26-30"
        audit = verify_saved_response(request, response_path(request, folder))
        expected = artifact["source_responses"][symbol]
        if (audit.sha256 != expected["sha256"] or
                audit.sha256 not in fixed["source_hashes"][symbol] or
                (symbol == "RELIANCE" and audit.sha256 != MAC_EXPECTED_SHA256) or
                len(audit.bars) != expected["bars"] or
                len({bar.trading_date for bar in audit.bars}) != expected["sessions"]):
            raise ValueError(f"original response or mapping mismatch: {symbol}")
        result[symbol] = audit.bars
    if sum(map(len, result.values())) != 45_300 or simulate(result, universe) != fixed:
        raise ValueError("45,300-bar archive does not reproduce frozen baseline")
    return universe, result, fixed


def _stamp(day: str, at: time) -> datetime:
    return datetime.combine(date.fromisoformat(day), at, IST)


def _bps(numerator: int, denominator: int) -> Decimal:
    return Decimal(numerator) * Decimal(10_000) / Decimal(denominator)


def fixed_audit(universe: dict, bars: dict, fixed: dict) -> dict:
    """Export original 10:00 rankings and all 125 dated eligibility decisions."""
    ids = {row["symbol"]: row["security_id"] for row in universe["instruments"]}
    sessions = {symbol: _session_bars(source, symbol, ids[symbol])
                for symbol, source in bars.items()}
    rows = []
    paths = []
    selected = {(r["day"], r["symbol"]): r for r in fixed["trade_ledger"]}
    for day in fixed["sessions"]:
        for symbol, security_id in ids.items():
            minutes = sessions[symbol].get(day, {})
            opening = [minutes.get(time(9, minute)) for minute in range(15, 45)]
            entry = minutes.get(time(9, 59))
            old = minutes.get(time(9, 44))
            future = minutes.get(time(10, 59))
            complete_past = all(opening) and entry is not None and old is not None
            entry_coverage = sum(time(9, 15) <= key <= time(9, 59) for key in minutes)
            row: dict[str, Any] = {
                "day": day, "symbol": symbol, "security_id": security_id,
                "decision_time_ist": "10:00:00", "last_input_label_ist": "09:59:00",
                "past_minute_coverage": entry_coverage,
                "past_complete": bool(complete_past and entry_coverage == 45),
                "future_exit_bar_present": future is not None,
                "source_sha256": bars[symbol][0].source_sha256,
                "original_decision": next(d["decision"] for d in fixed["decisions"]
                                          if d["day"] == day and d["symbol"] == symbol),
            }
            if complete_past:
                opening_high = max(x.high_nanos for x in opening)
                row.update(opening_high_inr=str(_rupees(opening_high)),
                           entry_close_reference_inr=str(_rupees(entry.close_nanos)),
                           breakout_bps=str(_bps(entry.close_nanos - opening_high, opening_high)),
                           trailing_15m_bps=str(_bps(entry.close_nanos - old.close_nanos,
                                                     old.close_nanos)),
                           volume_15m=sum(minutes[time(9, minute)].volume for minute in range(45, 60)),
                           breakout_eligible=entry.close_nanos > opening_high)
            rows.append(row)
            if (day, symbol) in selected:
                high = max(x.high_nanos for x in opening)
                invalidation = next((key for key in sorted(minutes)
                                     if time(10, 0) <= key <= time(10, 59)
                                     and minutes[key].close_nanos <= high), None)
                trade = selected[(day, symbol)]
                paths.append({"day": day, "symbol": symbol,
                              "opening_high_inr": str(_rupees(high)),
                              "first_breakout_invalidation_label_ist":
                              invalidation.isoformat() if invalidation else None,
                              "minimum_close_10_00_to_10_59_inr": str(_rupees(min(
                                  x.close_nanos for key, x in minutes.items()
                                  if time(10, 0) <= key <= time(10, 59)))),
                              "maximum_close_10_00_to_10_59_inr": str(_rupees(max(
                                  x.close_nanos for key, x in minutes.items()
                                  if time(10, 0) <= key <= time(10, 59)))),
                              "gross_inr": trade["gross_reference_inr"],
                              "fees_inr": trade["fee_components_inr"]["total"],
                              "friction_inr": trade["assumed_friction_inr"],
                              "net_inr": trade["net_reference_inr"]})
        all_ranked = sorted((r for r in rows if r["day"] == day and "breakout_bps" in r),
                            key=lambda r: (-Decimal(r["breakout_bps"]), r["security_id"]))
        for rank, row in enumerate(all_ranked, 1):
            row["all_stock_strength_rank"] = rank
        ranked = sorted((r for r in rows if r["day"] == day and r.get("breakout_eligible")),
                        key=lambda r: (-Decimal(r["breakout_bps"]), r["security_id"]))
        for rank, row in enumerate(ranked, 1):
            row["breakout_rank"] = rank
    return {"schema_version": "alpha-grid-cross-equity-fixed-audit-v1",
            "source": "VERIFIED_ORIGINAL_DHAN_BYTES", "examined_dates_only": True,
            "future_exit_availability_used_in_original_eligibility": True,
            "same_09_59_close_used_as_entry_reference": True,
            "candle_availability_timing": "UNVERIFIED",
            "rows": rows, "selected_paths": paths,
            "coverage": {"sessions": len(fixed["sessions"]), "instruments": len(ids),
                         "evaluated_rows": len(rows),
                         "complete_past_rows": sum(r["past_complete"] for r in rows),
                         "missing_future_exit_rows": sum(not r["future_exit_bar_present"] for r in rows)}}


def selected_minute_paths(universe: dict, bars: dict, fixed: dict) -> dict:
    """Retain all original labels from entry through fixed exit for five selections."""
    ids = {row["symbol"]: row["security_id"] for row in universe["instruments"]}
    sessions = {symbol: _session_bars(source, symbol, ids[symbol])
                for symbol, source in bars.items()}
    paths = []
    for trade in fixed["trade_ledger"]:
        day, symbol = trade["day"], trade["symbol"]
        minutes = sessions[symbol][day]
        rows = []
        for minute in range(9 * 60 + 59, 11 * 60):
            label = time(minute // 60, minute % 60)
            bar = minutes[label]
            rows.append({"label_ist": label.isoformat(),
                         "timestamp_ns": str(bar.source_timestamp_ns),
                         "open_inr": str(_rupees(bar.open_nanos)),
                         "high_inr": str(_rupees(bar.high_nanos)),
                         "low_inr": str(_rupees(bar.low_nanos)),
                         "close_inr": str(_rupees(bar.close_nanos)),
                         "volume": bar.volume, "source_sha256": bar.source_sha256})
        paths.append({"day": day, "symbol": symbol, "security_id": ids[symbol],
                      "rows": rows})
    return {"schema_version": "alpha-grid-selected-minute-paths-v2",
            "source": "VERIFIED_ORIGINAL_DHAN_BYTES", "paths": paths,
            "candle_label_semantics": "UNVERIFIED"}


def _feature(minutes: dict[time, HistoricalBar], day: str, decision: datetime,
             delay_seconds: int, prior_sessions: list[dict[time, HistoricalBar]] | None = None) -> dict | None:
    # Hypothesis only: label=start, completion at +60 s, availability after delay.
    cutoff = decision - timedelta(seconds=60 + delay_seconds)
    asof = cutoff.time().replace(second=0, microsecond=0)
    opening = [minutes.get(time(9, minute)) for minute in range(15, 45)]
    if any(x is None for x in opening):
        return None
    last = minutes.get(asof)
    earlier = minutes.get((cutoff - timedelta(minutes=15)).time().replace(second=0, microsecond=0))
    if last is None or earlier is None:
        return None
    recent = [minutes.get((cutoff - timedelta(minutes=i)).time().replace(second=0, microsecond=0))
              for i in range(15)]
    previous = [minutes.get((cutoff - timedelta(minutes=i)).time().replace(second=0, microsecond=0))
                for i in range(15, 30)]
    if any(x is None for x in recent + previous):
        return None
    high = max(x.high_nanos for x in opening)
    close = last.close_nanos
    recent_volume = sum(x.volume for x in recent)
    previous_volume = sum(x.volume for x in previous)
    comparable = []
    for earlier_session in prior_sessions or []:
        matched = [earlier_session.get(
            datetime.fromtimestamp(x.source_timestamp_ns / 1_000_000_000, IST).time())
                   for x in recent]
        if all(matched):
            comparable.append(sum(x.volume for x in matched))
    prior_median = median_low(comparable) if comparable else None
    realized = sum(abs(_bps(recent[i].close_nanos - recent[i + 1].close_nanos,
                            recent[i + 1].close_nanos)) for i in range(14)) / 14
    return {"day": day, "decision_time_ist": decision.time().isoformat(),
            "last_eligible_label_ist": asof.isoformat(),
            "last_eligible_timestamp_ns": str(last.source_timestamp_ns),
            "close_nanos": close, "opening_high_nanos": high,
            "opening_range_position_bps": str(_bps(close - high, high)),
            "recent_return_bps": str(_bps(close - earlier.close_nanos, earlier.close_nanos)),
            "recent_volume": recent_volume, "previous_volume": previous_volume,
            "volume_confirmed": recent_volume >= previous_volume,
            "comparable_time_prior_session_volume_median": prior_median,
            "comparable_time_relative_volume":
            str(Decimal(recent_volume) / Decimal(prior_median)) if prior_median else None,
            "up_minutes_in_recent_15": sum(recent[i].close_nanos > recent[i + 1].close_nanos
                                            for i in range(14)),
            "recent_realized_abs_return_bps": str(realized),
            "distance_from_recent_high_bps": str(_bps(close - max(x.high_nanos for x in recent), close)),
            "distance_from_recent_low_bps": str(_bps(close - min(x.low_nanos for x in recent), close)),
            "quoted_spread_bps": None, "available_depth": None,
            "market_index_direction": None, "favorable_probability": None,
            "adverse_probability": None, "source_sha256": last.source_sha256}


def _cost_bps(price: Decimal) -> Decimal:
    shares = int(MAX_REFERENCE_NOTIONAL // price)
    if shares < 1:
        return Decimal("Infinity")
    notional = price * shares
    fees = Decimal(published_intraday_fees(notional, notional)["total"])
    return FRICTION_BPS + fees * 10_000 / notional


def replay(universe: dict, bars: dict, *, delay_seconds: int,
           require_volume: bool) -> tuple[dict, list[dict]]:
    """Every-five-minute as-of evaluation; reference prices follow decisions."""
    if delay_seconds not in DELAYS:
        raise ValueError("unregistered timing sensitivity")
    ids = {row["symbol"]: row["security_id"] for row in universe["instruments"]}
    sessions = {symbol: _session_bars(source, symbol, ids[symbol])
                for symbol, source in bars.items()}
    days = sorted({day for group in sessions.values() for day in group})
    cash, peak, drawdown = CAPITAL, CAPITAL, Decimal(0)
    trades: list[dict] = []
    decisions: list[dict] = []
    curve = []
    for day in days:
        position = None
        entries = 0
        session_net = Decimal(0)
        for minute in range(10 * 60, 14 * 60 + 41, SLOT_MINUTES):
            decision = _stamp(day, time(minute // 60, minute % 60))
            features = {}
            for symbol in ids:
                minutes = sessions.get(symbol, {}).get(day, {})
                prior_sessions = [history for prior_day, history in sessions.get(symbol, {}).items()
                                  if prior_day < day]
                feature = _feature(minutes, day, decision, delay_seconds, prior_sessions)
                if feature is not None:
                    feature["symbol"] = symbol
                    feature["security_id"] = ids[symbol]
                features[symbol] = feature
            available = [f for f in features.values() if f is not None]
            market_return = (sum((Decimal(f["recent_return_bps"]) for f in available),
                                 Decimal(0)) / len(available)) if available else None
            ranked = []
            for symbol in ids:
                feature = features[symbol]
                if feature is None:
                    decisions.append({"day": day, "time_ist": decision.time().isoformat(),
                                      "symbol": symbol, "kind": "REJECT", "reason": "MISSING_ASOF_INPUT"})
                    continue
                price = _rupees(feature["close_nanos"])
                feature["relative_to_universe_bps"] = (
                    str(Decimal(feature["recent_return_bps"]) - market_return)
                    if market_return is not None else None)
                proxy = Decimal(feature["recent_return_bps"]) - _cost_bps(price)
                feature["net_momentum_proxy_bps"] = str(proxy)
                reason = ("NO_BREAKOUT" if feature["close_nanos"] <= feature["opening_high_nanos"]
                          else "NONPOSITIVE_NET_MOMENTUM_PROXY" if proxy <= 0
                          else "VOLUME_NOT_CONFIRMED" if require_volume and not feature["volume_confirmed"]
                          else None)
                decisions.append({"day": day, "time_ist": decision.time().isoformat(),
                                  "symbol": symbol, "security_id": ids[symbol],
                                  "kind": "CANDIDATE" if reason is None else "REJECT",
                                  "reason": reason, "features": feature})
                if reason is None:
                    ranked.append((proxy, ids[symbol], symbol, feature))
            ranked.sort(key=lambda item: (-item[0], item[1]))
            if position:
                symbol = position["symbol"]
                feature = features.get(symbol)
                reason = None
                if feature is None:
                    decisions.append({"day": day, "time_ist": decision.time().isoformat(),
                                      "symbol": symbol, "kind": "HOLD", "reason": "MISSING_ASOF_INPUT"})
                    if minute == 14 * 60 + 40:
                        reason = "SESSION_CUTOFF_MISSING_ASOF"
                else:
                    value = feature["close_nanos"]
                    if value <= feature["opening_high_nanos"]:
                        reason = "BREAKOUT_INVALIDATED"
                    elif Decimal(value) <= Decimal(position["entry_nanos"]) * (1 - ADVERSE_BPS / 10_000):
                        reason = "PROTECTIVE_ADVERSE_MOVE"
                    elif minute - position["entry_minute"] >= MAX_HOLD_MINUTES:
                        reason = "MAX_HOLD_60M"
                    elif minute == 14 * 60 + 40:
                        reason = "SESSION_CUTOFF"
                if reason:
                    minutes = sessions[symbol][day]
                    exit_label = (decision + timedelta(minutes=1)).time()
                    exit_bar = minutes.get(exit_label)
                    if exit_bar is None:
                        raise ValueError("no subsequent reference for required exit")
                    if feature and exit_bar.source_timestamp_ns <= int(feature["last_eligible_timestamp_ns"]):
                        raise ValueError("exit reference did not follow observation")
                    exit_price = _rupees(exit_bar.close_nanos)
                    sell = exit_price * position["shares"]
                    buy = Decimal(position["buy_notional_inr"])
                    fees = published_intraday_fees(buy, sell)
                    friction = _paise(buy * FRICTION_BPS / 10_000)
                    gross = _paise(sell - buy)
                    net = _paise(gross - Decimal(fees["total"]) - friction)
                    cash = _paise(cash + sell - Decimal(fees["total"]) - friction)
                    session_net += net
                    trade = {**position, "exit_decision_time_ist": decision.time().isoformat(),
                             "exit_reason": reason, "exit_asof_label_ist": feature["last_eligible_label_ist"] if feature else None,
                             "exit_reference_label_ist": exit_label.isoformat(),
                             "exit_close_reference_inr": str(exit_price),
                             "gross_reference_inr": str(gross), "fee_components_inr": fees,
                             "assumed_friction_inr": str(friction), "net_reference_inr": str(net),
                             "cash_after_exit_inr": str(cash), "actual_fill": False}
                    trades.append(trade)
                    decisions.append({"day": day, "time_ist": decision.time().isoformat(),
                                      "symbol": symbol, "kind": "EXIT", "reason": reason,
                                      "reference_label_ist": exit_label.isoformat()})
                    position = None
                    continue
                decisions.append({"day": day, "time_ist": decision.time().isoformat(),
                                  "symbol": symbol, "kind": "HOLD", "reason": "NO_EXIT_TRIGGER"})
                continue
            if minute > 14 * 60 or entries >= MAX_ENTRIES_PER_SESSION or session_net <= -SESSION_LOSS_LIMIT:
                decisions.append({"day": day, "time_ist": decision.time().isoformat(),
                                  "kind": "NO_TRADE", "reason": "ENTRY_CUTOFF_OR_RISK_LIMIT"})
                continue
            if not ranked:
                decisions.append({"day": day, "time_ist": decision.time().isoformat(),
                                  "kind": "NO_TRADE", "reason": "NO_POSITIVE_PROXY_CANDIDATE"})
                continue
            _, _, symbol, feature = ranked[0]
            entry_label = (decision + timedelta(minutes=1)).time()
            entry_bar = sessions[symbol][day].get(entry_label)
            if entry_bar is None or entry_bar.source_timestamp_ns <= int(feature["last_eligible_timestamp_ns"]):
                decisions.append({"day": day, "time_ist": decision.time().isoformat(),
                                  "symbol": symbol, "kind": "REJECT", "reason": "NO_LATER_ENTRY_REFERENCE"})
                continue
            entry_price = _rupees(entry_bar.close_nanos)
            shares = int(min(cash, MAX_REFERENCE_NOTIONAL) // entry_price)
            if shares < 1:
                decisions.append({"day": day, "time_ist": decision.time().isoformat(),
                                  "symbol": symbol, "kind": "REJECT", "reason": "VIRTUAL_CAPITAL_LIMIT"})
                continue
            buy = entry_price * shares
            cash = _paise(cash - buy)
            entries += 1
            position = {"day": day, "symbol": symbol, "security_id": ids[symbol],
                        "strategy_version": VERSION, "entry_decision_time_ist": decision.time().isoformat(),
                        "entry_asof_label_ist": feature["last_eligible_label_ist"],
                        "entry_reference_label_ist": entry_label.isoformat(),
                        "entry_close_reference_inr": str(entry_price),
                        "entry_nanos": entry_bar.close_nanos, "entry_minute": minute,
                        "shares": shares, "buy_notional_inr": str(buy),
                        "cash_after_entry_inr": str(cash),
                        "selection_reason": "HIGHEST_POSITIVE_NET_MOMENTUM_PROXY",
                        "ranked_candidates": [item[2] for item in ranked],
                        "source_sha256": entry_bar.source_sha256}
            decisions.append({"day": day, "time_ist": decision.time().isoformat(),
                              "symbol": symbol, "kind": "SELECT", "reason": position["selection_reason"],
                              "ranked_candidates": position["ranked_candidates"],
                              "reference_label_ist": entry_label.isoformat()})
        if position is not None:
            raise ValueError("virtual position remained open after session cutoff")
        peak = max(peak, cash)
        drawdown = max(drawdown, peak - cash)
        curve.append({"day": day, "session_net_inr": str(session_net),
                      "virtual_equity_inr": str(cash)})
    daily = {row["day"]: float(Decimal(row["session_net_inr"])) for row in curve}
    uncertainty = bootstrap_session_net(daily, min_sessions=5) if len(daily) >= 5 else None
    result = {"strategy_version": VERSION, "variant": "PRICE_PLUS_VOLUME" if require_volume else "PRICE_ONLY",
              "assumed_post_completion_delay_seconds": delay_seconds,
              "source": "EXPLORATORY_ARCHIVE", "timing_eligibility": "UNVERIFIED",
              "evaluation_status": "EXAMINED_DATES_NO_UNTOUCHED_HOLDOUT",
              "gross_reference_inr": str(_paise(sum((Decimal(t["gross_reference_inr"]) for t in trades), Decimal(0)))),
              "fees_inr": str(_paise(sum((Decimal(t["fee_components_inr"]["total"]) for t in trades), Decimal(0)))),
              "assumed_friction_inr": str(_paise(sum((Decimal(t["assumed_friction_inr"]) for t in trades), Decimal(0)))),
              "net_reference_inr": str(_paise(cash - CAPITAL)),
              "ending_virtual_capital_inr": str(cash), "max_drawdown_inr": str(drawdown),
              "independent_sessions": len(days), "selected_opportunities": len(trades),
              "selected_sessions": len({t["day"] for t in trades}),
              "session_bootstrap_95pct_mean_net_inr":
              [str(_paise(Decimal(str(uncertainty.confidence_low_nanos)))),
               str(_paise(Decimal(str(uncertainty.confidence_high_nanos))))]
              if uncertainty else None,
              "abstention_slots": sum(d["kind"] == "NO_TRADE" for d in decisions),
              "decision_counts": dict(Counter(d["kind"] for d in decisions)),
              "equity_curve": curve, "trade_ledger": trades,
              "reference_prices_are_fills": False, "broker_orders": 0, "qp01_writes": 0}
    return result, decisions


def _save_once(path: Path, value: object) -> str:
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"immutable artifact already exists: {path.name}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.parent.stat().st_mode & 0o077:
        raise PermissionError("research artifact directory must be private")
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    return hashlib.sha256(raw).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline verified cross-equity exploratory replay")
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    universe, bars, fixed = load_verified(args.private_dir)
    audit = fixed_audit(universe, bars, fixed)
    variants = {}
    all_decisions = {}
    for delay in DELAYS:
        for require_volume in (False, True):
            label = f"{'price_volume' if require_volume else 'price_only'}_{delay}s"
            variants[label], all_decisions[label] = replay(
                universe, bars, delay_seconds=delay, require_volume=require_volume)
    paired = {}
    for delay in DELAYS:
        simple = {row["day"]: Decimal(row["session_net_inr"])
                  for row in variants[f"price_only_{delay}s"]["equity_curve"]}
        richer = {row["day"]: Decimal(row["session_net_inr"])
                  for row in variants[f"price_volume_{delay}s"]["equity_curve"]}
        differences = {day: float(richer[day] - simple[day]) for day in simple}
        ci = bootstrap_session_net(differences, min_sessions=5)
        paired[f"{delay}s"] = {"incremental_net_inr": str(sum((richer[d] - simple[d] for d in simple), Decimal(0))),
                              "session_bootstrap_95pct_mean_incremental_inr":
                              [str(_paise(Decimal(str(ci.confidence_low_nanos)))),
                               str(_paise(Decimal(str(ci.confidence_high_nanos))))] if ci else None}
    report = {"schema_version": VERSION, "source": "EXPLORATORY_ARCHIVE",
              "source_hashes": fixed["source_hashes"], "bars": sum(map(len, bars.values())),
              "fixed_baseline_ending_inr": fixed["candidate"]["ending_virtual_capital_inr"],
              "momentum_baseline_ending_inr": fixed["momentum"]["ending_virtual_capital_inr"],
              "no_trade_ending_inr": str(CAPITAL),
              "specification": {"hypothesis": "Past-only price momentum plus volume confirmation has greater conservative reference net value than price alone",
                                "null": "No incremental conservative net value",
                                "slots": "Every 5 minutes 10:00–14:40 IST; entries through 14:00",
                                "input_cutoff": "Start label + 60s completion + scenario delay; unverified",
                                "reference": "First minute close label strictly after decision; not a fill",
                                "rank": "Positive 15m return minus published round-trip fees and one 40bp execution allowance; opening-high breakout required",
                                "volume_ablation": "Recent 15m volume >= preceding 15m volume",
                                "exits": "Opening-high invalidation, 200bp adverse move, 60m maximum, 14:40 cutoff; first later close reference",
                                "risk": "One virtual position, ₹100,000 notional cap, three entries/day, ₹500 realized session-loss cap",
                                "acceptance": "No promotion without independent prospective sessions, verified timing and measured costs; reject if conservative session-grouped net advantage is nonpositive",
                                "ml": "Not trained: only five examined sessions, no untouched chronological test"},
              "variants": variants, "paired_volume_ablation": paired, "ml_challenger": None,
              "research_eligibility": "EXPLORATORY_ONLY", "production_eligibility": "BLOCKED"}
    output = args.output_dir
    hashes = {"fixed_audit": _save_once(output / "fixed-audit-v2.json", audit),
              "adaptive_report": _save_once(output / "adaptive-report-v2.json", report),
              "all_decisions": _save_once(output / "all-decisions-v2.json", all_decisions),
              "selected_minute_paths": _save_once(output / "selected-minute-paths-v2.json",
                                                   selected_minute_paths(universe, bars, fixed))}
    _save_once(output / "manifest-v2.json", {"schema_version": "alpha-grid-cross-equity-research-manifest-v2",
                                              "source": "EXPLORATORY_ARCHIVE", "sha256": hashes})
    print(json.dumps({"artifacts": hashes,
                      "fixed_net_inr": str(Decimal(fixed["candidate"]["ending_virtual_capital_inr"]) - CAPITAL),
                      "variants": {name: {"net_inr": row["net_reference_inr"],
                                          "trades": row["selected_opportunities"]}
                                   for name, row in variants.items()}}))


if __name__ == "__main__":
    main()
