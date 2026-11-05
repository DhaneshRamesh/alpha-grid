"""Synthetic software fixtures only; no market research observations."""

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from quant.apps.live_simulation import (
    PORTFOLIO_VERSION,
    Ledger,
    PortfolioSimulator,
    Simulator,
)


def _universe() -> dict:
    return {
        "schema_version": "alpha-grid-cross-stock-universe-v1",
        "instruments": [
            {"security_id": str(index + 1), "symbol": f"FIXTURE{index + 1}"}
            for index in range(25)
        ],
    }


def _snapshot(
    when: datetime, bid: int, ask: int, ordinal: int, *, connected: bool = True
) -> dict:
    stamp = int(when.timestamp() * 1_000_000_000)
    return {
        "mode": "OBSERVE_ONLY",
        "feed_connected": connected,
        "journal_blocked": False,
        "session_id": "fixture-connection",
        "instruments": [
            {
                "instrument": {"security_id": "1", "symbol": "FIXTURE1"},
                "market": {
                    "freshness": "FRESH",
                    "quality": {"stale": False, "cross_feed_uncertain": False},
                    "source_receive_timestamp_ns": str(stamp),
                    "best_bid_price_nanos": str(bid),
                    "best_ask_price_nanos": str(ask),
                    "provenance": {
                        "feed_session_id": "fixture-connection",
                        "arrival_ordinal": str(ordinal),
                        "source_protocol": "DhanStandardV2",
                    },
                },
            },
            *(
                {
                    "instrument": {
                        "security_id": str(index),
                        "symbol": f"FIXTURE{index}",
                    },
                    "market": None,
                }
                for index in range(2, 26)
            ),
        ],
    }


def _portfolio_snapshot(when: datetime, quotes: dict[str, tuple[int, int, int]],
                        *, unverified: bool = False) -> dict:
    stamp = int(when.timestamp() * 1_000_000_000)
    rows = []
    for sid in range(1, 26):
        value = quotes.get(str(sid))
        market = None
        if value:
            bid, ask, ordinal = value
            market = {"packet_kind": "QUOTE", "freshness": "FRESH", "quality": {
                "stale": False, "source_time_unverified": False,
                "cross_feed_uncertain": False, "book_pairing_unverified": False},
                "source_receive_timestamp_ns": str(stamp),
                "best_bid_price_nanos": str(bid),
                "best_ask_price_nanos": str(ask),
                "provenance": {"feed_session_id": "fixture-connection",
                               "arrival_ordinal": str(ordinal),
                               "source_protocol": "DhanStandardV2",
                               "source_frame_id": str(ordinal),
                               "packet_offset": 0,
                               "raw_frame_sha256": "a" * 64,
                               "raw_durable_at_ns": str(stamp)}}
        rows.append({"instrument": {"security_id": str(sid),
                                    "symbol": f"FIXTURE{sid}"}, "market": market})
    return {"mode": "OBSERVE_ONLY", "feed_connected": True,
            "journal_blocked": False, "session_id": "fixture-connection",
            "no_trade_reasons": ["SESSION_CLASSIFICATION_UNVERIFIED"] if unverified else [],
            "instruments": rows}


def test_portfolio_concurrent_buy_reduce_hold_sell_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "portfolio-v2.jsonl"
    ledger = Ledger(path, PORTFOLIO_VERSION)
    sim = PortfolioSimulator(ledger, _universe())
    start = datetime(2026, 11, 3, 9, 45, tzinfo=ZoneInfo("Asia/Kolkata"))

    def ingest(minutes: int, quotes: dict[str, tuple[int, int, int]]) -> None:
        when = start + timedelta(minutes=minutes)
        sim.ingest(_portfolio_snapshot(when, quotes), int(when.timestamp() * 1_000_000_000))

    base = (100_000_000_000, 100_010_000_000)
    ingest(0, {"1": (*base, 1), "2": (*base, 1), "3": (*base, 1)})
    ingest(15, {"1": (101_000_000_000, 101_010_000_000, 2),
                "2": (101_000_000_000, 101_010_000_000, 2)})
    assert len(sim.positions) == 2
    assert sum(row["kind"] == "BUY" for row in ledger.events) == 2
    assert sum(Decimal(p["ask_reference_inr"]) * p["remaining_shares"]
               for p in sim.positions.values()) <= Decimal("15000")
    ingest(16, {"1": (100_000_000_000, 100_010_000_000, 3),
                "2": (102_000_000_000, 102_010_000_000, 3)})
    assert any(row["kind"] == "REDUCE" and row["symbol"] == "FIXTURE1"
               for row in ledger.events)
    assert any(row["kind"] == "HOLD" and row["symbol"] == "FIXTURE2"
               for row in ledger.events)
    ingest(17, {"1": (80_000_000_000, 80_010_000_000, 4),
                "2": (80_000_000_000, 80_010_000_000, 4),
                "3": (100_000_000_000, 100_010_000_000, 2)})
    assert len(sim.positions) == 0
    assert sum(row["kind"] == "SELL" for row in ledger.events) == 2
    assert sim.session_net["2026-11-03"] < -Decimal("500")
    ingest(32, {"3": (101_000_000_000, 101_010_000_000, 3)})
    assert any(row["kind"] == "REJECT" and row["reason"] == "SESSION_LOSS_LIMIT"
               and row["symbol"] == "FIXTURE3" for row in ledger.events)
    count, cash = len(ledger.events), sim.cash
    ledger.close()
    recovered_ledger = Ledger(path, PORTFOLIO_VERSION)
    recovered = PortfolioSimulator(recovered_ledger, _universe())
    assert recovered.cash == cash and not recovered.positions
    when = start + timedelta(minutes=32)
    recovered.ingest(_portfolio_snapshot(when, {"3": (101_000_000_000, 101_010_000_000, 3)}),
                     int(when.timestamp() * 1_000_000_000))
    assert len(recovered_ledger.events) == count
    recovered_ledger.close()


def test_portfolio_validation_pending_pauses_decisions_without_rewriting_scope(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "portfolio-v2.jsonl", PORTFOLIO_VERSION)
    sim = PortfolioSimulator(ledger, _universe())
    when = datetime(2026, 11, 3, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    sim.ingest(_portfolio_snapshot(when, {"1": (101_000_000_000, 101_010_000_000, 1)},
                                   unverified=True), int(when.timestamp() * 1_000_000_000))
    assert not sim.positions and sim.stale
    assert not any(row["kind"] == "BUY" for row in ledger.events)
    assert [row["reason"] for row in ledger.events if row["kind"] == "GAP"] == [
        "RESEARCH_VALIDATION_PENDING"]
    sim.ingest(_portfolio_snapshot(when, {"1": (101_000_000_000, 101_010_000_000, 1)},
                                   unverified=True), int(when.timestamp() * 1_000_000_000))
    assert sum(row["kind"] == "GAP" for row in ledger.events) == 1
    ledger.close()


def test_portfolio_three_positions_keep_all_stocks_updating_and_order_mixed_decisions(
    tmp_path: Path,
) -> None:
    ledger = Ledger(tmp_path / "portfolio-v2.jsonl", PORTFOLIO_VERSION)
    sim = PortfolioSimulator(ledger, _universe())
    start = datetime(2026, 11, 3, 9, 45, tzinfo=ZoneInfo("Asia/Kolkata"))

    def tick(minutes: int, prices: dict[int, int], ordinal: int) -> None:
        when = start + timedelta(minutes=minutes)
        quotes = {str(sid): (prices.get(sid, 100) * 1_000_000_000,
                             prices.get(sid, 100) * 1_000_000_000 + 10_000_000,
                             ordinal) for sid in range(1, 26)}
        sim.ingest(_portfolio_snapshot(when, quotes), int(when.timestamp() * 1_000_000_000))

    tick(0, {}, 1)
    tick(1, {}, 2)
    tick(15, {1: 101, 2: 101, 3: 101}, 3)
    assert list(sim.positions) == ["FIXTURE1", "FIXTURE2", "FIXTURE3"]
    assert len([e for e in ledger.events if e["kind"] == "BUY"]) == 3
    assert sim.cash >= 0
    assert sim._open_risk() <= Decimal("500")
    tick(16, {1: 99, 2: 102, 3: 102}, 4)
    assert sim.positions["FIXTURE1"]["remaining_shares"] < sim.positions["FIXTURE1"]["shares"]
    assert all(len(sim.observations[f"FIXTURE{sid}"]) == 4 for sid in range(1, 26))
    tick(17, {1: 99, 2: 99, 3: 102, 4: 101}, 5)
    mixed = [e for e in ledger.events if e.get("observation_id", "").endswith(":5")]
    assert [(e["kind"], e["symbol"]) for e in mixed if e["kind"] in
            {"SELL", "REDUCE", "HOLD", "BUY"}] == [
                ("SELL", "FIXTURE1"), ("REDUCE", "FIXTURE2"),
                ("HOLD", "FIXTURE3"), ("BUY", "FIXTURE4")]
    assert len(sim.positions) == 3
    assert sim.cash >= 0
    assert sim.status("OBSERVING")["capital_start_inr"] == "20000"
    assert sim.status("OBSERVING")["capital_buffer_at_full_deployment_inr"] == "5000"
    assert sim.status("OBSERVING")["reserved_cash_inr"] == "0"
    assert all(e.get("actual_fill") is False for e in ledger.events if e["kind"] in
               {"BUY", "SELL", "REDUCE"})
    running_cash = Decimal("20000")
    for event in ledger.events:
        if event["kind"] == "BUY":
            running_cash -= Decimal(event["buy_notional_inr"]) + Decimal(event["buy_fee_inr"])
        elif event["kind"] in {"SELL", "REDUCE"}:
            running_cash += (Decimal(event["sell_notional_inr"])
                             - Decimal(event["fees"]["sell"])
                             - Decimal(event["residual_friction_inr"]))
            assert Decimal(event["fees"]["total"]) == (Decimal(event["fees"]["buy"])
                                                           + Decimal(event["fees"]["sell"]))
            assert Decimal(event["net_reference_inr"]) == (
                Decimal(event["sell_notional_inr"])
                - Decimal(next(row["ask_reference_inr"] for row in ledger.events
                               if row["sequence"] == event["entry_sequence"]))
                  * event["shares_sold"]
                - Decimal(event["fees"]["total"])
                - Decimal(event["residual_friction_inr"]))
        if event["kind"] in {"BUY", "SELL", "REDUCE"}:
            assert Decimal(event["cash_after_inr"]) == running_cash
    assert running_cash == sim.cash
    cash, positions, events = sim.cash, dict(sim.positions), len(ledger.events)
    try:
        Ledger(tmp_path / "portfolio-v2.jsonl", PORTFOLIO_VERSION)
    except BlockingIOError:
        pass
    else:
        raise AssertionError("second worker opened the active portfolio ledger")
    ledger.close()
    recovered_ledger = Ledger(tmp_path / "portfolio-v2.jsonl", PORTFOLIO_VERSION)
    recovered = PortfolioSimulator(recovered_ledger, _universe())
    assert recovered.cash == cash and recovered.positions == positions
    when = start + timedelta(minutes=17)
    quotes = {str(sid): (({1: 99, 2: 99, 3: 102, 4: 101}.get(sid, 100)) * 1_000_000_000,
                         ({1: 99, 2: 99, 3: 102, 4: 101}.get(sid, 100)) * 1_000_000_000
                         + 10_000_000, 5) for sid in range(1, 26)}
    recovered.ingest(_portfolio_snapshot(when, quotes), int(when.timestamp() * 1_000_000_000))
    assert len(recovered_ledger.events) == events and recovered.cash == cash
    recovered_ledger.close()
    path = tmp_path / "portfolio-v2.jsonl"
    path.write_text(path.read_text().replace("FIXTURE4", "TAMPERED"))
    try:
        Ledger(path, PORTFOLIO_VERSION)
    except ValueError as exc:
        assert "hash invalid" in str(exc)
    else:
        raise AssertionError("tampered v2 ledger accepted")


def test_portfolio_aggregate_loss_reservation_and_stale_open_position(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "portfolio-v2.jsonl", PORTFOLIO_VERSION)
    sim = PortfolioSimulator(ledger, _universe())
    start = datetime(2026, 11, 3, 9, 45, tzinfo=ZoneInfo("Asia/Kolkata"))

    def tick(minutes: int, quotes: dict[str, tuple[int, int, int]]) -> None:
        when = start + timedelta(minutes=minutes)
        converted = {sid: (bid * 1_000_000_000, bid * 1_000_000_000 + 10_000_000,
                           ordinal) for sid, (bid, _, ordinal) in quotes.items()}
        sim.ingest(_portfolio_snapshot(when, converted), int(when.timestamp() * 1_000_000_000))

    tick(0, {str(i): (100, 0, 1) for i in range(1, 5)})
    tick(1, {str(i): (100, 0, 2) for i in range(1, 5)})
    tick(15, {"1": (101, 0, 3)})
    assert len(sim.positions) == 1
    tick(16, {"1": (95, 0, 4)})
    assert not sim.positions and sim.session_net["2026-11-03"] < 0
    tick(17, {"2": (101, 0, 3), "3": (101, 0, 3), "4": (101, 0, 3)})
    assert len(sim.positions) == 1
    assert any(e["kind"] == "REJECT" and e["reason"] == "AGGREGATE_RISK_BUDGET"
               for e in ledger.events)
    assert sim.session_net["2026-11-03"] - sim._open_risk() >= -Decimal("500")

    ledger.close()


def test_portfolio_rejects_stale_open_stock_future_and_nonfeed_quotes(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "portfolio-v2.jsonl", PORTFOLIO_VERSION)
    sim = PortfolioSimulator(ledger, _universe())
    start = datetime(2026, 11, 3, 9, 45, tzinfo=ZoneInfo("Asia/Kolkata"))

    def snapshot(minutes: int, quotes: dict[str, tuple[int, int, int]]) -> tuple[dict, int]:
        when = start + timedelta(minutes=minutes)
        converted = {sid: (price * 1_000_000_000, price * 1_000_000_000 + 10_000_000,
                           ordinal) for sid, (price, _, ordinal) in quotes.items()}
        return _portfolio_snapshot(when, converted), int(when.timestamp() * 1_000_000_000)

    for minute, prices in [(0, {"1": (100, 0, 1), "2": (100, 0, 1)}),
                           (15, {"1": (101, 0, 2)})]:
        snap, now = snapshot(minute, prices)
        sim.ingest(snap, now)
    assert list(sim.positions) == ["FIXTURE1"]
    snap, now = snapshot(16, {"2": (101, 0, 2)})
    sim.ingest(snap, now)
    assert "FIXTURE2" not in sim.positions
    assert any(e["kind"] == "REJECT" and e["reason"] == "OPEN_POSITION_QUOTE_STALE"
               for e in ledger.events)
    snap, now = snapshot(17, {"2": (102, 0, 3)})
    snap["instruments"][1]["market"]["source_receive_timestamp_ns"] = str(now + 1)
    prior_observed = sum(e["kind"] == "OBSERVED" for e in ledger.events)
    sim.ingest(snap, now)
    assert sum(e["kind"] == "OBSERVED" for e in ledger.events) == prior_observed
    snap, now = snapshot(18, {"2": (103, 0, 4)})
    snap["instruments"][1]["market"]["provenance"]["source_protocol"] = "HISTORICAL_ARCHIVE"
    sim.ingest(snap, now)
    assert sum(e["kind"] == "OBSERVED" for e in ledger.events) == prior_observed
    ledger.close()


def test_portfolio_pairs_only_fresh_original_ws_and_rest_book(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "portfolio-v2.jsonl", PORTFOLIO_VERSION)
    sim = PortfolioSimulator(ledger, _universe())
    start = datetime(2026, 11, 3, 9, 45, tzinfo=ZoneInfo("Asia/Kolkata"))

    def source(minutes: int, ordinal: int, price: int) -> tuple[dict, int, datetime]:
        when = start + timedelta(minutes=minutes)
        stamp = int(when.timestamp() * 1_000_000_000)
        snap = _portfolio_snapshot(when, {"1": (price * 1_000_000_000,
                                                price * 1_000_000_000 + 10_000_000,
                                                ordinal)})
        market = snap["instruments"][0]["market"]
        market["best_bid_price_nanos"] = None
        market["best_ask_price_nanos"] = None
        market["quality"]["source_time_unverified"] = True
        return snap, stamp, when

    def rest(when: datetime, bid: int, ask: int) -> dict:
        return {"source": "REST_SNAPSHOT", "received_at": when.isoformat(),
                "http_status": 200, "response_bytes": 100,
                "sha256": "b" * 64,
                "quotes": [{"security_id": "1", "depth": {
                    "buy": [{"price": bid, "quantity": 100}],
                    "sell": [{"price": ask, "quantity": 100}]}}]}

    snap, now, when = source(0, 1, 100)
    sim.ingest(snap, now, rest(when, 100, 100.01))
    first = next(event for event in ledger.events if event["kind"] == "OBSERVED")
    assert first["book_source"] == "REST_SNAPSHOT"
    assert first["raw_frame_sha256"] == "a" * 64
    assert first["rest_snapshot_sha256"] == "b" * 64
    assert first["availability_ns"] == first["observed_at_ns"]
    assert first["raw_durable_at_ns"] <= first["availability_ns"]
    assert first["rest_received_ns"] <= first["availability_ns"]
    snap, now, when = source(15, 2, 101)
    sim.ingest(snap, now, rest(when - timedelta(seconds=3), 101, 101.01))
    assert not sim.positions
    assert any(event["kind"] == "REJECT" and
               event["reason"] == "FRESH_VERIFIED_BOOK_UNAVAILABLE" for event in ledger.events)
    snap, now, when = source(16, 3, 101)
    sim.ingest(snap, now, rest(when, 101, 101.01))
    assert list(sim.positions) == ["FIXTURE1"]
    assert sim.positions["FIXTURE1"]["actual_fill"] is False
    ledger.close()


def test_one_bad_rest_book_does_not_block_another_stock(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "portfolio-v2.jsonl", PORTFOLIO_VERSION)
    sim = PortfolioSimulator(ledger, _universe())
    when = datetime(2026, 11, 3, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    now = int(when.timestamp() * 1_000_000_000)
    snap = _portfolio_snapshot(when, {
        "1": (100_000_000_000, 100_010_000_000, 1),
        "2": (100_000_000_000, 100_010_000_000, 1),
    })
    snap["no_trade_reasons"] = ["SOURCE_TIME_UNVERIFIED"]
    for item in snap["instruments"][:2]:
        item["market"]["best_bid_price_nanos"] = None
        item["market"]["best_ask_price_nanos"] = None
    rest = {"source": "REST_SNAPSHOT", "received_at": when.isoformat(),
            "sha256": "b" * 64, "http_status": 200, "response_bytes": 120,
            "quotes": [
                {"security_id": "1", "depth": {
                    "buy": [{"price": 101, "quantity": 1}],
                    "sell": [{"price": 100, "quantity": 1}]}},
                {"security_id": "2", "depth": {
                    "buy": [{"price": 100, "quantity": 1}],
                    "sell": [{"price": 100.01, "quantity": 1}]}},
            ]}
    sim.ingest(snap, now, rest)
    assert [row["symbol"] for row in ledger.events if row["kind"] == "OBSERVED"] == [
        "FIXTURE2"]
    assert any(row["kind"] == "REJECT" and row["symbol"] == "FIXTURE1"
               and row["reason"] == "FRESH_VERIFIED_BOOK_UNAVAILABLE"
               for row in ledger.events)
    ledger.close()


def test_full_packet_book_is_same_receipt_past_only_and_liquidity_bounded(
    tmp_path: Path,
) -> None:
    ledger = Ledger(tmp_path / "portfolio-v2.jsonl", PORTFOLIO_VERSION)
    sim = PortfolioSimulator(ledger, _universe())
    start = datetime(2026, 11, 3, 9, 45, tzinfo=ZoneInfo("Asia/Kolkata"))

    def full(minutes: int, bid: int, ask: int, ordinal: int,
             qty: int = 100) -> tuple[dict, int]:
        when = start + timedelta(minutes=minutes)
        stamp = int(when.timestamp() * 1_000_000_000)
        snapshot = _portfolio_snapshot(when, {"1": (bid, ask, ordinal)})
        item = snapshot["instruments"][0]
        item["market"]["packet_kind"] = "FULL"
        bids = [{"price_nanos": str(bid - index * 1_000_000_000),
                 "displayed_quantity": str(qty), "order_count": 2}
                for index in range(5)]
        asks = [{"price_nanos": str(ask + index * 1_000_000_000),
                 "displayed_quantity": str(qty), "order_count": 3}
                for index in range(5)]
        item["depth"] = {"source": "STANDARD_FULL", "derived_pairing": False,
                         "provenance": dict(item["market"]["provenance"]),
                         "receive_timestamp_ns": str(stamp), "bids": bids, "asks": asks,
                         "book_metrics": {"bid_quantity": str(5 * qty),
                                          "ask_quantity": str(5 * qty),
                                          "five_level_imbalance": 0.0}}
        return snapshot, stamp

    first, now = full(0, 100_000_000_000, 100_010_000_000, 1)
    sim.ingest(first, now)
    observed = next(event for event in ledger.events if event["kind"] == "OBSERVED")
    assert observed["book_source"] == "LIVE_WS"
    assert len(observed["book_bids"]) == len(observed["book_asks"]) == 5
    assert observed["bid_liquidity_quantity"] == 100
    future, now = full(15, 101_000_000_000, 101_010_000_000, 2)
    future["instruments"][0]["depth"]["receive_timestamp_ns"] = str(now + 1)
    sim.ingest(future, now)
    assert not sim.positions
    assert any(event["reason"] == "INVALID_FULL_BOOK" for event in ledger.events
               if event["kind"] == "REJECT")
    valid, now = full(16, 101_000_000_000, 101_010_000_000, 3)
    sim.ingest(valid, now)
    assert list(sim.positions) == ["FIXTURE1"]
    assert sim.positions["FIXTURE1"]["actual_fill"] is False
    ledger.close()


def test_portfolio_v2_ignores_unverified_twenty_level_depth_books(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "portfolio-v2.jsonl", PORTFOLIO_VERSION)
    sim = PortfolioSimulator(ledger, _universe())
    start = datetime(2026, 11, 3, 9, 45, tzinfo=ZoneInfo("Asia/Kolkata"))

    def depth20(minutes: int, bid: int, ask: int, ordinal: int, qty: int = 200) -> tuple[dict, int]:
        when = start + timedelta(minutes=minutes)
        stamp = int(when.timestamp() * 1_000_000_000)
        snapshot = _portfolio_snapshot(when, {"1": (bid, ask, ordinal)})
        item = snapshot["instruments"][0]
        item["market"]["packet_kind"] = "QUOTE"
        bids = [{"price_nanos": str(bid - index * 100_000_000),
                 "displayed_quantity": str(qty), "order_count": 2}
                for index in range(20)]
        asks = [{"price_nanos": str(ask + index * 100_000_000),
                 "displayed_quantity": str(qty), "order_count": 3}
                for index in range(20)]
        item["depth"] = {"source": "DEPTH_20", "derived_pairing": True,
                         "security_id": "1", "raw_frame_sha256": "b" * 64,
                         "receive_timestamp_ns": str(stamp), "bids": bids, "asks": asks,
                         "book_metrics": {"bid_quantity": str(20 * qty),
                                          "ask_quantity": str(20 * qty),
                                          "five_level_imbalance": 0.0}}
        return snapshot, stamp

    first, now = depth20(0, 100_000_000_000, 100_010_000_000, 1)
    sim.ingest(first, now)
    observed = next(event for event in ledger.events if event["kind"] == "OBSERVED")
    assert observed["book_source"] == "LIVE_WS"
    assert observed["book_bids"] is None
    assert observed["book_asks"] is None
    assert observed["bid_liquidity_quantity"] is None

    valid, now = depth20(16, 101_000_000_000, 101_010_000_000, 2)
    sim.ingest(valid, now)
    assert list(sim.positions) == ["FIXTURE1"]
    assert sim.positions["FIXTURE1"]["actual_fill"] is False
    ledger.close()


def test_full_book_must_share_exact_standard_frame_provenance(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "portfolio-v2.jsonl", PORTFOLIO_VERSION)
    sim = PortfolioSimulator(ledger, _universe())
    when = datetime(2026, 11, 3, 9, 45, tzinfo=ZoneInfo("Asia/Kolkata"))
    stamp = int(when.timestamp() * 1_000_000_000)
    snap = _portfolio_snapshot(when, {"1": (100_000_000_000, 100_010_000_000, 1)})
    item = snap["instruments"][0]
    levels = [{"price_nanos": str(100_000_000_000 - i * 1_000_000_000),
               "displayed_quantity": "100", "order_count": 2} for i in range(5)]
    item["market"]["packet_kind"] = "FULL"
    item["depth"] = {"source": "STANDARD_FULL", "derived_pairing": False,
                     "receive_timestamp_ns": str(stamp),
                     "provenance": {**item["market"]["provenance"], "raw_frame_sha256": "b" * 64},
                     "bids": levels,
                     "asks": [{**row, "price_nanos": str(100_010_000_000 + i * 1_000_000_000)}
                              for i, row in enumerate(levels)],
                     "book_metrics": {"bid_quantity": "500", "ask_quantity": "500"}}
    sim.ingest(snap, stamp)
    assert not any(row["kind"] == "OBSERVED" for row in ledger.events)
    assert any(row["kind"] == "REJECT" and row["reason"] == "INVALID_FULL_BOOK"
               for row in ledger.events)
    item["market"]["packet_kind"] = "QUOTE"
    item["market"]["provenance"]["arrival_ordinal"] = "2"
    sim.ingest(snap, stamp)
    observed = next(row for row in ledger.events if row["kind"] == "OBSERVED")
    assert observed["book_bids"] is None
    assert observed["bid_nanos"] == 100_000_000_000
    ledger.close()


def test_stale_or_mismatched_depth20_does_not_block_standard_feed(tmp_path: Path) -> None:
    """Codex regression: Stale or mismatched DEPTH_20 must NOT block valid standard market quote processing."""
    ledger = Ledger(tmp_path / "portfolio-v2.jsonl", PORTFOLIO_VERSION)
    sim = PortfolioSimulator(ledger, _universe())
    start = datetime(2026, 11, 3, 9, 45, tzinfo=ZoneInfo("Asia/Kolkata"))
    stamp = int(start.timestamp() * 1_000_000_000)

    # 1. Snapshot with STALE DEPTH_20 (receive timestamp 10 seconds in past, > MAX_AGE_NS)
    snap_stale = _portfolio_snapshot(start, {"1": (100_000_000_000, 100_010_000_000, 1)})
    item = snap_stale["instruments"][0]
    item["market"]["packet_kind"] = "QUOTE"
    bids = [{"price_nanos": str(100_000_000_000 - i * 100_000_000), "displayed_quantity": "100", "order_count": 1} for i in range(20)]
    asks = [{"price_nanos": str(100_010_000_000 + i * 100_000_000), "displayed_quantity": "100", "order_count": 1} for i in range(20)]
    item["depth"] = {
        "source": "DEPTH_20", "derived_pairing": True, "security_id": "1",
        "raw_frame_sha256": "c" * 64,
        "receive_timestamp_ns": str(stamp - 10_000_000_000),  # 10s stale!
        "bids": bids, "asks": asks,
        "book_metrics": {"bid_quantity": "2000", "ask_quantity": "2000", "five_level_imbalance": 0.0},
    }

    # Ingest: MUST NOT reject standard quote observation!
    sim.ingest(snap_stale, stamp)
    assert len(sim.observations["FIXTURE1"]) == 1
    obs_stale = sim.observations["FIXTURE1"][0]
    assert obs_stale["symbol"] == "FIXTURE1"
    # DEPTH_20 was safely ignored due to staleness
    assert obs_stale["book_bids"] is None

    # 2. Snapshot with MISMATCHED DEPTH_20 (security_id "999" instead of "1")
    when_2 = start + timedelta(seconds=1)
    stamp_2 = int(when_2.timestamp() * 1_000_000_000)
    snap_mismatch = _portfolio_snapshot(when_2, {"1": (100_020_000_000, 100_030_000_000, 2)})
    item2 = snap_mismatch["instruments"][0]
    item2["market"]["packet_kind"] = "QUOTE"
    item2["depth"] = {
        "source": "DEPTH_20", "derived_pairing": True, "security_id": "999",  # Instrument mismatch!
        "raw_frame_sha256": "d" * 64,
        "receive_timestamp_ns": str(stamp_2),
        "bids": bids, "asks": asks,
        "book_metrics": {"bid_quantity": "2000", "ask_quantity": "2000", "five_level_imbalance": 0.0},
    }

    sim.ingest(snap_mismatch, stamp_2)
    assert len(sim.observations["FIXTURE1"]) == 2
    obs_mismatch = sim.observations["FIXTURE1"][1]
    assert obs_mismatch["symbol"] == "FIXTURE1"
    assert obs_mismatch["book_bids"] is None
    ledger.close()


def test_raw_quote_cannot_enter_portfolio_even_if_global_gate_is_misreported(
    tmp_path: Path,
) -> None:
    ledger = Ledger(tmp_path / "portfolio-v2.jsonl", PORTFOLIO_VERSION)
    sim = PortfolioSimulator(ledger, _universe())
    when = datetime(2026, 11, 3, 9, 45, tzinfo=ZoneInfo("Asia/Kolkata"))
    snapshot = _portfolio_snapshot(when, {"1": (100_000_000_000, 100_010_000_000, 1)})
    snapshot["instruments"][0]["market"]["packet_kind"] = "RAW_QUOTE"
    sim.ingest(snapshot, int(when.timestamp() * 1_000_000_000))
    assert not any(row["kind"] == "OBSERVED" for row in ledger.events)
    assert any(row["kind"] == "REJECT" and row["reason"] == "RAW_OR_UNKNOWN_PACKET_QUARANTINED"
               for row in ledger.events)
    ledger.close()


def test_full_book_top_level_quantity_limits_virtual_reference_entry(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "portfolio-v2.jsonl", PORTFOLIO_VERSION)
    sim = PortfolioSimulator(ledger, _universe())
    start = datetime(2026, 11, 3, 9, 45, tzinfo=ZoneInfo("Asia/Kolkata"))
    for minute, bid, ordinal, quantity in [(0, 100, 1, 100), (15, 101, 2, 1)]:
        when = start + timedelta(minutes=minute)
        now = int(when.timestamp() * 1_000_000_000)
        snap = _portfolio_snapshot(when, {"1": (bid * 1_000_000_000,
                                                 bid * 1_000_000_000 + 10_000_000, ordinal)})
        item = snap["instruments"][0]
        item["market"]["packet_kind"] = "FULL"
        item["depth"] = {"source": "STANDARD_FULL", "derived_pairing": False,
                         "provenance": dict(item["market"]["provenance"]),
                         "receive_timestamp_ns": str(now),
                         "bids": [{"price_nanos": str((bid - i) * 1_000_000_000),
                                   "displayed_quantity": str(quantity), "order_count": 1}
                                  for i in range(5)],
                         "asks": [{"price_nanos": str((bid + i) * 1_000_000_000 + 10_000_000),
                                   "displayed_quantity": str(quantity), "order_count": 1}
                                  for i in range(5)],
                         "book_metrics": {"bid_quantity": str(5 * quantity),
                                          "ask_quantity": str(5 * quantity)}}
        sim.ingest(snap, now)
    assert not sim.positions
    assert any(row["kind"] == "REJECT" and
               row["reason"] == "DISPLAYED_ENTRY_LIQUIDITY_INSUFFICIENT"
               for row in ledger.events)
    ledger.close()


def test_persistent_virtual_entry_exit_and_restart_are_idempotent(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.jsonl"
    start = datetime(2026, 11, 3, 9, 45, tzinfo=ZoneInfo("Asia/Kolkata"))
    ledger = Ledger(path)
    sim = Simulator(ledger, _universe())
    first = _snapshot(start, 100_000_000_000, 100_010_000_000, 1)
    second = _snapshot(
        start + timedelta(minutes=15), 100_200_000_000, 100_210_000_000, 2
    )
    sim.ingest(first, int(start.timestamp() * 1_000_000_000))
    sim.ingest(second, int((start + timedelta(minutes=15)).timestamp() * 1_000_000_000))
    assert sim.position is not None
    assert sim.cash < Decimal("1000000")
    ledger.close()

    recovered_ledger = Ledger(path)
    recovered = Simulator(recovered_ledger, _universe())
    count = len(recovered_ledger.events)
    recovered.ingest(
        second, int((start + timedelta(minutes=15)).timestamp() * 1_000_000_000)
    )
    assert len(recovered_ledger.events) == count
    assert recovered.position is not None
    exit_time = start + timedelta(minutes=45)
    recovered.ingest(
        _snapshot(exit_time, 100_500_000_000, 100_510_000_000, 3),
        int(exit_time.timestamp() * 1_000_000_000),
    )
    assert recovered.position is None
    trade = recovered.status("OBSERVING")["trades"][0]
    assert Decimal(trade["net_reference"]) == (
        Decimal(trade["sell_notional"])
        - Decimal(
            next(row for row in recovered_ledger.events if row["kind"] == "ENTRY")[
                "buy_notional"
            ]
        )
        - Decimal(trade["fees"]["total"])
        - Decimal(trade["assumed_friction"])
    )
    assert trade["actual_fill"] is False
    recovered_ledger.close()


def test_disconnect_rejects_new_decisions_and_hash_tamper_fails(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    start = datetime(2026, 11, 3, 9, 45, tzinfo=ZoneInfo("Asia/Kolkata"))
    ledger = Ledger(path)
    sim = Simulator(ledger, _universe())
    sim.ingest(
        _snapshot(start, 100_000_000_000, 100_010_000_000, 1),
        int(start.timestamp() * 1_000_000_000),
    )
    later = start + timedelta(minutes=15)
    sim.ingest(
        _snapshot(later, 100_200_000_000, 100_210_000_000, 2, connected=False),
        int(later.timestamp() * 1_000_000_000),
    )
    assert sim.position is None and sim.stale
    assert any(row["kind"] == "GAP" for row in ledger.events)
    ledger.close()
    recovered_ledger = Ledger(path)
    recovered = Simulator(recovered_ledger, _universe())
    assert recovered.stale and not recovered.observations
    recovered_ledger.close()
    original = path.read_text()
    path.write_text(original.replace("FIXTURE1", "TAMPERED"))
    try:
        Ledger(path)
    except ValueError as exc:
        assert "hash invalid" in str(exc)
    else:
        raise AssertionError("tampered ledger accepted")


def test_only_one_decision_per_instrument_minute_and_verified_source(
    tmp_path: Path,
) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    sim = Simulator(ledger, _universe())
    start = datetime(2026, 11, 3, 9, 45, tzinfo=ZoneInfo("Asia/Kolkata"))
    sim.ingest(
        _snapshot(start, 100_000_000_000, 100_010_000_000, 1),
        int(start.timestamp() * 1_000_000_000),
    )
    at = start + timedelta(minutes=15)
    bad = _snapshot(at, 100_200_000_000, 100_210_000_000, 2)
    bad["instruments"][0]["market"]["provenance"]["source_protocol"] = "UNKNOWN"
    sim.ingest(bad, int(at.timestamp() * 1_000_000_000))
    assert sim.position is None and sim.stale
    sim.ingest(
        _snapshot(at, 100_200_000_000, 100_210_000_000, 2),
        int(at.timestamp() * 1_000_000_000),
    )
    assert sim.position is None  # Gap removed the past-only lookback.
    ledger.close()

    second_ledger = Ledger(tmp_path / "other-ledger.jsonl")
    second = Simulator(second_ledger, _universe())
    second.ingest(
        _snapshot(start, 100_000_000_000, 100_010_000_000, 1),
        int(start.timestamp() * 1_000_000_000),
    )
    second.ingest(
        _snapshot(at, 100_020_000_000, 100_030_000_000, 2),
        int(at.timestamp() * 1_000_000_000),
    )
    one_second = at + timedelta(seconds=1)
    second.ingest(
        _snapshot(one_second, 100_020_000_000, 100_030_000_000, 3),
        int(one_second.timestamp() * 1_000_000_000),
    )
    assert sum(row["kind"] == "REJECTED" for row in second_ledger.events) == 1
    second_ledger.close()


def test_partial_final_ledger_record_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append("START", mode="SIMULATION_ONLY")
    ledger.close()
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    try:
        Ledger(path)
    except ValueError as exc:
        assert "partial final record" in str(exc)
    else:
        raise AssertionError("partial ledger record accepted")
