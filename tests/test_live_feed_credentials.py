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
    get_token_status,
    install_token,
    load_credentials,
    main,
    pre_expiry_renewal_policy,
    renew_token,
    save_credentials,
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


def test_valid_token_survives_cockpit_restart(tmp_path: Path) -> None:
    expiry = datetime.now(UTC) + timedelta(days=5)
    install_res = install_token(
        root=tmp_path,
        client="9876543210",
        token="valid.operator.token",
        expiry=expiry,
        token_type="API",
    )
    assert install_res["schema_version"] == "dhan-api-token-v1"
    assert install_res["client_id_masked"] == "98****10"

    # Files must be mode 0600 in private directory
    token_file = tmp_path / "data/.secrets/dhan-access-token"
    assert token_file.stat().st_mode & 0o077 == 0
    meta_file = tmp_path / "data/.secrets/dhan-access-token.meta.json"
    assert meta_file.stat().st_mode & 0o077 == 0

    # Status check
    status, info = get_token_status(tmp_path)
    assert status == "VALID"
    assert info["token_type"] == "API"
    assert info["is_renewable"] is False
    assert info["automatic_renewal_enabled"] is False

    # Simulate fresh process restart by reading from disk
    loaded_token, loaded_client, loaded_expiry = load_credentials(tmp_path)
    assert loaded_token == "valid.operator.token"
    assert loaded_client == "9876543210"
    assert abs((loaded_expiry - expiry).total_seconds()) < 1.0


def test_expired_token_fails_closed(tmp_path: Path) -> None:
    now = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
    expiry = now + timedelta(hours=1)
    install_token(
        root=tmp_path,
        client="123456",
        token="fixture.expiring.token",
        expiry=expiry,
        token_type="API",
        now=now,
    )

    # At now + 30 min (within 2h): EXPIRING
    st, _ = get_token_status(tmp_path, now=now + timedelta(minutes=30))
    assert st == "EXPIRING"

    # At now + 2 hours: EXPIRED
    st, _ = get_token_status(tmp_path, now=now + timedelta(hours=2))
    assert st == "EXPIRED"

    # Calling load_credentials when expired fails closed with ValueError
    with pytest.raises(ValueError, match="authentication required"):
        load_credentials(tmp_path, now=now + timedelta(hours=2))


def test_renewal_succeeds_and_atomically_replaces_previous_token(tmp_path: Path) -> None:
    now = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
    initial_expiry = now + timedelta(hours=1)
    install_token(
        root=tmp_path,
        client="123456",
        token="initial.web.token",
        expiry=initial_expiry,
        token_type="WEB",
        now=now,
    )

    class MockResponse(io.BytesIO):
        status = 200

    new_expiry_ist = "2026-11-06 15:30:00"
    mock_payload = {
        "dhanClientId": "123456",
        "dhanClientName": "ALPHA TESTER",
        "accessToken": "renewed.jwt.token",
        "expiryTime": new_expiry_ist,
    }

    requests = []

    def mock_opener(request: Request, **_kwargs: object) -> MockResponse:
        requests.append((request.full_url, request.get_method(), request.headers))
        return MockResponse(json.dumps(mock_payload).encode())

    status, info = renew_token(tmp_path, opener=mock_opener, now=now)
    assert status == "VALID"
    assert info["token_type"] == "RENEWED"
    assert info["is_renewable"] is True

    # Verify atomic update on disk
    loaded_token, loaded_client, loaded_expiry = load_credentials(tmp_path, now=now)
    assert loaded_token == "renewed.jwt.token"
    assert loaded_client == "123456"

    # Request header verification (access-token and dhanClientId sent)
    assert len(requests) == 1
    assert requests[0][0] == "https://api.dhan.co/v2/RenewToken"
    assert requests[0][1] == "GET"
    assert requests[0][2]["Access-token"] == "initial.web.token"
    assert requests[0][2]["Dhanclientid"] == "123456"


def test_renewal_failure_retains_safe_state_without_leaking_credentials(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    now = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
    initial_expiry = now + timedelta(hours=1)
    install_token(
        root=tmp_path,
        client="123456",
        token="safe.original.token",
        expiry=initial_expiry,
        token_type="WEB",
        now=now,
    )

    class ErrorResponse(io.BytesIO):
        status = 401

    def failing_opener(_req: Request, **_kwargs: object) -> ErrorResponse:
        return ErrorResponse(b'{"errorType":"Order_Error","errorCode":"DH-906","errorMessage":"Invalid Token"}')

    status, info = renew_token(tmp_path, opener=failing_opener, now=now)
    assert status == "AUTH REQUIRED"
    assert "RENEWAL_HTTP_401" in info.get("error", "")

    # Original credentials must remain completely usable and untouched!
    loaded_token, loaded_client, _ = load_credentials(tmp_path, now=now)
    assert loaded_token == "safe.original.token"
    assert loaded_client == "123456"

    # Secrets never leaked to stdout/stderr
    captured = capsys.readouterr()
    assert "safe.original.token" not in captured.out
    assert "safe.original.token" not in captured.err


def test_unsupported_token_type_does_not_attempt_renewal(tmp_path: Path) -> None:
    now = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
    install_token(
        root=tmp_path,
        client="123456",
        token="api.key.token",
        expiry=now + timedelta(days=30),
        token_type="API",
        now=now,
    )

    def forbidden_opener(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Renewal endpoint must NOT be called for API tokens")

    status, info = renew_token(tmp_path, opener=forbidden_opener, now=now)
    assert status == "TOKEN_RENEWAL_NOT_SUPPORTED"
    assert info["error"] == "TOKEN_NOT_RENEWABLE"


def test_pre_expiry_policy_leaves_automatic_renewal_disabled_by_default(tmp_path: Path) -> None:
    now = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
    install_token(
        root=tmp_path,
        client="123456",
        token="expiring.web.token",
        expiry=now + timedelta(hours=1),
        token_type="WEB",
        now=now,
    )

    def forbidden_opener(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Automatic renewal must NOT trigger without operator approval")

    # By default, automatic renewal is disabled
    status, info = pre_expiry_renewal_policy(tmp_path, allow_automatic=False, opener=forbidden_opener, now=now)
    assert status == "AUTOMATIC_RENEWAL_DISABLED"
    assert info["policy"] == "PRE_EXPIRY_DISABLED_PENDING_OPERATOR_APPROVAL"


def test_no_secret_appears_in_status_or_cli_reporting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret_token = "ultra.sensitive.jwt.token.value"
    client_id = "1000000001"
    now = datetime.now(UTC)
    install_token(
        root=tmp_path,
        client=client_id,
        token=secret_token,
        expiry=now + timedelta(days=7),
        token_type="API",
    )

    # 1. get_token_status info dictionary check
    status, info = get_token_status(tmp_path)
    assert status == "VALID"
    assert secret_token not in str(info)
    assert info["client_id_masked"] == "10****01"

    # 2. CLI --token-status check
    monkeypatch.setattr("sys.argv", [
        "credentials", "--root", str(tmp_path), "--private-dir", str(tmp_path), "--token-status"
    ])
    capsys.readouterr()  # clear buffer
    assert main() == 0
    captured = capsys.readouterr().out
    assert secret_token not in captured
    parsed_json = json.loads(captured.strip())
    assert parsed_json["status"] == "VALID"
    assert secret_token not in json.dumps(parsed_json)


def test_install_token_fallback_to_stored_client_and_base64_charset(tmp_path: Path):
    now = datetime.now(UTC)
    # First install with explicit client
    install_token(
        root=tmp_path,
        client="1000000001",
        token="initial.token",
        expiry=now + timedelta(days=7),
    )
    # Install new token containing base64 chars (+, /, =) without passing client
    b64_token = "fresh+token/with+base64=chars"
    result = install_token(
        root=tmp_path,
        client=None,
        token=b64_token,
        expiry=now + timedelta(days=7),
    )
    assert result["client_id_masked"] == "10****01"
    # Round-trip verify with shared loader
    loaded_token, loaded_client, _ = load_credentials(tmp_path, now=now)
    assert loaded_token == b64_token
    assert loaded_client == "1000000001"


def test_get_token_status_reflects_remote_profile_401(tmp_path: Path):
    now = datetime.now(UTC)
    install_token(
        root=tmp_path,
        client="1000000001",
        token="active.token",
        expiry=now + timedelta(days=7),
    )
    status, _ = get_token_status(tmp_path, now=now)
    assert status == "VALID"

    # Simulate profile check recording 401 for this token
    token_sha256 = hashlib.sha256("active.token".encode("ascii")).hexdigest()
    profile_check = tmp_path / "data/.secrets/dhan-profile-check.json"
    profile_check.write_text(json.dumps({
        "schema_version": "alpha-grid-dhan-profile-check-v1",
        "token_sha256": token_sha256,
        "result": "PROFILE_HTTP_401",
        "checked_at": now.isoformat(),
    }))
    profile_check.chmod(0o600)

    status, info = get_token_status(tmp_path, now=now)
    assert status == "AUTH REQUIRED"
    assert info["reason"] == "PROFILE_HTTP_401"


def test_profile_check_updates_cached_401_without_changing_credential_bytes(tmp_path: Path):
    now = datetime.now(UTC)
    private_dir = tmp_path / "private_offline"
    private_dir.mkdir(mode=0o700)

    # 1. Install token and capture initial file bytes
    install_token(
        root=tmp_path,
        client="1000000001",
        token="active.reused.token",
        expiry=now + timedelta(days=7),
    )
    token_bytes_before = (tmp_path / "data/.secrets/dhan-access-token").read_bytes()
    meta_bytes_before = (tmp_path / "data/.secrets/dhan-access-token.meta.json").read_bytes()
    client_bytes_before = (tmp_path / "data/.secrets/dhan-client-id").read_bytes()

    # 2. Simulate cached 401 on disk for this same token SHA-256
    token_sha256 = hashlib.sha256("active.reused.token".encode("ascii")).hexdigest()
    profile_check = tmp_path / "data/.secrets/dhan-profile-check.json"
    profile_check.write_text(json.dumps({
        "schema_version": "alpha-grid-dhan-profile-check-v1",
        "token_sha256": token_sha256,
        "result": "PROFILE_HTTP_401",
        "checked_at": now.isoformat(),
    }))
    profile_check.chmod(0o600)

    status, info = get_token_status(tmp_path, now=now, private_dir=private_dir)
    assert status == "AUTH REQUIRED"
    assert info["reason"] == "PROFILE_HTTP_401"

    # 3. Reauthorization succeeds at broker (profile check returns Active)
    class MockResponse(io.BytesIO):
        status = 200

    future_validity = (datetime.now(IST) + timedelta(hours=2)).strftime("%d/%m/%Y %H:%M")
    future_data = (datetime.now(IST) + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S.0")
    payload = json.dumps({
        "dhanClientId": "1000000001",
        "tokenValidity": future_validity,
        "dataPlan": "Active",
        "dataValidity": future_data,
    }).encode()

    def mock_opener(*_args, **_kwargs):
        return MockResponse(payload)

    # Authorized profile check executed using existing stored token
    result = _check_profile(tmp_path, private_dir, mock_opener)
    assert result == "DHAN_TOKEN_AND_DATA_PLAN_ACTIVE"

    # 4. Verification:
    # A) Credential bytes MUST remain identical (not reinstalled or modified)
    assert (tmp_path / "data/.secrets/dhan-access-token").read_bytes() == token_bytes_before
    assert (tmp_path / "data/.secrets/dhan-access-token.meta.json").read_bytes() == meta_bytes_before
    assert (tmp_path / "data/.secrets/dhan-client-id").read_bytes() == client_bytes_before

    # B) Cached 401 is cleared in both secret_dir and private_dir
    status, _ = get_token_status(tmp_path, now=now, private_dir=private_dir)
    assert status == "VALID"

    secret_check = json.loads((tmp_path / "data/.secrets/dhan-profile-check.json").read_text())
    assert secret_check["result"] == "DHAN_TOKEN_AND_DATA_PLAN_ACTIVE"
    private_check = json.loads((private_dir / "dhan-profile-check.json").read_text())
    assert private_check["result"] == "DHAN_TOKEN_AND_DATA_PLAN_ACTIVE"


def test_genuine_dhan_401_fails_closed_and_records_status(
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    private_dir = tmp_path / "private_offline"
    private_dir.mkdir(mode=0o700)

    install_token(
        root=tmp_path,
        client="1000000001",
        token="invalid.token",
        expiry=now + timedelta(days=7),
    )

    class Mock401(io.BytesIO):
        status = 401

    def mock_opener(*_args: object, **_kwargs: object) -> Mock401:
        return Mock401(b'{"error": "Unauthorized"}')

    result = _check_profile(tmp_path, private_dir, mock_opener)
    assert result == "PROFILE_HTTP_401"

    status, info = get_token_status(
        tmp_path, now=now, private_dir=private_dir
    )
    assert status == "AUTH REQUIRED"
    assert info["reason"] == "PROFILE_HTTP_401"


