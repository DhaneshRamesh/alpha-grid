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


def _install_web_token(root: Path) -> None:
    """Install an operator-supplied Dhan Web token without network or echo."""
    secret_dir = root / "data/.secrets"
    secret_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (
        secret_dir.is_symlink()
        or secret_dir.stat().st_uid != os.getuid()
        or secret_dir.stat().st_mode & 0o077
    ):
        raise PermissionError("Dhan credential directory is not owner-private")
    client = getpass.getpass("Dhan Client ID: ").strip()
    token = getpass.getpass("Fresh Dhan Web Access Token: ").strip()
    expiry_text = input("Dhan Web token expiry in IST (YYYY-MM-DD HH:MM:SS): ").strip()
    if not re.fullmatch(r"[0-9]+", client) or not re.fullmatch(
        r"[A-Za-z0-9._-]+", token
    ):
        raise ValueError("Dhan Client ID or Access Token format invalid")
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

    def write_private(path: Path, content: bytes) -> None:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=secret_dir)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)

    metadata = {
        "schema_version": "dhan-web-manual-token-v1",
        "source": "https://web.dhan.co/",
        "expiry_evidence": "operator_entered",
        "received_at_utc": now.isoformat(),
        "expires_at_utc": expiry.astimezone(UTC).isoformat(),
        "token_sha256": hashlib.sha256(token.encode("ascii")).hexdigest(),
        "client_id_sha256": hashlib.sha256(client.encode("ascii")).hexdigest(),
    }
    write_private(secret_dir / "dhan-client-id", (client + "\n").encode("ascii"))
    write_private(secret_dir / "dhan-access-token", (token + "\n").encode("ascii"))
    write_private(
        secret_dir / "dhan-access-token.meta.json",
        (json.dumps(metadata, sort_keys=True) + "\n").encode(),
    )
    print("Token stored locally; Dhan validity and Data API entitlement unverified.")


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


def _check_profile(
    root: Path, private: Path, opener: Callable[..., Any] | None = None
) -> str:
    """One operator-triggered, read-only Dhan profile check; return status only."""
    try:
        token, client, _expiry = load_credentials(root)
    except (OSError, ValueError, KeyError, TypeError):
        return "AUTH_REQUIRED"
    request = Request(
        "https://api.dhan.co/v2/profile",
        headers={"access-token": token, "Accept": "application/json"},
        method="GET",
    )
    try:
        with (opener or build_opener(_NoRedirect).open)(request, timeout=8) as response:
            if response.status != 200:
                return f"PROFILE_HTTP_{response.status}"
            raw = response.read(16_385)
    except HTTPError as exc:
        return f"PROFILE_HTTP_{exc.code}"
    except (OSError, URLError):
        return "PROFILE_NETWORK_ERROR"
    if len(raw) > 16_384:
        return "PROFILE_RESPONSE_TOO_LARGE"
    try:
        profile = json.loads(raw)
        if not isinstance(profile, dict) or str(profile.get("dhanClientId")) != client:
            return "PROFILE_CLIENT_ID_MISMATCH"
        if profile.get("dataPlan") != "Active":
            return "DATA_API_NOT_ACTIVE"
        token_validity = datetime.strptime(
            profile["tokenValidity"], "%d/%m/%Y %H:%M"
        ).replace(tzinfo=IST)
        data_validity = datetime.strptime(
            profile["dataValidity"], "%Y-%m-%d %H:%M:%S.%f"
        ).replace(tzinfo=IST)
        if token_validity <= datetime.now(IST) or data_validity <= datetime.now(IST):
            return "PROFILE_VALIDITY_EXPIRED"
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return "PROFILE_RESPONSE_UNVERIFIED"
    return "DHAN_TOKEN_AND_DATA_PLAN_ACTIVE"


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
    root: Path, now: datetime | None = None
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
        (meta.get("schema_version"), meta.get("source"))
        not in {
            ("dhan-consent-token-v1", "https://auth.dhan.co/app/consumeApp-consent"),
            ("dhan-web-manual-token-v1", "https://web.dhan.co/"),
        }
        or meta.get("token_sha256") != hashlib.sha256(token_bytes).hexdigest()
        or expiry.tzinfo is None
        or expiry <= now + timedelta(minutes=1)
    ):
        raise ValueError(
            "Dhan access token expired or metadata invalid; authentication required"
        )
    token = token_bytes.decode("ascii")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", token):
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
        meta.get("schema_version") == "dhan-web-manual-token-v1"
        and meta.get("client_id_sha256")
        != hashlib.sha256(client.encode("ascii")).hexdigest()
    ):
        raise ValueError(
            "Dhan client identity metadata invalid; authentication required"
        )
    return token, client, expiry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--private-dir", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--gateway", type=Path)
    mode.add_argument("--depth-binary", type=Path)
    mode.add_argument("--install-web-token", action="store_true")
    mode.add_argument("--check-local-token", action="store_true")
    mode.add_argument("--check-profile", action="store_true")
    args = parser.parse_args()
    if args.install_web_token:
        _install_web_token(args.root)
        return 0
    if args.check_local_token:
        load_credentials(args.root)
        print("LOCAL_TOKEN_METADATA_VALID; remote validity and entitlement unverified")
        return 0
    if args.check_profile:
        result = _check_profile(args.root, args.private_dir)
        try:
            token, _client, _expiry = load_credentials(args.root)
            status_path = args.private_dir / "dhan-profile-check.json"
            if status_path.is_symlink() or (status_path.exists() and status_path.stat().st_mode & 0o077):
                raise PermissionError("private Dhan profile status invalid")
            record = {"schema_version": "alpha-grid-dhan-profile-check-v1",
                      "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                      "result": result, "checked_at": datetime.now(UTC).isoformat()}
            fd = os.open(status_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, ValueError, KeyError, TypeError):
            pass
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
