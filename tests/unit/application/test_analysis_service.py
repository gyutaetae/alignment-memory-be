import hashlib
from datetime import UTC, datetime

import pytest

from alignment_memory.adapters.github import FixtureGitHubAdapter
from alignment_memory.adapters.memory import InMemoryRepository
from alignment_memory.adapters.openrouter import FixtureOpenRouterAdapter
from alignment_memory.application import AlignmentAnalysisService, AnalyzePullRequestCommand
from alignment_memory.domain import (
    AiRun,
    AlignmentOutcome,
    Job,
    JobStatus,
    JobType,
    ValidationStatus,
)
from alignment_memory.ports import (
    AnalysisDocument,
    AnalysisRequest,
    CollectedSource,
    GitHubRepositoryRef,
    GitHubSourceType,
    LlmProviderError,
    LlmValidationError,
    SourceBatch,
)

NOW = datetime(2026, 8, 3, 12, tzinfo=UTC)
HEAD = "a" * 40


def _collected_source(
    *,
    source_id: str,
    version_id: str,
    source_type: GitHubSourceType,
    external_id: str,
    url: str,
    content: str,
) -> CollectedSource:
    return CollectedSource(
        source_id=source_id,
        source_version_id=version_id,
        repository_id="repo-1",
        source_type=source_type,
        external_id=external_id,
        external_version=HEAD,
        url=url,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        occurred_at=NOW,
    )


def _sources() -> tuple[CollectedSource, CollectedSource]:
    decision = _collected_source(
        source_id="source-decision",
        version_id="version-decision",
        source_type=GitHubSourceType.MARKDOWN,
        external_id="docs/adr.md",
        url="https://github.com/owner/repo/blob/main/docs/adr.md",
        content="Browser extensions are excluded from the MVP.",
    )
    pull = _collected_source(
        source_id="source-pr",
        version_id="version-pr",
        source_type=GitHubSourceType.PULL_REQUEST,
        external_id="pr:42",
        url="https://github.com/owner/repo/pull/42",
        content="Add extension\n\nThis PR adds browser extension sync.",
    )
    return decision, pull


def _analysis(*, quote: str = "Browser extensions are excluded from the MVP.") -> dict[str, object]:
    return {
        "outcome": "direct_conflict",
        "nodes": [],
        "findings": [
            {
                "finding_type": "direct_conflict",
                "target_node_logical_key": "decision:no-browser-extension",
                "target_node_type": "decision",
                "target_node_status": "active",
                "contradicts": True,
                "uncertain": False,
                "explanation": "The PR contradicts the active browser boundary.",
                "recommended_action": "Remove the extension or supersede the decision.",
                "evidence": [
                    {
                        "source_version_id": "version-decision",
                        "url": "https://github.com/owner/repo/blob/main/docs/adr.md",
                        "exact_quote": quote,
                        "role": "contradicts",
                    },
                    {
                        "source_version_id": "version-pr",
                        "url": "https://github.com/owner/repo/pull/42",
                        "exact_quote": "This PR adds browser extension sync.",
                        "role": "supports",
                    },
                ],
            }
        ],
        "edges": [],
    }


def _repository_ref() -> GitHubRepositoryRef:
    return GitHubRepositoryRef(
        repository_id="repo-1",
        owner="owner",
        name="repo",
        installation_id=7,
    )


def _job() -> Job:
    return Job(
        id="job-1",
        repository_id="repo-1",
        event_key=f"pull_request:42:{HEAD}",
        job_type=JobType.PR_ANALYSIS,
        status=JobStatus.QUEUED,
        progress=0,
        head_sha=HEAD,
        created_at=NOW,
        updated_at=NOW,
    )


def _command(decision: CollectedSource) -> AnalyzePullRequestCommand:
    return AnalyzePullRequestCommand(
        job_id="job-1",
        repository=_repository_ref(),
        pr_number=42,
        head_sha=HEAD,
        knowledge_revision=1,
        prompt_version="alignment-v1",
        actor_login="member",
        context_sources=(decision,),
    )


@pytest.mark.asyncio
async def test_service_validates_deterministic_outcome_and_is_idempotent() -> None:
    decision, pull = _sources()
    repository = InMemoryRepository()
    await repository.create_job(_job())
    github = FixtureGitHubAdapter(
        pr_batches={(42, HEAD): SourceBatch(sources=(pull,), baseline_commit_sha=HEAD)},
        allowed_actors=frozenset({"member"}),
    )
    llm = FixtureOpenRouterAdapter(
        [_analysis()],
        requested_model="configured-primary",
        actual_model="provider-model",
    )
    service = AlignmentAnalysisService(
        github=github,
        llm=llm,
        repository=repository,
        clock=lambda: NOW,
    )

    first = await service.analyze_pull_request(_command(decision))
    second = await service.analyze_pull_request(_command(decision))

    assert first is second
    assert first.outcome is AlignmentOutcome.DIRECT_CONFLICT
    assert all(evidence.verified for evidence in first.findings[0].evidence)
    assert len(llm.requests) == 1
    assert github.pr_calls == [(42, HEAD)]
    stored_run = await repository.get_ai_run(
        "job-1",
        llm.requests[0].input_hash,
        "alignment-v1",
    )
    assert stored_run is not None
    assert stored_run.requested_model == "configured-primary"
    assert stored_run.actual_model == "provider-model"


@pytest.mark.asyncio
async def test_service_rejects_fabricated_quote_before_side_effect_result() -> None:
    decision, pull = _sources()
    repository = InMemoryRepository()
    await repository.create_job(_job())
    github = FixtureGitHubAdapter(
        pr_batches={(42, HEAD): SourceBatch(sources=(pull,), baseline_commit_sha=HEAD)},
        allowed_actors=frozenset({"member"}),
    )
    llm = FixtureOpenRouterAdapter(
        [_analysis(quote="Fabricated exclusion quote")],
        validate_evidence=False,
    )
    service = AlignmentAnalysisService(
        github=github,
        llm=llm,
        repository=repository,
        clock=lambda: NOW,
    )

    with pytest.raises(LlmValidationError, match="evidence quote"):
        await service.analyze_pull_request(_command(decision))

    assert await repository.get_result_for_job("job-1") is None
    assert (
        await repository.get_ai_run(
            "job-1",
            llm.requests[0].input_hash,
            "alignment-v1",
        )
        is None
    )


@pytest.mark.asyncio
async def test_service_reuses_valid_run_after_partial_persistence() -> None:
    decision, pull = _sources()
    repository = InMemoryRepository()
    await repository.create_job(_job())
    request = AnalysisRequest(
        job_id="job-1",
        repository_id="repo-1",
        pr_number=42,
        head_sha=HEAD,
        knowledge_revision=1,
        prompt_version="alignment-v1",
        documents=tuple(
            AnalysisDocument(
                source_version_id=source.source_version_id,
                source_type=source.source_type.value,
                url=source.url,
                content=source.content,
            )
            for source in (decision, pull)
        ),
    )
    await repository.persist_ai_run(
        AiRun(
            id=request.stable_run_id,
            job_id="job-1",
            provider="fixture",
            requested_model="configured-primary",
            actual_model="provider-model",
            prompt_version=request.prompt_version,
            input_hash=request.input_hash,
            output_json=_analysis(),
            validation_status=ValidationStatus.VALID,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            created_at=NOW,
            completed_at=NOW,
        )
    )
    github = FixtureGitHubAdapter(
        pr_batches={(42, HEAD): SourceBatch(sources=(pull,), baseline_commit_sha=HEAD)},
        allowed_actors=frozenset({"member"}),
    )
    llm = FixtureOpenRouterAdapter(
        [LlmProviderError("should_not_call", "LLM should not be called", retryable=False)]
    )
    service = AlignmentAnalysisService(
        github=github,
        llm=llm,
        repository=repository,
        clock=lambda: NOW,
    )

    alignment = await service.analyze_pull_request(_command(decision))

    assert alignment.outcome is AlignmentOutcome.DIRECT_CONFLICT
    assert llm.requests == []
