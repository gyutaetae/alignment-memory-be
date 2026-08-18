import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alignment_memory.adapters.github import FixtureGitHubAdapter
from alignment_memory.adapters.memory import InMemoryRepository
from alignment_memory.adapters.openrouter import FixtureOpenRouterAdapter
from alignment_memory.application import AlignmentAnalysisService, AnalyzePullRequestCommand
from alignment_memory.domain import Job, JobStatus, JobType
from alignment_memory.ports import (
    CollectedSource,
    GitHubRepositoryRef,
    GitHubSourceType,
    SourceBatch,
)

FIXTURE_DIR = Path(__file__).parent / "analysis"
NOW = datetime(2026, 8, 3, 12, tzinfo=UTC)


def _source(repository_id: str, payload: dict[str, str]) -> CollectedSource:
    source_type = (
        GitHubSourceType.PULL_REQUEST if "/pull/" in payload["url"] else GitHubSourceType.MARKDOWN
    )
    return CollectedSource(
        source_id=f"source:{payload['id']}",
        source_version_id=payload["id"],
        repository_id=repository_id,
        source_type=source_type,
        external_id=payload["id"],
        external_version="fixture-v1",
        url=payload["url"],
        content=payload["content"],
        content_hash=hashlib.sha256(payload["content"].encode()).hexdigest(),
        occurred_at=NOW,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixture_path",
    sorted(FIXTURE_DIR.glob("*.json")),
    ids=lambda path: path.stem,
)
async def test_alignment_fixture_runs_through_application_service(fixture_path: Path) -> None:
    fixture = json.loads(fixture_path.read_text())
    repository_id = f"repo:{fixture_path.stem}"
    pr_number = fixture["pull_request"]["number"]
    head_sha = fixture["pull_request"]["head"]["sha"]
    job_id = f"job:{fixture_path.stem}"
    collected = tuple(
        _source(repository_id, source_version) for source_version in fixture["source_versions"]
    )

    repository = InMemoryRepository()
    await repository.create_job(
        Job(
            id=job_id,
            repository_id=repository_id,
            event_key=f"pull_request:{pr_number}:{head_sha}",
            job_type=JobType.PR_ANALYSIS,
            status=JobStatus.QUEUED,
            progress=0,
            head_sha=head_sha,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    github = FixtureGitHubAdapter(
        pr_batches={
            (pr_number, head_sha): SourceBatch(
                sources=collected,
                baseline_commit_sha=head_sha,
            )
        },
        allowed_actors=frozenset({"fixture-user"}),
    )
    llm = FixtureOpenRouterAdapter([fixture["analysis"]])
    service = AlignmentAnalysisService(
        github=github,
        llm=llm,
        repository=repository,
        clock=lambda: NOW,
    )

    alignment = await service.analyze_pull_request(
        AnalyzePullRequestCommand(
            job_id=job_id,
            repository=GitHubRepositoryRef(
                repository_id=repository_id,
                owner="gyutaetae",
                name="harness",
                installation_id=1,
            ),
            pr_number=pr_number,
            head_sha=head_sha,
            knowledge_revision=1,
            prompt_version="fixture-v1",
            actor_login="fixture-user",
        )
    )

    assert alignment.outcome.value == fixture["expected_outcome"]
    assert all(evidence.verified for finding in alignment.findings for evidence in finding.evidence)
