from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_ROOT = REPOSITORY_ROOT / ".github" / "workflows"
ANALYZE_PATH = WORKFLOW_ROOT / "alignment-analyze.yml"
PUBLISH_PATH = WORKFLOW_ROOT / "alignment-publish.yml"


def _workflow(path: Path) -> dict[str, object]:
    payload = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(payload, dict)
    return payload


def test_workflows_never_use_target_event_or_execute_pr_head() -> None:
    combined = ANALYZE_PATH.read_text() + PUBLISH_PATH.read_text()
    analyze = _workflow(ANALYZE_PATH)
    jobs = analyze["jobs"]
    assert isinstance(jobs, dict)
    steps = jobs["analyze"]["steps"]
    checkout = next(step for step in steps if step.get("uses") == "actions/checkout@v4")

    assert "pull_request_target" not in combined
    assert "pull_request.head.sha" not in combined
    assert "pull_request.base.sha" in checkout["with"]["ref"]
    assert "author_association" in jobs["analyze"]["if"]
    assert "head.repo.full_name == github.repository" in jobs["analyze"]["if"]


def test_analyze_has_read_only_permissions_and_analysis_secrets() -> None:
    analyze = _workflow(ANALYZE_PATH)
    job = analyze["jobs"]["analyze"]
    permissions = job["permissions"]
    source = ANALYZE_PATH.read_text(encoding="utf-8")

    assert permissions
    assert set(permissions.values()) == {"read"}
    assert "OPENROUTER_API_KEY" in source
    assert "INTERNAL_HMAC_SECRET" in source
    assert "actions/upload-artifact@v4" in source
    assert job["runs-on"] == "ubuntu-latest"


def test_publish_has_only_required_write_boundary_and_no_model_secret() -> None:
    publish = _workflow(PUBLISH_PATH)
    job = publish["jobs"]["publish"]
    permissions = job["permissions"]
    source = PUBLISH_PATH.read_text(encoding="utf-8")

    assert permissions == {
        "actions": "read",
        "checks": "write",
        "contents": "write",
        "issues": "write",
        "pull-requests": "write",
    }
    assert "OPENROUTER_API_KEY" not in source
    assert "actions/download-artifact@v4" in source
    assert "github.event.repository.default_branch" in source
    assert "head_repository.full_name == github.repository" in source


def test_concurrency_cancels_stale_pr_analysis_and_serializes_repository_publish() -> None:
    analyze = _workflow(ANALYZE_PATH)
    publish = _workflow(PUBLISH_PATH)
    publish_source = PUBLISH_PATH.read_text(encoding="utf-8")

    assert analyze["concurrency"]["cancel-in-progress"] == "true"
    assert "pull_request.number" in analyze["concurrency"]["group"]
    assert publish["concurrency"]["cancel-in-progress"] == "false"
    assert "alignment-publish-{0}-repository" in publish["concurrency"]["group"]
    assert "Revalidate main SHA before generated write" in publish_source
    assert "git push origin" in publish_source
    assert "--force" not in publish_source
