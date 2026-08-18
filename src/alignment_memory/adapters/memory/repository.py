import asyncio
from collections import defaultdict
from dataclasses import replace
from datetime import datetime

from alignment_memory.domain import (
    AiRun,
    Alignment,
    AppendOnlyViolation,
    ContextPassport,
    Handshake,
    Job,
    JobStatus,
    KnowledgeEdge,
    KnowledgeNode,
    KnowledgeNodeVersion,
    Override,
    Source,
    SourceVersion,
    ValidationStatus,
    append_knowledge_node_version,
    append_source_version,
    transition_job,
)
from alignment_memory.ports.control_plane import (
    GeneratedArtifactRecord,
    KnowledgeNodeSnapshot,
    MembershipRecord,
    RepositoryRecord,
    StaleRepositoryStateError,
)


class InMemoryRepository:
    """Concurrency-safe local adapter with the same append-only rules as PostgreSQL."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sources: dict[str, Source] = {}
        self._source_keys: dict[tuple[str, str, str], str] = {}
        self._source_versions: dict[str, tuple[SourceVersion, ...]] = defaultdict(tuple)
        self._source_version_ids: dict[str, SourceVersion] = {}
        self._nodes: dict[str, KnowledgeNode] = {}
        self._node_keys: dict[tuple[str, str], str] = {}
        self._node_versions: dict[str, tuple[KnowledgeNodeVersion, ...]] = defaultdict(tuple)
        self._node_version_ids: dict[str, KnowledgeNodeVersion] = {}
        self._edges: dict[str, KnowledgeEdge] = {}
        self._jobs: dict[str, Job] = {}
        self._job_keys: dict[tuple[str, str], str] = {}
        self._ai_runs: dict[str, AiRun] = {}
        self._ai_run_keys: dict[tuple[str, str, str], str] = {}
        self._alignments: dict[str, Alignment] = {}
        self._result_job_ids: dict[str, str] = {}
        self._alignment_keys: dict[tuple[str, int, str, int], str] = {}
        self._handshakes: dict[str, Handshake] = {}
        self._overrides: dict[str, Override] = {}
        self._repository_records: dict[str, RepositoryRecord] = {}
        self._memberships: dict[tuple[str, str], MembershipRecord] = {}
        self._context_passports: dict[str, ContextPassport] = {}
        self._generated_artifacts: dict[str, GeneratedArtifactRecord] = {}
        self._generated_artifact_keys: dict[tuple[str, str, str], str] = {}

    async def seed_repository_record(self, repository: RepositoryRecord) -> None:
        async with self._lock:
            existing = self._repository_records.get(repository.id)
            if existing is not None and existing != repository:
                raise AppendOnlyViolation("repository fixture already exists with different data")
            self._repository_records[repository.id] = repository

    async def seed_membership(self, membership: MembershipRecord) -> None:
        key = (membership.repository_id, membership.profile_id)
        async with self._lock:
            existing = self._memberships.get(key)
            if existing is not None and existing != membership:
                raise AppendOnlyViolation("membership fixture already exists with different data")
            self._memberships[key] = membership

    async def add_source(self, source: Source) -> Source:
        natural_key = (source.repository_id, source.source_type, source.external_id)
        async with self._lock:
            existing = self._sources.get(source.id)
            existing_id = self._source_keys.get(natural_key)
            if existing is not None:
                if existing == source:
                    return existing
                raise AppendOnlyViolation("source identity already exists with different data")
            if existing_id is not None:
                stored = self._sources[existing_id]
                if stored.url == source.url:
                    return stored
                raise AppendOnlyViolation("source identity already exists with different data")
            self._sources[source.id] = source
            self._source_keys[natural_key] = source.id
            return source

    async def get_source(self, source_id: str) -> Source | None:
        return self._sources.get(source_id)

    async def append_source_version(self, version: SourceVersion) -> SourceVersion:
        async with self._lock:
            if version.source_id not in self._sources:
                raise AppendOnlyViolation("source version requires an existing source")
            existing_by_id = self._source_version_ids.get(version.id)
            if existing_by_id is not None:
                if existing_by_id == version:
                    return existing_by_id
                raise AppendOnlyViolation("source version ID already exists with different data")

            history = self._source_versions[version.source_id]
            duplicate_hash = next(
                (item for item in history if item.content_hash == version.content_hash),
                None,
            )
            if duplicate_hash is not None:
                return duplicate_hash

            updated = append_source_version(history, version)
            self._source_versions[version.source_id] = updated
            self._source_version_ids[version.id] = version
            return version

    async def list_source_versions(self, source_id: str) -> tuple[SourceVersion, ...]:
        return self._source_versions[source_id]

    async def add_knowledge_node(self, node: KnowledgeNode) -> KnowledgeNode:
        natural_key = (node.repository_id, node.logical_key)
        async with self._lock:
            existing = self._nodes.get(node.id)
            existing_id = self._node_keys.get(natural_key)
            if existing is not None:
                if self._same_node_identity(existing, node):
                    return existing
                raise AppendOnlyViolation("knowledge node identity already exists")
            if existing_id is not None:
                stored = self._nodes[existing_id]
                if self._same_node_identity(stored, node):
                    return stored
                raise AppendOnlyViolation("knowledge node identity already exists")
            self._nodes[node.id] = node
            self._node_keys[natural_key] = node.id
            return node

    async def append_knowledge_node_version(
        self,
        version: KnowledgeNodeVersion,
    ) -> KnowledgeNodeVersion:
        async with self._lock:
            node = self._nodes.get(version.node_id)
            if node is None:
                raise AppendOnlyViolation("knowledge version requires an existing node")
            existing = self._node_version_ids.get(version.id)
            if existing is not None:
                if existing == version:
                    return existing
                raise AppendOnlyViolation("knowledge version ID already exists with different data")

            history = self._node_versions[version.node_id]
            updated = append_knowledge_node_version(history, version)
            self._node_versions[version.node_id] = updated
            self._node_version_ids[version.id] = version
            self._nodes[node.id] = KnowledgeNode(
                id=node.id,
                repository_id=node.repository_id,
                node_type=node.node_type,
                logical_key=node.logical_key,
                current_version_id=version.id,
            )
            return version

    async def add_knowledge_edge(self, edge: KnowledgeEdge) -> KnowledgeEdge:
        async with self._lock:
            if edge.from_node_id not in self._nodes or edge.to_node_id not in self._nodes:
                raise AppendOnlyViolation("knowledge edge requires existing endpoint nodes")
            existing = self._edges.get(edge.id)
            if existing == edge:
                return edge
            if existing is not None:
                raise AppendOnlyViolation("knowledge edge ID already exists with different data")
            self._edges[edge.id] = edge
            return edge

    async def get_active_context(
        self,
        repository_id: str,
        revision: int | None = None,
    ) -> tuple[KnowledgeNodeVersion, ...]:
        active: list[KnowledgeNodeVersion] = []
        for node in self._nodes.values():
            if node.repository_id != repository_id:
                continue
            versions = self._node_versions[node.id]
            eligible = tuple(
                version
                for version in versions
                if revision is None or version.revision <= revision
            )
            if eligible:
                active.append(eligible[-1])
        return tuple(sorted(active, key=lambda version: (version.node_id, version.revision)))

    async def create_job(self, job: Job) -> Job:
        natural_key = (job.repository_id, job.event_key)
        async with self._lock:
            existing = self._jobs.get(job.id)
            existing_id = self._job_keys.get(natural_key)
            if existing is not None:
                if self._same_job_identity(existing, job):
                    return existing
                raise AppendOnlyViolation("job event key already exists with different data")
            if existing_id is not None:
                stored = self._jobs[existing_id]
                if self._same_job_identity(stored, job):
                    return stored
                raise AppendOnlyViolation("job event key already exists with different data")
            self._jobs[job.id] = job
            self._job_keys[natural_key] = job.id
            return job

    async def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    async def compare_and_set_job(
        self,
        job_id: str,
        expected_status: JobStatus,
        next_status: JobStatus,
        *,
        occurred_at: datetime,
        error_code: str | None = None,
    ) -> Job | None:
        async with self._lock:
            current = self._jobs.get(job_id)
            if current is None:
                raise KeyError(job_id)
            if current.status is not expected_status:
                return None
            transitioned = transition_job(
                current,
                next_status,
                occurred_at=occurred_at,
                error_code=error_code,
            )
            self._jobs[job_id] = transitioned
            return transitioned

    async def persist_validated_result(
        self,
        job_id: str,
        alignment: Alignment,
    ) -> Alignment:
        natural_key = (
            alignment.repository_id,
            alignment.pr_number,
            alignment.head_sha,
            alignment.knowledge_revision,
        )
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.repository_id != alignment.repository_id:
                raise AppendOnlyViolation("alignment repository does not match its job")

            result_id = self._result_job_ids.get(job_id)
            natural_result_id = self._alignment_keys.get(natural_key)
            if result_id is not None or natural_result_id is not None:
                existing_id = result_id or natural_result_id
                existing = self._alignments[existing_id]
                if existing == alignment:
                    self._result_job_ids[job_id] = existing.id
                    return existing
                raise AppendOnlyViolation("validated result already exists with different data")

            if alignment.id in self._alignments:
                raise AppendOnlyViolation("alignment ID already exists with different data")
            self._alignments[alignment.id] = alignment
            self._alignment_keys[natural_key] = alignment.id
            self._result_job_ids[job_id] = alignment.id
            return alignment

    async def get_result_for_job(self, job_id: str) -> Alignment | None:
        result_id = self._result_job_ids.get(job_id)
        return self._alignments.get(result_id) if result_id is not None else None

    async def get_alignment(self, alignment_id: str) -> Alignment | None:
        return self._alignments.get(alignment_id)

    async def list_jobs(self, repository_id: str) -> tuple[Job, ...]:
        return tuple(
            sorted(
                (job for job in self._jobs.values() if job.repository_id == repository_id),
                key=lambda job: (job.created_at, job.id),
                reverse=True,
            )
        )

    async def list_alignments(self, repository_id: str) -> tuple[Alignment, ...]:
        return tuple(
            sorted(
                (
                    alignment
                    for alignment in self._alignments.values()
                    if alignment.repository_id == repository_id
                ),
                key=lambda alignment: (alignment.created_at, alignment.id),
                reverse=True,
            )
        )

    async def persist_ai_run(self, run: AiRun) -> AiRun:
        natural_key = (run.job_id, run.input_hash, run.prompt_version)
        async with self._lock:
            if run.job_id not in self._jobs:
                raise AppendOnlyViolation("AI run requires an existing job")
            existing = self._ai_runs.get(run.id)
            existing_id = self._ai_run_keys.get(natural_key)
            if existing is not None:
                if existing == run:
                    return existing
                raise AppendOnlyViolation("AI run ID already exists with different data")
            if existing_id is not None:
                stored = self._ai_runs[existing_id]
                if stored == run:
                    return stored
                raise AppendOnlyViolation("AI run input already exists with different data")
            self._ai_runs[run.id] = run
            self._ai_run_keys[natural_key] = run.id
            return run

    async def get_ai_run(
        self,
        job_id: str,
        input_hash: str,
        prompt_version: str,
    ) -> AiRun | None:
        run_id = self._ai_run_keys.get((job_id, input_hash, prompt_version))
        return self._ai_runs.get(run_id) if run_id is not None else None

    async def append_handshake(self, handshake: Handshake) -> Handshake:
        async with self._lock:
            if handshake.id in self._handshakes:
                raise AppendOnlyViolation("handshake ID already exists")
            if handshake.analysis_id not in self._alignments:
                raise AppendOnlyViolation("handshake requires an existing alignment")
            self._handshakes[handshake.id] = handshake
            return handshake

    async def list_handshakes(self, analysis_id: str) -> tuple[Handshake, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._handshakes.values()
                    if item.analysis_id == analysis_id
                ),
                key=lambda item: (item.created_at, item.id),
            )
        )

    async def append_override(self, override: Override) -> Override:
        async with self._lock:
            if override.id in self._overrides:
                raise AppendOnlyViolation("override ID already exists")
            self._overrides[override.id] = override
            return override

    async def list_overrides(self, target_type: str, target_id: str) -> tuple[Override, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._overrides.values()
                    if item.target_type == target_type and item.target_id == target_id
                ),
                key=lambda item: (item.created_at, item.id),
            )
        )

    async def list_repositories(self, profile_id: str) -> tuple[RepositoryRecord, ...]:
        repository_ids = {
            membership.repository_id
            for membership in self._memberships.values()
            if membership.profile_id == profile_id and membership.active
        }
        return tuple(
            sorted(
                (
                    repository
                    for repository in self._repository_records.values()
                    if repository.id in repository_ids
                ),
                key=lambda repository: (repository.owner.lower(), repository.name.lower()),
            )
        )

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
        return self._repository_records.get(repository_id)

    async def get_membership(
        self,
        repository_id: str,
        profile_id: str,
    ) -> MembershipRecord | None:
        membership = self._memberships.get((repository_id, profile_id))
        return membership if membership is not None and membership.active else None

    async def list_knowledge_snapshots(
        self,
        repository_id: str,
    ) -> tuple[KnowledgeNodeSnapshot, ...]:
        snapshots: list[KnowledgeNodeSnapshot] = []
        for node in self._nodes.values():
            if node.repository_id != repository_id:
                continue
            versions = self._node_versions[node.id]
            if versions:
                snapshots.append(KnowledgeNodeSnapshot(node=node, version=versions[-1]))
        return tuple(sorted(snapshots, key=lambda item: item.node.logical_key))

    async def list_knowledge_node_versions(
        self,
        node_id: str,
    ) -> tuple[KnowledgeNodeVersion, ...]:
        return self._node_versions[node_id]

    async def advance_repository_revision(
        self,
        repository_id: str,
        *,
        expected_revision: int,
        head_sha: str,
    ) -> RepositoryRecord:
        async with self._lock:
            current = self._repository_records.get(repository_id)
            if current is None:
                raise KeyError(repository_id)
            target_revision = expected_revision + 1
            if (
                current.knowledge_revision == target_revision
                and current.baseline_commit_sha == head_sha
                and current.main_commit_sha == head_sha
            ):
                return current
            if current.knowledge_revision != expected_revision:
                raise StaleRepositoryStateError("repository knowledge revision is stale")
            updated = replace(
                current,
                baseline_commit_sha=head_sha,
                main_commit_sha=head_sha,
                knowledge_revision=target_revision,
            )
            self._repository_records[repository_id] = updated
            return updated

    async def persist_generated_artifact(
        self,
        artifact: GeneratedArtifactRecord,
    ) -> GeneratedArtifactRecord:
        natural_key = (artifact.repository_id, artifact.path, artifact.content_hash)
        async with self._lock:
            existing = self._generated_artifacts.get(artifact.id)
            existing_id = self._generated_artifact_keys.get(natural_key)
            if existing is not None:
                if existing == artifact:
                    return existing
                raise AppendOnlyViolation("generated artifact ID already exists")
            if existing_id is not None:
                return self._generated_artifacts[existing_id]
            self._generated_artifacts[artifact.id] = artifact
            self._generated_artifact_keys[natural_key] = artifact.id
            return artifact

    async def list_generated_artifacts(
        self,
        repository_id: str,
    ) -> tuple[GeneratedArtifactRecord, ...]:
        return tuple(
            sorted(
                (
                    artifact
                    for artifact in self._generated_artifacts.values()
                    if artifact.repository_id == repository_id
                ),
                key=lambda artifact: (artifact.knowledge_revision, artifact.path),
            )
        )

    async def list_knowledge_edges(
        self,
        repository_id: str,
    ) -> tuple[KnowledgeEdge, ...]:
        return tuple(
            sorted(
                (
                    edge
                    for edge in self._edges.values()
                    if edge.repository_id == repository_id and edge.valid_to_revision is None
                ),
                key=lambda edge: (edge.from_node_id, edge.to_node_id, edge.relation_type),
            )
        )

    async def count_sources(self, repository_id: str) -> int:
        return sum(
            source.repository_id == repository_id for source in self._sources.values()
        )

    async def get_source_version_with_source(
        self,
        source_version_id: str,
    ) -> tuple[Source, SourceVersion] | None:
        version = self._source_version_ids.get(source_version_id)
        if version is None:
            return None
        source = self._sources.get(version.source_id)
        return (source, version) if source is not None else None

    async def append_context_passport(
        self,
        passport: ContextPassport,
    ) -> ContextPassport:
        async with self._lock:
            if passport.analysis_id not in self._alignments:
                raise AppendOnlyViolation("context passport requires an existing alignment")
            existing = self._context_passports.get(passport.id)
            if existing is not None:
                if existing == passport:
                    return existing
                raise AppendOnlyViolation("context passport ID already exists")
            natural = next(
                (
                    item
                    for item in self._context_passports.values()
                    if item.analysis_id == passport.analysis_id
                    and item.profile_id == passport.profile_id
                    and item.language == passport.language
                    and item.ai_run_id == passport.ai_run_id
                ),
                None,
            )
            if natural is not None:
                return natural
            self._context_passports[passport.id] = passport
            return passport

    async def get_context_passport(
        self,
        alignment_id: str,
        profile_id: str,
        language: str | None = None,
    ) -> ContextPassport | None:
        candidates = [
            passport
            for passport in self._context_passports.values()
            if passport.analysis_id == alignment_id
            and passport.profile_id == profile_id
            and (language is None or passport.language == language)
        ]
        return max(candidates, key=lambda item: (item.created_at, item.id), default=None)

    async def repository_id_for_target(
        self,
        target_type: str,
        target_id: str,
    ) -> str | None:
        if target_type == "alignment":
            alignment = self._alignments.get(target_id)
            return alignment.repository_id if alignment is not None else None
        if target_type == "finding":
            alignment = next(
                (
                    item
                    for item in self._alignments.values()
                    if any(finding.id == target_id for finding in item.findings)
                ),
                None,
            )
            return alignment.repository_id if alignment is not None else None
        if target_type == "knowledge_node":
            node = self._nodes.get(target_id)
            return node.repository_id if node is not None else None
        if target_type == "knowledge_node_version":
            version = self._node_version_ids.get(target_id)
            node = self._nodes.get(version.node_id) if version is not None else None
            return node.repository_id if node is not None else None
        return None

    async def persist_worker_result(
        self,
        job_id: str,
        run: AiRun,
        alignment: Alignment,
        *,
        expected_head_sha: str,
        expected_main_sha: str | None,
    ) -> Alignment:
        async with self._lock:
            job = self._jobs.get(job_id)
            repository = self._repository_records.get(alignment.repository_id)
            if job is None:
                raise KeyError(job_id)
            if repository is None:
                raise KeyError(alignment.repository_id)
            if job.repository_id != alignment.repository_id or run.job_id != job_id:
                raise AppendOnlyViolation("worker result provenance does not match its job")
            if run.validation_status is not ValidationStatus.VALID:
                raise AppendOnlyViolation("worker result must be validated")
            if job.head_sha != expected_head_sha or alignment.head_sha != expected_head_sha:
                raise StaleRepositoryStateError("worker head SHA is stale")
            if (
                expected_main_sha is not None
                and repository.main_commit_sha != expected_main_sha
            ):
                raise StaleRepositoryStateError("repository main SHA is stale")

            run_key = (run.job_id, run.input_hash, run.prompt_version)
            existing_run_id = self._ai_run_keys.get(run_key)
            if existing_run_id is not None:
                existing_run = self._ai_runs[existing_run_id]
                if existing_run != run:
                    raise AppendOnlyViolation("AI run input already exists with different data")
            elif run.id in self._ai_runs:
                if self._ai_runs[run.id] != run:
                    raise AppendOnlyViolation("AI run ID already exists with different data")
            else:
                self._ai_runs[run.id] = run
                self._ai_run_keys[run_key] = run.id

            natural_key = (
                alignment.repository_id,
                alignment.pr_number,
                alignment.head_sha,
                alignment.knowledge_revision,
            )
            result_id = self._result_job_ids.get(job_id)
            natural_result_id = self._alignment_keys.get(natural_key)
            existing_id = result_id or natural_result_id
            if existing_id is not None:
                existing = self._alignments[existing_id]
                if existing != alignment:
                    raise AppendOnlyViolation(
                        "validated result already exists with different data"
                    )
                self._result_job_ids[job_id] = existing.id
                return existing
            if alignment.id in self._alignments:
                raise AppendOnlyViolation("alignment ID already exists with different data")
            self._alignments[alignment.id] = alignment
            self._alignment_keys[natural_key] = alignment.id
            self._result_job_ids[job_id] = alignment.id
            return alignment

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
