from __future__ import annotations

import asyncio
import hashlib
import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import jwt
from fastapi import Request

from alignment_memory.interfaces.api.errors import ApiError
from alignment_memory.settings import Settings

TEST_USER_HEADER = "X-Alignment-Test-User"
TIMESTAMP_HEADER = "X-Alignment-Timestamp"
SIGNATURE_HEADER = "X-Alignment-Signature"
IDEMPOTENCY_HEADER = "Idempotency-Key"


@dataclass(frozen=True, slots=True, kw_only=True)
class UserContext:
    profile_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class InternalRequestContext:
    timestamp: str
    body_digest: str
    idempotency_key: str | None


class SupabaseJwtVerifier:
    def __init__(self, settings: Settings) -> None:
        self._issuer = settings.supabase_jwt_issuer
        self._audience = settings.supabase_jwt_audience
        self._secret = settings.supabase_jwt_secret
        self._fixture_secret = settings.fixture_jwt_secret
        self._app_mode = settings.app_mode
        self._jwks_client = (
            jwt.PyJWKClient(settings.supabase_jwks_url, cache_keys=True)
            if settings.supabase_jwks_url
            else None
        )

    async def verify(self, token: str) -> UserContext:
        try:
            header = jwt.get_unverified_header(token)
            algorithm = header.get("alg")
            if algorithm == "HS256":
                key = self._secret or (
                    self._fixture_secret if self._app_mode == "fixture" else None
                )
                if key is None:
                    raise ApiError(
                        status_code=503,
                        code="auth_configuration_error",
                        message="Authentication is not configured",
                    )
            elif algorithm in {"RS256", "ES256"} and self._jwks_client is not None:
                signing_key = await asyncio.to_thread(
                    self._jwks_client.get_signing_key_from_jwt,
                    token,
                )
                key = signing_key.key
            else:
                raise ApiError(
                    status_code=401,
                    code="invalid_token",
                    message="Access token is invalid",
                )
            claims = jwt.decode(
                token,
                key,
                algorithms=[algorithm],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except ApiError:
            raise
        except jwt.PyJWTError as error:
            raise ApiError(
                status_code=401,
                code="invalid_token",
                message="Access token is invalid or expired",
            ) from error
        profile_id = claims.get("sub")
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise ApiError(
                status_code=401,
                code="invalid_token",
                message="Access token subject is invalid",
            )
        return UserContext(profile_id=profile_id)


async def authenticate_user(request: Request) -> UserContext:
    settings: Settings = request.app.state.settings
    test_profile = request.headers.get(TEST_USER_HEADER)
    if (
        test_profile is not None
        and settings.app_mode == "fixture"
        and settings.environment == "test"
        and settings.fixture_test_auth_enabled
    ):
        try:
            UUID(test_profile)
        except ValueError as error:
            raise ApiError(
                status_code=401,
                code="invalid_test_identity",
                message="Test identity is invalid",
            ) from error
        return UserContext(profile_id=test_profile)

    authorization = request.headers.get("Authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise ApiError(
            status_code=401,
            code="authentication_required",
            message="A valid bearer token is required",
        )
    verifier: SupabaseJwtVerifier = request.app.state.jwt_verifier
    return await verifier.verify(token.strip())


class InternalHmacVerifier:
    def __init__(self, settings: Settings) -> None:
        secret = settings.internal_hmac_secret or (
            settings.fixture_hmac_secret if settings.app_mode == "fixture" else None
        )
        self._secret = secret.encode() if secret is not None else None
        self._replay_window_seconds = settings.internal_hmac_replay_window_seconds

    async def verify(self, request: Request) -> InternalRequestContext:
        if self._secret is None:
            raise ApiError(
                status_code=503,
                code="internal_auth_configuration_error",
                message="Internal authentication is not configured",
            )
        timestamp = request.headers.get(TIMESTAMP_HEADER)
        supplied_signature = request.headers.get(SIGNATURE_HEADER)
        if timestamp is None or supplied_signature is None:
            raise ApiError(
                status_code=401,
                code="internal_signature_required",
                message="A signed internal request is required",
            )
        signed_at = self._parse_timestamp(timestamp)
        now = datetime.now(UTC)
        if abs((now - signed_at).total_seconds()) > self._replay_window_seconds:
            raise ApiError(
                status_code=401,
                code="internal_signature_expired",
                message="Internal request timestamp is outside the replay window",
            )
        body = await request.body()
        body_digest = hashlib.sha256(body).hexdigest()
        signing_input = f"{timestamp}.{body_digest}".encode()
        expected = hmac.new(self._secret, signing_input, hashlib.sha256).hexdigest()
        normalized_signature = supplied_signature.removeprefix("sha256=")
        if not hmac.compare_digest(expected, normalized_signature):
            raise ApiError(
                status_code=401,
                code="invalid_internal_signature",
                message="Internal request signature is invalid",
            )
        key = request.headers.get(IDEMPOTENCY_HEADER)
        if request.method in {"POST", "PUT", "PATCH"} and (key is None or not key.strip()):
            raise ApiError(
                status_code=400,
                code="idempotency_key_required",
                message="Idempotency-Key is required for internal writes",
            )
        if key is not None and len(key) > 200:
            raise ApiError(
                status_code=400,
                code="invalid_idempotency_key",
                message="Idempotency-Key is invalid",
            )
        return InternalRequestContext(
            timestamp=timestamp,
            body_digest=body_digest,
            idempotency_key=key,
        )

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        try:
            if value.isdigit():
                return datetime.fromtimestamp(int(value), tz=UTC)
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (OverflowError, OSError, ValueError) as error:
            raise ApiError(
                status_code=401,
                code="invalid_internal_timestamp",
                message="Internal request timestamp is invalid",
            ) from error
        if parsed.tzinfo is None:
            raise ApiError(
                status_code=401,
                code="invalid_internal_timestamp",
                message="Internal request timestamp must include a timezone",
            )
        return parsed.astimezone(UTC)


async def authenticate_internal(request: Request) -> InternalRequestContext:
    verifier: InternalHmacVerifier = request.app.state.hmac_verifier
    return await verifier.verify(request)
