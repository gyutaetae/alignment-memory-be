from alignment_memory.domain import (
    AlignmentOutcome,
    ConflictPreconditionError,
    EvidenceReference,
    EvidenceRole,
    Finding,
    KnowledgeStatus,
    NodeType,
    determine_alignment_outcome,
    is_supported_direct_conflict,
    require_supported_direct_conflict,
)


def _finding(
    *,
    finding_type: AlignmentOutcome = AlignmentOutcome.DIRECT_CONFLICT,
    target_node_type: NodeType = NodeType.DECISION,
    target_node_status: KnowledgeStatus = KnowledgeStatus.ACTIVE,
    verified: bool = True,
    contradicts: bool = True,
    uncertain: bool = False,
) -> Finding:
    return Finding(
        id="finding-1",
        analysis_id="analysis-1",
        finding_type=finding_type,
        target_node_id="decision-browser-extension",
        target_node_type=target_node_type,
        target_node_status=target_node_status,
        contradicts=contradicts,
        uncertain=uncertain,
        explanation="The proposed work contradicts the recorded decision.",
        recommended_action="Remove the extension or supersede the decision.",
        evidence=(
            EvidenceReference(
                source_version_id="source-version-1",
                url="https://github.com/gyutaetae/harness/blob/main/docs/prd.md",
                exact_quote="Browser extensions are excluded from the MVP.",
                role=EvidenceRole.CONTRADICTS,
                verified=verified,
            ),
        ),
    )


def test_direct_conflict_requires_active_supported_core_knowledge() -> None:
    supported = _finding()

    assert is_supported_direct_conflict(supported) is True
    assert determine_alignment_outcome((supported,)) is AlignmentOutcome.DIRECT_CONFLICT


def test_unsupported_conflict_becomes_missing_alignment() -> None:
    against_task = _finding(target_node_type=NodeType.TASK)

    assert is_supported_direct_conflict(against_task) is False
    assert determine_alignment_outcome((against_task,)) is AlignmentOutcome.MISSING_ALIGNMENT

    try:
        require_supported_direct_conflict(against_task)
    except ConflictPreconditionError:
        pass
    else:
        raise AssertionError("unsupported Direct Conflict must be rejected")


def test_uncertainty_or_invalid_evidence_cannot_be_direct_conflict() -> None:
    uncertain = _finding(uncertain=True)
    invalid_evidence = _finding(verified=False)

    assert determine_alignment_outcome((uncertain,)) is AlignmentOutcome.MISSING_ALIGNMENT
    assert determine_alignment_outcome((invalid_evidence,)) is AlignmentOutcome.MISSING_ALIGNMENT


def test_no_supported_conflict_is_aligned_when_context_is_sufficient() -> None:
    assert determine_alignment_outcome(()) is AlignmentOutcome.ALIGNED
    assert (
        determine_alignment_outcome((), context_is_sufficient=False)
        is AlignmentOutcome.MISSING_ALIGNMENT
    )
