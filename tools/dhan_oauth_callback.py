"""Local Dhan API-key callback and untrusted order-postback receiver.

The only outbound request is the documented consume-consent GET after a redirect.
There is no order transport or connection to Alpha Grid's paper ledgers.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import uuid
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from quant.acquisition.dhan import _private_directory, _verify_windows_private_acl

HOST = "127.0.0.1"
PORT = 8766  # 8765 belongs to the existing Rust telemetry gateway.
CALLBACK_PATH = "/dhan/callback"
POSTBACK_PATH = "/dhan/postback"
TOKEN_FILE = Path("data/.secrets/dhan-access-token")
CLIENT_ID_FILE = Path("data/.secrets/dhan-client-id")
API_KEY_FILE = Path("data/.secrets/dhan-api-key")
API_SECRET_FILE = Path("data/.secrets/dhan-api-secret")
POSTBACK_DIR = Path("data/.secrets/dhan-postbacks")
MAX_POSTBACK_BYTES = 64 * 1024
MAX_AUTH_RESPONSE_BYTES = 16 * 1024
ORDER_STATUSES = frozenset(
    {"TRANSIT", "PENDING", "REJECTED", "CANCELLED", "TRADED", "EXPIRED"}
)
CONSUME_URL = "https://auth.dhan.co/app/consumeApp-consent"
GENERATE_URL = "https://auth.dhan.co/app/generate-consent"
LOGIN_URL = "https://auth.dhan.co/login/consentApp-login"
IST = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class ConsentToken:
    access_token: str
    expiry_time_ist: str
    expires_at_utc: str


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
        # Never forward API key/secret headers to a redirect destination.
        return None


def _strict_json(raw: bytes) -> object:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise ValueError("non-JSON number")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)


def endpoint_urls(environment: dict[str, str], port: int = PORT) -> tuple[str, str]:
    """Return configured HTTPS URLs, or local test URLs when no host exists."""
    public = environment.get("ALPHA_GRID_PUBLIC_BASE_URL", "").strip()
    if public:
        parsed = urlsplit(public)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("public base URL must be a public HTTPS origin")
        if "." not in parsed.hostname or parsed.hostname.endswith(
            (".local", ".internal")
        ):
            raise ValueError("public base URL requires a public DNS hostname")
        try:
            ipaddress.ip_address(parsed.hostname)
        except ValueError:
            pass
        else:
            raise ValueError("public base URL requires a DNS hostname")
        base = public.rstrip("/")
    else:
        base = f"http://{HOST}:{port}"
    return base + CALLBACK_PATH, base + POSTBACK_PATH


def _private_write(path: Path, content: bytes, *, replace: bool) -> None:
    _private_directory(path.parent)
    target = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp") if replace else path
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(target, path)
        if os.name == "nt":
            _verify_windows_private_acl(path)
    finally:
        if replace:
            target.unlink(missing_ok=True)


class DhanAuthClient:
    def __init__(
        self,
        client_id: str,
        api_key: str,
        api_secret: str,
        *,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        if not all((client_id, api_key, api_secret)):
            raise ValueError(
                "DHAN_CLIENT_ID, DHAN_API_KEY and DHAN_API_SECRET required"
            )
        self.client_id = client_id
        self._api_key = api_key
        self._api_secret = api_secret
        self._opener = opener or build_opener(_NoRedirect).open

    @classmethod
    def from_environment(cls) -> DhanAuthClient:
        return cls(
            os.environ.get("DHAN_CLIENT_ID", ""),
            os.environ.get("DHAN_API_KEY", ""),
            os.environ.get("DHAN_API_SECRET", ""),
        )

    @classmethod
    def from_private_store(cls) -> DhanAuthClient:
        _private_directory(CLIENT_ID_FILE.parent)
        values: list[str] = []
        for path in (CLIENT_ID_FILE, API_KEY_FILE, API_SECRET_FILE):
            if os.name == "nt":
                _verify_windows_private_acl(path)
            values.append(path.read_text(encoding="ascii").strip())
        return cls(*values)

    def generate_consent(self) -> str:
        request = Request(
            f"{GENERATE_URL}?client_id={quote(self.client_id, safe='')}",
            data=b"",
            headers={"app_id": self._api_key, "app_secret": self._api_secret},
            method="POST",
        )
        with self._opener(request, timeout=10) as response:
            raw = response.read(MAX_AUTH_RESPONSE_BYTES + 1)
        if len(raw) > MAX_AUTH_RESPONSE_BYTES:
            raise ValueError("Dhan consent response too large")
        payload = _strict_json(raw)
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise ValueError("Dhan consent response invalid")
        consent_id = payload.get("consentAppId")
        if not isinstance(consent_id, str) or len(consent_id) > 128:
            raise ValueError("Dhan consent identifier invalid")
        try:
            uuid.UUID(consent_id)
        except ValueError as exc:
            raise ValueError("Dhan consent identifier invalid") from exc
        return consent_id

    def consume_consent(self, token_id: str) -> ConsentToken:
        request = Request(
            f"{CONSUME_URL}?tokenId={quote(token_id, safe='')}",
            headers={"app_id": self._api_key, "app_secret": self._api_secret},
            method="GET",
        )
        with self._opener(request, timeout=10) as response:
            raw = response.read(MAX_AUTH_RESPONSE_BYTES + 1)
        if len(raw) > MAX_AUTH_RESPONSE_BYTES:
            raise ValueError("Dhan auth response too large")
        payload = _strict_json(raw)
        if (
            not isinstance(payload, dict)
            or payload.get("dhanClientId") != self.client_id
        ):
            raise ValueError("Dhan auth client identity mismatch")
        access_token = payload.get("accessToken")
        if (
            not isinstance(access_token, str)
            or not 1 <= len(access_token) <= 8192
            or any(char.isspace() for char in access_token)
        ):
            raise ValueError("Dhan auth response lacks a valid access token")
        expiry = payload.get("expiryTime")
        if not isinstance(expiry, str) or "T" not in expiry:
            raise ValueError("Dhan auth response lacks expiryTime")
        try:
            parsed = datetime.fromisoformat(expiry)
        except ValueError as exc:
            raise ValueError("Dhan auth expiryTime invalid") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)
        if parsed <= datetime.now(UTC):
            raise ValueError("Dhan auth token already expired")
        return ConsentToken(access_token, expiry, parsed.astimezone(UTC).isoformat())


class CallbackServer(HTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        client: DhanAuthClient,
        token_file: Path = TOKEN_FILE,
        postback_dir: Path = POSTBACK_DIR,
    ) -> None:
        super().__init__(address, CallbackHandler)
        self.client = client
        self.token_file = token_file
        self.postback_dir = postback_dir

    def handle_error(self, request: object, client_address: tuple[str, int]) -> None:
        # Default HTTPServer tracebacks can include request details.
        pass


class CallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        # Dhan's redirect includes tokenId in the URL; payloads contain PII.
        pass

    def _reply(self, status: int, body: bytes, *, location: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        server = self.server
        assert isinstance(server, CallbackServer)
        address = urlsplit(self.path)
        if address.path == "/dhan/complete":
            self._reply(200, b"Dhan authorization received. You may close this tab.")
            return
        if address.path.rstrip("/") != CALLBACK_PATH:
            self._reply(404, b"Not found")
            return
        try:
            values = parse_qs(
                address.query, keep_blank_values=True, max_num_fields=10
            ).get("tokenId", [])
        except ValueError:
            self._reply(400, b"Invalid callback")
            return
        if (
            len(values) != 1
            or not 1 <= len(values[0]) <= 4096
            or any(not 33 <= ord(char) <= 126 for char in values[0])
        ):
            self._reply(400, b"Invalid callback")
            return
        try:
            token = server.client.consume_consent(values[0])
        except (OSError, ValueError):
            self._reply(502, b"Dhan authorization failed")
            return
        try:
            _private_write(
                server.token_file, (token.access_token + "\n").encode(), replace=True
            )
            _private_write(
                server.token_file.with_suffix(".meta.json"),
                (
                    json.dumps(
                        {
                            "schema_version": "dhan-consent-token-v1",
                            "source": CONSUME_URL,
                            "received_at_utc": datetime.now(UTC).isoformat(),
                            "expiry_time_ist": token.expiry_time_ist,
                            "expires_at_utc": token.expires_at_utc,
                            "token_sha256": hashlib.sha256(
                                token.access_token.encode()
                            ).hexdigest(),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                ).encode(),
                replace=True,
            )
        except OSError:
            self._reply(500, b"Token storage failed")
            return
        self._reply(303, b"", location="/dhan/complete")

    def do_POST(self) -> None:
        server = self.server
        assert isinstance(server, CallbackServer)
        address = urlsplit(self.path)
        if address.path != POSTBACK_PATH or address.query:
            self._reply(404, b"Not found")
            return
        if self.headers.get_content_type() != "application/json":
            self._reply(415, b"JSON required")
            return
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._reply(411, b"Content-Length required")
            return
        if not 1 <= length <= MAX_POSTBACK_BYTES:
            self._reply(413, b"Postback size invalid")
            return
        self.connection.settimeout(10)
        try:
            raw = self.rfile.read(length)
        except OSError:
            self._reply(408, b"Postback read failed")
            return
        if len(raw) != length:
            self._reply(400, b"Incomplete postback")
            return
        try:
            payload = _strict_json(raw)
        except (UnicodeDecodeError, ValueError):
            self._reply(400, b"Invalid postback JSON")
            return
        if (
            not isinstance(payload, dict)
            or not all(
                isinstance(payload.get(key), str) and payload[key]
                for key in ("dhanClientId", "orderId", "orderStatus")
            )
            or payload["orderStatus"] not in ORDER_STATUSES
        ):
            self._reply(400, b"Invalid postback fields")
            return
        # A matching client ID is a consistency check, not sender authentication.
        if payload["dhanClientId"] != server.client.client_id:
            self._reply(403, b"Client ID mismatch")
            return
        receipt_id = uuid.uuid4().hex
        raw_path = server.postback_dir / f"{receipt_id}.json"
        meta_path = server.postback_dir / f"{receipt_id}.meta.json"
        metadata = {
            "schema_version": "dhan-postback-quarantine-v1",
            "received_at": datetime.now(UTC).isoformat(),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "origin_verified": False,
            "verification_reason": "NO_DHAN_SIGNATURE_METHOD_DOCUMENTED",
        }
        try:
            _private_write(raw_path, raw, replace=False)
            _private_write(
                meta_path,
                (json.dumps(metadata, sort_keys=True) + "\n").encode(),
                replace=False,
            )
        except OSError:
            self._reply(500, b"Postback storage failed")
            return
        self._reply(202, b"Quarantined")


def main() -> None:
    parser = argparse.ArgumentParser(description="Private local Dhan consent callback")
    parser.add_argument("--initiate", action="store_true")
    args = parser.parse_args()
    client = DhanAuthClient.from_private_store()
    endpoint_urls(dict(os.environ))
    with CallbackServer((HOST, PORT), client) as server:
        print("Dhan consent callback listening locally")
        if args.initiate:
            try:
                consent_id = client.generate_consent()
                if not webbrowser.open(
                    f"{LOGIN_URL}?consentAppId={quote(consent_id, safe='')}"
                ):
                    raise RuntimeError("Dhan browser login could not be opened")
            except (OSError, ValueError, RuntimeError):
                raise RuntimeError("Dhan consent initiation failed") from None
            print("Dhan browser login opened; complete authentication personally")
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
