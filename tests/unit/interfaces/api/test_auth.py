import asyncio
from datetime import UTC, datetime, timedelta

import jwt
from fastapi.testclient import TestClient

from alignment_memory.interfaces.api.dependencies import FIXTURE_PROFILE_ID
from alignment_memory.interfaces.api.main import create_app
from alignment_memory.interfaces.api.security import TEST_USER_HEADER
from alignment_memory.settings import Settings

JWT_SECRET = "unit-test-jwt-secret-at-least-32-bytes-long"


def _settings(**overrides: object) -> Settings:
    return Settings(
        app_mode="fixture",
        environment="test",
        supabase_jwt_secret=JWT_SECRET,
        _env_file=None,
        **overrides,
    )


def _token(
    *,
    audience: str = "authenticated",
    expires_in: int = 300,
    secret: str = JWT_SECRET,
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": FIXTURE_PROFILE_ID,
            "iss": "https://fixture.supabase.co/auth/v1",
            "aud": audience,
            "iat": now,
            "exp": now + timedelta(seconds=expires_in),
        },
        secret,
        algorithm="HS256",
    )


def test_user_route_requires_valid_supabase_token_and_returns_error_envelope() -> None:
    app = create_app(_settings())

    with TestClient(app) as client:
        missing = client.get("/api/v1/repositories", headers={"X-Request-ID": "request-1"})
        wrong_audience = client.get(
            "/api/v1/repositories",
            headers={"Authorization": f"Bearer {_token(audience='wrong')}"},
        )
        expired = client.get(
            "/api/v1/repositories",
            headers={"Authorization": f"Bearer {_token(expires_in=-1)}"},
        )
        wrong_signature = client.get(
            "/api/v1/repositories",
            headers={"Authorization": f"Bearer {_token(secret='x' * 40)}"},
        )
        valid = client.get(
            "/api/v1/repositories",
            headers={"Authorization": f"Bearer {_token()}"},
        )

    assert missing.status_code == 401
    assert missing.headers["X-Request-ID"] == "request-1"
    assert missing.json() == {
        "error": {
            "code": "authentication_required",
            "message": "A valid bearer token is required",
            "retryable": False,
            "requestId": "request-1",
        }
    }
    assert wrong_audience.status_code == 401
    assert wrong_audience.json()["error"]["code"] == "invalid_token"
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "invalid_token"
    assert wrong_signature.status_code == 401
    assert wrong_signature.json()["error"]["code"] == "invalid_token"
    assert valid.status_code == 200
    assert len(valid.json()["repositories"]) == 1


def test_fixture_test_header_requires_explicit_test_setting() -> None:
    disabled = create_app(_settings(fixture_test_auth_enabled=False))
    enabled = create_app(_settings(fixture_test_auth_enabled=True))
    headers = {TEST_USER_HEADER: FIXTURE_PROFILE_ID}

    with TestClient(disabled) as client:
        rejected = client.get("/api/v1/repositories", headers=headers)
    with TestClient(enabled) as client:
        accepted = client.get("/api/v1/repositories", headers=headers)

    assert rejected.status_code == 401
    assert accepted.status_code == 200


def test_live_composition_does_not_connect_or_require_openrouter_on_app_creation() -> None:
    app = create_app(
        Settings(
            app_mode="live",
            database_url="postgresql://example.invalid/alignment",
            github_app_id="123",
            github_app_private_key="not-parsed-until-use",
            supabase_jwt_issuer="https://project.supabase.co/auth/v1",
            supabase_jwks_url="https://project.supabase.co/auth/v1/.well-known/jwks.json",
            internal_hmac_secret="live-hmac-secret",
            _env_file=None,
        )
    )

    assert app.state.container.repository is None
    assert app.state.container.llm is None
    asyncio.run(app.state.container.close())
