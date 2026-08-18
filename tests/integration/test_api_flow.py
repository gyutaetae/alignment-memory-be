import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from alignment_memory.adapters.github import FixtureGitHubAdapter
from alignment_memory.interfaces.api.dependencies import (
    FIXTURE_ALIGNMENT_ID,
    FIXTURE_HEAD_SHA,
    FIXTURE_MAIN_SHA,
    FIXTURE_OUTSIDER_ID,
    FIXTURE_PROFILE_ID,
    FIXTURE_READER_ID,
    FIXTURE_REPOSITORY_ID,
    FIXTURE_SOURCE_VERSION_ID,
)
from alignment_memory.interfaces.api.main import create_app
from alignment_memory.interfaces.api.security import TEST_USER_HEADER
from alignment_memory.settings import Settings

HMAC_SECRET = "integration-hmac-secret"


def _app():
    return create_app(
        Settings(
            app_mode="fixture",
            environment="test",
            fixture_test_auth_enabled=True,
            internal_hmac_secret=HMAC_SECRET,
            _env_file=None,
        )
    )


def _user(profile_id: str = FIXTURE_PROFILE_ID) -> dict[str, str]:
    return {TEST_USER_HEADER: profile_id}


def _encoded(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


def _signed(body: bytes, key: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    digest = hashlib.sha256(body).hexdigest()
    signature = hmac.new(
        HMAC_SECRET.encode(),
        f"{timestamp}.{digest}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Alignment-Timestamp": timestamp,
        "X-Alignment-Signature": signature,
        "Idempotency-Key": key,
    }


def _internal_post(
    client: TestClient,
    path: str,
    payload: dict[str, object],
    key: str,
):
    body = _encoded(payload)
    return client.post(path, content=body, headers=_signed(body, key))


def test_membership_and_initial_sync_are_enforced_and_idempotent() -> None:
    app = _app()

    with TestClient(app) as client:
        callback = client.get(
            "/api/v1/github/installations/callback?installation_id=99",
            headers=_user(),
        )
        forbidden = client.get(
            f"/api/v1/repositories/{FIXTURE_REPOSITORY_ID}/dashboard",
            headers=_user(FIXTURE_OUTSIDER_ID),
        )
        reader_sync = client.post(
            f"/api/v1/repositories/{FIXTURE_REPOSITORY_ID}/sync",
            headers=_user(FIXTURE_READER_ID),
        )
        first = client.post(
            f"/api/v1/repositories/{FIXTURE_REPOSITORY_ID}/sync",
            headers=_user(),
        )
        second = client.post(
            f"/api/v1/repositories/{FIXTURE_REPOSITORY_ID}/sync",
            headers=_user(),
        )
        github = app.state.container.github

    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "repository_membership_required"
    assert reader_sync.status_code == 403
    assert reader_sync.json()["error"]["code"] == "repository_write_required"
    assert first.status_code == second.status_code == 202
    assert first.json()["jobId"] == second.json()["jobId"]
    assert callback.status_code == 200
    assert callback.json()["repositories"][0]["id"] == FIXTURE_REPOSITORY_ID
    assert isinstance(github, FixtureGitHubAdapter)
    assert len(github.dispatch_calls) == 1


def test_internal_job_transition_is_visible_to_user_polling() -> None:
    with TestClient(_app()) as client:
        created = _internal_post(
            client,
            "/api/v1/internal/jobs",
            {
                "repositoryId": FIXTURE_REPOSITORY_ID,
                "eventKey": "poll-transition",
                "eventType": "pr_analysis",
                "headSha": "d" * 40,
            },
            "poll-create",
        )
        job_id = created.json()["jobId"]
        transitioned = _internal_post(
            client,
            f"/api/v1/internal/jobs/{job_id}/events",
            {"expectedStatus": "queued", "nextStatus": "fetching"},
            "poll-event",
        )
        replay = _internal_post(
            client,
            f"/api/v1/internal/jobs/{job_id}/events",
            {"expectedStatus": "queued", "nextStatus": "fetching"},
            "poll-event",
        )
        polled = client.get(f"/api/v1/jobs/{job_id}", headers=_user())

    assert created.status_code == 201
    assert transitioned.status_code == replay.status_code == 200
    assert transitioned.json() == replay.json()
    assert polled.status_code == 200
    assert polled.json()["status"] == "fetching"
    assert polled.json()["progress"] == 15


def test_dashboard_graph_alignment_passport_handshake_and_override_flow() -> None:
    with TestClient(_app()) as client:
        dashboard = client.get(
            f"/api/v1/repositories/{FIXTURE_REPOSITORY_ID}/dashboard",
            headers=_user(),
        )
        graph = client.get(
            f"/api/v1/repositories/{FIXTURE_REPOSITORY_ID}/graph",
            headers=_user(),
        )
        detail_before = client.get(
            f"/api/v1/alignments/{FIXTURE_ALIGNMENT_ID}",
            headers=_user(),
        )
        passport = client.get(
            f"/api/v1/alignments/{FIXTURE_ALIGNMENT_ID}/context-passport",
            headers=_user(),
        )
        generated = client.post(
            f"/api/v1/alignments/{FIXTURE_ALIGNMENT_ID}/context-passport/generate",
            json={"language": "ko"},
            headers=_user(),
        )
        handshake = client.post(
            f"/api/v1/alignments/{FIXTURE_ALIGNMENT_ID}/handshakes",
            json={
                "response": "needs_clarification",
                "message": "Please show the original decision.",
                "sourceLanguage": "en",
            },
            headers=_user(),
        )
        reader_override = client.post(
            f"/api/v1/alignments/{FIXTURE_ALIGNMENT_ID}/overrides",
            json={
                "overrideType": "false_positive",
                "reason": "The PR only updates documentation.",
            },
            headers=_user(FIXTURE_READER_ID),
        )
        override = client.post(
            f"/api/v1/alignments/{FIXTURE_ALIGNMENT_ID}/overrides",
            json={
                "overrideType": "false_positive",
                "reason": "The PR only updates documentation.",
            },
            headers=_user(),
        )
        detail_after = client.get(
            f"/api/v1/alignments/{FIXTURE_ALIGNMENT_ID}",
            headers=_user(),
        )

    assert dashboard.status_code == 200
    assert dashboard.json()["summary"]["knowledgeNodeCount"] == 2
    assert graph.status_code == 200
    assert len(graph.json()["nodes"]) == 2
    assert len(graph.json()["edges"]) == 1
    assert detail_before.json()["outcome"] == "direct_conflict"
    assert detail_before.json()["findings"][0]["evidence"][0]["verified"] is True
    assert passport.status_code == 200
    assert passport.json()["language"] == "en"
    assert generated.status_code == 201
    assert generated.json()["language"] == "ko"
    assert handshake.status_code == 201
    assert reader_override.status_code == 403
    assert override.status_code == 201
    assert detail_after.json()["findings"] == detail_before.json()["findings"]
    assert len(detail_after.json()["handshakes"]) == 1
    assert len(detail_after.json()["overrides"]) == 1


def test_validated_internal_result_persists_and_stale_head_is_rejected() -> None:
    with TestClient(_app()) as client:
        created = _internal_post(
            client,
            "/api/v1/internal/jobs",
            {
                "repositoryId": FIXTURE_REPOSITORY_ID,
                "eventKey": "result-flow",
                "eventType": "pr_analysis",
                "headSha": "d" * 40,
            },
            "result-create",
        )
        job_id = created.json()["jobId"]
        base_result: dict[str, object] = {
            "repositoryId": FIXTURE_REPOSITORY_ID,
            "prNumber": 8,
            "headSha": "e" * 40,
            "mainSha": FIXTURE_MAIN_SHA,
            "knowledgeRevision": 1,
            "provider": "fixture",
            "requestedModel": "fixture-primary",
            "actualModel": "fixture-model",
            "promptVersion": "v1",
            "inputHash": "result-input",
            "usage": {"total_tokens": 0},
            "analysis": {
                "outcome": "aligned",
                "nodes": [],
                "findings": [],
                "edges": [],
            },
        }
        invalid_outcome_result = {**base_result}
        invalid_outcome_result["headSha"] = "d" * 40
        invalid_outcome_result["analysis"] = {
            "outcome": "missing_alignment",
            "nodes": [],
            "findings": [],
            "edges": [],
        }
        invalid_outcome = _internal_post(
            client,
            f"/api/v1/internal/jobs/{job_id}/result",
            invalid_outcome_result,
            "result-invalid-outcome",
        )
        fabricated_evidence_result = {**base_result}
        fabricated_evidence_result["headSha"] = "d" * 40
        fabricated_evidence_result["analysis"] = {
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
                    "explanation": "Conflicts with the MVP boundary.",
                    "recommended_action": "Keep the extension out of scope.",
                    "evidence": [
                        {
                            "source_version_id": FIXTURE_SOURCE_VERSION_ID,
                            "url": (
                                "https://github.com/fixture-owner/"
                                "alignment-memory-demo/blob/main/docs/adr.md"
                            ),
                            "exact_quote": "This quote was never in the source.",
                            "role": "contradicts",
                        }
                    ],
                }
            ],
            "edges": [],
        }
        fabricated_evidence = _internal_post(
            client,
            f"/api/v1/internal/jobs/{job_id}/result",
            fabricated_evidence_result,
            "result-fabricated-evidence",
        )
        stale = _internal_post(
            client,
            f"/api/v1/internal/jobs/{job_id}/result",
            base_result,
            "result-stale",
        )
        base_result["headSha"] = "d" * 40
        base_result["mainSha"] = "f" * 40
        stale_main = _internal_post(
            client,
            f"/api/v1/internal/jobs/{job_id}/result",
            base_result,
            "result-stale-main",
        )
        base_result["mainSha"] = FIXTURE_MAIN_SHA
        persisted = _internal_post(
            client,
            f"/api/v1/internal/jobs/{job_id}/result",
            base_result,
            "result-valid",
        )
        idempotent_retry = _internal_post(
            client,
            f"/api/v1/internal/jobs/{job_id}/result",
            base_result,
            "result-valid-after-restart",
        )
        detail = client.get(
            f"/api/v1/alignments/{persisted.json()['id']}",
            headers=_user(),
        )

    assert invalid_outcome.status_code == 422
    assert invalid_outcome.json()["error"]["code"] == "invalid_result_outcome"
    assert fabricated_evidence.status_code == 422
    assert fabricated_evidence.json()["error"]["code"] == "invalid_result_evidence"
    assert stale.status_code == stale_main.status_code == 409
    assert stale.json()["error"]["code"] == "stale_repository_state"
    assert stale_main.json()["error"]["code"] == "stale_repository_state"
    assert persisted.status_code == 200
    assert idempotent_retry.status_code == 200
    assert idempotent_retry.json() == persisted.json()
    assert persisted.json()["outcome"] == "aligned"
    assert detail.status_code == 200
    assert detail.json()["headSha"] != FIXTURE_HEAD_SHA
