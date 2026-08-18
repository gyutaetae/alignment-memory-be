from copy import deepcopy

import pytest
from pydantic import ValidationError

from alignment_memory.contracts.analysis import AnalysisResult


def _evidence() -> dict[str, str]:
    return {
        "source_version_id": "source-version-decision-1",
        "url": "https://github.com/gyutaetae/harness/blob/main/docs/prd.md",
        "exact_quote": "Browser extensions are excluded from the MVP.",
        "role": "contradicts",
    }


def _direct_conflict_payload() -> dict[str, object]:
    return {
        "outcome": "direct_conflict",
        "nodes": [
            {
                "logical_key": "task:add-browser-extension",
                "node_type": "task",
                "title": "Add browser extension sync",
                "summary": "Add browser extension ingestion to the MVP.",
                "status": "active",
                "evidence": [
                    {
                        "source_version_id": "source-version-pr-42",
                        "url": "https://github.com/gyutaetae/harness/pull/42",
                        "exact_quote": "This PR adds browser extension sync.",
                        "role": "supports",
                    }
                ],
            }
        ],
        "findings": [
            {
                "finding_type": "direct_conflict",
                "target_node_logical_key": "decision:no-browser-extension",
                "target_node_type": "decision",
                "target_node_status": "active",
                "contradicts": True,
                "uncertain": False,
                "explanation": "The proposed extension contradicts the active decision.",
                "recommended_action": "Remove extension sync or supersede the decision.",
                "evidence": [_evidence()],
            }
        ],
        "edges": [
            {
                "from_node_logical_key": "task:add-browser-extension",
                "to_node_logical_key": "decision:no-browser-extension",
                "relation_type": "conflicts_with",
                "evidence": [_evidence()],
            }
        ],
    }


def test_strict_schema_rejects_unknown_and_demographic_fields() -> None:
    payload = _direct_conflict_payload()
    payload["nationality"] = "inferred"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AnalysisResult.model_validate(payload)

    nested_payload = _direct_conflict_payload()
    nested_payload["nodes"][0]["personality"] = "risk-averse"  # type: ignore[index]
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AnalysisResult.model_validate(nested_payload)


def test_every_node_finding_and_edge_requires_evidence() -> None:
    for collection in ("nodes", "findings", "edges"):
        payload = deepcopy(_direct_conflict_payload())
        del payload[collection][0]["evidence"]  # type: ignore[index]

        with pytest.raises(ValidationError):
            AnalysisResult.model_validate(payload)


def test_direct_conflict_contract_enforces_core_active_target() -> None:
    payload = _direct_conflict_payload()
    payload["findings"][0]["target_node_type"] = "task"  # type: ignore[index]

    with pytest.raises(ValidationError, match="active Goal, Requirement, or Decision"):
        AnalysisResult.model_validate(payload)


def test_outcome_and_uncertainty_are_consistent() -> None:
    direct = AnalysisResult.model_validate(_direct_conflict_payload())
    assert direct.outcome.value == "direct_conflict"

    payload = _direct_conflict_payload()
    payload["outcome"] = "aligned"
    with pytest.raises(ValidationError, match="requires Direct Conflict outcome"):
        AnalysisResult.model_validate(payload)

    missing_payload = _direct_conflict_payload()
    missing_payload["outcome"] = "missing_alignment"
    missing_payload["findings"][0].update(  # type: ignore[index, union-attr]
        finding_type="missing_alignment",
        target_node_logical_key=None,
        target_node_type=None,
        target_node_status=None,
        contradicts=False,
        uncertain=True,
    )
    result = AnalysisResult.model_validate(missing_payload)
    assert result.outcome.value == "missing_alignment"


def test_json_schema_forbids_additional_top_level_fields() -> None:
    schema = AnalysisResult.model_json_schema()

    assert schema["title"] == "AnalysisResult"
    assert schema["additionalProperties"] is False
