from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

import httpx
import jwt

from alignment_memory.domain import normalize_stored_body
from alignment_memory.ports.github import (
    ALLOWED_ACTOR_ASSOCIATIONS,
    ActorAssociation,
    CollectedSource,
    GitHubAuthenticationError,
    GitHubPermissionError,
    GitHubRateLimitError,
    GitHubRepositoryRef,
    GitHubResponseError,
    GitHubSourceType,
    GitHubTimeoutError,
    SourceBatch,
)

JsonObject = dict[str, Any]
Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True, kw_only=True)
class GitHubAppCredentials:
    app_id: str
    private_key: str


@dataclass(frozen=True, slots=True, kw_only=True)
class GitHubAdapterConfig:
    api_base_url: str = "https://api.github.com"
    api_version: str = "2022-11-28"
    timeout_seconds: float = 15.0
    max_retries: int = 2
    max_retry_delay_seconds: float = 30.0
    sync_workflow: str = "alignment-analyze.yml"


@dataclass(frozen=True, slots=True, kw_only=True)
class _InstallationToken:
    value: str
    expires_at: datetime


class GitHubAppAdapter:
    """Read-only GitHub App REST adapter for explicitly allowed repository text."""

    def __init__(
        self,
        credentials: GitHubAppCredentials,
        *,
        config: GitHubAdapterConfig | None = None,
        client: httpx.AsyncClient | None = None,
        installation_token: str | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock | None = None,
    ) -> None:
        self._credentials = credentials
        self._config = config or GitHubAdapterConfig()
        self._client = client or httpx.AsyncClient(
            base_url=self._config.api_base_url,
            timeout=self._config.timeout_seconds,
        )
        self._owns_client = client is None
        self._static_installation_token = installation_token
        self._tokens: dict[int, _InstallationToken] = {}
        self._sleep = sleep
        self._clock = clock or (lambda: datetime.now(UTC))

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
            self._owns_client = False

    async def actor_is_allowed(
        self,
        repository: GitHubRepositoryRef,
        actor_login: str,
    ) -> bool:
        if not actor_login.strip():
            return False
        response = await self._request(
            repository,
            "GET",
            f"/repos/{repository.owner}/{repository.name}/collaborators/"
            f"{quote(actor_login, safe='')}/permission",
        )
        payload = self._json_object(response)
        permission = payload.get("permission")
        return permission in {"read", "triage", "write", "maintain", "admin"}

    async def fetch_allowed_sources(
        self,
        repository: GitHubRepositoryRef,
        *,
        baseline_commit_sha: str | None = None,
        actor_login: str | None = None,
    ) -> SourceBatch:
        await self._require_allowed_actor(repository, actor_login)
        repository_payload = self._json_object(
            await self._request(
                repository,
                "GET",
                f"/repos/{repository.owner}/{repository.name}",
            )
        )
        default_branch = str(repository_payload.get("default_branch") or repository.default_branch)
        head = self._json_object(
            await self._request(
                repository,
                "GET",
                f"/repos/{repository.owner}/{repository.name}/commits/"
                f"{quote(default_branch, safe='')}",
            )
        )
        head_sha = self._required_text(head, "sha", "repository head SHA")

        if baseline_commit_sha is None:
            sources = await self._collect_initial_sources(repository, head_sha)
        else:
            sources = await self._collect_incremental_sources(
                repository,
                baseline_commit_sha=baseline_commit_sha,
                head_sha=head_sha,
            )
        return SourceBatch(
            sources=self._deduplicated_sources(sources),
            baseline_commit_sha=head_sha,
        )

    async def fetch_pr_context(
        self,
        repository: GitHubRepositoryRef,
        *,
        number: int,
        head_sha: str,
        actor_login: str | None = None,
    ) -> SourceBatch:
        await self._require_allowed_actor(repository, actor_login)
        if number <= 0:
            raise GitHubResponseError(
                "github_invalid_pr",
                "pull request number must be positive",
                retryable=False,
            )
        pull = self._json_object(
            await self._request(
                repository,
                "GET",
                f"/repos/{repository.owner}/{repository.name}/pulls/{number}",
            )
        )
        actual_head = self._nested_text(pull, "head", "sha")
        if actual_head != head_sha:
            raise GitHubResponseError(
                "github_stale_pr_head",
                "pull request head SHA no longer matches the requested analysis",
                retryable=False,
            )
        if not self._association_is_allowed(pull.get("author_association")):
            raise GitHubPermissionError("pull request actor association is not allowed")
        self._require_in_repository_pull(pull)

        sources = [self._pull_request_source(repository, pull)]
        sources.append(await self._pull_request_diff_source(repository, pull))
        return SourceBatch(sources=tuple(sources), baseline_commit_sha=head_sha)

    async def dispatch_sync(
        self,
        repository: GitHubRepositoryRef,
        job_id: str,
    ) -> None:
        if not job_id.strip():
            raise ValueError("job_id is required")
        await self._request(
            repository,
            "POST",
            f"/repos/{repository.owner}/{repository.name}/actions/workflows/"
            f"{quote(self._config.sync_workflow, safe='')}/dispatches",
            json_body={
                "ref": repository.default_branch,
                "inputs": {"jobId": job_id},
            },
        )

    async def _collect_initial_sources(
        self,
        repository: GitHubRepositoryRef,
        head_sha: str,
    ) -> list[CollectedSource]:
        markdown, issues, pulls, commits = await asyncio.gather(
            self._collect_markdown_tree(repository, head_sha),
            self._collect_issues(repository),
            self._collect_pulls(repository),
            self._collect_commits(repository),
        )
        return [*markdown, *issues, *pulls, *commits]

    async def _collect_incremental_sources(
        self,
        repository: GitHubRepositoryRef,
        *,
        baseline_commit_sha: str,
        head_sha: str,
    ) -> list[CollectedSource]:
        baseline = self._json_object(
            await self._request(
                repository,
                "GET",
                f"/repos/{repository.owner}/{repository.name}/commits/{baseline_commit_sha}",
            )
        )
        boundary = self._commit_occurred_at(baseline)
        comparison = await self._compare(repository, baseline_commit_sha, head_sha)

        markdown: list[CollectedSource] = []
        for file_payload in comparison["files"]:
            path = file_payload.get("filename")
            if (
                isinstance(path, str)
                and file_payload.get("status") != "removed"
                and self._is_allowed_markdown_path(path)
            ):
                markdown.append(await self._markdown_source(repository, path, head_sha))

        commits = [
            self._commit_source(repository, commit)
            for commit in comparison["commits"]
            if commit.get("sha") != baseline_commit_sha
        ]
        issues, pulls = await asyncio.gather(
            self._collect_issues(repository, updated_after=boundary),
            self._collect_pulls(repository, updated_after=boundary),
        )
        return [*markdown, *issues, *pulls, *commits]

    async def _collect_markdown_tree(
        self,
        repository: GitHubRepositoryRef,
        ref: str,
    ) -> list[CollectedSource]:
        tree = self._json_object(
            await self._request(
                repository,
                "GET",
                f"/repos/{repository.owner}/{repository.name}/git/trees/{ref}",
                params={"recursive": "1"},
            )
        )
        if tree.get("truncated") is True:
            raise GitHubResponseError(
                "github_tree_truncated",
                "GitHub tree response was truncated",
                retryable=False,
            )
        entries = tree.get("tree")
        if not isinstance(entries, list):
            raise self._invalid_response("repository tree is not a list")
        markdown: list[CollectedSource] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            if (
                entry.get("type") == "blob"
                and isinstance(path, str)
                and self._is_allowed_markdown_path(path)
            ):
                markdown.append(await self._markdown_source(repository, path, ref))
        return markdown

    async def _markdown_source(
        self,
        repository: GitHubRepositoryRef,
        path: str,
        ref: str,
    ) -> CollectedSource:
        encoded_path = quote(path, safe="/")
        payload = self._json_object(
            await self._request(
                repository,
                "GET",
                f"/repos/{repository.owner}/{repository.name}/contents/{encoded_path}",
                params={"ref": ref},
            )
        )
        if payload.get("type") != "file" or payload.get("encoding") != "base64":
            raise self._invalid_response("Markdown content is not a base64 file")
        encoded_content = payload.get("content")
        if not isinstance(encoded_content, str):
            raise self._invalid_response("Markdown content is missing")
        try:
            content = base64.b64decode(encoded_content, validate=False).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise self._invalid_response("Markdown content is not valid UTF-8") from error
        external_version = self._required_text(payload, "sha", "Markdown blob SHA")
        occurred_at = self._clock()
        return self._normalize_source(
            repository,
            source_type=GitHubSourceType.MARKDOWN,
            external_id=path,
            external_version=external_version,
            url=str(
                payload.get("html_url")
                or f"https://github.com/{repository.full_name}/blob/{ref}/{path}"
            ),
            content=content,
            occurred_at=occurred_at,
        )

    async def _collect_issues(
        self,
        repository: GitHubRepositoryRef,
        *,
        updated_after: datetime | None = None,
    ) -> list[CollectedSource]:
        params: dict[str, str | int] = {"state": "all", "per_page": 100}
        if updated_after is not None:
            params["since"] = updated_after.isoformat().replace("+00:00", "Z")
        items = await self._paginate(
            repository,
            f"/repos/{repository.owner}/{repository.name}/issues",
            params=params,
        )
        issues: list[CollectedSource] = []
        for item in items:
            if "pull_request" in item or not self._association_is_allowed(
                item.get("author_association")
            ):
                continue
            updated_at = self._parse_datetime(item.get("updated_at"), "issue updated_at")
            if updated_after is not None and updated_at <= updated_after:
                continue
            number = self._required_int(item, "number", "issue number")
            title = self._required_text(item, "title", "issue title")
            body = item.get("body") if isinstance(item.get("body"), str) else ""
            issues.append(
                self._normalize_source(
                    repository,
                    source_type=GitHubSourceType.ISSUE,
                    external_id=f"issue:{number}",
                    external_version=updated_at.isoformat(),
                    url=self._required_text(item, "html_url", "issue URL"),
                    content=f"{title}\n\n{body}".rstrip(),
                    occurred_at=updated_at,
                    author_login=self._nested_optional_text(item, "user", "login"),
                )
            )
        return issues

    async def _collect_pulls(
        self,
        repository: GitHubRepositoryRef,
        *,
        updated_after: datetime | None = None,
    ) -> list[CollectedSource]:
        items = await self._paginate(
            repository,
            f"/repos/{repository.owner}/{repository.name}/pulls",
            params={"state": "all", "sort": "updated", "direction": "desc", "per_page": 100},
        )
        pulls: list[CollectedSource] = []
        for item in items:
            if not self._association_is_allowed(item.get("author_association")):
                continue
            updated_at = self._parse_datetime(item.get("updated_at"), "pull request updated_at")
            if updated_after is not None and updated_at <= updated_after:
                continue
            self._require_in_repository_pull(item)
            pulls.append(self._pull_request_source(repository, item))
            pulls.append(await self._pull_request_diff_source(repository, item))
        return pulls

    async def _collect_commits(
        self,
        repository: GitHubRepositoryRef,
    ) -> list[CollectedSource]:
        items = await self._paginate(
            repository,
            f"/repos/{repository.owner}/{repository.name}/commits",
            params={"per_page": 100},
        )
        return [self._commit_source(repository, item) for item in items]

    def _pull_request_source(
        self,
        repository: GitHubRepositoryRef,
        payload: Mapping[str, Any],
    ) -> CollectedSource:
        number = self._required_int(payload, "number", "pull request number")
        title = self._required_text(payload, "title", "pull request title")
        body = payload.get("body") if isinstance(payload.get("body"), str) else ""
        occurred_at = self._parse_datetime(
            payload.get("updated_at") or payload.get("created_at"),
            "pull request timestamp",
        )
        head_sha = self._nested_text(payload, "head", "sha")
        return self._normalize_source(
            repository,
            source_type=GitHubSourceType.PULL_REQUEST,
            external_id=f"pr:{number}",
            external_version=head_sha,
            url=self._required_text(payload, "html_url", "pull request URL"),
            content=f"{title}\n\n{body}".rstrip(),
            occurred_at=occurred_at,
            author_login=self._nested_optional_text(payload, "user", "login"),
        )

    async def _pull_request_diff_source(
        self,
        repository: GitHubRepositoryRef,
        payload: Mapping[str, Any],
    ) -> CollectedSource:
        number = self._required_int(payload, "number", "pull request number")
        response = await self._request(
            repository,
            "GET",
            f"/repos/{repository.owner}/{repository.name}/pulls/{number}",
            accept="application/vnd.github.diff",
        )
        occurred_at = self._parse_datetime(
            payload.get("updated_at") or payload.get("created_at"),
            "pull request timestamp",
        )
        return self._normalize_source(
            repository,
            source_type=GitHubSourceType.PULL_REQUEST_DIFF,
            external_id=f"pr:{number}:diff",
            external_version=self._nested_text(payload, "head", "sha"),
            url=str(
                payload.get("diff_url")
                or f"https://github.com/{repository.full_name}/pull/{number}.diff"
            ),
            content=response.text,
            occurred_at=occurred_at,
            author_login=self._nested_optional_text(payload, "user", "login"),
        )

    def _commit_source(
        self,
        repository: GitHubRepositoryRef,
        payload: Mapping[str, Any],
    ) -> CollectedSource:
        sha = self._required_text(payload, "sha", "commit SHA")
        commit = payload.get("commit")
        if not isinstance(commit, Mapping):
            raise self._invalid_response("commit payload is missing")
        message = self._required_text(commit, "message", "commit message")
        return self._normalize_source(
            repository,
            source_type=GitHubSourceType.COMMIT,
            external_id=sha,
            external_version=sha,
            url=str(
                payload.get("html_url") or f"https://github.com/{repository.full_name}/commit/{sha}"
            ),
            content=message,
            occurred_at=self._commit_occurred_at(payload),
            author_login=self._nested_optional_text(payload, "author", "login"),
        )

    async def _compare(
        self,
        repository: GitHubRepositoryRef,
        baseline_sha: str,
        head_sha: str,
    ) -> dict[str, list[JsonObject]]:
        next_url: str | None = (
            f"/repos/{repository.owner}/{repository.name}/compare/{baseline_sha}...{head_sha}"
        )
        params: Mapping[str, str | int] | None = {"per_page": 100}
        commits: list[JsonObject] = []
        files: list[JsonObject] = []
        while next_url is not None:
            response = await self._request(repository, "GET", next_url, params=params)
            payload = self._json_object(response)
            page_commits = payload.get("commits", [])
            page_files = payload.get("files", [])
            if not isinstance(page_commits, list) or not isinstance(page_files, list):
                raise self._invalid_response("comparison arrays are invalid")
            commits.extend(item for item in page_commits if isinstance(item, dict))
            files.extend(item for item in page_files if isinstance(item, dict))
            next_url = response.links.get("next", {}).get("url")
            params = None
        return {"commits": commits, "files": files}

    async def _paginate(
        self,
        repository: GitHubRepositoryRef,
        path: str,
        *,
        params: Mapping[str, str | int],
    ) -> list[JsonObject]:
        items: list[JsonObject] = []
        next_url: str | None = path
        next_params: Mapping[str, str | int] | None = params
        while next_url is not None:
            response = await self._request(
                repository,
                "GET",
                next_url,
                params=next_params,
            )
            try:
                payload = response.json()
            except ValueError as error:
                raise self._invalid_response("paginated response is not JSON") from error
            if not isinstance(payload, list):
                raise self._invalid_response("paginated response is not a list")
            items.extend(item for item in payload if isinstance(item, dict))
            next_url = response.links.get("next", {}).get("url")
            next_params = None
        return items

    async def _require_allowed_actor(
        self,
        repository: GitHubRepositoryRef,
        actor_login: str | None,
    ) -> None:
        if actor_login is not None and not await self.actor_is_allowed(repository, actor_login):
            raise GitHubPermissionError()

    async def _request(
        self,
        repository: GitHubRepositoryRef,
        method: str,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        accept: str = "application/vnd.github+json",
        json_body: Mapping[str, object] | None = None,
    ) -> httpx.Response:
        token = await self._installation_access_token(repository.installation_id)
        try:
            return await self._installation_request(
                token,
                method,
                url,
                params=params,
                accept=accept,
                json_body=json_body,
            )
        except GitHubAuthenticationError:
            if self._static_installation_token is not None:
                raise
            self._tokens.pop(repository.installation_id, None)
            refreshed = await self._installation_access_token(repository.installation_id)
            return await self._installation_request(
                refreshed,
                method,
                url,
                params=params,
                accept=accept,
                json_body=json_body,
            )

    async def _installation_request(
        self,
        token: str,
        method: str,
        url: str,
        *,
        params: Mapping[str, str | int] | None,
        accept: str,
        json_body: Mapping[str, object] | None,
    ) -> httpx.Response:
        return await self._request_with_retry(
            method,
            url,
            headers={
                "Accept": accept,
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": self._config.api_version,
            },
            params=params,
            json_body=json_body,
        )

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str | int] | None = None,
        json_body: Mapping[str, object] | None = None,
    ) -> httpx.Response:
        last_timeout: httpx.TimeoutException | None = None
        for attempt in range(self._config.max_retries + 1):
            try:
                response = await self._client.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json_body,
                    timeout=self._config.timeout_seconds,
                )
            except httpx.TimeoutException as error:
                last_timeout = error
                if attempt < self._config.max_retries:
                    await self._sleep(min(2**attempt, self._config.max_retry_delay_seconds))
                    continue
                raise GitHubTimeoutError() from error
            except httpx.RequestError as error:
                if attempt < self._config.max_retries:
                    await self._sleep(min(2**attempt, self._config.max_retry_delay_seconds))
                    continue
                raise GitHubResponseError(
                    "github_network_error",
                    "GitHub network request failed",
                    retryable=True,
                ) from error

            if response.status_code < 400:
                return response
            retryable = self._retryable_response(response)
            if retryable and attempt < self._config.max_retries:
                await self._sleep(self._retry_delay(response, attempt))
                continue
            self._raise_response_error(response, retryable=retryable)

        raise GitHubTimeoutError() from last_timeout

    async def _installation_access_token(self, installation_id: int) -> str:
        if self._static_installation_token is not None:
            return self._static_installation_token
        cached = self._tokens.get(installation_id)
        if cached is not None and cached.expires_at - timedelta(seconds=60) > self._clock():
            return cached.value

        app_jwt = self._create_app_jwt()
        response = await self._request_with_retry(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {app_jwt}",
                "X-GitHub-Api-Version": self._config.api_version,
            },
            json_body={
                "permissions": {
                    "actions": "write",
                    "contents": "read",
                    "issues": "read",
                    "pull_requests": "read",
                }
            },
        )
        payload = self._json_object(response)
        token = self._required_text(payload, "token", "installation token")
        expires_at = self._parse_datetime(payload.get("expires_at"), "installation token expiry")
        self._tokens[installation_id] = _InstallationToken(value=token, expires_at=expires_at)
        return token

    def _create_app_jwt(self) -> str:
        now = int(self._clock().timestamp())
        try:
            encoded = jwt.encode(
                {"iat": now - 60, "exp": now + 9 * 60, "iss": self._credentials.app_id},
                self._credentials.private_key,
                algorithm="RS256",
            )
        except (ValueError, TypeError, jwt.PyJWTError) as error:
            raise GitHubAuthenticationError("GitHub App private key is invalid") from error
        return encoded

    def _normalize_source(
        self,
        repository: GitHubRepositoryRef,
        *,
        source_type: GitHubSourceType,
        external_id: str,
        external_version: str,
        url: str,
        content: str,
        occurred_at: datetime,
        author_login: str | None = None,
    ) -> CollectedSource:
        normalized_content = normalize_stored_body(content)
        content_hash = hashlib.sha256(normalized_content.encode()).hexdigest()
        source_id = str(
            uuid5(
                NAMESPACE_URL,
                f"alignment-memory:source:{repository.repository_id}:"
                f"{source_type.value}:{external_id}",
            )
        )
        source_version_id = str(
            uuid5(NAMESPACE_URL, f"alignment-memory:source-version:{source_id}:{content_hash}")
        )
        return CollectedSource(
            source_id=source_id,
            source_version_id=source_version_id,
            repository_id=repository.repository_id,
            source_type=source_type,
            external_id=external_id,
            external_version=external_version,
            url=url,
            content=normalized_content,
            content_hash=content_hash,
            occurred_at=occurred_at,
            author_login=author_login,
        )

    @staticmethod
    def _deduplicated_sources(
        sources: Sequence[CollectedSource],
    ) -> tuple[CollectedSource, ...]:
        unique = {
            (source.source_type.value, source.external_id, source.content_hash): source
            for source in sources
        }
        return tuple(
            sorted(
                unique.values(),
                key=lambda source: (
                    source.source_type.value,
                    source.external_id,
                    source.content_hash,
                ),
            )
        )

    @staticmethod
    def _is_allowed_markdown_path(path: str) -> bool:
        lowered = path.lower()
        return (
            lowered.endswith((".md", ".markdown"))
            and not lowered.startswith("knowledge/generated/")
            and ".." not in path.split("/")
        )

    @staticmethod
    def _association_is_allowed(value: object) -> bool:
        try:
            association = ActorAssociation(str(value))
        except ValueError:
            return False
        return association in ALLOWED_ACTOR_ASSOCIATIONS

    @staticmethod
    def _require_in_repository_pull(payload: Mapping[str, Any]) -> None:
        head = payload.get("head")
        base = payload.get("base")
        if not isinstance(head, Mapping) or not isinstance(base, Mapping):
            raise GitHubResponseError(
                "github_invalid_response",
                "pull request repository identity is missing",
                retryable=False,
            )
        head_repo = head.get("repo")
        base_repo = base.get("repo")
        if not isinstance(head_repo, Mapping) or not isinstance(base_repo, Mapping):
            raise GitHubPermissionError("pull request repository identity is unavailable")
        if (
            head_repo.get("id") is None
            or base_repo.get("id") is None
            or head_repo.get("id") != base_repo.get("id")
        ):
            raise GitHubPermissionError("external fork pull requests are not allowed")

    @staticmethod
    def _commit_occurred_at(payload: Mapping[str, Any]) -> datetime:
        commit = payload.get("commit")
        if not isinstance(commit, Mapping):
            raise GitHubResponseError(
                "github_invalid_response",
                "commit payload is missing",
                retryable=False,
            )
        for actor_key in ("committer", "author"):
            actor = commit.get(actor_key)
            if isinstance(actor, Mapping) and actor.get("date") is not None:
                return GitHubAppAdapter._parse_datetime(
                    actor.get("date"),
                    "commit timestamp",
                )
        raise GitHubAppAdapter._invalid_response("commit timestamp is missing")

    @staticmethod
    def _parse_datetime(value: object, field_name: str) -> datetime:
        if not isinstance(value, str):
            raise GitHubAppAdapter._invalid_response(f"{field_name} is missing")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise GitHubAppAdapter._invalid_response(f"{field_name} is invalid") from error
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    @staticmethod
    def _nested_text(payload: Mapping[str, Any], outer: str, inner: str) -> str:
        nested = payload.get(outer)
        if not isinstance(nested, Mapping):
            raise GitHubAppAdapter._invalid_response(f"{outer}.{inner} is missing")
        return GitHubAppAdapter._required_text(nested, inner, f"{outer}.{inner}")

    @staticmethod
    def _nested_optional_text(
        payload: Mapping[str, Any],
        outer: str,
        inner: str,
    ) -> str | None:
        nested = payload.get(outer)
        if not isinstance(nested, Mapping):
            return None
        value = nested.get(inner)
        return value if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _required_text(payload: Mapping[str, Any], key: str, field_name: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise GitHubAppAdapter._invalid_response(f"{field_name} is missing")
        return value

    @staticmethod
    def _required_int(payload: Mapping[str, Any], key: str, field_name: str) -> int:
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise GitHubAppAdapter._invalid_response(f"{field_name} is invalid")
        return value

    @staticmethod
    def _json_object(response: httpx.Response) -> JsonObject:
        try:
            payload = response.json()
        except ValueError as error:
            raise GitHubAppAdapter._invalid_response("GitHub response is not JSON") from error
        if not isinstance(payload, dict):
            raise GitHubAppAdapter._invalid_response("GitHub response is not an object")
        return payload

    @staticmethod
    def _invalid_response(message: str) -> GitHubResponseError:
        return GitHubResponseError(
            "github_invalid_response",
            message,
            retryable=False,
        )

    @staticmethod
    def _retryable_response(response: httpx.Response) -> bool:
        return response.status_code in {408, 429, 500, 502, 503, 504} or (
            response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0"
        )

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after is not None:
            try:
                return min(float(retry_after), self._config.max_retry_delay_seconds)
            except ValueError:
                pass
        reset = response.headers.get("x-ratelimit-reset")
        if reset is not None:
            try:
                delay = max(0.0, float(reset) - time.time())
                return min(delay, self._config.max_retry_delay_seconds)
            except ValueError:
                pass
        return min(float(2**attempt), self._config.max_retry_delay_seconds)

    @staticmethod
    def _raise_response_error(response: httpx.Response, *, retryable: bool) -> None:
        if response.status_code == 401:
            raise GitHubAuthenticationError()
        if response.status_code == 403 and not retryable:
            raise GitHubPermissionError()
        if response.status_code == 429 or (
            response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0"
        ):
            raise GitHubRateLimitError()
        raise GitHubResponseError(
            "github_request_failed",
            f"GitHub request failed with status {response.status_code}",
            retryable=retryable,
            status_code=response.status_code,
        )
