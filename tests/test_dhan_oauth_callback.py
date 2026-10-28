"""Local HTTP checks for Dhan OAuth completion and unverified postbacks."""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tools.dhan_oauth_callback import (
    CALLBACK_PATH,
    POSTBACK_PATH,
    CallbackServer,
    ConsentToken,
    DhanAuthClient,
    endpoint_urls,
)


class FakeClient:
    client_id = "test-client"

    def __init__(self) -> None:
        self.consumed: list[str] = []

    def consume_consent(self, token_id: str) -> ConsentToken:
        self.consumed.append(token_id)
        expiry = (datetime.now(UTC) + timedelta(hours=23)).isoformat()
        return ConsentToken("test-access-token", expiry, expiry)


def request(
    port: int,
    method: str,
    path: str,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


class CallbackTests(unittest.TestCase):
    def test_documented_generate_consent_is_one_post(self) -> None:
        seen: list[object] = []

        def opener(req: object, *, timeout: int) -> io.BytesIO:
            seen.append((req, timeout))
            return io.BytesIO(
                b'{"consentAppId":"940b0ca1-3ff4-4476-b46e-03a3ce7dc55d","status":"success"}'
            )

        client = DhanAuthClient("test-client", "test-key", "test-secret", opener=opener)
        self.assertEqual(
            client.generate_consent(), "940b0ca1-3ff4-4476-b46e-03a3ce7dc55d"
        )
        self.assertEqual(len(seen), 1)
        outbound, timeout = seen[0]
        self.assertEqual(outbound.get_method(), "POST")  # type: ignore[attr-defined]
        self.assertEqual(
            outbound.full_url,  # type: ignore[attr-defined]
            "https://auth.dhan.co/app/generate-consent?client_id=test-client",
        )
        self.assertEqual(timeout, 10)

    def test_documented_consume_request_and_client_identity(self) -> None:
        seen: list[object] = []

        def opener(req: object, *, timeout: int) -> io.BytesIO:
            seen.append((req, timeout))
            expiry = (datetime.now(UTC) + timedelta(hours=23)).isoformat()
            return io.BytesIO(
                json.dumps(
                    {
                        "dhanClientId": "test-client",
                        "accessToken": "test-access-token",
                        "expiryTime": expiry,
                    }
                ).encode()
            )

        client = DhanAuthClient("test-client", "test-key", "test-secret", opener=opener)
        result = client.consume_consent("one time/id")
        self.assertEqual(result.access_token, "test-access-token")
        self.assertTrue(result.expires_at_utc)
        outbound, timeout = seen[0]
        self.assertEqual(outbound.get_method(), "GET")  # type: ignore[attr-defined]
        self.assertEqual(
            outbound.full_url,  # type: ignore[attr-defined]
            "https://auth.dhan.co/app/consumeApp-consent?tokenId=one%20time%2Fid",
        )
        self.assertEqual(
            outbound.get_header("App_id"),
            "test-key",  # type: ignore[attr-defined]
        )
        self.assertEqual(
            outbound.get_header("App_secret"),  # type: ignore[attr-defined]
            "test-secret",
        )
        self.assertEqual(timeout, 10)

        def wrong_client(req: object, *, timeout: int) -> io.BytesIO:
            return io.BytesIO(
                b'{"dhanClientId":"other-client","accessToken":"test-access-token"}'
            )

        mismatched = DhanAuthClient(
            "test-client", "test-key", "test-secret", opener=wrong_client
        )
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            mismatched.consume_consent("one-time")

    def test_http_callback_exchanges_then_saves_token_without_echo(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path("data/.secrets")) as directory:
            client = FakeClient()
            token_file = Path(directory) / "token"
            postbacks = Path(directory) / "postbacks"
            with CallbackServer(
                ("127.0.0.1", 0), client, token_file, postbacks
            ) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    status, _, _ = request(
                        server.server_port,
                        "GET",
                        f"{CALLBACK_PATH}?tokenId=one&tokenId=two",
                    )
                    self.assertEqual(status, 400)
                    self.assertEqual(client.consumed, [])
                    status, headers, body = request(
                        server.server_port, "GET", f"{CALLBACK_PATH}/?tokenId=one-time"
                    )
                    self.assertEqual(status, 303)
                    self.assertEqual(headers["Location"], "/dhan/complete")
                    self.assertEqual(headers["Cache-Control"], "no-store")
                    self.assertNotIn(b"one-time", body)
                    self.assertNotIn(b"test-access-token", body)
                    self.assertEqual(client.consumed, ["one-time"])
                    self.assertEqual(token_file.read_bytes(), b"test-access-token\n")
                    token_meta = json.loads(
                        token_file.with_suffix(".meta.json").read_text()
                    )
                    self.assertEqual(
                        token_meta["schema_version"], "dhan-consent-token-v1"
                    )
                    self.assertEqual(
                        token_meta["token_sha256"],
                        hashlib.sha256(b"test-access-token").hexdigest(),
                    )
                    self.assertTrue(token_meta["expires_at_utc"])
                    status, _, body = request(
                        server.server_port, "GET", "/dhan/complete"
                    )
                    self.assertEqual(status, 200)
                    self.assertNotIn(b"test-access-token", body)
                finally:
                    server.shutdown()
                    thread.join(timeout=3)

    def test_postback_is_quarantined_with_unverified_origin(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path("data/.secrets")) as directory:
            client = FakeClient()
            postbacks = Path(directory) / "postbacks"
            with CallbackServer(
                ("127.0.0.1", 0), client, Path(directory) / "token", postbacks
            ) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    payload = {
                        "dhanClientId": "test-client",
                        "orderId": "order-1",
                        "orderStatus": "PENDING",
                        "transactionType": "BUY",
                    }
                    raw = json.dumps(payload, separators=(",", ":")).encode()
                    status, _, body = request(
                        server.server_port,
                        "POST",
                        POSTBACK_PATH,
                        raw,
                        {"Content-Type": "application/json"},
                    )
                    self.assertEqual(status, 202)
                    self.assertNotIn(b"order-1", body)
                    files = list(postbacks.glob("*.json"))
                    raw_files = [
                        path for path in files if not path.name.endswith(".meta.json")
                    ]
                    self.assertEqual(len(raw_files), 1)
                    self.assertEqual(raw_files[0].read_bytes(), raw)
                    meta = json.loads(
                        raw_files[0].with_suffix(".meta.json").read_text()
                    )
                    self.assertEqual(meta["sha256"], hashlib.sha256(raw).hexdigest())
                    self.assertIs(meta["origin_verified"], False)
                    self.assertNotIn("orderId", meta)

                    payload["dhanClientId"] = "wrong-client"
                    status, _, _ = request(
                        server.server_port,
                        "POST",
                        POSTBACK_PATH,
                        json.dumps(payload).encode(),
                        {"Content-Type": "application/json"},
                    )
                    self.assertEqual(status, 403)
                    self.assertEqual(len(list(postbacks.glob("*.meta.json"))), 1)
                    duplicate = (
                        b'{"dhanClientId":"test-client","orderId":"order-2",'
                        b'"orderStatus":"PENDING","orderStatus":"TRADED"}'
                    )
                    status, _, _ = request(
                        server.server_port,
                        "POST",
                        POSTBACK_PATH,
                        duplicate,
                        {"Content-Type": "application/json"},
                    )
                    self.assertEqual(status, 400)
                    self.assertEqual(len(list(postbacks.glob("*.meta.json"))), 1)
                finally:
                    server.shutdown()
                    thread.join(timeout=3)

    def test_public_urls_require_https_origin(self) -> None:
        self.assertEqual(
            endpoint_urls({}, 8766),
            (
                "http://127.0.0.1:8766/dhan/callback",
                "http://127.0.0.1:8766/dhan/postback",
            ),
        )
        self.assertEqual(
            endpoint_urls({"ALPHA_GRID_PUBLIC_BASE_URL": "https://example.test"}),
            (
                "https://example.test/dhan/callback",
                "https://example.test/dhan/postback",
            ),
        )
        for base in (
            "http://example.test",
            "https://localhost",
            "https://127.0.0.2",
            "https://example.test/x",
        ):
            with self.assertRaises(ValueError):
                endpoint_urls({"ALPHA_GRID_PUBLIC_BASE_URL": base})


if __name__ == "__main__":
    unittest.main()
