from datetime import datetime
from typing import Protocol, runtime_checkable

from alignment_memory.domain import (
    AiRun,
    Alignment,
    Handshake,
    Job,
    JobStatus,
    KnowledgeEdge,
    KnowledgeNode,
    KnowledgeNodeVersion,
    Override,
    Source,
    SourceVersion,
)
from alignment_memory.ports.control_plane import KnowledgeNodeSnapshot, RepositoryRecord


@runtime_checkable
class SourceRepository(Protocol):
    async def add_source(self, source: Source) -> Source: ...

    async def get_source(self, source_id: str) -> Source | None: ...

    async def append_source_version(self, version: SourceVersion) -> SourceVersion: ...

    async def list_source_versions(self, source_id: str) -> tuple[SourceVersion, ...]: ...


@runtime_checkable
class KnowledgeRepository(Protocol):
    async def add_knowledge_node(self, node: KnowledgeNode) -> KnowledgeNode: ...

    async def append_knowledge_node_version(
        self,
        version: KnowledgeNodeVersion,
    ) -> KnowledgeNodeVersion: ...

    async def add_knowledge_edge(self, edge: KnowledgeEdge) -> KnowledgeEdge: ...

    async def get_active_context(
        self,
        repository_id: str,
        revision: int | None = None,
    ) -> tuple[KnowledgeNodeVersion, ...]: ...

    async def persist_validated_result(
        self,
        job_id: str,
        alignment: Alignment,
    ) -> Alignment: ...

    async def get_result_for_job(self, job_id: str) -> Alignment | None: ...

    async def list_knowledge_snapshots(
        self,
        repository_id: str,
    ) -> tuple[KnowledgeNodeSnapshot, ...]: ...

    async def list_knowledge_node_versions(
        self,
        node_id: str,
    ) -> tuple[KnowledgeNodeVersion, ...]: ...

    async def list_knowledge_edges(
        self,
        repository_id: str,
    ) -> tuple[KnowledgeEdge, ...]: ...

    async def get_source_version_with_source(
        self,
        source_version_id: str,
    ) -> tuple[Source, SourceVersion] | None: ...

    async def advance_repository_revision(
        self,
        repository_id: str,
        *,
        expected_revision: int,
        head_sha: str,
    ) -> RepositoryRecord: ...

    async def acknowledge_repository_publication(
        self,
        repository_id: str,
        *,
        expected_main_sha: str,
        published_main_sha: str,
    ) -> RepositoryRecord: ...


@runtime_checkable
class JobRepository(Protocol):
    async def create_job(self, job: Job) -> Job: ...

    async def get_job(self, job_id: str) -> Job | None: ...

    async def compare_and_set_job(
        self,
        job_id: str,
        expected_status: JobStatus,
        next_status: JobStatus,
        *,
        occurred_at: datetime,
        error_code: str | None = None,
    ) -> Job | None: ...


@runtime_checkable
class AnalysisRunRepository(Protocol):
    async def persist_ai_run(self, run: AiRun) -> AiRun: ...

    async def get_ai_run(
        self,
        job_id: str,
        input_hash: str,
        prompt_version: str,
    ) -> AiRun | None: ...


@runtime_checkable
class CorrectionRepository(Protocol):
    async def append_handshake(self, handshake: Handshake) -> Handshake: ...

    async def list_handshakes(self, analysis_id: str) -> tuple[Handshake, ...]: ...

    async def append_override(self, override: Override) -> Override: ...

    async def list_overrides(self, target_type: str, target_id: str) -> tuple[Override, ...]: ...


@runtime_checkable
class PersistenceRepository(
    SourceRepository,
    KnowledgeRepository,
    JobRepository,
    AnalysisRunRepository,
    CorrectionRepository,
    Protocol,
):
    """Complete persistence boundary used by application services and local demos."""
