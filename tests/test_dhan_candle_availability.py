"""Synthetic software fixtures only; never market evidence."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from quant.acquisition.dhan import HistoricalHttpResponse, UrlLibTransport
from quant.apps import dhan_candle_availability as probe


def _calendar(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "version": "synthetic-test-only",
                "scope": {"start": "2026-10-27", "end": "2026-10-28"},
                "special": [
                    {
                        "day": "2026-10-27",
                        "opens": "09:15:00",
                        "closes": "15:15:00",
                        "kind": "CAS_CONTINUOUS_TRADING",
                        "verified": True,
                    }
                ],
            }
        )
    )
    return path


def _registry(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "version": "synthetic-test-only",
                "records": [
                    {
                        "instrument": {
                            "venue": "NSE",
                            "segment": "NSE_EQ",
                            "security_id": "2885",
                            "symbol": "RELIANCE",
                        },
                        "name": "RELIANCE INDUSTRIES LTD",
                        "effective_from": "2026-10-27",
                        "effective_to": "2026-10-28",
                        "listing_status": "LISTED",
                        "source_manifest_version": "synthetic-test-only",
                    }
                ],
            }
        )
    )
    return path


def _response(*, version: int | None) -> bytes:
    stamps = [] if version is None else [int(probe._slot_utc("09:59:00").timestamp())]
    values = [] if version is None else [100 + version]
    return (
        json.dumps(
            {
                "timestamp": stamps,
                "open": values,
                "high": values,
                "low": values,
                "close": values,
                "volume": values,
            }
        ).encode()
        + b" \n"
    )


class FakeClock:
    def __init__(self) -> None:
        self.current = probe._slot_utc("09:59:30")
        self.elapsed_ns = 0

    def now(self) -> datetime:
        return self.current

    def monotonic_ns(self) -> int:
        return self.elapsed_ns

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)
        self.elapsed_ns += round(seconds * 1_000_000_000)


class FakeTransport:
    def __init__(self, clock: FakeClock, *, fail: bool = False) -> None:
        self.clock = clock
        self.calls = 0
        self.fail = fail

    def post_json_with_metadata(
        self, url: str, payload: dict[str, object], token: str
    ) -> HistoricalHttpResponse:
        assert url == probe.REQUEST.endpoint
        assert payload == probe.REQUEST.payload()
        assert token == "fixture-token-only"
        self.clock.advance(0.1)
        self.calls += 1
        if self.fail:
            raise urllib.error.HTTPError(url, 401, "fixture", {}, io.BytesIO(b"denied"))
        version = None if self.calls == 1 else 1 if self.calls == 2 else 2
        return HistoricalHttpResponse(_response(version=version), 200, None)


def test_plan_is_offline_and_cap_is_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["dhan_candle_availability"])
    probe.main()
    value = json.loads(capsys.readouterr().out)
    assert value["mode"] == "PLAN_NO_NETWORK"
    assert value["maximum_requests"] == 14
    assert value["request"]["securityId"] == "2885"
    assert value["request"]["fromDate"] == "2026-10-27 00:00:00"
    assert value["request"]["toDate"] == "2026-10-28 00:00:00"
    assert not list(tmp_path.iterdir())

    clock = FakeClock()
    client = FakeTransport(clock)
    with pytest.raises(PermissionError, match="14-request"):
        probe.capture(
            tmp_path / "blocked",
            _calendar(tmp_path / "calendar.json"),
            _registry(tmp_path / "registry.json"),
            "fixture-token-only",
            max_requests=13,
            transport=client,
            now=clock.now,
            monotonic_ns=clock.monotonic_ns,
            sleep=clock.advance,
        )
    assert client.calls == 0
    assert not (tmp_path / "blocked").exists()


def test_undated_instrument_identity_blocks_all_requests(tmp_path: Path) -> None:
    clock = FakeClock()
    client = FakeTransport(clock)
    registry = _registry(tmp_path / "registry.json")
    data = json.loads(registry.read_text())
    data["records"][0]["effective_from"] = "2026-10-26"
    data["records"][0]["effective_to"] = "2026-10-27"
    registry.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="historical security ID"):
        probe.capture(
            tmp_path / "blocked",
            _calendar(tmp_path / "calendar.json"),
            registry,
            "fixture-token-only",
            max_requests=14,
            transport=client,
            now=clock.now,
            monotonic_ns=clock.monotonic_ns,
            sleep=clock.advance,
        )
    assert client.calls == 0
    assert not (tmp_path / "blocked").exists()


def test_synthetic_capture_preserves_every_version_and_reports_observed_stability(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    client = FakeTransport(clock)
    output = tmp_path / "private"
    result = probe.capture(
        output,
        _calendar(tmp_path / "calendar.json"),
        _registry(tmp_path / "registry.json"),
        "fixture-token-only",
        max_requests=14,
        transport=client,
        now=clock.now,
        monotonic_ns=clock.monotonic_ns,
        sleep=clock.advance,
    )
    assert result["requests_sent"] == client.calls == 14
    assert result["slots_skipped"] == 0
    assert result["timing_eligibility"] == "UNVERIFIED"
    for index in range(14):
        slot = json.loads((output / f"slot-{index:02}.json").read_text())
        raw = (output / slot["response_file"]).read_bytes()
        receipt = json.loads(
            (output / slot["response_file"]).with_suffix(".meta.json").read_text()
        )
        expected = _response(version=None if index == 0 else 1 if index == 1 else 2)
        assert raw == expected
        assert slot["sha256"] == receipt["sha256"] == hashlib.sha256(raw).hexdigest()
        assert slot["elapsed_monotonic_ns"] == 100_000_000
        assert "fixture-token-only" not in json.dumps(slot) + json.dumps(receipt)
    observed = probe.report(output)["epoch_label_observations"]
    target = observed[str(int(probe._slot_utc("09:59:00").timestamp()))]
    assert target["observed_revision_count"] == 1
    assert target["revision_slots"] == ["slot-02"]
    assert target["last_absent_interval_utc"] is not None
    assert target["observed_unchanged_tail_from_slot"] == "slot-02"
    assert target["post_close_anchor_matches"] is True
    assert target["completion_time_verified"] is False


def test_http_failure_is_saved_without_retry(tmp_path: Path) -> None:
    clock = FakeClock()
    client = FakeTransport(clock, fail=True)
    output = tmp_path / "private"
    result = probe.capture(
        output,
        _calendar(tmp_path / "calendar.json"),
        _registry(tmp_path / "registry.json"),
        "fixture-token-only",
        max_requests=14,
        transport=client,
        now=clock.now,
        monotonic_ns=clock.monotonic_ns,
        sleep=clock.advance,
    )
    assert client.calls == result["requests_sent"] == 1
    assert result["stop_reason"] == "HTTP_ERROR_NO_RETRY"
    item = json.loads((output / "slot-00.json").read_text())
    assert (output / item["response_file"]).read_bytes() == b"denied"


def test_historical_transport_exposes_status_without_changing_raw_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response(io.BytesIO):
        status = 200
        headers = {"Date": "Tue, 27 Oct 2026 04:30:00 GMT"}

    def fake_urlopen(request: object, *, timeout: int) -> Response:
        assert timeout == 30
        return Response(b"raw-synthetic-only\n")

    monkeypatch.setattr("quant.acquisition.dhan.urllib.request.urlopen", fake_urlopen)
    client = UrlLibTransport()
    result = client.post_json_with_metadata("https://example.invalid", {}, "fixture")
    assert result.raw == b"raw-synthetic-only\n"
    assert result.http_status == 200
    assert result.server_date == "Tue, 27 Oct 2026 04:30:00 GMT"
