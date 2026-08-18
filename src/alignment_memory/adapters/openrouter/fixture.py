from __future__ import annotations

import json
from collections.abc import Sequence

from pydantic import ValidationError

from alignment_memory.contracts.analysis import AnalysisResult
from alignment_memory.ports.llm import (
    AnalysisRequest,
    LlmAdapterError,
    LlmAnalysis,
    LlmUsage,
    LlmValidationError,
    validate_analysis_result_evidence,
)

FixtureResult = AnalysisResult | dict[str, object] | str | LlmAdapterError


class FixtureOpenRouterAdapter:
    """Deterministic, network-free LLM adapter for fixture mode."""

    def __init__(
        self,
        responses: Sequence[FixtureResult],
        *,
        requested_model: str = "fixture-primary",
        actual_model: str = "fixture-model",
        usage: LlmUsage | None = None,
        validate_evidence: bool = True,
    ) -> None:
        if not responses:
            raise ValueError("at least one fixture response is required")
        self._responses = list(responses)
        self._requested_model = requested_model
        self._actual_model = actual_model
        self._usage = usage or LlmUsage()
        self._validate_evidence = validate_evidence
        self.requests: list[AnalysisRequest] = []

    async def analyze(self, request: AnalysisRequest) -> LlmAnalysis:
        self.requests.append(request)
        response = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        if isinstance(response, LlmAdapterError):
            raise response
        try:
            raw = json.loads(response) if isinstance(response, str) else response
            result = raw if isinstance(raw, AnalysisResult) else AnalysisResult.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as error:
            raise LlmValidationError("fixture response does not match AnalysisResult") from error
        if self._validate_evidence:
            validate_analysis_result_evidence(result, request.documents)
        return LlmAnalysis(
            run_id=request.stable_run_id,
            result=result,
            provider="fixture",
            requested_model=self._requested_model,
            actual_model=self._actual_model,
            prompt_version=request.prompt_version,
            input_hash=request.input_hash,
            usage=self._usage,
        )
