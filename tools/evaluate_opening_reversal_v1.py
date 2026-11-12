"""Frozen EXP-OPENING-SELL-PRESSURE-REVERSAL-V1 exploratory reference test."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random

from quant.acquisition.calendar import IST
from quant.acquisition.contracts import CashInstrument
from quant.acquisition.dhan import HistoricalRequest
from quant.research.prospective import compute_dhan_intraday_fees
from tools.execute_full_3yr_acquisition import (
    CORPORATE_ACTIONS, NSE_SPECIAL_SESSIONS, NSE_TRADING_HOLIDAYS,
    HistoricalValuationIneligible, contemporaneous_price, validate_candle_data,
)

SPEC = Path("docs/exp-opening-sell-pressure-reversal-v1.md")
SNAPSHOT = Path("data/research_snapshot_manifest_v1.json")
UNIVERSE = Path("docs/cross-stock-universe-2026-11-02.json")
STOCKS = {"HDFCBANK": "1333", "RELIANCE": "2885", "INFY": "1594",
          "ICICIBANK": "4963", "BHARTIARTL": "10604"}
START, TRAIN_END, END = "2023-11-01", "2025-04-30", "2025-10-31"
POSITION_CAP = Decimal("5000")


def minute(m: int) -> time:
    return time(m // 60, m % 60)


def load_verified_bars() -> tuple[dict, dict, int, int]:
    """Read only five frozen names and pre-holdout responses, failing on any bad source."""
    ranked = json.loads(UNIVERSE.read_text())["instruments"][:5]
    if {x["symbol"]: str(x["security_id"]) for x in ranked} != STOCKS:
        raise ValueError("frozen five-stock identity mismatch")
    manifest = json.loads(SNAPSHOT.read_text())
    root = Path(manifest["provenance"]["raw_data_dir"]).resolve()
    bars: dict[str, dict[str, dict[time, dict]]] = {sym: {} for sym in STOCKS}
    source_rows: dict[str, dict[str, dict]] = {sym: {} for sym in STOCKS}
    files = byte_count = 0
    for row in manifest["files"]:
        sym = row["symbol"]
        if sym not in STOCKS:
            continue
        parts = row["key"].split("-")
        if len(parts) != 8:
            raise ValueError("source window key malformed")
        start, end = "-".join(parts[2:5]), "-".join(parts[5:8])
        if end > "2025-11-01" or start > END:
            continue  # Never read the temporal holdout response.
        if (parts[0] != STOCKS[sym] or parts[1] != "1" or
                str(row["security_id"]) != STOCKS[sym]):
            raise ValueError("source instrument identity mismatch")
        path = Path(row["path"])
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("source path outside pinned raw directory")
        raw = path.read_bytes()
        if len(raw) != row["byte_size"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError(f"raw source hash/size mismatch: {row['key']}")
        req = HistoricalRequest(CashInstrument("NSE", "NSE_EQ", STOCKS[sym], sym),
                                date.fromisoformat(start), date.fromisoformat(end), 1)
        audit = validate_candle_data(raw, req)
        if not audit["valid"] or audit["bars"] != row["bars"]:
            raise ValueError(f"raw candle contract failed: {row['key']}: {audit.get('error')}")
        obj = json.loads(raw)
        for ts, o, h, l, c, v in zip(obj["timestamp"], obj["open"], obj["high"],
                                     obj["low"], obj["close"], obj["volume"], strict=True):
            stamp = datetime.fromtimestamp(int(ts), IST)
            day = stamp.date().isoformat()
            if not START <= day <= END:
                continue
            if (stamp.weekday() >= 5 or day in NSE_TRADING_HOLIDAYS or
                    day in NSE_SPECIAL_SESSIONS):
                continue
            clock = stamp.time()
            if not time(9, 15) <= clock <= time(12, 11):
                continue
            day_bars = bars[sym].setdefault(day, {})
            if clock in day_bars:
                raise ValueError(f"duplicate selected-session minute: {sym} {day} {clock}")
            day_bars[clock] = {"o": o, "h": h, "l": l, "c": c, "v": v}
            source_rows[sym][day] = row
        files += 1
        byte_count += len(raw)
    return bars, source_rows, files, byte_count


def action_masks(bars: dict[str, dict[str, dict[time, dict]]]) -> dict[str, set[str]]:
    masks: dict[str, set[str]] = {sym: set() for sym in STOCKS}
    for sym in STOCKS:
        days = sorted(bars[sym])
        for event in CORPORATE_ACTIONS.get(sym, []):
            ex_day = event["ex_date"]
            if not START <= ex_day <= END:
                continue
            masks[sym].add(ex_day)
            prior = [day for day in days if day < ex_day]
            if prior:
                masks[sym].add(prior[-1])
    return masks


def select_pairs(bars: dict[str, dict[str, dict[time, dict]]]) -> tuple[list[tuple[str, str, str]], dict]:
    """Select on 09:15–09:59 data only; future bars cannot change selection."""
    masks = action_masks(bars)
    days = sorted(set().union(*(set(x) for x in bars.values())))
    pairs: list[tuple[str, str, str]] = []
    no_shock = no_control = 0
    for day in days:
        opening: list[tuple[float, str]] = []
        for sym in STOCKS:
            if day in masks[sym]:
                continue
            b = bars[sym].get(day, {})
            if any(minute(m) not in b for m in range(9 * 60 + 15, 10 * 60)):
                continue
            o, c = b[time(9, 15)]["o"], b[time(9, 59)]["c"]
            if o <= 0 or c <= 0:
                continue
            opening.append(((c / o - 1) * 10000, sym))
        shocks = [row for row in opening if row[0] <= -50.0]
        if not shocks:
            no_shock += 1
            continue
        target = min(shocks, key=lambda row: (row[0], row[1]))[1]
        others = [row for row in opening if row[1] != target]
        if not others:
            no_control += 1
            continue
        control = min(others, key=lambda row: (abs(row[0]), row[1]))[1]
        pairs.append((day, target, control))
    return pairs, {"available_session_dates": len(days), "no_shock": no_shock,
                   "no_control": no_control, "past_only_pairs": len(pairs)}


def complete_pairs(bars: dict[str, dict[str, dict[time, dict]]],
                   pairs: list[tuple[str, str, str]]) -> tuple[list[tuple[str, str, str]], int]:
    complete = []
    for day, target, control in pairs:
        if all(all(minute(m) in bars[sym][day] for m in range(10 * 60, 12 * 60 + 12))
               for sym in (target, control)):
            complete.append((day, target, control))
    return complete, len(pairs) - len(complete)


def outcome(bars: dict[str, dict[str, dict[time, dict]]], pair: tuple[str, str, str],
            symbol: str, spread_bps: int, residual_bps: int, late: bool,
            source_rows: dict) -> dict:
    day = pair[0]
    entry = time(10, 11) if late else time(10, 6)
    exit_at = time(12, 11) if late else time(12, 6)
    try:
        source_row = source_rows[symbol][day]
        buy_px = contemporaneous_price(Decimal(str(bars[symbol][day][entry]["o"])),
                                       symbol, day, source_row)
        sell_px = contemporaneous_price(Decimal(str(bars[symbol][day][exit_at]["o"])),
                                        symbol, day, source_row)
    except (HistoricalValuationIneligible, KeyError) as exc:
        reason = str(exc) if isinstance(exc, HistoricalValuationIneligible) else "PRICE_SOURCE_MISSING"
        return {"day": day, "symbol": symbol, "status": "NO_TRADE_UNRESOLVED",
                "reason": reason, "shares": 0,
                **{key: Decimal(0) for key in ("buy", "gross", "fees", "spread", "residual",
                                                 "net", "gross_bps", "net_bps")}}
    shares = int(POSITION_CAP // buy_px)
    if shares < 1:
        return {"day": day, "symbol": symbol, "status": "NO_TRADE_UNAFFORDABLE",
                "reason": "POSITION_CAP_BELOW_WHOLE_SHARE_PRICE", "shares": 0,
                "entry_price": buy_px, "exit_price": sell_px,
                **{key: Decimal(0) for key in ("buy", "gross", "fees", "spread", "residual",
                                                 "net", "gross_bps", "net_bps")}}
    buy, sell = buy_px * shares, sell_px * shares
    gross = sell - buy
    fees = compute_dhan_intraday_fees(buy, sell).total_statutory_fees
    spread = buy * Decimal(spread_bps) / 10000
    residual = buy * Decimal(residual_bps) / 10000
    net = gross - fees - spread - residual
    return {"day": day, "symbol": symbol, "status": "ELIGIBLE", "reason": None,
            "shares": shares, "entry_price": buy_px, "exit_price": sell_px,
            "buy": buy, "gross": gross,
            "fees": fees, "spread": spread, "residual": residual, "net": net,
            "gross_bps": gross / buy * 10000, "net_bps": net / buy * 10000}


def interval(values: list[float], seed: int) -> list[float | None]:
    if not values:
        return [None, None]
    rng = random.Random(seed)
    n = len(values)
    draws = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(2000))
    return [round(draws[49], 4), round(draws[1949], 4)]


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[int((len(ordered) - 1) * p)], 4)


def summarize(bars: dict, pairs: list[tuple[str, str, str]],
              spread: int, residual: int, late: bool, source_rows: dict) -> dict:
    selected = [outcome(bars, pair, pair[1], spread, residual, late, source_rows) for pair in pairs]
    controls = [outcome(bars, pair, pair[2], spread, residual, late, source_rows) for pair in pairs]
    n = len(selected)
    if not n:
        return {"n": 0}
    status = dict(sorted(Counter(row["status"] for row in selected).items()))
    control_status = dict(sorted(Counter(row["status"] for row in controls).items()))
    no_trade = [{"decision_date": row["day"], "symbol": row["symbol"], "reason": row["reason"]}
                for row in selected if row["status"] != "ELIGIBLE"]
    control_no_trade = [{"decision_date": row["day"], "symbol": row["symbol"], "reason": row["reason"]}
                        for row in controls if row["status"] != "ELIGIBLE"]
    add = lambda key: sum((row[key] for row in selected), Decimal(0))
    net_inr = [float(x["net"]) for x in selected]
    paired_inr = [float(a["net"] - b["net"]) for a, b in zip(selected, controls, strict=True)]
    pnl = peak = Decimal(0)
    drawdown = Decimal(0)
    for row in selected:
        pnl += row["net"]
        peak = max(peak, pnl)
        drawdown = max(drawdown, peak - pnl)
    counts = Counter(x["symbol"] for x in selected)
    return {"n": n, "valuation_status": status, "control_valuation_status": control_status,
            "no_trade": no_trade, "control_no_trade": control_no_trade,
            "symbols": dict(sorted(counts.items())),
            "gross_inr": str(add("gross")), "fees_inr": str(add("fees")),
            "spread_inr": str(add("spread")), "residual_inr": str(add("residual")),
            "net_inr": str(add("net")),
            "control_net_inr": str(sum((x["net"] for x in controls), Decimal(0))),
            "mean_net_inr": round(sum(net_inr) / n, 4),
            "mean_net_bps": round(sum(float(x["net_bps"]) for x in selected) / n, 4),
            "selected_mean_net_inr_ci95": interval(net_inr, 20261108),
            "paired_mean_net_inr": round(sum(paired_inr) / n, 4),
            "paired_mean_net_inr_ci95": interval(paired_inr, 20261109),
            "favorable_ge_30bps": round(sum(x["gross_bps"] >= 30 for x in selected) / n, 4),
            "adverse_le_minus30bps": round(sum(x["gross_bps"] <= -30 for x in selected) / n, 4),
            "gross_bps_p05_p50_p95": [percentile([float(x["gross_bps"]) for x in selected], p)
                                      for p in (0.05, 0.50, 0.95)],
            "net_bps_p05_p50_p95": [percentile([float(x["net_bps"]) for x in selected], p)
                                    for p in (0.05, 0.50, 0.95)],
            "worst_net_inr": str(min(x["net"] for x in selected)),
            "max_reference_drawdown_inr": str(drawdown)}


def main() -> None:
    spec_hash = hashlib.sha256(SPEC.read_bytes()).hexdigest()
    bars, source_rows, files, byte_count = load_verified_bars()
    pairs, selection = select_pairs(bars)
    complete, censored = complete_pairs(bars, pairs)
    early = [pair for pair in complete if pair[0] <= TRAIN_END]
    later = [pair for pair in complete if pair[0] > TRAIN_END]
    results = {
        "experiment": "EXP-OPENING-SELL-PRESSURE-REVERSAL-V1",
        "spec_sha256": spec_hash, "files_verified": files, "source_bytes": byte_count,
        "selection": {**selection, "censored_pairs": censored, "complete_pairs": len(complete)},
        "no_trade_net_inr": "0",
        "valuation_basis": "PROVISIONAL_ACTION_AWARE_REFERENCE",
    }
    results["early"] = {"base": summarize(bars, early, 5, 5, False, source_rows),
                        "conservative": summarize(bars, early, 10, 10, False, source_rows)}
    results["later"] = {"base": summarize(bars, later, 5, 5, False, source_rows),
                        "conservative": summarize(bars, later, 10, 10, False, source_rows),
                        "late_entry_conservative": summarize(bars, later, 10, 10, True, source_rows)}
    r = results["later"]["conservative"]
    earlier = results["early"]["conservative"]
    stress = results["later"]["late_entry_conservative"]
    if any("NO_TRADE_UNRESOLVED" in side.get("valuation_status", {}) or
           "NO_TRADE_UNRESOLVED" in side.get("control_valuation_status", {})
           for side in (r, earlier, stress)):
        results["classification"] = "ECONOMIC_INELIGIBLE_UNRESOLVED_PRICE_BASIS"
    elif r["n"] < 30:
        results["classification"] = "INCONCLUSIVE_LOW_SESSION_COUNT"
    elif (r["mean_net_inr"] <= 0 or r["paired_mean_net_inr"] <= 0 or
          stress["mean_net_inr"] <= 0):
        results["classification"] = "REJECT_AND_STOP"
    elif (r["selected_mean_net_inr_ci95"][0] <= 0 or
          r["paired_mean_net_inr_ci95"][0] <= 0 or
          earlier["mean_net_inr"] <= 0 or earlier["paired_mean_net_inr"] <= 0 or
          len(r["symbols"]) < 3 or max(r["symbols"].values()) / r["n"] > 0.60):
        results["classification"] = "INCONCLUSIVE_AND_STOP"
    else:
        results["classification"] = "EXPLORATORY_ONLY_FURTHER_VALIDATION"
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
