from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable


class GitHubSourceType(StrEnum):
    MARKDOWN = "markdown"
    ISSUE = "issue"
    PULL_REQUEST = "pull_request"
    PULL_REQUEST_DIFF = "pull_request_diff"
    COMMIT = "commit"


class ActorAssociation(StrEnum):
    OWNER = "OWNER"
    MEMBER = "MEMBER"
    COLLABORATOR = "COLLABORATOR"
    CONTRIBUTOR = "CONTRIBUTOR"
    FIRST_TIMER = "FIRST_TIMER"
    FIRST_TIME_CONTRIBUTOR = "FIRST_TIME_CONTRIBUTOR"
    MANNEQUIN = "MANNEQUIN"
    NONE = "NONE"


ALLOWED_ACTOR_ASSOCIATIONS = frozenset(
    {
        ActorAssociation.OWNER,
        ActorAssociation.MEMBER,
        ActorAssociation.COLLABORATOR,
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class GitHubRepositoryRef:
    repository_id: str
    owner: str
    name: str
    installation_id: int
    default_branch: str = "main"

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True, slots=True, kw_only=True)
class CollectedSource:
    source_id: str
    source_version_id: str
    repository_id: str
    source_type: GitHubSourceType
    external_id: str
    external_version: str
    url: str
    content: str
    content_hash: str
    occurred_at: datetime
    author_login: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceBatch:
    sources: tuple[CollectedSource, ...]
    baseline_commit_sha: str


class GitHubAdapterError(RuntimeError):
    """Safe, typed failure exposed by a GitHub adapter."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class GitHubAuthenticationError(GitHubAdapterError):
    def __init__(self, message: str = "GitHub authentication failed") -> None:
        super().__init__("github_authentication_failed", message, retryable=False)


class GitHubPermissionError(GitHubAdapterError):
    def __init__(self, message: str = "GitHub actor or installation is not allowed") -> None:
        super().__init__("github_permission_denied", message, retryable=False, status_code=403)


class GitHubRateLimitError(GitHubAdapterError):
    def __init__(self, message: str = "GitHub rate limit exhausted") -> None:
        super().__init__("github_rate_limited", message, retryable=True, status_code=429)


class GitHubTimeoutError(GitHubAdapterError):
    def __init__(self, message: str = "GitHub request timed out") -> None:
        super().__init__("github_timeout", message, retryable=True)


class GitHubResponseError(GitHubAdapterError):
    pass


@runtime_checkable
class GitHubPort(Protocol):
    async def fetch_allowed_sources(
        self,
        repository: GitHubRepositoryRef,
        *,
        baseline_commit_sha: str | None = None,
        actor_login: str | None = None,
    ) -> SourceBatch: ...

    async def fetch_pr_context(
        self,
        repository: GitHubRepositoryRef,
        *,
        number: int,
        head_sha: str,
        actor_login: str | None = None,
    ) -> SourceBatch: ...

    async def actor_is_allowed(
        self,
        repository: GitHubRepositoryRef,
        actor_login: str,
    ) -> bool: ...

    async def dispatch_sync(
        self,
        repository: GitHubRepositoryRef,
        job_id: str,
    ) -> None: ...
