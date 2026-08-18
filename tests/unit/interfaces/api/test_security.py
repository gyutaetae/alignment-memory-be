import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from alignment_memory.interfaces.api.dependencies import FIXTURE_REPOSITORY_ID
from alignment_memory.interfaces.api.main import create_app
from alignment_memory.settings import Settings

HMAC_SECRET = "unit-test-hmac-secret"


def _client() -> TestClient:
    return TestClient(
        create_app(
            Settings(
                app_mode="fixture",
                environment="test",
                fixture_test_auth_enabled=True,
                internal_hmac_secret=HMAC_SECRET,
                _env_file=None,
            )
        )
    )


def _signed_headers(
    body: bytes,
    *,
    timestamp: str | None = None,
    key: str = "security-test",
) -> dict[str, str]:
    timestamp = timestamp or str(int(time.time()))
    digest = hashlib.sha256(body).hexdigest()
    signature = hmac.new(
        HMAC_SECRET.encode(),
        f"{timestamp}.{digest}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Alignment-Timestamp": timestamp,
        "X-Alignment-Signature": signature,
        "Idempotency-Key": key,
    }


def _body(event_key: str) -> bytes:
    return json.dumps(
        {
            "repositoryId": FIXTURE_REPOSITORY_ID,
            "eventKey": event_key,
            "eventType": "pr_analysis",
            "headSha": "d" * 40,
        },
        separators=(",", ":"),
    ).encode()


def test_internal_hmac_rejects_body_tamper_and_expired_replay() -> None:
    original = _body("signed-original")
    tampered = _body("tampered")
    expired_timestamp = str(int(time.time()) - 1000)

    with _client() as client:
        tamper_response = client.post(
            "/api/v1/internal/jobs",
            content=tampered,
            headers=_signed_headers(original),
        )
        replay_response = client.post(
            "/api/v1/internal/jobs",
            content=original,
            headers=_signed_headers(original, timestamp=expired_timestamp),
        )

    assert tamper_response.status_code == 401
    assert tamper_response.json()["error"]["code"] == "invalid_internal_signature"
    assert replay_response.status_code == 401
    assert replay_response.json()["error"]["code"] == "internal_signature_expired"


def test_internal_idempotency_replays_same_response_and_rejects_key_reuse() -> None:
    first_body = _body("idempotent-event")
    changed_body = _body("different-event")

    with _client() as client:
        first = client.post(
            "/api/v1/internal/jobs",
            content=first_body,
            headers=_signed_headers(first_body, key="same-key"),
        )
        replay = client.post(
            "/api/v1/internal/jobs",
            content=first_body,
            headers=_signed_headers(first_body, key="same-key"),
        )
        conflict = client.post(
            "/api/v1/internal/jobs",
            content=changed_body,
            headers=_signed_headers(changed_body, key="same-key"),
        )

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
