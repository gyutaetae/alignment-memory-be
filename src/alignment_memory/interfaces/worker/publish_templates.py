from __future__ import annotations

from pathlib import Path

from alignment_memory.contracts import AnalysisEvidence, AnalysisFinding, AnalysisNode
from alignment_memory.domain import AlignmentOutcome, NodeType
from alignment_memory.interfaces.worker.result_schema import ValidatedAnalysisArtifact

GENERATED_RELATIVE_PATH = Path("knowledge/generated/project-memory.md")
CHECK_NAME = "Alignment Memory"

_OUTCOME_LABELS = {
    AlignmentOutcome.ALIGNED: "Aligned",
    AlignmentOutcome.MISSING_ALIGNMENT: "Missing Alignment",
    AlignmentOutcome.DIRECT_CONFLICT: "Direct Conflict",
}
_NODE_SECTION_LABELS = {
    NodeType.GOAL: "Goals",
    NodeType.REQUIREMENT: "Requirements",
    NodeType.DECISION: "Decisions",
    NodeType.TASK: "Tasks",
    NodeType.ARTIFACT: "Artifacts",
    NodeType.RISK: "Risks",
}


def comment_marker(artifact: ValidatedAnalysisArtifact) -> str:
    if artifact.event.pr_number is None:
        raise ValueError("PR comment marker requires a pull request artifact")
    return (
        f"<!-- alignment-memory:pr:{artifact.event.repository_full_name}:"
        f"{artifact.event.pr_number} -->"
    )


def render_pr_comment(artifact: ValidatedAnalysisArtifact) -> str:
    if artifact.event.publication_kind != "pr_comment":
        raise ValueError("only pull request artifacts can render a PR comment")
    outcome = _OUTCOME_LABELS[artifact.analysis.outcome]
    findings = _sorted_findings(artifact.analysis.findings)
    evidence = _sorted_evidence(
        item for finding in findings for item in finding.evidence
    )

    existing = _existing_agreement(findings, evidence)
    reasons = (
        "\n".join(f"- {_markdown_text(finding.explanation)}" for finding in findings)
        if findings
        else "- No supported conflict was found in the validated project context."
    )
    next_actions = (
        "\n".join(
            f"- {_markdown_text(finding.recommended_action)}" for finding in findings
        )
        if findings
        else "- Continue review and merge when the normal project checks pass."
    )
    evidence_block = _comment_evidence(evidence)
    return "\n".join(
        (
            comment_marker(artifact),
            f"<!-- alignment-memory:head:{artifact.event.head_sha} -->",
            f"## Alignment Memory — {outcome}",
            "",
            f"**Textual outcome:** {outcome}",
            "",
            "### Existing agreement",
            existing,
            "",
            "### Proposed change",
            _markdown_text(artifact.event.proposed_change),
            "",
            "### Exact quote + source URL",
            evidence_block,
            "",
            "### Reason",
            reasons,
            "",
            "### Next action",
            next_actions,
            "",
            f"Analyzed head SHA: `{artifact.event.head_sha}`",
        )
    ).rstrip() + "\n"


def render_check_summary(artifact: ValidatedAnalysisArtifact) -> str:
    if artifact.event.publication_kind != "pr_comment":
        raise ValueError("only pull request artifacts can render a check summary")
    outcome = _OUTCOME_LABELS[artifact.analysis.outcome]
    findings = _sorted_findings(artifact.analysis.findings)
    evidence = _sorted_evidence(
        item for finding in findings for item in finding.evidence
    )
    evidence_line = (
        f'"{_markdown_text(evidence[0].exact_quote)}" — {evidence[0].url}'
        if evidence
        else "No contradictory exact quote was required for this outcome."
    )
    reason = (
        _markdown_text(findings[0].explanation)
        if findings
        else "No supported conflict was found in the validated project context."
    )
    next_action = (
        _markdown_text(findings[0].recommended_action)
        if findings
        else "Continue normal review."
    )
    return "\n".join(
        (
            f"## {outcome}",
            "",
            f"- Existing agreement: {_existing_agreement(findings, evidence)}",
            f"- Proposed change: {_markdown_text(artifact.event.proposed_change)}",
            f"- Exact quote + source URL: {evidence_line}",
            f"- Reason: {reason}",
            f"- Next action: {next_action}",
            f"- Head SHA: `{artifact.event.head_sha}`",
        )
    )


def check_conclusion(artifact: ValidatedAnalysisArtifact) -> str:
    return "failure" if artifact.analysis.outcome is AlignmentOutcome.DIRECT_CONFLICT else "success"


def render_generated_wiki(artifact: ValidatedAnalysisArtifact) -> str:
    if artifact.event.publication_kind != "generated_wiki":
        raise ValueError("only repository artifacts can render generated knowledge")
    nodes = sorted(
        artifact.analysis.nodes,
        key=lambda item: (item.node_type.value, item.logical_key, item.title),
    )
    indexed_nodes = {node.logical_key: node for node in nodes}
    lines = [
        "# Generated Project Memory",
        "",
        "> Generated deterministically from validated evidence. "
        "Edit source records, not this file.",
        "",
        f"- Repository: `{_markdown_text(artifact.event.repository_full_name)}`",
        f"- Knowledge revision: `{artifact.knowledge_revision + 1}`",
        f"- Source head: `{artifact.event.head_sha}`",
        "",
        "## Contents",
        "",
    ]
    if nodes:
        lines.extend(
            f"- {_wikilink(node)}"
            for node in nodes
        )
    else:
        lines.append("- No validated knowledge nodes were produced.")

    for node_type in NodeType:
        typed_nodes = [node for node in nodes if node.node_type is node_type]
        if not typed_nodes:
            continue
        lines.extend(("", f"## {_NODE_SECTION_LABELS[node_type]}", ""))
        for node in typed_nodes:
            lines.extend(_render_node(node))

    edges = sorted(
        artifact.analysis.edges,
        key=lambda item: (
            item.from_node_logical_key,
            item.relation_type,
            item.to_node_logical_key,
        ),
    )
    if edges:
        lines.extend(("", "## Relations", ""))
        for edge in edges:
            from_node = indexed_nodes.get(edge.from_node_logical_key)
            to_node = indexed_nodes.get(edge.to_node_logical_key)
            from_link = _wikilink(from_node) if from_node else _markdown_text(
                edge.from_node_logical_key
            )
            to_link = _wikilink(to_node) if to_node else _markdown_text(
                edge.to_node_logical_key
            )
            lines.append(
                f"- {from_link} — {_markdown_text(edge.relation_type)} → {to_link}"
            )
    return "\n".join(lines).rstrip() + "\n"


def resolve_generated_path(
    repository_root: Path,
    requested_path: str | Path = GENERATED_RELATIVE_PATH,
) -> Path:
    relative = Path(requested_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("generated path must not be absolute or traverse directories")
    if relative.as_posix() != GENERATED_RELATIVE_PATH.as_posix():
        raise ValueError("generated filename is not an approved deterministic target")
    root = repository_root.resolve()
    generated_root = (root / "knowledge" / "generated").resolve()
    target = (root / relative).resolve()
    if target.parent != generated_root:
        raise ValueError("generated path must stay under knowledge/generated")
    return target


def _render_node(node: AnalysisNode) -> list[str]:
    lines = [
        f"### {_heading_text(node.logical_key)}",
        "",
        f"- Title: {_markdown_text(node.title)}",
        f"- Type: `{node.node_type.value}`",
        f"- Status: `{node.status.value}`",
        f"- Summary: {_markdown_text(node.summary)}",
        "- Evidence:",
    ]
    for evidence in _sorted_evidence(node.evidence):
        lines.append(
            f"  - [{_markdown_text(evidence.exact_quote)}]({evidence.url}) "
            f"(`{evidence.source_version_id}`)"
        )
    lines.append("")
    return lines


def _wikilink(node: AnalysisNode) -> str:
    key = _wikilink_text(node.logical_key)
    title = _wikilink_text(node.title)
    return f"[[project-memory#{key}|{title}]]"


def _existing_agreement(
    findings: tuple[AnalysisFinding, ...],
    evidence: tuple[AnalysisEvidence, ...],
) -> str:
    logical_keys = sorted(
        {
            finding.target_node_logical_key
            for finding in findings
            if finding.target_node_logical_key is not None
        }
    )
    if logical_keys:
        return ", ".join(f"`{_markdown_text(key)}`" for key in logical_keys)
    if evidence:
        return _markdown_text(evidence[0].exact_quote)
    return "No conflicting active agreement was identified."


def _comment_evidence(evidence: tuple[AnalysisEvidence, ...]) -> str:
    if not evidence:
        return "No contradictory exact quote or source URL was required for this outcome."
    blocks: list[str] = []
    for item in evidence:
        quote = _markdown_text(item.exact_quote).replace("\n", "\n> ")
        blocks.extend((f"> {quote}", f"> Source: {item.url}", ""))
    return "\n".join(blocks).rstrip()


def _sorted_findings(
    findings: tuple[AnalysisFinding, ...],
) -> tuple[AnalysisFinding, ...]:
    return tuple(
        sorted(
            findings,
            key=lambda item: (
                item.finding_type.value,
                item.target_node_logical_key or "",
                item.explanation,
            ),
        )
    )


def _sorted_evidence(evidence: object) -> tuple[AnalysisEvidence, ...]:
    items = tuple(item for item in evidence if isinstance(item, AnalysisEvidence))
    unique = {
        (item.source_version_id, str(item.url), item.exact_quote, item.role.value): item
        for item in items
    }
    return tuple(sorted(unique.values(), key=lambda item: tuple(map(str, (
        item.url,
        item.source_version_id,
        item.exact_quote,
        item.role.value,
    )))))


def _markdown_text(value: str) -> str:
    return (
        value.replace("\x00", "")
        .replace("<!--", "&lt;!--")
        .replace("-->", "--&gt;")
        .strip()
    )


def _heading_text(value: str) -> str:
    return _markdown_text(value).replace("#", "\\#")


def _wikilink_text(value: str) -> str:
    return _markdown_text(value).replace("|", "-").replace("]]", "] ]")
