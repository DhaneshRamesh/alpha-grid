"""Private, backend-only credential loader for an approved read-only feed."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from quant.acquisition.calendar import IST

ALLOWED_SCHEMAS: set[tuple[str, str]] = {
    ("dhan-consent-token-v1", "https://auth.dhan.co/app/consumeApp-consent"),
    ("dhan-web-manual-token-v1", "https://web.dhan.co/"),
    ("dhan-api-token-v1", "https://api.dhan.co/"),
    ("dhan-api-token-v1", "https://dhanhq.co/"),
    ("dhan-renewed-token-v1", "https://api.dhan.co/v2/RenewToken"),
}


def _write_private_atomic(path: Path, content: bytes, secret_dir: Path) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=secret_dir)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def save_credentials(
    root: Path,
    client: str,
    token: str,
    expiry: datetime,
    schema_version: str = "dhan-api-token-v1",
    source: str = "https://api.dhan.co/",
    token_type: str = "API",
    is_renewable: bool | None = None,
    expiry_evidence: str = "operator_entered",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Store credentials atomically in owner-private storage without leaking secrets."""
    secret_dir = root / "data/.secrets"
    secret_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (
        secret_dir.is_symlink()
        or secret_dir.stat().st_uid != os.getuid()
        or secret_dir.stat().st_mode & 0o077
    ):
        raise PermissionError("Dhan credential directory is not owner-private")

    client = client.strip()
    token = token.strip()
    if not re.fullmatch(r"[0-9]+", client) or not re.fullmatch(
        r"[A-Za-z0-9._\-+/=]+", token
    ):
        raise ValueError("Dhan Client ID or Access Token format invalid")

    if expiry.tzinfo is None:
        raise ValueError("Expiry must be timezone-aware")

    current_time = now.astimezone(UTC) if now is not None else datetime.now(UTC)
    if expiry.astimezone(UTC) <= current_time + timedelta(minutes=1):
        raise ValueError("Dhan token expiry must be in the future")
    if expiry.astimezone(UTC) > current_time + timedelta(days=366):
        raise ValueError("Dhan token expiry cannot exceed 366 days")

    if is_renewable is None:
        is_renewable = token_type in {"WEB", "RENEWED"}

    metadata: dict[str, Any] = {
        "schema_version": schema_version,
        "source": source,
        "token_type": token_type,
        "is_renewable": is_renewable,
        "expiry_evidence": expiry_evidence,
        "received_at_utc": current_time.isoformat(),
        "expires_at_utc": expiry.astimezone(UTC).isoformat(),
        "token_sha256": hashlib.sha256(token.encode("ascii")).hexdigest(),
        "client_id_sha256": hashlib.sha256(client.encode("ascii")).hexdigest(),
    }

    _write_private_atomic(
        secret_dir / "dhan-client-id", (client + "\n").encode("ascii"), secret_dir
    )
    _write_private_atomic(
        secret_dir / "dhan-access-token", (token + "\n").encode("ascii"), secret_dir
    )
    _write_private_atomic(
        secret_dir / "dhan-access-token.meta.json",
        (json.dumps(metadata, sort_keys=True) + "\n").encode(),
        secret_dir,
    )
    (secret_dir / "dhan-profile-check.json").unlink(missing_ok=True)

    masked_client = (
        client[:2] + "****" + client[-2:] if len(client) >= 4 else "****"
    )
    return {
        "schema_version": schema_version,
        "token_type": token_type,
        "is_renewable": is_renewable,
        "expires_at_utc": metadata["expires_at_utc"],
        "client_id_masked": masked_client,
    }


def _install_web_token(root: Path) -> None:
    """Install an operator-supplied Dhan Web token without network or echo."""
    client = getpass.getpass("Dhan Client ID: ").strip()
    token = getpass.getpass("Fresh Dhan Web Access Token: ").strip()
    expiry_text = input("Dhan Web token expiry in IST (YYYY-MM-DD HH:MM:SS): ").strip()
    try:
        expiry = datetime.strptime(expiry_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    except ValueError as exc:
        raise ValueError("Dhan Web expiry format invalid") from exc
    now = datetime.now(UTC)
    if (
        not now + timedelta(minutes=1)
        < expiry.astimezone(UTC)
        <= now + timedelta(hours=25)
    ):
        raise ValueError("Dhan Web token expiry must be within the next 25 hours")

    save_credentials(
        root=root,
        client=client,
        token=token,
        expiry=expiry,
        schema_version="dhan-web-manual-token-v1",
        source="https://web.dhan.co/",
        token_type="WEB",
        is_renewable=True,
        expiry_evidence="operator_entered",
    )
    print("Token stored locally; Dhan validity and Data API entitlement unverified.")


def install_token(
    root: Path,
    client: str | None = None,
    token: str = "",
    expiry: datetime | str | None = None,
    token_type: str = "API",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Programmatic or scripted entry to install a Dhan API or Web token."""
    token_type_upper = token_type.strip().upper()
    if token_type_upper not in {"API", "WEB"}:
        raise ValueError("token_type must be 'API' or 'WEB'")

    if not client:
        client_file = root / "data/.secrets/dhan-client-id"
        if client_file.is_file():
            try:
                client = _private_bytes(client_file).decode("ascii").strip()
            except (OSError, PermissionError):
                pass
        if not client and (root / ".env").is_file():
            try:
                for line in _private_bytes(root / ".env").decode("utf-8").splitlines():
                    if line.strip().startswith("DHAN_CLIENT_ID="):
                        client = line.partition("=")[2].strip().strip("\"'")
            except OSError:
                pass
    if not client:
        raise ValueError("Dhan Client ID required")

    current_time = now.astimezone(UTC) if now is not None else datetime.now(UTC)
    if expiry is None:
        if token_type_upper == "WEB":
            parsed_expiry = current_time + timedelta(hours=24)
        else:
            parsed_expiry = current_time + timedelta(days=30)
    elif isinstance(expiry, datetime):
        parsed_expiry = expiry if expiry.tzinfo is not None else expiry.replace(tzinfo=IST)
    else:
        clean = expiry.strip().replace("T", " ").partition(".")[0]
        try:
            parsed_expiry = datetime.strptime(clean, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
        except ValueError:
            parsed_expiry = datetime.strptime(clean, "%d/%m/%Y %H:%M").replace(tzinfo=IST)

    schema_version = (
        "dhan-web-manual-token-v1" if token_type_upper == "WEB" else "dhan-api-token-v1"
    )
    source = "https://web.dhan.co/" if token_type_upper == "WEB" else "https://api.dhan.co/"
    saved = save_credentials(
        root=root,
        client=client,
        token=token,
        expiry=parsed_expiry,
        schema_version=schema_version,
        source=source,
        token_type=token_type_upper,
        is_renewable=(token_type_upper == "WEB"),
        expiry_evidence="operator_entered",
        now=current_time,
    )
    loaded_token, loaded_client, _ = load_credentials(root, now=current_time)
    if loaded_token != token.strip() or loaded_client != client.strip():
        raise ValueError("Saved credential failed shared loader verification")
    return saved


def get_token_status(
    root: Path, now: datetime | None = None, private_dir: Path | None = None
) -> tuple[str, dict[str, Any]]:
    """Return status only: VALID, EXPIRING, EXPIRED, AUTH REQUIRED. Never expose secrets."""
    now = now or datetime.now(UTC)
    secret_dir = root / "data/.secrets"
    token_file = secret_dir / "dhan-access-token"
    meta_file = secret_dir / "dhan-access-token.meta.json"
    client_file = secret_dir / "dhan-client-id"

    base_info: dict[str, Any] = {
        "schema_version": None,
        "token_type": None,
        "is_renewable": False,
        "expires_at_utc": None,
        "expires_at_ist": None,
        "automatic_renewal_enabled": False,
    }

    if (
        not secret_dir.is_dir()
        or secret_dir.is_symlink()
        or (secret_dir.stat().st_mode & 0o077)
    ):
        return "AUTH REQUIRED", {**base_info, "reason": "SECRET_DIRECTORY_UNAVAILABLE"}

    if not token_file.is_file() or not meta_file.is_file():
        return "AUTH REQUIRED", {**base_info, "reason": "TOKEN_FILES_MISSING"}

    try:
        token_bytes = _private_bytes(token_file).strip()
        meta = json.loads(_private_bytes(meta_file))
        expiry = datetime.fromisoformat(meta["expires_at_utc"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError, PermissionError):
        return "AUTH REQUIRED", {**base_info, "reason": "METADATA_UNREADABLE"}

    token = token_bytes.decode("ascii", errors="replace")
    if not re.fullmatch(r"[A-Za-z0-9._\-+/=]+", token):
        return "AUTH REQUIRED", {**base_info, "reason": "TOKEN_FORMAT_INVALID"}

    if (
        (meta.get("schema_version"), meta.get("source")) not in ALLOWED_SCHEMAS
        or meta.get("token_sha256") != hashlib.sha256(token_bytes).hexdigest()
        or expiry.tzinfo is None
    ):
        return "AUTH REQUIRED", {**base_info, "reason": "TOKEN_METADATA_INVALID"}

    client = None
    if client_file.is_file():
        try:
            client = _private_bytes(client_file).decode("ascii").strip()
        except (OSError, PermissionError):
            pass
    elif (root / ".env").is_file():
        try:
            for line in _private_bytes(root / ".env").decode("utf-8").splitlines():
                if line.strip().startswith("DHAN_CLIENT_ID="):
                    client = line.partition("=")[2].strip().strip("\"'")
        except OSError:
            pass

    if not client or not re.fullmatch(r"[0-9]+", client):
        return "AUTH REQUIRED", {**base_info, "reason": "CLIENT_ID_INVALID"}

    if (
        meta.get("client_id_sha256") is not None
        and meta.get("client_id_sha256")
        != hashlib.sha256(client.encode("ascii")).hexdigest()
    ):
        return "AUTH REQUIRED", {**base_info, "reason": "CLIENT_ID_MISMATCH"}

    token_type = meta.get("token_type") or (
        "WEB"
        if meta.get("schema_version")
        in {"dhan-web-manual-token-v1", "dhan-renewed-token-v1"}
        else "CONSENT"
        if meta.get("schema_version") == "dhan-consent-token-v1"
        else "API"
    )
    is_renewable = bool(meta.get("is_renewable", token_type in {"WEB", "RENEWED"}))

    masked_client = (
        client[:2] + "****" + client[-2:] if len(client) >= 4 else "****"
    )
    info = {
        "schema_version": meta.get("schema_version"),
        "source": meta.get("source"),
        "token_type": token_type,
        "is_renewable": is_renewable,
        "expires_at_utc": expiry.astimezone(UTC).isoformat(),
        "expires_at_ist": expiry.astimezone(IST).isoformat(),
        "client_id_masked": masked_client,
        "automatic_renewal_enabled": False,
    }

    if expiry <= now + timedelta(minutes=1):
        return "EXPIRED", info

    if expiry <= now + timedelta(hours=2):
        return "EXPIRING", info

    latest_check: dict[str, Any] | None = None
    target_dirs = [secret_dir]
    if private_dir and private_dir != secret_dir:
        target_dirs.append(private_dir)
    for directory in target_dirs:
        profile_check_file = directory / "dhan-profile-check.json"
        if profile_check_file.is_file():
            try:
                check_data = json.loads(_private_bytes(profile_check_file))
                if check_data.get("token_sha256") == hashlib.sha256(token_bytes).hexdigest():
                    if latest_check is None or str(check_data.get("checked_at", "")) > str(latest_check.get("checked_at", "")):
                        latest_check = check_data
            except (OSError, ValueError, KeyError, json.JSONDecodeError, PermissionError):
                pass

    if latest_check and latest_check.get("result") in {"PROFILE_HTTP_401", "AUTHENTICATION_FAILED"}:
        return "AUTH REQUIRED", {**info, "reason": latest_check.get("result")}

    return "VALID", info


def renew_token(
    root: Path,
    opener: Callable[..., Any] | None = None,
    now: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    """Renew an active Dhan Web token via https://api.dhan.co/v2/RenewToken."""
    now = now or datetime.now(UTC)
    status, info = get_token_status(root, now)
    if status == "AUTH REQUIRED":
        return "AUTH REQUIRED", {"error": info.get("reason", "AUTH_REQUIRED")}

    if not info.get("is_renewable"):
        return "TOKEN_RENEWAL_NOT_SUPPORTED", {
            "error": "TOKEN_NOT_RENEWABLE",
            "token_type": info.get("token_type"),
        }

    if status == "EXPIRED":
        return "AUTH REQUIRED", {"error": "CANNOT_RENEW_EXPIRED_TOKEN"}

    token, client, _ = load_credentials(root, now)

    request = Request(
        "https://api.dhan.co/v2/RenewToken",
        headers={
            "access-token": token,
            "dhanClientId": client,
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with (opener or build_opener(_NoRedirect).open)(request, timeout=8) as response:
            if response.status != 200:
                return "AUTH REQUIRED", {"error": f"RENEWAL_HTTP_{response.status}"}
            raw = response.read(16_385)
    except HTTPError as exc:
        return "AUTH REQUIRED", {"error": f"RENEWAL_HTTP_{exc.code}"}
    except (OSError, URLError):
        return "RENEWAL_NETWORK_ERROR", {"error": "NETWORK_UNAVAILABLE"}

    if len(raw) > 16_384:
        return "AUTH REQUIRED", {"error": "RENEWAL_RESPONSE_TOO_LARGE"}

    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return "AUTH REQUIRED", {"error": "RENEWAL_SCHEMA_INVALID"}
        if str(payload.get("dhanClientId")) != client:
            return "AUTH REQUIRED", {"error": "RENEWAL_CLIENT_ID_MISMATCH"}

        new_token = str(payload.get("accessToken") or payload.get("token") or "")
        if not re.fullmatch(r"[A-Za-z0-9._\-+/=]+", new_token):
            return "AUTH REQUIRED", {"error": "RENEWAL_TOKEN_FORMAT_INVALID"}

        expiry_str = payload.get("expiryTime") or payload.get("tokenValidity")
        if not expiry_str:
            return "AUTH REQUIRED", {"error": "RENEWAL_EXPIRY_MISSING"}

        expiry_clean = str(expiry_str).replace("T", " ").partition(".")[0]
        try:
            new_expiry = datetime.strptime(
                expiry_clean, "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=IST)
        except ValueError:
            try:
                new_expiry = datetime.strptime(
                    expiry_clean, "%d/%m/%Y %H:%M"
                ).replace(tzinfo=IST)
            except ValueError:
                return "AUTH REQUIRED", {"error": "RENEWAL_EXPIRY_PARSE_FAILED"}

        if new_expiry.astimezone(UTC) <= now + timedelta(minutes=1):
            return "AUTH REQUIRED", {"error": "RENEWAL_RETURNED_EXPIRED_TIMESTAMP"}

    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return "AUTH REQUIRED", {"error": "RENEWAL_RESPONSE_UNVERIFIED"}

    save_credentials(
        root=root,
        client=client,
        token=new_token,
        expiry=new_expiry,
        schema_version="dhan-renewed-token-v1",
        source="https://api.dhan.co/v2/RenewToken",
        token_type="RENEWED",
        is_renewable=True,
        expiry_evidence="dhan_renew_response",
        now=now,
    )
    new_status, new_info = get_token_status(root, now)
    return "VALID", new_info


def pre_expiry_renewal_policy(
    root: Path,
    allow_automatic: bool = False,
    opener: Callable[..., Any] | None = None,
    now: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    """Controlled renewal policy: automatic renewal remains disabled unless explicitly approved."""
    now = now or datetime.now(UTC)
    operator_approved = (
        allow_automatic
        or os.environ.get(
            "ALPHA_GRID_ALLOW_AUTOMATIC_TOKEN_RENEWAL", ""
        ).strip().lower()
        in {"1", "true"}
    )
    if not operator_approved:
        status, info = get_token_status(root, now)
        return "AUTOMATIC_RENEWAL_DISABLED", {
            **info,
            "policy": "PRE_EXPIRY_DISABLED_PENDING_OPERATOR_APPROVAL",
        }

    status, info = get_token_status(root, now)
    if status == "EXPIRING" and info.get("is_renewable"):
        return renew_token(root, opener=opener, now=now)
    return status, info


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


def record_profile_check(
    root: Path,
    result: str,
    private_dir: Path | None = None,
    now: datetime | None = None,
) -> None:
    """Record profile check status atomically in private storage without leaking credentials."""
    try:
        token, _client, _expiry = load_credentials(root)
    except (OSError, ValueError, KeyError, TypeError):
        return
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    current_time = now or datetime.now(UTC)
    record = {
        "schema_version": "alpha-grid-dhan-profile-check-v1",
        "token_sha256": token_hash,
        "result": result,
        "checked_at": current_time.isoformat(),
    }
    content = (json.dumps(record, sort_keys=True) + "\n").encode()
    targets = {root / "data/.secrets"}
    if private_dir:
        targets.add(private_dir)
    for target in targets:
        if not target.is_dir() or target.is_symlink() or (target.stat().st_mode & 0o077):
            continue
        status_path = target / "dhan-profile-check.json"
        if status_path.is_symlink() or (status_path.exists() and status_path.stat().st_mode & 0o077):
            continue
        try:
            _write_private_atomic(status_path, content, target)
        except OSError:
            pass


def _check_profile(
    root: Path, private: Path, opener: Callable[..., Any] | None = None
) -> str:
    """One operator-triggered, read-only Dhan profile check; return status only."""
    try:
        token, client, _expiry = load_credentials(root, None, False)
    except (OSError, ValueError, KeyError, TypeError):
        return "AUTH_REQUIRED"
    request = Request(
        "https://api.dhan.co/v2/profile",
        headers={"access-token": token, "Accept": "application/json"},
        method="GET",
    )
    result = "PROFILE_RESPONSE_UNVERIFIED"
    try:
        with (opener or build_opener(_NoRedirect).open)(request, timeout=8) as response:
            if response.status != 200:
                result = f"PROFILE_HTTP_{response.status}"
            else:
                raw = response.read(16_385)
                if len(raw) > 16_384:
                    result = "PROFILE_RESPONSE_TOO_LARGE"
                else:
                    try:
                        profile = json.loads(raw)
                        if not isinstance(profile, dict) or str(profile.get("dhanClientId")) != client:
                            result = "PROFILE_CLIENT_ID_MISMATCH"
                        elif profile.get("dataPlan") != "Active":
                            result = "DATA_API_NOT_ACTIVE"
                        else:
                            token_validity = datetime.strptime(
                                profile["tokenValidity"], "%d/%m/%Y %H:%M"
                            ).replace(tzinfo=IST)
                            data_validity = datetime.strptime(
                                profile["dataValidity"], "%Y-%m-%d %H:%M:%S.%f"
                            ).replace(tzinfo=IST)
                            if token_validity <= datetime.now(IST) or data_validity <= datetime.now(IST):
                                result = "PROFILE_VALIDITY_EXPIRED"
                            else:
                                result = "DHAN_TOKEN_AND_DATA_PLAN_ACTIVE"
                                try:
                                    secret_dir = root / "data/.secrets"
                                    meta_path = secret_dir / "dhan-access-token.meta.json"
                                    if meta_path.is_file():
                                        meta_data = json.loads(_private_bytes(meta_path))
                                        meta_expiry = datetime.fromisoformat(meta_data["expires_at_utc"])
                                        token_utc = token_validity.astimezone(UTC)
                                        if token_utc > meta_expiry or meta_expiry <= datetime.now(UTC):
                                            meta_data["expires_at_utc"] = token_utc.isoformat()
                                            meta_data["expiry_evidence"] = "dhan_v2_profile"
                                            _write_private_atomic(
                                                meta_path,
                                                (json.dumps(meta_data, sort_keys=True) + "\n").encode(),
                                                secret_dir,
                                            )
                                except Exception:
                                    pass
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                        result = "PROFILE_RESPONSE_UNVERIFIED"
    except HTTPError as exc:
        result = f"PROFILE_HTTP_{exc.code}"
    except (OSError, URLError):
        result = "PROFILE_NETWORK_ERROR"

    record_profile_check(root, result, private)
    return result


def _private_bytes(path: Path, limit: int = 16384) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_size > limit
        ):
            raise PermissionError("Dhan credential file is not owner-private")
        return os.read(fd, limit + 1)
    finally:
        os.close(fd)


def load_credentials(
    root: Path, now: datetime | None = None, check_expiry: bool = True
) -> tuple[str, str, datetime]:
    """Return credentials only to a backend process; never print either value."""
    now = now or datetime.now(UTC)
    secret_dir = root / "data/.secrets"
    if secret_dir.is_symlink() or secret_dir.stat().st_mode & 0o077:
        raise PermissionError("Dhan credential directory is not owner-private")
    token_bytes = _private_bytes(secret_dir / "dhan-access-token").strip()
    meta = json.loads(_private_bytes(secret_dir / "dhan-access-token.meta.json"))
    expiry = datetime.fromisoformat(meta["expires_at_utc"])
    if (
        (meta.get("schema_version"), meta.get("source")) not in ALLOWED_SCHEMAS
        or meta.get("token_sha256") != hashlib.sha256(token_bytes).hexdigest()
        or expiry.tzinfo is None
        or (check_expiry and expiry <= now + timedelta(minutes=1))
    ):
        raise ValueError(
            "Dhan access token expired or metadata invalid; authentication required"
        )
    token = token_bytes.decode("ascii")
    if not re.fullmatch(r"[A-Za-z0-9._\-+/=]+", token):
        raise ValueError("Dhan access token format invalid")
    client_path = secret_dir / "dhan-client-id"
    if client_path.is_file():
        client = _private_bytes(client_path).decode("ascii").strip()
    else:
        # The existing Mac OAuth setup stores its client ID in an owner-private .env.
        try:
            lines = _private_bytes(root / ".env").decode("utf-8").splitlines()
        except OSError as exc:
            raise ValueError("Dhan client identity unavailable") from exc
        values = [
            line.partition("=")[2].strip().strip("\"'")
            for line in lines
            if line.strip().startswith("DHAN_CLIENT_ID=")
        ]
        if len(values) != 1:
            raise ValueError("Dhan client identity unavailable")
        client = values[0]
    if not re.fullmatch(r"[0-9]+", client):
        raise ValueError("Dhan client identity invalid")
    if (
        meta.get("client_id_sha256") is not None
        and meta.get("client_id_sha256")
        != hashlib.sha256(client.encode("ascii")).hexdigest()
    ):
        raise ValueError(
            "Dhan client identity metadata invalid; authentication required"
        )
    return token, client, expiry


def main() -> int:
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--private-dir", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--gateway", type=Path)
    mode.add_argument("--depth-binary", type=Path)
    mode.add_argument("--install-web-token", action="store_true")
    mode.add_argument("--install-token", action="store_true")
    mode.add_argument("--check-local-token", action="store_true")
    mode.add_argument("--token-status", action="store_true")
    mode.add_argument("--renew-token", action="store_true")
    mode.add_argument("--pre-expiry-check", action="store_true")
    mode.add_argument("--check-profile", action="store_true")

    # Optional parameters for token installation and renewal
    parser.add_argument("--client-id", type=str)
    parser.add_argument("--access-token", type=str)
    parser.add_argument("--expiry-ist", type=str)
    parser.add_argument("--token-type", choices=["API", "WEB"], default="API")
    parser.add_argument("--stdin", action="store_true")
    parser.add_argument("--allow-automatic-renewal", action="store_true")

    args = parser.parse_args()
    if args.install_web_token:
        _install_web_token(args.root)
        return 0

    if args.install_token:
        client = args.client_id
        token = args.access_token
        expiry_ist = args.expiry_ist
        token_type = args.token_type

        if args.stdin:
            raw_input = sys.stdin.read(8192)
            try:
                data = json.loads(raw_input)
                client = data.get("clientId") or data.get("client_id") or client
                token = data.get("accessToken") or data.get("access_token") or token
                expiry_ist = data.get("expiryTime") or data.get("expiry_ist") or expiry_ist
                token_type = data.get("tokenType") or data.get("token_type") or token_type
            except json.JSONDecodeError as exc:
                print(json.dumps({"status": "AUTH REQUIRED", "error": f"STDIN_JSON_INVALID: {exc}"}))
                return 1

        if not client and not args.stdin:
            client = getpass.getpass("Dhan Client ID: ").strip()
        if not token and not args.stdin:
            token = getpass.getpass("Dhan Access Token: ").strip()

        try:
            result = install_token(
                root=args.root,
                client=client or None,
                token=token or "",
                expiry=expiry_ist,
                token_type=token_type,
            )
            print(json.dumps({"status": "VALID", **result}))
            return 0
        except Exception as exc:
            print(json.dumps({"status": "AUTH REQUIRED", "error": str(exc)}))
            return 1

    if args.check_local_token:
        load_credentials(args.root)
        print("LOCAL_TOKEN_METADATA_VALID; remote validity and entitlement unverified")
        return 0

    if args.token_status:
        status, info = get_token_status(args.root, private_dir=args.private_dir)
        print(json.dumps({"status": status, **info}, sort_keys=True))
        return 0 if status == "VALID" else 1

    if args.renew_token:
        status, info = renew_token(args.root)
        print(json.dumps({"status": status, **info}, sort_keys=True))
        return 0 if status in {"VALID", "RENEWED"} else 1

    if args.pre_expiry_check:
        status, info = pre_expiry_renewal_policy(
            args.root, allow_automatic=args.allow_automatic_renewal
        )
        print(json.dumps({"status": status, **info}, sort_keys=True))
        return 0
    if args.check_profile:
        result = _check_profile(args.root, args.private_dir)
        if result == "DHAN_TOKEN_AND_DATA_PLAN_ACTIVE":
            from quant.apps.live_feed_scope import ensure_local_read_only_scope

            ensure_local_read_only_scope(args.root, args.private_dir)
        print(result)
        return 0 if result == "DHAN_TOKEN_AND_DATA_PLAN_ACTIVE" else 1
    from quant.apps.live_feed_scope import approved_plan

    (
        ids,
        journal,
        nse,
        nse_manifest,
        master,
        master_manifest,
        calendar,
        universe,
        standard_feed,
        depth_enabled,
        capture_mode,
        expiry,
    ) = approved_plan(args.root, args.private_dir)
    token, client, _token_expiry = load_credentials(args.root)
    env = os.environ.copy()
    env.update(
        DHAN_ACCESS_TOKEN=token, DHAN_CLIENT_ID=client, ALPHA_GRID_ENABLE_DHAN_QUOTE="1"
    )
    env.pop("DHAN_API_KEY", None)
    env.pop("DHAN_API_SECRET", None)
    if args.depth_binary:
        if depth_enabled != "1":
            raise ValueError("20-level depth is outside approved scope")
        private = args.private_dir
        session = datetime.now(IST).date().isoformat()
        journal_dir = private / f"depth-{session}"
        journal_dir.mkdir(mode=0o700, exist_ok=True)
        if journal_dir.is_symlink() or journal_dir.stat().st_mode & 0o077:
            raise PermissionError("private depth journal directory invalid")
        subscriptions = private / f"depth-subscriptions-{session}.json"
        fd, name = tempfile.mkstemp(prefix="depth-subscriptions-", dir=private)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    [
                        {"ExchangeSegment": "NSE_EQ", "SecurityId": item}
                        for item in ids.split(",")
                    ],
                    stream,
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, subscriptions)
        finally:
            Path(name).unlink(missing_ok=True)
        env["QUANT_ENABLE_LIVE_READONLY"] = "1"
        env["ALPHA_GRID_DEPTH_STATUS_FILE"] = str(private / "depth-status.json")
        env["ALPHA_GRID_DEPTH_BOOKS_FILE"] = str(private / "depth-books.json")
        argv = [
            str(args.depth_binary),
            "live-readonly",
            str(journal_dir),
            str(subscriptions),
            str(int(expiry) * 1_000_000_000),
            str(private / "gateway-stop-requested"),
        ]
        os.execve(str(args.depth_binary), argv, env)
    argv = [
        str(args.gateway),
        "--security-ids",
        ids,
        "--universe",
        universe,
        "--dhan-quote-readonly",
        "--online",
        "--quote-journal",
        journal,
        "--instrument-master",
        master,
        "--master-manifest",
        master_manifest,
        "--calendar",
        calendar,
        "--nse-security",
        nse,
        "--nse-manifest",
        nse_manifest,
        "--approval-ledger",
        str(args.private_dir / "paper-approvals.qp"),
        "--depth-books",
        str(args.private_dir / "depth-books.json"),
        "--stop-file",
        str(args.private_dir / "gateway-stop-requested"),
    ]
    if capture_mode in {"RAW_CAPTURE_ONLY", "RAW_CAPTURE_WITH_ELIGIBLE_PROJECTION"}:
        argv.append("--raw-capture-only")
        if capture_mode == "RAW_CAPTURE_WITH_ELIGIBLE_PROJECTION":
            argv.append("--eligible-projection")
        env["ALPHA_GRID_CAPTURE_EVENT_LOG"] = str(
            args.private_dir
            / f"raw-capture-events-{datetime.now(IST).date().isoformat()}.jsonl"
        )
        env["ALPHA_GRID_CAPTURE_STATUS_FILE"] = str(
            args.private_dir / "raw-capture-status.json"
        )
    if standard_feed == "STANDARD_FULL":
        argv.append("--standard-full")
    os.execve(str(args.gateway), argv, env)


if __name__ == "__main__":
    raise SystemExit(main())
