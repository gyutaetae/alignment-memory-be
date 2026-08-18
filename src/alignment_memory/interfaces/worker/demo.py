from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import httpx

from alignment_memory.adapters.github import FixtureGitHubAdapter
from alignment_memory.adapters.memory import InMemoryRepository
from alignment_memory.adapters.openrouter import FixtureOpenRouterAdapter
from alignment_memory.application import AlignmentAnalysisService, AnalyzePullRequestCommand
from alignment_memory.contracts import AnalysisResult
from alignment_memory.domain import (
    Alignment,
    EvidenceReference,
    HandshakeResponse,
    Job,
    JobStatus,
    JobType,
    KnowledgeEdge,
    KnowledgeNode,
    KnowledgeNodeVersion,
    Override,
    OverrideType,
    Source,
    SourceVersion,
    append_override,
    exact_quote_is_present,
    verify_evidence_reference,
)
from alignment_memory.interfaces.api.dependencies import (
    FIXTURE_MAIN_SHA,
    FIXTURE_PROFILE_ID,
    FIXTURE_READER_ID,
    FIXTURE_REPOSITORY_ID,
    FIXTURE_SOURCE_VERSION_ID,
    AppContainer,
)
from alignment_memory.interfaces.api.main import create_app
from alignment_memory.interfaces.api.security import TEST_USER_HEADER
from alignment_memory.interfaces.worker.api_client import HmacApiClient
from alignment_memory.interfaces.worker.event_parser import ParsedGitHubEvent
from alignment_memory.interfaces.worker.publish_templates import (
    GENERATED_RELATIVE_PATH,
    comment_marker,
    render_generated_wiki,
    render_pr_comment,
)
from alignment_memory.interfaces.worker.result_schema import ValidatedAnalysisArtifact
from alignment_memory.ports import (
    CollectedSource,
    GeneratedArtifactRecord,
    GitHubRepositoryRef,
    GitHubSourceType,
    SourceBatch,
)
from alignment_memory.settings import Settings

AnalyzeEventRunner = Callable[..., Awaitable[ValidatedAnalysisArtifact]]

_FIXTURE_NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
_FIXTURE_HMAC_SECRET = "alignment-memory-demo-hmac-secret"
_DECISION_URL = (
    "https://github.com/fixture-owner/alignment-memory-demo/blob/main/docs/adr.md"
)
_DECISION_QUOTE = "Browser extensions are out of scope for the MVP."
_CONFLICT_HEAD = "c" * 40
_RESOLVED_HEAD = "d" * 40
_MERGE_HEAD = "e" * 40
_MERGE_SOURCE_VERSION_ID = "demo-merge-source-version"
_MERGE_SOURCE_URL = (
    "https://github.com/fixture-owner/alignment-memory-demo/commit/" + _MERGE_HEAD
)
_MERGE_SOURCE_CONTENT = (
    "Keep the collaboration flow repository-native and document the resolved boundary."
)


async def run_demo(
    output_dir: Path,
    *,
    analyze_event_runner: AnalyzeEventRunner,
) -> None:
    """Run the credential-free demo and write explicit fixture-only proof artifacts."""

    output_dir.mkdir(parents=True, exist_ok=True)
    evaluation, evaluated_alignments = await _evaluate_six_fixtures()
    vertical_slice = await _run_vertical_slice(output_dir, analyze_event_runner)
    correction = _correction_impact(evaluated_alignments)
    evaluation["correctionImpact"] = correction
    evaluation["verticalSlice"] = {
        "passed": vertical_slice["passed"],
        "proofFile": "vertical-slice.json",
    }
    evaluation["summary"]["passed"] = bool(  # type: ignore[index]
        evaluation["summary"]["passed"] and vertical_slice["passed"]  # type: ignore[index]
    )

    _write_json(output_dir / "evaluation.json", evaluation)
    (output_dir / "evaluation.md").write_text(
        _render_evaluation_markdown(evaluation),
        encoding="utf-8",
    )
    _write_json(output_dir / "vertical-slice.json", vertical_slice)


async def _evaluate_six_fixtures() -> tuple[dict[str, Any], dict[str, Alignment]]:
    fixture_dir = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "analysis"
    fixture_paths = sorted(fixture_dir.glob("*.json"))
    if len(fixture_paths) != 6:
        raise RuntimeError(f"expected exactly six evaluation fixtures, found {len(fixture_paths)}")

    cases: list[dict[str, Any]] = []
    alignments: dict[str, Alignment] = {}
    for fixture_path in fixture_paths:
        case, alignment = await _evaluate_fixture(fixture_path)
        cases.append(case)
        alignments[case["name"]] = alignment

    expected_aligned = sum(case["expectedOutcome"] == "aligned" for case in cases)
    expected_conflicts = sum(
        case["expectedOutcome"] == "direct_conflict" for case in cases
    )
    passed = all(case["passed"] for case in cases)
    return (
        {
            "schemaVersion": "alignment-memory-evaluation/v1",
            "execution": {
                "mode": "fixture",
                "externalServicesCalled": False,
                "provider": "fixture",
                "actualModel": "fixture-model",
                "liveProof": False,
                "disclaimer": (
                    "Deterministic local fixtures; this is not GitHub, OpenRouter, "
                    "Supabase, or Vercel live proof."
                ),
            },
            "summary": {
                "fixtureCount": len(cases),
                "expectedAligned": expected_aligned,
                "expectedDirectConflicts": expected_conflicts,
                "passedCases": sum(case["passed"] for case in cases),
                "passed": passed,
            },
            "cases": cases,
        },
        alignments,
    )


async def _evaluate_fixture(fixture_path: Path) -> tuple[dict[str, Any], Alignment]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    repository_id = f"fixture-evaluation:{fixture['name']}"
    job_id = f"fixture-job:{fixture['name']}"
    pull_request = fixture["pull_request"]
    sources = tuple(
        _collected_source(repository_id, source, pull_request)
        for source in fixture["source_versions"]
    )

    repository = InMemoryRepository()
    await repository.create_job(
        Job(
            id=job_id,
            repository_id=repository_id,
            event_key=f"evaluation:{fixture['name']}",
            job_type=JobType.PR_ANALYSIS,
            status=JobStatus.QUEUED,
            progress=0,
            head_sha=pull_request["head"]["sha"],
            created_at=_FIXTURE_NOW,
            updated_at=_FIXTURE_NOW,
        )
    )
    github = FixtureGitHubAdapter(
        pr_batches={
            (pull_request["number"], pull_request["head"]["sha"]): SourceBatch(
                sources=sources,
                baseline_commit_sha=pull_request["head"]["sha"],
            )
        },
        allowed_actors=frozenset({pull_request["user"]["login"]}),
    )
    llm = FixtureOpenRouterAdapter([fixture["analysis"]])
    service = AlignmentAnalysisService(
        github=github,
        llm=llm,
        repository=repository,
        clock=lambda: _FIXTURE_NOW,
    )
    owner, name = fixture["repository"]["full_name"].split("/", maxsplit=1)
    alignment = await service.analyze_pull_request(
        AnalyzePullRequestCommand(
            job_id=job_id,
            repository=GitHubRepositoryRef(
                repository_id=repository_id,
                owner=owner,
                name=name,
                installation_id=1,
            ),
            pr_number=pull_request["number"],
            head_sha=pull_request["head"]["sha"],
            knowledge_revision=1,
            prompt_version="fixture-evaluation-v1",
            actor_login=pull_request["user"]["login"],
        )
    )
    request = llm.requests[-1]
    run = await repository.get_ai_run(job_id, request.input_hash, request.prompt_version)
    if run is None:
        raise RuntimeError("fixture evaluation did not persist its execution provenance")

    source_index = {source.source_version_id: source for source in sources}
    result = AnalysisResult.model_validate(fixture["analysis"])
    evidence_items = tuple(
        evidence
        for collection in (
            *(node.evidence for node in result.nodes),
            *(finding.evidence for finding in result.findings),
            *(edge.evidence for edge in result.edges),
        )
        for evidence in collection
    )
    evidence_valid = all(
        evidence.source_version_id in source_index
        and str(evidence.url) == source_index[evidence.source_version_id].url
        and exact_quote_is_present(
            evidence.exact_quote,
            source_index[evidence.source_version_id].content,
        )
        for evidence in evidence_items
    )
    expected = fixture["expected_outcome"]
    passed = alignment.outcome.value == expected and evidence_valid
    return (
        {
            "name": fixture["name"],
            "expectedOutcome": expected,
            "actualOutcome": alignment.outcome.value,
            "evidenceQuoteCount": len(evidence_items),
            "evidenceQuoteValidity": evidence_valid,
            "provider": run.provider,
            "requestedModel": run.requested_model,
            "actualModel": run.actual_model,
            "externalAiCalled": False,
            "passed": passed,
        },
        alignment,
    )


def _collected_source(
    repository_id: str,
    source: Mapping[str, str],
    pull_request: Mapping[str, Any],
) -> CollectedSource:
    source_type = (
        GitHubSourceType.PULL_REQUEST
        if "/pull/" in source["url"]
        else GitHubSourceType.MARKDOWN
    )
    return CollectedSource(
        source_id=_stable_id("fixture-source", repository_id, source["id"]),
        source_version_id=source["id"],
        repository_id=repository_id,
        source_type=source_type,
        external_id=source["id"],
        external_version=pull_request["head"]["sha"],
        url=source["url"],
        content=source["content"],
        content_hash=hashlib.sha256(source["content"].encode()).hexdigest(),
        occurred_at=_FIXTURE_NOW,
        author_login=pull_request["user"]["login"],
    )


def _correction_impact(alignments: Mapping[str, Alignment]) -> dict[str, Any]:
    before = alignments["direct-conflict-browser-extension"]
    after = alignments["aligned-browser-boundary"]
    override = Override(
        id=_stable_id("evaluation-override", before.id),
        target_type="alignment",
        target_id=before.id,
        override_type=OverrideType.SUPERSEDE_DECISION,
        reason="The team explicitly superseded the browser-extension boundary.",
        actor_profile_id=FIXTURE_PROFILE_ID,
        created_at=_FIXTURE_NOW,
    )
    application = append_override(before.findings, (), override)
    return {
        "scenario": "browser-extension-boundary",
        "beforeOutcome": before.outcome.value,
        "correctionType": override.override_type.value,
        "reasonRecorded": True,
        "priorFindingPreserved": application.findings == before.findings,
        "correctionEvidenceRole": application.correction_evidence.role.value,
        "afterOutcome": after.outcome.value,
        "passed": (
            before.outcome.value == "direct_conflict"
            and after.outcome.value == "aligned"
            and application.findings == before.findings
        ),
    }


async def _run_vertical_slice(
    output_dir: Path,
    analyze_event_runner: AnalyzeEventRunner,
) -> dict[str, Any]:
    settings = Settings(
        app_mode="fixture",
        environment="test",
        fixture_test_auth_enabled=True,
        internal_hmac_secret=_FIXTURE_HMAC_SECRET,
        _env_file=None,
    )
    container = AppContainer(settings)
    await container.start()
    repository = container.require_repository()
    if not isinstance(repository, InMemoryRepository):
        raise RuntimeError("fixture demo requires InMemoryRepository")

    conflict_event = _pr_event(
        head_sha=_CONFLICT_HEAD,
        title="Add browser extension synchronization",
        body="This PR adds a browser extension to the MVP.",
    )
    resolved_event = _pr_event(
        head_sha=_RESOLVED_HEAD,
        title="Keep synchronization repository-native",
        body="This revision removes browser extension work and keeps the web workflow.",
    )
    merge_event = ParsedGitHubEvent(
        event_name="push",
        event_key=f"merge-publish:1:{_MERGE_HEAD}",
        repository_full_name="fixture-owner/alignment-memory-demo",
        github_repository_id=1,
        default_branch="main",
        actor_login="fixture-user",
        actor_association=None,
        head_sha=_MERGE_HEAD,
        main_sha=_MERGE_HEAD,
        proposed_change="Merge the repository-native synchronization resolution.",
        source_url=_MERGE_SOURCE_URL,
    )

    conflict_batch = _pr_batch(_CONFLICT_HEAD, "Add browser extension synchronization.")
    resolved_batch = _pr_batch(_RESOLVED_HEAD, "Keep synchronization repository-native.")
    merge_source = CollectedSource(
        source_id=_stable_id("demo-source", _MERGE_SOURCE_VERSION_ID),
        source_version_id=_MERGE_SOURCE_VERSION_ID,
        repository_id=FIXTURE_REPOSITORY_ID,
        source_type=GitHubSourceType.COMMIT,
        external_id=f"commit:{_MERGE_HEAD}",
        external_version=_MERGE_HEAD,
        url=_MERGE_SOURCE_URL,
        content=_MERGE_SOURCE_CONTENT,
        content_hash=hashlib.sha256(_MERGE_SOURCE_CONTENT.encode()).hexdigest(),
        occurred_at=_FIXTURE_NOW,
        author_login="fixture-user",
    )
    github = FixtureGitHubAdapter(
        pr_batches={
            (17, _CONFLICT_HEAD): conflict_batch,
            (17, _RESOLVED_HEAD): resolved_batch,
        },
        sync_batches={
            FIXTURE_MAIN_SHA: SourceBatch(
                sources=(merge_source,),
                baseline_commit_sha=_MERGE_HEAD,
            )
        },
    )
    conflict_result = _conflict_result()
    aligned_result = AnalysisResult(
        outcome="aligned",
        nodes=(),
        findings=(),
        edges=(),
    )
    merge_result = _merge_result()
    llm = FixtureOpenRouterAdapter(
        [conflict_result, conflict_result, aligned_result, merge_result]
    )

    app = create_app(settings, container=container)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://fixture") as client:
        api = HmacApiClient(
            "http://fixture",
            _FIXTURE_HMAC_SECRET,
            client=client,
        )
        try:
            source_count_before = await repository.count_sources(FIXTURE_REPOSITORY_ID)
            conflict = await analyze_event_runner(
                conflict_event,
                repository_id=FIXTURE_REPOSITORY_ID,
                supplied_job_id=None,
                prompt_version="fixture-demo-v1",
                api=api,
                github=github,
                llm=llm,
            )
            await _complete_job(api, conflict.job_id)
            first_alignments = await repository.list_alignments(FIXTURE_REPOSITORY_ID)
            first_marker = comment_marker(conflict)
            (output_dir / "conflict-comment.md").write_text(
                render_pr_comment(conflict),
                encoding="utf-8",
            )

            conflict_retry = await analyze_event_runner(
                conflict_event,
                repository_id=FIXTURE_REPOSITORY_ID,
                supplied_job_id=None,
                prompt_version="fixture-demo-v1",
                api=api,
                github=github,
                llm=llm,
            )
            retry_alignments = await repository.list_alignments(FIXTURE_REPOSITORY_ID)

            resolved = await analyze_event_runner(
                resolved_event,
                repository_id=FIXTURE_REPOSITORY_ID,
                supplied_job_id=None,
                prompt_version="fixture-demo-v1",
                api=api,
                github=github,
                llm=llm,
            )
            await _complete_job(api, resolved.job_id)
            (output_dir / "resolved-comment.md").write_text(
                render_pr_comment(resolved),
                encoding="utf-8",
            )

            conflict_alignment = next(
                item
                for item in retry_alignments
                if item.pr_number == 17 and item.head_sha == _CONFLICT_HEAD
            )
            passport_proof = await _record_passports_and_handshakes(
                client,
                conflict_alignment.id,
            )

            merge = await analyze_event_runner(
                merge_event,
                repository_id=FIXTURE_REPOSITORY_ID,
                supplied_job_id=None,
                prompt_version="fixture-demo-v1",
                api=api,
                github=github,
                llm=llm,
            )
            projection = _FixtureMergeProjection(repository)
            first_projection = await projection.apply(merge)
            retry_projection = await projection.apply(merge)
            await _complete_job(api, merge.job_id)
            generated_markdown = render_generated_wiki(merge)
            (output_dir / "project-memory.md").write_text(
                generated_markdown,
                encoding="utf-8",
            )
        finally:
            await api.close()
    await container.close()

    final_source_count = await repository.count_sources(FIXTURE_REPOSITORY_ID)
    conflict_findings = sum(
        len(item.findings)
        for item in retry_alignments
        if item.pr_number == 17 and item.head_sha == _CONFLICT_HEAD
    )
    assertions = {
        "conflictIsDirectConflict": conflict.analysis.outcome.value == "direct_conflict",
        "conflictExactEvidence": any(
            evidence.exact_quote == _DECISION_QUOTE
            for finding in conflict.analysis.findings
            for evidence in finding.evidence
        ),
        "eventJobRetryStable": conflict_retry.job_id == conflict.job_id,
        "findingNotDuplicated": (
            len(first_alignments) == len(retry_alignments) and conflict_findings == 1
        ),
        "commentMarkerNotDuplicated": (
            first_marker == comment_marker(conflict_retry) == comment_marker(resolved)
        ),
        "resolvedIsAligned": resolved.analysis.outcome.value == "aligned",
        "sourceNotDuplicated": (
            first_projection["sourceCount"] == retry_projection["sourceCount"]
            and final_source_count == retry_projection["sourceCount"]
            and final_source_count > source_count_before
        ),
        "knowledgeVersionNotDuplicated": (
            first_projection["knowledgeVersionCount"]
            == retry_projection["knowledgeVersionCount"]
        ),
        "generatedArtifactNotDuplicated": (
            first_projection["artifactCount"] == retry_projection["artifactCount"] == 1
        ),
        "knowledgeRevisionUpdatedOnce": (
            first_projection["knowledgeRevision"]
            == retry_projection["knowledgeRevision"]
            == 2
        ),
        "generatedMarkdownUpdatedOnce": (
            render_generated_wiki(merge) == generated_markdown
            and "Keep synchronization repository-native" in generated_markdown
            and "Knowledge revision: `2`" in generated_markdown
        ),
        "passportHandshakeRecorded": passport_proof["handshakeCount"] == 2,
    }
    return {
        "schemaVersion": "alignment-memory-vertical-slice/v1",
        "execution": {
            "mode": "fixture",
            "externalServicesCalled": False,
            "liveProof": False,
            "provider": "fixture",
            "actualModel": "fixture-model",
        },
        "decision": {"exactQuote": _DECISION_QUOTE, "sourceUrl": _DECISION_URL},
        "conflict": {
            "outcome": conflict.analysis.outcome.value,
            "jobId": conflict.job_id,
            "commentMarker": first_marker,
            "findingCount": conflict_findings,
        },
        "resolution": {
            "method": "pull_request_revision",
            "outcome": resolved.analysis.outcome.value,
            "commentMarker": comment_marker(resolved),
        },
        "merge": {
            "eventKey": merge.event.event_key,
            "firstApply": first_projection,
            "retryApply": retry_projection,
            "generatedPath": GENERATED_RELATIVE_PATH.as_posix(),
        },
        "passport": passport_proof,
        "idempotencyAssertions": assertions,
        "passed": all(assertions.values()),
    }


def _pr_event(*, head_sha: str, title: str, body: str) -> ParsedGitHubEvent:
    return ParsedGitHubEvent(
        event_name="pull_request",
        event_key=f"pr:1:17:{head_sha}",
        repository_full_name="fixture-owner/alignment-memory-demo",
        github_repository_id=1,
        default_branch="main",
        actor_login="fixture-user",
        actor_association=None,
        head_sha=head_sha,
        main_sha=FIXTURE_MAIN_SHA,
        proposed_change=f"{title}\n\n{body}",
        source_url="https://github.com/fixture-owner/alignment-memory-demo/pull/17",
        pr_number=17,
    )


def _pr_batch(head_sha: str, content: str) -> SourceBatch:
    return SourceBatch(
        sources=(
            CollectedSource(
                source_id=_stable_id("demo-pr-source", head_sha),
                source_version_id=f"demo-pr-version-{head_sha[:8]}",
                repository_id=FIXTURE_REPOSITORY_ID,
                source_type=GitHubSourceType.PULL_REQUEST,
                external_id="pr:17",
                external_version=head_sha,
                url="https://github.com/fixture-owner/alignment-memory-demo/pull/17",
                content=content,
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
                occurred_at=_FIXTURE_NOW,
                author_login="fixture-user",
            ),
        ),
        baseline_commit_sha=head_sha,
    )


def _conflict_result() -> AnalysisResult:
    return AnalysisResult.model_validate(
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
                    "explanation": "The PR adds the excluded browser extension.",
                    "recommended_action": (
                        "Remove extension work or supersede the decision with a reason."
                    ),
                    "evidence": [
                        {
                            "source_version_id": FIXTURE_SOURCE_VERSION_ID,
                            "url": _DECISION_URL,
                            "exact_quote": _DECISION_QUOTE,
                            "role": "contradicts",
                        }
                    ],
                }
            ],
            "edges": [],
        }
    )


def _merge_result() -> AnalysisResult:
    evidence = {
        "source_version_id": _MERGE_SOURCE_VERSION_ID,
        "url": _MERGE_SOURCE_URL,
        "exact_quote": _MERGE_SOURCE_CONTENT,
        "role": "supports",
    }
    return AnalysisResult.model_validate(
        {
            "outcome": "aligned",
            "nodes": [
                {
                    "logical_key": "task:repository-native-sync",
                    "node_type": "task",
                    "title": "Keep synchronization repository-native",
                    "summary": "The merged change preserves the recorded MVP boundary.",
                    "status": "active",
                    "evidence": [evidence],
                }
            ],
            "findings": [],
            "edges": [
                {
                    "from_node_logical_key": "task:repository-native-sync",
                    "to_node_logical_key": "exclude-browser-extension",
                    "relation_type": "complies_with",
                    "evidence": [evidence],
                }
            ],
        }
    )


async def _complete_job(api: HmacApiClient, job_id: str) -> None:
    context = await api.get_job_context(job_id)
    job = context.get("job")
    if not isinstance(job, Mapping):
        raise RuntimeError("job context is malformed")
    status = job.get("status")
    if status == "persisting":
        await api.transition_job(
            job_id,
            expected_status="persisting",
            next_status="writing_github",
        )
        status = "writing_github"
    if status == "writing_github":
        await api.transition_job(
            job_id,
            expected_status="writing_github",
            next_status="completed",
        )


async def _record_passports_and_handshakes(
    client: httpx.AsyncClient,
    alignment_id: str,
) -> dict[str, Any]:
    participants = (
        (FIXTURE_PROFILE_ID, "ko", "Korean PM", "동의합니다. 기존 결정과 근거를 확인했습니다."),
        (
            FIXTURE_READER_ID,
            "en",
            "English collaborator",
            "Agreed after reviewing the original evidence.",
        ),
    )
    records: list[dict[str, Any]] = []
    for profile_id, language, role, message in participants:
        headers = {TEST_USER_HEADER: profile_id}
        passport = await client.post(
            f"/api/v1/alignments/{alignment_id}/context-passport/generate",
            json={"language": language},
            headers=headers,
        )
        passport.raise_for_status()
        handshake = await client.post(
            f"/api/v1/alignments/{alignment_id}/handshakes",
            json={
                "response": HandshakeResponse.AGREE.value,
                "message": message,
                "sourceLanguage": language,
            },
            headers=headers,
        )
        handshake.raise_for_status()
        records.append(
            {
                "role": role,
                "language": language,
                "passportId": passport.json()["id"],
                "handshakeResponse": handshake.json()["response"],
            }
        )
    return {"participants": records, "handshakeCount": len(records)}


class _FixtureMergeProjection:
    def __init__(self, repository: InMemoryRepository) -> None:
        self._repository = repository

    async def apply(self, artifact: ValidatedAnalysisArtifact) -> dict[str, int]:
        if artifact.event.event_name != "push":
            raise ValueError("fixture merge projection requires a push artifact")
        await self._persist_documents(artifact)
        snapshots = await self._repository.list_knowledge_snapshots(
            artifact.event.repository_id
        )
        node_by_key = {snapshot.node.logical_key: snapshot.node for snapshot in snapshots}
        for item in artifact.analysis.nodes:
            node = node_by_key.get(item.logical_key)
            if node is None:
                node = await self._repository.add_knowledge_node(
                    KnowledgeNode(
                        id=_stable_id("demo-node", artifact.event.repository_id, item.logical_key),
                        repository_id=artifact.event.repository_id,
                        node_type=item.node_type,
                        logical_key=item.logical_key,
                    )
                )
                node_by_key[item.logical_key] = node
            version_id = _stable_id("demo-version", node.id, artifact.event.event_key)
            history = await self._repository.list_knowledge_node_versions(node.id)
            if not any(version.id == version_id for version in history):
                evidence_items: list[EvidenceReference] = []
                for item_evidence in item.evidence:
                    evidence_items.append(await self._verified_evidence(item_evidence))
                await self._repository.append_knowledge_node_version(
                    KnowledgeNodeVersion(
                        id=version_id,
                        node_id=node.id,
                        revision=len(history) + 1,
                        title=item.title,
                        summary=item.summary,
                        status=item.status,
                        created_by="fixture-demo",
                        created_at=_FIXTURE_NOW,
                        evidence=tuple(evidence_items),
                        supersedes_version_id=history[-1].id if history else None,
                    )
                )

        for item in artifact.analysis.edges:
            from_node = node_by_key.get(item.from_node_logical_key)
            to_node = node_by_key.get(item.to_node_logical_key)
            if from_node is None or to_node is None:
                raise ValueError("merge edge references an unknown knowledge node")
            await self._repository.add_knowledge_edge(
                KnowledgeEdge(
                    id=_stable_id(
                        "demo-edge",
                        artifact.event.repository_id,
                        item.from_node_logical_key,
                        item.relation_type,
                        item.to_node_logical_key,
                        artifact.event.event_key,
                    ),
                    repository_id=artifact.event.repository_id,
                    from_node_id=from_node.id,
                    to_node_id=to_node.id,
                    relation_type=item.relation_type,
                    valid_from_revision=artifact.knowledge_revision + 1,
                    evidence=tuple(
                        [
                            await self._verified_evidence(item_evidence)
                            for item_evidence in item.evidence
                        ]
                    ),
                )
            )

        markdown = render_generated_wiki(artifact)
        content_hash = hashlib.sha256(markdown.encode()).hexdigest()
        await self._repository.persist_generated_artifact(
            GeneratedArtifactRecord(
                id=_stable_id(
                    "demo-artifact",
                    artifact.event.repository_id,
                    GENERATED_RELATIVE_PATH.as_posix(),
                    content_hash,
                ),
                repository_id=artifact.event.repository_id,
                path=GENERATED_RELATIVE_PATH.as_posix(),
                content_hash=content_hash,
                blob_sha=content_hash[:40],
                commit_sha=artifact.event.head_sha,
                knowledge_revision=artifact.knowledge_revision + 1,
                created_at=_FIXTURE_NOW,
            )
        )
        repository = await self._repository.advance_repository_revision(
            artifact.event.repository_id,
            expected_revision=artifact.knowledge_revision,
            head_sha=artifact.event.head_sha,
        )
        snapshots = await self._repository.list_knowledge_snapshots(
            artifact.event.repository_id
        )
        versions = 0
        for snapshot in snapshots:
            history = await self._repository.list_knowledge_node_versions(snapshot.node.id)
            versions += len(history)
        artifacts = await self._repository.list_generated_artifacts(
            artifact.event.repository_id
        )
        return {
            "sourceCount": await self._repository.count_sources(
                artifact.event.repository_id
            ),
            "knowledgeNodeCount": len(snapshots),
            "knowledgeVersionCount": versions,
            "artifactCount": len(artifacts),
            "knowledgeRevision": repository.knowledge_revision,
        }

    async def _persist_documents(self, artifact: ValidatedAnalysisArtifact) -> None:
        for document in artifact.documents:
            if await self._repository.get_source_version_with_source(
                document.source_version_id
            ) is not None:
                continue
            source_type = _allowed_source_type(document.source_type)
            source = await self._repository.add_source(
                Source(
                    id=_stable_id(
                        "demo-source",
                        artifact.event.repository_id,
                        document.source_version_id,
                    ),
                    repository_id=artifact.event.repository_id,
                    source_type=source_type,
                    external_id=document.source_version_id,
                    url=str(document.url),
                )
            )
            await self._repository.append_source_version(
                SourceVersion(
                    id=document.source_version_id,
                    source_id=source.id,
                    external_version=artifact.event.head_sha,
                    content=document.content,
                    content_hash=hashlib.sha256(document.content.encode()).hexdigest(),
                    occurred_at=artifact.created_at,
                    ingested_at=_FIXTURE_NOW,
                )
            )

    async def _verified_evidence(self, evidence: Any) -> EvidenceReference:
        stored = await self._repository.get_source_version_with_source(
            evidence.source_version_id
        )
        if stored is None:
            raise ValueError("merge evidence references an unpersisted source version")
        source, version = stored
        return verify_evidence_reference(
            EvidenceReference(
                source_version_id=evidence.source_version_id,
                url=str(evidence.url),
                exact_quote=evidence.exact_quote,
                role=evidence.role,
            ),
            source,
            version,
        )


def _allowed_source_type(source_type: str) -> str:
    if source_type in {item.value for item in GitHubSourceType}:
        return source_type
    if source_type.startswith("github_push"):
        return GitHubSourceType.COMMIT.value
    if source_type == "active_knowledge":
        return GitHubSourceType.MARKDOWN.value
    raise ValueError(f"unsupported fixture source type: {source_type}")


def _render_evaluation_markdown(evaluation: Mapping[str, Any]) -> str:
    execution = evaluation["execution"]
    summary = evaluation["summary"]
    correction = evaluation["correctionImpact"]
    lines = [
        "# Alignment Memory Fixture Evaluation",
        "",
        "> **Fixture-only proof.** No live GitHub, OpenRouter, Supabase, or Vercel "
        "service was called. Do not present this report as external AI or deployment proof.",
        "",
        "## Execution provenance",
        "",
        f"- Mode: `{execution['mode']}`",
        f"- Provider observed during execution: `{execution['provider']}`",
        f"- Actual model observed during execution: `{execution['actualModel']}`",
        f"- External services called: `{str(execution['externalServicesCalled']).lower()}`",
        "",
        "## Six-fixture outcomes",
        "",
        "| Fixture | Expected | Actual | Quotes valid | Provider / actual model | Pass |",
        "|---|---|---|---:|---|---:|",
    ]
    for case in evaluation["cases"]:
        lines.append(
            "| {name} | {expectedOutcome} | {actualOutcome} | {evidenceQuoteValidity} "
            "({evidenceQuoteCount}) | {provider} / {actualModel} | {passed} |".format(
                **case
            )
        )
    lines.extend(
        (
            "",
            "## Correction impact",
            "",
            f"- Scenario: `{correction['scenario']}`",
            f"- Before: `{correction['beforeOutcome']}`",
            f"- Correction: `{correction['correctionType']}` with a recorded reason",
            f"- Prior finding preserved: `{str(correction['priorFindingPreserved']).lower()}`",
            f"- After reanalysis: `{correction['afterOutcome']}`",
            "",
            "## Result",
            "",
            f"- Cases passed: `{summary['passedCases']}/{summary['fixtureCount']}`",
            f"- Vertical slice passed: `{str(evaluation['verticalSlice']['passed']).lower()}`",
            f"- Overall passed: `{str(summary['passed']).lower()}`",
            "",
        )
    )
    return "\n".join(lines)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _stable_id(kind: str, *parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, ":".join(("alignment-memory", kind, *parts))))
