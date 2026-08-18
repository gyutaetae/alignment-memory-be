from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import NAMESPACE_URL, uuid5

from alignment_memory.contracts.analysis import AnalysisResult
from alignment_memory.domain import exact_quote_is_present


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalysisDocument:
    source_version_id: str
    source_type: str
    url: str
    content: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalysisRequest:
    job_id: str
    repository_id: str
    pr_number: int
    head_sha: str
    knowledge_revision: int
    prompt_version: str
    documents: tuple[AnalysisDocument, ...]
    context_is_sufficient: bool = True

    @property
    def input_hash(self) -> str:
        canonical = {
            "repository_id": self.repository_id,
            "pr_number": self.pr_number,
            "head_sha": self.head_sha,
            "knowledge_revision": self.knowledge_revision,
            "context_is_sufficient": self.context_is_sufficient,
            "documents": [
                {
                    "source_version_id": document.source_version_id,
                    "source_type": document.source_type,
                    "url": document.url,
                    "content": document.content,
                }
                for document in self.documents
            ],
        }
        payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    @property
    def stable_run_id(self) -> str:
        return str(
            uuid5(
                NAMESPACE_URL,
                f"alignment-memory:ai-run:{self.job_id}:{self.prompt_version}:{self.input_hash}",
            )
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class LlmUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float | None = None

    def as_dict(self) -> dict[str, int | float]:
        usage: dict[str, int | float] = {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }
        if self.cost is not None:
            usage["cost"] = self.cost
        return usage


@dataclass(frozen=True, slots=True, kw_only=True)
class LlmAnalysis:
    run_id: str
    result: AnalysisResult
    provider: str
    requested_model: str
    actual_model: str
    prompt_version: str
    input_hash: str
    usage: LlmUsage

    @property
    def output_json(self) -> dict[str, object]:
        return self.result.model_dump(mode="json")


class LlmAdapterError(RuntimeError):
    """Safe, typed failure exposed by an LLM adapter."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        attempted_models: tuple[str, ...] = (),
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.attempted_models = attempted_models
        self.status_code = status_code


class LlmAuthenticationError(LlmAdapterError):
    def __init__(self, message: str = "LLM authentication failed") -> None:
        super().__init__("llm_authentication_failed", message, retryable=False)


class LlmProviderError(LlmAdapterError):
    pass


class LlmTimeoutError(LlmAdapterError):
    def __init__(self, message: str = "LLM request timed out") -> None:
        super().__init__("llm_timeout", message, retryable=True)


class LlmValidationError(LlmAdapterError):
    def __init__(
        self,
        message: str = "LLM response failed schema or evidence validation",
        *,
        attempted_models: tuple[str, ...] = (),
    ) -> None:
        super().__init__(
            "llm_validation_failed",
            message,
            retryable=False,
            attempted_models=attempted_models,
        )


def validate_analysis_result_evidence(
    result: AnalysisResult,
    documents: tuple[AnalysisDocument, ...],
) -> None:
    indexed = {document.source_version_id: document for document in documents}
    evidence_sets = [node.evidence for node in result.nodes]
    evidence_sets.extend(finding.evidence for finding in result.findings)
    evidence_sets.extend(edge.evidence for edge in result.edges)

    for evidence_set in evidence_sets:
        for evidence in evidence_set:
            document = indexed.get(evidence.source_version_id)
            if document is None:
                raise LlmValidationError("evidence references an unknown source version")
            if str(evidence.url) != document.url:
                raise LlmValidationError("evidence URL does not match the stored source")
            if not exact_quote_is_present(evidence.exact_quote, document.content):
                raise LlmValidationError("evidence quote is not present in the stored source")


@runtime_checkable
class LlmPort(Protocol):
    async def analyze(self, request: AnalysisRequest) -> LlmAnalysis: ...
