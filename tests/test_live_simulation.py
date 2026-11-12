"""Synthetic software fixtures only; no market research observations."""

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quant.apps.live_simulation import (
    PORTFOLIO_DYNAMIC_VERSION,
    PORTFOLIO_VERSION,
    Ledger,
    PortfolioSimulator,
    Simulator,
    sizing_comparison_report,
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
                        *, unverified: bool = False, session_id: str = "fixture-connection") -> dict:
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
                "provenance": {"feed_session_id": session_id,
                               "arrival_ordinal": str(ordinal),
                               "source_protocol": "DhanStandardV2",
                               "source_frame_id": str(ordinal),
                               "packet_offset": 0,
                               "raw_frame_sha256": "a" * 64,
                               "raw_durable_at_ns": str(stamp)}}
        rows.append({"instrument": {"security_id": str(sid),
                                    "symbol": f"FIXTURE{sid}"}, "market": market})
    return {"mode": "OBSERVE_ONLY", "feed_connected": True,
            "journal_blocked": False, "session_id": session_id,
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


def _full_sizing_snapshot(when: datetime, price: int, ordinal: int,
                          ask_quantity: int = 80) -> dict:
    bid = price * 1_000_000_000
    ask = bid + 10_000_000
    snap = _portfolio_snapshot(when, {"1": (bid, ask, ordinal)})
    item = snap["instruments"][0]
    market = item["market"]
    market["packet_kind"] = "FULL"
    provenance = market["provenance"]
    bids = [{"price_nanos": str(bid - index * 1_000_000_000),
             "displayed_quantity": "100", "order_count": 2} for index in range(5)]
    asks = [{"price_nanos": str(ask + index * 1_000_000_000),
             "displayed_quantity": str(ask_quantity if index == 0 else 100),
             "order_count": 2} for index in range(5)]
    item["depth"] = {"source": "STANDARD_FULL", "derived_pairing": False,
                     "receive_timestamp_ns": market["source_receive_timestamp_ns"],
                     "provenance": dict(provenance), "bids": bids, "asks": asks,
                     "book_metrics": {"bid_quantity": "500",
                                      "ask_quantity": str(ask_quantity + 400)}}
    return snap


def test_paired_dynamic_sizing_shrinks_only_and_recovers_without_duplicate(tmp_path: Path) -> None:
    base_path, dynamic_path = tmp_path / "base.jsonl", tmp_path / "dynamic.jsonl"
    base_ledger = Ledger(base_path, PORTFOLIO_VERSION)
    dynamic_ledger = Ledger(dynamic_path, PORTFOLIO_DYNAMIC_VERSION)
    base = PortfolioSimulator(base_ledger, _universe())
    dynamic = PortfolioSimulator(dynamic_ledger, _universe(), sizing_policy="DYNAMIC_V1")
    base.log_comparison_opportunities = dynamic.log_comparison_opportunities = True
    start = datetime(2026, 11, 3, 9, 45, tzinfo=ZoneInfo("Asia/Kolkata"))
    for minute, price in ((0, 100), (1, 100), (5, 100), (10, 100), (15, 101),
                          (16, 98)):
        when = start + timedelta(minutes=minute)
        snap = _full_sizing_snapshot(when, price, minute + 1)
        stamp = int(when.timestamp() * 1_000_000_000)
        base.ingest(snap, stamp)
        dynamic.ingest(snap, stamp)
    base_buy = next(event for event in base_ledger.events if event["kind"] == "BUY")
    dynamic_buy = next(event for event in dynamic_ledger.events if event["kind"] == "BUY")
    assert base_buy["shares"] == 49
    assert 0 < dynamic_buy["shares"] <= 20 < base_buy["shares"]
    assert not base.positions and not dynamic.positions
    report = sizing_comparison_report(base_ledger, dynamic_ledger)
    assert report["status"] == "INSUFFICIENT_PROSPECTIVE_SESSIONS"
    assert report["paired_decision_inputs"] == 2
    assert report["paired_candidate_signal_count"] == 1
    assert report["independent_opportunity_sessions"] == 1
    for arm in (report["baseline"], report["dynamic"]):
        assert Decimal(arm["net_reference_inr"]) == (
            Decimal(arm["gross_reference_inr"]) - Decimal(arm["published_fees_inr"])
            - Decimal(arm["assumed_residual_friction_inr"]))
        assert arm["actual_fills"] == 0
    counts = len(base_ledger.events), len(dynamic_ledger.events)
    base_ledger.close(); dynamic_ledger.close()
    recovered_base_ledger = Ledger(base_path, PORTFOLIO_VERSION)
    recovered_dynamic_ledger = Ledger(dynamic_path, PORTFOLIO_DYNAMIC_VERSION)
    recovered_base = PortfolioSimulator(recovered_base_ledger, _universe())
    recovered_dynamic = PortfolioSimulator(recovered_dynamic_ledger, _universe(),
                                           sizing_policy="DYNAMIC_V1")
    when = start + timedelta(minutes=16)
    snap = _full_sizing_snapshot(when, 98, 17)
    stamp = int(when.timestamp() * 1_000_000_000)
    recovered_base.ingest(snap, stamp); recovered_dynamic.ingest(snap, stamp)
    assert (len(recovered_base_ledger.events), len(recovered_dynamic_ledger.events)) == counts
    assert sizing_comparison_report(recovered_base_ledger, recovered_dynamic_ledger) == report
    next_day = (start + timedelta(days=1)).date().isoformat()
    next(event for event in recovered_base_ledger.events if event["kind"] == "SELL")["day"] = next_day
    censored = sizing_comparison_report(recovered_base_ledger, recovered_dynamic_ledger)
    assert censored["status"] == "CROSS_SESSION_EXIT_CENSORED"
    assert censored["baseline"]["cross_session_exits"] == 1
    assert censored["paired_mean_incremental_net_inr_ci95"] == report["paired_mean_incremental_net_inr_ci95"]
    altered = next(event for event in recovered_dynamic_ledger.events if event["kind"] == "OBSERVED")
    altered["book_asks"][0]["displayed_quantity"] = "1"
    with pytest.raises(ValueError, match="source streams differ"):
        sizing_comparison_report(recovered_base_ledger, recovered_dynamic_ledger)
    recovered_base_ledger.close(); recovered_dynamic_ledger.close()


def test_dynamic_policy_abstains_without_complete_fresh_standard_book(tmp_path: Path) -> None:
    base_ledger = Ledger(tmp_path / "base.jsonl", PORTFOLIO_VERSION)
    dynamic_ledger = Ledger(tmp_path / "dynamic.jsonl", PORTFOLIO_DYNAMIC_VERSION)
    base = PortfolioSimulator(base_ledger, _universe())
    dynamic = PortfolioSimulator(dynamic_ledger, _universe(), sizing_policy="DYNAMIC_V1")
    base.log_comparison_opportunities = dynamic.log_comparison_opportunities = True
    start = datetime(2026, 11, 3, 9, 45, tzinfo=ZoneInfo("Asia/Kolkata"))
    for minute, price in ((0, 100), (1, 100), (5, 100), (10, 100), (15, 101)):
        when = start + timedelta(minutes=minute)
        snap = _portfolio_snapshot(when, {"1": (price * 1_000_000_000,
                                                 price * 1_000_000_000 + 10_000_000,
                                                 minute + 1)})
        stamp = int(when.timestamp() * 1_000_000_000)
        base.ingest(snap, stamp); dynamic.ingest(snap, stamp)
    assert any(row["kind"] == "BUY" for row in base_ledger.events)
    assert not any(row["kind"] == "BUY" for row in dynamic_ledger.events)
    assert any(row["kind"] == "REJECT" and
               row["reason"] == "DYNAMIC_COMPLETE_STANDARD_BOOK_UNAVAILABLE"
               for row in dynamic_ledger.events)
    assert sizing_comparison_report(base_ledger, dynamic_ledger)["status"] == "OPEN_VIRTUAL_POSITIONS"
    base_ledger.close(); dynamic_ledger.close()


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


def test_portfolio_insufficient_lookback_post_restart_rejects_past_lookback_unavailable_and_flat_stock_rejects_momentum(
    tmp_path: Path,
) -> None:
    path = tmp_path / "portfolio-v2.jsonl"
    ledger = Ledger(path, PORTFOLIO_VERSION)
    sim = PortfolioSimulator(ledger, _universe())
    start = datetime(2026, 11, 3, 9, 45, tzinfo=ZoneInfo("Asia/Kolkata"))

    def make_quotes(p1: int, p2: int, ordinal: int) -> dict[str, tuple[int, int, int]]:
        return {str(sid): ((p1 if sid == 1 else p2 if sid == 2 else 100) * 1_000_000_000,
                           (p1 if sid == 1 else p2 if sid == 2 else 100) * 1_000_000_000 + 10_000_000,
                           ordinal) for sid in range(1, 26)}

    def tick(minutes: int, p1: int = 100, p2: int = 100, ordinal: int = 1, session: str = "session-1") -> None:
        when = start + timedelta(minutes=minutes)
        sim.ingest(
            _portfolio_snapshot(when, make_quotes(p1, p2, ordinal), session_id=session),
            int(when.timestamp() * 1_000_000_000),
        )

    # T=0 in session-1
    tick(0, 100, 100, ordinal=1, session="session-1")
    # T=1m in session-1 (only 1m history, must reject with PAST_LOOKBACK_UNAVAILABLE)
    tick(1, 100, 100, ordinal=2, session="session-1")
    rejections_1m = [e for e in ledger.events if e["kind"] == "REJECT" and "09:46" in e.get("decision_slot", "")]
    assert len(rejections_1m) == 25
    assert all(e["reason"] == "PAST_LOOKBACK_UNAVAILABLE" for e in rejections_1m)
    assert not any(e["reason"] == "NO_POSITIVE_NET_MOMENTUM_PROXY" for e in rejections_1m)

    # Socket restart occurs: new feed session "session-2" at T=2m
    # First packet triggers FEED_SESSION_CHANGED gap and clears session
    when_restart = start + timedelta(minutes=2)
    sim.ingest(
        _portfolio_snapshot(when_restart, make_quotes(100, 100, 1), session_id="session-2"),
        int(when_restart.timestamp() * 1_000_000_000),
    )
    assert any(e["kind"] == "GAP" and e["reason"] == "FEED_SESSION_CHANGED" for e in ledger.events)
    assert sim.feed_session is None

    # Next tick in session-2 establishes new session
    when_established = start + timedelta(minutes=2, seconds=1)
    sim.ingest(
        _portfolio_snapshot(when_established, make_quotes(100, 100, 2), session_id="session-2"),
        int(when_established.timestamp() * 1_000_000_000),
    )
    assert sim.feed_session == "session-2"
    assert len(sim.observations["FIXTURE1"]) == 1

    # T=3m in session-2: 1 minute after restart (buffer age ~60s >= 30s)
    # Under old defect, 60s fallback produced spurious NO_POSITIVE_NET_MOMENTUM_PROXY.
    # Now it must strictly abstain with PAST_LOOKBACK_UNAVAILABLE.
    when_short = start + timedelta(minutes=3)
    sim.ingest(
        _portfolio_snapshot(when_short, make_quotes(100, 100, 3), session_id="session-2"),
        int(when_short.timestamp() * 1_000_000_000),
    )
    rejections_3m = [e for e in ledger.events if e["kind"] == "REJECT" and "09:48" in e.get("decision_slot", "")]
    assert len(rejections_3m) == 25
    assert all(e["reason"] == "PAST_LOOKBACK_UNAVAILABLE" for e in rejections_3m)
    assert not any(e["reason"] == "NO_POSITIVE_NET_MOMENTUM_PROXY" for e in rejections_3m)

    # T=17m 1s in session-2: exactly 15 minutes after session-2 established (09:47:01 to 10:02:01)
    # Full 15-minute lookback is now available within session-2!
    # Stock 1 advances +100 bps (100 -> 101): passes momentum hurdle -> BUY
    # Stock 2 is flat (100 -> 100): genuine consolidation -> NO_POSITIVE_NET_MOMENTUM_PROXY
    when_full = start + timedelta(minutes=17, seconds=1)
    sim.ingest(
        _portfolio_snapshot(when_full, make_quotes(101, 100, 4), session_id="session-2"),
        int(when_full.timestamp() * 1_000_000_000),
    )

    decisions_17m = [e for e in ledger.events if "10:02" in e.get("decision_slot", "")]
    buy_events = [e for e in decisions_17m if e["kind"] == "BUY"]
    assert len(buy_events) == 1
    assert buy_events[0]["symbol"] == "FIXTURE1"

    reject_events = [e for e in decisions_17m if e["kind"] == "REJECT"]
    assert any(e["symbol"] == "FIXTURE2" and e["reason"] == "NO_POSITIVE_NET_MOMENTUM_PROXY"
               for e in reject_events)
    ledger.close()


def test_dynamic_volatility_stress_reduces_size_without_changing_signal(tmp_path: Path) -> None:
    base_ledger = Ledger(tmp_path / "base.jsonl", PORTFOLIO_VERSION)
    dynamic_ledger = Ledger(tmp_path / "dynamic.jsonl", PORTFOLIO_DYNAMIC_VERSION)
    base = PortfolioSimulator(base_ledger, _universe())
    dynamic = PortfolioSimulator(dynamic_ledger, _universe(), sizing_policy="DYNAMIC_V1")
    base.log_comparison_opportunities = dynamic.log_comparison_opportunities = True
    start = datetime(2026, 11, 3, 9, 45, tzinfo=ZoneInfo("Asia/Kolkata"))
    for minute, price in ((0, 100), (1, 110), (5, 90), (10, 105), (15, 101)):
        when = start + timedelta(minutes=minute)
        snap = _full_sizing_snapshot(when, price, minute + 1, ask_quantity=1000)
        stamp = int(when.timestamp() * 1_000_000_000)
        base.ingest(snap, stamp); dynamic.ingest(snap, stamp)
    base_buy = next(event for event in base_ledger.events if event["kind"] == "BUY")
    dynamic_buy = next(event for event in dynamic_ledger.events if event["kind"] == "BUY")
    assert 0 < dynamic_buy["shares"] < base_buy["shares"]
    assert dynamic_buy["shares"] < 250  # Depth cap is loose; volatility sets this bound.
    sized = next(event for event in dynamic_ledger.events if event["kind"] == "SIZING")
    assert Decimal(sized["trailing_volatility_15m_bps"]) > 100
    assert Decimal(dynamic_buy["risk_bound_inr"]) == Decimal(sized["volatility_stress_bound_inr"])
    assert dynamic._open_risk() <= Decimal("500")
    assert sizing_comparison_report(base_ledger, dynamic_ledger)["paired_candidate_signal_count"] == 1
    base_ledger.close(); dynamic_ledger.close()


def test_dynamic_rejects_missing_timestamp_incomplete_or_crossed_book(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "dynamic.jsonl", PORTFOLIO_DYNAMIC_VERSION)
    sim = PortfolioSimulator(ledger, _universe(), sizing_policy="DYNAMIC_V1")
    start = datetime(2026, 11, 3, 9, 45, tzinfo=ZoneInfo("Asia/Kolkata"))
    for minute, defect in ((0, None), (15, "incomplete"), (16, None),
                           (17, "crossed"), (18, "timestamp")):
        when = start + timedelta(minutes=minute)
        snap = _full_sizing_snapshot(when, 100 if minute == 0 else 101, minute + 1)
        item = snap["instruments"][0]
        if defect == "incomplete":
            item["depth"]["asks"].pop()
        elif defect == "crossed":
            item["market"]["best_ask_price_nanos"] = item["market"]["best_bid_price_nanos"]
        elif defect == "timestamp":
            del item["market"]["provenance"]["raw_durable_at_ns"]
        stamp = int(when.timestamp() * 1_000_000_000)
        sim.ingest(snap, stamp)
    reasons = {event["reason"] for event in ledger.events if event["kind"] == "REJECT"}
    assert {"INVALID_FULL_BOOK", "CROSSED_OR_EMPTY_BOOK", "QUOTE_FIELDS_MISSING",
            "DYNAMIC_TRAILING_VOLATILITY_UNAVAILABLE"} <= reasons
    assert not any(event["kind"] == "BUY" for event in ledger.events)
    ledger.close()
