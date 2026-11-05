"""Targeted offline tests for auditable, fail-closed 3-year historical acquisition."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
import hashlib
import io
import json
import math
from pathlib import Path
import urllib.error
from zoneinfo import ZoneInfo

import pytest

from quant.acquisition.contracts import CashInstrument
from quant.acquisition.dhan import HistoricalHttpResponse, HistoricalRequest
from tools.execute_cross_equity_acquisition import execute_batch
from tools.execute_full_3yr_acquisition import (
    determine_research_eligibility,
    determine_session_coverage,
    determine_window_ca_status,
    generate_summary_report,
    reconcile_exchange_sessions,
    run_acquisition,
    validate_candle_data,
    verify_existing_checkpoint_file,
)

IST = ZoneInfo("Asia/Kolkata")


def _make_manifest(tmp_path: Path, windows: list[dict]) -> Path:
    p = tmp_path / "manifest.json"
    p.write_text(
        json.dumps(
            {
                "schema_version": "alpha-grid-historical-manifest-v1",
                "universe_version": "alpha-grid-cross-stock-universe-v1",
                "windows": windows,
            }
        )
    )
    return p


def _make_valid_raw_response(
    start: date,
    days: int = 5,
    bars_per_day: int = 2,
    base_price: float = 100.0,
) -> bytes:
    stamps = []
    opens = []
    highs = []
    lows = []
    closes = []
    volumes = []
    for d_idx in range(days):
        day = start + timedelta(days=d_idx)
        if day.weekday() >= 5:  # skip weekends
            continue
        for b_idx in range(bars_per_day):
            bar_time = time(9, 15 + b_idx)
            dt = datetime.combine(day, bar_time, IST)
            stamps.append(int(dt.timestamp()))
            opens.append(base_price + b_idx)
            highs.append(base_price + b_idx + 1.0)
            lows.append(base_price + b_idx - 0.5)
            closes.append(base_price + b_idx + 0.5)
            volumes.append(100.0)

    return json.dumps(
        {
            "timestamp": stamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    ).encode()


def _save_response(path: Path, raw: bytes, req: HistoricalRequest) -> None:
    digest = hashlib.sha256(raw).hexdigest()
    path.write_bytes(raw)
    path.with_suffix(".sha256").write_text(digest + "\n")
    path.with_suffix(".meta.json").write_text(json.dumps({
        "schema_version": "dhan-chart-raw-v1",
        "endpoint": req.endpoint,
        "method": "POST",
        "request": req.payload(),
        "sha256": digest,
        "received_at": "2024-02-01T00:00:00+00:00",
        "http_status": 200,
    }))


class MockTransport:
    def __init__(self, responses: dict[str, bytes | Exception]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def post_json(self, url: str, payload: dict[str, object], token: str) -> bytes:
        return self.post_json_with_metadata(url, payload, token).raw

    def post_json_with_metadata(
        self, url: str, payload: dict[str, object], token: str
    ) -> HistoricalHttpResponse:
        key = f"{payload['securityId']}-{payload['fromDate']}"
        self.calls.append(key)
        resp = self.responses.get(key)
        if isinstance(resp, Exception):
            raise resp
        if resp is None:
            resp = json.dumps({"timestamp": [], "open": [], "high": [], "low": [], "close": [], "volume": []}).encode()
        return HistoricalHttpResponse(resp, 200, "Thu, 05 Nov 2026 12:00:00 GMT")


def test_fatal_auth_stops_immediately_and_persists_checkpoint(tmp_path: Path) -> None:
    manifest_p = _make_manifest(
        tmp_path,
        [
            {"security_id": "2885", "symbol": "RELIANCE", "venue": "NSE", "segment": "NSE_EQ", "start": "2024-01-01", "end": "2024-01-30"},
            {"security_id": "1333", "symbol": "HDFCBANK", "venue": "NSE", "segment": "NSE_EQ", "start": "2024-01-01", "end": "2024-01-30"},
        ],
    )
    checkpoint_p = tmp_path / "checkpoint.json"
    err = urllib.error.HTTPError("https://api.dhan.co", 401, "Unauthorized", {}, io.BytesIO(b"Unauthorized"))
    transport = MockTransport({"2885-2024-01-01 00:00:00": err})

    res = run_acquisition(
        tmp_path,
        manifest_path=manifest_p,
        checkpoint_path=checkpoint_p,
        token="test-token",
        transport=transport,
        delay_seconds=0.0,
        sleep=lambda _: None,
    )

    assert res["fatal"] is True
    assert res["errors"] == 1
    assert res["executed"] == 0
    assert res["status"] == "FATAL"
    # Ensure transport stopped immediately on first request and did not call second window
    assert len(transport.calls) == 1
    # Ensure checkpoint was persisted on disk before exiting
    assert checkpoint_p.exists()
    cp = json.loads(checkpoint_p.read_text())
    assert "2885-1-2024-01-01-2024-01-30" in cp
    assert cp["2885-1-2024-01-01-2024-01-30"]["status"] == "FATAL_AUTH_ERROR"

    # A later operator-supplied valid token may resume the same missing window.
    recovered = MockTransport({"2885-2024-01-01 00:00:00": _make_valid_raw_response(date(2024, 1, 1))})
    resumed = run_acquisition(tmp_path, manifest_path=manifest_p, checkpoint_path=checkpoint_p, token="new-test-token", transport=recovered, max_requests=1, delay_seconds=0, sleep=lambda _: None)
    assert resumed["total_completed"] == 1
    assert len(recovered.calls) == 1


def test_incomplete_manifest_returns_incomplete_status(tmp_path: Path) -> None:
    manifest_p = _make_manifest(
        tmp_path,
        [
            {"security_id": "2885", "symbol": "RELIANCE", "venue": "NSE", "segment": "NSE_EQ", "start": "2024-01-01", "end": "2024-01-30"},
            {"security_id": "1333", "symbol": "HDFCBANK", "venue": "NSE", "segment": "NSE_EQ", "start": "2024-01-01", "end": "2024-01-30"},
        ],
    )
    checkpoint_p = tmp_path / "checkpoint.json"
    raw_rel = _make_valid_raw_response(date(2024, 1, 1), days=5)
    transport = MockTransport({"2885-2024-01-01 00:00:00": raw_rel})

    # Limit to 1 request
    res = run_acquisition(
        tmp_path,
        manifest_path=manifest_p,
        checkpoint_path=checkpoint_p,
        token="test-token",
        transport=transport,
        max_requests=1,
        delay_seconds=0.0,
        sleep=lambda _: None,
    )

    assert res["status"] == "INCOMPLETE"
    assert res["total_completed"] == 1
    assert res["total_planned"] == 2
    assert res["executed"] == 1
    assert res["fatal"] is False


def test_hash_mismatch_fails_closed_without_overwriting(tmp_path: Path) -> None:
    manifest_p = _make_manifest(
        tmp_path,
        [
            {"security_id": "2885", "symbol": "RELIANCE", "venue": "NSE", "segment": "NSE_EQ", "start": "2024-01-01", "end": "2024-01-30"},
        ],
    )
    checkpoint_p = tmp_path / "checkpoint.json"
    raw_rel = _make_valid_raw_response(date(2024, 1, 1), days=5)
    target_json = tmp_path / "2885-1-2024-01-01-2024-01-30.json"
    target_sha = target_json.with_suffix(".sha256")
    target_meta = target_json.with_suffix(".meta.json")

    target_json.write_bytes(raw_rel)
    target_sha.write_text("bad-corrupted-hash\n")
    target_meta.write_text(json.dumps({
        "schema_version": "dhan-chart-raw-v1",
        "endpoint": "https://api.dhan.co/v2/charts/intraday",
        "method": "POST",
        "request": {"securityId": "2885", "exchangeSegment": "NSE_EQ", "instrument": "EQUITY", "interval": "1", "oi": False, "fromDate": "2024-01-01 00:00:00", "toDate": "2024-01-30 00:00:00"},
        "sha256": "bad-corrupted-hash",
    }))

    transport = MockTransport({})
    res = run_acquisition(
        tmp_path,
        manifest_path=manifest_p,
        checkpoint_path=checkpoint_p,
        token="test-token",
        transport=transport,
        delay_seconds=0.0,
        sleep=lambda _: None,
    )

    # Must fail closed: not overwrite and record corrupt status
    assert len(transport.calls) == 0
    assert target_json.read_bytes() == raw_rel
    cp = json.loads(checkpoint_p.read_text())
    assert cp["2885-1-2024-01-01-2024-01-30"]["status"] == "CORRUPT_HASH_MISMATCH"
    assert cp["2885-1-2024-01-01-2024-01-30"]["source_integrity"] == "DEFECTIVE_SOURCE"
    assert res["status"] == "INCOMPLETE"


def test_corrupt_checkpoint_recovery_preserves_evidence(tmp_path: Path) -> None:
    manifest_p = _make_manifest(
        tmp_path,
        [
            {"security_id": "2885", "symbol": "RELIANCE", "venue": "NSE", "segment": "NSE_EQ", "start": "2024-01-01", "end": "2024-01-30"},
        ],
    )
    checkpoint_p = tmp_path / "checkpoint.json"
    checkpoint_p.write_bytes(b"{corrupted json syntax invalid...")

    res = run_acquisition(
        tmp_path,
        manifest_path=manifest_p,
        checkpoint_path=checkpoint_p,
        verify_only=True,
    )

    # Checkpoint should be safely recovered
    assert checkpoint_p.exists()
    cp = json.loads(checkpoint_p.read_text())
    assert isinstance(cp, dict)
    quarantine_files = list((tmp_path / ".quarantine").glob("corrupt-checkpoint-*"))
    assert len(quarantine_files) == 1
    assert quarantine_files[0].read_bytes() == b"{corrupted json syntax invalid..."


def test_strict_candle_shape_ordering_duplicates_and_finite_checks() -> None:
    req = HistoricalRequest(
        CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE"),
        date(2024, 1, 1),
        date(2024, 1, 5),
        1,
    )

    # 1. Non-dict root
    res1 = validate_candle_data(b"[1, 2, 3]", req)
    assert res1["valid"] is False
    assert res1["shape_valid"] is False

    # 2. Unequal lengths
    res2 = validate_candle_data(
        json.dumps({
            "timestamp": [1704080700],
            "open": [100.0, 101.0],
            "high": [102.0],
            "low": [99.0],
            "close": [100.0],
            "volume": [10.0],
        }).encode(),
        req,
    )
    assert res2["valid"] is False
    assert "lengths" in res2["error"]

    # 3. Unordered timestamps
    s1 = int(datetime(2024, 1, 1, 9, 20, tzinfo=IST).timestamp())
    s2 = int(datetime(2024, 1, 1, 9, 15, tzinfo=IST).timestamp())
    res3 = validate_candle_data(
        json.dumps({
            "timestamp": [s1, s2],
            "open": [100.0, 100.0],
            "high": [101.0, 101.0],
            "low": [99.0, 99.0],
            "close": [100.5, 100.5],
            "volume": [100.0, 100.0],
        }).encode(),
        req,
    )
    assert res3["valid"] is False
    assert res3["unordered_timestamps"] == 1

    # 4. Duplicate timestamps (conflicting)
    res4 = validate_candle_data(
        json.dumps({
            "timestamp": [s2, s2],
            "open": [100.0, 105.0],
            "high": [101.0, 106.0],
            "low": [99.0, 104.0],
            "close": [100.5, 105.5],
            "volume": [100.0, 200.0],
        }).encode(),
        req,
    )
    assert res4["valid"] is False
    assert res4["duplicate_timestamps"] == 1
    assert res4["conflicting_duplicates"] == 1

    # 5. Non-finite values (NaN / boolean)
    res5 = validate_candle_data(
        b'{"timestamp": [1704080700], "open": [null], "high": [101.0], "low": [99.0], "close": [100.0], "volume": [10.0]}',
        req,
    )
    assert res5["valid"] is False
    assert res5["non_finite_count"] >= 1

    # 6. OHLC violation
    res6 = validate_candle_data(
        json.dumps({
            "timestamp": [s2],
            "open": [105.0],
            "high": [101.0],  # High < Open!
            "low": [99.0],
            "close": [100.0],
            "volume": [10.0],
        }).encode(),
        req,
    )
    assert res6["valid"] is False
    assert res6["ohlc_violations"] == 1


def test_zero_bars_point_in_time_classification() -> None:
    # MONEYVIEW pre-IPO
    cov_mv = determine_session_coverage("MONEYVIEW", date(2024, 1, 1), date(2024, 1, 30), bars=0, unique_sessions=[])
    assert cov_mv == "NOT_YET_LISTED"

    # CUPID pre-July 2024
    cov_cupid = determine_session_coverage("CUPID", date(2024, 1, 1), date(2024, 1, 30), bars=0, unique_sessions=[])
    assert cov_cupid == "ZERO_BARS_CAUSE_UNVERIFIED"

    # Other stock zero bars
    cov_other = determine_session_coverage("INFY", date(2024, 1, 1), date(2024, 1, 30), bars=0, unique_sessions=[])
    assert cov_other == "ZERO_BARS_EMPTY"
    empty = reconcile_exchange_sessions("INFY", date(2024, 1, 1), date(2024, 1, 6), 0, {})
    assert empty["missing_sessions"] == ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]

    r_elig, e_elig = determine_research_eligibility(0, True, cov_cupid, "NO_KNOWN_BONUS_OR_SPLIT_RECORDED")
    assert r_elig == "INELIGIBLE_NO_DATA"
    assert e_elig == "INELIGIBLE_NO_DATA"


def test_calendar_aware_session_reconciliation_detects_holidays_and_special_sessions() -> None:
    # January 2024:
    # Window: 2024-01-01 to 2024-01-31 (pilot window)
    # Total weekdays: 23
    # Declared holidays: 2024-01-22 (Consecration) and 2024-01-26 (Republic Day) -> 21 weekdays
    # Special session: 2024-01-20 (Saturday DR live session)
    # Total expected sessions: 21 + 1 = 22 sessions
    recon = reconcile_exchange_sessions(
        symbol="RELIANCE",
        start=date(2024, 1, 1),
        end=date(2024, 1, 31),
        bars=22 * 375,
        session_bar_counts={
            # 21 regular weekdays with 375 bars
            "2024-01-01": 375, "2024-01-02": 375, "2024-01-03": 375, "2024-01-04": 375, "2024-01-05": 375,
            "2024-01-08": 375, "2024-01-09": 375, "2024-01-10": 375, "2024-01-11": 375, "2024-01-12": 375,
            "2024-01-15": 375, "2024-01-16": 375, "2024-01-17": 375, "2024-01-18": 375, "2024-01-19": 375,
            # Saturday DR special session
            "2024-01-20": 105,
            # Jan 22 is exchange holiday (Consecration) - not in session_bar_counts
            "2024-01-23": 375, "2024-01-24": 375, "2024-01-25": 375,
            # Jan 26 is exchange holiday (Republic Day) - not in session_bar_counts
            "2024-01-29": 375, "2024-01-30": 375,
        },
    )
    # Reconciled full coverage: all expected trading sessions present, holidays respected
    assert recon["coverage_classification"] == "FULL_SESSION_COVERAGE"
    assert len(recon["missing_sessions"]) == 0
    assert len(recon["truncated_sessions"]) == 0
    assert len(recon["special_sessions_observed"]) == 1
    assert recon["special_sessions_observed"][0]["kind"] == "SATURDAY_DISASTER_RECOVERY"


def test_calendar_aware_session_reconciliation_flags_missing_and_truncated_minutes_explicitly() -> None:
    # Session missing entirely
    recon_missing = reconcile_exchange_sessions(
        symbol="RELIANCE",
        start=date(2024, 1, 1),
        end=date(2024, 1, 6),
        bars=4 * 375,
        session_bar_counts={
            "2024-01-01": 375,
            "2024-01-02": 375,
            # Jan 3 missing!
            "2024-01-04": 375,
            "2024-01-05": 375,
        },
    )
    assert recon_missing["coverage_classification"] == "PARTIAL_COVERAGE_MISSING_SESSIONS"
    assert recon_missing["missing_sessions"] == ["2024-01-03"]

    # Session truncated (deficit of minutes)
    recon_truncated = reconcile_exchange_sessions(
        symbol="RELIANCE",
        start=date(2024, 1, 1),
        end=date(2024, 1, 6),
        bars=4 * 375 + 350,
        session_bar_counts={
            "2024-01-01": 375,
            "2024-01-02": 375,
            "2024-01-03": 350,  # 25 minute deficit!
            "2024-01-04": 375,
            "2024-01-05": 375,
        },
    )
    assert recon_truncated["coverage_classification"] == "PARTIAL_COVERAGE_INCOMPLETE_MINUTES"
    assert len(recon_truncated["truncated_sessions"]) == 1
    assert recon_truncated["truncated_sessions"][0]["date"] == "2024-01-03"
    assert recon_truncated["truncated_sessions"][0]["deficit"] == 25


def test_muhurat_and_preopen_and_cas_timestamps_accepted_as_valid() -> None:
    req = HistoricalRequest(
        CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE"),
        date(2023, 11, 1),
        date(2023, 11, 15),
        1,
    )
    # Pre-open discovery at 09:07 IST
    s_pre = int(datetime(2023, 11, 10, 9, 7, tzinfo=IST).timestamp())
    # Continuous bar at 09:15 IST
    s_cont = int(datetime(2023, 11, 10, 9, 15, tzinfo=IST).timestamp())
    # Closing auction / post-market bar at 15:35 IST
    s_close = int(datetime(2023, 11, 10, 15, 35, tzinfo=IST).timestamp())
    # Diwali Muhurat evening session bar at 18:15 IST on 2023-11-12
    s_muhurat = int(datetime(2023, 11, 12, 18, 15, tzinfo=IST).timestamp())

    raw = json.dumps({
        "timestamp": [s_pre, s_cont, s_close, s_muhurat],
        "open": [100.0, 100.5, 101.0, 102.0],
        "high": [100.0, 101.0, 101.0, 102.5],
        "low": [100.0, 100.0, 100.8, 101.5],
        "close": [100.0, 100.8, 101.0, 102.0],
        "volume": [50.0, 500.0, 100.0, 1000.0],
    }).encode()

    val = validate_candle_data(raw, req)
    assert val["valid"] is True
    assert val["out_of_bounds"] == 0
    assert val["bars"] == 4
    assert val["session_bar_counts"] == {"2023-11-10": 3, "2023-11-12": 1}
    assert val["continuous_bars_count"] == 1
    assert val["pre_open_bars_count"] == 1
    assert val["post_close_bars_count"] == 1
    assert val["muhurat_bars_count"] == 1
    assert val["has_auction_bars"] is True


def test_cas_is_instrument_specific_and_auction_bars_block_continuous_execution() -> None:
    # 1. RELIANCE on verified CAS date 2026-10-15 expects 360 continuous bars
    recon_rel = reconcile_exchange_sessions(
        symbol="RELIANCE",
        start=date(2026, 10, 15),
        end=date(2026, 10, 16),
        bars=360,
        session_bar_counts={"2026-10-15": 360},
    )
    assert recon_rel["coverage_classification"] == "FULL_SESSION_COVERAGE"
    assert recon_rel["special_sessions_observed"][0]["kind"] == "CAS_PHASE_1_CONTINUOUS"
    assert recon_rel["cas_semantics_unverified"] is False

    # 2. Non-CAS stock (e.g. PCJEWELLER) on 2026-10-15 does NOT get automatic CAS 360 expectation
    recon_pcj = reconcile_exchange_sessions(
        symbol="PCJEWELLER",
        start=date(2026, 10, 15),
        end=date(2026, 10, 16),
        bars=360,
        session_bar_counts={"2026-10-15": 360},
    )
    assert recon_pcj["coverage_classification"] == "PARTIAL_COVERAGE_INCOMPLETE_MINUTES"
    assert recon_pcj["cas_semantics_unverified"] is True

    # 3. Research eligibility: auction bars require masking and block continuous execution
    r_elig, e_elig = determine_research_eligibility(
        bars=376,
        is_valid=True,
        cov_status="FULL_SESSION_COVERAGE",
        ca_status="VERIFIED_UNADJUSTED_EMPIRICAL",
        has_auction_bars=True,
    )
    assert r_elig == "ELIGIBLE_AUCTION_CANDLES_MASKING_REQUIRED"
    assert e_elig == "INELIGIBLE_AUCTION_BARS_REQUIRE_AUCTION_EXECUTION_MODEL"


def test_ex_date_spanning_window_and_unverified_execution_status() -> None:
    # RELIANCE 1:1 Bonus ex_date is 2024-10-28
    # Window 2024-10-01 to 2024-11-01 spans the ex-date
    ca = determine_window_ca_status("RELIANCE", date(2024, 10, 1), date(2024, 11, 1))
    assert ca["corporate_action_status"] == "EX_DATE_SPANNED_WITHIN_WINDOW"
    assert ca["adjustment_factor"] is None
    assert ca["evidence_basis"] == "UNVERIFIED_INTRADAY_ADJUSTMENT"

    r_elig, e_elig = determine_research_eligibility(
        bars=5000, is_valid=True, cov_status="FULL_SESSION_COVERAGE", ca_status=ca["corporate_action_status"]
    )
    assert r_elig == "REQUIRES_EX_DATE_DISCONTINUITY_PURGING"
    assert e_elig == "INELIGIBLE_EX_DATE_DISCONTINUITY"

    # Window prior to ex-date: 2024-01-01 to 2024-01-30
    ca_pre = determine_window_ca_status("RELIANCE", date(2024, 1, 1), date(2024, 1, 30))
    assert ca_pre["corporate_action_status"] == "VERIFIED_BACK_ADJUSTED"
    assert ca_pre["adjustment_factor"] == 2.0
    r_pre, e_pre = determine_research_eligibility(
        bars=5000, is_valid=True, cov_status="FULL_SESSION_COVERAGE", ca_status=ca_pre["corporate_action_status"]
    )
    assert r_pre == "ELIGIBLE"
    assert e_pre == "INELIGIBLE_BACK_ADJUSTED_REQUIRES_RECONSTRUCTION"

    # Stock with NO recorded action (e.g. INFY): must NOT claim ELIGIBLE_RAW_UNADJUSTED without evidence
    ca_infy = determine_window_ca_status("INFY", date(2024, 1, 1), date(2024, 1, 30))
    assert ca_infy["corporate_action_status"] == "NO_KNOWN_BONUS_OR_SPLIT_RECORDED"
    r_infy, e_infy = determine_research_eligibility(
        bars=5000, is_valid=True, cov_status="FULL_SESSION_COVERAGE", ca_status=ca_infy["corporate_action_status"]
    )
    assert r_infy == "ELIGIBLE"
    assert e_infy == "UNVERIFIED_PRICE_BASIS"


def test_resume_verifies_provenance_and_persists_without_network(tmp_path: Path) -> None:
    manifest_p = _make_manifest(
        tmp_path,
        [
            {"security_id": "2885", "symbol": "RELIANCE", "venue": "NSE", "segment": "NSE_EQ", "start": "2024-01-01", "end": "2024-01-30"},
        ],
    )
    checkpoint_p = tmp_path / "checkpoint.json"
    raw_rel = _make_valid_raw_response(date(2024, 1, 1), days=5)
    target_json = tmp_path / "2885-1-2024-01-01-2024-01-30.json"
    target_sha = target_json.with_suffix(".sha256")
    target_meta = target_json.with_suffix(".meta.json")

    target_json.write_bytes(raw_rel)
    computed_sha = hashlib.sha256(raw_rel).hexdigest()
    target_sha.write_text(computed_sha + "\n")
    target_meta.write_text(json.dumps({
        "schema_version": "dhan-chart-raw-v1",
        "endpoint": "https://api.dhan.co/v2/charts/intraday",
        "method": "POST",
        "request": {
            "securityId": "2885",
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "interval": "1",
            "oi": False,
            "fromDate": "2024-01-01 00:00:00",
            "toDate": "2024-01-30 00:00:00",
        },
        "sha256": computed_sha,
        "received_at": "2024-02-01T00:00:00+00:00",
        "http_status": 200,
    }))

    # Verify existing without network
    res = run_acquisition(
        tmp_path,
        manifest_path=manifest_p,
        checkpoint_path=checkpoint_p,
        verify_only=True,
    )

    assert res["status"] == "VERIFY_ONLY_FINISHED"
    assert res["total_completed"] == 1
    assert res["errors"] == 0
    assert checkpoint_p.exists()
    cp = json.loads(checkpoint_p.read_text())
    entry = cp["2885-1-2024-01-01-2024-01-30"]
    assert entry["status"] == "COMPLETED"
    assert entry["source_integrity"] == "VALID_SOURCE"
    assert entry["sha256"] == computed_sha


def test_generate_summary_report_derives_strictly_from_window_records(tmp_path: Path) -> None:
    manifest_p = _make_manifest(
        tmp_path,
        [
            {"security_id": "2885", "symbol": "RELIANCE", "venue": "NSE", "segment": "NSE_EQ", "start": "2024-01-01", "end": "2024-01-30"},
            {"security_id": "2885", "symbol": "RELIANCE", "venue": "NSE", "segment": "NSE_EQ", "start": "2024-01-30", "end": "2024-04-29"},
        ],
    )
    checkpoint_p = tmp_path / "acquisition-checkpoint.json"
    # A checkpoint alone is not evidence; the first window also has verified bytes.
    req = HistoricalRequest(CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE"), date(2024, 1, 1), date(2024, 1, 30), 1)
    saved = tmp_path / "2885-1-2024-01-01-2024-01-30.json"
    _save_response(saved, _make_valid_raw_response(date(2024, 1, 1)), req)
    checkpoint_p.write_text(json.dumps({
        "2885-1-2024-01-01-2024-01-30": {
            "key": "2885-1-2024-01-01-2024-01-30",
            "symbol": "RELIANCE",
            "security_id": "2885",
            "start": "2024-01-01",
            "end": "2024-01-30",
            "status": "COMPLETED",
            "bars_count": 5000,
            "byte_size": 250000,
            "coverage_classification": "FULL_SESSION_COVERAGE",
            "source_integrity": "VALID_SOURCE",
            "corporate_action_status": "VERIFIED_BACK_ADJUSTED",
            "corporate_action_factor": 2.0,
            "exploratory_return_model_eligibility": "ELIGIBLE",
            "historical_execution_model_eligibility": "INELIGIBLE_BACK_ADJUSTED_REQUIRES_RECONSTRUCTION",
        }
    }))

    report = generate_summary_report(tmp_path, manifest_path=manifest_p)
    sym_row = report["universe_summary"][0]
    assert sym_row["symbol"] == "RELIANCE"
    assert sym_row["windows_planned"] == 2
    assert sym_row["windows_completed"] == 1
    # Derived status must reflect incomplete acquisition, NOT hardcoded success!
    assert sym_row["point_in_time_status"] == "INCOMPLETE_ACQUISITION"
    assert sym_row["exploratory_return_model_eligibility"] == "INCOMPLETE_PENDING_WINDOWS"
    assert sym_row["historical_execution_model_eligibility"] == "INCOMPLETE_PENDING_WINDOWS"
    assert "2024-01-30->2024-04-29 (PENDING)" in sym_row["missing_windows"]
    saved.unlink()
    report_after_loss = generate_summary_report(tmp_path, manifest_path=manifest_p)
    assert report_after_loss["total_completed_requests"] == 0


def test_missing_raw_or_provenance_cannot_resume_from_checkpoint(tmp_path: Path) -> None:
    w = {"security_id": "2885", "symbol": "RELIANCE", "venue": "NSE", "segment": "NSE_EQ", "start": "2024-01-01", "end": "2024-01-30"}
    manifest = _make_manifest(tmp_path, [w])
    key = "2885-1-2024-01-01-2024-01-30"
    (tmp_path / "checkpoint.json").write_text(json.dumps({key: {"status": "COMPLETED", "source_integrity": "VALID_SOURCE"}}))
    result = run_acquisition(tmp_path, manifest_path=manifest, checkpoint_path=tmp_path / "checkpoint.json", verify_only=True)
    assert result["total_completed"] == 0
    assert json.loads((tmp_path / "checkpoint.json").read_text())[key]["status"] == "MISSING"
    req = HistoricalRequest(CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE"), date(2024, 1, 1), date(2024, 1, 30), 1)
    raw = _make_valid_raw_response(date(2024, 1, 1))
    target = tmp_path / f"{key}.json"
    target.write_bytes(raw)
    target.with_suffix(".sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    assert verify_existing_checkpoint_file(req, target)["status"] == "MISSING_METADATA"
    result = run_acquisition(tmp_path, manifest_path=manifest, checkpoint_path=tmp_path / "checkpoint.json", verify_only=True)
    assert result["total_completed"] == 0
    transport = MockTransport({})
    run_acquisition(tmp_path, manifest_path=manifest, checkpoint_path=tmp_path / "checkpoint.json", token="test-token", transport=transport, max_requests=1, delay_seconds=0, sleep=lambda _: None)
    assert transport.calls == []
    target.with_suffix(".meta.json").write_text(json.dumps({
        "schema_version": "dhan-chart-raw-v1", "endpoint": req.endpoint,
        "method": "POST", "request": req.payload(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "received_at": "2024-02-01T00:00:00+00:00",
    }))
    assert verify_existing_checkpoint_file(req, target)["status"] == "CORRUPT_METADATA_MISMATCH"
    meta = json.loads(target.with_suffix(".meta.json").read_text())
    meta["http_status"] = 200
    meta["normalization"] = {"reordered": True, "sorted_original_indices": [], "collapsed_duplicate_indices": []}
    target.with_suffix(".meta.json").write_text(json.dumps(meta))
    assert verify_existing_checkpoint_file(req, target)["status"] == "CORRUPT_METADATA_MISMATCH"


def test_request_budget_counts_failed_http_attempts(tmp_path: Path) -> None:
    windows = [
        {"security_id": sid, "symbol": sym, "venue": "NSE", "segment": "NSE_EQ", "start": "2024-01-01", "end": "2024-01-30"}
        for sid, sym in (("2885", "RELIANCE"), ("1333", "HDFCBANK"))
    ]
    manifest = _make_manifest(tmp_path, windows)
    error = urllib.error.HTTPError("https://api.dhan.co", 500, "Server Error", {}, io.BytesIO(b"server error"))
    transport = MockTransport({"2885-2024-01-01 00:00:00": error})
    result = run_acquisition(tmp_path, manifest_path=manifest, token="test-token", transport=transport, max_requests=1, delay_seconds=0, sleep=lambda _: None)
    assert result["attempted"] == 1
    assert len(transport.calls) == 1
    assert result["status"] == "INCOMPLETE"


def test_invalid_200_response_is_quarantined_and_not_refetched_on_resume(tmp_path: Path) -> None:
    w = {"security_id": "2885", "symbol": "RELIANCE", "venue": "NSE", "segment": "NSE_EQ", "start": "2024-01-01", "end": "2024-01-30"}
    manifest = _make_manifest(tmp_path, [w])
    transport = MockTransport({"2885-2024-01-01 00:00:00": b'{"error":"invalid candles"}'})
    first = run_acquisition(tmp_path, manifest_path=manifest, token="test-token", transport=transport, delay_seconds=0, sleep=lambda _: None)
    assert first["attempted"] == 1
    assert json.loads((tmp_path / "acquisition-checkpoint.json").read_text())["2885-1-2024-01-01-2024-01-30"]["status"] == "INVALID_RESPONSE_QUARANTINED"
    assert list((tmp_path / ".quarantine").glob("*.json"))
    second = run_acquisition(tmp_path, manifest_path=manifest, token="test-token", transport=transport, delay_seconds=0, sleep=lambda _: None)
    assert second["attempted"] == 0
    assert len(transport.calls) == 1


def test_auction_bars_do_not_hide_missing_continuous_minutes() -> None:
    day = date(2024, 1, 2)
    req = HistoricalRequest(CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE"), day, date(2024, 1, 3), 1)
    times = [datetime(2024, 1, 2, 9, 15, tzinfo=IST) + timedelta(minutes=i) for i in range(360)]
    times += [datetime(2024, 1, 2, 15, 30, tzinfo=IST) + timedelta(minutes=i) for i in range(15)]
    raw = json.dumps({"timestamp": [int(t.timestamp()) for t in times], "open": [100] * 375, "high": [101] * 375, "low": [99] * 375, "close": [100] * 375, "volume": [1] * 375}).encode()
    val = validate_candle_data(raw, req)
    assert val["valid"]
    recon = reconcile_exchange_sessions("RELIANCE", day, req.end, val["bars"], val["session_bar_counts"], val["continuous_session_bar_counts"])
    assert recon["coverage_classification"] == "PARTIAL_COVERAGE_INCOMPLETE_MINUTES"
    assert recon["truncated_sessions"][0]["deficit"] == 15


def test_out_of_range_timestamp_is_invalid_not_exception() -> None:
    req = HistoricalRequest(CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE"), date(2024, 1, 1), date(2024, 1, 2), 1)
    raw = json.dumps({"timestamp": [10**18], "open": [100], "high": [101], "low": [99], "close": [100], "volume": [1]}).encode()
    assert validate_candle_data(raw, req)["valid"] is False


def test_validator_rejects_candles_the_shared_parser_cannot_use() -> None:
    req = HistoricalRequest(CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE"), date(2024, 1, 1), date(2024, 1, 2), 1)
    stamp = int(datetime(2024, 1, 1, 9, 15, tzinfo=IST).timestamp())
    for price, volume in ((-100, 1), (100, 1.5)):
        raw = json.dumps({"timestamp": [stamp], "open": [price], "high": [price + 1], "low": [price - 1], "close": [price], "volume": [volume]}).encode()
        result = validate_candle_data(raw, req)
        assert result["valid"] is False
        assert result["parser_error"]


def test_manifest_rejects_duplicate_and_misrepresented_requests(tmp_path: Path) -> None:
    w = {"security_id": "2885", "symbol": "RELIANCE", "venue": "NSE", "segment": "NSE_EQ", "start": "2024-01-01", "end": "2024-01-30"}
    duplicate = _make_manifest(tmp_path, [w, w])
    with pytest.raises(ValueError, match="manifest"):
        run_acquisition(tmp_path, manifest_path=duplicate, verify_only=True)
    wrong_payload = _make_manifest(tmp_path, [{**w, "payload": {"securityId": "1333"}}])
    with pytest.raises(ValueError, match="manifest"):
        run_acquisition(tmp_path, manifest_path=wrong_payload, verify_only=True)


def test_missing_later_windows_take_priority_over_early_zero_bars(tmp_path: Path) -> None:
    windows = [
        {"security_id": "766325", "symbol": "MONEYVIEW", "venue": "NSE", "segment": "NSE_EQ", "start": start, "end": end}
        for start, end in (("2024-01-01", "2024-01-30"), ("2026-10-01", "2026-10-30"))
    ]
    manifest = _make_manifest(tmp_path, windows)
    req = HistoricalRequest(CashInstrument("NSE", "NSE_EQ", "766325", "MONEYVIEW"), date(2024, 1, 1), date(2024, 1, 30), 1)
    _save_response(tmp_path / "766325-1-2024-01-01-2024-01-30.json", b'{"timestamp":[],"open":[],"high":[],"low":[],"close":[],"volume":[]}', req)
    row = generate_summary_report(tmp_path, manifest_path=manifest)["universe_summary"][0]
    assert row["point_in_time_status"] == "INCOMPLETE_ACQUISITION"


def test_summary_does_not_hide_ex_date_execution_block(tmp_path: Path) -> None:
    w = {"security_id": "2885", "symbol": "RELIANCE", "venue": "NSE", "segment": "NSE_EQ", "start": "2024-10-01", "end": "2024-11-01"}
    manifest = _make_manifest(tmp_path, [w])
    req = HistoricalRequest(CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE"), date(2024, 10, 1), date(2024, 11, 1), 1)
    _save_response(tmp_path / "2885-1-2024-10-01-2024-11-01.json", _make_valid_raw_response(date(2024, 10, 1), days=1), req)
    row = generate_summary_report(tmp_path, manifest_path=manifest)["universe_summary"][0]
    assert row["historical_execution_model_eligibility"] == "INELIGIBLE_EX_DATE_DISCONTINUITY"


def test_legacy_pilot_entry_uses_verified_resume_and_request_budget(tmp_path: Path) -> None:
    requests = [
        HistoricalRequest(CashInstrument("NSE", "NSE_EQ", sid, sym), date(2024, 1, 1), date(2024, 1, 30), 1)
        for sid, sym in (("2885", "RELIANCE"), ("1333", "HDFCBANK"))
    ]
    transport = MockTransport({"2885-2024-01-01 00:00:00": _make_valid_raw_response(date(2024, 1, 1))})
    checkpoint = tmp_path / "checkpoint.json"
    first = execute_batch(requests, tmp_path, "test-token", checkpoint, delay_seconds=0, max_requests=1, transport=transport, sleep=lambda _: None)
    assert first["attempted_this_run"] == 1
    assert first["status"] == "INCOMPLETE"
    assert len(transport.calls) == 1
    saved = tmp_path / "2885-1-2024-01-01-2024-01-30.json"
    saved.write_bytes(b"tampered")
    second = execute_batch(requests, tmp_path, "test-token", checkpoint, delay_seconds=0, max_requests=1, transport=transport, sleep=lambda _: None)
    assert second["results"][0]["status"] == "CORRUPT_HASH_MISMATCH"
    assert second["attempted_this_run"] == 1
    assert len(transport.calls) == 2
    assert saved.read_bytes() == b"tampered"
