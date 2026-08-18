import json

import pytest

from alignment_memory.interfaces.worker.cli import analyze_event
from alignment_memory.interfaces.worker.demo import run_demo


@pytest.mark.asyncio
async def test_fixture_demo_calls_api_and_worker_and_proves_retry_idempotency(tmp_path) -> None:
    await run_demo(tmp_path, analyze_event_runner=analyze_event)

    evaluation = json.loads((tmp_path / "evaluation.json").read_text(encoding="utf-8"))
    proof = json.loads((tmp_path / "vertical-slice.json").read_text(encoding="utf-8"))

    assert evaluation["execution"] == {
        "mode": "fixture",
        "externalServicesCalled": False,
        "provider": "fixture",
        "actualModel": "fixture-model",
        "liveProof": False,
        "disclaimer": (
            "Deterministic local fixtures; this is not GitHub, OpenRouter, "
            "Supabase, or Vercel live proof."
        ),
    }
    assert evaluation["summary"]["fixtureCount"] == 6
    assert evaluation["summary"]["passedCases"] == 6
    assert evaluation["summary"]["passed"] is True
    assert evaluation["correctionImpact"]["beforeOutcome"] == "direct_conflict"
    assert evaluation["correctionImpact"]["afterOutcome"] == "aligned"
    assert evaluation["correctionImpact"]["priorFindingPreserved"] is True

    assert proof["conflict"]["outcome"] == "direct_conflict"
    assert proof["resolution"]["outcome"] == "aligned"
    assert proof["conflict"]["commentMarker"] == proof["resolution"]["commentMarker"]
    assert proof["merge"]["firstApply"] == proof["merge"]["retryApply"]
    assert proof["merge"]["retryApply"]["artifactCount"] == 1
    assert proof["merge"]["retryApply"]["knowledgeRevision"] == 2
    assert proof["passport"]["handshakeCount"] == 2
    assert all(proof["idempotencyAssertions"].values())
    assert proof["passed"] is True

    assert "Fixture-only proof" in (tmp_path / "evaluation.md").read_text(encoding="utf-8")
    assert "Direct Conflict" in (tmp_path / "conflict-comment.md").read_text(
        encoding="utf-8"
    )
    assert "Knowledge revision: `2`" in (tmp_path / "project-memory.md").read_text(
        encoding="utf-8"
    )
