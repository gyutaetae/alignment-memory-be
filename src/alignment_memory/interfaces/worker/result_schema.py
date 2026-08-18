from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from alignment_memory.contracts import AnalysisResult
from alignment_memory.domain import AlignmentOutcome
from alignment_memory.ports import (
    AnalysisDocument,
    AnalysisRequest,
    LlmValidationError,
    validate_analysis_result_evidence,
)

NonEmptyText = Annotated[str, Field(min_length=1)]
CommitSha = Annotated[str, Field(pattern=r"^[0-9a-f]{40,64}$")]
InputHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ArtifactEvent(ArtifactModel):
    event_name: Literal["pull_request", "push", "workflow_dispatch"] = Field(alias="eventName")
    event_key: NonEmptyText = Field(alias="eventKey")
    repository_id: NonEmptyText = Field(alias="repositoryId")
    repository_full_name: NonEmptyText = Field(alias="repositoryFullName")
    github_repository_id: Annotated[int, Field(gt=0)] = Field(alias="githubRepositoryId")
    actor_login: NonEmptyText = Field(alias="actorLogin")
    head_sha: CommitSha = Field(alias="headSha")
    main_sha: CommitSha = Field(alias="mainSha")
    proposed_change: NonEmptyText = Field(alias="proposedChange")
    source_url: HttpUrl = Field(alias="sourceUrl")
    pr_number: Annotated[int, Field(gt=0)] | None = Field(default=None, alias="prNumber")
    publication_kind: Literal["pr_comment", "generated_wiki"] = Field(
        alias="publicationKind"
    )

    @model_validator(mode="after")
    def validate_publication_kind(self) -> Self:
        expected = "pr_comment" if self.pr_number is not None else "generated_wiki"
        if self.publication_kind != expected:
            raise ValueError("publication kind must be derived from the allowlisted event")
        if self.event_name == "pull_request" and self.pr_number is None:
            raise ValueError("pull request artifacts require a PR number")
        if self.event_name != "pull_request" and self.pr_number is not None:
            raise ValueError("repository artifacts cannot select a PR target")
        return self


class ArtifactDocument(ArtifactModel):
    source_version_id: NonEmptyText = Field(alias="sourceVersionId")
    source_type: NonEmptyText = Field(alias="sourceType")
    url: HttpUrl
    content: NonEmptyText

    def as_analysis_document(self) -> AnalysisDocument:
        return AnalysisDocument(
            source_version_id=self.source_version_id,
            source_type=self.source_type,
            url=str(self.url),
            content=self.content,
        )


class ValidatedAnalysisArtifact(ArtifactModel):
    schema_version: Literal["alignment-memory/v1"] = Field(alias="schemaVersion")
    validation_status: Literal["validated"] = Field(alias="validationStatus")
    job_id: NonEmptyText = Field(alias="jobId")
    event: ArtifactEvent
    knowledge_revision: Annotated[int, Field(ge=0)] = Field(alias="knowledgeRevision")
    context_is_sufficient: bool = Field(alias="contextIsSufficient")
    prompt_version: NonEmptyText = Field(alias="promptVersion")
    provider: NonEmptyText
    requested_model: NonEmptyText = Field(alias="requestedModel")
    actual_model: NonEmptyText = Field(alias="actualModel")
    input_hash: InputHash = Field(alias="inputHash")
    usage: dict[str, int | float] = Field(default_factory=dict)
    cost: Annotated[float, Field(ge=0)] | None = None
    documents: Annotated[tuple[ArtifactDocument, ...], Field(min_length=1)]
    analysis: AnalysisResult
    created_at: datetime = Field(alias="createdAt")

    @model_validator(mode="after")
    def validate_provenance_and_evidence(self) -> Self:
        if self.created_at.tzinfo is None:
            raise ValueError("artifact creation time must include a timezone")
        document_ids = [document.source_version_id for document in self.documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("artifact documents must have unique source version IDs")
        request = self.as_analysis_request()
        if request.input_hash != self.input_hash:
            raise ValueError("artifact input hash does not match its validated documents")
        try:
            validate_analysis_result_evidence(self.analysis, request.documents)
        except LlmValidationError as error:
            raise ValueError(str(error)) from error
        expected_outcome = _expected_outcome(self.analysis, self.context_is_sufficient)
        if expected_outcome is not self.analysis.outcome:
            raise ValueError("artifact outcome does not match validated findings")
        return self

    def as_analysis_request(self) -> AnalysisRequest:
        return AnalysisRequest(
            job_id=self.job_id,
            repository_id=self.event.repository_id,
            pr_number=self.event.pr_number or 0,
            head_sha=self.event.head_sha,
            knowledge_revision=self.knowledge_revision,
            prompt_version=self.prompt_version,
            documents=tuple(document.as_analysis_document() for document in self.documents),
            context_is_sufficient=self.context_is_sufficient,
        )


def _expected_outcome(
    analysis: AnalysisResult,
    context_is_sufficient: bool,
) -> AlignmentOutcome:
    if any(
        finding.finding_type is AlignmentOutcome.DIRECT_CONFLICT
        for finding in analysis.findings
    ):
        return AlignmentOutcome.DIRECT_CONFLICT
    if not context_is_sufficient or any(
        finding.finding_type is AlignmentOutcome.MISSING_ALIGNMENT or finding.uncertain
        for finding in analysis.findings
    ):
        return AlignmentOutcome.MISSING_ALIGNMENT
    return AlignmentOutcome.ALIGNED
