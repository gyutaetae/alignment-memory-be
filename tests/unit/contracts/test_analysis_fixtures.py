import json
from pathlib import Path

from alignment_memory.contracts.analysis import AnalysisResult
from alignment_memory.domain import exact_quote_is_present

_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "analysis"


def test_six_github_fixtures_are_strict_and_source_grounded() -> None:
    fixture_paths = sorted(_FIXTURE_DIR.glob("*.json"))
    assert len(fixture_paths) == 6

    outcomes: list[str] = []
    for fixture_path in fixture_paths:
        fixture = json.loads(fixture_path.read_text())
        assert fixture["repository"]["full_name"] == "gyutaetae/harness"
        assert fixture["pull_request"]["html_url"].startswith(
            "https://github.com/gyutaetae/harness/pull/"
        )

        result = AnalysisResult.model_validate(fixture["analysis"])
        assert result.outcome.value == fixture["expected_outcome"]
        outcomes.append(result.outcome.value)

        sources = {source["id"]: source for source in fixture["source_versions"]}
        evidence_sets = [node.evidence for node in result.nodes]
        evidence_sets.extend(finding.evidence for finding in result.findings)
        evidence_sets.extend(edge.evidence for edge in result.edges)
        for evidence_set in evidence_sets:
            for evidence in evidence_set:
                source = sources[evidence.source_version_id]
                assert str(evidence.url) == source["url"]
                assert exact_quote_is_present(evidence.exact_quote, source["content"])

    assert outcomes.count("aligned") == 3
    assert outcomes.count("direct_conflict") == 3
