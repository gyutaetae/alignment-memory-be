from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

JsonObject = dict[str, Any]
TimestampClock = Callable[[], int]


class WorkerApiError(RuntimeError):
    """A secret-safe control-plane failure exposed to the worker CLI."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable


class HmacApiClient:
    """Small internal API client matching the FastAPI timestamp/body-digest HMAC contract."""

    def __init__(
        self,
        base_url: str,
        secret: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 20.0,
        timestamp_clock: TimestampClock | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("API base URL is required")
        if not secret.strip():
            raise ValueError("API HMAC secret is required")
        self._secret = secret.encode()
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
        )
        self._owns_client = client is None
        self._timestamp_clock = timestamp_clock or (lambda: int(time.time()))

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
            self._owns_client = False

    async def create_job(
        self,
        *,
        repository_id: str,
        event_key: str,
        event_type: str,
        head_sha: str | None,
    ) -> JsonObject:
        payload: JsonObject = {
            "repositoryId": repository_id,
            "eventKey": event_key,
            "eventType": event_type,
            "headSha": head_sha,
        }
        return await self._request(
            "POST",
            "/api/v1/internal/jobs",
            payload=payload,
            idempotency_key=f"job:{event_key}",
        )

    async def get_job_context(self, job_id: str) -> JsonObject:
        return await self._request("GET", f"/api/v1/internal/jobs/{job_id}/context")

    async def transition_job(
        self,
        job_id: str,
        *,
        expected_status: str,
        next_status: str,
        error_code: str | None = None,
    ) -> JsonObject:
        payload: JsonObject = {
            "expectedStatus": expected_status,
            "nextStatus": next_status,
            "errorCode": error_code,
        }
        return await self._request(
            "POST",
            f"/api/v1/internal/jobs/{job_id}/events",
            payload=payload,
            idempotency_key=f"job-event:{job_id}:{expected_status}:{next_status}",
        )

    async def persist_result(self, job_id: str, payload: Mapping[str, object]) -> JsonObject:
        return await self._request(
            "POST",
            f"/api/v1/internal/jobs/{job_id}/result",
            payload=dict(payload),
            idempotency_key=f"job-result:{job_id}",
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, object] | None = None,
        idempotency_key: str | None = None,
    ) -> JsonObject:
        body = _canonical_json(payload) if payload is not None else b""
        timestamp = str(self._timestamp_clock())
        digest = hashlib.sha256(body).hexdigest()
        signature = hmac.new(
            self._secret,
            f"{timestamp}.{digest}".encode(),
            hashlib.sha256,
        ).hexdigest()
        headers = {
            "Accept": "application/json",
            "X-Alignment-Timestamp": timestamp,
            "X-Alignment-Signature": f"sha256={signature}",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key

        try:
            response = await self._client.request(
                method,
                path,
                content=body if payload is not None else None,
                headers=headers,
            )
        except httpx.TimeoutException as error:
            raise WorkerApiError(
                "control_plane_timeout",
                "Alignment Memory API request timed out",
                retryable=True,
            ) from error
        except httpx.RequestError as error:
            raise WorkerApiError(
                "control_plane_unavailable",
                "Alignment Memory API request failed",
                retryable=True,
            ) from error

        if response.status_code >= 400:
            raise _response_error(response)
        try:
            decoded = response.json()
        except ValueError as error:
            raise WorkerApiError(
                "control_plane_invalid_response",
                "Alignment Memory API returned invalid JSON",
                status_code=response.status_code,
            ) from error
        if not isinstance(decoded, dict):
            raise WorkerApiError(
                "control_plane_invalid_response",
                "Alignment Memory API returned a non-object response",
                status_code=response.status_code,
            )
        return decoded


def _canonical_json(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _response_error(response: httpx.Response) -> WorkerApiError:
    code = "control_plane_request_failed"
    retryable = response.status_code >= 500
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        envelope = payload.get("error")
        if isinstance(envelope, dict):
            supplied_code = envelope.get("code")
            supplied_retryable = envelope.get("retryable")
            if isinstance(supplied_code, str) and supplied_code.strip():
                code = supplied_code
            if isinstance(supplied_retryable, bool):
                retryable = supplied_retryable
    return WorkerApiError(
        code,
        f"Alignment Memory API rejected the worker request ({response.status_code})",
        status_code=response.status_code,
        retryable=retryable,
    )
