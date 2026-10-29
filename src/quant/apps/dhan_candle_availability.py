"""Bounded, opt-in Dhan historical-candle availability probe."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from quant.acquisition.calendar import load_calendar
from quant.acquisition.contracts import CashInstrument
from quant.acquisition.dhan import (
    HistoricalHttpResponse,
    HistoricalRequest,
    UrlLibTransport,
    _private_directory,
    _quarantine_response,
    _verify_windows_private_acl,
    _write_private_json,
    parse_response,
)
from quant.acquisition.registry import load_registry

IST = ZoneInfo("Asia/Kolkata")
SESSION_DAY = date(2026, 10, 27)
SLOT_TIMES = (
    "09:59:55",
    "10:00:02",
    "10:00:07",
    "10:00:20",
    "10:00:45",
    "10:00:55",
    "10:01:02",
    "10:01:07",
    "10:01:20",
    "10:01:45",
    "10:02:02",
    "10:02:20",
    "10:05:00",
    "15:45:00",
)
TARGET_TIMES = ("09:59:00", "10:00:00", "10:01:00")
REQUEST = HistoricalRequest(
    CashInstrument("NSE", "NSE_EQ", "2885", "RELIANCE"),
    SESSION_DAY,
    SESSION_DAY + timedelta(days=1),
    1,
)


class TimedTransport(Protocol):
    def post_json_with_metadata(
        self, url: str, payload: dict[str, object], token: str
    ) -> HistoricalHttpResponse: ...


def _slot_utc(value: str) -> datetime:
    local = datetime.combine(SESSION_DAY, clock_time.fromisoformat(value), IST)
    return local.astimezone(UTC)


def plan() -> dict[str, object]:
    return {
        "mode": "PLAN_NO_NETWORK",
        "session": SESSION_DAY.isoformat(),
        "instrument": "NSE_EQ:2885:RELIANCE",
        "endpoint": REQUEST.endpoint,
        "request": REQUEST.payload(),
        "scheduled_ist": [f"{SESSION_DAY}T{slot}+05:30" for slot in SLOT_TIMES],
        "maximum_requests": len(SLOT_TIMES),
        "retries": 0,
        "target_epoch_labels_ist": list(TARGET_TIMES),
        "timing_eligibility": "UNVERIFIED",
    }


def _target_rows(raw: bytes) -> dict[str, list[int]]:
    targets = {int(_slot_utc(value).timestamp()) for value in TARGET_TIMES}
    result: dict[str, list[int]] = {}
    for bar in parse_response(raw, REQUEST):
        stamp = (bar.source_timestamp_ns or 0) // 1_000_000_000
        if stamp in targets:
            result[str(stamp)] = [
                bar.open_nanos,
                bar.high_nanos,
                bar.low_nanos,
                bar.close_nanos,
                bar.volume,
            ]
    return result


def _verified_session(calendar_path: Path) -> None:
    calendar = load_calendar(calendar_path)
    session = calendar.special.get(SESSION_DAY)
    if (
        session is None
        or not session.verified
        or session.opens != clock_time(9, 15)
        or session.closes
        != {
            "CAS_CONTINUOUS_TRADING": clock_time(15, 15),
            "NON_CAS_CONTINUOUS_TRADING": clock_time(15, 30),
        }.get(session.kind)
    ):
        raise ValueError("dated, explicitly verified NSE session required")
    calendar.session(SESSION_DAY)  # Enforce the calendar's dated scope.


def _verified_instrument(registry_path: Path) -> None:
    registry = load_registry(registry_path)
    record = registry.by_security_id(REQUEST.instrument.security_id, SESSION_DAY)
    if record.instrument != REQUEST.instrument or registry.corporate_action_on(
        REQUEST.instrument.security_id, SESSION_DAY
    ):
        raise ValueError("dated RELIANCE identity or corporate-action check failed")


def capture(
    output_dir: Path,
    calendar_path: Path,
    registry_path: Path,
    token: str,
    *,
    max_requests: int,
    transport: TimedTransport | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    monotonic_ns: Callable[[], int] = time.perf_counter_ns,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    """Save every received body before inspecting it; skip missed slots, never retry."""
    if max_requests != len(SLOT_TIMES) or not token:
        raise PermissionError("exact 14-request scope and local access token required")
    _verified_session(calendar_path)
    _verified_instrument(registry_path)
    if now().astimezone(IST).date() != SESSION_DAY or now() >= _slot_utc(
        SLOT_TIMES[0]
    ) - timedelta(seconds=15):
        raise ValueError("probe must start on its verified session before 09:59:40 IST")
    _private_directory(output_dir)
    if any(output_dir.iterdir()):
        raise FileExistsError("availability output must be empty and immutable")
    _write_private_json(output_dir / "plan.json", plan())
    client = transport or UrlLibTransport()
    sent = 0
    skipped = 0
    stop_reason: str | None = None
    baseline_wall = now()
    baseline_mono = monotonic_ns()
    last_start_mono: int | None = None
    for index, slot in enumerate(SLOT_TIMES):
        target = _slot_utc(slot)
        delay = (target - now()).total_seconds()
        if delay > 0:
            sleep(delay)
        current = now()
        drift_ms = abs(
            (current - baseline_wall).total_seconds() * 1000
            - (monotonic_ns() - baseline_mono) / 1_000_000
        )
        if drift_ms > 250:
            stop_reason = "LOCAL_CLOCK_STEP_OR_DRIFT"
            break
        if current > target + timedelta(seconds=1):
            skipped += 1
            _write_private_json(
                output_dir / f"slot-{index:02}.json",
                {"scheduled_ist": slot, "state": "MISSED_NO_REQUEST"},
            )
            continue
        start_mono = monotonic_ns()
        if last_start_mono is not None and start_mono - last_start_mono < 1_000_000_000:
            skipped += 1
            _write_private_json(
                output_dir / f"slot-{index:02}.json",
                {"scheduled_ist": slot, "state": "RATE_GUARD_NO_REQUEST"},
            )
            continue
        last_start_mono = start_mono
        started = now()
        sent += 1
        try:
            response = client.post_json_with_metadata(
                REQUEST.endpoint, REQUEST.payload(), token
            )
        except urllib.error.HTTPError as exc:
            response = HistoricalHttpResponse(
                exc.read(), exc.code, exc.headers.get("Date")
            )
        except (urllib.error.URLError, TimeoutError) as exc:
            ended = now()
            _write_private_json(
                output_dir / f"slot-{index:02}.json",
                {
                    "scheduled_ist": slot,
                    "state": "TRANSPORT_FAILED_NO_BODY",
                    "request_started_at_utc": started.isoformat(),
                    "response_finished_at_utc": ended.isoformat(),
                    "elapsed_monotonic_ns": monotonic_ns() - start_mono,
                    "error_type": type(exc).__name__,
                },
            )
            stop_reason = "TRANSPORT_FAILED_NO_RETRY"
            break
        ended_mono = monotonic_ns()
        ended = now()
        evidence = _quarantine_response(
            response.raw,
            REQUEST,
            output_dir,
            ended.isoformat(),
            http_status=response.http_status,
        )
        item: dict[str, object] = {
            "scheduled_ist": slot,
            "state": "RESPONSE_SAVED",
            "request_started_at_utc": started.isoformat(),
            "response_finished_at_utc": ended.isoformat(),
            "elapsed_monotonic_ns": ended_mono - start_mono,
            "http_status": response.http_status,
            "server_date_header": response.server_date,
            "response_file": evidence.name,
            "sha256": sha256(response.raw).hexdigest(),
        }
        if response.http_status == 200:
            try:
                item["target_rows"] = _target_rows(response.raw)
            except (ValueError, json.JSONDecodeError) as exc:
                item["parse_error"] = type(exc).__name__
                stop_reason = "INVALID_RESPONSE_NO_RETRY"
        else:
            stop_reason = "HTTP_ERROR_NO_RETRY"
        _write_private_json(output_dir / f"slot-{index:02}.json", item)
        if stop_reason:
            break
    result = {
        "mode": "READ_ONLY_CANDLE_AVAILABILITY",
        "requests_sent": sent,
        "slots_skipped": skipped,
        "maximum_requests": len(SLOT_TIMES),
        "stop_reason": stop_reason,
        "timing_eligibility": "UNVERIFIED",
    }
    _write_private_json(output_dir / "capture-summary.json", result)
    return result


def report(output_dir: Path) -> dict[str, object]:
    """Recheck saved bytes and summarize sampled revisions, never assert completion."""
    if not (output_dir / "plan.json").is_file():
        raise ValueError("availability capture plan is missing")
    _private_directory(output_dir)
    if os.name == "nt":
        _verify_windows_private_acl(output_dir / "plan.json")
    if json.loads((output_dir / "plan.json").read_text()) != plan():
        raise ValueError("availability capture plan mismatch")
    snapshots: list[dict[str, Any]] = []
    for path in sorted(output_dir.glob("slot-*.json")):
        if os.name == "nt":
            _verify_windows_private_acl(path)
        item = json.loads(path.read_text())
        if item.get("state") != "RESPONSE_SAVED":
            continue
        name = item["response_file"]
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError("invalid availability response path")
        raw_path = output_dir / name
        metadata_path = raw_path.with_suffix(".meta.json")
        if os.name == "nt":
            _verify_windows_private_acl(raw_path)
            _verify_windows_private_acl(metadata_path)
        raw = raw_path.read_bytes()
        receipt = json.loads(metadata_path.read_text())
        digest = sha256(raw).hexdigest()
        if (
            digest != item["sha256"]
            or digest != receipt["sha256"]
            or receipt["request"] != REQUEST.payload()
            or receipt["endpoint"] != REQUEST.endpoint
            or receipt["received_at"] != item["response_finished_at_utc"]
            or receipt.get("http_status") != item["http_status"]
        ):
            raise ValueError("availability response integrity or provenance mismatch")
        if item["http_status"] != 200 or "parse_error" in item:
            continue
        rows = _target_rows(raw)
        if rows != item["target_rows"]:
            raise ValueError("availability derived candle revision mismatch")
        snapshots.append(
            {
                "slot": path.stem,
                "request_started_at_utc": item["request_started_at_utc"],
                "response_finished_at_utc": item["response_finished_at_utc"],
                "rows": rows,
            }
        )
    revisions: dict[str, object] = {}
    stamps = sorted(str(int(_slot_utc(value).timestamp())) for value in TARGET_TIMES)
    for stamp in stamps:
        observed = [row for row in snapshots if stamp in row["rows"]]
        if not observed:
            revisions[stamp] = {"state": "NOT_SEEN", "completion_time_verified": False}
            continue
        first = observed[0]
        first_index = snapshots.index(first)
        prior_absent = snapshots[first_index - 1] if first_index > 0 else None
        changes = [
            row
            for previous, row in zip(observed, observed[1:], strict=False)
            if row["rows"][stamp] != previous["rows"][stamp]
        ]
        final_value = observed[-1]["rows"][stamp]
        final_run = observed[-1:]
        for row in reversed(observed[:-1]):
            if row["rows"][stamp] != final_value:
                break
            final_run.insert(0, row)
        disappeared = any(
            stamp not in row["rows"] for row in snapshots[first_index + 1 :]
        )
        revisions[stamp] = {
            "first_seen_interval_utc": [
                first["request_started_at_utc"],
                first["response_finished_at_utc"],
            ],
            "last_absent_interval_utc": (
                [
                    prior_absent["request_started_at_utc"],
                    prior_absent["response_finished_at_utc"],
                ]
                if prior_absent is not None
                else None
            ),
            "observed_revision_count": len(changes),
            "revision_slots": [row["slot"] for row in changes],
            "disappeared_after_first_seen": disappeared,
            "observed_unchanged_tail_from_slot": (
                final_run[0]["slot"]
                if len(final_run) >= 2 and not disappeared
                else None
            ),
            "post_close_anchor_matches": bool(
                observed[-1]["slot"] == f"slot-{len(SLOT_TIMES) - 1:02}"
                and len(final_run) >= 2
                and not disappeared
            ),
            "completion_time_verified": False,
        }
    return {
        "mode": "OFFLINE_AVAILABILITY_REPORT",
        "source": "SAVED_DHAN_RESPONSES_ONLY",
        "epoch_label_observations": revisions,
        "clock_offset_verified": False,
        "unseen_revisions_possible": True,
        "historical_causal_eligibility": "UNVERIFIED",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-read-only", action="store_true")
    parser.add_argument("--approved-scope", action="store_true")
    parser.add_argument("--max-requests", type=int, default=0)
    parser.add_argument("--calendar", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/historical/availability-2026-10-27"),
    )
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if args.report:
        result = report(args.output_dir)
    elif args.execute_read_only:
        if not args.approved_scope or args.calendar is None or args.registry is None:
            raise PermissionError(
                "separate approval, dated calendar and registry required"
            )
        token = os.environ.get("DHAN_ACCESS_TOKEN", "")
        result = capture(
            args.output_dir,
            args.calendar,
            args.registry,
            token,
            max_requests=args.max_requests,
        )
    else:
        result = plan()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
