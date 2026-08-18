from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from alignment_memory.contracts.analysis import AnalysisEvidence, AnalysisResult
from alignment_memory.domain import (
    AiRun,
    Alignment,
    EvidenceReference,
    Finding,
    Source,
    SourceVersion,
    ValidationStatus,
    determine_alignment_outcome,
    verify_evidence_reference,
)
from alignment_memory.ports.github import (
    CollectedSource,
    GitHubPort,
    GitHubRepositoryRef,
)
from alignment_memory.ports.llm import (
    AnalysisDocument,
    AnalysisRequest,
    LlmAnalysis,
    LlmPort,
    LlmUsage,
    LlmValidationError,
    validate_analysis_result_evidence,
)
from alignment_memory.ports.repositories import PersistenceRepository

Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True, kw_only=True)
class AnalyzePullRequestCommand:
    job_id: str
    repository: GitHubRepositoryRef
    pr_number: int
    head_sha: str
    knowledge_revision: int
    prompt_version: str
    actor_login: str | None = None
    context_sources: tuple[CollectedSource, ...] = ()
    context_is_sufficient: bool = True


class AlignmentAnalysisService:
    """Collect, validate, decide, and persist one idempotent PR analysis."""

    def __init__(
        self,
        *,
        github: GitHubPort,
        llm: LlmPort,
        repository: PersistenceRepository,
        clock: Clock | None = None,
    ) -> None:
        self._github = github
        self._llm = llm
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    async def analyze_pull_request(
        self,
        command: AnalyzePullRequestCommand,
    ) -> Alignment:
        existing_result = await self._repository.get_result_for_job(command.job_id)
        if existing_result is not None:
            return existing_result

        batch = await self._github.fetch_pr_context(
            command.repository,
            number=command.pr_number,
            head_sha=command.head_sha,
            actor_login=command.actor_login,
        )
        collected = self._deduplicate_sources((*batch.sources, *command.context_sources))
        persisted = await self._persist_sources(collected)
        documents = tuple(
            AnalysisDocument(
                source_version_id=version.id,
                source_type=source.source_type,
                url=source.url,
                content=version.content,
            )
            for source, version in persisted
        )
        request = AnalysisRequest(
            job_id=command.job_id,
            repository_id=command.repository.repository_id,
            pr_number=command.pr_number,
            head_sha=command.head_sha,
            knowledge_revision=command.knowledge_revision,
            prompt_version=command.prompt_version,
            documents=documents,
            context_is_sufficient=command.context_is_sufficient,
        )
        llm_analysis = await self._load_or_run_analysis(request)
        validate_analysis_result_evidence(llm_analysis.result, documents)
        self._verify_all_evidence(llm_analysis.result, persisted)

        now = self._clock()
        stored_run = await self._repository.persist_ai_run(
            AiRun(
                id=llm_analysis.run_id,
                job_id=command.job_id,
                provider=llm_analysis.provider,
                requested_model=llm_analysis.requested_model,
                actual_model=llm_analysis.actual_model,
                prompt_version=llm_analysis.prompt_version,
                input_hash=llm_analysis.input_hash,
                output_json=llm_analysis.output_json,
                validation_status=ValidationStatus.VALID,
                usage=llm_analysis.usage.as_dict(),
                cost=llm_analysis.usage.cost,
                created_at=now,
                completed_at=now,
            )
        )

        analysis_id = self._stable_id(
            "alignment",
            command.repository.repository_id,
            str(command.pr_number),
            command.head_sha,
            str(command.knowledge_revision),
            request.input_hash,
        )
        findings = tuple(
            self._finding_from_analysis(
                analysis_id,
                command.repository.repository_id,
                index,
                finding,
                persisted,
            )
            for index, finding in enumerate(llm_analysis.result.findings)
        )
        outcome = determine_alignment_outcome(
            findings,
            context_is_sufficient=command.context_is_sufficient,
        )
        alignment = Alignment(
            id=analysis_id,
            repository_id=command.repository.repository_id,
            pr_number=command.pr_number,
            head_sha=command.head_sha,
            knowledge_revision=command.knowledge_revision,
            outcome=outcome,
            findings=findings,
            ai_run_id=stored_run.id,
            created_at=now,
        )
        return await self._repository.persist_validated_result(command.job_id, alignment)

    async def _load_or_run_analysis(self, request: AnalysisRequest) -> LlmAnalysis:
        stored = await self._repository.get_ai_run(
            request.job_id,
            request.input_hash,
            request.prompt_version,
        )
        if stored is None:
            analysis = await self._llm.analyze(request)
            if (
                analysis.input_hash != request.input_hash
                or analysis.prompt_version != request.prompt_version
            ):
                raise LlmValidationError("LLM provenance does not match the analysis request")
            return analysis
        if stored.validation_status is not ValidationStatus.VALID:
            raise LlmValidationError("stored AI run is not valid")
        try:
            result = AnalysisResult.model_validate(stored.output_json)
        except ValueError as error:
            raise LlmValidationError("stored AI run no longer matches AnalysisResult") from error
        return LlmAnalysis(
            run_id=stored.id,
            result=result,
            provider=stored.provider,
            requested_model=stored.requested_model,
            actual_model=stored.actual_model,
            prompt_version=stored.prompt_version,
            input_hash=stored.input_hash,
            usage=LlmUsage(
                prompt_tokens=self._usage_int(stored.usage, "prompt_tokens"),
                completion_tokens=self._usage_int(stored.usage, "completion_tokens"),
                total_tokens=self._usage_int(stored.usage, "total_tokens"),
                cost=stored.cost,
            ),
        )

    async def _persist_sources(
        self,
        collected: Sequence[CollectedSource],
    ) -> tuple[tuple[Source, SourceVersion], ...]:
        persisted: list[tuple[Source, SourceVersion]] = []
        ingested_at = self._clock()
        for item in collected:
            source = await self._repository.add_source(
                Source(
                    id=item.source_id,
                    repository_id=item.repository_id,
                    source_type=item.source_type.value,
                    external_id=item.external_id,
                    url=item.url,
                )
            )
            version = await self._repository.append_source_version(
                SourceVersion(
                    id=item.source_version_id,
                    source_id=source.id,
                    external_version=item.external_version,
                    content=item.content,
                    content_hash=item.content_hash,
                    occurred_at=item.occurred_at,
                    ingested_at=ingested_at,
                )
            )
            persisted.append((source, version))
        return tuple(persisted)

    @staticmethod
    def _verify_all_evidence(
        result: AnalysisResult,
        persisted: Sequence[tuple[Source, SourceVersion]],
    ) -> None:
        indexed = {version.id: (source, version) for source, version in persisted}
        evidence_sets = [node.evidence for node in result.nodes]
        evidence_sets.extend(finding.evidence for finding in result.findings)
        evidence_sets.extend(edge.evidence for edge in result.edges)
        for evidence_set in evidence_sets:
            for evidence in evidence_set:
                source_and_version = indexed.get(evidence.source_version_id)
                if source_and_version is None:
                    raise LlmValidationError("evidence references an unpersisted source version")
                source, version = source_and_version
                verify_evidence_reference(
                    AlignmentAnalysisService._evidence_reference(evidence),
                    source,
                    version,
                )

    @staticmethod
    def _finding_from_analysis(
        analysis_id: str,
        repository_id: str,
        index: int,
        finding: object,
        persisted: Sequence[tuple[Source, SourceVersion]],
    ) -> Finding:
        from alignment_memory.contracts.analysis import AnalysisFinding

        if not isinstance(finding, AnalysisFinding):
            raise TypeError("finding must be an AnalysisFinding")
        indexed = {version.id: (source, version) for source, version in persisted}
        evidence: list[EvidenceReference] = []
        for item in finding.evidence:
            source, version = indexed[item.source_version_id]
            evidence.append(
                verify_evidence_reference(
                    AlignmentAnalysisService._evidence_reference(item),
                    source,
                    version,
                )
            )
        target_node_id = (
            AlignmentAnalysisService._stable_id(
                "knowledge-node",
                repository_id,
                finding.target_node_logical_key,
            )
            if finding.target_node_logical_key is not None
            else None
        )
        return Finding(
            id=AlignmentAnalysisService._stable_id("finding", analysis_id, str(index)),
            analysis_id=analysis_id,
            finding_type=finding.finding_type,
            explanation=finding.explanation,
            recommended_action=finding.recommended_action,
            evidence=tuple(evidence),
            target_node_id=target_node_id,
            target_node_type=finding.target_node_type,
            target_node_status=finding.target_node_status,
            contradicts=finding.contradicts,
            uncertain=finding.uncertain,
        )

    @staticmethod
    def _evidence_reference(evidence: AnalysisEvidence) -> EvidenceReference:
        return EvidenceReference(
            source_version_id=evidence.source_version_id,
            url=str(evidence.url),
            exact_quote=evidence.exact_quote,
            role=evidence.role,
        )

    @staticmethod
    def _deduplicate_sources(
        sources: Sequence[CollectedSource],
    ) -> tuple[CollectedSource, ...]:
        unique = {(source.source_id, source.content_hash): source for source in sources}
        return tuple(
            sorted(
                unique.values(),
                key=lambda source: (
                    source.source_type.value,
                    source.external_id,
                    source.content_hash,
                ),
            )
        )

    @staticmethod
    def _stable_id(kind: str, *parts: str) -> str:
        return str(uuid5(NAMESPACE_URL, ":".join(("alignment-memory", kind, *parts))))

    @staticmethod
    def _usage_int(usage: dict[str, int | float], key: str) -> int:
        value = usage.get(key, 0)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
