from datetime import UTC, datetime

import pytest

from alignment_memory.domain import (
    AlignmentOutcome,
    AppendOnlyViolation,
    DomainValidationError,
    EvidenceReference,
    EvidenceRole,
    Finding,
    KnowledgeStatus,
    NodeType,
    Override,
    OverrideType,
    SourceVersion,
    append_override,
    append_source_version,
)


def _finding() -> Finding:
    return Finding(
        id="finding-1",
        analysis_id="analysis-1",
        finding_type=AlignmentOutcome.DIRECT_CONFLICT,
        target_node_id="decision-1",
        target_node_type=NodeType.DECISION,
        target_node_status=KnowledgeStatus.ACTIVE,
        contradicts=True,
        explanation="The PR proposes an excluded browser extension.",
        recommended_action="Remove the extension integration.",
        evidence=(
            EvidenceReference(
                source_version_id="source-version-1",
                url="https://github.com/gyutaetae/harness/blob/main/docs/prd.md",
                exact_quote="Browser extensions are excluded.",
                role=EvidenceRole.CONTRADICTS,
                verified=True,
            ),
        ),
    )


def _override(*, reason: str = "The PR does not add an extension.") -> Override:
    return Override(
        id="override-1",
        target_type="finding",
        target_id="finding-1",
        override_type=OverrideType.FALSE_POSITIVE,
        reason=reason,
        actor_profile_id="profile-1",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


def test_override_requires_reason_and_preserves_prior_findings() -> None:
    finding = _finding()
    result = append_override((finding,), (), _override())

    assert result.findings == (finding,)
    assert result.findings[0] is finding
    assert result.overrides == (_override(),)
    assert result.correction_evidence.target_id == finding.id
    assert result.correction_evidence.reason == _override().reason
    assert result.correction_evidence.role is EvidenceRole.CORRECTION

    with pytest.raises(DomainValidationError, match="reason"):
        _override(reason="   ")


def test_override_history_rejects_duplicate_without_deleting_history() -> None:
    existing = (_override(),)

    with pytest.raises(AppendOnlyViolation, match="already exists"):
        append_override((_finding(),), existing, _override())

    assert existing == (_override(),)


def test_source_versions_are_appended_and_content_hashes_are_idempotent() -> None:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    first = SourceVersion(
        id="version-1",
        source_id="source-1",
        external_version="abc",
        content="first",
        content_hash="hash-1",
        occurred_at=now,
        ingested_at=now,
    )
    second = SourceVersion(
        id="version-2",
        source_id="source-1",
        external_version="def",
        content="second",
        content_hash="hash-2",
        occurred_at=now,
        ingested_at=now,
    )

    history = append_source_version((first,), second)
    assert history == (first, second)

    duplicate_hash = SourceVersion(
        id="version-3",
        source_id="source-1",
        external_version="ghi",
        content="second",
        content_hash="hash-2",
        occurred_at=now,
        ingested_at=now,
    )
    with pytest.raises(AppendOnlyViolation, match="content hash"):
        append_source_version(history, duplicate_hash)
