.PHONY: setup demo-evidence lint test check

setup:
	uv sync --group dev

demo-evidence:
	uv run python -m alignment_memory.interfaces.worker.cli demo --output artifacts/demo

lint:
	uv run ruff check src tests

test:
	uv run pytest -q

check: lint test
	git diff --check
