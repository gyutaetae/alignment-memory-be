from enum import StrEnum


class NodeType(StrEnum):
    GOAL = "goal"
    REQUIREMENT = "requirement"
    DECISION = "decision"
    TASK = "task"
    ARTIFACT = "artifact"
    RISK = "risk"


class AlignmentOutcome(StrEnum):
    ALIGNED = "aligned"
    DIRECT_CONFLICT = "direct_conflict"
    MISSING_ALIGNMENT = "missing_alignment"


class JobType(StrEnum):
    INITIAL_SYNC = "initial_sync"
    PR_ANALYSIS = "pr_analysis"
    MERGE_PUBLISH = "merge_publish"


class JobStatus(StrEnum):
    QUEUED = "queued"
    FETCHING = "fetching"
    ANALYZING = "analyzing"
    VALIDATING = "validating"
    PERSISTING = "persisting"
    WRITING_GITHUB = "writing_github"
    COMPLETED = "completed"
    FAILED = "failed"


class OverrideType(StrEnum):
    FALSE_POSITIVE = "false_positive"
    SUPERSEDE_DECISION = "supersede_decision"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class HandshakeResponse(StrEnum):
    AGREE = "agree"
    NEEDS_CLARIFICATION = "needs_clarification"
    DISAGREE = "disagree"


class KnowledgeStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DISPUTED = "disputed"


class EvidenceRole(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CORRECTION = "correction"


class ValidationStatus(StrEnum):
    PENDING = "pending"
    VALID = "valid"
    INVALID = "invalid"
