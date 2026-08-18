from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from alignment_memory.contracts.analysis import AnalysisResult
from alignment_memory.domain import HandshakeResponse, JobStatus, JobType, OverrideType

NonEmptyText = Annotated[str, Field(min_length=1)]
CommitSha = Annotated[str, Field(pattern=r"^[0-9a-f]{40,64}$")]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class HandshakeCreate(ApiModel):
    response: HandshakeResponse
    message: str | None = None
    source_language: NonEmptyText = Field(alias="sourceLanguage")


class OverrideCreate(ApiModel):
    override_type: OverrideType = Field(alias="overrideType")
    reason: NonEmptyText
    target_type: Literal[
        "alignment",
        "finding",
        "knowledge_node",
        "knowledge_node_version",
    ] = Field(default="alignment", alias="targetType")
    target_id: str | None = Field(default=None, alias="targetId")


class PassportGenerate(ApiModel):
    language: NonEmptyText


class InternalJobCreate(ApiModel):
    repository_id: NonEmptyText = Field(alias="repositoryId")
    event_key: NonEmptyText = Field(alias="eventKey")
    event_type: JobType = Field(alias="eventType")
    head_sha: CommitSha | None = Field(default=None, alias="headSha")


class InternalJobEvent(ApiModel):
    expected_status: JobStatus = Field(alias="expectedStatus")
    next_status: JobStatus = Field(alias="nextStatus")
    error_code: str | None = Field(default=None, alias="errorCode")


class WorkerResult(ApiModel):
    repository_id: NonEmptyText = Field(alias="repositoryId")
    pr_number: Annotated[int, Field(gt=0)] = Field(alias="prNumber")
    head_sha: CommitSha = Field(alias="headSha")
    main_sha: CommitSha | None = Field(default=None, alias="mainSha")
    knowledge_revision: Annotated[int, Field(ge=0)] = Field(alias="knowledgeRevision")
    provider: NonEmptyText
    requested_model: NonEmptyText = Field(alias="requestedModel")
    actual_model: NonEmptyText = Field(alias="actualModel")
    prompt_version: NonEmptyText = Field(alias="promptVersion")
    input_hash: NonEmptyText = Field(alias="inputHash")
    usage: dict[str, int | float] = Field(default_factory=dict)
    cost: Annotated[float, Field(ge=0)] | None = None
    context_is_sufficient: bool = Field(default=True, alias="contextIsSufficient")
    analysis: AnalysisResult
