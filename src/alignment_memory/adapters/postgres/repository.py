from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Self

from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from alignment_memory.domain import (
    AiRun,
    Alignment,
    AlignmentOutcome,
    AppendOnlyViolation,
    ContextPassport,
    EvidenceReference,
    EvidenceRole,
    Finding,
    Handshake,
    HandshakeResponse,
    Job,
    JobStatus,
    JobType,
    KnowledgeEdge,
    KnowledgeNode,
    KnowledgeNodeVersion,
    KnowledgeStatus,
    NodeType,
    Override,
    OverrideType,
    Source,
    SourceVersion,
    ValidationStatus,
    transition_job,
)
from alignment_memory.ports.control_plane import (
    KnowledgeNodeSnapshot,
    MembershipRecord,
    RepositoryRecord,
    StaleRepositoryStateError,
)

Row = dict[str, Any]
Connection = AsyncConnection[Row]
Pool = AsyncConnectionPool[Connection]


class PostgresRepository:
    """Psycopg persistence adapter; only ``create`` opens its connection pool."""

    def __init__(
        self,
        pool: Pool,
        *,
        connection: Connection | None = None,
        owns_pool: bool = False,
    ) -> None:
        self._pool = pool
        self._connection = connection
        self._owns_pool = owns_pool

    @classmethod
    async def create(
        cls,
        database_url: str,
        *,
        min_size: int = 0,
        max_size: int = 4,
        timeout: float = 10.0,
    ) -> Self:
        pool: Pool = AsyncConnectionPool(
            conninfo=database_url,
            min_size=min_size,
            max_size=max_size,
            timeout=timeout,
            kwargs={"row_factory": dict_row},
            open=False,
        )
        await pool.open(wait=True, timeout=timeout)
        return cls(pool, owns_pool=True)

    async def close(self) -> None:
        if self._owns_pool:
            await self._pool.close()
            self._owns_pool = False

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[PostgresRepository]:
        """Yield a repository bound to one atomic database transaction."""

        if self._connection is not None:
            async with self._connection.transaction():
                yield self
            return

        async with self._pool.connection() as connection, connection.transaction():
            yield type(self)(self._pool, connection=connection)

    @asynccontextmanager
    async def _connection_scope(self) -> AsyncIterator[Connection]:
        if self._connection is not None:
            yield self._connection
            return
        async with self._pool.connection() as connection:
            yield connection

    async def add_source(self, source: Source) -> Source:
        async with self._connection_scope() as connection:
            cursor = await connection.execute(
                """
                insert into sources (id, repository_id, source_type, external_id, url)
                values (%s, %s, %s, %s, %s)
                on conflict do nothing
                returning *
                """,
                (
                    source.id,
                    source.repository_id,
                    source.source_type,
                    source.external_id,
                    source.url,
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                row = await self._fetch_one(
                    connection,
                    "select * from sources where id = %s",
                    (source.id,),
                )
                if row is None:
                    row = await self._fetch_one(
                        connection,
                        """
                        select * from sources
                        where repository_id = %s and source_type = %s and external_id = %s
                        """,
                        (source.repository_id, source.source_type, source.external_id),
                    )
            stored = self._source_from_row(self._required(row, "source conflict disappeared"))
            if stored.id == source.id and stored != source:
                raise AppendOnlyViolation("source identity already exists with different data")
            if stored.id != source.id and stored.url != source.url:
                raise AppendOnlyViolation("source identity already exists with different data")
            return stored

    async def get_source(self, source_id: str) -> Source | None:
        async with self._connection_scope() as connection:
            row = await self._fetch_one(
                connection,
                "select * from sources where id = %s",
                (source_id,),
            )
        return self._source_from_row(row) if row is not None else None

    async def append_source_version(self, version: SourceVersion) -> SourceVersion:
        async with self._connection_scope() as connection:
            cursor = await connection.execute(
                """
                insert into source_versions (
                    id, source_id, external_version, content, content_hash,
                    author_profile_id, occurred_at, ingested_at
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                on conflict do nothing
                returning *
                """,
                (
                    version.id,
                    version.source_id,
                    version.external_version,
                    version.content,
                    version.content_hash,
                    version.author_profile_id,
                    version.occurred_at,
                    version.ingested_at,
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                row = await self._fetch_one(
                    connection,
                    """
                    select * from source_versions
                    where id = %s or (source_id = %s and content_hash = %s)
                    """,
                    (version.id, version.source_id, version.content_hash),
                )
                stored = self._source_version_from_row(
                    self._required(row, "source version conflict disappeared")
                )
                if stored.id == version.id and stored != version:
                    raise AppendOnlyViolation(
                        "source version ID already exists with different data"
                    )
                return stored
            return self._source_version_from_row(row)

    async def list_source_versions(self, source_id: str) -> tuple[SourceVersion, ...]:
        async with self._connection_scope() as connection:
            rows = await self._fetch_all(
                connection,
                """
                select * from source_versions
                where source_id = %s
                order by occurred_at, ingested_at, id
                """,
                (source_id,),
            )
        return tuple(self._source_version_from_row(row) for row in rows)

    async def add_knowledge_node(self, node: KnowledgeNode) -> KnowledgeNode:
        async with self._connection_scope() as connection:
            cursor = await connection.execute(
                """
                insert into knowledge_nodes (
                    id, repository_id, node_type, logical_key, current_version_id
                ) values (%s, %s, %s, %s, %s)
                on conflict do nothing
                returning *
                """,
                (
                    node.id,
                    node.repository_id,
                    node.node_type.value,
                    node.logical_key,
                    node.current_version_id,
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                row = await self._fetch_one(
                    connection,
                    """
                    select * from knowledge_nodes
                    where id = %s or (repository_id = %s and logical_key = %s)
                    """,
                    (node.id, node.repository_id, node.logical_key),
                )
            stored = self._knowledge_node_from_row(
                self._required(row, "knowledge node conflict disappeared")
            )
            if not self._same_node_identity(stored, node):
                raise AppendOnlyViolation("knowledge node identity already exists")
            return stored

    async def append_knowledge_node_version(
        self,
        version: KnowledgeNodeVersion,
    ) -> KnowledgeNodeVersion:
        async with self.transaction() as transaction:
            connection = transaction._bound_connection()
            node = await self._fetch_one(
                connection,
                "select * from knowledge_nodes where id = %s for update",
                (version.node_id,),
            )
            if node is None:
                raise AppendOnlyViolation("knowledge version requires an existing node")

            existing = await self._fetch_one(
                connection,
                "select * from knowledge_node_versions where id = %s",
                (version.id,),
            )
            if existing is not None:
                stored = await self._knowledge_version_from_row(connection, existing)
                if stored == version:
                    return stored
                raise AppendOnlyViolation(
                    "knowledge version ID already exists with different data"
                )

            previous = await self._fetch_one(
                connection,
                """
                select * from knowledge_node_versions
                where node_id = %s
                order by revision desc
                limit 1
                """,
                (version.node_id,),
            )
            expected_revision = 1 if previous is None else int(previous["revision"]) + 1
            expected_supersedes = None if previous is None else str(previous["id"])
            if version.revision != expected_revision:
                raise AppendOnlyViolation(
                    f"knowledge revision must be {expected_revision}"
                )
            if version.supersedes_version_id != expected_supersedes:
                raise AppendOnlyViolation(
                    "new knowledge version must supersede the previous version"
                )

            await connection.execute(
                """
                insert into knowledge_node_versions (
                    id, node_id, revision, title, summary, status, created_by,
                    ai_run_id, supersedes_version_id, created_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    version.id,
                    version.node_id,
                    version.revision,
                    version.title,
                    version.summary,
                    version.status.value,
                    version.created_by,
                    version.ai_run_id,
                    version.supersedes_version_id,
                    version.created_at,
                ),
            )
            await self._insert_evidence(
                connection,
                "knowledge_node_version",
                version.id,
                version.evidence,
            )
            await connection.execute(
                "update knowledge_nodes set current_version_id = %s where id = %s",
                (version.id, version.node_id),
            )
            return version

    async def add_knowledge_edge(self, edge: KnowledgeEdge) -> KnowledgeEdge:
        async with self.transaction() as transaction:
            connection = transaction._bound_connection()
            try:
                await connection.execute(
                    """
                    insert into knowledge_edges (
                        id, repository_id, from_node_id, to_node_id, relation_type,
                        valid_from_revision, valid_to_revision
                    ) values (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        edge.id,
                        edge.repository_id,
                        edge.from_node_id,
                        edge.to_node_id,
                        edge.relation_type,
                        edge.valid_from_revision,
                        edge.valid_to_revision,
                    ),
                )
            except UniqueViolation as error:
                raise AppendOnlyViolation("knowledge edge already exists") from error
            await self._insert_evidence(
                connection,
                "knowledge_edge",
                edge.id,
                edge.evidence,
            )
            return edge

    async def get_active_context(
        self,
        repository_id: str,
        revision: int | None = None,
    ) -> tuple[KnowledgeNodeVersion, ...]:
        async with self._connection_scope() as connection:
            rows = await self._fetch_all(
                connection,
                """
                select version.*
                from knowledge_nodes node
                join lateral (
                    select candidate.*
                    from knowledge_node_versions candidate
                    where candidate.node_id = node.id
                      and (%s::integer is null or candidate.revision <= %s::integer)
                    order by candidate.revision desc
                    limit 1
                ) version on true
                where node.repository_id = %s
                order by version.node_id, version.revision
                """,
                (revision, revision, repository_id),
            )
            return tuple(
                [await self._knowledge_version_from_row(connection, row) for row in rows]
            )

    async def create_job(self, job: Job) -> Job:
        async with self._connection_scope() as connection:
            cursor = await connection.execute(
                """
                insert into sync_jobs (
                    id, repository_id, event_type, event_key, status, head_sha,
                    progress, error_code, created_at, updated_at, completed_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict do nothing
                returning *
                """,
                (
                    job.id,
                    job.repository_id,
                    job.job_type.value,
                    job.event_key,
                    job.status.value,
                    job.head_sha,
                    job.progress,
                    job.error_code,
                    job.created_at,
                    job.updated_at,
                    job.completed_at,
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                row = await self._fetch_one(
                    connection,
                    """
                    select * from sync_jobs
                    where id = %s or (repository_id = %s and event_key = %s)
                    """,
                    (job.id, job.repository_id, job.event_key),
                )
            stored = self._job_from_row(self._required(row, "job conflict disappeared"))
            if not self._same_job_identity(stored, job):
                raise AppendOnlyViolation("job event key already exists with different data")
            return stored

    async def get_job(self, job_id: str) -> Job | None:
        async with self._connection_scope() as connection:
            row = await self._fetch_one(
                connection,
                "select * from sync_jobs where id = %s",
                (job_id,),
            )
        return self._job_from_row(row) if row is not None else None

    async def compare_and_set_job(
        self,
        job_id: str,
        expected_status: JobStatus,
        next_status: JobStatus,
        *,
        occurred_at: datetime,
        error_code: str | None = None,
    ) -> Job | None:
        async with self.transaction() as transaction:
            connection = transaction._bound_connection()
            row = await self._fetch_one(
                connection,
                "select * from sync_jobs where id = %s for update",
                (job_id,),
            )
            if row is None:
                raise KeyError(job_id)
            current = self._job_from_row(row)
            if current.status is not expected_status:
                return None
            transitioned = transition_job(
                current,
                next_status,
                occurred_at=occurred_at,
                error_code=error_code,
            )
            cursor = await connection.execute(
                """
                update sync_jobs
                set status = %s, progress = %s, error_code = %s,
                    updated_at = %s, completed_at = %s
                where id = %s and status = %s
                returning *
                """,
                (
                    transitioned.status.value,
                    transitioned.progress,
                    transitioned.error_code,
                    transitioned.updated_at,
                    transitioned.completed_at,
                    job_id,
                    expected_status.value,
                ),
            )
            updated = await cursor.fetchone()
            return self._job_from_row(updated) if updated is not None else None

    async def persist_validated_result(
        self,
        job_id: str,
        alignment: Alignment,
    ) -> Alignment:
        async with self.transaction() as transaction:
            connection = transaction._bound_connection()
            provenance = await self._fetch_one(
                connection,
                "select id from ai_runs where id = %s and job_id = %s",
                (alignment.ai_run_id, job_id),
            )
            if provenance is None:
                raise AppendOnlyViolation("alignment requires an AI run belonging to its job")

            cursor = await connection.execute(
                """
                insert into alignment_analyses (
                    id, repository_id, pr_number, head_sha, knowledge_revision,
                    outcome, ai_run_id, created_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                on conflict do nothing
                returning *
                """,
                (
                    alignment.id,
                    alignment.repository_id,
                    alignment.pr_number,
                    alignment.head_sha,
                    alignment.knowledge_revision,
                    alignment.outcome.value,
                    alignment.ai_run_id,
                    alignment.created_at,
                ),
            )
            stored_row = await cursor.fetchone()
            if stored_row is None:
                stored_row = await self._fetch_one(
                    connection,
                    """
                    select * from alignment_analyses
                    where ai_run_id = %s
                       or (
                           repository_id = %s and pr_number = %s and head_sha = %s
                           and knowledge_revision = %s
                       )
                    """,
                    (
                        alignment.ai_run_id,
                        alignment.repository_id,
                        alignment.pr_number,
                        alignment.head_sha,
                        alignment.knowledge_revision,
                    ),
                )
                stored = await self._alignment_from_row(
                    connection,
                    self._required(stored_row, "alignment conflict disappeared"),
                )
                if stored == alignment:
                    return stored
                raise AppendOnlyViolation(
                    "validated result already exists with different data"
                )

            for finding in alignment.findings:
                await connection.execute(
                    """
                    insert into alignment_findings (
                        id, analysis_id, finding_type, target_node_id,
                        target_node_type, target_node_status, contradicts, uncertain,
                        explanation, recommended_action, validation_status
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'valid')
                    """,
                    (
                        finding.id,
                        alignment.id,
                        finding.finding_type.value,
                        finding.target_node_id,
                        finding.target_node_type.value if finding.target_node_type else None,
                        (
                            finding.target_node_status.value
                            if finding.target_node_status
                            else None
                        ),
                        finding.contradicts,
                        finding.uncertain,
                        finding.explanation,
                        finding.recommended_action,
                    ),
                )
                await self._insert_evidence(
                    connection,
                    "alignment_finding",
                    finding.id,
                    finding.evidence,
                )
            return alignment

    async def get_result_for_job(self, job_id: str) -> Alignment | None:
        async with self._connection_scope() as connection:
            row = await self._fetch_one(
                connection,
                """
                select analysis.*
                from alignment_analyses analysis
                join ai_runs run on run.id = analysis.ai_run_id
                where run.job_id = %s
                order by analysis.created_at desc
                limit 1
                """,
                (job_id,),
            )
            return (
                await self._alignment_from_row(connection, row)
                if row is not None
                else None
            )

    async def get_alignment(self, alignment_id: str) -> Alignment | None:
        async with self._connection_scope() as connection:
            row = await self._fetch_one(
                connection,
                "select * from alignment_analyses where id = %s",
                (alignment_id,),
            )
            return (
                await self._alignment_from_row(connection, row)
                if row is not None
                else None
            )

    async def list_jobs(self, repository_id: str) -> tuple[Job, ...]:
        async with self._connection_scope() as connection:
            rows = await self._fetch_all(
                connection,
                """
                select * from sync_jobs
                where repository_id = %s
                order by created_at desc, id
                """,
                (repository_id,),
            )
        return tuple(self._job_from_row(row) for row in rows)

    async def list_alignments(self, repository_id: str) -> tuple[Alignment, ...]:
        async with self._connection_scope() as connection:
            rows = await self._fetch_all(
                connection,
                """
                select * from alignment_analyses
                where repository_id = %s
                order by created_at desc, id
                """,
                (repository_id,),
            )
            return tuple([await self._alignment_from_row(connection, row) for row in rows])

    async def persist_ai_run(self, run: AiRun) -> AiRun:
        async with self._connection_scope() as connection:
            cursor = await connection.execute(
                """
                insert into ai_runs (
                    id, job_id, provider, requested_model, actual_model,
                    prompt_version, input_hash, output_json, validation_status,
                    usage, cost, created_at, completed_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict do nothing
                returning *
                """,
                (
                    run.id,
                    run.job_id,
                    run.provider,
                    run.requested_model,
                    run.actual_model,
                    run.prompt_version,
                    run.input_hash,
                    Jsonb(run.output_json),
                    run.validation_status.value,
                    Jsonb(run.usage),
                    run.cost,
                    run.created_at,
                    run.completed_at,
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                row = await self._fetch_one(
                    connection,
                    """
                    select * from ai_runs
                    where id = %s or (
                        job_id = %s and input_hash = %s and prompt_version = %s
                    )
                    """,
                    (run.id, run.job_id, run.input_hash, run.prompt_version),
                )
                stored = self._ai_run_from_row(
                    self._required(row, "AI run conflict disappeared")
                )
                if stored == run:
                    return stored
                raise AppendOnlyViolation("AI run input already exists with different data")
            return self._ai_run_from_row(row)

    async def get_ai_run(
        self,
        job_id: str,
        input_hash: str,
        prompt_version: str,
    ) -> AiRun | None:
        async with self._connection_scope() as connection:
            row = await self._fetch_one(
                connection,
                """
                select * from ai_runs
                where job_id = %s and input_hash = %s and prompt_version = %s
                """,
                (job_id, input_hash, prompt_version),
            )
        return self._ai_run_from_row(row) if row is not None else None

    async def append_handshake(self, handshake: Handshake) -> Handshake:
        async with self._connection_scope() as connection:
            try:
                await connection.execute(
                    """
                    insert into handshakes (
                        id, analysis_id, profile_id, response, message,
                        source_language, created_at
                    ) values (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        handshake.id,
                        handshake.analysis_id,
                        handshake.profile_id,
                        handshake.response.value,
                        handshake.message,
                        handshake.source_language,
                        handshake.created_at,
                    ),
                )
            except UniqueViolation as error:
                raise AppendOnlyViolation("handshake ID already exists") from error
        return handshake

    async def list_handshakes(self, analysis_id: str) -> tuple[Handshake, ...]:
        async with self._connection_scope() as connection:
            rows = await self._fetch_all(
                connection,
                """
                select * from handshakes
                where analysis_id = %s
                order by created_at, id
                """,
                (analysis_id,),
            )
        return tuple(self._handshake_from_row(row) for row in rows)

    async def append_override(self, override: Override) -> Override:
        async with self._connection_scope() as connection:
            try:
                await connection.execute(
                    """
                    insert into human_overrides (
                        id, target_type, target_id, override_type, reason,
                        actor_profile_id, created_node_version_id, created_at
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        override.id,
                        override.target_type,
                        override.target_id,
                        override.override_type.value,
                        override.reason,
                        override.actor_profile_id,
                        override.created_node_version_id,
                        override.created_at,
                    ),
                )
            except UniqueViolation as error:
                raise AppendOnlyViolation("override ID already exists") from error
        return override

    async def list_overrides(self, target_type: str, target_id: str) -> tuple[Override, ...]:
        async with self._connection_scope() as connection:
            rows = await self._fetch_all(
                connection,
                """
                select * from human_overrides
                where target_type = %s and target_id = %s
                order by created_at, id
                """,
                (target_type, target_id),
            )
        return tuple(self._override_from_row(row) for row in rows)

    async def list_repositories(self, profile_id: str) -> tuple[RepositoryRecord, ...]:
        async with self._connection_scope() as connection:
            rows = await self._fetch_all(
                connection,
                """
                select repository.*, installation.github_installation_id
                from repositories repository
                join github_installations installation
                  on installation.id = repository.installation_id
                join repository_memberships membership
                  on membership.repository_id = repository.id
                where membership.profile_id = %s and membership.active
                order by lower(repository.owner), lower(repository.name)
                """,
                (profile_id,),
            )
        return tuple(self._repository_record_from_row(row) for row in rows)

    async def list_installation_repositories(
        self,
        profile_id: str,
        github_installation_id: int,
    ) -> tuple[RepositoryRecord, ...]:
        repositories = await self.list_repositories(profile_id)
        return tuple(
            repository
            for repository in repositories
            if repository.github_installation_id == github_installation_id
        )

    async def get_repository_record(self, repository_id: str) -> RepositoryRecord | None:
        async with self._connection_scope() as connection:
            row = await self._fetch_one(
                connection,
                """
                select repository.*, installation.github_installation_id
                from repositories repository
                join github_installations installation
                  on installation.id = repository.installation_id
                where repository.id = %s
                """,
                (repository_id,),
            )
        return self._repository_record_from_row(row) if row is not None else None

    async def get_membership(
        self,
        repository_id: str,
        profile_id: str,
    ) -> MembershipRecord | None:
        async with self._connection_scope() as connection:
            row = await self._fetch_one(
                connection,
                """
                select repository_id, profile_id, github_permission, active
                from repository_memberships
                where repository_id = %s and profile_id = %s and active
                """,
                (repository_id, profile_id),
            )
        return self._membership_from_row(row) if row is not None else None

    async def list_knowledge_snapshots(
        self,
        repository_id: str,
    ) -> tuple[KnowledgeNodeSnapshot, ...]:
        async with self._connection_scope() as connection:
            rows = await self._fetch_all(
                connection,
                """
                select node.*
                from knowledge_nodes node
                where node.repository_id = %s and node.current_version_id is not null
                order by node.logical_key
                """,
                (repository_id,),
            )
            snapshots: list[KnowledgeNodeSnapshot] = []
            for row in rows:
                version_row = await self._fetch_one(
                    connection,
                    "select * from knowledge_node_versions where id = %s",
                    (row["current_version_id"],),
                )
                if version_row is None:
                    continue
                snapshots.append(
                    KnowledgeNodeSnapshot(
                        node=self._knowledge_node_from_row(row),
                        version=await self._knowledge_version_from_row(
                            connection,
                            version_row,
                        ),
                    )
                )
        return tuple(snapshots)

    async def list_knowledge_edges(
        self,
        repository_id: str,
    ) -> tuple[KnowledgeEdge, ...]:
        async with self._connection_scope() as connection:
            rows = await self._fetch_all(
                connection,
                """
                select * from knowledge_edges
                where repository_id = %s and valid_to_revision is null
                order by from_node_id, to_node_id, relation_type
                """,
                (repository_id,),
            )
            edges = [
                KnowledgeEdge(
                    id=str(row["id"]),
                    repository_id=str(row["repository_id"]),
                    from_node_id=str(row["from_node_id"]),
                    to_node_id=str(row["to_node_id"]),
                    relation_type=row["relation_type"],
                    valid_from_revision=row["valid_from_revision"],
                    valid_to_revision=row["valid_to_revision"],
                    evidence=await self._evidence_for_target(
                        connection,
                        "knowledge_edge",
                        str(row["id"]),
                    ),
                )
                for row in rows
            ]
        return tuple(edges)

    async def count_sources(self, repository_id: str) -> int:
        async with self._connection_scope() as connection:
            row = await self._fetch_one(
                connection,
                "select count(*) as source_count from sources where repository_id = %s",
                (repository_id,),
            )
        return int(row["source_count"]) if row is not None else 0

    async def get_source_version_with_source(
        self,
        source_version_id: str,
    ) -> tuple[Source, SourceVersion] | None:
        async with self._connection_scope() as connection:
            version_row = await self._fetch_one(
                connection,
                "select * from source_versions where id = %s",
                (source_version_id,),
            )
            if version_row is None:
                return None
            source_row = await self._fetch_one(
                connection,
                "select * from sources where id = %s",
                (version_row["source_id"],),
            )
        if source_row is None:
            return None
        return (
            self._source_from_row(source_row),
            self._source_version_from_row(version_row),
        )

    async def append_context_passport(
        self,
        passport: ContextPassport,
    ) -> ContextPassport:
        async with self._connection_scope() as connection:
            cursor = await connection.execute(
                """
                insert into context_passports (
                    id, analysis_id, profile_id, language, content,
                    source_version_ids, ambiguities, ai_run_id, created_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict do nothing
                returning *
                """,
                (
                    passport.id,
                    passport.analysis_id,
                    passport.profile_id,
                    passport.language,
                    passport.content,
                    list(passport.source_version_ids),
                    list(passport.ambiguities),
                    passport.ai_run_id,
                    passport.created_at,
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                row = await self._fetch_one(
                    connection,
                    """
                    select * from context_passports
                    where id = %s or (
                        analysis_id = %s and profile_id = %s
                        and language = %s and ai_run_id = %s
                    )
                    """,
                    (
                        passport.id,
                        passport.analysis_id,
                        passport.profile_id,
                        passport.language,
                        passport.ai_run_id,
                    ),
                )
            stored = self._context_passport_from_row(
                self._required(row, "context passport conflict disappeared")
            )
            if stored.id == passport.id and stored != passport:
                raise AppendOnlyViolation("context passport ID already exists")
            return stored

    async def get_context_passport(
        self,
        alignment_id: str,
        profile_id: str,
        language: str | None = None,
    ) -> ContextPassport | None:
        async with self._connection_scope() as connection:
            row = await self._fetch_one(
                connection,
                """
                select * from context_passports
                where analysis_id = %s and profile_id = %s
                  and (%s::text is null or language = %s::text)
                order by created_at desc, id desc
                limit 1
                """,
                (alignment_id, profile_id, language, language),
            )
        return self._context_passport_from_row(row) if row is not None else None

    async def repository_id_for_target(
        self,
        target_type: str,
        target_id: str,
    ) -> str | None:
        queries = {
            "alignment": "select repository_id from alignment_analyses where id = %s",
            "finding": """
                select analysis.repository_id
                from alignment_findings finding
                join alignment_analyses analysis on analysis.id = finding.analysis_id
                where finding.id = %s
            """,
            "knowledge_node": "select repository_id from knowledge_nodes where id = %s",
            "knowledge_node_version": """
                select node.repository_id
                from knowledge_node_versions version
                join knowledge_nodes node on node.id = version.node_id
                where version.id = %s
            """,
        }
        query = queries.get(target_type)
        if query is None:
            return None
        async with self._connection_scope() as connection:
            row = await self._fetch_one(connection, query, (target_id,))
        return str(row["repository_id"]) if row is not None else None

    async def persist_worker_result(
        self,
        job_id: str,
        run: AiRun,
        alignment: Alignment,
        *,
        expected_head_sha: str,
        expected_main_sha: str | None,
    ) -> Alignment:
        async with self.transaction() as transaction:
            connection = transaction._bound_connection()
            job_row = await self._fetch_one(
                connection,
                "select * from sync_jobs where id = %s for update",
                (job_id,),
            )
            if job_row is None:
                raise KeyError(job_id)
            repository_row = await self._fetch_one(
                connection,
                "select * from repositories where id = %s for update",
                (alignment.repository_id,),
            )
            if repository_row is None:
                raise KeyError(alignment.repository_id)
            job = self._job_from_row(job_row)
            if job.repository_id != alignment.repository_id or run.job_id != job_id:
                raise AppendOnlyViolation("worker result provenance does not match its job")
            if run.validation_status is not ValidationStatus.VALID:
                raise AppendOnlyViolation("worker result must be validated")
            if job.head_sha != expected_head_sha or alignment.head_sha != expected_head_sha:
                raise StaleRepositoryStateError("worker head SHA is stale")
            if (
                expected_main_sha is not None
                and repository_row["main_commit_sha"] != expected_main_sha
            ):
                raise StaleRepositoryStateError("repository main SHA is stale")
            await transaction.persist_ai_run(run)
            return await transaction.persist_validated_result(job_id, alignment)

    def _bound_connection(self) -> Connection:
        if self._connection is None:
            raise RuntimeError("operation requires a transaction-bound repository")
        return self._connection

    @staticmethod
    async def _fetch_one(
        connection: Connection,
        query: str,
        parameters: tuple[object, ...],
    ) -> Row | None:
        cursor = await connection.execute(query, parameters)
        return await cursor.fetchone()

    @staticmethod
    async def _fetch_all(
        connection: Connection,
        query: str,
        parameters: tuple[object, ...],
    ) -> list[Row]:
        cursor = await connection.execute(query, parameters)
        return await cursor.fetchall()

    @staticmethod
    def _required(row: Row | None, message: str) -> Row:
        if row is None:
            raise RuntimeError(message)
        return row

    async def _insert_evidence(
        self,
        connection: Connection,
        target_type: str,
        target_id: str,
        evidence_set: tuple[EvidenceReference, ...],
    ) -> None:
        for evidence in evidence_set:
            await connection.execute(
                """
                insert into evidence_links (
                    target_type, target_id, source_version_id, quote, relation, verified
                ) values (%s, %s, %s, %s, %s, %s)
                on conflict (
                    target_type, target_id, source_version_id, quote, relation
                ) do nothing
                """,
                (
                    target_type,
                    target_id,
                    evidence.source_version_id,
                    evidence.exact_quote,
                    evidence.role.value,
                    evidence.verified,
                ),
            )

    async def _evidence_for_target(
        self,
        connection: Connection,
        target_type: str,
        target_id: str,
    ) -> tuple[EvidenceReference, ...]:
        rows = await self._fetch_all(
            connection,
            """
            select link.*, source.url
            from evidence_links link
            join source_versions version on version.id = link.source_version_id
            join sources source on source.id = version.source_id
            where link.target_type = %s and link.target_id = %s
            order by link.created_at, link.id
            """,
            (target_type, target_id),
        )
        return tuple(
            EvidenceReference(
                source_version_id=str(row["source_version_id"]),
                url=row["url"],
                exact_quote=row["quote"],
                role=EvidenceRole(row["relation"]),
                verified=row["verified"],
            )
            for row in rows
        )

    async def _knowledge_version_from_row(
        self,
        connection: Connection,
        row: Row,
    ) -> KnowledgeNodeVersion:
        version_id = str(row["id"])
        return KnowledgeNodeVersion(
            id=version_id,
            node_id=str(row["node_id"]),
            revision=row["revision"],
            title=row["title"],
            summary=row["summary"],
            status=KnowledgeStatus(row["status"]),
            created_by=row["created_by"],
            ai_run_id=str(row["ai_run_id"]) if row["ai_run_id"] else None,
            supersedes_version_id=(
                str(row["supersedes_version_id"])
                if row["supersedes_version_id"]
                else None
            ),
            created_at=row["created_at"],
            evidence=await self._evidence_for_target(
                connection,
                "knowledge_node_version",
                version_id,
            ),
        )

    async def _alignment_from_row(
        self,
        connection: Connection,
        row: Row,
    ) -> Alignment:
        analysis_id = str(row["id"])
        finding_rows = await self._fetch_all(
            connection,
            """
            select * from alignment_findings
            where analysis_id = %s
            order by created_at, id
            """,
            (analysis_id,),
        )
        findings: list[Finding] = []
        for finding_row in finding_rows:
            finding_id = str(finding_row["id"])
            findings.append(
                Finding(
                    id=finding_id,
                    analysis_id=analysis_id,
                    finding_type=AlignmentOutcome(finding_row["finding_type"]),
                    target_node_id=(
                        str(finding_row["target_node_id"])
                        if finding_row["target_node_id"]
                        else None
                    ),
                    target_node_type=(
                        NodeType(finding_row["target_node_type"])
                        if finding_row["target_node_type"]
                        else None
                    ),
                    target_node_status=(
                        KnowledgeStatus(finding_row["target_node_status"])
                        if finding_row["target_node_status"]
                        else None
                    ),
                    contradicts=finding_row["contradicts"],
                    uncertain=finding_row["uncertain"],
                    explanation=finding_row["explanation"],
                    recommended_action=finding_row["recommended_action"],
                    evidence=await self._evidence_for_target(
                        connection,
                        "alignment_finding",
                        finding_id,
                    ),
                )
            )
        return Alignment(
            id=analysis_id,
            repository_id=str(row["repository_id"]),
            pr_number=row["pr_number"],
            head_sha=row["head_sha"],
            knowledge_revision=row["knowledge_revision"],
            outcome=AlignmentOutcome(row["outcome"]),
            findings=tuple(findings),
            ai_run_id=str(row["ai_run_id"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _source_from_row(row: Row) -> Source:
        return Source(
            id=str(row["id"]),
            repository_id=str(row["repository_id"]),
            source_type=row["source_type"],
            external_id=row["external_id"],
            url=row["url"],
        )

    @staticmethod
    def _ai_run_from_row(row: Row) -> AiRun:
        return AiRun(
            id=str(row["id"]),
            job_id=str(row["job_id"]),
            provider=row["provider"],
            requested_model=row["requested_model"],
            actual_model=row["actual_model"],
            prompt_version=row["prompt_version"],
            input_hash=row["input_hash"],
            output_json=row["output_json"],
            validation_status=ValidationStatus(row["validation_status"]),
            usage=row["usage"],
            cost=float(row["cost"]) if row["cost"] is not None else None,
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _source_version_from_row(row: Row) -> SourceVersion:
        return SourceVersion(
            id=str(row["id"]),
            source_id=str(row["source_id"]),
            external_version=row["external_version"],
            content=row["content"],
            content_hash=row["content_hash"],
            author_profile_id=(
                str(row["author_profile_id"]) if row["author_profile_id"] else None
            ),
            occurred_at=row["occurred_at"],
            ingested_at=row["ingested_at"],
        )

    @staticmethod
    def _knowledge_node_from_row(row: Row) -> KnowledgeNode:
        return KnowledgeNode(
            id=str(row["id"]),
            repository_id=str(row["repository_id"]),
            node_type=NodeType(row["node_type"]),
            logical_key=row["logical_key"],
            current_version_id=(
                str(row["current_version_id"]) if row["current_version_id"] else None
            ),
        )

    @staticmethod
    def _job_from_row(row: Row) -> Job:
        return Job(
            id=str(row["id"]),
            repository_id=str(row["repository_id"]),
            event_key=row["event_key"],
            job_type=JobType(row["event_type"]),
            status=JobStatus(row["status"]),
            progress=row["progress"],
            head_sha=row["head_sha"],
            error_code=row["error_code"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _handshake_from_row(row: Row) -> Handshake:
        return Handshake(
            id=str(row["id"]),
            analysis_id=str(row["analysis_id"]),
            profile_id=str(row["profile_id"]),
            response=HandshakeResponse(row["response"]),
            message=row["message"],
            source_language=row["source_language"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _override_from_row(row: Row) -> Override:
        return Override(
            id=str(row["id"]),
            target_type=row["target_type"],
            target_id=str(row["target_id"]),
            override_type=OverrideType(row["override_type"]),
            reason=row["reason"],
            actor_profile_id=str(row["actor_profile_id"]),
            created_node_version_id=(
                str(row["created_node_version_id"])
                if row["created_node_version_id"]
                else None
            ),
            created_at=row["created_at"],
        )

    @staticmethod
    def _repository_record_from_row(row: Row) -> RepositoryRecord:
        return RepositoryRecord(
            id=str(row["id"]),
            github_repository_id=row["github_repository_id"],
            github_installation_id=row["github_installation_id"],
            owner=row["owner"],
            name=row["name"],
            default_branch=row["default_branch"],
            baseline_commit_sha=row["baseline_commit_sha"],
            main_commit_sha=row["main_commit_sha"],
            knowledge_revision=row["knowledge_revision"],
        )

    @staticmethod
    def _membership_from_row(row: Row) -> MembershipRecord:
        return MembershipRecord(
            repository_id=str(row["repository_id"]),
            profile_id=str(row["profile_id"]),
            github_permission=str(row["github_permission"]),
            active=row["active"],
        )

    @staticmethod
    def _context_passport_from_row(row: Row) -> ContextPassport:
        return ContextPassport(
            id=str(row["id"]),
            analysis_id=str(row["analysis_id"]),
            profile_id=str(row["profile_id"]),
            language=row["language"],
            content=row["content"],
            source_version_ids=tuple(str(item) for item in row["source_version_ids"]),
            ambiguities=tuple(row["ambiguities"]),
            ai_run_id=str(row["ai_run_id"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _same_node_identity(left: KnowledgeNode, right: KnowledgeNode) -> bool:
        return (
            left.repository_id == right.repository_id
            and left.node_type is right.node_type
            and left.logical_key == right.logical_key
        )

    @staticmethod
    def _same_job_identity(left: Job, right: Job) -> bool:
        return (
            left.repository_id == right.repository_id
            and left.event_key == right.event_key
            and left.job_type is right.job_type
            and left.head_sha == right.head_sha
        )
