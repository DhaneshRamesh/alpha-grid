"""Account cache software behavior only; fixture bytes are never market evidence."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from quant.apps import dhan_account_cache as cache


def test_private_account_response_recovery_and_five_minute_spacing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DHAN_CLIENT_ID", "fixture-client")
    with tempfile.TemporaryDirectory(dir=Path("data/.secrets")) as directory:
        monkeypatch.setattr(cache, "ROOT", Path(directory))
        token_path = Path(directory) / "fixture-token"
        cache._write_private_bytes(token_path, b"fixture-only")
        monkeypatch.setattr(cache, "TOKEN_PATH", token_path)
        start = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)
        assert cache.reserve("fundlimit", start)
        assert not cache.reserve("fundlimit", start + timedelta(minutes=4))
        raw = json.dumps(
            {"dhanClientId": "fixture-client", "availabelBalance": 123.45}
        ).encode()
        receipt = start.isoformat()
        saved = cache.save("fundlimit", 200, raw, receipt)
        recovered = cache.load("fundlimit", "fixture-client")
        assert recovered["data"]["available_balance_inr"] == "123.45"
        assert recovered["response_sha256"] == saved["sha256"]
        assert recovered["retrieved_at"] == receipt
        assert cache.reserve("fundlimit", start + timedelta(minutes=5))
        cache.save("fundlimit", 401, b'{"errorCode":"DH-901"}', receipt)
        stale = cache.load("fundlimit", "fixture-client")
        assert stale["reauthentication_required"]
        assert stale["last_http_status"] == 401
        assert stale["data"] == recovered["data"]
        assert cache.reserve("fundlimit", start + timedelta(minutes=10))
        cache.save("fundlimit", 200, b'{"dhanClientId":"wrong"}', receipt)
        invalid = cache.load("fundlimit", "fixture-client")
        assert invalid["data"] == recovered["data"]
        assert invalid["last_validation_failure_at"] == receipt
        state = cache._read_state("fundlimit")
        path = cache.ROOT / state["latest_success"]
        path.write_bytes(b"tampered")
        with pytest.raises(ValueError, match="integrity mismatch"):
            cache.load("fundlimit", "fixture-client")


def test_private_consent_expiry_must_match_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with tempfile.TemporaryDirectory(dir=Path("data/.secrets")) as directory:
        token_path = Path(directory) / "fixture-token"
        raw = b"fixture-token-bytes\n"
        cache._write_private_bytes(token_path, raw)
        monkeypatch.setattr(cache, "TOKEN_PATH", token_path)
        cache._write_private_json(
            token_path.with_suffix(".meta.json"),
            {
                "schema_version": "dhan-consent-token-v1",
                "source": "https://auth.dhan.co/app/consumeApp-consent",
                "expires_at_utc": (datetime.now(UTC) + timedelta(hours=23)).isoformat(),
                "token_sha256": sha256(raw.strip()).hexdigest(),
            },
        )
        monkeypatch.setattr("sys.argv", ["cache", "credential", "fundlimit"])
        cache.main()
        assert json.loads(capsys.readouterr().out)["metadata_valid"]
        token_path.write_bytes(b"changed-fixture-token\n")
        cache.main()
        assert not json.loads(capsys.readouterr().out)["metadata_valid"]
