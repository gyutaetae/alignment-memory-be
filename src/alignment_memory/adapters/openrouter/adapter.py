from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from alignment_memory.adapters.openrouter.prompt import build_messages
from alignment_memory.contracts.analysis import AnalysisResult
from alignment_memory.domain import exact_quote_is_present
from alignment_memory.ports.llm import (
    AnalysisRequest,
    LlmAnalysis,
    LlmAuthenticationError,
    LlmProviderError,
    LlmTimeoutError,
    LlmUsage,
    LlmValidationError,
    validate_analysis_result_evidence,
)

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenRouterConfig:
    primary_model: str
    fallback_model: str | None = None
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_seconds: float = 30.0
    max_retries: int = 2
    max_retry_delay_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.primary_model.strip():
            raise ValueError("primary_model is required")
        if self.fallback_model is not None and not self.fallback_model.strip():
            raise ValueError("fallback_model cannot be blank")
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")

    @property
    def models(self) -> tuple[str, ...]:
        if self.fallback_model is None or self.fallback_model == self.primary_model:
            return (self.primary_model,)
        return (self.primary_model, self.fallback_model)


class OpenRouterAdapter:
    """OpenRouter chat-completions adapter with deterministic model fallback."""

    provider_name = "openrouter"
    provider_label = "OpenRouter"

    def __init__(
        self,
        api_key: str,
        config: OpenRouterConfig,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if not api_key.strip():
            raise ValueError(f"{self.provider_label} API key is required")
        self._api_key = api_key
        self._config = config
        self._client = client or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
        )
        self._owns_client = client is None
        self._sleep = sleep

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
            self._owns_client = False

    async def analyze(self, request: AnalysisRequest) -> LlmAnalysis:
        attempted: list[str] = []
        last_error: LlmProviderError | LlmTimeoutError | LlmValidationError | None = None
        for model in self._config.models:
            attempted.append(model)
            try:
                return await self._analyze_with_model(request, model)
            except LlmAuthenticationError:
                raise
            except (LlmProviderError, LlmTimeoutError, LlmValidationError) as error:
                last_error = error
                if isinstance(error, LlmProviderError) and not error.retryable:
                    continue

        attempted_models = tuple(attempted)
        if isinstance(last_error, LlmValidationError):
            raise LlmValidationError(
                str(last_error),
                attempted_models=attempted_models,
            ) from last_error
        if isinstance(last_error, LlmTimeoutError):
            raise LlmTimeoutError(
                f"all configured models timed out: {', '.join(attempted_models)}"
            ) from last_error
        if isinstance(last_error, LlmProviderError):
            raise LlmProviderError(
                last_error.code,
                "all configured models failed",
                retryable=last_error.retryable,
                attempted_models=attempted_models,
                status_code=last_error.status_code,
            ) from last_error
        raise LlmProviderError(
            "llm_provider_failed",
            "no configured model produced a result",
            retryable=False,
            attempted_models=attempted_models,
        )

    async def generate_passport(
        self,
        findings_text: str,
        outcome: str,
        language: str,
    ) -> str:
        """Generate a localized Context Passport explanation via the configured provider."""
        system_prompt = (
            "You are a technical communication specialist. "
            "Your job is to explain code alignment findings to a specific stakeholder "
            "in their preferred language. Be concise, factual, and cite the evidence. "
            "Structure your response as: what changed, why it matters, what evidence supports "
            "the finding, and what action is recommended."
        )
        user_prompt = (
            f"Generate a Context Passport in language: {language}\n\n"
            f"Alignment outcome: {outcome}\n\n"
            f"Findings:\n{findings_text}\n\n"
            f"Explain this to the stakeholder in {language}. Include:\n"
            f"1. What was found\n"
            f"2. Why it matters\n"
            f"3. Recommended next steps\n"
            f"4. Key evidence citations"
        )
        body: dict[str, Any] = {
            "model": self._config.primary_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "stream": False,
        }
        try:
            response = await self._client.post(
                "/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=self._config.timeout_seconds,
            )
            if response.status_code >= 400:
                return f"Alignment outcome: {outcome}. {findings_text}"
            payload = response.json()
            choices = payload.get("choices", [])
            if choices and isinstance(choices[0], dict):
                message = choices[0].get("message", {})
                content = message.get("content", "")
                if content.strip():
                    return content.strip()
        except Exception:
            pass
        return f"Alignment outcome: {outcome}. {findings_text}"

    async def _analyze_with_model(
        self,
        request: AnalysisRequest,
        model: str,
    ) -> LlmAnalysis:
        response = await self._request_model(request, model)
        payload = self._json_object(response)
        self._raise_embedded_error(payload)

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LlmValidationError(f"{self.provider_label} response has no completion choice")
        choice = choices[0]
        if choice.get("finish_reason") == "error" or isinstance(choice.get("error"), dict):
            raise LlmProviderError(
                "llm_generation_failed",
                f"{self.provider_label} generation failed",
                retryable=True,
                status_code=502,
            )
        message = choice.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise LlmValidationError(f"{self.provider_label} completion content is missing")
        try:
            raw_result = json.loads(message["content"])
        except json.JSONDecodeError as error:
            raise LlmValidationError(
                f"{self.provider_label} completion is not valid JSON"
            ) from error
        try:
            result = AnalysisResult.model_validate(raw_result)
        except ValidationError as error:
            raise LlmValidationError(
                f"{self.provider_label} completion does not match AnalysisResult"
            ) from error
        result = self._canonicalize_evidence_ids(result, request)
        validate_analysis_result_evidence(result, request.documents)

        actual_model = payload.get("model")
        if not isinstance(actual_model, str) or not actual_model.strip():
            actual_model = model
        return LlmAnalysis(
            run_id=request.stable_run_id,
            result=result,
            provider=self.provider_name,
            requested_model=model,
            actual_model=actual_model,
            prompt_version=request.prompt_version,
            input_hash=request.input_hash,
            usage=self._parse_usage(payload.get("usage")),
        )

    @staticmethod
    def _canonicalize_evidence_ids(
        result: AnalysisResult,
        request: AnalysisRequest,
    ) -> AnalysisResult:
        """Repair only evidence IDs proven by a unique URL and exact-quote match."""
        known_ids = {document.source_version_id for document in request.documents}
        payload = result.model_dump(mode="json")
        changed = False
        for collection_name in ("nodes", "findings", "edges"):
            collection = payload.get(collection_name)
            if not isinstance(collection, list):
                continue
            for item in collection:
                if not isinstance(item, dict):
                    continue
                evidence_set = item.get("evidence")
                if not isinstance(evidence_set, list):
                    continue
                for evidence in evidence_set:
                    if not isinstance(evidence, dict):
                        continue
                    source_version_id = evidence.get("source_version_id")
                    if source_version_id in known_ids:
                        continue
                    url = evidence.get("url")
                    quote = evidence.get("exact_quote")
                    if not isinstance(url, str) or not isinstance(quote, str):
                        continue
                    matches = [
                        document
                        for document in request.documents
                        if document.url == url and exact_quote_is_present(quote, document.content)
                    ]
                    if len(matches) == 1:
                        evidence["source_version_id"] = matches[0].source_version_id
                        changed = True
        return AnalysisResult.model_validate(payload) if changed else result

    async def _request_model(
        self,
        request: AnalysisRequest,
        model: str,
    ) -> httpx.Response:
        body: dict[str, Any] = {
            "model": model,
            "messages": build_messages(request),
            "temperature": 0,
            "stream": False,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "alignment_result",
                    "strict": True,
                    "schema": self._response_schema(),
                },
            },
        }
        body.update(self._provider_request_fields())
        for attempt in range(self._config.max_retries + 1):
            try:
                response = await self._client.post(
                    "/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=self._config.timeout_seconds,
                )
            except httpx.TimeoutException as error:
                if attempt < self._config.max_retries:
                    await self._sleep(min(float(2**attempt), self._config.max_retry_delay_seconds))
                    continue
                raise LlmTimeoutError() from error
            except httpx.RequestError as error:
                if attempt < self._config.max_retries:
                    await self._sleep(min(float(2**attempt), self._config.max_retry_delay_seconds))
                    continue
                raise LlmProviderError(
                    "llm_network_error",
                    f"{self.provider_label} network request failed",
                    retryable=True,
                ) from error

            if response.status_code < 400:
                return response
            retryable = response.status_code in {408, 429, 500, 502, 503, 504}
            if retryable and attempt < self._config.max_retries:
                await self._sleep(self._retry_delay(response, attempt))
                continue
            self._raise_http_error(response, retryable=retryable)

        raise LlmProviderError(
            "llm_provider_failed",
            f"{self.provider_label} request failed",
            retryable=True,
        )

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after is not None:
            try:
                return min(float(retry_after), self._config.max_retry_delay_seconds)
            except ValueError:
                pass
        return min(float(2**attempt), self._config.max_retry_delay_seconds)

    def _provider_request_fields(self) -> dict[str, Any]:
        return {"provider": {"require_parameters": True}}

    def _response_schema(self) -> dict[str, Any]:
        return AnalysisResult.model_json_schema()

    def _raise_http_error(self, response: httpx.Response, *, retryable: bool) -> None:
        if response.status_code == 401:
            raise LlmAuthenticationError()
        raise LlmProviderError(
            "llm_provider_failed",
            f"{self.provider_label} request failed with status {response.status_code}",
            retryable=retryable,
            status_code=response.status_code,
        )

    def _raise_embedded_error(self, payload: Mapping[str, Any]) -> None:
        error = payload.get("error")
        if not isinstance(error, Mapping):
            return
        code = error.get("code")
        status_code = code if isinstance(code, int) else None
        if status_code == 401:
            raise LlmAuthenticationError()
        retryable = status_code in {408, 429, 500, 502, 503, 504}
        raise LlmProviderError(
            "llm_generation_failed",
            f"{self.provider_label} returned a generation error",
            retryable=retryable,
            status_code=status_code,
        )

    def _json_object(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as error:
            raise LlmValidationError(f"{self.provider_label} response is not JSON") from error
        if not isinstance(payload, dict):
            raise LlmValidationError(f"{self.provider_label} response is not an object")
        return payload

    @staticmethod
    def _parse_usage(payload: object) -> LlmUsage:
        usage = payload if isinstance(payload, Mapping) else {}
        return LlmUsage(
            prompt_tokens=OpenRouterAdapter._nonnegative_int(usage.get("prompt_tokens")),
            completion_tokens=OpenRouterAdapter._nonnegative_int(usage.get("completion_tokens")),
            total_tokens=OpenRouterAdapter._nonnegative_int(usage.get("total_tokens")),
            cost=OpenRouterAdapter._optional_nonnegative_float(usage.get("cost")),
        )

    @staticmethod
    def _nonnegative_int(value: object) -> int:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    @staticmethod
    def _optional_nonnegative_float(value: object) -> float | None:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return float(value)
        return None
