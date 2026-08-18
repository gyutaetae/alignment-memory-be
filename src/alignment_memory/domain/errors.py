class DomainError(ValueError):
    """Base class for deterministic domain rule violations."""


class DomainValidationError(DomainError):
    """Raised when an entity or value object violates a domain invariant."""


class EvidenceValidationError(DomainError):
    """Raised when a source-bound claim cannot be verified."""


class ConflictPreconditionError(DomainError):
    """Raised when Direct Conflict lacks its deterministic prerequisites."""


class InvalidStateTransition(DomainError):
    """Raised when a job attempts an unsupported state transition."""


class AppendOnlyViolation(DomainError):
    """Raised when immutable history would be replaced or duplicated."""
