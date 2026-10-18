"""Read-only Dhan historical charts, offline unless explicitly enabled."""

from __future__ import annotations

import json
import os
import stat
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

from quant.acquisition.contracts import PRICE_SCALE, CashInstrument, HistoricalBar

_IST = ZoneInfo("Asia/Kolkata")
SUPPORTED_INTERVALS = (1, 5, 15, 25, 60)


@dataclass(frozen=True, slots=True)
class HistoricalRequest:
    instrument: CashInstrument
    start: date  # inclusive
    end: date  # exclusive
    interval_minutes: int | None

    def __post_init__(self) -> None:
        if not self.instrument.security_id.isdecimal():
            raise ValueError("Dhan security ID must be numeric")

    @property
    def endpoint(self) -> str:
        return "https://api.dhan.co/v2/charts/" + (
            "historical" if self.interval_minutes is None else "intraday"
        )

    def payload(self) -> dict[str, object]:
        common: dict[str, object] = {
            "securityId": self.instrument.security_id,
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "oi": False,
        }
        if self.interval_minutes is None:
            common.update(
                expiryCode=0,
                fromDate=self.start.isoformat(),
                toDate=self.end.isoformat(),
            )
        else:
            common.update(
                interval=str(self.interval_minutes),
                fromDate=f"{self.start.isoformat()} 00:00:00",
                toDate=f"{self.end.isoformat()} 00:00:00",
            )
        return common


def plan_requests(
    instruments: tuple[CashInstrument, ...],
    start: date,
    end: date,
    interval_minutes: int | None,
) -> tuple[HistoricalRequest, ...]:
    if start >= end:
        raise ValueError("start must precede exclusive end")
    if interval_minutes is not None and interval_minutes not in SUPPORTED_INTERVALS:
        raise ValueError("unsupported Dhan intraday interval")
    result = []
    for instrument in instruments:
        cursor = start
        while cursor < end:
            stop = min(cursor + timedelta(days=90), end)
            result.append(HistoricalRequest(instrument, cursor, stop, interval_minutes))
            cursor = stop
    return tuple(result)


def dry_run(
    requests: tuple[HistoricalRequest, ...], output_dir: Path
) -> dict[str, object]:
    """Describe work without looking up a token or constructing a client."""
    estimated_bars = sum(
        (req.end - req.start).days
        * (1 if req.interval_minutes is None else 375 // req.interval_minutes)
        for req in requests
    )
    return {
        "mode": "DRY_RUN_NO_NETWORK",
        "request_count": len(requests),
        "estimated_upper_bound_bars": estimated_bars,
        "illustrative_raw_bytes_at_128_per_bar": estimated_bars * 128,
        "estimate_note": "calendar-day upper bound; not a measured download size",
        "output_dir": str(output_dir),
        "subscription": (
            "Dhan Data API entitlement and valid local access token required"
        ),
        "requests": [
            {"endpoint": req.endpoint, "payload": req.payload()} for req in requests
        ],
    }


def response_path(request: HistoricalRequest, output_dir: Path) -> Path:
    label = (
        f"{request.instrument.security_id}-{request.interval_minutes or 'day'}-"
        f"{request.start}-{request.end}"
    )
    return output_dir / f"{label}.json"


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    mode = path.lstat().st_mode
    if not stat.S_ISDIR(mode) or mode & 0o077:
        raise PermissionError(f"quarantine directory is not private: {path}")


def _write_private_bytes(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _write_private_json(path: Path, value: dict[str, object]) -> None:
    encoded = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.partial")
    try:
        _write_private_bytes(temporary, encoded)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _quarantine_response(
    raw: bytes, request: HistoricalRequest, directory: Path, received_at: str
) -> Path:
    _private_directory(directory)
    path = directory / f"{uuid.uuid4().hex}.response"
    digest = sha256(raw).hexdigest()
    _write_private_bytes(path, raw)
    folder = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(folder)
    finally:
        os.close(folder)
    _write_private_json(
        path.with_suffix(".meta.json"),
        {
            "schema_version": "dhan-chart-quarantine-v1",
            "sha256": digest,
            "received_at": received_at,
            "endpoint": request.endpoint,
            "request": request.payload(),
        },
    )
    return path


@dataclass(frozen=True, slots=True)
class SavedResponseAudit:
    path: Path
    sha256: str
    retrieved_at: datetime | None
    bars: tuple[HistoricalBar, ...]


def verify_saved_response(request: HistoricalRequest, path: Path) -> SavedResponseAudit:
    raw = path.read_bytes()
    digest = sha256(raw).hexdigest()
    if path.with_suffix(".sha256").read_text().strip() != digest:
        raise ValueError("saved Dhan response checksum mismatch")
    metadata_path = path.with_suffix(".meta.json")
    retrieved_at = None
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text())
        if (
            metadata.get("schema_version") != "dhan-chart-raw-v1"
            or metadata.get("endpoint") != request.endpoint
            or metadata.get("method") != "POST"
            or metadata.get("request") != request.payload()
            or metadata.get("sha256") != digest
        ):
            raise ValueError("saved Dhan response provenance mismatch")
        if "normalization" in metadata:
            diagnostics = diagnose_response(raw, request)
            expected = {
                "reordered": diagnostics["reordered"],
                "sorted_original_indices": diagnostics["sorted_original_indices"],
                "collapsed_duplicate_indices": diagnostics[
                    "identical_duplicate_indices"
                ],
            }
            if metadata["normalization"] != expected:
                raise ValueError("saved Dhan normalization provenance mismatch")
        retrieved_at = datetime.fromisoformat(metadata["received_at"])
        if retrieved_at.tzinfo is None:
            raise ValueError("saved Dhan retrieval time has no timezone")
    return SavedResponseAudit(
        path, digest, retrieved_at, tuple(parse_response(raw, request))
    )


def recover_quarantined_response(
    request: HistoricalRequest, evidence: Path, output_dir: Path
) -> SavedResponseAudit:
    """Publish an already received chart body offline, retaining its receipt time."""
    raw = evidence.read_bytes()
    digest = sha256(raw).hexdigest()
    receipt = json.loads(evidence.with_suffix(".meta.json").read_text())
    if (
        receipt.get("schema_version") != "dhan-chart-quarantine-v1"
        or receipt.get("sha256") != digest
        or receipt.get("endpoint") != request.endpoint
        or receipt.get("request") != request.payload()
    ):
        raise ValueError("quarantined Dhan response provenance mismatch")
    received_at = datetime.fromisoformat(receipt["received_at"])
    if received_at.tzinfo is None:
        raise ValueError("quarantined Dhan receipt time has no timezone")
    bars = parse_response(raw, request)
    diagnostics = diagnose_response(raw, request)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = response_path(request, output_dir)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError("existing Dhan checkpoint has different response bytes")
    else:
        temporary = output_dir / f".{path.stem}.{uuid.uuid4().hex}.partial"
        try:
            _write_private_bytes(temporary, raw)
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    checksum = path.with_suffix(".sha256")
    if checksum.exists():
        if checksum.read_text().strip() != digest:
            raise ValueError("checkpoint source checksum mismatch")
    else:
        _write_private_bytes(checksum, (digest + "\n").encode())
    metadata_path = path.with_suffix(".meta.json")
    if not metadata_path.exists():
        _write_private_json(
            metadata_path,
            {
                "schema_version": "dhan-chart-raw-v1",
                "endpoint": request.endpoint,
                "method": "POST",
                "request": request.payload(),
                "received_at": receipt["received_at"],
                "sha256": digest,
                "normalization": {
                    "reordered": diagnostics["reordered"],
                    "sorted_original_indices": diagnostics["sorted_original_indices"],
                    "collapsed_duplicate_indices": diagnostics[
                        "identical_duplicate_indices"
                    ],
                },
                "recovered_from_quarantine": evidence.name,
            },
        )
    directory = os.open(output_dir, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    audit = verify_saved_response(request, path)
    if len(audit.bars) != len(bars):
        raise ValueError("recovered Dhan bar count changed")
    return audit


class HistoricalTransport(Protocol):
    def post_json(self, url: str, payload: dict[str, object], token: str) -> bytes: ...


class UrlLibTransport:
    def post_json(self, url: str, payload: dict[str, object], token: str) -> bytes:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, sort_keys=True).encode(),
            headers={"Content-Type": "application/json", "access-token": token},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return cast(bytes, response.read())


def run_backfill(
    requests: tuple[HistoricalRequest, ...],
    *,
    output_dir: Path,
    quarantine_dir: Path | None = None,
    enabled: bool = False,
    transport: HistoricalTransport | None = None,
    token: str | None = None,
    minimum_interval_seconds: float = 1.0,
    max_retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Path]:
    """Persist each complete response before advancing a resumable checkpoint."""
    if not enabled:
        raise PermissionError("authenticated historical acquisition is disabled")
    secret = token or os.environ.get("DHAN_ACCESS_TOKEN")
    if not secret:
        raise ValueError("local Dhan access token required")
    if minimum_interval_seconds <= 0 or max_retries < 0:
        raise ValueError("invalid pacing or retry policy")
    client = transport or UrlLibTransport()
    output_dir.mkdir(parents=True, exist_ok=True)
    quarantine = quarantine_dir or output_dir / ".quarantine"
    paths = []
    for req in requests:
        path = response_path(req, output_dir)
        label = path.stem
        if path.exists():
            raw_existing = path.read_bytes()
            parse_response(raw_existing, req)
            checksum = path.with_suffix(".sha256")
            digest = sha256(raw_existing).hexdigest()
            if checksum.exists():
                if checksum.read_text().strip() != digest:
                    raise ValueError("checkpoint source checksum mismatch")
            else:
                # Recover the sidecar after atomic raw publication.
                with checksum.open("x") as stream:
                    stream.write(digest + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            paths.append(path)
            continue
        for attempt in range(max_retries + 1):
            try:
                raw = client.post_json(req.endpoint, req.payload(), secret)
                received_at = datetime.now(UTC).isoformat()
                # The received bytes are evidence even when validation fails. Never
                # retry a validation error or advance the checkpoint without them.
                evidence = _quarantine_response(raw, req, quarantine, received_at)
                diagnostics = diagnose_response(raw, req)
                try:
                    parse_response(raw, req)
                except (ValueError, json.JSONDecodeError) as exc:
                    _write_private_json(
                        evidence.with_suffix(".diagnostics.json"),
                        {**diagnostics, "validation_error": str(exc)},
                    )
                    exc.add_note(
                        f"Response quarantined at {evidence}; "
                        f"SHA-256 {sha256(raw).hexdigest()}"
                    )
                    raise
                _write_private_json(
                    evidence.with_suffix(".diagnostics.json"), diagnostics
                )
                temporary = output_dir / f".{label}.{uuid.uuid4().hex}.partial"
                try:
                    with temporary.open("xb") as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.link(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
                checksum = path.with_suffix(".sha256")
                with checksum.open("x") as stream:
                    stream.write(sha256(raw).hexdigest() + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                metadata_temp = output_dir / f".{label}.{uuid.uuid4().hex}.meta"
                try:
                    with metadata_temp.open("xb") as stream:
                        stream.write(
                            (
                                json.dumps(
                                    {
                                        "schema_version": "dhan-chart-raw-v1",
                                        "endpoint": req.endpoint,
                                        "method": "POST",
                                        "request": req.payload(),
                                        "received_at": received_at,
                                        "sha256": sha256(raw).hexdigest(),
                                        "normalization": {
                                            "reordered": diagnostics["reordered"],
                                            "sorted_original_indices": diagnostics[
                                                "sorted_original_indices"
                                            ],
                                            "collapsed_duplicate_indices": diagnostics[
                                                "identical_duplicate_indices"
                                            ],
                                        },
                                    },
                                    sort_keys=True,
                                    separators=(",", ":"),
                                )
                                + "\n"
                            ).encode()
                        )
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(metadata_temp, path.with_suffix(".meta.json"))
                finally:
                    metadata_temp.unlink(missing_ok=True)
                directory = os.open(output_dir, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
                paths.append(path)
                break
            except urllib.error.HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == max_retries:
                    raise RuntimeError(f"Dhan chart HTTP {exc.code}") from None
                sleep(min(2**attempt, 16))
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == max_retries:
                    raise RuntimeError("Dhan chart transport failed") from exc
                sleep(min(2**attempt, 16))
        sleep(minimum_interval_seconds)
    return paths


def timestamp_to_date(epoch_seconds: int) -> date:
    return datetime.fromtimestamp(epoch_seconds, UTC).astimezone(_IST).date()


def _price_nanos(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (Decimal, int)):
        raise ValueError("invalid chart price")
    scaled = Decimal(value) * PRICE_SCALE
    if not scaled.is_finite() or scaled != scaled.to_integral_value():
        raise ValueError("chart price exceeds nanounit precision")
    return int(scaled)


def _timestamp_unit(stamp: int) -> str:
    magnitude = abs(stamp)
    if magnitude < 100_000_000_000:
        return "seconds"
    if magnitude < 100_000_000_000_000:
        return "milliseconds"
    if magnitude < 100_000_000_000_000_000:
        return "microseconds"
    return "nanoseconds_or_larger"


def _integral_json_number(value: object) -> int | None:
    """Accept exact JSON integers, including Dhan's integral decimal encoding."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if (
        isinstance(value, Decimal)
        and value.is_finite()
        and abs(value) < 10**20
        and value == value.to_integral_value()
    ):
        return int(value)
    return None


def diagnose_response(raw: bytes, request: HistoricalRequest) -> dict[str, object]:
    """Inspect saved bytes offline; never assume chart array ordering or units."""
    fields = ("timestamp", "open", "high", "low", "close", "volume")
    report: dict[str, object] = {
        "sha256": sha256(raw).hexdigest(),
        "request_start_inclusive": request.start.isoformat(),
        "request_end_exclusive": request.end.isoformat(),
    }
    try:
        data: Any = json.loads(raw, parse_float=Decimal)
    except (ValueError, UnicodeDecodeError) as exc:
        report["json_error"] = type(exc).__name__
        return report
    if not isinstance(data, dict):
        report["structure_error"] = "response is not an object"
        return report
    arrays = {name: data.get(name) for name in fields}
    report["array_lengths"] = {
        name: len(value) if isinstance(value, list) else None
        for name, value in arrays.items()
    }
    stamps = arrays["timestamp"]
    if not isinstance(stamps, list):
        return report
    normalized_stamps = [_integral_json_number(stamp) for stamp in stamps]
    report["timestamp_encodings"] = sorted({type(stamp).__name__ for stamp in stamps})
    report["timestamp_units"] = sorted(
        {
            _timestamp_unit(stamp) if stamp is not None else "invalid"
            for stamp in normalized_stamps
        }
    )
    out_of_order = []
    for index in range(1, len(normalized_stamps)):
        prior, current = normalized_stamps[index - 1], normalized_stamps[index]
        if prior is not None and current is not None and current < prior:
            out_of_order.append([index - 1, index])
    report["out_of_order_pairs"] = out_of_order
    if any(
        not isinstance(value, list) or len(value) != len(stamps)
        for value in arrays.values()
    ):
        return report
    rows = list(zip(*(arrays[name] for name in fields), strict=True))
    groups: dict[int, list[int]] = {}
    for index, stamp in enumerate(normalized_stamps):
        if stamp is not None:
            groups.setdefault(stamp, []).append(index)
    report["duplicate_timestamp_groups"] = [
        {"timestamp": stamp, "indices": indices}
        for stamp, indices in groups.items()
        if len(indices) > 1
    ]
    report["conflicting_duplicate_timestamps"] = [
        stamp
        for stamp, indices in groups.items()
        if len(indices) > 1
        and any(rows[index][1:] != rows[indices[0]][1:] for index in indices[1:])
    ]
    report["identical_duplicate_indices"] = [
        index
        for indices in groups.values()
        for index in indices[1:]
        if rows[index][1:] == rows[indices[0]][1:]
    ]
    valid_stamps = [stamp for stamp in normalized_stamps if stamp is not None]
    if len(valid_stamps) == len(stamps):
        original_indices = sorted(
            range(len(stamps)), key=lambda index: valid_stamps[index]
        )
        report["sorted_original_indices"] = original_indices
        report["reordered"] = original_indices != list(range(len(stamps)))
    else:
        report["sorted_original_indices"] = []
        report["reordered"] = False
    boundaries = []
    for index, stamp in enumerate(normalized_stamps):
        if stamp is not None and _timestamp_unit(stamp) == "seconds":
            try:
                trading_date = timestamp_to_date(stamp)
            except (ValueError, OSError, OverflowError):
                continue
            if not request.start <= trading_date < request.end:
                boundaries.append(
                    {
                        "index": index,
                        "timestamp": stamp,
                        "trading_date": trading_date.isoformat(),
                    }
                )
    report["outside_request"] = boundaries
    return report


def parse_response(raw: bytes, request: HistoricalRequest) -> list[HistoricalBar]:
    """Sort complete index-aligned candles; collapse only identical duplicates."""
    data: Any = json.loads(raw, parse_float=Decimal)
    if not isinstance(data, dict):
        raise ValueError("Dhan chart response must be an object")
    fields = ("timestamp", "open", "high", "low", "close", "volume")
    if any(not isinstance(data.get(name), list) for name in fields):
        raise ValueError("missing Dhan chart arrays")
    arrays = [data[name] for name in fields]
    count = len(arrays[0])
    if any(len(array) != count for array in arrays):
        raise ValueError("Dhan chart arrays have unequal lengths")
    result: list[HistoricalBar] = []
    digest = sha256(raw).hexdigest()
    previous_stamp: int | None = None
    rows = []
    for stamp, opened, high, low, close, volume in zip(*arrays, strict=True):
        normalized_stamp = _integral_json_number(stamp)
        if normalized_stamp is None:
            raise ValueError("invalid Dhan chart timestamp")
        rows.append((normalized_stamp, opened, high, low, close, volume))
    rows.sort(key=lambda row: row[0])
    for stamp, opened, high, low, close, volume in rows:
        if _timestamp_unit(stamp) != "seconds":
            raise ValueError("Dhan chart timestamp unit is not epoch seconds")
        normalized_volume = _integral_json_number(volume)
        if normalized_volume is None:
            raise ValueError("invalid chart volume")
        try:
            trading_date = timestamp_to_date(stamp)
        except (ValueError, OSError, OverflowError) as exc:
            raise ValueError("invalid Dhan chart timestamp") from exc
        if not request.start <= trading_date < request.end:
            raise ValueError("Dhan chart timestamp outside request")
        bar = HistoricalBar(
            instrument=request.instrument,
            interval_minutes=request.interval_minutes,
            trading_date=trading_date,
            source_timestamp_ns=stamp * 1_000_000_000
            if request.interval_minutes is not None
            else None,
            open_nanos=_price_nanos(opened),
            high_nanos=_price_nanos(high),
            low_nanos=_price_nanos(low),
            close_nanos=_price_nanos(close),
            volume=normalized_volume,
            source="DHAN_HISTORICAL",
            source_sha256=digest,
            timestamp_semantics="UNVERIFIED"
            if request.interval_minutes is not None
            else "DAILY",
        )
        if result and stamp == previous_stamp:
            prior = result[-1]
            if (
                prior.open_nanos,
                prior.high_nanos,
                prior.low_nanos,
                prior.close_nanos,
                prior.volume,
            ) != (
                bar.open_nanos,
                bar.high_nanos,
                bar.low_nanos,
                bar.close_nanos,
                bar.volume,
            ):
                raise ValueError("conflicting duplicate Dhan chart candles")
            continue
        result.append(bar)
        previous_stamp = stamp
    return result


def deduplicate_windows(groups: list[list[HistoricalBar]]) -> list[HistoricalBar]:
    """Identical overlaps collapse; conflicting source bars stop the import."""
    seen: dict[tuple[str, int | None, int], HistoricalBar] = {}
    for group in groups:
        for bar in group:
            stamp = (
                bar.source_timestamp_ns
                if bar.source_timestamp_ns is not None
                else bar.trading_date.toordinal()
            )
            key = (bar.instrument.security_id, bar.interval_minutes, stamp)
            prior = seen.get(key)
            if prior is not None:
                values = (
                    "open_nanos",
                    "high_nanos",
                    "low_nanos",
                    "close_nanos",
                    "volume",
                )
                if any(getattr(prior, name) != getattr(bar, name) for name in values):
                    raise ValueError("conflicting bars at window boundary")
            else:
                seen[key] = bar
    return sorted(
        seen.values(),
        key=lambda bar: (
            bar.instrument.security_id,
            bar.source_timestamp_ns or bar.trading_date.toordinal(),
        ),
    )


def iter_saved_backfill(
    requests: tuple[HistoricalRequest, ...], paths: list[Path]
) -> Iterator[HistoricalBar]:
    """Replay one bounded raw window at a time, verifying checkpoint hashes."""
    if len(requests) != len(paths):
        raise ValueError("one original response is required per request")
    previous: dict[tuple[str, int | None], HistoricalBar] = {}
    for request, path in zip(requests, paths, strict=True):
        raw = path.read_bytes()
        checksum_path = path.with_suffix(".sha256")
        if (
            not checksum_path.exists()
            or checksum_path.read_text().strip() != sha256(raw).hexdigest()
        ):
            raise ValueError("saved Dhan response checksum mismatch")
        for bar in parse_response(raw, request):
            key = (bar.instrument.security_id, bar.interval_minutes)
            prior = previous.get(key)
            if prior is not None:
                old_stamp = prior.source_timestamp_ns or prior.trading_date.toordinal()
                new_stamp = bar.source_timestamp_ns or bar.trading_date.toordinal()
                if new_stamp < old_stamp:
                    raise ValueError("saved Dhan windows are out of order")
                if new_stamp == old_stamp:
                    values = (
                        "open_nanos",
                        "high_nanos",
                        "low_nanos",
                        "close_nanos",
                        "volume",
                    )
                    if any(
                        getattr(prior, name) != getattr(bar, name) for name in values
                    ):
                        raise ValueError("conflicting bars at window boundary")
                    continue
            previous[key] = bar
            yield bar
