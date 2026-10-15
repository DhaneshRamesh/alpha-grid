"""Read-only Dhan historical charts, offline unless explicitly enabled."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

from quant.acquisition.contracts import CashInstrument

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
            paths.append(path)
            continue
        for attempt in range(max_retries + 1):
            try:
                raw = client.post_json(req.endpoint, req.payload(), secret)
                parsed: Any = json.loads(raw)
                if not isinstance(parsed, dict) or "timestamp" not in parsed:
                    raise ValueError("invalid Dhan chart response")
                # Original response bytes remain immutable. O_EXCL prevents overwrite.
                with path.open("xb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
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
