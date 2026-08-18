from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from alignment_memory.domain import (
    AiRun,
    Alignment,
    ContextPassport,
    Job,
    KnowledgeEdge,
    KnowledgeNode,
    KnowledgeNodeVersion,
    Source,
    SourceVersion,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class RepositoryRecord:
    id: str
    github_repository_id: int
    github_installation_id: int
    owner: str
    name: str
    default_branch: str
    baseline_commit_sha: str | None = None
    main_commit_sha: str | None = None
    knowledge_revision: int = 0

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True, slots=True, kw_only=True)
class MembershipRecord:
    repository_id: str
    profile_id: str
    github_permission: str
    active: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeNodeSnapshot:
    node: KnowledgeNode
    version: KnowledgeNodeVersion


@dataclass(frozen=True, slots=True, kw_only=True)
class GeneratedArtifactRecord:
    id: str
    repository_id: str
    path: str
    content_hash: str
    blob_sha: str
    commit_sha: str
    knowledge_revision: int
    created_at: datetime


class StaleRepositoryStateError(RuntimeError):
    """A worker result was produced from a head that is no longer current."""


@runtime_checkable
class ControlPlaneRepository(Protocol):
    async def list_repositories(self, profile_id: str) -> tuple[RepositoryRecord, ...]: ...

    async def list_installation_repositories(
        self,
        profile_id: str,
        github_installation_id: int,
    ) -> tuple[RepositoryRecord, ...]: ...

    async def get_repository_record(self, repository_id: str) -> RepositoryRecord | None: ...

    async def get_membership(
        self,
        repository_id: str,
        profile_id: str,
    ) -> MembershipRecord | None: ...

    async def get_alignment(self, alignment_id: str) -> Alignment | None: ...

    async def list_jobs(self, repository_id: str) -> tuple[Job, ...]: ...

    async def list_alignments(self, repository_id: str) -> tuple[Alignment, ...]: ...

    async def list_knowledge_snapshots(
        self,
        repository_id: str,
    ) -> tuple[KnowledgeNodeSnapshot, ...]: ...

    async def list_knowledge_edges(
        self,
        repository_id: str,
    ) -> tuple[KnowledgeEdge, ...]: ...

    async def count_sources(self, repository_id: str) -> int: ...

    async def get_source_version_with_source(
        self,
        source_version_id: str,
    ) -> tuple[Source, SourceVersion] | None: ...

    async def append_context_passport(
        self,
        passport: ContextPassport,
    ) -> ContextPassport: ...

    async def get_context_passport(
        self,
        alignment_id: str,
        profile_id: str,
        language: str | None = None,
    ) -> ContextPassport | None: ...

    async def repository_id_for_target(
        self,
        target_type: str,
        target_id: str,
    ) -> str | None: ...

    async def persist_worker_result(
        self,
        job_id: str,
        run: AiRun,
        alignment: Alignment,
        *,
        expected_head_sha: str,
        expected_main_sha: str | None,
    ) -> Alignment: ...
