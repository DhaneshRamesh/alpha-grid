"""Synthetic software fixtures; no model performance evidence."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pytest import MonkeyPatch, raises

from quant.acquisition.calendar import IST
from quant.acquisition.dhan import HistoricalHttpResponse, UrlLibTransport, run_backfill
from quant.apps import local_training_worker


def test_missing_credentials_prevent_new_history_request_without_fitting(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(
        local_training_worker,
        "study",
        lambda: (_ for _ in ()).throw(AssertionError("fit called")),
    )
    monkeypatch.setattr(local_training_worker, "run_backfill",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network called")))
    monkeypatch.setattr(local_training_worker, "valid_authorization", lambda *a: ({}, [], "v2"))
    monkeypatch.setattr(local_training_worker, "load_credentials",
                        lambda *a: (_ for _ in ()).throw(ValueError("authentication required")))
    with raises(ValueError, match="authentication required"):
        local_training_worker.run_job(tmp_path, {"id": "abc123", "ticker": "HDFCBANK"})


def test_verified_job_artifact_is_immutable_and_idempotent(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(
        local_training_worker,
        "study",
        lambda: calls.append(1) or {"status": "EXPLORATORY"},
    )
    job = {"id": "abc123", "ticker": "RELIANCE"}
    first = local_training_worker.run_job(tmp_path, job)
    second = local_training_worker.run_job(tmp_path, job)
    raw = (tmp_path / "training-artifacts" / "abc123.json").read_bytes()
    assert calls == [1]
    assert first == second
    assert first["sha256"] == hashlib.sha256(raw).hexdigest()
    assert json.loads(raw)["promotion_eligibility"] == "BLOCKED"


def test_single_ticker_fetch_preserves_original_response_and_replays_no_orders(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    rows = [{"symbol": "HDFCBANK" if i == 0 else f"S{i}", "security_id": str(1000 + i),
             "isin": f"INE{i:09d}"} for i in range(25)]
    (docs / "cross-stock-universe-2026-11-02.json").write_text(json.dumps({
        "schema_version": "alpha-grid-cross-stock-universe-v1", "selection_asof": "2026-10-30", "instruments": rows}))
    stamp = int(datetime(2026, 10, 26, 9, 15, tzinfo=IST).timestamp())
    raw = json.dumps({"timestamp": [stamp], "open": [100], "high": [101], "low": [99],
                      "close": [100], "volume": [1000]}).encode()
    requests = []

    class FixtureTransport(UrlLibTransport):
        def post_json_with_metadata(self, url, payload, token):
            requests.append((url, payload, token))
            return HistoricalHttpResponse(raw, 200, "Mon, 26 Oct 2026 10:00:00 GMT")

    original = run_backfill
    def offline_backfill(requests_arg, **kwargs):
        assert kwargs["max_retries"] == 0
        return original(requests_arg, transport=FixtureTransport(), **kwargs)

    monkeypatch.setattr(local_training_worker, "ROOT", tmp_path)
    monkeypatch.setattr(local_training_worker, "run_backfill", offline_backfill)
    monkeypatch.setattr(local_training_worker, "load_credentials", lambda *a: ("fixture.token", "123",
                          datetime.now(UTC) + timedelta(hours=1)))
    monkeypatch.setattr(local_training_worker, "valid_authorization", lambda *a: ({}, [], "v2"))
    result = local_training_worker.run_job(tmp_path, {"id": "abc123", "ticker": "HDFCBANK"})
    assert len(requests) == 1
    assert result["state"] == "COMPLETE" and result["fitted_model"] is False
    original_response = next(path for path in (tmp_path / "historical-25-oct26-30").glob("*.json")
                             if not path.name.endswith(".meta.json"))
    assert original_response.read_bytes() == raw
    assert original_response.with_suffix(".sha256").read_text().strip() == hashlib.sha256(raw).hexdigest()
    artifact = json.loads(Path(result["artifact"]).read_text())
    assert artifact["frozen_rule_simulation"]["broker_orders"] == 0
    assert artifact["frozen_rule_simulation"]["missing_symbols"] == sorted(row["symbol"] for row in rows[1:])
