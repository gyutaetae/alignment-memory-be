from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from alignment_memory.contracts import AnalysisResult
from alignment_memory.interfaces.worker.publish_templates import (
    comment_marker,
    render_generated_wiki,
    render_pr_comment,
    resolve_generated_path,
)
from alignment_memory.interfaces.worker.result_schema import (
    ArtifactDocument,
    ArtifactEvent,
    ValidatedAnalysisArtifact,
)
from alignment_memory.ports import AnalysisRequest

REPOSITORY_ID = "10000000-0000-0000-0000-000000000001"
HEAD_SHA = "a" * 40
MAIN_SHA = "b" * 40
SOURCE_URL = "https://github.com/acme/alignment-memory/blob/main/docs/adr.md"
QUOTE = "Browser extensions are out of scope for the MVP."


def _artifact(*, head_sha: str = HEAD_SHA) -> ValidatedAnalysisArtifact:
    document = ArtifactDocument(
        sourceVersionId="source-version-1",
        sourceType="active_knowledge",
        url=SOURCE_URL,
        content=QUOTE,
    )
    analysis = AnalysisResult.model_validate(
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
                    "explanation": "The proposed extension contradicts the active boundary.",
                    "recommended_action": "Remove the extension or supersede the decision.",
                    "evidence": [
                        {
                            "source_version_id": "source-version-1",
                            "url": SOURCE_URL,
                            "exact_quote": QUOTE,
                            "role": "contradicts",
                        }
                    ],
                }
            ],
            "edges": [],
        }
    )
    request = AnalysisRequest(
        job_id="job-1",
        repository_id=REPOSITORY_ID,
        pr_number=7,
        head_sha=head_sha,
        knowledge_revision=3,
        prompt_version="worker-v1",
        documents=(document.as_analysis_document(),),
    )
    return ValidatedAnalysisArtifact(
        schemaVersion="alignment-memory/v1",
        validationStatus="validated",
        jobId="job-1",
        event=ArtifactEvent(
            eventName="pull_request",
            eventKey=f"pr:123:7:{head_sha}",
            repositoryId=REPOSITORY_ID,
            repositoryFullName="acme/alignment-memory",
            githubRepositoryId=123,
            actorLogin="member",
            headSha=head_sha,
            mainSha=MAIN_SHA,
            proposedChange="Add browser extension synchronization.",
            sourceUrl="https://github.com/acme/alignment-memory/pull/7",
            prNumber=7,
            publicationKind="pr_comment",
        ),
        knowledgeRevision=3,
        contextIsSufficient=True,
        promptVersion="worker-v1",
        provider="fixture",
        requestedModel="fixture-primary",
        actualModel="fixture-model",
        inputHash=request.input_hash,
        usage={"total_tokens": 0},
        documents=(document,),
        analysis=analysis,
        createdAt=datetime(2026, 8, 4, tzinfo=UTC),
    )


def _repository_artifact() -> ValidatedAnalysisArtifact:
    document = ArtifactDocument(
        sourceVersionId="source-version-1",
        sourceType="markdown",
        url=SOURCE_URL,
        content=QUOTE,
    )
    evidence = {
        "source_version_id": "source-version-1",
        "url": SOURCE_URL,
        "exact_quote": QUOTE,
        "role": "supports",
    }
    analysis = AnalysisResult.model_validate(
        {
            "outcome": "aligned",
            "nodes": [
                {
                    "logical_key": "web-only",
                    "node_type": "decision",
                    "title": "Keep the MVP web-only",
                    "summary": "Do not add a browser extension.",
                    "status": "active",
                    "evidence": [evidence],
                },
                {
                    "logical_key": "ship-mvp",
                    "node_type": "goal",
                    "title": "Ship the MVP",
                    "summary": "Deliver one vertical slice.",
                    "status": "active",
                    "evidence": [evidence],
                },
            ],
            "findings": [],
            "edges": [
                {
                    "from_node_logical_key": "ship-mvp",
                    "to_node_logical_key": "web-only",
                    "relation_type": "constrains",
                    "evidence": [evidence],
                }
            ],
        }
    )
    request = AnalysisRequest(
        job_id="job-sync",
        repository_id=REPOSITORY_ID,
        pr_number=0,
        head_sha=MAIN_SHA,
        knowledge_revision=3,
        prompt_version="worker-v1",
        documents=(document.as_analysis_document(),),
    )
    return ValidatedAnalysisArtifact(
        schemaVersion="alignment-memory/v1",
        validationStatus="validated",
        jobId="job-sync",
        event=ArtifactEvent(
            eventName="workflow_dispatch",
            eventKey=f"initial-sync:123:{MAIN_SHA}",
            repositoryId=REPOSITORY_ID,
            repositoryFullName="acme/alignment-memory",
            githubRepositoryId=123,
            actorLogin="owner",
            headSha=MAIN_SHA,
            mainSha=MAIN_SHA,
            proposedChange="Initial repository synchronization requested.",
            sourceUrl=f"https://github.com/acme/alignment-memory/tree/{MAIN_SHA}",
            publicationKind="generated_wiki",
        ),
        knowledgeRevision=3,
        contextIsSufficient=True,
        promptVersion="worker-v1",
        provider="fixture",
        requestedModel="fixture-primary",
        actualModel="fixture-model",
        inputHash=request.input_hash,
        usage={"total_tokens": 0},
        documents=(document,),
        analysis=analysis,
        createdAt=datetime(2026, 8, 4, tzinfo=UTC),
    )


def test_pr_comment_has_fixed_alignment_diff_fields_and_stable_marker() -> None:
    artifact = _artifact()
    comment = render_pr_comment(artifact)

    assert "Textual outcome:** Direct Conflict" in comment
    assert "Existing agreement" in comment
    assert "Proposed change" in comment
    assert QUOTE in comment
    assert SOURCE_URL in comment
    assert "Reason" in comment
    assert "Next action" in comment
    assert HEAD_SHA in comment
    assert comment_marker(artifact) == comment_marker(_artifact(head_sha="c" * 40))


def test_artifact_schema_rejects_tampered_quote_and_input_hash() -> None:
    payload = _artifact().model_dump(mode="json", by_alias=True)
    payload["analysis"]["findings"][0]["evidence"][0]["exact_quote"] = "fabricated"
    with pytest.raises(ValidationError, match="evidence quote"):
        ValidatedAnalysisArtifact.model_validate(payload)

    payload = _artifact().model_dump(mode="json", by_alias=True)
    payload["inputHash"] = "f" * 64
    with pytest.raises(ValidationError, match="input hash"):
        ValidatedAnalysisArtifact.model_validate(payload)


def test_generated_wiki_is_deterministic_sorted_markdown_with_wikilinks() -> None:
    artifact = _repository_artifact()
    first = render_generated_wiki(artifact)
    second = render_generated_wiki(artifact)

    assert first == second
    assert "[[project-memory#ship-mvp|Ship the MVP]]" in first
    assert "[[project-memory#web-only|Keep the MVP web-only]]" in first
    assert first.index("## Goals") < first.index("## Decisions")
    assert SOURCE_URL in first


@pytest.mark.parametrize(
    "requested",
    [
        "../outside.md",
        "knowledge/generated/other.md",
        "knowledge/generated/../outside.md",
        "/tmp/project-memory.md",
    ],
)
def test_generated_path_rejects_traversal_and_arbitrary_filenames(
    tmp_path: Path,
    requested: str,
) -> None:
    with pytest.raises(ValueError):
        resolve_generated_path(tmp_path, requested)


def test_generated_path_is_fixed_under_allowlisted_directory(tmp_path: Path) -> None:
    assert resolve_generated_path(tmp_path).relative_to(tmp_path) == Path(
        "knowledge/generated/project-memory.md"
    )
