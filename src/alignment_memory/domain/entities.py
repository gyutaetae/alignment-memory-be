from dataclasses import dataclass
from datetime import datetime

from alignment_memory.domain.enums import (
    AlignmentOutcome,
    EvidenceRole,
    HandshakeResponse,
    JobStatus,
    JobType,
    KnowledgeStatus,
    NodeType,
    OverrideType,
    ValidationStatus,
)
from alignment_memory.domain.errors import DomainValidationError


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise DomainValidationError(f"{field_name} is required")


@dataclass(frozen=True, slots=True, kw_only=True)
class RepositoryIdentity:
    github_repository_id: int
    owner: str
    name: str

    def __post_init__(self) -> None:
        if self.github_repository_id <= 0:
            raise DomainValidationError("github_repository_id must be positive")
        _require_text(self.owner, "owner")
        _require_text(self.name, "name")

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True, slots=True, kw_only=True)
class Source:
    id: str
    repository_id: str
    source_type: str
    external_id: str
    url: str

    def __post_init__(self) -> None:
        for field_name in ("id", "repository_id", "source_type", "external_id", "url"):
            _require_text(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceVersion:
    id: str
    source_id: str
    external_version: str
    content: str
    content_hash: str
    occurred_at: datetime
    ingested_at: datetime
    author_profile_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("id", "source_id", "external_version", "content_hash"):
            _require_text(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceReference:
    source_version_id: str
    url: str
    exact_quote: str
    role: EvidenceRole
    verified: bool = False

    def __post_init__(self) -> None:
        for field_name in ("source_version_id", "url", "exact_quote"):
            _require_text(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeNode:
    id: str
    repository_id: str
    node_type: NodeType
    logical_key: str
    current_version_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("id", "repository_id", "logical_key"):
            _require_text(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeNodeVersion:
    id: str
    node_id: str
    revision: int
    title: str
    summary: str
    status: KnowledgeStatus
    created_by: str
    created_at: datetime
    evidence: tuple[EvidenceReference, ...]
    ai_run_id: str | None = None
    supersedes_version_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("id", "node_id", "title", "summary", "created_by"):
            _require_text(getattr(self, field_name), field_name)
        if self.revision < 1:
            raise DomainValidationError("revision must be at least 1")
        if not self.evidence:
            raise DomainValidationError("knowledge node version requires evidence")


@dataclass(frozen=True, slots=True, kw_only=True)
class KnowledgeEdge:
    id: str
    repository_id: str
    from_node_id: str
    to_node_id: str
    relation_type: str
    valid_from_revision: int
    evidence: tuple[EvidenceReference, ...]
    valid_to_revision: int | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "id",
            "repository_id",
            "from_node_id",
            "to_node_id",
            "relation_type",
        ):
            _require_text(getattr(self, field_name), field_name)
        if self.from_node_id == self.to_node_id:
            raise DomainValidationError("knowledge edge endpoints must differ")
        if self.valid_from_revision < 1:
            raise DomainValidationError("valid_from_revision must be at least 1")
        if (
            self.valid_to_revision is not None
            and self.valid_to_revision < self.valid_from_revision
        ):
            raise DomainValidationError("valid_to_revision cannot precede valid_from_revision")
        if not self.evidence:
            raise DomainValidationError("knowledge edge requires evidence")


@dataclass(frozen=True, slots=True, kw_only=True)
class Finding:
    id: str
    analysis_id: str
    finding_type: AlignmentOutcome
    explanation: str
    recommended_action: str
    evidence: tuple[EvidenceReference, ...]
    target_node_id: str | None = None
    target_node_type: NodeType | None = None
    target_node_status: KnowledgeStatus | None = None
    contradicts: bool = False
    uncertain: bool = False

    def __post_init__(self) -> None:
        for field_name in ("id", "analysis_id", "explanation", "recommended_action"):
            _require_text(getattr(self, field_name), field_name)
        if not self.evidence:
            raise DomainValidationError("finding requires evidence")


@dataclass(frozen=True, slots=True, kw_only=True)
class Alignment:
    id: str
    repository_id: str
    pr_number: int
    head_sha: str
    knowledge_revision: int
    outcome: AlignmentOutcome
    findings: tuple[Finding, ...]
    ai_run_id: str
    created_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("id", "repository_id", "head_sha", "ai_run_id"):
            _require_text(getattr(self, field_name), field_name)
        if self.pr_number <= 0:
            raise DomainValidationError("pr_number must be positive")
        if self.knowledge_revision < 0:
            raise DomainValidationError("knowledge_revision cannot be negative")


@dataclass(frozen=True, slots=True, kw_only=True)
class Override:
    id: str
    target_type: str
    target_id: str
    override_type: OverrideType
    reason: str
    actor_profile_id: str
    created_at: datetime
    created_node_version_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "id",
            "target_type",
            "target_id",
            "reason",
            "actor_profile_id",
        ):
            _require_text(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True, kw_only=True)
class Handshake:
    id: str
    analysis_id: str
    profile_id: str
    response: HandshakeResponse
    message: str | None
    source_language: str
    created_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("id", "analysis_id", "profile_id", "source_language"):
            _require_text(getattr(self, field_name), field_name)


@dataclass(frozen=True, slots=True, kw_only=True)
class ContextPassport:
    id: str
    analysis_id: str
    profile_id: str
    language: str
    content: str
    source_version_ids: tuple[str, ...]
    ambiguities: tuple[str, ...]
    ai_run_id: str
    created_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("id", "analysis_id", "profile_id", "language", "content", "ai_run_id"):
            _require_text(getattr(self, field_name), field_name)
        if not self.source_version_ids:
            raise DomainValidationError("context passport requires source versions")
        if any(not source_version_id.strip() for source_version_id in self.source_version_ids):
            raise DomainValidationError("source_version_ids cannot contain blank values")


@dataclass(frozen=True, slots=True, kw_only=True)
class Job:
    id: str
    repository_id: str
    event_key: str
    job_type: JobType
    status: JobStatus
    progress: int
    created_at: datetime
    updated_at: datetime
    head_sha: str | None = None
    error_code: str | None = None
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in ("id", "repository_id", "event_key"):
            _require_text(getattr(self, field_name), field_name)
        if not 0 <= self.progress <= 100:
            raise DomainValidationError("progress must be between 0 and 100")
        if self.status is JobStatus.FAILED and not self.error_code:
            raise DomainValidationError("failed job requires error_code")
        if self.status is JobStatus.COMPLETED and self.progress != 100:
            raise DomainValidationError("completed job progress must be 100")


@dataclass(frozen=True, slots=True, kw_only=True)
class AiRun:
    id: str
    job_id: str
    provider: str
    requested_model: str
    actual_model: str
    prompt_version: str
    input_hash: str
    output_json: dict[str, object]
    validation_status: ValidationStatus
    usage: dict[str, int | float]
    created_at: datetime
    completed_at: datetime | None = None
    cost: float | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "id",
            "job_id",
            "provider",
            "requested_model",
            "actual_model",
            "prompt_version",
            "input_hash",
        ):
            _require_text(getattr(self, field_name), field_name)
        if self.cost is not None and self.cost < 0:
            raise DomainValidationError("cost cannot be negative")
