"""Local SIMULATION_ONLY worker over durable, read-only gateway snapshots.

No Dhan or broker endpoint is used here. The gateway owns feed capture/QJ01.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import stat
import time
from collections import defaultdict, deque
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from quant.acquisition.calendar import IST
from quant.apps.historical_simulator import (
    CAPITAL,
    MAX_REFERENCE_NOTIONAL,
    published_intraday_fees,
)

VERSION = "alpha-grid-live-simulation-v1"
MAX_AGE_NS = 2_000_000_000
LOOKBACK_NS = 15 * 60_000_000_000
HOLD_NS = 30 * 60_000_000_000
FRICTION_BPS = Decimal("40")
PORTFOLIO_VERSION = "alpha-grid-live-portfolio-simulation-v2"
# These values mirror cash-paper-risk-v1 defaults. The v1 ledger is untouched.
PORTFOLIO_CAPITAL = Decimal("20000")
PORTFOLIO_MAX_DEPLOYED = Decimal("15000")
PORTFOLIO_MAX_INSTRUMENT = Decimal("5000")
PORTFOLIO_MAX_POSITIONS = 3
PORTFOLIO_SESSION_LOSS = Decimal("500")
PORTFOLIO_DECISION_LOSS = Decimal("200")
PORTFOLIO_ADVERSE_BPS = Decimal("200")
PORTFOLIO_MAX_SPREAD_BPS = Decimal("25")
PORTFOLIO_RESIDUAL_FRICTION_BPS = Decimal("20")


class Ledger:
    def __init__(self, path: Path, version: str = VERSION) -> None:
        self.path = path
        self.version = version
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT |
                     getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("simulation ledger is not private regular file")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.file = os.fdopen(fd, "a+b", buffering=0)
        except BaseException:
            os.close(fd)
            raise
        self.events: list[dict] = []
        previous = "0" * 64
        try:
            self.file.seek(0)
            original = self.file.read()
            if original and not original.endswith(b"\n"):
                raise ValueError("simulation ledger partial final record")
            for line in original.splitlines():
                row = json.loads(line)
                digest = row.pop("hash")
                if (
                    row.get("schema_version") != version
                    or row.get("previous") != previous
                    or row.get("sequence") != len(self.events) + 1
                ):
                    raise ValueError("simulation ledger chain invalid")
                payload = json.dumps(
                    row, sort_keys=True, separators=(",", ":")
                ).encode()
                if hashlib.sha256(payload).hexdigest() != digest:
                    raise ValueError("simulation ledger hash invalid")
                row["hash"] = digest
                self.events.append(row)
                previous = digest
            self.previous = previous
        except BaseException:
            self.file.close()
            raise

    def append(self, kind: str, **fields: object) -> dict:
        row = {
            "schema_version": self.version,
            "sequence": len(self.events) + 1,
            "previous": self.previous,
            "kind": kind,
            **fields,
        }
        payload = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        row["hash"] = hashlib.sha256(payload).hexdigest()
        encoded = (
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        )
        if self.file.write(encoded) != len(encoded):
            raise OSError("simulation ledger short write")
        os.fsync(self.file.fileno())
        self.events.append(row)
        self.previous = row["hash"]
        return row

    def close(self) -> None:
        self.file.close()


class Simulator:
    def __init__(self, ledger: Ledger, universe: dict) -> None:
        if (
            universe.get("schema_version") != "alpha-grid-cross-stock-universe-v1"
            or len(universe.get("instruments", [])) != 25
        ):
            raise ValueError("fixed 25-stock manifest required")
        self.ids = {x["security_id"]: x["symbol"] for x in universe["instruments"]}
        if len(self.ids) != 25:
            raise ValueError("duplicate universe ID")
        self.ledger = ledger
        self.cash = CAPITAL
        self.position: dict | None = None
        self.observations: dict[str, deque] = defaultdict(deque)
        self.seen: set[str] = set()
        self.selections: dict[str, int] = defaultdict(int)
        self.equity = CAPITAL
        self.peak = CAPITAL
        self.drawdown = Decimal(0)
        self.feed_session: str | None = None
        self.stale = True
        self.rejected = 0
        self.trades: deque[dict] = deque(maxlen=100)
        self.decisions: deque[dict] = deque(maxlen=100)
        self.curve: deque[dict] = deque(maxlen=100)
        self.decision_slots: set[str] = set()
        self.realized_equity = CAPITAL
        for event in ledger.events:
            self._apply(event)

    def _apply(self, event: dict) -> None:
        kind = event["kind"]
        if kind == "OBSERVED":
            self.seen.add(event["id"])
            self.observations[event["symbol"]].append(event)
            self.feed_session = event["feed_session"]
        elif kind == "ENTRY":
            if self.position is not None:
                raise ValueError("duplicate virtual entry")
            self.cash = Decimal(event["cash_after"])
            self.position = event
            self.selections[event["day"]] += 1
        elif kind == "EXIT":
            if (
                self.position is None
                or event["entry_sequence"] != self.position["sequence"]
            ):
                raise ValueError("orphan virtual exit")
            self.cash = Decimal(event["cash_after"])
            self.position = None
            self.equity = self.cash
            self.peak = max(self.peak, self.equity)
            self.drawdown = max(self.drawdown, self.peak - self.equity)
            self.realized_equity += Decimal(event["net_reference"])
            self.curve.append(
                {
                    "day": event["day"],
                    "virtual_equity_inr": str(self.realized_equity),
                    "session_net_inr": event["net_reference"],
                }
            )
            self.trades.append(event)
        elif kind == "REJECTED":
            self.rejected += 1
        elif kind == "GAP":
            self.stale = True
            self.observations.clear()
            if event["reason"] == "FEED_SESSION_CHANGED":
                self.feed_session = None
        elif kind == "STOP":
            self.stale = True
            self.observations.clear()
        if kind in {"CANDIDATE", "ENTRY", "REJECTED", "GAP"}:
            self.decisions.append(event)
        if event.get("decision_slot"):
            self.decision_slots.add(event["decision_slot"])

    def emit(self, kind: str, **fields: object) -> dict:
        row = self.ledger.append(kind, **fields)
        self._apply(row)
        return row

    def gap(self, reason: str, now_ns: int) -> None:
        if not self.stale:
            self.emit("GAP", reason=reason, observed_at_ns=now_ns)
        self.stale = True
        self.observations.clear()
        if reason == "FEED_SESSION_CHANGED":
            self.feed_session = None

    def ingest(self, snapshot: dict, now_ns: int) -> None:
        if (
            snapshot.get("mode") != "OBSERVE_ONLY"
            or not snapshot.get("feed_connected")
            or snapshot.get("journal_blocked")
        ):
            self.gap("FEED_DISCONNECTED_OR_STORAGE_BLOCKED", now_ns)
            return
        scoped = {
            item.get("instrument", {}).get("security_id"): item.get(
                "instrument", {}
            ).get("symbol")
            for item in snapshot.get("instruments", [])
        }
        if scoped != self.ids:
            self.gap("FIXED_UNIVERSE_FEED_UNAVAILABLE", now_ns)
            return
        session = snapshot.get("session_id")
        if (
            not isinstance(session, str)
            or not session
            or (self.feed_session and self.feed_session != session)
        ):
            self.gap("FEED_SESSION_CHANGED", now_ns)
            return
        fresh = []
        current_quote = False
        for item in snapshot.get("instruments", []):
            instrument = item.get("instrument") or {}
            sid = instrument.get("security_id")
            market = item.get("market") or {}
            provenance = market.get("provenance") or {}
            quality = market.get("quality") or {}
            if sid not in self.ids or instrument.get("symbol") != self.ids[sid]:
                continue
            if market.get("freshness") != "FRESH" or any(
                quality.get(x)
                for x in ("stale", "cross_feed_uncertain", "book_pairing_unverified")
            ):
                continue
            try:
                received = int(market["source_receive_timestamp_ns"])
                bid = int(market["best_bid_price_nanos"])
                ask = int(market["best_ask_price_nanos"])
            except (KeyError, TypeError, ValueError):
                continue
            if not 0 <= now_ns - received <= MAX_AGE_NS or not 0 < bid < ask:
                continue
            ordinal = provenance.get("arrival_ordinal")
            if (
                provenance.get("feed_session_id") != session
                or provenance.get("source_protocol") != "DhanStandardV2"
                or not ordinal
            ):
                continue
            current_quote = True
            identity = f"{session}:{sid}:{ordinal}"
            if identity in self.seen:
                continue
            row = self.emit(
                "OBSERVED",
                id=identity,
                symbol=self.ids[sid],
                security_id=sid,
                feed_session=session,
                arrival_ordinal=str(ordinal),
                receive_ns=received,
                observed_at_ns=now_ns,
                bid_nanos=bid,
                ask_nanos=ask,
            )
            fresh.append(row)
            history = self.observations[row["symbol"]]
            while (
                history
                and received - history[0]["receive_ns"] > LOOKBACK_NS + 60_000_000_000
            ):
                history.popleft()
        if not current_quote:
            self.gap("NO_FRESH_QUOTE", now_ns)
            return
        if not fresh:
            return
        self.feed_session = session
        self.stale = False
        if self.position:
            current = next(
                (r for r in fresh if r["symbol"] == self.position["symbol"]), None
            )
            if (
                current
                and datetime.fromtimestamp(current["receive_ns"] / 1_000_000_000, IST)
                .date()
                .isoformat()
                == self.position["day"]
                and current["receive_ns"] - self.position["receive_ns"] >= HOLD_NS
            ):
                self._exit(current)
            return
        local = datetime.fromtimestamp(now_ns / 1_000_000_000, IST)
        if (
            not (9 <= local.hour <= 14)
            or (local.hour == 9 and local.minute < 30)
            or (local.hour == 14 and local.minute > 30)
        ):
            return
        day = local.date().isoformat()
        if self.selections[day] >= 3 or local.second > 4:
            return
        candidates = []
        for row in fresh:
            decision_slot = f"{day}:{local.hour:02}:{local.minute:02}:{row['symbol']}"
            if decision_slot in self.decision_slots:
                continue
            history = self.observations[row["symbol"]]
            old = next(
                (
                    r
                    for r in history
                    if LOOKBACK_NS
                    <= row["receive_ns"] - r["receive_ns"]
                    <= LOOKBACK_NS + 60_000_000_000
                ),
                None,
            )
            if not old:
                continue
            mid = (row["bid_nanos"] + row["ask_nanos"]) // 2
            old_mid = (old["bid_nanos"] + old["ask_nanos"]) // 2
            spread_bps = Decimal(row["ask_nanos"] - row["bid_nanos"]) * 10000 / mid
            momentum_bps = Decimal(mid - old_mid) * 10000 / old_mid
            if spread_bps <= 10 and momentum_bps >= 15:
                candidates.append((momentum_bps, row["security_id"], row))
                self.emit(
                    "CANDIDATE",
                    day=day,
                    symbol=row["symbol"],
                    observation_id=row["id"],
                    decision_slot=decision_slot,
                    momentum_bps=str(momentum_bps.quantize(Decimal("0.001"))),
                    quoted_spread_bps=str(spread_bps.quantize(Decimal("0.001"))),
                )
            else:
                self.emit(
                    "REJECTED",
                    day=day,
                    symbol=row["symbol"],
                    reason="SPREAD_OR_MOMENTUM",
                    observation_id=row["id"],
                    decision_slot=decision_slot,
                )
        if candidates:
            _, _, chosen = sorted(candidates, key=lambda x: (-x[0], x[1]))[0]
            for _, _, rejected in candidates:
                if rejected["id"] != chosen["id"]:
                    self.emit(
                        "REJECTED",
                        day=day,
                        symbol=rejected["symbol"],
                        reason="LOWER_CANDIDATE_RANK",
                        observation_id=rejected["id"],
                    )
            self._enter(chosen, day)

    def _enter(self, row: dict, day: str) -> None:
        ask = Decimal(row["ask_nanos"]) / 1_000_000_000
        shares = int(min(self.cash, MAX_REFERENCE_NOTIONAL) // ask)
        if shares < 1:
            self.emit(
                "REJECTED",
                day=day,
                symbol=row["symbol"],
                reason="CAPITAL_LIMIT",
                observation_id=row["id"],
            )
            return
        notional = (ask * shares).quantize(Decimal("0.01"))
        self.emit(
            "ENTRY",
            day=day,
            symbol=row["symbol"],
            security_id=row["security_id"],
            observation_id=row["id"],
            receive_ns=row["receive_ns"],
            shares=shares,
            ask_reference=str(ask),
            buy_notional=str(notional),
            cash_after=str(self.cash - notional),
            actual_fill=False,
        )

    def _exit(self, row: dict) -> None:
        assert self.position is not None
        entry = self.position
        bid = Decimal(row["bid_nanos"]) / 1_000_000_000
        sell = (bid * entry["shares"]).quantize(Decimal("0.01"))
        buy = Decimal(entry["buy_notional"])
        fees = published_intraday_fees(buy, sell)
        friction = (buy * FRICTION_BPS / 10000).quantize(Decimal("0.01"))
        net = sell - buy - Decimal(fees["total"]) - friction
        self.emit(
            "EXIT",
            day=entry["day"],
            symbol=entry["symbol"],
            entry_sequence=entry["sequence"],
            observation_id=row["id"],
            bid_reference=str(bid),
            sell_notional=str(sell),
            fees=fees,
            assumed_friction=str(friction),
            net_reference=str(net),
            cash_after=str(self.cash + sell - Decimal(fees["total"]) - friction),
            actual_fill=False,
        )

    def status(self, state: str) -> dict:
        quotes = []
        for symbol, history in self.observations.items():
            if history:
                row = history[-1]
                quotes.append(
                    {
                        "symbol": symbol,
                        "bid_nanos": str(row["bid_nanos"]),
                        "ask_nanos": str(row["ask_nanos"]),
                        "receive_ns": str(row["receive_ns"]),
                    }
                )
        return {
            "schema_version": VERSION,
            "state": state,
            "feed_status": "STALE" if self.stale else "OBSERVING",
            "universe_count": 25,
            "capital_start_inr": str(CAPITAL),
            "cash_inr": str(self.cash),
            "equity_inr": str(
                self.equity
                if not self.position
                else self.cash + Decimal(self.position["buy_notional"])
            ),
            "max_drawdown_inr": str(self.drawdown),
            "position": self.position,
            "trades": list(self.trades),
            "decisions": list(self.decisions),
            "equity_curve": list(self.curve),
            "observed_quotes": sorted(quotes, key=lambda row: row["symbol"]),
            "rejected_count": self.rejected,
            "ledger_events": len(self.ledger.events),
            "source": "LOCAL_GATEWAY_AFTER_QJ01",
            "actual_fills": 0,
            "broker_orders": 0,
        }


class PortfolioSimulator:
    """Concurrent virtual positions over eligible, journaled quote snapshots."""

    def __init__(self, ledger: Ledger, universe: dict) -> None:
        rows = universe.get("instruments", [])
        if universe.get("schema_version") != "alpha-grid-cross-stock-universe-v1" or len(rows) != 25:
            raise ValueError("fixed 25-stock universe required")
        self.ids = {row["security_id"]: row["symbol"] for row in rows}
        if len(self.ids) != 25 or ledger.version != PORTFOLIO_VERSION:
            raise ValueError("portfolio ledger or universe mismatch")
        self.ledger = ledger
        self.cash = PORTFOLIO_CAPITAL
        self.positions: dict[str, dict] = {}
        self.observations: dict[str, deque] = defaultdict(deque)
        self.seen: set[str] = set()
        self.decision_slots: set[str] = set()
        self.session_net: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
        self.trades: deque[dict] = deque(maxlen=100)
        self.decisions: deque[dict] = deque(maxlen=200)
        self.curve: deque[dict] = deque(maxlen=100)
        self.rejected = 0
        self.feed_session: str | None = None
        self.stale = True
        self.last_gap_reason: str | None = None
        self.peak = PORTFOLIO_CAPITAL
        self.drawdown = Decimal(0)
        for event in ledger.events:
            self._apply(event)

    def _apply(self, event: dict) -> None:
        kind = event["kind"]
        symbol = event.get("symbol")
        if kind == "OBSERVED":
            self.seen.add(event["id"])
            self.observations[symbol].append(event)
            self.feed_session = event["feed_session"]
        elif kind == "BUY":
            if symbol in self.positions:
                raise ValueError("duplicate virtual position")
            self.positions[symbol] = {**event, "remaining_shares": event["shares"],
                                      "buy_fee_remaining_inr": event.get("buy_fee_inr", "0"),
                                      "reduced": False}
            self.cash = Decimal(event["cash_after_inr"])
        elif kind in {"REDUCE", "SELL"}:
            position = self.positions.get(symbol)
            if position is None or event["entry_sequence"] != position["sequence"]:
                raise ValueError("orphan virtual disposal")
            if event["shares_sold"] < 1 or event["shares_sold"] > position["remaining_shares"]:
                raise ValueError("virtual disposal shares invalid")
            remaining = position["remaining_shares"] - event["shares_sold"]
            if remaining != event["remaining_shares"] or (kind == "SELL") != (remaining == 0):
                raise ValueError("virtual disposal remaining shares invalid")
            self.cash = Decimal(event["cash_after_inr"])
            self.session_net[event["day"]] += Decimal(event["net_reference_inr"])
            if kind == "SELL":
                del self.positions[symbol]
                self.trades.append(event)
            else:
                position["remaining_shares"] = remaining
                position["buy_fee_remaining_inr"] = event["buy_fee_remaining_inr"]
                position["reduced"] = True
            equity = self.cash + sum(
                Decimal(row["ask_reference_inr"]) * row["remaining_shares"]
                for row in self.positions.values()
            )
            self.peak = max(self.peak, equity)
            self.drawdown = max(self.drawdown, self.peak - equity)
            self.curve.append({"day": event["day"], "sequence": event["sequence"],
                               "virtual_book_equity_inr": str(equity),
                               "realized_session_net_inr": str(self.session_net[event["day"]])})
        elif kind == "REJECT":
            self.rejected += 1
        elif kind == "GAP":
            self.stale = True
            self.last_gap_reason = event["reason"]
            self.observations.clear()
            if event["reason"] == "FEED_SESSION_CHANGED":
                self.feed_session = None
        elif kind == "STOP":
            self.stale = True
            self.observations.clear()
        if kind in {"BUY", "SELL", "REDUCE", "HOLD", "REJECT", "GAP"}:
            self.decisions.append(event)
        if event.get("decision_slot"):
            self.decision_slots.add(event["decision_slot"])

    def emit(self, kind: str, **fields: object) -> dict:
        row = self.ledger.append(kind, **fields)
        self._apply(row)
        return row

    def gap(self, reason: str, now_ns: int) -> None:
        if not self.stale or self.last_gap_reason != reason:
            self.emit("GAP", reason=reason, observed_at_ns=now_ns)
        self.stale = True
        self.observations.clear()
        if reason == "FEED_SESSION_CHANGED":
            self.feed_session = None

    def _reject_once(self, symbol: str, slot: str, reason: str, observation_id: str | None = None) -> None:
        if slot not in self.decision_slots:
            self.emit("REJECT", symbol=symbol, decision_slot=slot, reason=reason,
                      observation_id=observation_id)

    @staticmethod
    def _rest_book(rest: dict | None, sid: str, received_ns: int,
                   now_ns: int) -> tuple[int, int, int, str, int, int] | None:
        if not isinstance(rest, dict) or rest.get("source") != "REST_SNAPSHOT":
            return None
        try:
            rest_ns = int(datetime.fromisoformat(
                rest["received_at"].replace("Z", "+00:00")
            ).timestamp() * 1_000_000_000)
            rest_hash = rest["sha256"]
            quotes = rest["quotes"]
            if (rest.get("http_status") != 200 or
                    not isinstance(rest.get("response_bytes"), int) or
                    rest["response_bytes"] <= 0 or
                    not 0 <= now_ns - rest_ns <= MAX_AGE_NS or
                    abs(rest_ns - received_ns) > MAX_AGE_NS or
                    not isinstance(rest_hash, str) or len(rest_hash) != 64 or
                    any(c not in "0123456789abcdef" for c in rest_hash) or
                    not isinstance(quotes, list) or
                    len({row["security_id"] for row in quotes}) != len(quotes)):
                return None
            match = next((row for row in quotes if row["security_id"] == sid), None)
            if match is None:
                return None
            depth = match["depth"]
            bid = next(row for row in depth["buy"]
                       if Decimal(str(row["price"])) > 0 and row["quantity"] > 0)
            ask = next(row for row in depth["sell"]
                       if Decimal(str(row["price"])) > 0 and row["quantity"] > 0)
            bid_nanos = int(Decimal(str(bid["price"])) * 1_000_000_000)
            ask_nanos = int(Decimal(str(ask["price"])) * 1_000_000_000)
            if not 0 < bid_nanos < ask_nanos:
                return None
            return bid_nanos, ask_nanos, rest_ns, rest_hash, int(bid["quantity"]), int(ask["quantity"])
        except (KeyError, TypeError, ValueError, StopIteration, ArithmeticError):
            return None

    def _old(self, row: dict) -> dict | None:
        return next((old for old in self.observations[row["symbol"]]
                     if LOOKBACK_NS <= row["receive_ns"] - old["receive_ns"]
                     <= LOOKBACK_NS + 60_000_000_000), None)

    @staticmethod
    def _risk_bound(ask: Decimal, shares: int, spread_bps: Decimal) -> Decimal:
        notional = ask * shares
        fees = Decimal(published_intraday_fees(notional, notional)["total"])
        return notional * (PORTFOLIO_ADVERSE_BPS + PORTFOLIO_RESIDUAL_FRICTION_BPS
                           + spread_bps) / 10_000 + fees

    def _open_risk(self) -> Decimal:
        return sum((Decimal(position.get("risk_bound_inr") or self._risk_bound(
                        Decimal(position["ask_reference_inr"]), position["shares"],
                        Decimal(position["quoted_spread_bps"])))
                    * position["remaining_shares"] / position["shares"]
                    for position in self.positions.values()), Decimal(0))

    def _dispose(self, row: dict, reason: str, *, reduce: bool, slot: str) -> None:
        position = self.positions[row["symbol"]]
        remaining = position["remaining_shares"]
        quantity = max(1, remaining // 2) if reduce and remaining > 1 else remaining
        if row.get("bid_liquidity_quantity") is not None and row["bid_liquidity_quantity"] < quantity:
            self._reject_once(row["symbol"], slot, "DISPLAYED_EXIT_LIQUIDITY_INSUFFICIENT", row["id"])
            return
        kind = "REDUCE" if quantity < remaining else "SELL"
        bid = Decimal(row["bid_nanos"]) / 1_000_000_000
        ask = Decimal(position["ask_reference_inr"])
        buy, sell = ask * quantity, bid * quantity
        buy_fee_remaining = Decimal(position["buy_fee_remaining_inr"])
        allocated_buy_fee = (buy_fee_remaining if quantity == remaining else
                             (buy_fee_remaining * quantity / remaining).quantize(Decimal("0.01")))
        sell_fee = Decimal(published_intraday_fees(Decimal(0), sell)["total"])
        total_fees = allocated_buy_fee + sell_fee
        residual = (buy * PORTFOLIO_RESIDUAL_FRICTION_BPS / 10_000).quantize(Decimal("0.01"))
        net = sell - buy - total_fees - residual
        self.emit(kind, day=datetime.fromtimestamp(row["receive_ns"] / 1_000_000_000,
                                               IST).date().isoformat(),
                  entry_day=position["day"], symbol=row["symbol"],
                  entry_sequence=position["sequence"], observation_id=row["id"],
                  decision_slot=slot, reason=reason, shares_sold=quantity,
                  remaining_shares=remaining - quantity, bid_reference_inr=str(bid),
                  sell_notional_inr=str(sell), fees={"buy": str(allocated_buy_fee),
                                                     "sell": str(sell_fee),
                                                     "total": str(total_fees)},
                  buy_fee_remaining_inr=str(buy_fee_remaining - allocated_buy_fee),
                  residual_friction_inr=str(residual), net_reference_inr=str(net),
                  cash_after_inr=str(self.cash + sell - sell_fee - residual),
                  actual_fill=False)

    def ingest(self, snapshot: dict, now_ns: int, rest_snapshot: dict | None = None) -> None:
        if (snapshot.get("mode") != "OBSERVE_ONLY" or not snapshot.get("feed_connected")
                or snapshot.get("journal_blocked")):
            self.gap("FEED_DISCONNECTED_OR_STORAGE_BLOCKED", now_ns)
            return
        reasons = set(snapshot.get("no_trade_reasons") or [])
        if reasons & {"RAW_CAPTURE_VALIDATION_PENDING", "SESSION_CLASSIFICATION_UNVERIFIED"}:
            self.gap("RESEARCH_VALIDATION_PENDING", now_ns)
            return
        session = snapshot.get("session_id")
        if not isinstance(session, str) or not session:
            self.gap("FEED_SESSION_INVALID", now_ns)
            return
        if self.feed_session and self.feed_session != session:
            self.gap("FEED_SESSION_CHANGED", now_ns)
            return
        items = snapshot.get("instruments") or []
        observed_ids = [item.get("instrument", {}).get("security_id") for item in items]
        if (len(observed_ids) != len(set(observed_ids)) or
                any(sid not in self.ids or item.get("instrument", {}).get("symbol") != self.ids[sid]
                    for sid, item in zip(observed_ids, items, strict=True))):
            self.gap("INSTRUMENT_ROUTING_INVALID", now_ns)
            return
        local_now = datetime.fromtimestamp(now_ns / 1_000_000_000, IST)
        for sid, symbol in self.ids.items():
            if sid not in observed_ids:
                slot = f"{local_now.date()}:{local_now.hour:02}:{local_now.minute:02}:{symbol}"
                self._reject_once(symbol, slot, "MISSING_INSTRUMENT_SNAPSHOT")
        fresh = []
        for item in items:
            instrument = item["instrument"]
            sid = instrument["security_id"]
            symbol = self.ids[sid]
            market = item.get("market") or {}
            provenance = market.get("provenance") or {}
            quality = market.get("quality") or {}
            local = datetime.fromtimestamp(now_ns / 1_000_000_000, IST)
            slot = f"{local.date()}:{local.hour:02}:{local.minute:02}:{symbol}"
            if market.get("packet_kind") not in {"QUOTE", "FULL"}:
                self._reject_once(symbol, slot, "RAW_OR_UNKNOWN_PACKET_QUARANTINED")
                continue
            if (market.get("freshness") != "FRESH" or
                    any(quality.get(key) for key in
                        ("stale", "cross_feed_uncertain", "book_pairing_unverified"))):
                self._reject_once(symbol, slot, "STALE_OR_UNVERIFIED_QUOTE")
                continue
            try:
                received = int(market["source_receive_timestamp_ns"])
                durable_at = int(provenance["raw_durable_at_ns"])
                frame_id = int(provenance["source_frame_id"])
                packet_offset = int(provenance["packet_offset"])
                raw_hash = provenance["raw_frame_sha256"]
            except (KeyError, TypeError, ValueError):
                self._reject_once(symbol, slot, "QUOTE_FIELDS_MISSING")
                continue
            if (not 0 <= now_ns - received <= MAX_AGE_NS or
                    not received <= durable_at <= now_ns or frame_id < 1 or
                    packet_offset < 0 or not isinstance(raw_hash, str) or
                    len(raw_hash) != 64 or any(c not in "0123456789abcdef" for c in raw_hash) or
                    provenance.get("feed_session_id") != session or
                    provenance.get("source_protocol") != "DhanStandardV2" or
                    not provenance.get("arrival_ordinal")):
                self._reject_once(symbol, slot, "QUOTE_FRESHNESS_OR_PROVENANCE")
                continue
            rest_evidence = None
            book_metrics = None
            book_bids = None
            book_asks = None
            bid_liquidity = None
            ask_liquidity = None
            try:
                bid = int(market["best_bid_price_nanos"])
                ask = int(market["best_ask_price_nanos"])
            except (KeyError, TypeError, ValueError):
                if market.get("packet_kind") == "FULL":
                    self._reject_once(symbol, slot, "INVALID_FULL_BOOK", None)
                    continue
                rest_evidence = self._rest_book(rest_snapshot, sid, received, now_ns)
                if rest_evidence is None:
                    self._reject_once(symbol, slot, "FRESH_VERIFIED_BOOK_UNAVAILABLE")
                    continue
                bid, ask, rest_ns, rest_hash, bid_liquidity, ask_liquidity = rest_evidence
            if not 0 < bid < ask:
                self._reject_once(symbol, slot, "CROSSED_OR_EMPTY_BOOK")
                continue
            depth = item.get("depth") or {}
            depth_source = depth.get("source")
            if market.get("packet_kind") == "FULL":
                try:
                    book_bids = depth["bids"]
                    book_asks = depth["asks"]
                    book_metrics = depth.get("book_metrics") or {}
                    depth_provenance = depth["provenance"]
                    if (depth_source != "STANDARD_FULL" or
                            depth.get("derived_pairing") is not False or
                            any(depth_provenance.get(key) != provenance.get(key) for key in
                                ("feed_session_id", "source_protocol", "source_frame_id",
                                 "packet_offset", "raw_frame_sha256", "raw_durable_at_ns")) or
                            int(depth["receive_timestamp_ns"]) != received or
                            len(book_bids) != 5 or len(book_asks) != 5 or
                            int(book_bids[0]["price_nanos"]) != bid or
                            int(book_asks[0]["price_nanos"]) != ask):
                        raise ValueError("Full packet book mismatch")
                    bid_prices = [int(level["price_nanos"]) for level in book_bids]
                    ask_prices = [int(level["price_nanos"]) for level in book_asks]
                    bid_total = sum(int(level["displayed_quantity"]) for level in book_bids)
                    ask_total = sum(int(level["displayed_quantity"]) for level in book_asks)
                    bid_liquidity = int(book_bids[0]["displayed_quantity"])
                    ask_liquidity = int(book_asks[0]["displayed_quantity"])
                    if (not all(left > right for left, right in zip(bid_prices, bid_prices[1:])) or
                            not all(left < right for left, right in zip(ask_prices, ask_prices[1:])) or
                            bid_prices[0] >= ask_prices[0] or
                            min(bid_liquidity, ask_liquidity) <= 0):
                        raise ValueError("Book levels invalid or crossed")
                    if (bid_total != int(book_metrics["bid_quantity"]) or
                            ask_total != int(book_metrics["ask_quantity"])):
                        raise ValueError("Full packet book totals mismatch")
                except (KeyError, TypeError, ValueError, IndexError):
                    self._reject_once(symbol, slot, "INVALID_FULL_BOOK")
                    continue
            # DEPTH_20 is independently received and has no raw frame binding in
            # the shared snapshot. It remains descriptive in Rust/UI and cannot
            # supply portfolio liquidity, book metrics, or entry prices.
            identity = f"{session}:{sid}:{provenance['arrival_ordinal']}"
            if identity in self.seen:
                continue
            row = self.emit("OBSERVED", id=identity, symbol=symbol, security_id=sid,
                            feed_session=session, receive_ns=received, observed_at_ns=now_ns,
                            provider_last_trade_ns=provenance.get("source_last_trade_timestamp_ns"),
                            availability_ns=now_ns,
                            raw_durable_at_ns=durable_at,
                            rest_received_ns=rest_evidence[2] if rest_evidence else None,
                            book_source="REST_SNAPSHOT" if rest_evidence else "LIVE_WS",
                            book_metrics=book_metrics, book_bids=book_bids, book_asks=book_asks,
                            bid_liquidity_quantity=bid_liquidity,
                            ask_liquidity_quantity=ask_liquidity,
                            rest_snapshot_sha256=rest_evidence[3] if rest_evidence else None,
                            source_frame_id=frame_id,
                            packet_offset=packet_offset, raw_frame_sha256=raw_hash,
                            bid_nanos=bid, ask_nanos=ask)
            fresh.append(row)
            history = self.observations[symbol]
            while history and received - history[0]["receive_ns"] > LOOKBACK_NS + 60_000_000_000:
                history.popleft()
        if not fresh:
            return
        self.feed_session = session
        self.stale = False
        candidates = []
        for row in sorted(fresh, key=lambda item: item["security_id"]):
            local = datetime.fromtimestamp(row["receive_ns"] / 1_000_000_000, IST)
            slot = f"{local.date()}:{local.hour:02}:{local.minute:02}:{row['symbol']}"
            if slot in self.decision_slots:
                continue
            minute = local.hour * 60 + local.minute
            if not 9 * 60 + 15 <= minute <= 14 * 60 + 40:
                self._reject_once(row["symbol"], slot, "OUTSIDE_ENTRY_AND_EXIT_WINDOW", row["id"])
                continue
            old = self._old(row)
            mid = (row["bid_nanos"] + row["ask_nanos"]) // 2
            old_mid = ((old["bid_nanos"] + old["ask_nanos"]) // 2) if old else None
            momentum = (Decimal(mid - old_mid) * 10_000 / old_mid) if old_mid else None
            spread = Decimal(row["ask_nanos"] - row["bid_nanos"]) * 10_000 / mid
            position = self.positions.get(row["symbol"])
            if position:
                entry_ask = Decimal(position["ask_reference_inr"])
                bid = Decimal(row["bid_nanos"]) / 1_000_000_000
                if local.date().isoformat() != position["day"]:
                    self._dispose(row, "CARRIED_GAP_FIRST_FRESH_REFERENCE", reduce=False, slot=slot)
                elif minute >= 14 * 60 + 40:
                    self._dispose(row, "SESSION_CUTOFF", reduce=False, slot=slot)
                elif bid <= entry_ask * (1 - PORTFOLIO_ADVERSE_BPS / 10_000):
                    self._dispose(row, "PROTECTIVE_ADVERSE_MOVE", reduce=False, slot=slot)
                elif momentum is not None and momentum <= 0 and position["reduced"]:
                    self._dispose(row, "PERSISTENT_MOMENTUM_REVERSAL", reduce=False, slot=slot)
                elif momentum is not None and momentum <= 0 and position["remaining_shares"] > 1:
                    self._dispose(row, "MOMENTUM_REVERSAL", reduce=True, slot=slot)
                else:
                    self.emit("HOLD", day=position["day"], symbol=row["symbol"],
                              decision_slot=slot, observation_id=row["id"],
                              reason="NO_EXIT_TRIGGER")
                continue
            if minute >= 14 * 60 + 40:
                self._reject_once(row["symbol"], slot, "ENTRY_CUTOFF", row["id"])
                continue
            if old_mid is None:
                self._reject_once(row["symbol"], slot, "PAST_LOOKBACK_UNAVAILABLE", row["id"])
                continue
            if spread > PORTFOLIO_MAX_SPREAD_BPS:
                self._reject_once(row["symbol"], slot, "QUOTED_SPREAD_LIMIT", row["id"])
                continue
            ask = Decimal(row["ask_nanos"]) / 1_000_000_000
            notional = min(PORTFOLIO_MAX_INSTRUMENT, self.cash)
            shares = int(notional // ask)
            if shares < 1:
                self._reject_once(row["symbol"], slot, "VIRTUAL_CAPITAL_LIMIT", row["id"])
                continue
            fees = Decimal(published_intraday_fees(ask * shares, ask * shares)["total"])
            downside_bound = self._risk_bound(ask, shares, spread)
            if downside_bound > PORTFOLIO_DECISION_LOSS:
                self._reject_once(row["symbol"], slot, "DECISION_LOSS_LIMIT", row["id"])
                continue
            net_proxy = momentum - spread - PORTFOLIO_RESIDUAL_FRICTION_BPS - fees * 10_000 / (ask * shares)
            if net_proxy <= 0:
                self._reject_once(row["symbol"], slot, "NO_POSITIVE_NET_MOMENTUM_PROXY", row["id"])
                continue
            candidates.append((net_proxy, row["security_id"], row, slot, shares,
                               momentum, spread))
        for net_proxy, _, row, slot, shares, momentum, spread in sorted(
                candidates, key=lambda item: (-item[0], item[1])):
            day = datetime.fromtimestamp(row["receive_ns"] / 1_000_000_000, IST).date().isoformat()
            deployed = sum(Decimal(position["ask_reference_inr"]) * position["remaining_shares"]
                           for position in self.positions.values())
            remaining = min(self.cash, PORTFOLIO_MAX_DEPLOYED - deployed,
                            PORTFOLIO_MAX_INSTRUMENT)
            ask = Decimal(row["ask_nanos"]) / 1_000_000_000
            shares = min(shares, int(remaining // ask))
            while shares and (ask * shares + Decimal(
                    published_intraday_fees(ask * shares, Decimal(0))["total"]) > self.cash):
                shares -= 1
            bound = self._risk_bound(ask, shares, spread) if shares else Decimal(0)
            open_quotes_fresh = all(
                self.observations[position_symbol]
                and 0 <= row["observed_at_ns"]
                    - self.observations[position_symbol][-1]["receive_ns"] <= MAX_AGE_NS
                for position_symbol in self.positions
            )
            if not open_quotes_fresh:
                self._reject_once(row["symbol"], slot, "OPEN_POSITION_QUOTE_STALE", row["id"])
            elif (row.get("ask_liquidity_quantity") is not None and
                  row["ask_liquidity_quantity"] < shares):
                self._reject_once(row["symbol"], slot, "DISPLAYED_ENTRY_LIQUIDITY_INSUFFICIENT", row["id"])
            elif self.session_net[day] <= -PORTFOLIO_SESSION_LOSS:
                self._reject_once(row["symbol"], slot, "SESSION_LOSS_LIMIT", row["id"])
            elif (bound > PORTFOLIO_DECISION_LOSS or
                  self.session_net[day] - self._open_risk() - bound
                  < -PORTFOLIO_SESSION_LOSS):
                self._reject_once(row["symbol"], slot, "AGGREGATE_RISK_BUDGET", row["id"])
            elif len(self.positions) >= PORTFOLIO_MAX_POSITIONS or shares < 1:
                self._reject_once(row["symbol"], slot, "POSITION_OR_EXPOSURE_LIMIT", row["id"])
            else:
                buy = ask * shares
                buy_fee = Decimal(published_intraday_fees(buy, Decimal(0))["total"])
                self.emit("BUY", day=day, symbol=row["symbol"], security_id=row["security_id"],
                          observation_id=row["id"], decision_slot=slot,
                          ask_reference_inr=str(ask), shares=shares,
                          buy_notional_inr=str(buy), buy_fee_inr=str(buy_fee),
                          cash_after_inr=str(self.cash - buy - buy_fee),
                          risk_bound_inr=str(bound),
                          momentum_bps=str(momentum), quoted_spread_bps=str(spread),
                          net_momentum_proxy_bps=str(net_proxy), actual_fill=False)

    def status(self, state: str) -> dict:
        deployed = sum(Decimal(position["ask_reference_inr"]) * position["remaining_shares"]
                       for position in self.positions.values())
        equity = self.cash + deployed
        quotes = []
        now_ns = time.time_ns()
        for symbol, history in self.observations.items():
            if history:
                row = history[-1]
                if not 0 <= now_ns - row["receive_ns"] <= MAX_AGE_NS:
                    continue
                quotes.append({"symbol": symbol, "bid_nanos": str(row["bid_nanos"]),
                               "ask_nanos": str(row["ask_nanos"]),
                               "receive_ns": str(row["receive_ns"]),
                               "availability_ns": str(row["availability_ns"]),
                               "observed_at_ns": str(row["observed_at_ns"]),
                               "receipt_to_worker_ms": str(Decimal(
                                   row["observed_at_ns"] - row["receive_ns"]) / 1_000_000),
                               "book_source": row["book_source"],
                               "raw_frame_sha256": row["raw_frame_sha256"],
                               "rest_snapshot_sha256": row["rest_snapshot_sha256"]})
        return {"schema_version": PORTFOLIO_VERSION, "state": state,
                "feed_status": "STALE" if self.stale else "OBSERVING",
                "universe_count": 25, "monitored_instruments": len(quotes),
                "capital_start_inr": str(PORTFOLIO_CAPITAL), "cash_inr": str(self.cash),
                "equity_inr": str(equity), "equity_basis": "COST_BASIS_NOT_MARKET_VALUE",
                "max_drawdown_inr": str(self.drawdown), "positions": list(self.positions.values()),
                "position": None, "trades": list(self.trades), "decisions": list(self.decisions),
                "equity_curve": list(self.curve), "observed_quotes": sorted(quotes, key=lambda x: x["symbol"]),
                "rejected_count": self.rejected, "ledger_events": len(self.ledger.events),
                "remaining_deployable_inr": str(max(Decimal(0), PORTFOLIO_MAX_DEPLOYED - deployed)),
                "capital_buffer_at_full_deployment_inr": str(
                    PORTFOLIO_CAPITAL - PORTFOLIO_MAX_DEPLOYED),
                "reserved_cash_inr": "0",
                "remaining_position_slots": max(0, PORTFOLIO_MAX_POSITIONS - len(self.positions)),
                "open_risk_bound_inr": str(self._open_risk()),
                "risk_limits": {"version": "cash-paper-risk-v1", "max_deployed_inr": str(PORTFOLIO_MAX_DEPLOYED),
                                "max_instrument_inr": str(PORTFOLIO_MAX_INSTRUMENT),
                                "max_positions": PORTFOLIO_MAX_POSITIONS,
                                "max_session_loss_inr": str(PORTFOLIO_SESSION_LOSS),
                                "max_loss_per_decision_inr": str(PORTFOLIO_DECISION_LOSS),
                                "max_spread_bps": str(PORTFOLIO_MAX_SPREAD_BPS),
                                "max_market_age_ns": str(MAX_AGE_NS)},
                "source": "LOCAL_GATEWAY_AFTER_QJ01", "actual_fills": 0, "broker_orders": 0,
                "qp01_writes": 0}


def save_status(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, separators=(",", ":")) + "\n")
    os.chmod(temporary, 0o600)
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def gateway_cookie(secret: str) -> str:
    request = Request(
        "http://127.0.0.1:8765/api/session",
        method="POST",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Authorization": f"Bearer {secret}",
        },
    )
    with urlopen(request, timeout=2) as response:
        return response.headers["Set-Cookie"].split(";", 1)[0]


def local_rest_snapshot(cookie: str) -> dict | None:
    """Read the existing backend cache; this does not request Dhan refresh."""
    request = Request(
        "http://127.0.0.1:8767/trade/market-snapshot",
        headers={"Cookie": cookie},
    )
    try:
        with urlopen(request, timeout=1) as response:
            raw = response.read(1_000_001)
        return json.loads(raw) if len(raw) <= 1_000_000 else None
    except (OSError, URLError, ValueError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--portfolio-v2", action="store_true",
                        help="Use the separate concurrent virtual portfolio ledger")
    parser.add_argument(
        "--universe",
        type=Path,
        default=Path("docs/cross-stock-universe-2026-11-02.json"),
    )
    args = parser.parse_args()
    private = args.private_dir
    if private.is_symlink() or private.stat().st_mode & 0o077:
        raise PermissionError("private simulation directory required")
    stop = private / "stop-requested"
    ledger = Ledger(private / ("simulation-portfolio-v2.jsonl" if args.portfolio_v2
                               else "simulation-ledger.jsonl"),
                    PORTFOLIO_VERSION if args.portfolio_v2 else VERSION)
    sim = (PortfolioSimulator if args.portfolio_v2 else Simulator)(
        ledger, json.loads(args.universe.read_text()))
    status_path = private / "simulation-status.json"
    halted = False

    def halt(*_: object) -> None:
        nonlocal halted
        halted = True

    signal.signal(signal.SIGTERM, halt)
    signal.signal(signal.SIGINT, halt)
    cookie = None
    try:
        sim.emit("START", observed_at_ns=time.time_ns(), mode="SIMULATION_ONLY")
        save_status(status_path, sim.status("STARTING"))
        while not halted and not stop.exists():
            try:
                if cookie is None:
                    cookie = gateway_cookie(os.environ["ALPHA_GRID_UI_SESSION_SECRET"])
                request = Request(
                    "http://127.0.0.1:8765/api/snapshot", headers={"Cookie": cookie}
                )
                with urlopen(request, timeout=2) as response:
                    snapshot = json.load(response)["payload"]
                rest = (local_rest_snapshot(cookie) if isinstance(sim, PortfolioSimulator)
                        and snapshot.get("feed_connected") else None)
                if isinstance(sim, PortfolioSimulator):
                    sim.ingest(snapshot, time.time_ns(), rest)
                else:
                    sim.ingest(snapshot, time.time_ns())
                local = datetime.now(IST)
                market_minute = local.hour * 60 + local.minute
                if not 9 * 60 + 15 <= market_minute <= 15 * 60 + 14:
                    state = "MARKET_CLOSED"
                elif not sim.stale:
                    state = "OBSERVING"
                elif isinstance(sim, PortfolioSimulator) and snapshot.get("feed_connected"):
                    reasons = set(snapshot.get("no_trade_reasons") or [])
                    state = ("SIMULATION_PAUSED_VALIDATION_PENDING"
                             if reasons & {"RAW_CAPTURE_VALIDATION_PENDING",
                                           "SESSION_CLASSIFICATION_UNVERIFIED"}
                             else "SIMULATION_PAUSED_NO_FRESH_BOOK")
                else:
                    state = "WAITING_FOR_GENUINE_FEED"
            except (OSError, URLError, ValueError, KeyError):
                cookie = None
                sim.gap("GATEWAY_UNAVAILABLE", time.time_ns())
                state = "GATEWAY_UNAVAILABLE"
            save_status(status_path, sim.status(state))
            time.sleep(1)
    finally:
        sim.emit("STOP", observed_at_ns=time.time_ns(), mode="SIMULATION_ONLY")
        save_status(status_path, sim.status("STOPPED"))
        ledger.close()


if __name__ == "__main__":
    main()
