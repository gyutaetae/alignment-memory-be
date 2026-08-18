import pytest

from alignment_memory.interfaces.worker.event_parser import (
    EventParseError,
    parse_github_event,
)

HEAD_SHA = "a" * 40
MAIN_SHA = "b" * 40


def _pull_request_event(
    *,
    association: str = "MEMBER",
    head_repository_id: int = 123,
) -> dict[str, object]:
    return {
        "action": "synchronize",
        "repository": {
            "id": 123,
            "full_name": "acme/alignment-memory",
            "default_branch": "main",
            "private": False,
        },
        "sender": {"login": "member", "site_admin": True},
        "pull_request": {
            "number": 7,
            "title": "Keep the project web-only",
            "body": "Documents why the extension remains excluded.",
            "html_url": "https://github.com/acme/alignment-memory/pull/7",
            "author_association": association,
            "head": {"sha": HEAD_SHA, "repo": {"id": head_repository_id}},
            "base": {"sha": MAIN_SHA, "repo": {"id": 123}},
            "malicious": "run PR shell code",
        },
        "installation": {"token": "must-not-be-parsed"},
    }


def test_pull_request_parser_extracts_only_allowlisted_data() -> None:
    parsed = parse_github_event(_pull_request_event(), event_name="pull_request")

    assert parsed.pr_number == 7
    assert parsed.actor_login == "member"
    assert parsed.publication_kind == "pr_comment"
    assert parsed.head_sha == HEAD_SHA
    assert parsed.main_sha == MAIN_SHA
    assert parsed.proposed_change == (
        "Keep the project web-only\n\nDocuments why the extension remains excluded."
    )
    assert "run PR shell code" not in parsed.proposed_change
    assert "must-not-be-parsed" not in repr(parsed)


@pytest.mark.parametrize("association", ["CONTRIBUTOR", "FIRST_TIMER", "NONE"])
def test_pull_request_parser_rejects_non_collaborators(association: str) -> None:
    with pytest.raises(EventParseError, match="allowed collaborator"):
        parse_github_event(
            _pull_request_event(association=association),
            event_name="pull_request",
        )


def test_pull_request_parser_rejects_external_fork() -> None:
    with pytest.raises(EventParseError, match="external fork"):
        parse_github_event(
            _pull_request_event(head_repository_id=999),
            event_name="pull_request",
        )


def test_push_parser_allows_only_default_branch_commit_messages() -> None:
    payload = {
        "ref": "refs/heads/main",
        "after": HEAD_SHA,
        "repository": {
            "id": 123,
            "full_name": "acme/alignment-memory",
            "default_branch": "main",
        },
        "sender": {"login": "member"},
        "commits": [
            {
                "message": "Document the current decision",
                "added": ["backend/untrusted.py"],
                "patch": "run this shell command",
            }
        ],
    }

    parsed = parse_github_event(payload, event_name="push")

    assert parsed.proposed_change == "Document the current decision"
    assert "untrusted.py" not in parsed.proposed_change
    assert parsed.publication_kind == "generated_wiki"


def test_workflow_dispatch_requires_trusted_checked_out_sha() -> None:
    payload = {
        "repository": {
            "id": 123,
            "full_name": "acme/alignment-memory",
            "default_branch": "main",
        },
        "sender": {"login": "owner"},
    }

    parsed = parse_github_event(
        payload,
        event_name="workflow_dispatch",
        trusted_head_sha=MAIN_SHA,
    )
    assert parsed.head_sha == MAIN_SHA

    with pytest.raises(EventParseError, match="trusted workflow head SHA"):
        parse_github_event(payload, event_name="workflow_dispatch")
