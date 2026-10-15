"""Read-only Dhan historical charts, offline unless explicitly enabled."""

from __future__ import annotations

import json
import os
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
        "estimate_note": "calendar-day upper bound; not a measured download size",
        "output_dir": str(output_dir),
        "subscription": (
            "Dhan Data API entitlement and valid local access token required"
        ),
        "requests": [
            {"endpoint": req.endpoint, "payload": req.payload()} for req in requests
        ],
    }


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
    paths = []
    for req in requests:
        label = (
            f"{req.instrument.security_id}-{req.interval_minutes or 'day'}-"
            f"{req.start}-{req.end}"
        )
        path = output_dir / f"{label}.json"
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
                parse_response(raw, req)
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


def parse_response(raw: bytes, request: HistoricalRequest) -> list[HistoricalBar]:
    """Parse parallel arrays without trusting their order or clock meaning."""
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
    result = []
    digest = sha256(raw).hexdigest()
    previous = -1
    for stamp, opened, high, low, close, volume in zip(*arrays, strict=True):
        if isinstance(stamp, bool) or not isinstance(stamp, int) or stamp <= previous:
            raise ValueError("Dhan chart timestamps must increase")
        if isinstance(volume, bool) or not isinstance(volume, int):
            raise ValueError("invalid chart volume")
        previous = stamp
        trading_date = timestamp_to_date(stamp)
        if not request.start <= trading_date < request.end:
            raise ValueError("Dhan chart timestamp outside request")
        result.append(
            HistoricalBar(
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
                volume=volume,
                source="DHAN_HISTORICAL",
                source_sha256=digest,
                timestamp_semantics="UNVERIFIED"
                if request.interval_minutes is not None
                else "DAILY",
            )
        )
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
