"""Frozen weekly relative-strength reference experiment; offline, no orders."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
import hashlib
import json
import math
from pathlib import Path
import random
import statistics

from quant.acquisition.calendar import IST
from quant.acquisition.contracts import CashInstrument
from quant.acquisition.dhan import HistoricalRequest
from quant.research.prospective import NSE_CASH_TRANSACTION_RATE, SEBI_TURNOVER_RATE, IPFT_TURNOVER_RATE
from tools.execute_full_3yr_acquisition import (
    CORPORATE_ACTIONS, NSE_SPECIAL_SESSIONS, NSE_TRADING_HOLIDAYS,
    HistoricalValuationIneligible, contemporaneous_price, validate_candle_data,
)

SPEC = Path("docs/exp-weekly-relative-strength-v1.md")
SNAPSHOT = Path("data/research_snapshot_manifest_v1.json")
UNIVERSE = Path("docs/cross-stock-universe-2026-11-02.json")
HOLDOUT = {"TCS", "KOTAKBANK", "MARUTI", "BHEL", "DIVISLAB"}
END = "2025-10-31"
EARLY_END = "2024-12-31"
CAP = Decimal("5000")


def verified_morning_and_closes() -> tuple[dict, dict[str, str], dict, int, int]:
    universe = json.loads(UNIVERSE.read_text())["instruments"]
    ids = {row["symbol"]: str(row["security_id"]) for row in universe if row["symbol"] not in HOLDOUT}
    if len(universe) != 25 or len(ids) != 20 or len(HOLDOUT & set(ids)):
        raise ValueError("frozen universe/holdout mismatch")
    manifest = json.loads(SNAPSHOT.read_text())
    root = Path(manifest["provenance"]["raw_data_dir"]).resolve()
    bars: dict[str, dict[str, dict[time, float]]] = {symbol: {} for symbol in ids}
    source_rows: dict[str, dict[str, dict[time, dict]]] = {symbol: {} for symbol in ids}
    files = bytes_read = 0
    for row in manifest["files"]:
        symbol = row["symbol"]
        if symbol not in ids:
            continue
        parts = row["key"].split("-")
        if len(parts) != 8:
            raise ValueError("source key invalid")
        start, end = "-".join(parts[2:5]), "-".join(parts[5:8])
        if end > "2025-11-01":
            continue  # Never read temporal holdout bytes.
        if parts[:2] != [ids[symbol], "1"] or str(row["security_id"]) != ids[symbol]:
            raise ValueError("source identity mismatch")
        path = Path(row["path"])
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("source path outside raw archive")
        raw = path.read_bytes()
        if len(raw) != row["byte_size"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError(f"source byte/hash mismatch: {row['key']}")
        req = HistoricalRequest(CashInstrument("NSE", "NSE_EQ", ids[symbol], symbol),
                                date.fromisoformat(start), date.fromisoformat(end), 1)
        check = validate_candle_data(raw, req)
        if not check["valid"] or check["bars"] != row["bars"]:
            raise ValueError(f"candle contract invalid: {row['key']}")
        obj = json.loads(raw)
        for stamp, open_px, close_px in zip(obj["timestamp"], obj["open"], obj["close"], strict=True):
            instant = datetime.fromtimestamp(int(stamp), IST)
            day = instant.date().isoformat()
            if (day > END or instant.weekday() >= 5 or day in NSE_TRADING_HOLIDAYS
                    or day in NSE_SPECIAL_SESSIONS):
                continue
            clock = instant.time()
            if clock not in (time(9, 30), time(14, 29)):
                continue
            cell = bars[symbol].setdefault(day, {})
            if clock in cell:
                raise ValueError(f"duplicate research bar: {symbol} {day} {clock}")
            cell[clock] = float(open_px if clock == time(9, 30) else close_px)
            source_rows[symbol].setdefault(day, {})[clock] = row
        files += 1
        bytes_read += len(raw)
    return bars, ids, source_rows, files, bytes_read


def corporate_masks(sessions: list[str], symbols: dict[str, str]) -> dict[str, set[str]]:
    masks = {symbol: set() for symbol in symbols}
    for symbol in symbols:
        for event in CORPORATE_ACTIONS.get(symbol, []):
            ex_day = event["ex_date"]
            masks[symbol].add(ex_day)
            earlier = [day for day in sessions if day < ex_day]
            if earlier:
                masks[symbol].add(earlier[-1])
    return masks


def select(bars: dict, ids: dict[str, str]) -> tuple[list[tuple[int, str, str, str]], dict, list[str]]:
    sessions = sorted({day for stock in bars.values() for day, row in stock.items()
                       if time(14, 29) in row})
    masks = corporate_masks(sessions, ids)
    selected: list[tuple[int, str, str, str]] = []
    counts = Counter()
    last_exit = -1
    for index in range(60, len(sessions) - 5):
        day = sessions[index]
        if date.fromisoformat(day).weekday() != 4:
            continue
        counts["fridays"] += 1
        ranked = []
        for symbol in ids:
            lookback = sessions[index - 60:index + 1]
            if any(d in masks[symbol] or time(14, 29) not in bars[symbol].get(d, {}) for d in lookback):
                continue
            closes = [bars[symbol][d][time(14, 29)] for d in lookback]
            if min(closes) <= 0:
                continue
            returns = [math.log(b / a) for a, b in zip(closes, closes[1:])]
            volatility = statistics.stdev(returns)
            if volatility <= 0:
                continue
            trend = math.log(closes[-1] / closes[0])
            ranked.append((trend / (volatility * math.sqrt(60)), symbol, trend))
        if len(ranked) < 15:
            counts["insufficient_names"] += 1
            continue
        ranked.sort(key=lambda value: (-value[0], value[1]))
        if ranked[0][0] <= 0:
            counts["no_positive_trend"] += 1
            continue
        target, control = ranked[0][1], ranked[len(ranked) // 2][1]
        if any(d in masks[sym] for sym in (target, control)
               for d in sessions[index:index + 6]):
            counts["corporate_action_censored"] += 1
            continue
        if any(time(9, 30) not in bars[sym].get(d, {}) for sym in (target, control)
               for d in sessions[index + 1:index + 6]):
            counts["missing_path_censored"] += 1
            continue
        if index + 1 <= last_exit:
            counts["overlap_censored"] += 1
            continue
        breadth = "POSITIVE_BREADTH" if sum(row[2] > 0 for row in ranked) / len(ranked) >= 0.5 else "NEGATIVE_BREADTH"
        selected.append((index, target, control, breadth))
        last_exit = index + 5
    return selected, dict(counts), sessions


def _ceil_paise(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_CEILING)


def _nearest_rupee(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def delivery_fees(buy: Decimal, sell: Decimal) -> Decimal:
    """Same provisional rate/rounding contract as Rust FeeSchedule::for_fill."""
    def leg(notional: Decimal, *, sale: bool) -> Decimal:
        exchange = _ceil_paise(notional * NSE_CASH_TRANSACTION_RATE)
        sebi = _ceil_paise(notional * SEBI_TURNOVER_RATE)
        ipft = _ceil_paise(notional * IPFT_TURNOVER_RATE)
        dp = Decimal("12.50") if sale else Decimal(0)
        gst = _ceil_paise((exchange + sebi + ipft + dp) * Decimal("0.18"))
        stt = _nearest_rupee(notional * Decimal("0.001"))
        stamp = Decimal(0) if sale else _nearest_rupee(notional * Decimal("0.00015"))
        return exchange + sebi + ipft + dp + gst + stt + stamp
    return leg(buy, sale=False) + leg(sell, sale=True)


def result(bars: dict, sessions: list[str], pick: tuple[int, str, str, str],
           symbol: str, spread_bps: int, residual_bps: int, source_rows: dict) -> dict:
    index = pick[0]
    entry_day, exit_day = sessions[index + 1], sessions[index + 5]
    try:
        buy_price = contemporaneous_price(
            Decimal(str(bars[symbol][entry_day][time(9, 30)])), symbol, entry_day,
            source_rows[symbol][entry_day][time(9, 30)],
        )
        sell_price = contemporaneous_price(
            Decimal(str(bars[symbol][exit_day][time(9, 30)])), symbol, exit_day,
            source_rows[symbol][exit_day][time(9, 30)],
        )
    except (HistoricalValuationIneligible, KeyError) as exc:
        reason = str(exc) if isinstance(exc, HistoricalValuationIneligible) else "PRICE_SOURCE_MISSING"
        return {"status": "NO_TRADE_UNRESOLVED", "reason": reason, "quantity": 0,
                **{key: Decimal(0) for key in ("gross", "fees", "spread", "residual", "net",
                                                 "gross_bps", "net_bps")}}
    quantity = int(CAP // buy_price)
    if quantity < 1:
        return {"status": "NO_TRADE_UNAFFORDABLE", "reason": "POSITION_CAP_BELOW_WHOLE_SHARE_PRICE",
                "quantity": 0, "entry_price": buy_price, "exit_price": sell_price,
                **{key: Decimal(0) for key in ("gross", "fees", "spread", "residual", "net",
                                                 "gross_bps", "net_bps")}}
    buy, sell = quantity * buy_price, quantity * sell_price
    gross = sell - buy
    fees = delivery_fees(buy, sell)
    spread = buy * Decimal(spread_bps) / 10000
    residual = buy * Decimal(residual_bps) / 10000
    return {"status": "ELIGIBLE", "reason": None, "quantity": quantity,
            "entry_price": buy_price, "exit_price": sell_price,
            "gross": gross, "fees": fees, "spread": spread, "residual": residual,
            "net": gross - fees - spread - residual, "gross_bps": gross * 10000 / buy,
            "net_bps": (gross - fees - spread - residual) * 10000 / buy}


def ci(values: list[float], seed: int) -> list[float | None]:
    if not values:
        return [None, None]
    rng = random.Random(seed)
    n = len(values)
    draws = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(2000))
    return [round(draws[49], 4), round(draws[1949], 4)]


def summary(bars: dict, sessions: list[str], picks: list[tuple[int, str, str, str]],
            spread: int, residual: int, source_rows: dict) -> dict:
    if not picks:
        return {"n": 0}
    chosen = [result(bars, sessions, pick, pick[1], spread, residual, source_rows) for pick in picks]
    controls = [result(bars, sessions, pick, pick[2], spread, residual, source_rows) for pick in picks]
    n = len(picks)
    status = dict(sorted(Counter(row["status"] for row in chosen).items()))
    control_status = dict(sorted(Counter(row["status"] for row in controls).items()))
    no_trade = [{"decision_date": sessions[pick[0]], "symbol": pick[1], "reason": row["reason"]}
                for pick, row in zip(picks, chosen, strict=True) if row["status"] != "ELIGIBLE"]
    control_no_trade = [{"decision_date": sessions[pick[0]], "symbol": pick[2], "reason": row["reason"]}
                        for pick, row in zip(picks, controls, strict=True) if row["status"] != "ELIGIBLE"]
    total = lambda key: sum((row[key] for row in chosen), Decimal(0))
    nets = [float(row["net"]) for row in chosen]
    paired = [float(a["net"] - b["net"]) for a, b in zip(chosen, controls, strict=True)]
    breadth_net = {label: round(sum(float(row["net"]) for row, pick in zip(chosen, picks, strict=True)
                                    if pick[3] == label) / sum(pick[3] == label for pick in picks), 4)
                   for label in set(pick[3] for pick in picks)}
    running = peak = drawdown = Decimal(0)
    for row in chosen:
        running += row["net"]
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
    quantile = lambda key, p: round(sorted(float(row[key]) for row in chosen)[int((n - 1) * p)], 4)
    return {"n": n, "valuation_status": status, "control_valuation_status": control_status,
            "no_trade": no_trade, "control_no_trade": control_no_trade,
            "symbols": dict(sorted(Counter(pick[1] for pick in picks).items())),
            "breadth": dict(sorted(Counter(pick[3] for pick in picks).items())),
            "mean_net_by_breadth_inr": dict(sorted(breadth_net.items())),
            "gross_inr": str(total("gross")), "fees_inr": str(total("fees")),
            "spread_inr": str(total("spread")), "residual_inr": str(total("residual")),
            "net_inr": str(total("net")),
            "control_net_inr": str(sum((row["net"] for row in controls), Decimal(0))),
            "mean_net_inr": round(sum(nets) / n, 4), "mean_net_ci95": ci(nets, 20261108),
            "mean_over_sd_per_opportunity": round(statistics.mean(nets) / statistics.stdev(nets), 4)
             if n > 1 and statistics.stdev(nets) > 0 else None,
            "paired_mean_net_inr": round(sum(paired) / n, 4),
            "paired_mean_ci95": ci(paired, 20261109),
            "gross_bps_p05_p50_p95": [quantile("gross_bps", p) for p in (0.05, 0.5, 0.95)],
            "net_bps_p05_p50_p95": [quantile("net_bps", p) for p in (0.05, 0.5, 0.95)],
            "favorable_ge_100bps": round(sum(row["gross_bps"] >= 100 for row in chosen) / n, 4),
            "adverse_le_minus100bps": round(sum(row["gross_bps"] <= -100 for row in chosen) / n, 4),
            "mean_favorable_ge_100bps": round(statistics.mean(float(row["gross_bps"]) for row in chosen
                                      if row["gross_bps"] >= 100), 4)
                                      if any(row["gross_bps"] >= 100 for row in chosen) else None,
            "mean_adverse_le_minus100bps": round(statistics.mean(float(row["gross_bps"]) for row in chosen
                                        if row["gross_bps"] <= -100), 4)
                                        if any(row["gross_bps"] <= -100 for row in chosen) else None,
            "worst_net_inr": str(min(row["net"] for row in chosen)),
            "max_reference_drawdown_inr": str(drawdown)}


def main() -> None:
    spec_hash = hashlib.sha256(SPEC.read_bytes()).hexdigest()
    bars, ids, source_rows, files, byte_count = verified_morning_and_closes()
    picks, selection, sessions = select(bars, ids)
    early = [pick for pick in picks if sessions[pick[0]] <= EARLY_END]
    later = [pick for pick in picks if sessions[pick[0]] > EARLY_END]
    results = {"experiment": "EXP-WEEKLY-RELATIVE-STRENGTH-V1", "spec_sha256": spec_hash,
               "files_verified": files, "source_bytes": byte_count,
               "selection": selection, "session_dates": len(sessions),
               "no_trade_net_inr": "0", "valuation_basis": "PROVISIONAL_ACTION_AWARE_REFERENCE"}
    results["early"] = {"base": summary(bars, sessions, early, 10, 10, source_rows),
                        "conservative": summary(bars, sessions, early, 20, 20, source_rows)}
    results["later"] = {"base": summary(bars, sessions, later, 10, 10, source_rows),
                        "conservative": summary(bars, sessions, later, 20, 20, source_rows)}
    current = results["later"]["conservative"]
    old = results["early"]["conservative"]
    if any("NO_TRADE_UNRESOLVED" in side["valuation_status"] or
           "NO_TRADE_UNRESOLVED" in side["control_valuation_status"]
           for side in (current, old)):
        classification = "ECONOMIC_INELIGIBLE_UNRESOLVED_PRICE_BASIS"
    elif current["n"] < 25:
        classification = "INCONCLUSIVE_LOW_SESSION_COUNT"
    elif current["mean_net_inr"] <= 0 or current["paired_mean_net_inr"] <= 0:
        classification = "REJECTED"
    elif (current["mean_net_ci95"][0] <= 0 or current["paired_mean_ci95"][0] <= 0
          or old["mean_net_inr"] <= 0 or old["paired_mean_net_inr"] <= 0
          or results["later"]["base"]["mean_net_inr"] <= 0
          or len(current["symbols"]) < 5 or max(current["symbols"].values()) / current["n"] > 0.4):
        classification = "INCONCLUSIVE"
    else:
        classification = "ECONOMIC_CANDIDATE_EXPLORATORY_ONLY"
    results["classification"] = classification
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
