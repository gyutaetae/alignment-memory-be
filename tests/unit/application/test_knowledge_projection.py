from datetime import UTC, datetime

import pytest

from alignment_memory.adapters.memory import InMemoryRepository
from alignment_memory.application import (
    ProjectionDocument,
    ProjectKnowledgeCommand,
    ProjectKnowledgeService,
)
from alignment_memory.contracts import AnalysisResult
from alignment_memory.domain import AiRun, Job, JobStatus, JobType, ValidationStatus
from alignment_memory.ports import RepositoryRecord

NOW = datetime(2026, 8, 20, tzinfo=UTC)
REPOSITORY_ID = "10000000-0000-0000-0000-000000000001"
JOB_ID = "30000000-0000-0000-0000-000000000001"
HEAD_SHA = "a" * 40
SOURCE_ID = "20000000-0000-0000-0000-000000000001"
SOURCE_VERSION_ID = "20000000-0000-0000-0000-000000000002"
SOURCE_URL = "https://github.com/acme/demo/blob/main/docs/decision.md"
QUOTE = "원문 사용자 메시지는 외부 분석 서비스에 저장하지 않는다."


def _analysis() -> AnalysisResult:
    evidence = {
        "source_version_id": SOURCE_VERSION_ID,
        "url": SOURCE_URL,
        "exact_quote": QUOTE,
        "role": "supports",
    }
    return AnalysisResult.model_validate(
        {
            "outcome": "aligned",
            "nodes": [
                {
                    "logical_key": "goal:privacy-safe-collaboration",
                    "node_type": "goal",
                    "title": "Privacy-safe cross-border collaboration",
                    "summary": "Keep shared debugging evidence free of raw messages.",
                    "status": "active",
                    "evidence": [evidence],
                },
                {
                    "logical_key": "decision:no-raw-message-logging",
                    "node_type": "decision",
                    "title": "Do not log raw user messages",
                    "summary": "Store only anonymized aggregate metrics.",
                    "status": "active",
                    "evidence": [evidence],
                },
            ],
            "findings": [],
            "edges": [
                {
                    "from_node_logical_key": "goal:privacy-safe-collaboration",
                    "to_node_logical_key": "decision:no-raw-message-logging",
                    "relation_type": "constrains",
                    "evidence": [evidence],
                }
            ],
        }
    )


@pytest.mark.asyncio
async def test_projection_persists_sources_knowledge_and_revision_idempotently() -> None:
    repository = InMemoryRepository()
    await repository.seed_repository_record(
        RepositoryRecord(
            id=REPOSITORY_ID,
            github_repository_id=1,
            github_installation_id=2,
            owner="acme",
            name="demo",
            default_branch="main",
            main_commit_sha=HEAD_SHA,
        )
    )
    await repository.create_job(
        Job(
            id=JOB_ID,
            repository_id=REPOSITORY_ID,
            event_key="initial-sync",
            job_type=JobType.INITIAL_SYNC,
            status=JobStatus.PERSISTING,
            progress=85,
            head_sha=HEAD_SHA,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    run = AiRun(
        id="60000000-0000-0000-0000-000000000001",
        job_id=JOB_ID,
        provider="openai",
        requested_model="gpt-4.1-mini",
        actual_model="gpt-4.1-mini-2025-04-14",
        prompt_version="test-v1",
        input_hash="f" * 64,
        output_json=_analysis().model_dump(mode="json"),
        validation_status=ValidationStatus.VALID,
        usage={"total_tokens": 100},
        created_at=NOW,
        completed_at=NOW,
    )
    command = ProjectKnowledgeCommand(
        job_id=JOB_ID,
        repository_id=REPOSITORY_ID,
        event_key="initial-sync:1:" + HEAD_SHA,
        head_sha=HEAD_SHA,
        expected_revision=0,
        run=run,
        documents=(
            ProjectionDocument(
                source_id=SOURCE_ID,
                source_version_id=SOURCE_VERSION_ID,
                source_type="markdown",
                external_id="docs/decision.md",
                external_version=HEAD_SHA,
                url=SOURCE_URL,
                content=QUOTE,
                content_hash="e" * 64,
                occurred_at=NOW,
            ),
        ),
        analysis=_analysis(),
        created_at=NOW,
    )

    service = ProjectKnowledgeService(repository)
    first = await service.apply(command)
    second = await service.apply(command)

    assert first == second
    assert first.knowledge_revision == 1
    assert first.baseline_commit_sha == HEAD_SHA
    assert await repository.count_sources(REPOSITORY_ID) == 1
    assert len(await repository.list_knowledge_snapshots(REPOSITORY_ID)) == 2
    assert len(await repository.list_knowledge_edges(REPOSITORY_ID)) == 1
