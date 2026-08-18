# Code Architecture — Alignment Memory

> Authority: module boundaries, dependency direction, runtimes, and repository layout. Data ownership is in [data-schema.md](./data-schema.md); decision rationale is in [adr.md](./adr.md).

## Shape

One repository, one Python modular monolith, two Python entrypoints, and one React SPA. No microservices or message broker.

```text
apps/web                 React control and explanation surface
backend/alignment_memory shared Python domain/application package
  interfaces/api         FastAPI control plane
  interfaces/worker      GitHub Action CLI worker
supabase/migrations      schema and RLS
.github/workflows        trusted analysis and privileged publish jobs
knowledge/generated      only AI-writable repository path
```

## Dependency rule

```text
interfaces → application → domain ← ports ← adapters
```

- `domain` imports no FastAPI, GitHub, OpenRouter, or Supabase code.
- `application` coordinates use cases through ports.
- `adapters` implement ports; external SDK models do not leak into domain types.
- API and Worker share schemas and use cases; they do not duplicate analysis rules.

## Repository layout

```text
apps/
  web/
    src/
      features/{auth,repositories,dashboard,alignment,graph}/
      shared/{api,components,styles,types}/
backend/
  pyproject.toml
  Dockerfile
  src/alignment_memory/
    domain/{entities,enums,errors,policies}/
    application/{commands,queries,services}/
    ports/{github,llm,repositories,clock}/
    adapters/{github,openrouter,supabase}/
    contracts/                 # Pydantic boundary schemas
    interfaces/
      api/{main,dependencies,routes}/
      worker/{cli,event_parser,publish_templates}/
  tests/{unit,integration,fixtures}/
supabase/migrations/
.github/workflows/
knowledge/generated/
docs/
```

## Component responsibilities

### React SPA

- React + TypeScript + Vite; Vercel static deployment.
- TanStack Query owns server state; avoid a second global state library in MVP.
- CSS Modules and CSS variables; one accent `#2563EB`.
- Screens: Connect, Project Memory, Knowledge Graph, Alignment Detail.
- `@xyflow/react` + Dagre renders a relevant subgraph; the server returns domain graph data, not library-specific coordinates.

### FastAPI control plane

- Verify Supabase JWT and repository membership.
- Link GitHub App installations.
- Create jobs, dispatch workflow, serve polling reads.
- Persist signed, validated Worker results transactionally.
- Serve dashboard, graph, Alignment, Handshake, and Override APIs.
- Never perform long OpenRouter work or push Git commits.

### Action Analyze Job

- Run trusted worker code from `main`; never execute PR head code.
- Collect only allowed GitHub material.
- Normalize and content-hash inputs.
- Retrieve active project context.
- Call OpenRouter with fixed schema and configurable model list.
- Validate Pydantic shape, exact evidence quote, allowed node types, and conflict preconditions.
- Send HMAC-signed progress/result to FastAPI.
- Produce a schema-validated artifact for Publish Job; no GitHub write permission.

### Action Publish Job

- Does not receive `OPENROUTER_API_KEY`.
- Renders comments and Markdown from fixed templates; model output cannot select paths.
- Writes only PR comments and `knowledge/generated/**` with explicit permissions.
- Rechecks `main` SHA; never force-pushes.

### Supabase

- Auth, PostgreSQL, migrations, RLS, versioned evidence, and job state.
- No Realtime dependency in MVP; frontend polls job state.

## Core ports

```python
class GitHubPort:
    def fetch_allowed_sources(repo, since_sha): ...
    def fetch_pr_context(repo, number, head_sha): ...
    def dispatch_sync(repo, job_id): ...

class LlmPort:
    def analyze(input, schema, model_policy): ...

class KnowledgeRepository:
    def get_active_context(repo_id, revision): ...
    def persist_validated_result(job_id, result): ...

class JobRepository:
    def create(event_key, payload): ...
    def transition(job_id, expected_state, next_state): ...
```

Port methods return internal types and typed errors. Retries belong at adapter/use-case boundaries; domain policies remain deterministic.

## API surface

User endpoints use Supabase JWT. Internal Action endpoints use timestamped HMAC signatures.

- `GET /api/v1/github/installations/callback`
- `GET /api/v1/repositories`
- `POST /api/v1/repositories/{id}/sync`
- `GET /api/v1/jobs/{jobId}`
- `GET /api/v1/repositories/{id}/dashboard`
- `GET /api/v1/repositories/{id}/graph`
- `GET /api/v1/alignments/{id}`
- `POST /api/v1/alignments/{id}/handshakes`
- `POST /api/v1/alignments/{id}/overrides`
- `POST /api/v1/internal/jobs`
- `GET /api/v1/internal/jobs/{id}/context`
- `POST /api/v1/internal/jobs/{id}/events`
- `POST /api/v1/internal/jobs/{id}/result`

Return errors as `{error: {code, message, retryable, requestId}}`. Do not expose provider payloads or secrets.

## Concurrency and security

- PR analysis concurrency key: repository + PR number; cancel stale head SHA.
- Publish concurrency key: repository; queue writes, then verify current SHA.
- Actor gate: automatic only for owner/member/collaborator.
- Never use `pull_request_target` in MVP.
- Treat all repository text as untrusted data; it cannot become shell, path, or tool instructions.
- Separate analysis secrets from GitHub write permissions.
- Idempotency keys and DB constraints are the final defense; workflow ordering is not assumed.

## Test boundaries

- Domain unit tests: state transitions, conflict preconditions, override behavior, idempotency.
- Adapter contract tests: GitHub fixtures, OpenRouter schema/error mapping, HMAC verification.
- Integration tests: Supabase migrations/RLS and versioned persistence.
- Frontend tests: status priority, evidence reveal, Handshake/Override separation.
- One Playwright happy path may be added after the core flow works.
- Live evaluation: three aligned and three conflicting repository scenarios; one real PR of each outcome.

## Tooling and deployment

- Backend: Python 3.12, uv, Pydantic, FastAPI, Ruff, pytest.
- Frontend: React, TypeScript, Vite, npm, ESLint, Vitest, React Testing Library.
- Web: Vercel using `apps/web/vercel.json`.
- API: the production-neutral `backend/Dockerfile` start command and `/healthz` healthcheck;
  deploy that image to the selected container host.
- Database/Auth: Supabase.
- Worker: GitHub-hosted Actions.
- Runtime AI: OpenRouter; Codex Pro is a development agent, not application runtime.

## Architecture principles

1. Keep irreversible business rules deterministic and provider-independent.
2. Put AI at semantic boundaries, then validate before side effects.
3. Grant write authority to the narrowest process and path.
4. Optimize for one observable vertical slice; retain seams for later extraction, not premature services.
