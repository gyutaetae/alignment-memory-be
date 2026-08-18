# Alignment Memory — Backend

FastAPI control plane, GitHub Actions worker, Supabase migrations.

See `docs/` for product and architecture documentation.

## Structure

```
src/alignment_memory/    Python package (FastAPI + Worker)
supabase/migrations/     PostgreSQL schema and RLS
.github/workflows/       PR Analyze / Merge Publish Actions
knowledge/generated/     AI-generated project knowledge (auto-updated on merge)
docs/                    Product, flow, schema, architecture docs
```

## Quick Start (fixture mode)

```bash
uv sync --group dev
APP_MODE=fixture uv run uvicorn alignment_memory.interfaces.api.main:create_app --factory --host 127.0.0.1 --port 8000 --reload
```
