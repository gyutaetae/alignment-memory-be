from datetime import UTC, datetime, timedelta

import pytest

from alignment_memory.domain import (
    InvalidStateTransition,
    Job,
    JobStatus,
    JobType,
    transition_job,
)


def _queued_job() -> Job:
    now = datetime(2026, 8, 1, tzinfo=UTC)
    return Job(
        id="job-1",
        repository_id="repo-1",
        event_key="pull_request:42:abc123",
        job_type=JobType.PR_ANALYSIS,
        status=JobStatus.QUEUED,
        progress=0,
        created_at=now,
        updated_at=now,
        head_sha="abc123",
    )


def test_job_follows_explicit_forward_state_machine() -> None:
    job = _queued_job()
    statuses = (
        JobStatus.FETCHING,
        JobStatus.ANALYZING,
        JobStatus.VALIDATING,
        JobStatus.PERSISTING,
        JobStatus.WRITING_GITHUB,
        JobStatus.COMPLETED,
    )

    for offset, status in enumerate(statuses, start=1):
        job = transition_job(
            job,
            status,
            occurred_at=job.created_at + timedelta(minutes=offset),
        )

    assert job.status is JobStatus.COMPLETED
    assert job.progress == 100
    assert job.completed_at == job.updated_at


def test_job_cannot_skip_or_leave_terminal_state() -> None:
    job = _queued_job()

    with pytest.raises(InvalidStateTransition):
        transition_job(job, JobStatus.ANALYZING, occurred_at=job.created_at)

    failed = transition_job(
        job,
        JobStatus.FAILED,
        occurred_at=job.created_at,
        error_code="provider_unavailable",
    )
    assert failed.progress == job.progress
    with pytest.raises(InvalidStateTransition):
        transition_job(failed, JobStatus.QUEUED, occurred_at=failed.updated_at)


def test_failed_transition_requires_error_code() -> None:
    job = _queued_job()

    with pytest.raises(InvalidStateTransition, match="error_code"):
        transition_job(job, JobStatus.FAILED, occurred_at=job.created_at)
