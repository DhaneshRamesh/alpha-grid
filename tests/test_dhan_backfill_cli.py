from __future__ import annotations

import hashlib
import json
import urllib.error
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quant.acquisition.contracts import CashInstrument
from quant.acquisition.dhan import HistoricalRequest, response_path, run_backfill
from quant.acquisition.dhan_instruments import MASTER_URL
from quant.apps.acquisition import run


def _master(tmp_path: Path) -> tuple[Path, Path]:
    master = tmp_path / "master.csv"
    master.write_text(
        "SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,"
        "SEM_INSTRUMENT_NAME,SEM_TRADING_SYMBOL,SEM_SERIES,SM_SYMBOL_NAME\n"
        "NSE,E,2885,EQUITY,RELIANCE,EQ,RELIANCE INDUSTRIES LTD\n"
    )
    manifest = tmp_path / "master.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "dhan-instrument-master-v1",
                "source_url": MASTER_URL,
                "retrieved_at": datetime.now(UTC).isoformat(),
                "sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
            }
        )
    )
    return master, manifest


def _raw(day: date) -> bytes:
    stamp = int(
        datetime.combine(day, time(9, 15), ZoneInfo("Asia/Kolkata")).timestamp()
    )
    return json.dumps(
        {
            "timestamp": [stamp],
            "open": [100],
            "high": [101],
            "low": [99],
            "close": [100],
            "volume": [1000],
        }
    ).encode()


class _Transport:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.calls = 0
        self.fail_on_call = fail_on_call

    def post_json(self, url: str, payload: dict[str, object], token: str) -> bytes:
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise urllib.error.URLError("synthetic interruption")
        return _raw(date.fromisoformat(str(payload["fromDate"]).split()[0]))


def _args(tmp_path: Path, master: Path, manifest: Path) -> list[str]:
    return [
        "--security-id",
        "2885",
        "--symbol",
        "RELIANCE",
        "--start",
        "2025-01-01",
        "--end",
        "2025-01-05",
        "--interval",
        "1",
        "--output-dir",
        str(tmp_path / "raw"),
        "--master",
        str(master),
        "--master-manifest",
        str(manifest),
        "--window",
        "2025-01-01:2025-01-02",
        "--window",
        "2025-01-02:2025-01-03",
        "--window",
        "2025-01-03:2025-01-04",
        "--window",
        "2025-01-04:2025-01-05",
    ]


def test_six_month_plan_reuses_verified_final_window(tmp_path: Path) -> None:
    master, manifest = _master(tmp_path)
    last = HistoricalRequest(
        CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE"),
        date(2026, 10, 12),
        date(2026, 10, 17),
        1,
    )
    transport = _Transport()
    run_backfill(
        (last,),
        output_dir=tmp_path / "raw",
        enabled=True,
        transport=transport,
        token="fixture",
        sleep=lambda _: None,
    )
    args = [
        "--security-id",
        "2885",
        "--symbol",
        "RELIANCE",
        "--start",
        "2026-04-01",
        "--end",
        "2026-10-17",
        "--interval",
        "1",
        "--output-dir",
        str(tmp_path / "raw"),
        "--master",
        str(master),
        "--master-manifest",
        str(manifest),
        "--window",
        "2026-04-01:2026-06-30",
        "--window",
        "2026-06-30:2026-09-28",
        "--window",
        "2026-09-28:2026-10-12",
        "--window",
        "2026-10-12:2026-10-17",
    ]
    plan = run(["dry-run", *args])
    detail = plan["backfill_plan"]
    assert [item["days"] for item in detail["windows"]] == [90, 90, 14, 5]
    assert detail["planned_new_request_count"] == 3
    assert detail["verified_cached_count"] == 1
    assert (
        detail["windows"][-1]["sha256"] == hashlib.sha256(_raw(last.start)).hexdigest()
    )
    assert detail["potential_calendar_overlap_days"] == 0
    assert detail["illustrative_new_raw_bytes_at_375_slots_per_calendar_day"] > 0
    assert transport.calls == 1
    with pytest.raises(ValueError, match="contiguously"):
        run(["dry-run", *args[:-6], "--window", "2026-09-27:2026-10-17"])
    prefix = args[: args.index("--window")]
    prefix[prefix.index("--end") + 1] = "2026-07-01"
    with pytest.raises(ValueError, match="at most 90 days"):
        run(["dry-run", *prefix, "--window", "2026-04-01:2026-07-01"])


def test_backfill_opt_in_cap_and_resume_without_silent_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    master, manifest = _master(tmp_path)
    args = _args(tmp_path, master, manifest)
    monkeypatch.delenv("DHAN_ACCESS_TOKEN", raising=False)
    with pytest.raises(PermissionError, match="separate authorization"):
        run(["backfill", *args, "--max-new-requests", "4"])
    flags = [
        "--subscription-confirmed",
        "--backfill-authorized",
        "--execute-read-only",
        "--max-new-requests",
        "4",
    ]
    with pytest.raises(ValueError, match="per-run cap"):
        run(["backfill", *args, *flags[:-1], "3"])
    with pytest.raises(ValueError, match="DHAN_ACCESS_TOKEN"):
        run(["backfill", *args, *flags])

    from quant.acquisition import dhan

    first_transport = _Transport(fail_on_call=2)

    def interrupted(
        requests: tuple[HistoricalRequest, ...], **kwargs: object
    ) -> list[Path]:
        return dhan.run_backfill(
            requests, transport=first_transport, sleep=lambda _: None, **kwargs
        )

    monkeypatch.setenv("DHAN_ACCESS_TOKEN", "fixture-secret")
    monkeypatch.setattr("quant.apps.acquisition.run_backfill", interrupted)
    with pytest.raises(RuntimeError, match="transport failed"):
        run(["backfill", *args, *flags])
    assert first_transport.calls == 2
    checkpoint = response_path(
        HistoricalRequest(
            CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE"),
            date(2025, 1, 1),
            date(2025, 1, 2),
            1,
        ),
        tmp_path / "raw",
    )
    assert checkpoint.exists()
    second_transport = _Transport()

    def resumed(
        requests: tuple[HistoricalRequest, ...], **kwargs: object
    ) -> list[Path]:
        return dhan.run_backfill(
            requests, transport=second_transport, sleep=lambda _: None, **kwargs
        )

    monkeypatch.setattr("quant.apps.acquisition.run_backfill", resumed)
    result = run(["backfill", *args, *flags])
    assert result["planned_new_request_count"] == 3
    assert result["rows_after_boundary_reconciliation"] == 4
    assert result["research_eligibility"] == "BLOCKED_PENDING_SOURCE_QUALITY_REVIEW"
    assert second_transport.calls == 3
    assert "fixture-secret" not in json.dumps(result)
    monkeypatch.delenv("DHAN_ACCESS_TOKEN")
    assert run(["backfill", *args, *flags])["planned_new_request_count"] == 0
    checkpoint.with_suffix(".meta.json").unlink()
    with pytest.raises(ValueError, match="retrieval provenance"):
        run(["dry-run", *args])
