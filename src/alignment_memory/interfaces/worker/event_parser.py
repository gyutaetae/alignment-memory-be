from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from alignment_memory.ports import ActorAssociation

_ALLOWED_PULL_REQUEST_ACTIONS = frozenset({"edited", "opened", "reopened", "synchronize"})
_ALLOWED_ASSOCIATIONS = frozenset(
    {
        ActorAssociation.OWNER,
        ActorAssociation.MEMBER,
        ActorAssociation.COLLABORATOR,
    }
)
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
_ZERO_SHA_PATTERN = re.compile(r"^0{40,64}$")
_MAX_PROPOSED_CHANGE_LENGTH = 100_000


class EventParseError(ValueError):
    """A GitHub event is unsupported, malformed, or outside the worker allowlist."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ParsedGitHubEvent:
    event_name: Literal["pull_request", "push", "workflow_dispatch"]
    event_key: str
    repository_full_name: str
    github_repository_id: int
    default_branch: str
    actor_login: str
    actor_association: ActorAssociation | None
    head_sha: str
    main_sha: str
    proposed_change: str
    source_url: str
    pr_number: int | None = None

    @property
    def publication_kind(self) -> Literal["pr_comment", "generated_wiki"]:
        return "pr_comment" if self.pr_number is not None else "generated_wiki"

    @property
    def event_source_version_id(self) -> str:
        digest = hashlib.sha256(self.proposed_change.encode()).hexdigest()
        return f"github-event:{self.event_key}:{digest}"


def load_github_event(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise EventParseError("GitHub event file is not readable JSON") from error
    if not isinstance(payload, dict):
        raise EventParseError("GitHub event payload must be an object")
    return payload


def parse_github_event(
    payload: Mapping[str, Any],
    *,
    event_name: str,
    trusted_head_sha: str | None = None,
) -> ParsedGitHubEvent:
    """Extract only approved text and identity fields from a GitHub event."""

    repository = _mapping(payload, "repository")
    repository_id = _positive_int(repository, "id")
    repository_full_name = _text(repository, "full_name")
    default_branch = _text(repository, "default_branch")
    actor_login = _text(_mapping(payload, "sender"), "login")

    if event_name == "pull_request":
        return _parse_pull_request(
            payload,
            repository_id=repository_id,
            repository_full_name=repository_full_name,
            default_branch=default_branch,
            actor_login=actor_login,
        )
    if event_name == "push":
        return _parse_push(
            payload,
            repository_id=repository_id,
            repository_full_name=repository_full_name,
            default_branch=default_branch,
            actor_login=actor_login,
        )
    if event_name == "workflow_dispatch":
        head_sha = _sha(trusted_head_sha, "trusted workflow head SHA")
        return ParsedGitHubEvent(
            event_name="workflow_dispatch",
            event_key=f"initial-sync:{repository_id}:{head_sha}",
            repository_full_name=repository_full_name,
            github_repository_id=repository_id,
            default_branch=default_branch,
            actor_login=actor_login,
            actor_association=None,
            head_sha=head_sha,
            main_sha=head_sha,
            proposed_change="Initial repository synchronization requested.",
            source_url=f"https://github.com/{repository_full_name}/tree/{head_sha}",
        )
    raise EventParseError(f"unsupported GitHub event: {event_name}")


def _parse_pull_request(
    payload: Mapping[str, Any],
    *,
    repository_id: int,
    repository_full_name: str,
    default_branch: str,
    actor_login: str,
) -> ParsedGitHubEvent:
    action = _text(payload, "action")
    if action not in _ALLOWED_PULL_REQUEST_ACTIONS:
        raise EventParseError(f"pull request action is not allowed: {action}")

    pull_request = _mapping(payload, "pull_request")
    try:
        association = ActorAssociation(_text(pull_request, "author_association"))
    except ValueError as error:
        raise EventParseError("pull request actor association is invalid") from error
    if association not in _ALLOWED_ASSOCIATIONS:
        raise EventParseError("pull request actor is not an allowed collaborator")

    head = _mapping(pull_request, "head")
    base = _mapping(pull_request, "base")
    head_repository = _mapping(head, "repo")
    base_repository = _mapping(base, "repo")
    if (
        _positive_int(head_repository, "id") != repository_id
        or _positive_int(base_repository, "id") != repository_id
    ):
        raise EventParseError("external fork pull requests are not allowed")

    number = _positive_int(pull_request, "number")
    head_sha = _sha(_text(head, "sha"), "pull request head SHA")
    main_sha = _sha(_text(base, "sha"), "pull request base SHA")
    title = _text(pull_request, "title")
    body_value = pull_request.get("body")
    body = body_value if isinstance(body_value, str) else ""
    proposed_change = _bounded_text(f"{title}\n\n{body}".rstrip())
    source_url = _text(pull_request, "html_url")
    return ParsedGitHubEvent(
        event_name="pull_request",
        event_key=f"pr:{repository_id}:{number}:{head_sha}",
        repository_full_name=repository_full_name,
        github_repository_id=repository_id,
        default_branch=default_branch,
        actor_login=actor_login,
        actor_association=association,
        head_sha=head_sha,
        main_sha=main_sha,
        proposed_change=proposed_change,
        source_url=source_url,
        pr_number=number,
    )


def _parse_push(
    payload: Mapping[str, Any],
    *,
    repository_id: int,
    repository_full_name: str,
    default_branch: str,
    actor_login: str,
) -> ParsedGitHubEvent:
    expected_ref = f"refs/heads/{default_branch}"
    if _text(payload, "ref") != expected_ref:
        raise EventParseError("only pushes to the default branch are allowed")
    head_sha = _sha(_text(payload, "after"), "push head SHA")
    if _ZERO_SHA_PATTERN.fullmatch(head_sha):
        raise EventParseError("branch deletion events are not allowed")

    messages: list[str] = []
    commits = payload.get("commits")
    if isinstance(commits, list):
        for commit in commits:
            if not isinstance(commit, Mapping):
                continue
            message = commit.get("message")
            if isinstance(message, str) and message.strip():
                messages.append(message.strip())
    proposed_change = _bounded_text("\n\n".join(messages) or "Default branch updated.")
    return ParsedGitHubEvent(
        event_name="push",
        event_key=f"merge-publish:{repository_id}:{head_sha}",
        repository_full_name=repository_full_name,
        github_repository_id=repository_id,
        default_branch=default_branch,
        actor_login=actor_login,
        actor_association=None,
        head_sha=head_sha,
        main_sha=head_sha,
        proposed_change=proposed_change,
        source_url=f"https://github.com/{repository_full_name}/commit/{head_sha}",
    )


def _mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise EventParseError(f"GitHub event field {key} must be an object")
    return value


def _text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EventParseError(f"GitHub event field {key} must be non-empty text")
    return value.strip()


def _positive_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise EventParseError(f"GitHub event field {key} must be a positive integer")
    return value


def _sha(value: str | None, field_name: str) -> str:
    if value is None or _SHA_PATTERN.fullmatch(value) is None:
        raise EventParseError(f"{field_name} is invalid")
    return value


def _bounded_text(value: str) -> str:
    if not value.strip():
        raise EventParseError("event proposed change cannot be empty")
    if len(value) > _MAX_PROPOSED_CHANGE_LENGTH:
        raise EventParseError("event proposed change exceeds the allowlisted size")
    return value
