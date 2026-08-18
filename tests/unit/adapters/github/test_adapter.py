import base64
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from alignment_memory.adapters.github import (
    FixtureGitHubAdapter,
    GitHubAdapterConfig,
    GitHubAppAdapter,
    GitHubAppCredentials,
)
from alignment_memory.ports import GitHubPort, GitHubRepositoryRef, GitHubSourceType

NOW = datetime(2026, 8, 3, 12, tzinfo=UTC)
BASELINE = "a" * 40
HEAD = "b" * 40


def _repository() -> GitHubRepositoryRef:
    return GitHubRepositoryRef(
        repository_id="repo-1",
        owner="owner",
        name="repo",
        installation_id=7,
    )


def _commit(sha: str, message: str, occurred_at: datetime) -> dict[str, object]:
    return {
        "sha": sha,
        "html_url": f"https://github.com/owner/repo/commit/{sha}",
        "commit": {
            "message": message,
            "committer": {"date": occurred_at.isoformat().replace("+00:00", "Z")},
        },
        "author": {"login": "member"},
    }


def _pull(number: int, sha: str, updated_at: datetime, *, association: str) -> dict[str, object]:
    return {
        "number": number,
        "title": f"Pull {number}",
        "body": f"Body {number}",
        "html_url": f"https://github.com/owner/repo/pull/{number}",
        "diff_url": f"https://github.com/owner/repo/pull/{number}.diff",
        "updated_at": updated_at.isoformat().replace("+00:00", "Z"),
        "author_association": association,
        "user": {"login": "member"},
        "head": {"sha": sha, "repo": {"id": 1}},
        "base": {"sha": BASELINE, "repo": {"id": 1}},
    }


def _issue(number: int, updated_at: datetime, *, association: str) -> dict[str, object]:
    return {
        "number": number,
        "title": f"Issue {number}",
        "body": f"Body {number}",
        "html_url": f"https://github.com/owner/repo/issues/{number}",
        "updated_at": updated_at.isoformat().replace("+00:00", "Z"),
        "author_association": association,
        "user": {"login": "member"},
    }


@pytest.mark.asyncio
async def test_installation_token_is_created_and_cached() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path == "/app/installations/7/access_tokens":
            token_calls += 1
            assert request.method == "POST"
            assert request.headers["authorization"].startswith("Bearer ey")
            return httpx.Response(
                201,
                json={
                    "token": "installation-token",
                    "expires_at": (NOW + timedelta(hours=1)).isoformat(),
                },
            )
        assert request.headers["authorization"] == "Bearer installation-token"
        return httpx.Response(200, json={"permission": "write"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.test",
    ) as client:
        adapter = GitHubAppAdapter(
            GitHubAppCredentials(app_id="123", private_key=pem),
            client=client,
            clock=lambda: NOW,
        )
        assert await adapter.actor_is_allowed(_repository(), "member")
        assert await adapter.actor_is_allowed(_repository(), "member")

    assert token_calls == 1


@pytest.mark.asyncio
async def test_initial_sync_paginates_and_fetches_only_allowed_content() -> None:
    requested_content_paths: list[str] = []
    page_two_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal page_two_calls
        path = request.url.path
        page = request.url.params.get("page")
        if path == "/repos/owner/repo":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == "/repos/owner/repo/commits/main":
            return httpx.Response(200, json=_commit(HEAD, "Head", NOW))
        if path == f"/repos/owner/repo/git/trees/{HEAD}":
            return httpx.Response(
                200,
                json={
                    "truncated": False,
                    "tree": [
                        {"type": "blob", "path": "README.md"},
                        {"type": "blob", "path": "src/app.py"},
                        {"type": "blob", "path": "knowledge/generated/index.md"},
                    ],
                },
            )
        if path.startswith("/repos/owner/repo/contents/"):
            requested_content_paths.append(path)
            return httpx.Response(
                200,
                json={
                    "type": "file",
                    "encoding": "base64",
                    "sha": "c" * 40,
                    "html_url": "https://github.com/owner/repo/blob/main/README.md",
                    "content": base64.b64encode(b"# Read me\r\nAllowed markdown").decode(),
                },
            )
        if path == "/repos/owner/repo/issues":
            if page == "2":
                page_two_calls += 1
                external = _issue(9, NOW, association="CONTRIBUTOR")
                return httpx.Response(200, json=[external])
            return httpx.Response(
                200,
                json=[_issue(1, NOW, association="MEMBER")],
                headers={
                    "Link": '<https://api.github.test/repos/owner/repo/issues?page=2>; rel="next"'
                },
            )
        if path == "/repos/owner/repo/pulls" and page == "2":
            page_two_calls += 1
            return httpx.Response(
                200,
                json=[_pull(3, "3" * 40, NOW, association="CONTRIBUTOR")],
            )
        if path == "/repos/owner/repo/pulls":
            return httpx.Response(
                200,
                json=[_pull(2, "2" * 40, NOW, association="COLLABORATOR")],
                headers={
                    "Link": '<https://api.github.test/repos/owner/repo/pulls?page=2>; rel="next"'
                },
            )
        if path == "/repos/owner/repo/pulls/2":
            assert request.headers["accept"] == "application/vnd.github.diff"
            return httpx.Response(200, text="diff --git a/docs/a.md b/docs/a.md\n+Allowed")
        if path == "/repos/owner/repo/commits" and page == "2":
            page_two_calls += 1
            return httpx.Response(200, json=[_commit("e" * 40, "Second", NOW)])
        if path == "/repos/owner/repo/commits":
            return httpx.Response(
                200,
                json=[_commit("d" * 40, "First", NOW)],
                headers={
                    "Link": '<https://api.github.test/repos/owner/repo/commits?page=2>; rel="next"'
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.test",
    ) as client:
        adapter = GitHubAppAdapter(
            GitHubAppCredentials(app_id="fixture", private_key="unused"),
            client=client,
            installation_token="token",
            clock=lambda: NOW,
        )
        batch = await adapter.fetch_allowed_sources(_repository())

    assert batch.baseline_commit_sha == HEAD
    assert page_two_calls == 3
    assert requested_content_paths == ["/repos/owner/repo/contents/README.md"]
    assert [source.source_type for source in batch.sources] == [
        GitHubSourceType.COMMIT,
        GitHubSourceType.COMMIT,
        GitHubSourceType.ISSUE,
        GitHubSourceType.MARKDOWN,
        GitHubSourceType.PULL_REQUEST,
        GitHubSourceType.PULL_REQUEST_DIFF,
    ]
    markdown = next(source for source in batch.sources if source.source_type == "markdown")
    assert markdown.content == "# Read me\nAllowed markdown"
    assert len(markdown.content_hash) == 64
    assert markdown.source_id != markdown.source_version_id
    assert isinstance(FixtureGitHubAdapter(sync_batches={None: batch}), GitHubPort)


@pytest.mark.asyncio
async def test_incremental_sync_excludes_baseline_and_timestamp_boundary() -> None:
    boundary = NOW - timedelta(hours=1)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/owner/repo":
            return httpx.Response(200, json={"default_branch": "main"})
        if path == "/repos/owner/repo/commits/main":
            return httpx.Response(200, json=_commit(HEAD, "Head", NOW))
        if path == f"/repos/owner/repo/commits/{BASELINE}":
            return httpx.Response(200, json=_commit(BASELINE, "Boundary", boundary))
        if path == f"/repos/owner/repo/compare/{BASELINE}...{HEAD}":
            return httpx.Response(
                200,
                json={
                    "commits": [
                        _commit(BASELINE, "Boundary", boundary),
                        _commit(HEAD, "After boundary", NOW),
                    ],
                    "files": [
                        {"filename": "docs/new.md", "status": "added"},
                        {"filename": "src/app.py", "status": "modified"},
                    ],
                },
            )
        if path == "/repos/owner/repo/contents/docs/new.md":
            return httpx.Response(
                200,
                json={
                    "type": "file",
                    "encoding": "base64",
                    "sha": "f" * 40,
                    "html_url": "https://github.com/owner/repo/blob/main/docs/new.md",
                    "content": base64.b64encode(b"New decision").decode(),
                },
            )
        if path == "/repos/owner/repo/issues":
            return httpx.Response(
                200,
                json=[
                    _issue(10, boundary, association="MEMBER"),
                    _issue(11, NOW, association="MEMBER"),
                ],
            )
        if path == "/repos/owner/repo/pulls":
            return httpx.Response(
                200,
                json=[
                    _pull(10, "1" * 40, boundary, association="MEMBER"),
                    _pull(12, "2" * 40, NOW, association="MEMBER"),
                ],
            )
        if path == "/repos/owner/repo/pulls/12":
            return httpx.Response(200, text="diff --git a/docs/new.md b/docs/new.md")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.test",
    ) as client:
        adapter = GitHubAppAdapter(
            GitHubAppCredentials(app_id="fixture", private_key="unused"),
            client=client,
            installation_token="token",
            clock=lambda: NOW,
        )
        batch = await adapter.fetch_allowed_sources(
            _repository(),
            baseline_commit_sha=BASELINE,
        )

    assert batch.baseline_commit_sha == HEAD
    assert {source.external_id for source in batch.sources} == {
        HEAD,
        "docs/new.md",
        "issue:11",
        "pr:12",
        "pr:12:diff",
    }


@pytest.mark.asyncio
async def test_rate_limit_retries_are_bounded() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0.25"})
        return httpx.Response(200, json={"permission": "read"})

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.github.test",
    ) as client:
        adapter = GitHubAppAdapter(
            GitHubAppCredentials(app_id="fixture", private_key="unused"),
            config=GitHubAdapterConfig(max_retries=1),
            client=client,
            installation_token="token",
            sleep=record_sleep,
        )
        assert await adapter.actor_is_allowed(_repository(), "member")

    assert attempts == 2
    assert delays == [0.25]
