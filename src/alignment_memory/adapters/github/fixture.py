from __future__ import annotations

from collections.abc import Mapping

from alignment_memory.ports.github import (
    GitHubPermissionError,
    GitHubRepositoryRef,
    SourceBatch,
)


class FixtureGitHubAdapter:
    """Credential-free deterministic adapter used by contract tests and fixture mode."""

    def __init__(
        self,
        *,
        sync_batches: Mapping[str | None, SourceBatch] | None = None,
        pr_batches: Mapping[tuple[int, str], SourceBatch] | None = None,
        allowed_actors: frozenset[str] = frozenset({"fixture-user"}),
    ) -> None:
        self._sync_batches = dict(sync_batches or {})
        self._pr_batches = dict(pr_batches or {})
        self._allowed_actors = allowed_actors
        self.sync_calls: list[str | None] = []
        self.pr_calls: list[tuple[int, str]] = []
        self.dispatch_calls: list[tuple[str, str]] = []

    async def actor_is_allowed(
        self,
        repository: GitHubRepositoryRef,
        actor_login: str,
    ) -> bool:
        del repository
        return actor_login in self._allowed_actors

    async def fetch_allowed_sources(
        self,
        repository: GitHubRepositoryRef,
        *,
        baseline_commit_sha: str | None = None,
        actor_login: str | None = None,
    ) -> SourceBatch:
        del repository
        await self._require_actor(actor_login)
        self.sync_calls.append(baseline_commit_sha)
        try:
            return self._sync_batches[baseline_commit_sha]
        except KeyError as error:
            raise KeyError(f"no fixture sync batch for {baseline_commit_sha!r}") from error

    async def fetch_pr_context(
        self,
        repository: GitHubRepositoryRef,
        *,
        number: int,
        head_sha: str,
        actor_login: str | None = None,
    ) -> SourceBatch:
        del repository
        await self._require_actor(actor_login)
        key = (number, head_sha)
        self.pr_calls.append(key)
        try:
            return self._pr_batches[key]
        except KeyError as error:
            raise KeyError(f"no fixture PR batch for {key!r}") from error

    async def _require_actor(self, actor_login: str | None) -> None:
        if actor_login is not None and actor_login not in self._allowed_actors:
            raise GitHubPermissionError()

    async def dispatch_sync(
        self,
        repository: GitHubRepositoryRef,
        job_id: str,
    ) -> None:
        self.dispatch_calls.append((repository.repository_id, job_id))
