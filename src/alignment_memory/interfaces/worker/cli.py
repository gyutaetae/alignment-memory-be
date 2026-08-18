from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from alignment_memory.adapters.github import (
    GitHubAdapterConfig,
    GitHubAppAdapter,
    GitHubAppCredentials,
)
from alignment_memory.adapters.openrouter import OpenRouterAdapter, OpenRouterConfig
from alignment_memory.interfaces.worker.api_client import HmacApiClient, WorkerApiError
from alignment_memory.interfaces.worker.event_parser import (
    EventParseError,
    ParsedGitHubEvent,
    load_github_event,
    parse_github_event,
)
from alignment_memory.interfaces.worker.publish_templates import (
    CHECK_NAME,
    GENERATED_RELATIVE_PATH,
    check_conclusion,
    comment_marker,
    render_check_summary,
    render_generated_wiki,
    render_pr_comment,
    resolve_generated_path,
)
from alignment_memory.interfaces.worker.result_schema import (
    ArtifactDocument,
    ArtifactEvent,
    ValidatedAnalysisArtifact,
)
from alignment_memory.ports import (
    AnalysisRequest,
    GitHubRepositoryRef,
    LlmAnalysis,
)

_PROMPT_VERSION = "alignment-worker-v1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alignment-memory-worker")
    commands = parser.add_subparsers(dest="command", required=True)

    analyze = commands.add_parser(
        "analyze-event",
        help="parse an allowlisted GitHub event and emit a validated analysis artifact",
    )
    analyze.add_argument("--event-path", type=Path, required=True)
    analyze.add_argument("--event-name", default=os.getenv("GITHUB_EVENT_NAME"))
    analyze.add_argument("--trusted-head-sha", default=os.getenv("GITHUB_SHA"))
    analyze.add_argument("--repository-id", default=os.getenv("ALIGNMENT_REPOSITORY_ID"))
    analyze.add_argument("--job-id", default=os.getenv("INPUT_JOB_ID"))
    analyze.add_argument("--output", type=Path, required=True)
    analyze.add_argument("--api-base-url", default=os.getenv("ALIGNMENT_API_BASE_URL"))
    analyze.add_argument("--api-hmac-secret", default=os.getenv("INTERNAL_HMAC_SECRET"))
    analyze.add_argument("--openrouter-api-key", default=os.getenv("OPENROUTER_API_KEY"))
    analyze.add_argument(
        "--openrouter-primary-model",
        default=os.getenv("OPENROUTER_PRIMARY_MODEL", "openai/gpt-4.1-mini"),
    )
    analyze.add_argument(
        "--openrouter-fallback-model",
        default=os.getenv("OPENROUTER_FALLBACK_MODEL", "google/gemini-2.5-flash"),
    )
    analyze.add_argument(
        "--openrouter-base-url",
        default=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )
    analyze.add_argument("--github-token", default=os.getenv("GITHUB_TOKEN"))
    analyze.add_argument(
        "--github-api-base-url",
        default=os.getenv("GITHUB_API_URL", "https://api.github.com"),
    )
    analyze.add_argument("--prompt-version", default=_PROMPT_VERSION)

    publish = commands.add_parser(
        "publish",
        help="render a fixed publication from a validated analysis artifact",
    )
    publish.add_argument("--artifact", type=Path, required=True)
    publish.add_argument("--output", type=Path, required=True)
    publish.add_argument("--repository-root", type=Path, default=Path.cwd())

    demo = commands.add_parser(
        "demo",
        help="run the credential-free fixture vertical slice and evaluation",
    )
    demo.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "analyze-event":
            asyncio.run(_run_analyze_command(args))
        elif args.command == "publish":
            _run_publish_command(args)
        else:
            from alignment_memory.interfaces.worker.demo import run_demo

            asyncio.run(run_demo(args.output, analyze_event_runner=analyze_event))
    except (
        EventParseError,
        ValidationError,
        ValueError,
        WorkerApiError,
        OSError,
        RuntimeError,
    ) as error:
        print(f"alignment-memory-worker: {error}", file=sys.stderr)
        return 2
    return 0


async def _run_analyze_command(args: argparse.Namespace) -> None:
    event_name = _required(args.event_name, "GitHub event name")
    payload = load_github_event(args.event_path)
    event = parse_github_event(
        payload,
        event_name=event_name,
        trusted_head_sha=args.trusted_head_sha,
    )
    api = HmacApiClient(
        _required(args.api_base_url, "Alignment API base URL"),
        _required(args.api_hmac_secret, "internal API HMAC secret"),
    )
    llm = OpenRouterAdapter(
        _required(args.openrouter_api_key, "OpenRouter API key"),
        OpenRouterConfig(
            primary_model=_required(args.openrouter_primary_model, "OpenRouter primary model"),
            fallback_model=args.openrouter_fallback_model or None,
            base_url=_required(args.openrouter_base_url, "OpenRouter base URL"),
        ),
    )
    github = GitHubAppAdapter(
        GitHubAppCredentials(app_id="workflow-token", private_key="unused"),
        config=GitHubAdapterConfig(api_base_url=args.github_api_base_url),
        installation_token=_required(args.github_token, "GitHub token"),
    )
    try:
        artifact = await analyze_event(
            event,
            repository_id=args.repository_id,
            supplied_job_id=args.job_id,
            prompt_version=args.prompt_version,
            api=api,
            github=github,
            llm=llm,
        )
        _write_json(args.output, artifact.model_dump(mode="json", by_alias=True))
    finally:
        await github.close()
        await llm.close()
        await api.close()


async def analyze_event(
    event: ParsedGitHubEvent,
    *,
    repository_id: str | None,
    supplied_job_id: str | None,
    prompt_version: str,
    api: HmacApiClient,
    github: GitHubAppAdapter,
    llm: OpenRouterAdapter,
) -> ValidatedAnalysisArtifact:
    job_id: str | None = supplied_job_id
    current_status: str | None = None
    try:
        if job_id is None:
            internal_repository_id = _required(
                repository_id,
                "ALIGNMENT_REPOSITORY_ID for event-created jobs",
            )
            created = await api.create_job(
                repository_id=internal_repository_id,
                event_key=event.event_key,
                event_type=_job_type(event),
                head_sha=event.head_sha,
            )
            job_id = _object_text(created, "jobId")

        context = await api.get_job_context(job_id)
        job = _object(context, "job")
        repository = _object(context, "repository")
        current_status = _object_text(job, "status")
        internal_repository_id = _object_text(repository, "id")
        if repository_id is not None and repository_id != internal_repository_id:
            raise ValueError("worker repository ID does not match the API job context")
        _verify_repository_identity(event, repository)

        current_status = await _advance(
            api,
            job_id,
            current_status,
            expected="queued",
            target="fetching",
        )
        repository_ref = GitHubRepositoryRef(
            repository_id=internal_repository_id,
            owner=_object_text(repository, "owner"),
            name=_object_text(repository, "name"),
            installation_id=_object_positive_int(repository, "githubInstallationId"),
            default_branch=_object_text(repository, "defaultBranch"),
        )
        if event.pr_number is not None:
            source_batch = await github.fetch_pr_context(
                repository_ref,
                number=event.pr_number,
                head_sha=event.head_sha,
                actor_login=event.actor_login,
            )
        else:
            if not await github.actor_is_allowed(repository_ref, event.actor_login):
                raise EventParseError("repository event actor is not an allowed collaborator")
            source_batch = await github.fetch_allowed_sources(
                repository_ref,
                baseline_commit_sha=(
                    _object_optional_text(repository, "baselineCommitSha")
                    if event.event_name == "push"
                    else None
                ),
                actor_login=event.actor_login,
            )

        documents, context_document_ids = _analysis_documents(
            event,
            context,
            source_batch.sources,
        )
        context_is_sufficient = bool(context_document_ids) if event.pr_number else True
        current_status = await _advance(
            api,
            job_id,
            current_status,
            expected="fetching",
            target="analyzing",
        )
        request = AnalysisRequest(
            job_id=job_id,
            repository_id=internal_repository_id,
            pr_number=event.pr_number or 0,
            head_sha=event.head_sha,
            knowledge_revision=_object_nonnegative_int(repository, "knowledgeRevision"),
            prompt_version=prompt_version,
            documents=tuple(document.as_analysis_document() for document in documents),
            context_is_sufficient=context_is_sufficient,
        )
        analysis = await llm.analyze(request)
        if (
            analysis.input_hash != request.input_hash
            or analysis.prompt_version != request.prompt_version
        ):
            raise ValueError("LLM analysis provenance does not match worker inputs")
        current_status = await _advance(
            api,
            job_id,
            current_status,
            expected="analyzing",
            target="validating",
        )
        if event.pr_number is not None:
            _require_pr_result_uses_active_context(analysis, context_document_ids)

        artifact = _validated_artifact(
            job_id=job_id,
            event=event,
            repository_id=internal_repository_id,
            knowledge_revision=request.knowledge_revision,
            context_is_sufficient=context_is_sufficient,
            documents=documents,
            analysis=analysis,
        )
        current_status = await _advance(
            api,
            job_id,
            current_status,
            expected="validating",
            target="persisting",
        )
        if event.pr_number is not None:
            await api.persist_result(job_id, _worker_result_payload(artifact))
        return artifact
    except Exception as error:
        if job_id is not None and current_status not in {None, "completed", "failed"}:
            with suppress(WorkerApiError, ValueError):
                await api.transition_job(
                    job_id,
                    expected_status=current_status,
                    next_status="failed",
                    error_code=_safe_error_code(error),
                )
        raise


def _run_publish_command(args: argparse.Namespace) -> None:
    try:
        artifact = ValidatedAnalysisArtifact.model_validate_json(
            args.artifact.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as error:
        raise ValueError("publish requires a valid, validated analysis artifact") from error

    manifest: dict[str, object] = {
        "schemaVersion": "alignment-memory-publish/v1",
        "publicationKind": artifact.event.publication_kind,
        "jobId": artifact.job_id,
        "headSha": artifact.event.head_sha,
        "expectedMainSha": artifact.event.main_sha,
        "repositoryFullName": artifact.event.repository_full_name,
        "inputHash": artifact.input_hash,
        "generatedFiles": [],
    }
    if artifact.event.publication_kind == "pr_comment":
        manifest.update(
            {
                "prNumber": artifact.event.pr_number,
                "commentMarker": comment_marker(artifact),
                "commentBody": render_pr_comment(artifact),
                "checkName": CHECK_NAME,
                "checkSummary": render_check_summary(artifact),
                "checkConclusion": check_conclusion(artifact),
            }
        )
    else:
        target = resolve_generated_path(args.repository_root)
        content = render_generated_wiki(artifact)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        manifest["generatedFiles"] = [
            {
                "path": GENERATED_RELATIVE_PATH.as_posix(),
                "contentHash": hashlib.sha256(content.encode()).hexdigest(),
            }
        ]
    _write_json(args.output, manifest)


def _analysis_documents(
    event: ParsedGitHubEvent,
    context: Mapping[str, Any],
    sources: Sequence[object],
) -> tuple[tuple[ArtifactDocument, ...], frozenset[str]]:
    documents = list(_context_documents(context))
    context_ids = frozenset(document.source_version_id for document in documents)
    for source in sources:
        source_version_id = getattr(source, "source_version_id", None)
        source_type = getattr(source, "source_type", None)
        url = getattr(source, "url", None)
        content = getattr(source, "content", None)
        if not all(isinstance(item, str) for item in (source_version_id, url, content)):
            raise ValueError("GitHub adapter returned an invalid collected source")
        source_type_value = getattr(source_type, "value", source_type)
        if not isinstance(source_type_value, str):
            raise ValueError("GitHub adapter returned an invalid source type")
        documents.append(
            ArtifactDocument(
                sourceVersionId=source_version_id,
                sourceType=source_type_value,
                url=url,
                content=content,
            )
        )
    documents.append(
        ArtifactDocument(
            sourceVersionId=event.event_source_version_id,
            sourceType=f"github_{event.event_name}_event",
            url=event.source_url,
            content=event.proposed_change,
        )
    )
    unique: dict[str, ArtifactDocument] = {}
    for document in documents:
        existing = unique.get(document.source_version_id)
        if existing is not None and existing != document:
            raise ValueError("source version ID collision in worker inputs")
        unique[document.source_version_id] = document
    ordered = tuple(
        sorted(
            unique.values(),
            key=lambda item: (item.source_type, item.source_version_id, str(item.url)),
        )
    )
    return ordered, context_ids


def _context_documents(context: Mapping[str, Any]) -> tuple[ArtifactDocument, ...]:
    knowledge = context.get("knowledge")
    if not isinstance(knowledge, list):
        raise ValueError("API job context knowledge must be a list")
    grouped: dict[tuple[str, str], set[str]] = {}
    for version in knowledge:
        if not isinstance(version, Mapping):
            raise ValueError("API knowledge context entry must be an object")
        evidence_items = version.get("evidence")
        if not isinstance(evidence_items, list):
            raise ValueError("API knowledge evidence must be a list")
        for evidence in evidence_items:
            if not isinstance(evidence, Mapping) or evidence.get("verified") is not True:
                continue
            source_version_id = _object_text(evidence, "sourceVersionId")
            url = _object_text(evidence, "url")
            quote = _object_text(evidence, "exactQuote")
            grouped.setdefault((source_version_id, url), set()).add(quote)
    return tuple(
        ArtifactDocument(
            sourceVersionId=source_version_id,
            sourceType="active_knowledge",
            url=url,
            content="\n\n".join(sorted(quotes)),
        )
        for (source_version_id, url), quotes in sorted(grouped.items())
    )


def _validated_artifact(
    *,
    job_id: str,
    event: ParsedGitHubEvent,
    repository_id: str,
    knowledge_revision: int,
    context_is_sufficient: bool,
    documents: tuple[ArtifactDocument, ...],
    analysis: LlmAnalysis,
) -> ValidatedAnalysisArtifact:
    return ValidatedAnalysisArtifact(
        schemaVersion="alignment-memory/v1",
        validationStatus="validated",
        jobId=job_id,
        event=ArtifactEvent(
            eventName=event.event_name,
            eventKey=event.event_key,
            repositoryId=repository_id,
            repositoryFullName=event.repository_full_name,
            githubRepositoryId=event.github_repository_id,
            actorLogin=event.actor_login,
            headSha=event.head_sha,
            mainSha=event.main_sha,
            proposedChange=event.proposed_change,
            sourceUrl=event.source_url,
            prNumber=event.pr_number,
            publicationKind=event.publication_kind,
        ),
        knowledgeRevision=knowledge_revision,
        contextIsSufficient=context_is_sufficient,
        promptVersion=analysis.prompt_version,
        provider=analysis.provider,
        requestedModel=analysis.requested_model,
        actualModel=analysis.actual_model,
        inputHash=analysis.input_hash,
        usage=analysis.usage.as_dict(),
        cost=analysis.usage.cost,
        documents=documents,
        analysis=analysis.result,
        createdAt=datetime.now(UTC),
    )


def _worker_result_payload(artifact: ValidatedAnalysisArtifact) -> dict[str, object]:
    if artifact.event.pr_number is None:
        raise ValueError("repository artifacts cannot use the PR result endpoint")
    return {
        "repositoryId": artifact.event.repository_id,
        "prNumber": artifact.event.pr_number,
        "headSha": artifact.event.head_sha,
        "mainSha": artifact.event.main_sha,
        "knowledgeRevision": artifact.knowledge_revision,
        "provider": artifact.provider,
        "requestedModel": artifact.requested_model,
        "actualModel": artifact.actual_model,
        "promptVersion": artifact.prompt_version,
        "inputHash": artifact.input_hash,
        "usage": artifact.usage,
        "cost": artifact.cost,
        "contextIsSufficient": artifact.context_is_sufficient,
        "analysis": artifact.analysis.model_dump(mode="json"),
    }


def _require_pr_result_uses_active_context(
    analysis: LlmAnalysis,
    context_document_ids: frozenset[str],
) -> None:
    if analysis.result.nodes or analysis.result.edges:
        raise ValueError("PR analysis cannot propose official knowledge nodes or edges")
    evidence_sets = [finding.evidence for finding in analysis.result.findings]
    for evidence_set in evidence_sets:
        for evidence in evidence_set:
            if evidence.source_version_id not in context_document_ids:
                raise ValueError("PR finding evidence must cite persisted active knowledge")


async def _advance(
    api: HmacApiClient,
    job_id: str,
    current_status: str,
    *,
    expected: str,
    target: str,
) -> str:
    if current_status == target:
        return target
    ordered = (
        "queued",
        "fetching",
        "analyzing",
        "validating",
        "persisting",
        "writing_github",
        "completed",
    )
    if current_status == "failed":
        raise WorkerApiError("job_state_conflict", "failed jobs cannot be resumed")
    if (
        current_status in ordered
        and target in ordered
        and ordered.index(current_status) > ordered.index(target)
    ):
        return current_status
    if current_status != expected:
        raise WorkerApiError(
            "job_state_conflict",
            f"worker expected job status {expected}, received {current_status}",
        )
    response = await api.transition_job(
        job_id,
        expected_status=expected,
        next_status=target,
    )
    returned_status = _object_text(response, "status")
    if returned_status != target:
        raise WorkerApiError(
            "job_state_conflict",
            "control plane returned an unexpected job status",
        )
    return returned_status


def _verify_repository_identity(
    event: ParsedGitHubEvent,
    repository: Mapping[str, Any],
) -> None:
    if _object_text(repository, "fullName") != event.repository_full_name:
        raise ValueError("GitHub event repository does not match API job context")
    if _object_positive_int(repository, "githubRepositoryId") != event.github_repository_id:
        raise ValueError("GitHub numeric repository identity does not match API job context")
    current_main = _object_optional_text(repository, "mainCommitSha")
    if (
        event.event_name in {"pull_request", "workflow_dispatch"}
        and current_main is not None
        and current_main != event.main_sha
    ):
        raise ValueError("GitHub event was created from a stale main SHA")


def _job_type(event: ParsedGitHubEvent) -> str:
    if event.event_name == "pull_request":
        return "pr_analysis"
    if event.event_name == "workflow_dispatch":
        return "initial_sync"
    return "merge_publish"


def _safe_error_code(error: Exception) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and code.strip():
        return code[:100]
    if isinstance(error, (EventParseError, ValidationError, ValueError)):
        return "worker_validation_failed"
    return "worker_analysis_failed"


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _required(value: str | None, name: str) -> str:
    if value is None or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _object(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"API response field {key} must be an object")
    return value


def _object_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"API response field {key} must be non-empty text")
    return value


def _object_optional_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"API response field {key} must be text or null")
    return value


def _object_positive_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"API response field {key} must be a positive integer")
    return value


def _object_nonnegative_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"API response field {key} must be a non-negative integer")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
