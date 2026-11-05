"""Offline credential lifecycle checks; fixture secrets never reach a network."""

import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.request import Request

import pytest

from quant.acquisition.calendar import IST
from quant.apps.live_feed_credentials import (
    _check_profile,
    _install_web_token,
    load_credentials,
    main,
)


def test_private_oauth_token_hash_expiry_and_permissions(tmp_path: Path) -> None:
    private = tmp_path / "data/.secrets"
    private.mkdir(parents=True, mode=0o700)
    token = private / "dhan-access-token"
    token.write_text("fixture.token\n")
    token.chmod(0o600)
    meta = private / "dhan-access-token.meta.json"
    now = datetime(2026, 11, 2, 4, 0, tzinfo=UTC)
    record = {
        "schema_version": "dhan-consent-token-v1",
        "source": "https://auth.dhan.co/app/consumeApp-consent",
        "token_sha256": hashlib.sha256(b"fixture.token").hexdigest(),
        "expires_at_utc": (now + timedelta(hours=1)).isoformat(),
    }
    meta.write_text(json.dumps(record))
    meta.chmod(0o600)
    env = tmp_path / ".env"
    env.write_text("DHAN_CLIENT_ID=123456\nDHAN_API_KEY=fixture\n")
    env.chmod(0o600)
    assert load_credentials(tmp_path, now) == (
        "fixture.token",
        "123456",
        now + timedelta(hours=1),
    )
    with pytest.raises(ValueError, match="authentication required"):
        load_credentials(tmp_path, now + timedelta(hours=1))
    record["token_sha256"] = "0" * 64
    meta.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="metadata invalid"):
        load_credentials(tmp_path, now)
    meta.chmod(0o644)
    with pytest.raises(PermissionError, match="owner-private"):
        load_credentials(tmp_path, now)


def test_operator_installs_web_token_without_echo_or_remote_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    expiry = (datetime.now(UTC) + timedelta(hours=1)).astimezone(IST)
    answers = iter(["123456", "fixture.web.token"])
    monkeypatch.setattr(
        "quant.apps.live_feed_credentials.getpass.getpass", lambda *_: next(answers)
    )
    monkeypatch.setattr(
        "builtins.input", lambda *_: expiry.strftime("%Y-%m-%d %H:%M:%S")
    )
    _install_web_token(tmp_path)
    assert load_credentials(tmp_path)[0] == "fixture.web.token"
    assert "fixture.web.token" not in capsys.readouterr().out
    assert (tmp_path / "data/.secrets/dhan-access-token").stat().st_mode & 0o077 == 0


def test_profile_checks_entitlement_only_on_explicit_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "quant.apps.live_feed_scope.valid_authorization", lambda *a: ({}, [], "v2")
    )
    monkeypatch.setattr(
        "quant.apps.live_feed_credentials.load_credentials",
        lambda *a: (
            "fixture.web.token",
            "123456",
            datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    class Response(io.BytesIO):
        status = 200

    def response(data_plan: str) -> Response:
        future = datetime.now(IST) + timedelta(hours=1)
        return Response(
            json.dumps(
                {
                    "dhanClientId": "123456",
                    "tokenValidity": future.strftime("%d/%m/%Y %H:%M"),
                    "dataPlan": data_plan,
                    "dataValidity": future.strftime("%Y-%m-%d %H:%M:%S.0"),
                }
            ).encode()
        )

    requests = []

    def open_profile(request: Request, timeout: int) -> Response:
        requests.append((request.full_url, request.get_method(), timeout))
        return response("Active")

    assert (
        _check_profile(tmp_path, tmp_path, open_profile)
        == "DHAN_TOKEN_AND_DATA_PLAN_ACTIVE"
    )
    assert requests == [("https://api.dhan.co/v2/profile", "GET", 8)]
    assert (
        _check_profile(
            tmp_path, tmp_path, lambda *_args, **_kwargs: response("Deactive")
        )
        == "DATA_API_NOT_ACTIVE"
    )


def test_profile_requires_local_credential_before_any_request(tmp_path: Path) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("authenticated request must not occur")

    assert _check_profile(tmp_path, tmp_path, forbidden) == "AUTH_REQUIRED"


def test_eligible_projection_keeps_raw_capture_on_same_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("quant.apps.live_feed_scope.approved_plan", lambda *a: (
        "1,2", "raw.qj", "dated.nse", "dated.manifest", "master.csv",
        "master.manifest", "calendar.json", "universe.json", "STANDARD_QUOTE", "0",
        "RAW_CAPTURE_WITH_ELIGIBLE_PROJECTION", "1793770000",
    ))
    monkeypatch.setattr("quant.apps.live_feed_credentials.load_credentials",
                        lambda *a: ("fixture.token", "123456", datetime.now(UTC)))
    monkeypatch.setattr("sys.argv", ["credentials", "--root", str(tmp_path),
                                      "--private-dir", str(tmp_path),
                                      "--gateway", str(tmp_path / "gateway")])
    captured = []

    def fake_exec(_path: str, argv: list[str], _env: dict) -> None:
        captured.append(argv)
        raise RuntimeError("offline fixture intercepted exec")

    monkeypatch.setattr("quant.apps.live_feed_credentials.os.execve", fake_exec)
    with pytest.raises(RuntimeError, match="offline fixture"):
        main()
    assert len(captured) == 1
    assert captured[0][-2:] == ["--raw-capture-only", "--eligible-projection"]
    monkeypatch.setattr("quant.apps.live_feed_scope.approved_plan", lambda *a: (
        "1,2", "raw.qj", "dated.nse", "dated.manifest", "master.csv",
        "master.manifest", "calendar.json", "universe.json", "STANDARD_FULL", "0",
        "RAW_CAPTURE_ONLY", "1793770000",
    ))
    with pytest.raises(RuntimeError, match="offline fixture"):
        main()
    assert captured[1][-2:] == ["--raw-capture-only", "--standard-full"]
