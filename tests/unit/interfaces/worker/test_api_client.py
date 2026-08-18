import hashlib
import hmac
import json

import httpx
import pytest

from alignment_memory.interfaces.worker.api_client import HmacApiClient, WorkerApiError


@pytest.mark.asyncio
async def test_hmac_client_signs_exact_canonical_body_and_idempotency_key() -> None:
    secret = "worker-hmac-secret"
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        captured["headers"] = dict(request.headers)
        return httpx.Response(201, json={"jobId": "job-1"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.example.test",
    ) as http_client:
        client = HmacApiClient(
            "https://api.example.test",
            secret,
            client=http_client,
            timestamp_clock=lambda: 1_800_000_000,
        )
        response = await client.create_job(
            repository_id="repo-1",
            event_key="pr:1:7:head",
            event_type="pr_analysis",
            head_sha="a" * 40,
        )

    body = captured["body"]
    headers = captured["headers"]
    assert isinstance(body, bytes)
    assert isinstance(headers, dict)
    assert response == {"jobId": "job-1"}
    assert json.loads(body) == {
        "eventKey": "pr:1:7:head",
        "eventType": "pr_analysis",
        "headSha": "a" * 40,
        "repositoryId": "repo-1",
    }
    digest = hashlib.sha256(body).hexdigest()
    expected = hmac.new(
        secret.encode(),
        f"1800000000.{digest}".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert headers["x-alignment-signature"] == f"sha256={expected}"
    assert headers["idempotency-key"] == "job:pr:1:7:head"


@pytest.mark.asyncio
async def test_hmac_client_exposes_safe_error_code_without_provider_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "error": {
                    "code": "stale_repository_state",
                    "message": "secret upstream payload",
                    "retryable": True,
                }
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.example.test",
    ) as http_client:
        client = HmacApiClient(
            "https://api.example.test",
            "secret",
            client=http_client,
        )
        with pytest.raises(WorkerApiError) as raised:
            await client.get_job_context("job-1")

    assert raised.value.code == "stale_repository_state"
    assert raised.value.retryable is True
    assert "secret upstream payload" not in str(raised.value)
