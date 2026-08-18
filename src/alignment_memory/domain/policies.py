import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from alignment_memory.domain.entities import (
    EvidenceReference,
    Finding,
    Job,
    KnowledgeNodeVersion,
    Override,
    Source,
    SourceVersion,
)
from alignment_memory.domain.enums import (
    AlignmentOutcome,
    EvidenceRole,
    JobStatus,
    KnowledgeStatus,
    NodeType,
    OverrideType,
)
from alignment_memory.domain.errors import (
    AppendOnlyViolation,
    ConflictPreconditionError,
    EvidenceValidationError,
    InvalidStateTransition,
)

_CONFLICT_NODE_TYPES = frozenset({NodeType.GOAL, NodeType.REQUIREMENT, NodeType.DECISION})

_JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.FETCHING, JobStatus.FAILED}),
    JobStatus.FETCHING: frozenset({JobStatus.ANALYZING, JobStatus.FAILED}),
    JobStatus.ANALYZING: frozenset({JobStatus.VALIDATING, JobStatus.FAILED}),
    JobStatus.VALIDATING: frozenset({JobStatus.PERSISTING, JobStatus.FAILED}),
    JobStatus.PERSISTING: frozenset({JobStatus.WRITING_GITHUB, JobStatus.FAILED}),
    JobStatus.WRITING_GITHUB: frozenset({JobStatus.COMPLETED, JobStatus.FAILED}),
    JobStatus.COMPLETED: frozenset(),
    JobStatus.FAILED: frozenset(),
}

_JOB_PROGRESS: dict[JobStatus, int] = {
    JobStatus.QUEUED: 0,
    JobStatus.FETCHING: 15,
    JobStatus.ANALYZING: 35,
    JobStatus.VALIDATING: 60,
    JobStatus.PERSISTING: 75,
    JobStatus.WRITING_GITHUB: 90,
    JobStatus.COMPLETED: 100,
}


def normalize_stored_body(content: str) -> str:
    """Normalize storage representation without weakening exact-quote matching."""

    normalized_newlines = content.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", normalized_newlines)


def exact_quote_is_present(exact_quote: str, stored_body: str) -> bool:
    if not exact_quote.strip():
        return False
    normalized_quote = normalize_stored_body(exact_quote)
    return normalized_quote in normalize_stored_body(stored_body)


def verify_evidence_reference(
    evidence: EvidenceReference,
    source: Source,
    source_version: SourceVersion,
) -> EvidenceReference:
    if evidence.source_version_id != source_version.id:
        raise EvidenceValidationError("evidence source_version_id does not match source version")
    if source_version.source_id != source.id:
        raise EvidenceValidationError("source version does not belong to source")
    if evidence.url != source.url:
        raise EvidenceValidationError("evidence URL does not match source URL")
    if not exact_quote_is_present(evidence.exact_quote, source_version.content):
        raise EvidenceValidationError("exact quote is not present in normalized stored body")
    return replace(evidence, verified=True)


def is_supported_direct_conflict(finding: Finding) -> bool:
    return (
        finding.finding_type is AlignmentOutcome.DIRECT_CONFLICT
        and finding.contradicts
        and not finding.uncertain
        and finding.target_node_id is not None
        and finding.target_node_type in _CONFLICT_NODE_TYPES
        and finding.target_node_status is KnowledgeStatus.ACTIVE
        and bool(finding.evidence)
        and all(evidence.verified for evidence in finding.evidence)
    )


def require_supported_direct_conflict(finding: Finding) -> None:
    if not is_supported_direct_conflict(finding):
        raise ConflictPreconditionError(
            "Direct Conflict requires a verified contradiction against an active "
            "Goal, Requirement, or Decision"
        )


def determine_alignment_outcome(
    findings: Sequence[Finding],
    *,
    context_is_sufficient: bool = True,
) -> AlignmentOutcome:
    if any(is_supported_direct_conflict(finding) for finding in findings):
        return AlignmentOutcome.DIRECT_CONFLICT

    unsupported_conflict = any(
        finding.finding_type is AlignmentOutcome.DIRECT_CONFLICT for finding in findings
    )
    missing_or_uncertain = any(
        finding.finding_type is AlignmentOutcome.MISSING_ALIGNMENT or finding.uncertain
        for finding in findings
    )
    if not context_is_sufficient or unsupported_conflict or missing_or_uncertain:
        return AlignmentOutcome.MISSING_ALIGNMENT

    return AlignmentOutcome.ALIGNED


def transition_job(
    job: Job,
    next_status: JobStatus,
    *,
    occurred_at: datetime,
    error_code: str | None = None,
) -> Job:
    if next_status not in _JOB_TRANSITIONS[job.status]:
        raise InvalidStateTransition(f"cannot transition job from {job.status} to {next_status}")
    if next_status is JobStatus.FAILED and not error_code:
        raise InvalidStateTransition("failed transition requires error_code")
    if next_status is not JobStatus.FAILED and error_code is not None:
        raise InvalidStateTransition("error_code is only valid for a failed transition")

    return replace(
        job,
        status=next_status,
        progress=job.progress if next_status is JobStatus.FAILED else _JOB_PROGRESS[next_status],
        updated_at=occurred_at,
        error_code=error_code,
        completed_at=occurred_at
        if next_status in {JobStatus.COMPLETED, JobStatus.FAILED}
        else None,
    )


def append_source_version(
    existing: Sequence[SourceVersion],
    new_version: SourceVersion,
) -> tuple[SourceVersion, ...]:
    if any(version.source_id != new_version.source_id for version in existing):
        raise AppendOnlyViolation("source version history cannot mix sources")
    if any(version.id == new_version.id for version in existing):
        raise AppendOnlyViolation("source version ID already exists")
    if any(version.content_hash == new_version.content_hash for version in existing):
        raise AppendOnlyViolation("source content hash already exists")
    return (*existing, new_version)


def append_knowledge_node_version(
    existing: Sequence[KnowledgeNodeVersion],
    new_version: KnowledgeNodeVersion,
) -> tuple[KnowledgeNodeVersion, ...]:
    if any(version.node_id != new_version.node_id for version in existing):
        raise AppendOnlyViolation("knowledge version history cannot mix nodes")
    expected_revision = len(existing) + 1
    if new_version.revision != expected_revision:
        raise AppendOnlyViolation(f"knowledge revision must be {expected_revision}")
    if any(version.id == new_version.id for version in existing):
        raise AppendOnlyViolation("knowledge version ID already exists")
    if existing and new_version.supersedes_version_id != existing[-1].id:
        raise AppendOnlyViolation("new knowledge version must supersede the previous version")
    if not existing and new_version.supersedes_version_id is not None:
        raise AppendOnlyViolation("first knowledge version cannot supersede another version")
    return (*existing, new_version)


@dataclass(frozen=True, slots=True, kw_only=True)
class CorrectionEvidence:
    override_id: str
    target_type: str
    target_id: str
    override_type: OverrideType
    reason: str
    actor_profile_id: str
    created_at: datetime
    role: EvidenceRole = EvidenceRole.CORRECTION


@dataclass(frozen=True, slots=True, kw_only=True)
class OverrideApplication:
    findings: tuple[Finding, ...]
    overrides: tuple[Override, ...]
    correction_evidence: CorrectionEvidence


def append_override(
    findings: Sequence[Finding],
    existing_overrides: Sequence[Override],
    override: Override,
) -> OverrideApplication:
    if any(item.id == override.id for item in existing_overrides):
        raise AppendOnlyViolation("override ID already exists")

    preserved_findings = tuple(findings)
    appended_overrides = (*existing_overrides, override)
    correction = CorrectionEvidence(
        override_id=override.id,
        target_type=override.target_type,
        target_id=override.target_id,
        override_type=override.override_type,
        reason=override.reason,
        actor_profile_id=override.actor_profile_id,
        created_at=override.created_at,
    )
    return OverrideApplication(
        findings=preserved_findings,
        overrides=appended_overrides,
        correction_evidence=correction,
    )
