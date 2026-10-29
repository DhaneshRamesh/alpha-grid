from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import urllib.error
import zipfile
from datetime import UTC, date, datetime
from email.message import Message
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quant.acquisition.contracts import CashInstrument
from quant.acquisition.dhan import (
    HistoricalHttpResponse,
    HistoricalRequest,
    UrlLibTransport,
    deduplicate_windows,
    diagnose_response,
    dry_run,
    iter_saved_backfill,
    parse_response,
    plan_requests,
    response_path,
    run_backfill,
    verify_saved_response,
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


def test_exact_intraday_window_and_http_metadata_are_preserved(tmp_path: Path) -> None:
    request = HistoricalRequest(
        INSTRUMENT,
        date(2025, 1, 2),
        date(2025, 1, 3),
        1,
        datetime(2025, 1, 2, 9, 15, tzinfo=IST),
        datetime(2025, 1, 2, 15, 30, tzinfo=IST),
    )
    assert request.payload()["fromDate"] == "2025-01-02 09:15:00"
    assert request.payload()["toDate"] == "2025-01-02 15:30:00"
    assert response_path(request, tmp_path) != response_path(
        plan_requests((INSTRUMENT,), date(2025, 1, 2), date(2025, 1, 3), 1)[0],
        tmp_path,
    )

    class MetadataTransport(UrlLibTransport):
        def post_json_with_metadata(
            self, url: str, payload: dict[str, object], token: str
        ) -> HistoricalHttpResponse:
            assert payload == request.payload()
            return HistoricalHttpResponse(
                response(stamp(2)), 200, "Thu, 02 Jan 2025 04:00:00 GMT"
            )

    path = run_backfill(
        (request,),
        output_dir=tmp_path / "raw",
        enabled=True,
        token="fixture-only",
        transport=MetadataTransport(),
        max_retries=0,
        sleep=lambda _: None,
    )[0]
    metadata = json.loads(path.with_suffix(".meta.json").read_text())
    assert metadata["http_status"] == 200
    assert metadata["server_date"] == "Thu, 02 Jan 2025 04:00:00 GMT"
    original = next((path.parent / ".quarantine").glob("*.response"))
    assert original.read_bytes() == path.read_bytes()
    receipt = json.loads(original.with_suffix(".meta.json").read_text())
    assert receipt["http_status"] == 200


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


def test_private_storage_preflight_never_calls_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = plan_requests((INSTRUMENT,), date(2025, 1, 2), date(2025, 1, 3), 1)
    transport = FakeTransport([])

    def reject_private_storage(path: Path) -> None:
        raise PermissionError("private storage unavailable")

    monkeypatch.setattr(
        "quant.acquisition.dhan._private_directory", reject_private_storage
    )
    with pytest.raises(PermissionError, match="private storage unavailable"):
        run_backfill(
            request,
            output_dir=tmp_path,
            enabled=True,
            token="fixture-only",
            transport=transport,
        )
    assert transport.calls == 0


def test_synthetic_response_round_trip_preserves_private_evidence(
    tmp_path: Path,
) -> None:
    request = plan_requests((INSTRUMENT,), date(2025, 1, 2), date(2025, 1, 3), 1)[0]
    raw = response(stamp(2)) + b" \n"
    checkpoint = run_backfill(
        (request,),
        output_dir=tmp_path / "raw",
        enabled=True,
        token="fixture-only",
        transport=FakeTransport([raw]),
        sleep=lambda _: None,
        max_retries=0,
    )[0]
    digest = hashlib.sha256(raw).hexdigest()
    audit = verify_saved_response(request, checkpoint)
    quarantine = checkpoint.parent / ".quarantine"
    original = next(quarantine.glob("*.response"))
    receipt = json.loads(original.with_suffix(".meta.json").read_text())
    metadata = json.loads(checkpoint.with_suffix(".meta.json").read_text())

    assert checkpoint.read_bytes() == original.read_bytes() == raw
    assert checkpoint.with_suffix(".sha256").read_text().strip() == digest
    assert audit.sha256 == receipt["sha256"] == metadata["sha256"] == digest
    assert receipt["request"] == metadata["request"] == request.payload()
    assert receipt["endpoint"] == metadata["endpoint"] == request.endpoint
    assert metadata["method"] == "POST"
    assert receipt["received_at"] == metadata["received_at"]
    assert audit.retrieved_at is not None and audit.retrieved_at.tzinfo is not None
    assert "fixture-only" not in json.dumps(receipt) + json.dumps(metadata)
    if os.name != "nt":
        assert stat.S_IMODE(checkpoint.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(quarantine.stat().st_mode) == 0o700
        for path in (
            original,
            original.with_suffix(".meta.json"),
            checkpoint,
            checkpoint.with_suffix(".sha256"),
            checkpoint.with_suffix(".meta.json"),
        ):
            assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "nt", reason="native NTFS ACL check")
def test_windows_accepts_restricted_inherited_dacl(tmp_path: Path) -> None:
    from quant.acquisition.dhan import _private_directory, _write_private_bytes

    parent = tmp_path / "secure"
    _private_directory(parent)
    child = parent / "inherited"
    child.mkdir(mode=0o700)
    subprocess.run(
        ["icacls", str(child), "/inheritance:e"],
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "if (-not ((Get-Acl -LiteralPath $env:AG_TEST_PATH).Access | "
            "Where-Object IsInherited)) { exit 1 }",
        ],
        env={**os.environ, "AG_TEST_PATH": str(child)},
        capture_output=True,
        text=True,
        check=True,
    )
    _private_directory(child)
    target = child / "private.response"
    _write_private_bytes(target, b"synthetic-only")
    assert target.read_bytes() == b"synthetic-only"


@pytest.mark.skipif(os.name != "nt", reason="native NTFS ACL check")
def test_windows_rejects_broad_dacl_before_transport(tmp_path: Path) -> None:
    from quant.acquisition.dhan import _private_directory

    output = tmp_path / "broad"
    _private_directory(output)
    granted = subprocess.run(
        ["icacls", str(output), "/grant", "*S-1-1-0:(R)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Successfully processed 1 files" in granted.stdout
    transport = FakeTransport([])
    request = plan_requests((INSTRUMENT,), date(2025, 1, 2), date(2025, 1, 3), 1)
    with pytest.raises(PermissionError, match="ACL"):
        run_backfill(
            request,
            output_dir=output,
            enabled=True,
            token="fixture-only",
            transport=transport,
        )
    assert transport.calls == 0

    private_output = tmp_path / "private"
    checkpoint = run_backfill(
        request,
        output_dir=private_output,
        enabled=True,
        token="fixture-only",
        transport=FakeTransport([response(stamp(2))]),
        sleep=lambda _: None,
    )[0]
    subprocess.run(
        ["icacls", str(checkpoint), "/grant", "*S-1-1-0:(R)"],
        capture_output=True,
        text=True,
        check=True,
    )
    no_transport = FakeTransport([])
    with pytest.raises(PermissionError, match="ACL"):
        run_backfill(
            request,
            output_dir=private_output,
            enabled=True,
            token="fixture-only",
            transport=no_transport,
        )
    assert no_transport.calls == 0


@pytest.mark.skipif(os.name != "nt", reason="native NTFS recovery check")
def test_windows_interrupted_publication_recovers_in_new_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quant.acquisition import dhan

    request = plan_requests((INSTRUMENT,), date(2025, 1, 2), date(2025, 1, 3), 1)[0]
    raw = response(stamp(2)) + b" \n"
    output = tmp_path / "raw"
    transport = FakeTransport([raw])
    with monkeypatch.context() as fault:

        def interrupt_link(*_: object) -> None:
            raise OSError("interrupted")

        fault.setattr(dhan.os, "link", interrupt_link)
        with pytest.raises(OSError, match="interrupted"):
            run_backfill(
                (request,),
                output_dir=output,
                enabled=True,
                token="fixture-secret",
                transport=transport,
                sleep=lambda _: None,
                max_retries=0,
            )
    assert transport.calls == 1
    evidence = next((output / ".quarantine").glob("*.response"))
    assert evidence.read_bytes() == raw
    assert not dhan.response_path(request, output).exists()
    script = (
        "import json,sys; from pathlib import Path; from datetime import date; "
        "from quant.acquisition.contracts import CashInstrument; "
        "from quant.acquisition.dhan import "
        "plan_requests,recover_quarantined_response; "
        "i=CashInstrument('NSE','NSE_EQ','1333','RELIANCE','INE002A01018'); "
        "r=plan_requests((i,),date(2025,1,2),date(2025,1,3),1)[0]; "
        "a=recover_quarantined_response(r,Path(sys.argv[1]),Path(sys.argv[2])); "
        "print(json.dumps({'sha256':a.sha256,'received_at':a.retrieved_at.isoformat()}))"
    )
    recovered = subprocess.run(
        [sys.executable, "-c", script, str(evidence), str(output)],
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(recovered.stdout)
    checkpoint = dhan.response_path(request, output)
    digest = hashlib.sha256(raw).hexdigest()
    receipt = json.loads(evidence.with_suffix(".meta.json").read_text())
    metadata = json.loads(checkpoint.with_suffix(".meta.json").read_text())
    assert checkpoint.read_bytes() == raw
    assert result["sha256"] == digest
    assert checkpoint.with_suffix(".sha256").read_text().strip() == digest
    assert result["received_at"] == receipt["received_at"] == metadata["received_at"]
    assert metadata["request"] == receipt["request"] == request.payload()
    assert metadata["endpoint"] == receipt["endpoint"] == request.endpoint
    assert metadata["method"] == "POST"
    assert metadata["recovered_from_quarantine"] == evidence.name
    for path in (
        output,
        evidence.parent,
        evidence,
        evidence.with_suffix(".meta.json"),
        checkpoint,
        checkpoint.with_suffix(".sha256"),
        checkpoint.with_suffix(".meta.json"),
    ):
        dhan._verify_windows_private_acl(path)
    assert "fixture-secret" not in "".join(
        path.read_text() for path in output.rglob("*") if path.is_file()
    )


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


def test_integral_decimal_chart_timestamps_and_volume() -> None:
    req = plan_requests((INSTRUMENT,), date(2025, 1, 1), date(2025, 1, 5), 1)[0]
    raw = (
        response(stamp(2), stamp(3))
        .replace(str(stamp(2)).encode(), f"{stamp(2)}.0".encode())
        .replace(str(stamp(3)).encode(), f"{stamp(3)}.0".encode())
    )
    raw = raw.replace(b'"volume": [123, 123]', b'"volume": [123.0, 123.0]')
    report = diagnose_response(raw, req)
    assert report["timestamp_encodings"] == ["Decimal"]
    assert report["timestamp_units"] == ["seconds"]
    bars = parse_response(raw, req)
    assert len(bars) == 2
    assert bars[0].source_timestamp_ns == stamp(2) * 1_000_000_000
    assert [bar.volume for bar in bars] == [123, 123]
    fractional = raw.replace(f"{stamp(2)}.0".encode(), f"{stamp(2)}.5".encode())
    assert diagnose_response(fractional, req)["timestamp_units"] == [
        "invalid",
        "seconds",
    ]
    with pytest.raises(ValueError, match="timestamp"):
        parse_response(fractional, req)
    with pytest.raises(ValueError, match="volume"):
        parse_response(raw.replace(b"123.0", b"123.5"), req)


def test_out_of_order_rows_sort_together_and_duplicate_provenance(
    tmp_path: Path,
) -> None:
    req = plan_requests((INSTRUMENT,), date(2025, 1, 1), date(2025, 1, 5), 1)[0]
    data = json.loads(response(stamp(3), stamp(2), stamp(2)))
    data["open"] = [100.3, 100.2, 100.2]
    data["volume"] = [30, 20, 20]
    raw = json.dumps(data).encode()
    report = diagnose_response(raw, req)
    assert report["out_of_order_pairs"] == [[0, 1]]
    assert report["duplicate_timestamp_groups"] == [
        {"timestamp": stamp(2), "indices": [1, 2]}
    ]
    assert report["sorted_original_indices"] == [1, 2, 0]
    bars = parse_response(raw, req)
    assert [(bar.open_nanos, bar.volume) for bar in bars] == [
        (100_200_000_000, 20),
        (100_300_000_000, 30),
    ]
    transport = FakeTransport([raw])
    paths = run_backfill(
        (req,),
        output_dir=tmp_path / "raw",
        enabled=True,
        transport=transport,
        token="secret-marker",
        sleep=lambda _: None,
    )
    assert paths[0].read_bytes() == raw
    metadata = json.loads(paths[0].with_suffix(".meta.json").read_text())
    assert metadata["normalization"]["collapsed_duplicate_indices"] == [2]
    assert metadata["normalization"]["sorted_original_indices"] == [1, 2, 0]
    assert "secret-marker" not in json.dumps(metadata)


@pytest.mark.parametrize("failure", ["conflict", "length", "units", "boundary"])
def test_invalid_response_is_quarantined_before_validation(
    tmp_path: Path, failure: str
) -> None:
    req = plan_requests((INSTRUMENT,), date(2025, 1, 1), date(2025, 1, 5), 1)[0]
    data = json.loads(response(stamp(2), stamp(2)))
    if failure == "conflict":
        data["close"][1] = 999.0
    elif failure == "length":
        data["volume"].pop()
    elif failure == "units":
        data["timestamp"] = [value * 1000 for value in data["timestamp"]]
    else:
        data["timestamp"] = [stamp(2), stamp(5)]
    raw = (json.dumps(data) + " \n").encode()
    transport = FakeTransport([raw])
    raw_dir = tmp_path / "raw"
    with pytest.raises(ValueError):
        run_backfill(
            (req,),
            output_dir=raw_dir,
            enabled=True,
            transport=transport,
            token="secret-marker",
            sleep=lambda _: None,
        )
    assert transport.calls == 1
    assert not list(raw_dir.glob("*.json"))
    quarantine = raw_dir / ".quarantine"
    saved = list(quarantine.glob("*.response"))
    assert len(saved) == 1 and saved[0].read_bytes() == raw
    if os.name != "nt":
        assert stat.S_IMODE(quarantine.stat().st_mode) == 0o700
        assert stat.S_IMODE(saved[0].stat().st_mode) == 0o600
    metadata = json.loads(saved[0].with_suffix(".meta.json").read_text())
    diagnostics = json.loads(saved[0].with_suffix(".diagnostics.json").read_text())
    assert metadata["sha256"] == hashlib.sha256(raw).hexdigest()
    assert diagnostics["sha256"] == metadata["sha256"]
    assert "secret-marker" not in json.dumps(metadata) + json.dumps(diagnostics)
    if failure == "conflict":
        assert diagnostics["conflicting_duplicate_timestamps"] == [stamp(2)]
    elif failure == "length":
        assert diagnostics["array_lengths"]["volume"] == 1
    elif failure == "units":
        assert diagnostics["timestamp_units"] == ["milliseconds"]
    else:
        assert diagnostics["outside_request"][0]["index"] == 1


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


def test_http_error_body_is_quarantined_without_retry(tmp_path: Path) -> None:
    req = plan_requests((INSTRUMENT,), date(2025, 1, 1), date(2025, 1, 5), 1)[0]
    raw = b'{"error":"invalid token"}\r\n'
    error = urllib.error.HTTPError(
        "chart", 401, "unauthorized", Message(), io.BytesIO(raw)
    )
    transport = FakeTransport([error])
    with pytest.raises(RuntimeError, match="Dhan chart HTTP 401"):
        run_backfill(
            (req,),
            output_dir=tmp_path / "raw",
            enabled=True,
            transport=transport,
            token="secret-marker",
            max_retries=0,
            sleep=lambda _: None,
        )
    assert transport.calls == 1
    quarantine = tmp_path / "raw" / ".quarantine"
    saved = list(quarantine.glob("*.response"))
    assert len(saved) == 1 and saved[0].read_bytes() == raw
    metadata = json.loads(saved[0].with_suffix(".meta.json").read_text())
    assert metadata["sha256"] == hashlib.sha256(raw).hexdigest()
    assert metadata["request"] == req.payload()
    assert metadata["http_status"] == 401
    assert metadata["received_at"].endswith("+00:00")
    assert "secret-marker" not in json.dumps(metadata)


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
