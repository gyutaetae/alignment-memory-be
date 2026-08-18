from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import Request

from alignment_memory.adapters.github import (
    FixtureGitHubAdapter,
    GitHubAdapterConfig,
    GitHubAppAdapter,
    GitHubAppCredentials,
)
from alignment_memory.adapters.memory import InMemoryRepository
from alignment_memory.adapters.openrouter import (
    FixtureOpenRouterAdapter,
    OpenRouterAdapter,
    OpenRouterConfig,
)
from alignment_memory.adapters.postgres import PostgresRepository
from alignment_memory.contracts.analysis import AnalysisResult
from alignment_memory.domain import (
    AiRun,
    Alignment,
    AlignmentOutcome,
    ContextPassport,
    EvidenceReference,
    EvidenceRole,
    Finding,
    Job,
    JobStatus,
    JobType,
    KnowledgeEdge,
    KnowledgeNode,
    KnowledgeNodeVersion,
    KnowledgeStatus,
    NodeType,
    Source,
    SourceVersion,
    ValidationStatus,
)
from alignment_memory.interfaces.api.errors import ApiError
from alignment_memory.ports import (
    GitHubPort,
    LlmPort,
    MembershipRecord,
    RepositoryRecord,
)
from alignment_memory.settings import Settings

FIXTURE_PROFILE_ID = "00000000-0000-0000-0000-000000000001"
FIXTURE_READER_ID = "00000000-0000-0000-0000-000000000002"
FIXTURE_OUTSIDER_ID = "00000000-0000-0000-0000-000000000003"
FIXTURE_REPOSITORY_ID = "10000000-0000-0000-0000-000000000001"
FIXTURE_ALIGNMENT_ID = "40000000-0000-0000-0000-000000000001"
FIXTURE_JOB_ID = "30000000-0000-0000-0000-000000000001"
FIXTURE_SOURCE_VERSION_ID = "20000000-0000-0000-0000-000000000002"
FIXTURE_HEAD_SHA = "a" * 40
FIXTURE_MAIN_SHA = "b" * 40

RepositoryAdapter = InMemoryRepository | PostgresRepository


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredOperation:
    body_digest: str
    status_code: int
    payload: dict[str, Any]


class IdempotencyRegistry:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._operations: dict[tuple[str, str], StoredOperation] = {}

    async def get(
        self,
        scope: str,
        key: str,
        body_digest: str,
    ) -> StoredOperation | None:
        async with self._lock:
            operation = self._operations.get((scope, key))
            if operation is not None and operation.body_digest != body_digest:
                raise ApiError(
                    status_code=409,
                    code="idempotency_conflict",
                    message="Idempotency-Key was already used with a different request",
                )
            return operation

    async def store(
        self,
        scope: str,
        key: str,
        operation: StoredOperation,
    ) -> None:
        async with self._lock:
            self._operations.setdefault((scope, key), operation)


class AppContainer:
    def __init__(self, settings: Settings) -> None:
        settings.validate_runtime()
        self.settings = settings
        self.idempotency = IdempotencyRegistry()
        self.repository: RepositoryAdapter | None = None
        self.github: GitHubPort
        self.llm: LlmPort | None
        self._started = False

        if settings.app_mode == "fixture":
            self.repository = InMemoryRepository()
            self.github = FixtureGitHubAdapter()
            self.llm = FixtureOpenRouterAdapter(
                [
                    AnalysisResult(
                        outcome=AlignmentOutcome.ALIGNED,
                        nodes=(),
                        findings=(),
                        edges=(),
                    )
                ]
            )
            return

        app_id = self._required(settings.github_app_id, "GITHUB_APP_ID")
        private_key = self._required(
            settings.github_app_private_key,
            "GITHUB_APP_PRIVATE_KEY",
        )
        self._required(settings.database_url, "DATABASE_URL")
        self.github = GitHubAppAdapter(
            GitHubAppCredentials(app_id=app_id, private_key=private_key),
            config=GitHubAdapterConfig(
                api_base_url=settings.github_api_base_url,
                timeout_seconds=settings.github_api_timeout_seconds,
                max_retries=settings.github_api_max_retries,
                sync_workflow=settings.github_sync_workflow,
            ),
        )
        self.llm = (
            OpenRouterAdapter(
                settings.openrouter_api_key,
                OpenRouterConfig(
                    primary_model=settings.openrouter_primary_model,
                    fallback_model=settings.openrouter_fallback_model,
                    base_url=settings.openrouter_base_url,
                    timeout_seconds=settings.openrouter_timeout_seconds,
                    max_retries=settings.openrouter_max_retries,
                ),
            )
            if settings.openrouter_api_key
            else None
        )

    async def start(self) -> None:
        if self._started:
            return
        if self.settings.app_mode == "fixture":
            repository = self.require_repository()
            if not isinstance(repository, InMemoryRepository):
                raise RuntimeError("fixture mode requires the in-memory repository")
            await _seed_fixture_repository(repository)
        else:
            database_url = self._required(self.settings.database_url, "DATABASE_URL")
            self.repository = await PostgresRepository.create(database_url)
        self._started = True

    async def close(self) -> None:
        if self.repository is not None:
            close_repository = getattr(self.repository, "close", None)
            if close_repository is not None:
                await close_repository()
        for adapter in (self.github, self.llm):
            if adapter is None:
                continue
            close = getattr(adapter, "close", None)
            if close is not None:
                await close()
        self._started = False

    def require_repository(self) -> RepositoryAdapter:
        if self.repository is None:
            raise ApiError(
                status_code=503,
                code="service_unavailable",
                message="Persistence is not available",
                retryable=True,
            )
        return self.repository

    @staticmethod
    def _required(value: str | None, name: str) -> str:
        if value is None or not value.strip():
            raise RuntimeError(f"{name} is required in live mode")
        return value


def get_container(request: Request) -> AppContainer:
    container: AppContainer = request.app.state.container
    return container


async def _seed_fixture_repository(repository: InMemoryRepository) -> None:
    if await repository.get_repository_record(FIXTURE_REPOSITORY_ID) is not None:
        return
    now = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
    await repository.seed_repository_record(
        RepositoryRecord(
            id=FIXTURE_REPOSITORY_ID,
            github_repository_id=1,
            github_installation_id=99,
            owner="fixture-owner",
            name="alignment-memory-demo",
            default_branch="main",
            baseline_commit_sha=FIXTURE_MAIN_SHA,
            main_commit_sha=FIXTURE_MAIN_SHA,
            knowledge_revision=1,
        )
    )
    for profile_id, permission in (
        (FIXTURE_PROFILE_ID, "write"),
        (FIXTURE_READER_ID, "read"),
    ):
        await repository.seed_membership(
            MembershipRecord(
                repository_id=FIXTURE_REPOSITORY_ID,
                profile_id=profile_id,
                github_permission=permission,
            )
        )

    source = await repository.add_source(
        Source(
            id="20000000-0000-0000-0000-000000000001",
            repository_id=FIXTURE_REPOSITORY_ID,
            source_type="markdown",
            external_id="docs/adr.md",
            url="https://github.com/fixture-owner/alignment-memory-demo/blob/main/docs/adr.md",
        )
    )
    source_version = await repository.append_source_version(
        SourceVersion(
            id=FIXTURE_SOURCE_VERSION_ID,
            source_id=source.id,
            external_version=FIXTURE_MAIN_SHA,
            content="Browser extensions are out of scope for the MVP.",
            content_hash="c" * 64,
            occurred_at=now,
            ingested_at=now,
        )
    )
    evidence = EvidenceReference(
        source_version_id=source_version.id,
        url=source.url,
        exact_quote="Browser extensions are out of scope for the MVP.",
        role=EvidenceRole.CONTRADICTS,
        verified=True,
    )
    goal = await repository.add_knowledge_node(
        KnowledgeNode(
            id="50000000-0000-0000-0000-000000000001",
            repository_id=FIXTURE_REPOSITORY_ID,
            node_type=NodeType.GOAL,
            logical_key="ship-evidence-rich-mvp",
        )
    )
    decision = await repository.add_knowledge_node(
        KnowledgeNode(
            id="50000000-0000-0000-0000-000000000002",
            repository_id=FIXTURE_REPOSITORY_ID,
            node_type=NodeType.DECISION,
            logical_key="exclude-browser-extension",
        )
    )
    await repository.append_knowledge_node_version(
        KnowledgeNodeVersion(
            id="51000000-0000-0000-0000-000000000001",
            node_id=goal.id,
            revision=1,
            title="Ship the evidence-rich MVP",
            summary="Deliver one trusted vertical slice.",
            status=KnowledgeStatus.ACTIVE,
            created_by="fixture",
            created_at=now,
            evidence=(evidence,),
        )
    )
    await repository.append_knowledge_node_version(
        KnowledgeNodeVersion(
            id="51000000-0000-0000-0000-000000000002",
            node_id=decision.id,
            revision=1,
            title="Exclude browser extensions",
            summary="The MVP remains repository-native.",
            status=KnowledgeStatus.ACTIVE,
            created_by="fixture",
            created_at=now,
            evidence=(evidence,),
        )
    )
    await repository.add_knowledge_edge(
        KnowledgeEdge(
            id="52000000-0000-0000-0000-000000000001",
            repository_id=FIXTURE_REPOSITORY_ID,
            from_node_id=goal.id,
            to_node_id=decision.id,
            relation_type="constrains",
            valid_from_revision=1,
            evidence=(evidence,),
        )
    )
    job = await repository.create_job(
        Job(
            id=FIXTURE_JOB_ID,
            repository_id=FIXTURE_REPOSITORY_ID,
            event_key="fixture-pr-7",
            job_type=JobType.PR_ANALYSIS,
            status=JobStatus.COMPLETED,
            progress=100,
            head_sha=FIXTURE_HEAD_SHA,
            created_at=now,
            updated_at=now,
            completed_at=now,
        )
    )
    run = await repository.persist_ai_run(
        AiRun(
            id="60000000-0000-0000-0000-000000000001",
            job_id=job.id,
            provider="fixture",
            requested_model="fixture-primary",
            actual_model="fixture-model",
            prompt_version="fixture-v1",
            input_hash="fixture-input",
            output_json={"outcome": "aligned", "nodes": [], "findings": [], "edges": []},
            validation_status=ValidationStatus.VALID,
            usage={"total_tokens": 0},
            created_at=now,
            completed_at=now,
        )
    )
    finding = Finding(
        id="41000000-0000-0000-0000-000000000001",
        analysis_id=FIXTURE_ALIGNMENT_ID,
        finding_type=AlignmentOutcome.DIRECT_CONFLICT,
        target_node_id=decision.id,
        target_node_type=NodeType.DECISION,
        target_node_status=KnowledgeStatus.ACTIVE,
        contradicts=True,
        uncertain=False,
        explanation="The proposed extension conflicts with the active MVP boundary.",
        recommended_action="Keep the extension out of the MVP or supersede the decision.",
        evidence=(evidence,),
    )
    alignment = await repository.persist_validated_result(
        job.id,
        Alignment(
            id=FIXTURE_ALIGNMENT_ID,
            repository_id=FIXTURE_REPOSITORY_ID,
            pr_number=7,
            head_sha=FIXTURE_HEAD_SHA,
            knowledge_revision=1,
            outcome=AlignmentOutcome.DIRECT_CONFLICT,
            findings=(finding,),
            ai_run_id=run.id,
            created_at=now,
        ),
    )
    await repository.append_context_passport(
        ContextPassport(
            id="70000000-0000-0000-0000-000000000001",
            analysis_id=alignment.id,
            profile_id=FIXTURE_PROFILE_ID,
            language="en",
            content="The current decision excludes browser extensions from the MVP.",
            source_version_ids=(source_version.id,),
            ambiguities=(),
            ai_run_id=run.id,
            created_at=now,
        )
    )
