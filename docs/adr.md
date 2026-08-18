# ADR — Alignment Memory

> Authority: accepted technical/product decisions and their intent. This is a compact decision ledger, not a restatement of requirements.

Status for all entries: `accepted` unless noted.

## ADR-001 — Python over Java for MVP

- **Decision:** Python 3.12 + FastAPI; no Java service in MVP.
- **Intent:** one developer and 19 days favor the user's existing Python strength and AI/document tooling.
- **Trade-off:** less direct Java/Spring signaling for finance roles; compensate with idempotency, auditability, RLS, tests, and explicit architecture. A later bounded service may be reimplemented in Spring.

## ADR-002 — React SPA, not Next.js

- **Decision:** React + TypeScript + Vite on Vercel.
- **Intent:** FastAPI owns server behavior; SSR adds no core product value.
- **Trade-off:** no SSR; acceptable for an authenticated desktop tool.

## ADR-003 — Modular monolith with shared Python core

- **Decision:** API and Action Worker use one `alignment_memory` package and separate entrypoints.
- **Intent:** avoid duplicated extraction, validation, and state rules while preserving future worker extraction.
- **Rejected:** microservices, Celery, broker, and Turborepo for MVP.

## ADR-004 — Supabase plus GitHub App

- **Decision:** Supabase provides Auth/Postgres/RLS; GitHub App owns repository installation and dispatch permissions.
- **Intent:** account-based access and least-privilege repository integration without storing a personal token.
- **Trade-off:** first-time GitHub App setup is added intentionally as portfolio evidence.

## ADR-005 — GitHub Actions is the MVP worker

- **Decision:** Action performs collection and OpenRouter analysis; FastAPI is a short-lived control plane.
- **Intent:** avoid serverless timeouts and produce visible, real collaboration traces.
- **Trade-off:** collaborator/in-repository branches only; move to a queue worker when multi-repository scale requires it.

## ADR-006 — Separate Analyze and Publish jobs

- **Decision:** Analyze has AI secrets and no GitHub write; Publish has narrow GitHub write and no AI secret.
- **Intent:** a model or untrusted input cannot directly turn semantic output into arbitrary repository writes.
- **Constraints:** no PR-head execution, no `pull_request_target`, fixed templates, generated-path allowlist.

## ADR-007 — OpenRouter runtime; Codex is development tooling

- **Decision:** OpenRouter API with configurable fixed primary/fallback models and strict structured output.
- **Intent:** provider flexibility after Gemini proved unreliable for the user; ChatGPT Pro/Codex credentials are not a public application runtime.
- **Trade-off:** free models have low availability/limits; preserve a small-credit contingency and log the actual model.

## ADR-008 — Evidence validation gates AI

- **Decision:** JSON Schema + Pydantic + exact stored-quote verification before persistence or Action verdict.
- **Intent:** source-grounded trust matters more than fluent explanations.
- **Rule:** Direct Conflict requires valid evidence against an active Goal, Requirement, or Decision; uncertainty becomes Missing Alignment, not failure.

## ADR-009 — PostgreSQL graph, no graph/vector infrastructure

- **Decision:** versioned node/edge tables in Supabase Postgres; no Neo4j, pgvector, or GraphRAG.
- **Intent:** one small repository fits direct active-context retrieval and relational constraints.
- **Revisit:** when measured context size or graph queries exceed the vertical slice.

## ADR-010 — Immutable evidence and versioned derived knowledge

- **Decision:** sources, source versions, AI runs, findings, Handshakes, and Overrides are append-only; knowledge has explicit versions and statuses.
- **Intent:** compound memory must include why knowledge changed, not only the current answer.
- **Trade-off:** more tables and joins; justified by auditability and product differentiation.

## ADR-011 — Automatic writes are generated-only

- **Decision:** Initial Sync and merged changes may commit only `knowledge/generated/**` to `main`.
- **Intent:** automatic memory maintenance without allowing AI to alter app code, workflows, raw GitHub text, or human-authored Markdown.
- **Rule:** PR open/update analyzes only; official knowledge changes after merge or explicit Override.

## ADR-012 — Poll job state, do not stream

- **Decision:** job ID plus adaptive HTTP polling; no WebSocket, SSE, or Supabase Realtime dependency.
- **Intent:** a few coarse workflow states do not justify persistent connection complexity.
- **Revisit:** only if measured latency makes polling materially harmful.

## ADR-013 — React Flow + Dagre relevant subgraph

- **Decision:** `@xyflow/react` custom nodes with Dagre layout; default to conflict-related one/two-hop context, not the entire graph.
- **Intent:** explain impact clearly with native React components; avoid force-layout motion and visual noise.
- **Trade-off:** not intended for large-scale graph analytics.

## ADR-014 — Minimal, single-accent UI

- **Decision:** neutral colors plus `#2563EB`; no dark mode, mobile, gradient, or decorative motion in MVP.
- **Intent:** status, evidence, and next action must be understood before visual spectacle.
- **Rule:** use labels/icons/shapes, not color alone; six node types do not receive six colors.

## ADR-015 — Context Passport without demographic inference

- **Decision:** localize using declared language/timezone/communication preference, role, ownership, and cited contribution history.
- **Intent:** cross borders by preserving project intent, not by stereotyping national culture.
- **Rule:** original evidence remains available; AI-derived stakeholder interpretations are labeled and versioned.

## ADR-016 — Human disagreement is first-class evidence

- **Decision:** separate Handshake from Human Override; require Override reason and preserve the prior finding/version.
- **Intent:** prevent AI errors from compounding and make correction history a defensible data moat.

## ADR-017 — Narrow MVP boundary

- **Decision:** one public repository, desktop web, invited collaborators, and three results: Aligned, Missing Alignment, Direct Conflict.
- **Excluded:** browser extension; Slack/Notion/Figma/Agora ingestion; private/multi-repo; external forks; Stale Reference; whole-code ingestion.
- **Intent:** a complete, deployed, evidence-rich vertical slice is the winning portfolio artifact.
