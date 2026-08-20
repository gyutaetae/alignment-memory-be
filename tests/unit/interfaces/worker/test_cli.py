from datetime import UTC, datetime

import pytest

from alignment_memory.contracts import AnalysisResult
from alignment_memory.interfaces.worker.cli import (
    _event_source_type,
    _verify_repository_identity,
    analyze_event,
    build_parser,
)
from alignment_memory.interfaces.worker.event_parser import ParsedGitHubEvent
from alignment_memory.ports import (
    CollectedSource,
    GitHubSourceType,
    LlmAnalysis,
    LlmUsage,
    SourceBatch,
)

REPOSITORY_ID = "10000000-0000-0000-0000-000000000001"
HEAD_SHA = "a" * 40
MAIN_SHA = "b" * 40
SOURCE_URL = "https://github.com/acme/alignment-memory/blob/main/docs/adr.md"
QUOTE = "Browser extensions are out of scope for the MVP."


class FakeApi:
    def __init__(self) -> None:
        self.transitions: list[tuple[str, str]] = []
        self.persisted: dict[str, object] | None = None

    async def create_job(self, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["repository_id"] == REPOSITORY_ID
        return {"jobId": "job-1"}

    async def get_job_context(self, job_id: str) -> dict[str, object]:
        assert job_id == "job-1"
        return {
            "job": {"status": "queued"},
            "repository": {
                "id": REPOSITORY_ID,
                "fullName": "acme/alignment-memory",
                "githubRepositoryId": 123,
                "githubInstallationId": 456,
                "owner": "acme",
                "name": "alignment-memory",
                "defaultBranch": "main",
                "baselineCommitSha": MAIN_SHA,
                "mainCommitSha": MAIN_SHA,
                "knowledgeRevision": 3,
            },
            "knowledge": [
                {
                    "evidence": [
                        {
                            "sourceVersionId": "source-version-1",
                            "url": SOURCE_URL,
                            "exactQuote": QUOTE,
                            "verified": True,
                        }
                    ]
                }
            ],
        }

    async def transition_job(
        self,
        job_id: str,
        *,
        expected_status: str,
        next_status: str,
        error_code: str | None = None,
    ) -> dict[str, object]:
        assert job_id == "job-1"
        assert error_code is None
        self.transitions.append((expected_status, next_status))
        return {"status": next_status}

    async def persist_result(
        self,
        job_id: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        assert job_id == "job-1"
        self.persisted = payload
        return {"outcome": "direct_conflict"}


class FakeGitHub:
    async def fetch_pr_context(self, repository, **kwargs):  # type: ignore[no-untyped-def]
        assert repository.repository_id == REPOSITORY_ID
        assert kwargs == {
            "number": 7,
            "head_sha": HEAD_SHA,
            "actor_login": "member",
        }
        return SourceBatch(
            sources=(
                CollectedSource(
                    source_id="pr-source",
                    source_version_id="pr-version",
                    repository_id=REPOSITORY_ID,
                    source_type=GitHubSourceType.PULL_REQUEST,
                    external_id="pr:7",
                    external_version=HEAD_SHA,
                    url="https://github.com/acme/alignment-memory/pull/7",
                    content="Add browser extension synchronization.",
                    content_hash="c" * 64,
                    occurred_at=datetime(2026, 8, 4, tzinfo=UTC),
                    author_login="member",
                ),
            ),
            baseline_commit_sha=HEAD_SHA,
        )


class FakeLlm:
    async def analyze(self, request):  # type: ignore[no-untyped-def]
        evidence = next(
            document for document in request.documents if document.source_type == "active_knowledge"
        )
        result = AnalysisResult.model_validate(
            {
                "outcome": "direct_conflict",
                "nodes": [],
                "findings": [
                    {
                        "finding_type": "direct_conflict",
                        "target_node_logical_key": "exclude-browser-extension",
                        "target_node_type": "decision",
                        "target_node_status": "active",
                        "contradicts": True,
                        "uncertain": False,
                        "explanation": "The proposal conflicts with the active boundary.",
                        "recommended_action": "Remove it or supersede the decision.",
                        "evidence": [
                            {
                                "source_version_id": evidence.source_version_id,
                                "url": evidence.url,
                                "exact_quote": QUOTE,
                                "role": "contradicts",
                            }
                        ],
                    }
                ],
                "edges": [],
            }
        )
        return LlmAnalysis(
            run_id=request.stable_run_id,
            result=result,
            provider="fixture",
            requested_model="fixture-primary",
            actual_model="fixture-model",
            prompt_version=request.prompt_version,
            input_hash=request.input_hash,
            usage=LlmUsage(),
        )


def test_worker_reads_openai_configuration_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("OPENAI_PRIMARY_MODEL", "gpt-4.1-mini")

    args = build_parser().parse_args(
        ["analyze-event", "--event-path", "event.json", "--output", "result.json"]
    )

    assert args.llm_provider == "openai"
    assert args.openai_api_key == "test-openai-key"
    assert args.openai_primary_model == "gpt-4.1-mini"


def test_initial_sync_can_advance_a_stale_main_but_pr_analysis_cannot() -> None:
    repository = {
        "fullName": "acme/alignment-memory",
        "githubRepositoryId": 123,
        "mainCommitSha": "c" * 40,
    }
    initial_sync = ParsedGitHubEvent(
        event_name="workflow_dispatch",
        event_key=f"initial-sync:123:{MAIN_SHA}",
        repository_full_name="acme/alignment-memory",
        github_repository_id=123,
        default_branch="main",
        actor_login="member",
        actor_association=None,
        head_sha=MAIN_SHA,
        main_sha=MAIN_SHA,
        proposed_change="Initial repository synchronization requested.",
        source_url=f"https://github.com/acme/alignment-memory/tree/{MAIN_SHA}",
    )
    _verify_repository_identity(initial_sync, repository)

    pull_request = ParsedGitHubEvent(
        event_name="pull_request",
        event_key=f"pr:123:7:{HEAD_SHA}",
        repository_full_name="acme/alignment-memory",
        github_repository_id=123,
        default_branch="main",
        actor_login="member",
        actor_association=None,
        head_sha=HEAD_SHA,
        main_sha=MAIN_SHA,
        proposed_change="Add browser extension synchronization.",
        source_url="https://github.com/acme/alignment-memory/pull/7",
        pr_number=7,
    )
    with pytest.raises(ValueError, match="stale main SHA"):
        _verify_repository_identity(pull_request, repository)


@pytest.mark.parametrize(
    ("event_name", "expected_source_type"),
    [
        ("workflow_dispatch", GitHubSourceType.COMMIT.value),
        ("push", GitHubSourceType.COMMIT.value),
        ("pull_request", GitHubSourceType.PULL_REQUEST.value),
    ],
)
def test_event_sources_use_database_supported_types(
    event_name: str,
    expected_source_type: str,
) -> None:
    event = ParsedGitHubEvent(
        event_name=event_name,  # type: ignore[arg-type]
        event_key=f"event:{event_name}",
        repository_full_name="acme/alignment-memory",
        github_repository_id=123,
        default_branch="main",
        actor_login="member",
        actor_association=None,
        head_sha=HEAD_SHA,
        main_sha=MAIN_SHA,
        proposed_change="Test event source.",
        source_url="https://github.com/acme/alignment-memory",
        pr_number=7 if event_name == "pull_request" else None,
    )

    assert _event_source_type(event) == expected_source_type


@pytest.mark.asyncio
async def test_analyze_event_runs_signed_context_analysis_and_persists_validated_pr() -> None:
    api = FakeApi()
    event = ParsedGitHubEvent(
        event_name="pull_request",
        event_key=f"pr:123:7:{HEAD_SHA}",
        repository_full_name="acme/alignment-memory",
        github_repository_id=123,
        default_branch="main",
        actor_login="member",
        actor_association=None,
        head_sha=HEAD_SHA,
        main_sha=MAIN_SHA,
        proposed_change="Add browser extension synchronization.",
        source_url="https://github.com/acme/alignment-memory/pull/7",
        pr_number=7,
    )

    artifact = await analyze_event(
        event,
        repository_id=REPOSITORY_ID,
        supplied_job_id="",
        prompt_version="worker-v1",
        api=api,  # type: ignore[arg-type]
        github=FakeGitHub(),  # type: ignore[arg-type]
        llm=FakeLlm(),  # type: ignore[arg-type]
    )

    assert artifact.job_id == "job-1"
    assert artifact.validation_status == "validated"
    assert artifact.analysis.outcome.value == "direct_conflict"
    assert api.transitions == [
        ("queued", "fetching"),
        ("fetching", "analyzing"),
        ("analyzing", "validating"),
        ("validating", "persisting"),
    ]
    assert api.persisted is not None
    assert api.persisted["headSha"] == HEAD_SHA
