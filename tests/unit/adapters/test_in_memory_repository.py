from datetime import UTC, datetime, timedelta

import pytest

from alignment_memory.adapters.memory import InMemoryRepository
from alignment_memory.domain import (
    Alignment,
    AlignmentOutcome,
    AppendOnlyViolation,
    EvidenceReference,
    EvidenceRole,
    Finding,
    Handshake,
    HandshakeResponse,
    Job,
    JobStatus,
    JobType,
    KnowledgeNode,
    KnowledgeNodeVersion,
    KnowledgeStatus,
    NodeType,
    Override,
    OverrideType,
    Source,
    SourceVersion,
)
from alignment_memory.ports import PersistenceRepository

NOW = datetime(2026, 8, 3, tzinfo=UTC)


def _source() -> Source:
    return Source(
        id="source-1",
        repository_id="repo-1",
        source_type="markdown",
        external_id="docs/prd.md",
        url="https://github.com/gyutaetae/harness/blob/main/docs/prd.md",
    )


def _source_version(*, version_id: str = "source-version-1") -> SourceVersion:
    return SourceVersion(
        id=version_id,
        source_id="source-1",
        external_version="abc123",
        content="Browser extensions are excluded from the MVP.",
        content_hash="hash-1",
        occurred_at=NOW,
        ingested_at=NOW,
    )


def _evidence() -> EvidenceReference:
    return EvidenceReference(
        source_version_id="source-version-1",
        url=_source().url,
        exact_quote="Browser extensions are excluded from the MVP.",
        role=EvidenceRole.CONTRADICTS,
        verified=True,
    )


def _job() -> Job:
    return Job(
        id="job-1",
        repository_id="repo-1",
        event_key="pull_request:42:abc123",
        job_type=JobType.PR_ANALYSIS,
        status=JobStatus.QUEUED,
        progress=0,
        created_at=NOW,
        updated_at=NOW,
        head_sha="abc123",
    )


def _alignment() -> Alignment:
    finding = Finding(
        id="finding-1",
        analysis_id="analysis-1",
        finding_type=AlignmentOutcome.DIRECT_CONFLICT,
        explanation="The PR adds the excluded browser extension.",
        recommended_action="Remove the extension or supersede the decision.",
        evidence=(_evidence(),),
        target_node_id="node-1",
        target_node_type=NodeType.DECISION,
        target_node_status=KnowledgeStatus.ACTIVE,
        contradicts=True,
    )
    return Alignment(
        id="analysis-1",
        repository_id="repo-1",
        pr_number=42,
        head_sha="abc123",
        knowledge_revision=1,
        outcome=AlignmentOutcome.DIRECT_CONFLICT,
        findings=(finding,),
        ai_run_id="ai-run-1",
        created_at=NOW,
    )


@pytest.fixture
def repository() -> InMemoryRepository:
    return InMemoryRepository()


def test_in_memory_adapter_satisfies_persistence_protocol(
    repository: InMemoryRepository,
) -> None:
    assert isinstance(repository, PersistenceRepository)


@pytest.mark.asyncio
async def test_source_identity_is_immutable_and_versions_are_idempotent(
    repository: InMemoryRepository,
) -> None:
    source = _source()
    version = _source_version()

    assert await repository.add_source(source) is source
    assert await repository.add_source(source) is source
    retry_source = Source(
        id="source-retry",
        repository_id=source.repository_id,
        source_type=source.source_type,
        external_id=source.external_id,
        url=source.url,
    )
    assert await repository.add_source(retry_source) is source
    assert await repository.append_source_version(version) is version

    retried = _source_version(version_id="source-version-retry")
    assert await repository.append_source_version(retried) is version
    assert await repository.list_source_versions(source.id) == (version,)

    changed = Source(
        id=source.id,
        repository_id=source.repository_id,
        source_type=source.source_type,
        external_id=source.external_id,
        url="https://example.invalid/changed",
    )
    with pytest.raises(AppendOnlyViolation, match="different data"):
        await repository.add_source(changed)


@pytest.mark.asyncio
async def test_knowledge_versions_append_and_active_context_selects_latest(
    repository: InMemoryRepository,
) -> None:
    node = KnowledgeNode(
        id="node-1",
        repository_id="repo-1",
        node_type=NodeType.DECISION,
        logical_key="decision:no-browser-extension",
    )
    first = KnowledgeNodeVersion(
        id="node-version-1",
        node_id=node.id,
        revision=1,
        title="No browser extension",
        summary="The MVP excludes browser extensions.",
        status=KnowledgeStatus.ACTIVE,
        created_by="worker",
        created_at=NOW,
        evidence=(_evidence(),),
    )
    second = KnowledgeNodeVersion(
        id="node-version-2",
        node_id=node.id,
        revision=2,
        title="No browser extension",
        summary="The exclusion remains active after review.",
        status=KnowledgeStatus.ACTIVE,
        created_by="profile-1",
        created_at=NOW + timedelta(minutes=1),
        evidence=(_evidence(),),
        supersedes_version_id=first.id,
    )

    await repository.add_knowledge_node(node)
    await repository.append_knowledge_node_version(first)
    await repository.append_knowledge_node_version(second)

    assert await repository.get_active_context("repo-1") == (second,)
    assert await repository.get_active_context("repo-1", revision=1) == (first,)
    stored_node = await repository.add_knowledge_node(node)
    assert stored_node.current_version_id == second.id

    invalid_revision = KnowledgeNodeVersion(
        id="node-version-4",
        node_id=node.id,
        revision=4,
        title="Skipped revision",
        summary="This must not be stored.",
        status=KnowledgeStatus.ACTIVE,
        created_by="worker",
        created_at=NOW,
        evidence=(_evidence(),),
        supersedes_version_id=second.id,
    )
    with pytest.raises(AppendOnlyViolation, match="revision must be 3"):
        await repository.append_knowledge_node_version(invalid_revision)


@pytest.mark.asyncio
async def test_job_compare_and_set_rejects_stale_expected_state(
    repository: InMemoryRepository,
) -> None:
    job = await repository.create_job(_job())

    fetching = await repository.compare_and_set_job(
        job.id,
        JobStatus.QUEUED,
        JobStatus.FETCHING,
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert fetching is not None
    assert fetching.status is JobStatus.FETCHING

    stale = await repository.compare_and_set_job(
        job.id,
        JobStatus.QUEUED,
        JobStatus.FETCHING,
        occurred_at=NOW + timedelta(seconds=2),
    )
    assert stale is None
    assert await repository.get_job(job.id) == fetching
    retried_create = Job(
        id="job-retry",
        repository_id=job.repository_id,
        event_key=job.event_key,
        job_type=job.job_type,
        status=JobStatus.QUEUED,
        progress=0,
        created_at=NOW + timedelta(seconds=3),
        updated_at=NOW + timedelta(seconds=3),
        head_sha=job.head_sha,
    )
    assert await repository.create_job(retried_create) == fetching


@pytest.mark.asyncio
async def test_validated_result_is_persisted_once_per_job_and_event(
    repository: InMemoryRepository,
) -> None:
    await repository.create_job(_job())
    alignment = _alignment()

    assert await repository.persist_validated_result("job-1", alignment) is alignment
    assert await repository.persist_validated_result("job-1", alignment) is alignment
    assert await repository.get_result_for_job("job-1") is alignment

    conflicting = Alignment(
        id=alignment.id,
        repository_id=alignment.repository_id,
        pr_number=alignment.pr_number,
        head_sha=alignment.head_sha,
        knowledge_revision=alignment.knowledge_revision,
        outcome=AlignmentOutcome.ALIGNED,
        findings=(),
        ai_run_id=alignment.ai_run_id,
        created_at=alignment.created_at,
    )
    with pytest.raises(AppendOnlyViolation, match="different data"):
        await repository.persist_validated_result("job-1", conflicting)


@pytest.mark.asyncio
async def test_handshake_and_override_are_append_only(
    repository: InMemoryRepository,
) -> None:
    await repository.create_job(_job())
    await repository.persist_validated_result("job-1", _alignment())
    handshake = Handshake(
        id="handshake-1",
        analysis_id="analysis-1",
        profile_id="profile-1",
        response=HandshakeResponse.NEEDS_CLARIFICATION,
        message="Please clarify the extension boundary.",
        source_language="en",
        created_at=NOW,
    )
    override = Override(
        id="override-1",
        target_type="finding",
        target_id="finding-1",
        override_type=OverrideType.FALSE_POSITIVE,
        reason="The pull request changes only the desktop dashboard.",
        actor_profile_id="profile-1",
        created_at=NOW,
    )

    await repository.append_handshake(handshake)
    await repository.append_override(override)
    assert await repository.list_handshakes("analysis-1") == (handshake,)
    assert await repository.list_overrides("finding", "finding-1") == (override,)

    with pytest.raises(AppendOnlyViolation, match="handshake"):
        await repository.append_handshake(handshake)
    with pytest.raises(AppendOnlyViolation, match="override"):
        await repository.append_override(override)
