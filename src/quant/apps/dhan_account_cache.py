"""Private, immutable evidence for the five approved read-only Dhan account GETs."""

from __future__ import annotations

import base64
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from quant.acquisition.dhan import (
    _private_directory,
    _sync_directory,
    _verify_windows_private_acl,
    _write_private_bytes,
    _write_private_json,
)

ROOT = Path("data/.secrets/dhan-account-cache")
TOKEN_PATH = Path("data/.secrets/dhan-access-token")
ENDPOINTS = ("fundlimit", "holdings", "positions", "orders", "trades")
MIN_INTERVAL = timedelta(minutes=5)
MAX_BODY = 2 * 1024 * 1024
IST = timezone(timedelta(hours=5, minutes=30))


def _state_path(name: str) -> Path:
    if name not in ENDPOINTS:
        raise ValueError("unsupported account endpoint")
    return ROOT / f"{name}.state.json"


def _read_state(name: str) -> dict[str, Any]:
    path = _state_path(name)
    _private_directory(ROOT)
    if not path.exists():
        return {}
    if os.name == "nt":
        _verify_windows_private_acl(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "dhan-account-state-v1":
        raise ValueError("account state schema mismatch")
    return dict(value)


def reserve(name: str, now: datetime | None = None) -> bool:
    """Persist the attempt before network I/O; restart cannot shorten spacing."""
    now = now or datetime.now(UTC)
    state = _read_state(name)
    if previous := state.get("last_attempt_at"):
        elapsed = now - datetime.fromisoformat(previous)
        if elapsed < MIN_INTERVAL:
            return False
    state.update(
        schema_version="dhan-account-state-v1", last_attempt_at=now.isoformat()
    )
    _write_private_json(_state_path(name), state)
    return True


def save(name: str, status: int, raw: bytes, received_at: str) -> dict[str, Any]:
    """Retain exact HTTP body, including errors, before deriving any display state."""
    if name not in ENDPOINTS or not 100 <= status <= 599 or len(raw) > MAX_BODY:
        raise ValueError("account response outside bounded scope")
    received = datetime.fromisoformat(received_at)
    if received.tzinfo is None:
        raise ValueError("account receipt time must be timezone-aware")
    state = _read_state(name)
    if not state.get("last_attempt_at"):
        raise ValueError("account response has no reserved request")
    client_id = os.environ.get("DHAN_CLIENT_ID", "").strip()
    if not client_id:
        raise ValueError("Dhan client identity unavailable")
    identifier = uuid.uuid4().hex
    path = ROOT / f"{name}-{identifier}.response"
    digest = sha256(raw).hexdigest()
    _write_private_bytes(path, raw)
    _write_private_json(
        path.with_suffix(".meta.json"),
        {
            "schema_version": "dhan-account-response-v1",
            "method": "GET",
            "endpoint": f"https://api.dhan.co/v2/{name}",
            "http_status": status,
            "received_at": received_at,
            "sha256": digest,
            "byte_count": len(raw),
            "dhan_client_id": client_id,
        },
    )
    _sync_directory(ROOT)
    state["latest_response"] = path.name
    state["latest_status"] = status
    if status == 200:
        try:
            value = json.loads(raw)
            valid = (
                name == "fundlimit"
                and isinstance(value, dict)
                and value.get("dhanClientId") == client_id
            ) or (
                name != "fundlimit"
                and isinstance(value, list)
                and len(value) <= 10000
                and all(isinstance(row, dict) for row in value)
                and all(row.get("dhanClientId") in (None, client_id) for row in value)
            )
        except (UnicodeDecodeError, ValueError):
            valid = False
        if valid:
            state["latest_success"] = path.name
            state["reauthentication_required"] = False
            state.pop("blocked_token_mtime_ns", None)
        else:
            state["last_validation_failure_at"] = received_at
    if status == 401:
        state["reauthentication_required"] = True
        token_path = TOKEN_PATH
        if os.name == "nt":
            _verify_windows_private_acl(token_path)
        state["blocked_token_mtime_ns"] = token_path.stat().st_mtime_ns
    _write_private_json(_state_path(name), state)
    return {"http_status": status, "sha256": digest, "byte_count": len(raw)}


def record_failure(name: str, received_at: str) -> None:
    state = _read_state(name)
    if not state.get("last_attempt_at"):
        raise ValueError("account failure has no reserved request")
    if datetime.fromisoformat(received_at).tzinfo is None:
        raise ValueError("account failure time must be timezone-aware")
    state["last_transport_failure_at"] = received_at
    _write_private_json(_state_path(name), state)


def _verified(
    name: str, filename: str, client_id: str | None
) -> tuple[bytes, dict[str, Any]]:
    path = ROOT / filename
    if (
        path.parent != ROOT
        or not filename.startswith(f"{name}-")
        or path.suffix != ".response"
    ):
        raise ValueError("account response path mismatch")
    meta_path = path.with_suffix(".meta.json")
    if os.name == "nt":
        _verify_windows_private_acl(ROOT)
        _verify_windows_private_acl(path)
        _verify_windows_private_acl(meta_path)
    raw = path.read_bytes()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if (
        meta.get("schema_version") != "dhan-account-response-v1"
        or meta.get("method") != "GET"
        or meta.get("endpoint") != f"https://api.dhan.co/v2/{name}"
        or meta.get("sha256") != sha256(raw).hexdigest()
        or meta.get("byte_count") != len(raw)
        or not client_id
        or meta.get("dhan_client_id") != client_id
    ):
        raise ValueError("account response integrity mismatch")
    return raw, meta


def load(name: str, client_id: str | None = None) -> dict[str, Any]:
    state = _read_state(name)
    result: dict[str, Any] = {
        "endpoint": name,
        "status": "DATA_UNAVAILABLE",
        "last_attempt_at": state.get("last_attempt_at"),
        "last_http_status": state.get("latest_status"),
        "last_transport_failure_at": state.get("last_transport_failure_at"),
        "last_validation_failure_at": state.get("last_validation_failure_at"),
        "reauthentication_required": bool(state.get("reauthentication_required")),
        "blocked_token_mtime_ns": str(state["blocked_token_mtime_ns"])
        if "blocked_token_mtime_ns" in state
        else None,
    }
    filename = state.get("latest_success")
    if not filename:
        return result
    raw, meta = _verified(name, filename, client_id)
    if meta["http_status"] != 200:
        raise ValueError("account success status mismatch")
    data = json.loads(raw)
    if name == "fundlimit":
        if not client_id or data.get("dhanClientId") != client_id:
            raise ValueError("Dhan account identity unverified")
        balance = data.get("availabelBalance", data.get("availableBalance"))
        if (
            not isinstance(balance, (int, float))
            or isinstance(balance, bool)
            or balance < 0
        ):
            raise ValueError("Dhan balance unavailable")
        display: dict[str, Any] = {"available_balance_inr": str(balance)}
    else:
        if not isinstance(data, list) or len(data) > 10000:
            raise ValueError("Dhan account list invalid")
        if client_id and any(
            isinstance(row, dict) and row.get("dhanClientId") not in (None, client_id)
            for row in data
        ):
            raise ValueError("Dhan account identity mismatch")
        display = {"count": len(data)}
        if name in ("holdings", "positions"):
            display["items"] = [
                {
                    key: row[key]
                    for key in (
                        "tradingSymbol",
                        "securityId",
                        "totalQty",
                        "availableQty",
                        "netQty",
                        "productType",
                        "positionType",
                    )
                    if key in row
                }
                for row in data
                if isinstance(row, dict)
            ]
        elif name in ("orders", "trades"):
            display["items"] = [
                {
                    key: row[key]
                    for key in (
                        "tradingSymbol",
                        "securityId",
                        "transactionType",
                        "orderStatus",
                        "quantity",
                        "tradedQuantity",
                        "tradedPrice",
                        "createTime",
                        "updateTime",
                        "exchangeTime",
                    )
                    if key in row
                }
                for row in data
                if isinstance(row, dict)
            ]
    session_date = datetime.fromisoformat(meta["received_at"]).astimezone(IST).date()
    previous_session = (
        name in ("orders", "trades") and session_date != datetime.now(IST).date()
    )
    result.update(
        status="STALE_PREVIOUS_SESSION" if previous_session else "AVAILABLE",
        coverage_date=session_date.isoformat(),
        retrieved_at=meta["received_at"],
        response_sha256=meta["sha256"],
        source="DHAN_V2_ACCOUNT",
        data=display,
    )
    return result


def main() -> None:
    command, name = sys.argv[1:3]
    if command == "credential":
        path = TOKEN_PATH
        _private_directory(path.parent)
        if os.name == "nt":
            _verify_windows_private_acl(path)
        metadata_valid = False
        expires_at_utc = None
        meta_path = path.with_suffix(".meta.json")
        if meta_path.exists():
            if os.name == "nt":
                _verify_windows_private_acl(meta_path)
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            expiry = datetime.fromisoformat(meta.get("expires_at_utc", ""))
            metadata_valid = (
                meta.get("schema_version") == "dhan-consent-token-v1"
                and meta.get("source") == "https://auth.dhan.co/app/consumeApp-consent"
                and meta.get("token_sha256")
                == sha256(path.read_bytes().strip()).hexdigest()
                and expiry.tzinfo is not None
                and expiry > datetime.now(UTC) + timedelta(minutes=1)
            )
            if metadata_valid:
                expires_at_utc = expiry.isoformat()
        print(
            json.dumps(
                {
                    "private_token_present": path.is_file(),
                    "metadata_valid": metadata_valid,
                    "expires_at_utc": expires_at_utc,
                    "token_mtime_ns": str(path.stat().st_mtime_ns),
                }
            )
        )
    elif command == "reserve":
        print(json.dumps({"reserved": reserve(name)}))
    elif command == "save":
        payload = json.loads(sys.stdin.buffer.read(3 * MAX_BODY))
        raw = base64.b64decode(payload["body_base64"], validate=True)
        print(
            json.dumps(save(name, int(payload["status"]), raw, payload["received_at"]))
        )
    elif command == "failure":
        payload = json.loads(sys.stdin.buffer.read(1024))
        record_failure(name, payload["received_at"])
        print("{}")
    elif command == "load":
        print(json.dumps(load(name, os.environ.get("DHAN_CLIENT_ID"))))
    else:
        raise ValueError("unsupported account cache operation")


if __name__ == "__main__":
    main()
