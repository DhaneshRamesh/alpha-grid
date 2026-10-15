from __future__ import annotations

import hashlib
import io
import json
import urllib.error
import zipfile
from datetime import UTC, date, datetime
from email.message import Message
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quant.acquisition.contracts import CashInstrument
from quant.acquisition.dhan import (
    deduplicate_windows,
    dry_run,
    iter_saved_backfill,
    parse_response,
    plan_requests,
    run_backfill,
)
from quant.acquisition.nse import ArchiveSource, import_archive

IST = ZoneInfo("Asia/Kolkata")
INSTRUMENT = CashInstrument("NSE", "NSE_EQ", "1333", "RELIANCE", "INE002A01018")


def response(*stamps: int) -> bytes:
    return json.dumps(
        {
            "timestamp": list(stamps),
            "open": [100.05] * len(stamps),
            "high": [101.10] * len(stamps),
            "low": [99.95] * len(stamps),
            "close": [100.50] * len(stamps),
            "volume": [123] * len(stamps),
        }
    ).encode()


def stamp(day: int, minute: int = 15) -> int:
    return int(datetime(2025, 1, day, 9, minute, tzinfo=IST).timestamp())


def test_90_day_windows_and_dry_run_cannot_contact_network(tmp_path: Path) -> None:
    requests = plan_requests((INSTRUMENT,), date(2025, 1, 1), date(2025, 7, 2), 1)
    assert len(requests) == 3
    assert all((req.end - req.start).days <= 90 for req in requests)
    assert requests[0].start == date(2025, 1, 1)
    assert requests[-1].end == date(2025, 7, 2)
    plan = dry_run(requests, tmp_path)
    assert plan["mode"] == "DRY_RUN_NO_NETWORK"
    assert not tmp_path.joinpath("raw").exists()
    with pytest.raises(PermissionError):
        run_backfill(requests, output_dir=tmp_path, token="fake")


def test_missing_dhan_entitlement_credentials_never_calls_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DHAN_ACCESS_TOKEN", raising=False)
    request = plan_requests((INSTRUMENT,), date(2025, 1, 2), date(2025, 1, 3), 1)
    transport = FakeTransport([])
    with pytest.raises(ValueError, match="access token required"):
        run_backfill(request, output_dir=tmp_path, enabled=True, transport=transport)
    assert transport.calls == 0
    assert list(tmp_path.iterdir()) == []


def test_dhan_parse_empty_overlap_and_conflict() -> None:
    req = plan_requests((INSTRUMENT,), date(2025, 1, 1), date(2025, 1, 5), 1)[0]
    first = parse_response(response(stamp(2), stamp(3)), req)
    second = parse_response(response(stamp(3), stamp(4)), req)
    assert len(deduplicate_windows([first, second])) == 3
    assert first[0].open_nanos == 100_050_000_000
    assert first[0].timestamp_semantics == "UNVERIFIED"
    assert parse_response(response(), req) == []
    altered = json.loads(response(stamp(3)))
    altered["close"] = [101.00]
    with pytest.raises(ValueError, match="conflicting"):
        deduplicate_windows([first, parse_response(json.dumps(altered).encode(), req)])
    with pytest.raises(ValueError, match="unequal"):
        parse_response(
            b'{"timestamp":[1],"open":[],"high":[],"low":[],"close":[],"volume":[]}',
            req,
        )


class FakeTransport:
    def __init__(self, bodies: list[bytes | Exception]) -> None:
        self.bodies = bodies
        self.calls = 0

    def post_json(self, url: str, payload: dict[str, object], token: str) -> bytes:
        self.calls += 1
        item = self.bodies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_retry_checkpoint_and_original_response_immutable(tmp_path: Path) -> None:
    req = plan_requests((INSTRUMENT,), date(2025, 1, 1), date(2025, 1, 5), 1)
    raw = response(stamp(2))
    error = urllib.error.HTTPError("chart", 429, "rate limit", Message(), io.BytesIO())
    transport = FakeTransport([error, raw])
    sleeps: list[float] = []
    paths = run_backfill(
        req,
        output_dir=tmp_path,
        enabled=True,
        transport=transport,
        token="never-log-this",
        sleep=sleeps.append,
    )
    assert transport.calls == 2 and sleeps == [1, 1.0]
    assert paths[0].read_bytes() == raw
    assert (
        paths[0].with_suffix(".sha256").read_text().strip()
        == hashlib.sha256(raw).hexdigest()
    )
    paths[0].with_suffix(".sha256").unlink()
    assert (
        run_backfill(
            req,
            output_dir=tmp_path,
            enabled=True,
            transport=transport,
            token="fake",
            sleep=lambda _: None,
        )
        == paths
    )
    assert transport.calls == 2
    assert paths[0].with_suffix(".sha256").exists()
    paths[0].write_bytes(raw + b"corrupt")
    with pytest.raises(ValueError):
        run_backfill(
            req,
            output_dir=tmp_path,
            enabled=True,
            transport=transport,
            token="fake",
            sleep=lambda _: None,
        )


def test_nse_udiff_local_zip_preserves_daily_provenance(tmp_path: Path) -> None:
    csv_bytes = (
        b"TradDt,TckrSymb,SctySrs,ISIN,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol,TtlTrfVal,DlvryQty\n"
        b"2025-01-02,RELIANCE,EQ,INE002A01018,100.05,101.10,99.95,100.50,123,12345.67,80\n"
        b"2025-01-02,OTHER,BE,X,1,1,1,1,1,1,1\n"
    )
    path = tmp_path / "BhavCopy_NSE_CM_20250102_F_0000.csv.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("bhav.csv", csv_bytes)
    source = ArchiveSource(
        path, "UDIFF_CM", "https://www.nseindia.com/all-reports", datetime.now(UTC)
    )
    bars = list(import_archive(source, lambda symbol, isin, day: INSTRUMENT))
    assert len(bars) == 1
    assert bars[0].trading_date == date(2025, 1, 2)
    assert bars[0].source_timestamp_ns is None
    assert bars[0].close_nanos == 100_500_000_000
    assert bars[0].turnover_nanos == 12_345_670_000_000
    assert bars[0].source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="mapping mismatch"):
        list(
            import_archive(
                source,
                lambda symbol, isin, day: CashInstrument("NSE", "NSE_EQ", "1", "WRONG"),
            )
        )


def test_nse_duplicate_required_header_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.csv"
    path.write_text(
        "TradDt,TckrSymb,SctySrs,OpnPric,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol\n"
        "2025-01-02,RELIANCE,EQ,100,100,101,99,100,5\n"
    )
    source = ArchiveSource(
        path, "UDIFF_CM", "https://www.nseindia.com/all-reports", datetime.now(UTC)
    )
    with pytest.raises(ValueError, match="schema"):
        list(import_archive(source, lambda symbol, isin, day: INSTRUMENT))


def test_saved_windows_stream_with_checksum_and_boundary_dedup(tmp_path: Path) -> None:
    first_req = plan_requests((INSTRUMENT,), date(2025, 1, 1), date(2025, 1, 4), 1)[0]
    second_req = plan_requests((INSTRUMENT,), date(2025, 1, 3), date(2025, 1, 5), 1)[0]
    paths = [tmp_path / "first.json", tmp_path / "second.json"]
    for path, raw in zip(
        paths, (response(stamp(2), stamp(3)), response(stamp(3), stamp(4))), strict=True
    ):
        path.write_bytes(raw)
        path.with_suffix(".sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    assert len(list(iter_saved_backfill((first_req, second_req), paths))) == 3
    paths[1].with_suffix(".sha256").write_text("bad\n")
    with pytest.raises(ValueError, match="checksum"):
        list(iter_saved_backfill((first_req, second_req), paths))
