from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from alignment_memory.contracts import AnalysisEvidence, AnalysisResult
from alignment_memory.domain import (
    AiRun,
    EvidenceReference,
    KnowledgeEdge,
    KnowledgeNode,
    KnowledgeNodeVersion,
    Source,
    SourceVersion,
    verify_evidence_reference,
)
from alignment_memory.ports.control_plane import RepositoryRecord
from alignment_memory.ports.repositories import PersistenceRepository


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectionDocument:
    source_id: str
    source_version_id: str
    source_type: str
    external_id: str
    external_version: str
    url: str
    content: str
    content_hash: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectKnowledgeCommand:
    job_id: str
    repository_id: str
    event_key: str
    head_sha: str
    expected_revision: int
    run: AiRun
    documents: tuple[ProjectionDocument, ...]
    analysis: AnalysisResult
    created_at: datetime


class ProjectKnowledgeService:
    """Persist one validated repository analysis as append-only project memory."""

    def __init__(self, repository: PersistenceRepository) -> None:
        self._repository = repository

    async def apply(self, command: ProjectKnowledgeCommand) -> RepositoryRecord:
        await self._persist_documents(command)
        await self._repository.persist_ai_run(command.run)

        snapshots = await self._repository.list_knowledge_snapshots(command.repository_id)
        node_by_key = {snapshot.node.logical_key: snapshot.node for snapshot in snapshots}
        for item in command.analysis.nodes:
            node = node_by_key.get(item.logical_key)
            if node is None:
                node = await self._repository.add_knowledge_node(
                    KnowledgeNode(
                        id=self._stable_id(
                            "knowledge-node", command.repository_id, item.logical_key
                        ),
                        repository_id=command.repository_id,
                        node_type=item.node_type,
                        logical_key=item.logical_key,
                    )
                )
                node_by_key[item.logical_key] = node

            version_id = self._stable_id("knowledge-version", node.id, command.event_key)
            history = await self._repository.list_knowledge_node_versions(node.id)
            if not any(version.id == version_id for version in history):
                await self._repository.append_knowledge_node_version(
                    KnowledgeNodeVersion(
                        id=version_id,
                        node_id=node.id,
                        revision=len(history) + 1,
                        title=item.title,
                        summary=item.summary,
                        status=item.status,
                        created_by="alignment-memory[bot]",
                        created_at=command.created_at,
                        evidence=tuple(
                            [await self._verified_evidence(evidence) for evidence in item.evidence]
                        ),
                        ai_run_id=command.run.id,
                        supersedes_version_id=history[-1].id if history else None,
                    )
                )

        existing_edges = {
            edge.id for edge in await self._repository.list_knowledge_edges(command.repository_id)
        }
        for item in command.analysis.edges:
            from_node = node_by_key.get(item.from_node_logical_key)
            to_node = node_by_key.get(item.to_node_logical_key)
            if from_node is None or to_node is None:
                raise ValueError("knowledge edge references an unknown node")
            edge_id = self._stable_id(
                "knowledge-edge",
                command.repository_id,
                item.from_node_logical_key,
                item.relation_type,
                item.to_node_logical_key,
                command.event_key,
            )
            if edge_id in existing_edges:
                continue
            await self._repository.add_knowledge_edge(
                KnowledgeEdge(
                    id=edge_id,
                    repository_id=command.repository_id,
                    from_node_id=from_node.id,
                    to_node_id=to_node.id,
                    relation_type=item.relation_type,
                    valid_from_revision=command.expected_revision + 1,
                    evidence=tuple(
                        [await self._verified_evidence(evidence) for evidence in item.evidence]
                    ),
                )
            )

        return await self._repository.advance_repository_revision(
            command.repository_id,
            expected_revision=command.expected_revision,
            head_sha=command.head_sha,
        )

    async def _persist_documents(self, command: ProjectKnowledgeCommand) -> None:
        for item in command.documents:
            source = await self._repository.add_source(
                Source(
                    id=item.source_id,
                    repository_id=command.repository_id,
                    source_type=item.source_type,
                    external_id=item.external_id,
                    url=item.url,
                )
            )
            await self._repository.append_source_version(
                SourceVersion(
                    id=item.source_version_id,
                    source_id=source.id,
                    external_version=item.external_version,
                    content=item.content,
                    content_hash=item.content_hash,
                    occurred_at=item.occurred_at,
                    ingested_at=command.created_at,
                )
            )

    async def _verified_evidence(self, evidence: AnalysisEvidence) -> EvidenceReference:
        stored = await self._repository.get_source_version_with_source(evidence.source_version_id)
        if stored is None:
            raise ValueError("knowledge evidence references an unknown source version")
        source, version = stored
        return verify_evidence_reference(
            EvidenceReference(
                source_version_id=evidence.source_version_id,
                url=str(evidence.url),
                exact_quote=evidence.exact_quote,
                role=evidence.role,
            ),
            source,
            version,
        )

    @staticmethod
    def _stable_id(*parts: str) -> str:
        return str(uuid5(NAMESPACE_URL, ":".join(parts)))
