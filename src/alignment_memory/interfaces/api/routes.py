from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse

from alignment_memory.domain import (
    AiRun,
    Alignment,
    ContextPassport,
    EvidenceReference,
    Finding,
    Handshake,
    Job,
    JobStatus,
    JobType,
    KnowledgeNodeVersion,
    Override,
    ValidationStatus,
    determine_alignment_outcome,
    verify_evidence_reference,
)
from alignment_memory.interfaces.api.dependencies import (
    AppContainer,
    StoredOperation,
    get_container,
)
from alignment_memory.interfaces.api.errors import ApiError
from alignment_memory.interfaces.api.schemas import (
    HandshakeCreate,
    InternalJobCreate,
    InternalJobEvent,
    OverrideCreate,
    PassportGenerate,
    WorkerResult,
)
from alignment_memory.interfaces.api.security import (
    InternalRequestContext,
    UserContext,
    authenticate_internal,
    authenticate_user,
)
from alignment_memory.ports import (
    GitHubAdapterError,
    GitHubRepositoryRef,
    MembershipRecord,
    RepositoryRecord,
)

router = APIRouter(prefix="/api/v1")
User = Annotated[UserContext, Depends(authenticate_user)]
Internal = Annotated[InternalRequestContext, Depends(authenticate_internal)]
Container = Annotated[AppContainer, Depends(get_container)]
_WRITE_PERMISSIONS = frozenset({"write", "maintain", "admin"})


@router.get("/repositories", tags=["repositories"])
async def list_repositories(user: User, container: Container) -> dict[str, object]:
    repository = container.require_repository()
    records = await repository.list_repositories(user.profile_id)
    return {"repositories": [_repository_payload(record) for record in records]}


@router.get("/github/installations/callback", tags=["repositories"])
async def github_installation_callback(
    user: User,
    container: Container,
    installation_id: Annotated[int, Query(alias="installation_id", gt=0)],
) -> dict[str, object]:
    repository = container.require_repository()
    records = await repository.list_installation_repositories(
        user.profile_id,
        installation_id,
    )
    if not records:
        raise ApiError(
            status_code=403,
            code="repository_membership_required",
            message="No connected repository membership was found for this installation",
        )
    return {
        "installationId": installation_id,
        "repositories": [_repository_payload(record) for record in records],
    }


@router.post(
    "/repositories/{repository_id}/sync",
    tags=["repositories"],
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_initial_sync(
    repository_id: str,
    user: User,
    container: Container,
) -> dict[str, object]:
    repository = container.require_repository()
    record, _membership = await _require_repository_access(
        repository_id,
        user,
        container,
        write=True,
    )
    event_key = f"initial-sync:{record.id}:{record.baseline_commit_sha or 'initial'}"
    job_id = _stable_id("job", record.id, event_key)
    existing = await repository.get_job(job_id)
    now = datetime.now(UTC)
    job = await repository.create_job(
        Job(
            id=job_id,
            repository_id=record.id,
            event_key=event_key,
            job_type=JobType.INITIAL_SYNC,
            status=JobStatus.QUEUED,
            progress=0,
            created_at=now,
            updated_at=now,
        )
    )
    if existing is None:
        try:
            await container.github.dispatch_sync(
                GitHubRepositoryRef(
                    repository_id=record.id,
                    owner=record.owner,
                    name=record.name,
                    installation_id=record.github_installation_id,
                    default_branch=record.default_branch,
                ),
                job.id,
            )
        except GitHubAdapterError as error:
            await repository.compare_and_set_job(
                job.id,
                JobStatus.QUEUED,
                JobStatus.FAILED,
                occurred_at=datetime.now(UTC),
                error_code=error.code,
            )
            raise ApiError(
                status_code=502,
                code="sync_dispatch_failed",
                message="The sync job could not be dispatched",
                retryable=error.retryable,
            ) from error
    return _job_payload(job)


@router.get("/jobs/{job_id}", tags=["jobs"])
async def poll_job(job_id: str, user: User, container: Container) -> dict[str, object]:
    repository = container.require_repository()
    job = await repository.get_job(job_id)
    if job is None:
        raise _not_found("job")
    await _require_repository_access(job.repository_id, user, container)
    return _job_payload(job)


@router.get("/repositories/{repository_id}/dashboard", tags=["dashboard"])
async def get_dashboard(
    repository_id: str,
    user: User,
    container: Container,
) -> dict[str, object]:
    repository = container.require_repository()
    record, _membership = await _require_repository_access(repository_id, user, container)
    alignments = await repository.list_alignments(repository_id)
    jobs = await repository.list_jobs(repository_id)
    snapshots = await repository.list_knowledge_snapshots(repository_id)
    return {
        "repository": _repository_payload(record),
        "summary": {
            "sourceCount": await repository.count_sources(repository_id),
            "knowledgeNodeCount": len(snapshots),
            "alignmentCount": len(alignments),
            "openJobCount": sum(
                job.status not in {JobStatus.COMPLETED, JobStatus.FAILED} for job in jobs
            ),
        },
        "recentAlignments": [_alignment_summary(item) for item in alignments[:10]],
        "jobs": [_job_payload(item) for item in jobs[:10]],
    }


@router.get("/repositories/{repository_id}/graph", tags=["graph"])
async def get_relevant_graph(
    repository_id: str,
    user: User,
    container: Container,
) -> dict[str, object]:
    repository = container.require_repository()
    record, _membership = await _require_repository_access(repository_id, user, container)
    snapshots = await repository.list_knowledge_snapshots(repository_id)
    edges = await repository.list_knowledge_edges(repository_id)
    return {
        "repositoryId": record.id,
        "knowledgeRevision": record.knowledge_revision,
        "nodes": [
            {
                "id": snapshot.node.id,
                "logicalKey": snapshot.node.logical_key,
                "nodeType": snapshot.node.node_type.value,
                "title": snapshot.version.title,
                "summary": snapshot.version.summary,
                "status": snapshot.version.status.value,
                "revision": snapshot.version.revision,
                "evidence": [_evidence_payload(item) for item in snapshot.version.evidence],
            }
            for snapshot in snapshots
        ],
        "edges": [
            {
                "id": edge.id,
                "fromNodeId": edge.from_node_id,
                "toNodeId": edge.to_node_id,
                "relationType": edge.relation_type,
                "validFromRevision": edge.valid_from_revision,
                "evidence": [_evidence_payload(item) for item in edge.evidence],
            }
            for edge in edges
        ],
    }


@router.get("/alignments/{alignment_id}", tags=["alignments"])
async def get_alignment_detail(
    alignment_id: str,
    user: User,
    container: Container,
) -> dict[str, object]:
    repository = container.require_repository()
    alignment = await repository.get_alignment(alignment_id)
    if alignment is None:
        raise _not_found("alignment")
    await _require_repository_access(alignment.repository_id, user, container)
    handshakes = await repository.list_handshakes(alignment.id)
    overrides = await repository.list_overrides("alignment", alignment.id)
    finding_overrides = [
        override
        for finding in alignment.findings
        for override in await repository.list_overrides("finding", finding.id)
    ]
    return {
        **_alignment_payload(alignment),
        "handshakes": [_handshake_payload(item) for item in handshakes],
        "overrides": [_override_payload(item) for item in (*overrides, *finding_overrides)],
    }


@router.get("/alignments/{alignment_id}/context-passport", tags=["alignments"])
async def read_context_passport(
    alignment_id: str,
    user: User,
    container: Container,
    language: str | None = None,
) -> dict[str, object]:
    repository = container.require_repository()
    alignment = await repository.get_alignment(alignment_id)
    if alignment is None:
        raise _not_found("alignment")
    await _require_repository_access(alignment.repository_id, user, container)
    passport = await repository.get_context_passport(
        alignment.id,
        user.profile_id,
        language,
    )
    if passport is None:
        raise _not_found("context_passport")
    return _passport_payload(passport)


@router.post(
    "/alignments/{alignment_id}/context-passport/generate",
    tags=["alignments"],
    status_code=status.HTTP_201_CREATED,
)
async def generate_context_passport(
    alignment_id: str,
    body: PassportGenerate,
    user: User,
    container: Container,
) -> dict[str, object]:
    repository = container.require_repository()
    alignment = await repository.get_alignment(alignment_id)
    if alignment is None:
        raise _not_found("alignment")
    await _require_repository_access(alignment.repository_id, user, container)
    existing = await repository.get_context_passport(
        alignment.id,
        user.profile_id,
        body.language,
    )
    if existing is not None:
        return _passport_payload(existing)
    source_version_ids = tuple(
        dict.fromkeys(
            evidence.source_version_id
            for finding in alignment.findings
            for evidence in finding.evidence
        )
    )
    if not source_version_ids:
        raise ApiError(
            status_code=409,
            code="passport_evidence_required",
            message="A Context Passport requires validated source evidence",
        )
    explanations = " ".join(finding.explanation for finding in alignment.findings)
    passport = await repository.append_context_passport(
        ContextPassport(
            id=_stable_id(
                "context-passport",
                alignment.id,
                user.profile_id,
                body.language,
                alignment.ai_run_id,
            ),
            analysis_id=alignment.id,
            profile_id=user.profile_id,
            language=body.language,
            content=f"Alignment outcome: {alignment.outcome.value}. {explanations}".strip(),
            source_version_ids=source_version_ids,
            ambiguities=(),
            ai_run_id=alignment.ai_run_id,
            created_at=datetime.now(UTC),
        )
    )
    return _passport_payload(passport)


@router.post(
    "/alignments/{alignment_id}/handshakes",
    tags=["alignments"],
    status_code=status.HTTP_201_CREATED,
)
async def append_handshake(
    alignment_id: str,
    body: HandshakeCreate,
    user: User,
    container: Container,
) -> dict[str, object]:
    repository = container.require_repository()
    alignment = await repository.get_alignment(alignment_id)
    if alignment is None:
        raise _not_found("alignment")
    await _require_repository_access(alignment.repository_id, user, container)
    handshake = await repository.append_handshake(
        Handshake(
            id=str(uuid4()),
            analysis_id=alignment.id,
            profile_id=user.profile_id,
            response=body.response,
            message=body.message,
            source_language=body.source_language,
            created_at=datetime.now(UTC),
        )
    )
    return _handshake_payload(handshake)


@router.post(
    "/alignments/{alignment_id}/overrides",
    tags=["alignments"],
    status_code=status.HTTP_201_CREATED,
)
async def append_override(
    alignment_id: str,
    body: OverrideCreate,
    user: User,
    container: Container,
) -> dict[str, object]:
    repository = container.require_repository()
    alignment = await repository.get_alignment(alignment_id)
    if alignment is None:
        raise _not_found("alignment")
    await _require_repository_access(alignment.repository_id, user, container, write=True)
    target_id = body.target_id or alignment.id
    target_repository_id = await repository.repository_id_for_target(body.target_type, target_id)
    if target_repository_id != alignment.repository_id:
        raise ApiError(
            status_code=400,
            code="invalid_override_target",
            message="Override target does not belong to this alignment repository",
        )
    override = await repository.append_override(
        Override(
            id=str(uuid4()),
            target_type=body.target_type,
            target_id=target_id,
            override_type=body.override_type,
            reason=body.reason,
            actor_profile_id=user.profile_id,
            created_at=datetime.now(UTC),
        )
    )
    return _override_payload(override)


@router.post(
    "/internal/jobs",
    tags=["internal"],
    status_code=status.HTTP_201_CREATED,
)
async def create_internal_job(
    request: Request,
    body: InternalJobCreate,
    internal: Internal,
    container: Container,
) -> JSONResponse:
    replay = await _replayed_operation(request, internal, container)
    if replay is not None:
        return replay
    repository = container.require_repository()
    record = await repository.get_repository_record(body.repository_id)
    if record is None:
        raise _not_found("repository")
    job = await repository.create_job(
        Job(
            id=_stable_id("job", body.repository_id, body.event_key),
            repository_id=body.repository_id,
            event_key=body.event_key,
            job_type=body.event_type,
            status=JobStatus.QUEUED,
            progress=0,
            head_sha=body.head_sha,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )
    return await _store_operation(request, internal, container, 201, _job_payload(job))


@router.get("/internal/jobs/{job_id}/context", tags=["internal"])
async def get_internal_job_context(
    job_id: str,
    _internal: Internal,
    container: Container,
) -> dict[str, object]:
    repository = container.require_repository()
    job = await repository.get_job(job_id)
    if job is None:
        raise _not_found("job")
    record = await repository.get_repository_record(job.repository_id)
    if record is None:
        raise _not_found("repository")
    context = await repository.get_active_context(
        job.repository_id,
        record.knowledge_revision,
    )
    return {
        "job": _job_payload(job),
        "repository": _repository_payload(record),
        "knowledge": [_knowledge_version_payload(item) for item in context],
    }


@router.post("/internal/jobs/{job_id}/events", tags=["internal"])
async def append_internal_job_event(
    job_id: str,
    request: Request,
    body: InternalJobEvent,
    internal: Internal,
    container: Container,
) -> JSONResponse:
    replay = await _replayed_operation(request, internal, container)
    if replay is not None:
        return replay
    repository = container.require_repository()
    try:
        transitioned = await repository.compare_and_set_job(
            job_id,
            body.expected_status,
            body.next_status,
            occurred_at=datetime.now(UTC),
            error_code=body.error_code,
        )
    except KeyError as error:
        raise _not_found("job") from error
    if transitioned is None:
        current = await repository.get_job(job_id)
        if current is not None and current.status is body.next_status:
            return await _store_operation(
                request,
                internal,
                container,
                200,
                _job_payload(current),
            )
        raise ApiError(
            status_code=409,
            code="job_state_conflict",
            message="Job status changed before this event was applied",
            retryable=True,
        )
    return await _store_operation(
        request,
        internal,
        container,
        200,
        _job_payload(transitioned),
    )


@router.post("/internal/jobs/{job_id}/result", tags=["internal"])
async def persist_internal_result(
    job_id: str,
    request: Request,
    body: WorkerResult,
    internal: Internal,
    container: Container,
) -> JSONResponse:
    replay = await _replayed_operation(request, internal, container)
    if replay is not None:
        return replay
    repository = container.require_repository()
    job = await repository.get_job(job_id)
    if job is None:
        raise _not_found("job")
    existing_result = await repository.get_result_for_job(job_id)
    if existing_result is not None:
        if (
            existing_result.repository_id == body.repository_id
            and existing_result.pr_number == body.pr_number
            and existing_result.head_sha == body.head_sha
            and existing_result.knowledge_revision == body.knowledge_revision
            and existing_result.outcome is body.analysis.outcome
        ):
            return await _store_operation(
                request,
                internal,
                container,
                200,
                _alignment_payload(existing_result),
            )
        raise ApiError(
            status_code=409,
            code="result_conflict",
            message="This job already has a different validated result",
        )
    verified_evidence: dict[tuple[str, str, str, str], EvidenceReference] = {}
    evidence_sets = [node.evidence for node in body.analysis.nodes]
    evidence_sets.extend(finding.evidence for finding in body.analysis.findings)
    evidence_sets.extend(edge.evidence for edge in body.analysis.edges)
    for evidence_set in evidence_sets:
        for evidence in evidence_set:
            stored = await repository.get_source_version_with_source(
                evidence.source_version_id
            )
            if stored is None:
                raise ApiError(
                    status_code=422,
                    code="invalid_result_evidence",
                    message="Worker result references unknown source evidence",
                )
            source, source_version = stored
            verified = verify_evidence_reference(
                EvidenceReference(
                    source_version_id=evidence.source_version_id,
                    url=str(evidence.url),
                    exact_quote=evidence.exact_quote,
                    role=evidence.role,
                ),
                source,
                source_version,
            )
            verified_evidence[
                (
                    evidence.source_version_id,
                    str(evidence.url),
                    evidence.exact_quote,
                    evidence.role.value,
                )
            ] = verified

    alignment_id = _stable_id(
        "alignment",
        body.repository_id,
        str(body.pr_number),
        body.head_sha,
        str(body.knowledge_revision),
        body.input_hash,
    )
    findings = tuple(
        Finding(
            id=_stable_id("finding", alignment_id, str(index)),
            analysis_id=alignment_id,
            finding_type=finding.finding_type,
            target_node_id=(
                _stable_id(
                    "knowledge-node",
                    body.repository_id,
                    finding.target_node_logical_key,
                )
                if finding.target_node_logical_key is not None
                else None
            ),
            target_node_type=finding.target_node_type,
            target_node_status=finding.target_node_status,
            contradicts=finding.contradicts,
            uncertain=finding.uncertain,
            explanation=finding.explanation,
            recommended_action=finding.recommended_action,
            evidence=tuple(
                verified_evidence[
                    (
                        evidence.source_version_id,
                        str(evidence.url),
                        evidence.exact_quote,
                        evidence.role.value,
                    )
                ]
                for evidence in finding.evidence
            ),
        )
        for index, finding in enumerate(body.analysis.findings)
    )
    now = datetime.now(UTC)
    run = AiRun(
        id=_stable_id("ai-run", job_id, body.prompt_version, body.input_hash),
        job_id=job_id,
        provider=body.provider,
        requested_model=body.requested_model,
        actual_model=body.actual_model,
        prompt_version=body.prompt_version,
        input_hash=body.input_hash,
        output_json=body.analysis.model_dump(mode="json"),
        validation_status=ValidationStatus.VALID,
        usage=body.usage,
        cost=body.cost,
        created_at=now,
        completed_at=now,
    )
    outcome = determine_alignment_outcome(
        findings,
        context_is_sufficient=body.context_is_sufficient,
    )
    if outcome is not body.analysis.outcome:
        raise ApiError(
            status_code=422,
            code="invalid_result_outcome",
            message="Worker result outcome does not match validated findings",
        )
    alignment = Alignment(
        id=alignment_id,
        repository_id=body.repository_id,
        pr_number=body.pr_number,
        head_sha=body.head_sha,
        knowledge_revision=body.knowledge_revision,
        outcome=outcome,
        findings=findings,
        ai_run_id=run.id,
        created_at=now,
    )
    persisted = await repository.persist_worker_result(
        job_id,
        run,
        alignment,
        expected_head_sha=body.head_sha,
        expected_main_sha=body.main_sha,
    )
    return await _store_operation(
        request,
        internal,
        container,
        200,
        _alignment_payload(persisted),
    )


async def _require_repository_access(
    repository_id: str,
    user: UserContext,
    container: AppContainer,
    *,
    write: bool = False,
) -> tuple[RepositoryRecord, MembershipRecord]:
    repository = container.require_repository()
    record = await repository.get_repository_record(repository_id)
    membership = await repository.get_membership(repository_id, user.profile_id)
    if record is None or membership is None:
        raise ApiError(
            status_code=403,
            code="repository_membership_required",
            message="Repository membership is required",
        )
    if write and membership.github_permission not in _WRITE_PERMISSIONS:
        raise ApiError(
            status_code=403,
            code="repository_write_required",
            message="Repository write permission is required",
        )
    return record, membership


async def _replayed_operation(
    request: Request,
    internal: InternalRequestContext,
    container: AppContainer,
) -> JSONResponse | None:
    if internal.idempotency_key is None:
        return None
    operation = await container.idempotency.get(
        f"{request.method}:{request.url.path}",
        internal.idempotency_key,
        internal.body_digest,
    )
    if operation is None:
        return None
    return JSONResponse(status_code=operation.status_code, content=operation.payload)


async def _store_operation(
    request: Request,
    internal: InternalRequestContext,
    container: AppContainer,
    status_code: int,
    payload: dict[str, Any],
) -> JSONResponse:
    if internal.idempotency_key is not None:
        await container.idempotency.store(
            f"{request.method}:{request.url.path}",
            internal.idempotency_key,
            StoredOperation(
                body_digest=internal.body_digest,
                status_code=status_code,
                payload=payload,
            ),
        )
    return JSONResponse(status_code=status_code, content=payload)


def _repository_payload(repository: RepositoryRecord) -> dict[str, object]:
    return {
        "id": repository.id,
        "githubRepositoryId": repository.github_repository_id,
        "githubInstallationId": repository.github_installation_id,
        "owner": repository.owner,
        "name": repository.name,
        "fullName": repository.full_name,
        "defaultBranch": repository.default_branch,
        "baselineCommitSha": repository.baseline_commit_sha,
        "mainCommitSha": repository.main_commit_sha,
        "knowledgeRevision": repository.knowledge_revision,
    }


def _job_payload(job: Job) -> dict[str, object]:
    return {
        "jobId": job.id,
        "repositoryId": job.repository_id,
        "eventType": job.job_type.value,
        "eventKey": job.event_key,
        "status": job.status.value,
        "progress": job.progress,
        "headSha": job.head_sha,
        "errorCode": job.error_code,
        "createdAt": job.created_at.isoformat(),
        "updatedAt": job.updated_at.isoformat(),
        "completedAt": job.completed_at.isoformat() if job.completed_at else None,
    }


def _alignment_summary(alignment: Alignment) -> dict[str, object]:
    return {
        "id": alignment.id,
        "prNumber": alignment.pr_number,
        "headSha": alignment.head_sha,
        "outcome": alignment.outcome.value,
        "findingCount": len(alignment.findings),
        "createdAt": alignment.created_at.isoformat(),
    }


def _alignment_payload(alignment: Alignment) -> dict[str, object]:
    return {
        **_alignment_summary(alignment),
        "repositoryId": alignment.repository_id,
        "knowledgeRevision": alignment.knowledge_revision,
        "aiRunId": alignment.ai_run_id,
        "findings": [
            {
                "id": finding.id,
                "findingType": finding.finding_type.value,
                "targetNodeId": finding.target_node_id,
                "targetNodeType": (
                    finding.target_node_type.value if finding.target_node_type else None
                ),
                "targetNodeStatus": (
                    finding.target_node_status.value if finding.target_node_status else None
                ),
                "contradicts": finding.contradicts,
                "uncertain": finding.uncertain,
                "explanation": finding.explanation,
                "recommendedAction": finding.recommended_action,
                "evidence": [_evidence_payload(item) for item in finding.evidence],
            }
            for finding in alignment.findings
        ],
    }


def _evidence_payload(evidence: EvidenceReference) -> dict[str, object]:
    return {
        "sourceVersionId": evidence.source_version_id,
        "url": evidence.url,
        "exactQuote": evidence.exact_quote,
        "role": evidence.role.value,
        "verified": evidence.verified,
    }


def _knowledge_version_payload(version: KnowledgeNodeVersion) -> dict[str, object]:
    return {
        "id": version.id,
        "nodeId": version.node_id,
        "revision": version.revision,
        "title": version.title,
        "summary": version.summary,
        "status": version.status.value,
        "evidence": [_evidence_payload(item) for item in version.evidence],
    }


def _handshake_payload(handshake: Handshake) -> dict[str, object]:
    return {
        "id": handshake.id,
        "analysisId": handshake.analysis_id,
        "profileId": handshake.profile_id,
        "response": handshake.response.value,
        "message": handshake.message,
        "sourceLanguage": handshake.source_language,
        "createdAt": handshake.created_at.isoformat(),
    }


def _override_payload(override: Override) -> dict[str, object]:
    return {
        "id": override.id,
        "targetType": override.target_type,
        "targetId": override.target_id,
        "overrideType": override.override_type.value,
        "reason": override.reason,
        "actorProfileId": override.actor_profile_id,
        "createdNodeVersionId": override.created_node_version_id,
        "createdAt": override.created_at.isoformat(),
    }


def _passport_payload(passport: ContextPassport) -> dict[str, object]:
    return {
        "id": passport.id,
        "analysisId": passport.analysis_id,
        "profileId": passport.profile_id,
        "language": passport.language,
        "content": passport.content,
        "sourceVersionIds": list(passport.source_version_ids),
        "ambiguities": list(passport.ambiguities),
        "aiRunId": passport.ai_run_id,
        "createdAt": passport.created_at.isoformat(),
    }


def _stable_id(kind: str, *parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, ":".join(("alignment-memory", kind, *parts))))


def _not_found(resource: str) -> ApiError:
    return ApiError(
        status_code=404,
        code=f"{resource}_not_found",
        message=f"{resource.replace('_', ' ').title()} was not found",
    )
