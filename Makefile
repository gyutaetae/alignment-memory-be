.PHONY: setup demo-ui demo-evidence lint test build check

setup:
	uv sync --project backend --group dev
	npm --prefix apps/web ci

demo-ui:
	VITE_FIXTURE_MODE=true npm --prefix apps/web run dev -- --host 127.0.0.1

demo-evidence:
	uv run --project backend python -m alignment_memory.interfaces.worker.cli demo --output artifacts/demo

lint:
	uv run --project backend ruff check backend/src backend/tests
	npm --prefix apps/web run lint

test:
	uv run --project backend pytest -q
	VITE_FIXTURE_MODE=true npm --prefix apps/web test -- --run

build:
	npm --prefix apps/web run build

check: lint test build
	git diff --check
