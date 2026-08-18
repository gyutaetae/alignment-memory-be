from datetime import UTC, datetime

import pytest

from alignment_memory.domain import (
    EvidenceReference,
    EvidenceRole,
    EvidenceValidationError,
    Source,
    SourceVersion,
    exact_quote_is_present,
    normalize_stored_body,
    verify_evidence_reference,
)


def _source_evidence() -> tuple[Source, SourceVersion, EvidenceReference]:
    source = Source(
        id="source-1",
        repository_id="repo-1",
        source_type="markdown",
        external_id="docs/prd.md",
        url="https://github.com/gyutaetae/harness/blob/main/docs/prd.md",
    )
    source_version = SourceVersion(
        id="source-version-1",
        source_id=source.id,
        external_version="abc123",
        content="Decision\r\nDo not build a browser extension.\r\n",
        content_hash="hash-1",
        occurred_at=datetime(2026, 8, 1, tzinfo=UTC),
        ingested_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    evidence = EvidenceReference(
        source_version_id=source_version.id,
        url=source.url,
        exact_quote="Do not build a browser extension.",
        role=EvidenceRole.SUPPORTS,
    )
    return source, source_version, evidence


def test_exact_quote_must_exist_in_normalized_stored_body() -> None:
    source, source_version, evidence = _source_evidence()

    verified = verify_evidence_reference(evidence, source, source_version)

    assert verified.verified is True
    assert normalize_stored_body(source_version.content).count("\r") == 0


def test_fabricated_quote_is_rejected() -> None:
    source, source_version, evidence = _source_evidence()
    fabricated = EvidenceReference(
        source_version_id=evidence.source_version_id,
        url=evidence.url,
        exact_quote="The team approved a browser extension.",
        role=EvidenceRole.SUPPORTS,
    )

    with pytest.raises(EvidenceValidationError, match="not present"):
        verify_evidence_reference(fabricated, source, source_version)

    assert exact_quote_is_present(fabricated.exact_quote, source_version.content) is False


def test_evidence_source_identity_and_url_are_exact() -> None:
    source, source_version, evidence = _source_evidence()
    wrong_url = EvidenceReference(
        source_version_id=evidence.source_version_id,
        url="https://github.com/gyutaetae/harness/issues/1",
        exact_quote=evidence.exact_quote,
        role=EvidenceRole.SUPPORTS,
    )

    with pytest.raises(EvidenceValidationError, match="URL"):
        verify_evidence_reference(wrong_url, source, source_version)
