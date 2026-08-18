# PRD — Alignment Memory

> Authority: product intent, MVP scope, success criteria. Runtime sequences belong in [flow.md](./flow.md); implementation choices belong in [adr.md](./adr.md).

## Product thesis

Alignment Memory turns a GitHub repository's recorded history into an evidence-backed project memory. It checks whether new work still supports the team's goals and decisions, intervenes before merge, then updates generated knowledge after merge.

**Not a wiki with AI search:** the core value is a proactive `Alignment Diff` inside the GitHub workflow.

## Problem

Issues, PRs, commits, and Markdown preserve activity but fragment intent. As the team and geography expand:

- people forget why a decision was made;
- locally reasonable changes drift from shared goals;
- translation preserves words but loses project intent;
- asynchronous collaborators wait for missing context;
- manual wikis become stale.

The user must be able to answer, with sources: **Can this change be merged, what does it affect, and who still needs to align?**

## Primary users and initial boundary

- One public GitHub repository: `gyutaetae/alignment-memory`.
- Four-person internal team: developer 1, PM 2, designer 1.
- One invited foreign collaborator for post-MVP validation; use an in-repository branch, not a fork.
- Desktop web only. GitHub remains the work surface; the web app is the memory and review surface.

## Core concepts

AI extracts six node types from allowed sources:

| Type | Meaning |
|---|---|
| Goal | Desired outcome and success condition |
| Requirement | Behavior or constraint needed for a Goal |
| Decision | Chosen direction plus rationale |
| Task | Work to perform |
| Artifact | Produced code, document, design, or release |
| Risk | Threat, uncertainty, or unresolved dependency |

Every node, edge, conflict, and stakeholder claim must point to a preserved GitHub source version. The system knows recorded positions and actions; it must not claim access to unrecorded thoughts.

## MVP capabilities

### 1. Repository memory

- GitHub login and GitHub App installation.
- Initial Sync of existing Markdown, Issues, PR descriptions, PR diffs, and commit messages; do not ingest the whole source tree.
- Store `baselineCommitSha`; later runs analyze only new or changed records.
- Create the six node types, evidence links, generated Wiki Markdown, and a navigable partial graph.
- Idempotent retries: no duplicate source, node, comment, or commit.

### 2. Alignment gate

- On PR open/update, compare the change with active Goal, Requirement, and Decision nodes.
- `Direct Conflict`: valid contradictory evidence exists; post evidence and fail the Action.
- `Missing Alignment`: intent or justification is insufficient; warn without failing.
- `Aligned`: no supported conflict; pass.
- Do not update official project knowledge before merge.
- On merge, update knowledge and commit only `knowledge/generated/**` directly to `main`.

`Stale Reference` and full policy engines are post-MVP.

### 3. Human correction

- Handshake: `agree`, `needs_clarification`, or `disagree`.
- Override: `false_positive`, `supersede_decision`, or `insufficient_evidence`; reason required.
- Preserve the old version and append the correction. Never silently rewrite history.
- Use corrections as evidence in later analyses.

### 4. Borderless handoff

`Context Passport` localizes one shared set of evidence for the receiving stakeholder:

- what changed and why;
- why it affects this stakeholder;
- linked decisions and risks;
- unresolved questions and timezone-aware handoff;
- preferred-language explanation with original text available.

| Boundary | Product response | Claim boundary |
|---|---|---|
| Language | Context-preserving translation and project glossary | Primary validation target |
| Geography | Asynchronous handoff with timezone and required response | Secondary validation target |
| Culture | Self-declared communication preferences and ambiguity checks | Do not infer from nationality |
| Organization | Shared Goal/Decision graph and affected-stakeholder routing | Validate through actual GitHub traces |

## Why AI is indispensable

Rules can match identifiers; they cannot reliably determine whether differently worded work contradicts an earlier rationale. AI is used for semantic extraction, relationship proposal, conflict explanation, localized handoff, and missing-context detection. Deterministic code validates schema, evidence, permissions, and state transitions. AI never receives tools or authority to choose arbitrary files or commands.

## UX contract

- Minimal desktop UI; neutral palette plus one accent `#2563EB`.
- Dashboard prioritizes conflicts and next action, not the graph.
- Hero screen: existing agreement versus proposed change in `Alignment Diff`.
- Context Passport is secondary context; Handshake and Override are separate actions.
- Status is communicated by text and icon, never color alone.
- Graph shows the relevant subgraph first; it is an explanation surface, not the product itself.

See [flow.md](./flow.md) for journeys.

## Non-goals

- Browser extension; Slack, Notion, Figma, or Agora ingestion.
- Private, multi-organization, or multi-repository support.
- Whole-repository code ingestion or execution of PR code.
- Neo4j, pgvector, GraphRAG, message queues, or microservices.
- Obsidian-to-database sync. Obsidian may open generated Markdown only.
- Cultural or personality profiling; nationality-based adaptation.
- External fork automation in MVP.

## Success criteria

1. Initial Sync imports allowed history and a repeated run creates no duplicates.
2. Every AI result has an exact source URL and a quote found in the stored source version.
3. Three aligned fixtures pass and three intentional conflicts are detected.
4. One real conflicting PR fails with a useful comment; one aligned PR passes.
5. A merge updates DB knowledge, graph, and generated Markdown on `main` exactly once.
6. Human Override changes later analysis without deleting prior evidence.
7. Korean PM and English collaborator can use one Context Passport and Handshake to reach a recorded decision.
8. Frontend, backend, Action, and core tests have repeatable commands; the deployed flow works outside a staged mock.

## Demo spine

1. Show existing Decision excluding a browser extension.
2. Open a PR that adds extension sync; Action finds the old Decision, cites it, and fails.
3. Change the PR or supersede the Decision with a reason; rerun becomes Aligned.
4. Merge; generated Wiki and graph update automatically.
5. Switch Context Passport between Korean PM and English developer views; record a Handshake.

## Product principles

1. **Evidence before fluency:** a polished answer without a verified source is not knowledge.
2. **Intervene at work time:** prevent drift before merge, not during a later retrospective.
3. **Accumulate corrections:** disagreement is training evidence, not noise to erase.
4. **Generated-only automation:** AI may change derived knowledge, never human-authored source or application code.
5. **Personalize without stereotyping:** use explicit preferences and contribution history, not demographic inference.
6. **Win by completing the vertical slice:** one repository and one real cross-border trace are stronger than many integrations.
