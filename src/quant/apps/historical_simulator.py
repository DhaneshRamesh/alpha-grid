"""Offline reference-price simulator. No broker, QP01, feed, or network path."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from quant.acquisition.calendar import IST
from quant.acquisition.contracts import HistoricalBar
from quant.research.uncertainty import bootstrap_session_net

CAPITAL = Decimal("1000000.00")
MAX_REFERENCE_NOTIONAL = Decimal("100000.00")
FRICTION_BPS = Decimal("40")
UNIVERSE_PATH = Path("docs/cross-stock-universe-2026-11-02.json")
OPEN_START = time(9, 15)
OPEN_END = time(9, 45)
MOMENTUM_START = time(9, 44)
ENTRY_BAR = time(9, 59)
EXIT_BAR = time(10, 59)


def _rupees(nanos: int) -> Decimal:
    return Decimal(nanos) / Decimal(1_000_000_000)


def _paise(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _whole(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def published_intraday_fees(buy: Decimal, sell: Decimal) -> dict[str, str]:
    """Current published NSE cash-intraday components, approximate contract note."""
    turnover = buy + sell
    brokerage = _paise(min(Decimal("20"), buy * Decimal("0.0003"))) + _paise(
        min(Decimal("20"), sell * Decimal("0.0003"))
    )
    transaction = _paise(turnover * Decimal("0.000030699"))
    sebi = _paise(turnover * Decimal("0.000001"))
    ipft = _paise(turnover * Decimal("0.000000001"))
    gst = _paise((brokerage + transaction + sebi + ipft) * Decimal("0.18"))
    stt = _whole(sell * Decimal("0.00025"))
    stamp = _whole(buy * Decimal("0.00003"))
    parts = {
        "brokerage": brokerage,
        "nse_transaction": transaction,
        "sebi": sebi,
        "ipft": ipft,
        "gst": gst,
        "stt": stt,
        "stamp": stamp,
    }
    parts["total"] = sum(parts.values(), Decimal(0))
    return {key: str(value) for key, value in parts.items()}


def _session_bars(
    bars: tuple[HistoricalBar, ...], symbol: str, security_id: str
) -> dict[str, dict[time, HistoricalBar]]:
    result: dict[str, dict[time, HistoricalBar]] = defaultdict(dict)
    previous = -1
    source_hash: str | None = None
    source_day: str | None = None
    for bar in bars:
        stamp = bar.source_timestamp_ns
        if (
            bar.instrument.symbol != symbol
            or bar.instrument.security_id != security_id
            or bar.instrument.segment != "NSE_EQ"
            or bar.interval_minutes != 1
            or bar.source != "DHAN_HISTORICAL"
            or bar.timestamp_semantics != "UNVERIFIED"
            or stamp is None
            or stamp <= previous
            or (
                source_hash is not None
                and source_hash != bar.source_sha256
                and source_day == bar.trading_date.isoformat()
            )
        ):
            raise ValueError("simulation source identity, order or provenance invalid")
        local = datetime.fromtimestamp(stamp / 1_000_000_000, IST)
        if local.date() != bar.trading_date or not time(9, 15) <= local.time() < time(
            15, 30
        ):
            raise ValueError("simulation bar outside dated cash session")
        day = bar.trading_date.isoformat()
        if local.time() in result[day]:
            raise ValueError("simulation duplicate minute")
        result[day][local.time()] = bar
        source_hash = bar.source_sha256
        source_day = day
        previous = stamp
    return result


def _reference_trade(
    day: str,
    symbol: str,
    entry: HistoricalBar,
    exit_bar: HistoricalBar,
    score_bps: Decimal,
    rule: str,
) -> dict[str, Any] | None:
    entry_price, exit_price = _rupees(entry.close_nanos), _rupees(exit_bar.close_nanos)
    shares = int(MAX_REFERENCE_NOTIONAL // entry_price)
    if shares < 1:
        return None
    buy, sell = entry_price * shares, exit_price * shares
    fees = published_intraday_fees(buy, sell)
    gross = _paise(sell - buy)
    friction = _paise(buy * FRICTION_BPS / Decimal(10_000))
    net = _paise(gross - Decimal(fees["total"]) - friction)
    return {
        "day": day,
        "symbol": symbol,
        "rule": rule,
        "status": "CLOSED_REFERENCE_ONLY",
        "decision_time_ist": "10:00:00",
        "entry_label_ist": "09:59:00",
        "exit_label_ist": "10:59:00",
        "shares": shares,
        "score_bps": str(score_bps.quantize(Decimal("0.001"))),
        "entry_close_reference_inr": str(entry_price),
        "exit_close_reference_inr": str(exit_price),
        "entry_notional_inr": str(buy),
        "gross_reference_inr": str(gross),
        "fee_components_inr": fees,
        "assumed_friction_bps": str(FRICTION_BPS),
        "assumed_friction_inr": str(friction),
        "net_reference_inr": str(net),
        "capital_utilization": str((buy / CAPITAL).quantize(Decimal("0.000001"))),
        "actual_fill": False,
    }


def _summary(day_rows: list[dict[str, Any]], days: list[str]) -> dict[str, Any]:
    chosen = {row["day"]: row for row in day_rows}
    equity, peak, max_drawdown = CAPITAL, CAPITAL, Decimal(0)
    curve: list[dict[str, str]] = []
    daily_net: dict[str, float] = {}
    for day in days:
        if day in chosen:
            trade = chosen[day]
            before = equity
            buy = Decimal(trade["entry_notional_inr"])
            trade["virtual_cash_after_reference_entry_inr"] = str(_paise(before - buy))
            trade["virtual_position_shares_at_reference_entry"] = trade["shares"]
        net = Decimal(chosen[day]["net_reference_inr"]) if day in chosen else Decimal(0)
        equity = _paise(equity + net)
        if day in chosen:
            trade["virtual_cash_after_reference_exit_inr"] = str(equity)
            trade["virtual_position_shares_after_reference_exit"] = 0
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        curve.append(
            {"day": day, "virtual_equity_inr": str(equity), "session_net_inr": str(net)}
        )
        daily_net[day] = float(net)
    ci = bootstrap_session_net(daily_net, min_sessions=5) if len(days) >= 5 else None
    return {
        "selected_sessions": len(chosen),
        "independent_sessions": len(days),
        "ending_virtual_capital_inr": str(equity),
        "mean_session_net_inr": str(_paise((equity - CAPITAL) / len(days))),
        "mean_capital_utilization": str(
            (
                sum(
                    (Decimal(row["capital_utilization"]) for row in day_rows),
                    Decimal(0),
                )
                / len(days)
            ).quantize(Decimal("0.000001"))
        ),
        "max_drawdown_inr": str(max_drawdown),
        "session_bootstrap_95pct_mean_net_inr": [
            str(_paise(Decimal(str(ci.confidence_low_nanos)))),
            str(_paise(Decimal(str(ci.confidence_high_nanos)))),
        ]
        if ci
        else None,
        "equity_curve": curve,
    }


def _best(
    items: list[tuple[Decimal, str, HistoricalBar, HistoricalBar]],
    ids: dict[str, str],
) -> tuple[Decimal, str, HistoricalBar, HistoricalBar] | None:
    return (
        sorted(items, key=lambda item: (-item[0], ids[item[1]]))[0] if items else None
    )


def simulate(
    bars_by_symbol: dict[str, tuple[HistoricalBar, ...]],
    universe: dict[str, Any],
) -> dict[str, Any]:
    """Replay fixed 10:00 rule; bar labels/completion are unverified assumptions."""
    instruments = universe.get("instruments")
    if (
        universe.get("schema_version") != "alpha-grid-cross-stock-universe-v1"
        or not isinstance(instruments, list)
        or len(instruments) != 25
    ):
        raise ValueError("fixed 25-stock universe required")
    ids = {item["symbol"]: item["security_id"] for item in instruments}
    if len(ids) != 25 or set(bars_by_symbol) - set(ids):
        raise ValueError("simulation universe mismatch")
    sessions = {
        symbol: _session_bars(bars, symbol, ids[symbol])
        for symbol, bars in bars_by_symbol.items()
    }
    days = sorted({day for group in sessions.values() for day in group})
    if not days:
        raise ValueError("no genuine source sessions")
    decisions: list[dict[str, str]] = []
    candidate: list[dict[str, Any]] = []
    momentum: list[dict[str, Any]] = []
    forced: list[dict[str, Any]] = []
    for day in days:
        breakout_candidates: list[
            tuple[Decimal, str, HistoricalBar, HistoricalBar]
        ] = []
        momentum_candidates: list[
            tuple[Decimal, str, HistoricalBar, HistoricalBar]
        ] = []
        diagnostics: list[tuple[Decimal, str, HistoricalBar, HistoricalBar]] = []
        reasons: dict[str, str] = {}
        for symbol in ids:
            session = sessions.get(symbol, {}).get(day)
            if session is None:
                reasons[symbol] = "NO_GENUINE_ARCHIVE"
                continue
            opening = [session.get(time(9, minute)) for minute in range(15, 45)]
            at_944, entry, exit_bar = (
                session.get(t) for t in (MOMENTUM_START, ENTRY_BAR, EXIT_BAR)
            )
            required = [
                time(9 + minute // 60, minute % 60) for minute in range(15, 120)
            ]
            if (
                any(t not in session for t in required)
                or any(bar is None for bar in opening)
                or at_944 is None
                or entry is None
                or exit_bar is None
            ):
                reasons[symbol] = "INCOMPLETE_OPEN_OR_60M_REFERENCE"
                continue
            assert entry is not None and exit_bar is not None and at_944 is not None
            opening_high = max(bar.high_nanos for bar in opening if bar is not None)
            breakout = (
                Decimal(entry.close_nanos - opening_high)
                * 10_000
                / Decimal(opening_high)
            )
            move = (
                Decimal(entry.close_nanos - at_944.close_nanos)
                * 10_000
                / Decimal(at_944.close_nanos)
            )
            diagnostics.append((move, symbol, entry, exit_bar))
            if entry.close_nanos > opening_high:
                breakout_candidates.append((breakout, symbol, entry, exit_bar))
            else:
                reasons[symbol] = "NO_OPENING_RANGE_BREAKOUT"
            if move > 0:
                momentum_candidates.append((move, symbol, entry, exit_bar))

        selected = _best(breakout_candidates, ids)
        base = _best(momentum_candidates, ids)
        selected_trade = (
            _reference_trade(
                day,
                selected[1],
                selected[2],
                selected[3],
                selected[0],
                "OPENING_RANGE_BREAKOUT",
            )
            if selected
            else None
        )
        for symbol in ids:
            if symbol in reasons:
                decision = reasons[symbol]
            elif selected is not None and symbol == selected[1]:
                decision = (
                    "RULE_SELECTED_REFERENCE"
                    if selected_trade
                    else "INSUFFICIENT_VIRTUAL_CAPITAL"
                )
            else:
                decision = "REJECTED_LOWER_BREAKOUT_RANK"
            decisions.append(
                {
                    "day": day,
                    "symbol": symbol,
                    "security_id": ids[symbol],
                    "decision": decision,
                }
            )
        if selected:
            if selected_trade:
                candidate.append(selected_trade)
        elif diagnostics:
            diag = _best(diagnostics, ids)
            assert diag is not None
            row = _reference_trade(
                day, diag[1], diag[2], diag[3], diag[0], "FORCED_DIAGNOSTIC_EXCLUDED"
            )
            if row:
                forced.append(row)
        if base:
            row = _reference_trade(
                day, base[1], base[2], base[3], base[0], "TRAILING_15M_MOMENTUM"
            )
            if row:
                momentum.append(row)
    candidate_summary = _summary(candidate, days)
    momentum_summary = _summary(momentum, days)
    c_by_day = {row["day"]: Decimal(row["net_reference_inr"]) for row in candidate}
    m_by_day = {row["day"]: Decimal(row["net_reference_inr"]) for row in momentum}
    paired = {
        day: float(c_by_day.get(day, Decimal(0)) - m_by_day.get(day, Decimal(0)))
        for day in days
    }
    ci = bootstrap_session_net(paired, min_sessions=5) if len(days) >= 5 else None
    return {
        "schema_version": "alpha-grid-simulation-v1",
        "mode": "SIMULATION_ONLY",
        "source_type": "VERIFIED_DHAN_HISTORICAL_OHLCV",
        "universe_asof": universe["selection_asof"],
        "source_hashes": {
            symbol: sorted({bar.source_sha256 for bar in bars})
            for symbol, bars in bars_by_symbol.items()
        },
        "timing_assumption": "START_LABEL_AND_COMPLETION_BY_NEXT_MINUTE_UNVERIFIED",
        "eligibility": "EXPLORATORY_MECHANICS_ONLY",
        "reference_prices_are_fills": False,
        "virtual_starting_capital_inr": str(CAPITAL),
        "reference_notional_cap_inr": str(MAX_REFERENCE_NOTIONAL),
        "fixed_rule": (
            "09:15-09:44 opening high; at 10:00 use 09:59 close > opening high; "
            "highest excess bps, one per session; 10:59 close reference exit"
        ),
        "friction_assumption_bps": str(FRICTION_BPS),
        "fee_source": "https://dhan.co/pricing/",
        "sessions": days,
        "available_symbols": sorted(bars_by_symbol),
        "missing_symbols": sorted(set(ids) - set(bars_by_symbol)),
        "candidate": candidate_summary,
        "momentum": momentum_summary,
        "no_trade": {
            "ending_virtual_capital_inr": str(CAPITAL),
            "selected_sessions": 0,
        },
        "paired_candidate_minus_momentum_95pct_inr": [
            str(_paise(Decimal(str(ci.confidence_low_nanos)))),
            str(_paise(Decimal(str(ci.confidence_high_nanos)))),
        ]
        if ci
        else None,
        "trade_ledger": candidate,
        "momentum_ledger": momentum,
        "forced_diagnostics_excluded": forced,
        "decisions": decisions,
        "decision_counts": dict(Counter(row["decision"] for row in decisions)),
        "virtual_positions_end": [],
        "paper_orders": 0,
        "qp01_writes": 0,
        "broker_orders": 0,
        "evaluation_status": "NO_UNTOUCHED_CROSS_STOCK_HOLDOUT",
    }


def main() -> None:
    from quant.acquisition.dhan import (
        _private_directory,
        _sync_directory,
        _write_private_bytes,
        response_path,
        verify_saved_response,
    )
    from quant.apps.cockpit_cache import MAC_EXPECTED_SHA256, MAC_REQUEST, MAC_ROOT

    parser = argparse.ArgumentParser(
        description="Verify and simulate pinned Dhan history offline"
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--six-month",
        action="store_true",
        help="Replay three pinned RELIANCE responses as exploratory mechanics",
    )
    args = parser.parse_args()

    if args.six_month:
        from quant.apps.descriptive_dhan_opportunities import (
            load_verified_bars,
            validated_sessions,
        )

        bars, _ = load_verified_bars()
        validated_sessions(bars)
    else:
        bars = verify_saved_response(
            MAC_REQUEST, response_path(MAC_REQUEST, MAC_ROOT)
        ).bars
        if not bars or bars[0].source_sha256 != MAC_EXPECTED_SHA256:
            raise ValueError("pinned Mac historical archive unavailable")
    report = simulate({"RELIANCE": bars}, json.loads(UNIVERSE_PATH.read_text()))
    encoded = (json.dumps(report, indent=2) + "\n").encode()
    if args.output is not None:
        _private_directory(args.output.parent)
        _write_private_bytes(args.output, encoded)
        _sync_directory(args.output.parent)
        print(
            json.dumps(
                {
                    "mode": "SIMULATION_ONLY",
                    "output": str(args.output),
                    "sessions": len(report["sessions"]),
                }
            )
        )
    else:
        print(encoded.decode(), end="")


if __name__ == "__main__":
    main()
