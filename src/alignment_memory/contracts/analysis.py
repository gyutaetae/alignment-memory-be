from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from alignment_memory.domain.enums import (
    AlignmentOutcome,
    EvidenceRole,
    KnowledgeStatus,
    NodeType,
)

NonEmptyText = Annotated[str, Field(min_length=1)]


class StrictAnalysisModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AnalysisEvidence(StrictAnalysisModel):
    source_version_id: NonEmptyText
    url: HttpUrl
    exact_quote: NonEmptyText
    role: EvidenceRole


EvidenceSet = Annotated[tuple[AnalysisEvidence, ...], Field(min_length=1)]


class AnalysisNode(StrictAnalysisModel):
    logical_key: NonEmptyText
    node_type: NodeType
    title: NonEmptyText
    summary: NonEmptyText
    status: KnowledgeStatus
    evidence: EvidenceSet


class AnalysisFinding(StrictAnalysisModel):
    finding_type: AlignmentOutcome
    target_node_logical_key: NonEmptyText | None
    target_node_type: NodeType | None
    target_node_status: KnowledgeStatus | None
    contradicts: bool
    uncertain: bool
    explanation: NonEmptyText
    recommended_action: NonEmptyText
    evidence: EvidenceSet

    @model_validator(mode="after")
    def validate_direct_conflict_shape(self) -> Self:
        if self.finding_type is not AlignmentOutcome.DIRECT_CONFLICT:
            return self

        eligible_types = {NodeType.GOAL, NodeType.REQUIREMENT, NodeType.DECISION}
        if (
            self.target_node_logical_key is None
            or self.target_node_type not in eligible_types
            or self.target_node_status is not KnowledgeStatus.ACTIVE
            or not self.contradicts
            or self.uncertain
        ):
            raise ValueError(
                "Direct Conflict requires a certain contradiction against an active "
                "Goal, Requirement, or Decision"
            )
        return self


class AnalysisEdge(StrictAnalysisModel):
    from_node_logical_key: NonEmptyText
    to_node_logical_key: NonEmptyText
    relation_type: NonEmptyText
    evidence: EvidenceSet


class AnalysisResult(StrictAnalysisModel):
    outcome: AlignmentOutcome
    nodes: tuple[AnalysisNode, ...]
    findings: tuple[AnalysisFinding, ...]
    edges: tuple[AnalysisEdge, ...]

    @model_validator(mode="after")
    def validate_outcome_consistency(self) -> Self:
        direct_findings = tuple(
            finding
            for finding in self.findings
            if finding.finding_type is AlignmentOutcome.DIRECT_CONFLICT
        )
        uncertain_findings = tuple(finding for finding in self.findings if finding.uncertain)

        if self.outcome is AlignmentOutcome.DIRECT_CONFLICT and not direct_findings:
            raise ValueError("Direct Conflict outcome requires a supported conflict finding")
        if self.outcome is not AlignmentOutcome.DIRECT_CONFLICT and direct_findings:
            raise ValueError("supported conflict finding requires Direct Conflict outcome")
        if uncertain_findings and self.outcome is not AlignmentOutcome.MISSING_ALIGNMENT:
            raise ValueError("uncertainty requires Missing Alignment outcome")
        return self
