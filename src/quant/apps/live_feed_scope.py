"""Offline approval and preflight gate for a fixed 25-name quote session."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path

from quant.acquisition.calendar import IST
from quant.apps.dhan_quote_preflight import check, check_raw_capture
from quant.apps.live_feed_credentials import load_credentials


def _record_capture_validation(private: Path, day: str, record: dict) -> None:
    """Keep non-secret preflight measurements with each day's quarantined QJ01."""
    path = private / f"raw-capture-validation-{day}.jsonl"
    if path.is_symlink() or (path.exists() and path.stat().st_mode & 0o077):
        raise PermissionError("capture validation ledger is not private")
    fd = os.open(
        path,
        os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(fd, "ab") as stream:
        stream.write(json.dumps(record, sort_keys=True).encode() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def valid_authorization(
    root: Path, private: Path, now: datetime
) -> tuple[dict, list[str], str]:
    """Check saved permission independently of market hours or source preflight."""
    approval = private / "feed-scope.json"
    if (
        not approval.is_file()
        or approval.is_symlink()
        or approval.stat().st_mode & 0o077
    ):
        raise ValueError("private dated feed approval absent")
    scope = json.loads(approval.read_text())
    version = scope.get("schema_version")
    if version not in {
        "alpha-grid-approved-quote-scope-v1",
        "alpha-grid-market-data-access-v2",
    }:
        raise ValueError("approved quote scope unavailable")
    if (
        scope.get("purpose") != "SIMULATION_ONLY"
        or scope.get("standard_connections") != 1
        or scope.get("depth_connections") not in (0, 1)
        or scope.get("authorization_confirmed") is not True
    ):
        raise ValueError("approved quote scope unavailable")
    if version == "alpha-grid-approved-quote-scope-v1":
        if (
            scope.get("session_date_ist") != now.date().isoformat()
            or scope.get("depth_connections") != 0
        ):
            raise ValueError("dated approved quote scope unavailable")
    else:
        if (
            scope.get("segment") != "NSE_EQ"
            or scope.get("auto_connect") is not True
            or scope.get("revoked_at") is not None
            or scope.get("duration") not in {"SESSION_ONLY", "PERSISTENT"}
            or scope.get("data_entitlement_confirmed") is not True
        ):
            raise ValueError("approved quote scope disabled or revoked")
        if (
            scope["duration"] == "SESSION_ONLY"
            and scope.get("session_date_ist") != now.date().isoformat()
        ):
            raise ValueError("session-only approval expired")
        optional_expiry = scope.get("expires_at")
        if optional_expiry is not None:
            try:
                parsed_expiry = datetime.fromisoformat(
                    optional_expiry.replace("Z", "+00:00")
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("approval expiry invalid") from exc
            if parsed_expiry.tzinfo is None or parsed_expiry <= now:
                raise ValueError("approved feed scope expired")
    universe = root / "docs/cross-stock-universe-2026-11-02.json"
    raw = universe.read_bytes()
    if hashlib.sha256(raw).hexdigest() != scope.get("universe_sha256"):
        raise ValueError("frozen universe bytes mismatch")
    manifest = json.loads(raw)
    ids = [row["security_id"] for row in manifest["instruments"]]
    if len(ids) != 25 or len(set(ids)) != 25:
        raise ValueError("fixed universe IDs invalid")
    feeds = scope.get("authorized_feeds")
    standard_feed = feeds[0] if isinstance(feeds, list) and feeds else None
    if standard_feed not in {"STANDARD_QUOTE", "STANDARD_FULL"}:
        raise ValueError("approved standard feed mode unavailable")
    if version == "alpha-grid-approved-quote-scope-v1" and standard_feed != "STANDARD_QUOTE":
        raise ValueError("legacy quote approval cannot authorize Full packets")
    required_feeds = [standard_feed] + (
        ["DEPTH_20"] if scope["depth_connections"] else []
    )
    if (
        scope.get("authorized_security_ids") != ids
        or scope.get("authorized_feeds") != required_feeds
    ):
        raise ValueError("approved instruments or feed differ from frozen universe")
    if version == "alpha-grid-approved-quote-scope-v1":
        try:
            dated_expiry = datetime.fromisoformat(scope["expires_at_ist"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("approved feed expiry invalid") from exc
        if (
            dated_expiry.tzinfo is None
            or dated_expiry.utcoffset() != IST.utcoffset(now)
            or dated_expiry.astimezone(IST).date() != now.date()
            or dated_expiry <= now
            or dated_expiry.time() > clock_time(15, 14)
        ):
            raise ValueError("approved feed scope expired or exceeds session cutoff")
    return scope, ids, version


def ensure_local_read_only_scope(root: Path, private: Path) -> None:
    """Create the fixed standard-feed scope once a usable local token exists.

    An existing file, including a revoked approval, is never replaced here.
    """
    path = private / "feed-scope.json"
    if path.exists() or path.is_symlink():
        return
    token, _client, _expiry = load_credentials(root, None, False)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    profile_path = private / "dhan-profile-check.json"
    prior = None
    if profile_path.is_symlink() or (profile_path.exists() and profile_path.stat().st_mode & 0o077):
        raise PermissionError("private Dhan profile status invalid")
    if profile_path.exists():
        prior = json.loads(profile_path.read_text())
    if (
        prior is None
        or prior.get("token_sha256") != token_hash
        or prior.get("result") != "DHAN_TOKEN_AND_DATA_PLAN_ACTIVE"
    ):
        from quant.apps.live_feed_credentials import _check_profile

        result = _check_profile(root, private)
        if profile_path.exists():
            prior = json.loads(profile_path.read_text())
        else:
            prior = {
                "schema_version": "alpha-grid-dhan-profile-check-v1",
                "token_sha256": token_hash,
                "result": result,
                "checked_at": datetime.now(UTC).isoformat(),
            }
    if prior.get("result") != "DHAN_TOKEN_AND_DATA_PLAN_ACTIVE":
        raise ValueError(str(prior.get("result", "PROFILE_CHECK_UNAVAILABLE")))
    universe = root / "docs/cross-stock-universe-2026-11-02.json"
    raw = universe.read_bytes()
    ids = [row["security_id"] for row in json.loads(raw)["instruments"]]
    if len(ids) != 25 or len(set(ids)) != 25:
        raise ValueError("fixed universe IDs invalid")
    value = {
        "schema_version": "alpha-grid-market-data-access-v2",
        "purpose": "SIMULATION_ONLY",
        "segment": "NSE_EQ",
        "universe_sha256": hashlib.sha256(raw).hexdigest(),
        "authorized_security_ids": ids,
        "authorized_feeds": ["STANDARD_QUOTE"],
        "standard_connections": 1,
        "depth_connections": 0,
        "duration": "PERSISTENT",
        "session_date_ist": None,
        "expires_at": None,
        "auto_connect": True,
        "authorization_confirmed": True,
        "data_entitlement_confirmed": True,
        "data_entitlement_evidence": "DHAN_V2_PROFILE_ACTIVE",
        "data_entitlement_checked_at": prior["checked_at"],
        "authorized_at": datetime.now(UTC).isoformat(),
        "authorization_source": "LOCAL_CREDENTIAL_DEFAULT",
        "revoked_at": None,
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def approved_plan(
    root: Path, private: Path, now_ns: int | None = None
) -> tuple[str, ...]:
    now_ns = now_ns or time.time_ns()
    now = datetime.fromtimestamp(now_ns / 1_000_000_000, IST)
    scope, ids, version = valid_authorization(root, private, now)
    standard_feed = scope["authorized_feeds"][0]
    universe = root / "docs/cross-stock-universe-2026-11-02.json"
    if not clock_time(9, 15) <= now.time() < clock_time(15, 14):
        raise ValueError("capture window ended or market closed")
    if version == "alpha-grid-approved-quote-scope-v1":
        expiry = datetime.fromisoformat(scope["expires_at_ist"])
    else:
        expiry = now.replace(hour=15, minute=14, second=0, microsecond=0)
        if scope.get("expires_at"):
            expiry = min(
                expiry,
                datetime.fromisoformat(
                    scope["expires_at"].replace("Z", "+00:00")
                ).astimezone(IST),
            )
    _token, _client, token_expiry = load_credentials(root, now.astimezone(UTC))
    effective_expiry = min(expiry, token_expiry.astimezone(IST) - timedelta(minutes=1))

    def local_path(name: str) -> Path:
        value = scope.get(name)
        if not isinstance(value, str) or not value or "|" in value or "\n" in value:
            raise ValueError(f"{name} path invalid")
        path = (root / value).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError(f"{name} outside repository")
        return path

    if version == "alpha-grid-market-data-access-v2":
        dated = now.strftime("%d%m%Y")
        candidates = sorted(
            (root / "data/instruments").glob("dhan-scrip-master-????-??-??.csv")
        )
        if not candidates:
            raise ValueError("current Dhan instrument master unavailable")
        master = candidates[-1]
        master_manifest = master.with_suffix(".manifest.json")
        calendar = root / "data/config/calendar.json"
        nse = root / f"data/instruments/NSE_CM_security_{dated}.csv.gz"
        nse_manifest = root / f"data/instruments/NSE_CM_security_{dated}.manifest.json"
    else:
        master = local_path("master")
        master_manifest = local_path("master_manifest")
        calendar = local_path("calendar")
        nse = local_path("nse_security")
        nse_manifest = local_path("nse_manifest")
    session_dir = private / f"raw-standard-{now.date().isoformat()}"
    flat_file = private / f"raw-standard-{now.date().isoformat()}.qj"
    if version == "alpha-grid-market-data-access-v2":
        if session_dir.is_dir():
            journal = session_dir
        elif flat_file.is_file():
            journal = flat_file
        else:
            # Normal startup rotates into segmented journal directory
            journal = session_dir
    else:
        journal = private / f"standard-{now.date().isoformat()}.qj"
    if version == "alpha-grid-market-data-access-v2":
        capture = check_raw_capture(
            journal=journal,
            master=master,
            manifest=master_manifest,
            universe_path=universe,
            now_ns=now_ns,
        )
        unresolved = []
        strict_error = None
        try:
            check(
                journal=journal,
                master=master,
                manifest=master_manifest,
                calendar_path=calendar,
                now_ns=now_ns,
                nse_security=nse,
                nse_manifest=nse_manifest,
                universe_path=universe,
            )
        except (OSError, ValueError, KeyError, TypeError, PermissionError) as exc:
            strict_error = str(exc)
            unresolved.extend((
                "DATED_NSE_IDENTITY_OR_CAS_UNVERIFIED",
                "DATED_SESSION_CLASSIFICATION_UNVERIFIED",
            ))
        clock_offset = None
        clock_uncertainty = None
        try:
            clock = subprocess.run(
                ["sntp", "time.google.com"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            ).stdout
            match = re.match(
                r"^([+-]?\d+(?:\.\d+)?)\s+\+/-\s+(\d+(?:\.\d+)?)\s+time\.google\.com\b",
                clock.strip(),
            )
            if match:
                clock_offset, clock_uncertainty = float(match[1]), float(match[2])
        except (OSError, subprocess.SubprocessError):
            pass
        if clock_offset is None or abs(clock_offset) + clock_uncertainty > 0.100:
            unresolved.append("CLOCK_PRECISION_UNVERIFIED")
        decision_eligible = strict_error is None and not unresolved
        validation = {
            "schema_version": "alpha-grid-raw-capture-validation-v1",
            "checked_at_ns": now_ns,
            "eligibility": (
                "RAW_CAPTURE_WITH_ELIGIBLE_PROJECTION"
                if decision_eligible else "RAW_CAPTURE_ONLY"
            ),
            "research_eligible": False,
            "production_eligible": False,
            "virtual_decision_eligible": decision_eligible,
            "unresolved": unresolved,
            "strict_preflight_error": strict_error,
            "dhan_master_sha256": capture["dhan_master_sha256"],
            "dhan_master_retrieved_at": capture["dhan_master_retrieved_at"],
            "universe_sha256": scope["universe_sha256"],
            "clock_offset_seconds": clock_offset,
            "clock_uncertainty_seconds": clock_uncertainty,
            "subscription_security_ids": ids,
        }
        _record_capture_validation(private, now.date().isoformat(), validation)
    else:
        check(
            journal=journal,
            master=master,
            manifest=master_manifest,
            calendar_path=calendar,
            now_ns=now_ns,
            nse_security=nse,
            nse_manifest=nse_manifest,
            universe_path=universe,
        )
        clock = subprocess.run(
            ["sntp", "time.google.com"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
        match = re.match(
            r"^([+-]?\d+(?:\.\d+)?)\s+\+/-\s+(\d+(?:\.\d+)?)\s+time\.google\.com\b",
            clock.strip(),
        )
        if not match or abs(float(match[1])) + float(match[2]) > 0.100:
            raise ValueError("independent clock uncertainty exceeds 100 ms")
    depth_ready = 0
    if scope["depth_connections"]:
        depth_binary = root / "rust/target/debug/quant-depth"
        if (
            depth_binary.is_file()
            and os.access(depth_binary, os.X_OK)
            and shutil.disk_usage(private).free >= 512 * 1024 * 1024
        ):
            depth_ready = 1
    return tuple(
        str(value)
        for value in (
            ",".join(ids),
            journal,
            nse,
            nse_manifest,
            master,
            master_manifest,
            calendar,
            universe,
            standard_feed,
            depth_ready,
            ("RAW_CAPTURE_WITH_ELIGIBLE_PROJECTION" if decision_eligible
             else "RAW_CAPTURE_ONLY")
            if version == "alpha-grid-market-data-access-v2" else "RESEARCH_PREFLIGHT",
            int(effective_expiry.timestamp()),
        )
    )


def evaluated_gate(
    root: Path, private: Path, now_ns: int | None = None
) -> tuple[tuple[str, ...] | None, str, str]:
    """Return an offline plan or a non-secret, measured reason for blocking."""
    now_ns = now_ns or time.time_ns()
    fields = None
    state = "BLOCKED"
    reason = "PREFLIGHT_FAILED"
    try:
        now = datetime.fromtimestamp(now_ns / 1_000_000_000, IST)
        load_credentials(root, now.astimezone(UTC))
        valid_authorization(root, private, now)
        if not clock_time(9, 15) <= now.time() < clock_time(15, 14):
            state, reason = "MARKET CLOSED", "AUTO_CONNECT_ARMED"
        else:
            fields = approved_plan(root, private, now_ns)
            state, reason = (
                "CONNECTING",
                (
                    "RAW_CAPTURE_VALIDATION_PENDING"
                    if fields[-2] == "RAW_CAPTURE_ONLY"
                    else "APPROVED_PREFLIGHT_PASSED"
                ),
            )
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
    ) as exc:
        message = str(exc).lower()
        if any(
            part in message
            for part in ("credential", "token", "authentication required")
        ):
            state, reason = "AUTH REQUIRED", "CREDENTIAL_UNAVAILABLE_OR_EXPIRED"
        elif "scope expired" in message or "session cutoff" in message:
            state, reason = "MARKET CLOSED", "CAPTURE_WINDOW_ENDED"
        elif "capture window ended" in message or "market closed" in message:
            state, reason = "MARKET CLOSED", "CAPTURE_WINDOW_ENDED"
        elif "nse_cm_security" in message or "dated nse" in message:
            reason = "DATED_NSE_IDENTITY_OR_CAS_UNAVAILABLE"
        elif "dhan-scrip-master" in message or "master" in message:
            reason = "CURRENT_DHAN_INSTRUMENT_MASTER_UNAVAILABLE"
        elif "calendar" in message or "ordinary session" in message:
            reason = "DATED_SESSION_EVIDENCE_UNAVAILABLE"
        elif "clock" in message or "sntp" in message:
            reason = "INDEPENDENT_CLOCK_UNVERIFIED"
        elif "storage" in message or "journal" in message or "capacity" in message:
            reason = "PRIVATE_STORAGE_UNAVAILABLE"
        elif "approval absent" in message:
            profile_path = private / "dhan-profile-check.json"
            try:
                profile = json.loads(profile_path.read_text())
                result = profile.get("result")
                reason = result if result != "DHAN_TOKEN_AND_DATA_PLAN_ACTIVE" else "NO_APPROVED_FEED_SCOPE"
            except (OSError, ValueError, TypeError):
                reason = "NO_APPROVED_FEED_SCOPE"
        elif "approval" in message or "scope" in message:
            reason = "APPROVED_SCOPE_DISABLED_OR_EXPIRED"
    return fields, state, reason


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--status-file", type=Path)
    parser.add_argument("--display-only", action="store_true")
    parser.add_argument("--auto-local-scope", action="store_true")
    args = parser.parse_args()
    if args.auto_local_scope:
        try:
            ensure_local_read_only_scope(args.root, args.private_dir)
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if args.display_only:
        try:
            _scope, ids, _version = valid_authorization(
                args.root, args.private_dir, datetime.now(IST)
            )
        except (OSError, ValueError, KeyError, TypeError):
            return 1
        print(",".join(ids))
        return 0
    fields, state, reason = evaluated_gate(args.root, args.private_dir)
    if args.status_file:
        if (
            args.status_file.parent != args.private_dir
            or args.status_file.name != "feed-gate-status.json"
        ):
            raise ValueError("status file must be the private feed gate status")
        record = {
            "schema_version": "alpha-grid-feed-gate-v1",
            "state": state,
            "reason": reason,
            "checked_at_ns": time.time_ns(),
        }
        fd, name = tempfile.mkstemp(prefix="feed-gate-", dir=args.private_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(record) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            Path(name).replace(args.status_file)
        finally:
            Path(name).unlink(missing_ok=True)
    if fields is None:
        return 1
    print("|".join(fields))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
