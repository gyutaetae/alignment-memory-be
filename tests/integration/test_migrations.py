import os
import re
from pathlib import Path

import pytest
from psycopg import AsyncConnection

_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION_DIR = _ROOT / "supabase" / "migrations"
_MIGRATIONS = tuple(sorted(_MIGRATION_DIR.glob("*.sql")))
_SQL = "\n".join(path.read_text() for path in _MIGRATIONS).lower()
_TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

_REQUIRED_TABLES = {
    "profiles",
    "github_installations",
    "repositories",
    "repository_memberships",
    "sources",
    "source_versions",
    "sync_jobs",
    "ai_runs",
    "knowledge_nodes",
    "knowledge_node_versions",
    "knowledge_edges",
    "evidence_links",
    "alignment_analyses",
    "alignment_findings",
    "context_passports",
    "handshakes",
    "human_overrides",
    "generated_artifacts",
}


def test_migrations_create_required_tables_in_sequence() -> None:
    assert [path.name for path in _MIGRATIONS] == sorted(path.name for path in _MIGRATIONS)
    assert len(_MIGRATIONS) >= 2
    for table in _REQUIRED_TABLES:
        assert re.search(rf"create table if not exists\s+{table}\b", _SQL), table


def test_every_user_facing_table_enables_rls() -> None:
    for table in _REQUIRED_TABLES:
        assert f"alter table {table} enable row level security" in _SQL, table
    assert "is_repository_member" in _SQL
    assert "can_write_repository" in _SQL
    assert "service_role" in _SQL


def test_immutable_history_and_idempotency_constraints_are_declared() -> None:
    assert "unique (source_id, content_hash)" in _SQL
    assert "unique (node_id, revision)" in _SQL
    assert "unique (repository_id, event_key)" in _SQL
    assert "unique (repository_id, pr_number, head_sha, knowledge_revision)" in _SQL
    assert "source_versions_append_only" in _SQL
    assert "alignment_findings_append_only" in _SQL
    assert "handshakes_append_only" in _SQL
    assert "human_overrides_append_only" in _SQL
    assert "baseline_commit_sha" in _SQL
    assert "main_commit_sha" in _SQL
    assert "current_version_id" in _SQL


@pytest.mark.postgres
@pytest.mark.asyncio
@pytest.mark.skipif(
    not _TEST_DATABASE_URL,
    reason="TEST_DATABASE_URL is not set; PostgreSQL migration integration is skipped",
)
async def test_migrations_apply_twice_in_one_rollback_only_transaction() -> None:
    assert _TEST_DATABASE_URL is not None
    connection = await AsyncConnection.connect(_TEST_DATABASE_URL)
    try:
        await connection.execute("create schema if not exists auth")
        await connection.execute(
            "create table if not exists auth.users (id uuid primary key)"
        )
        cursor = await connection.execute(
            "select to_regprocedure('auth.uid()') is not null as exists"
        )
        row = await cursor.fetchone()
        if row is not None and not row[0]:
            await connection.execute(
                """
                create function auth.uid() returns uuid
                language sql stable
                as $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$
                """
            )

        for _ in range(2):
            for migration in _MIGRATIONS:
                await connection.execute(migration.read_text())

        cursor = await connection.execute(
            """
            select tablename
            from pg_tables
            where schemaname = 'public' and tablename = any(%s)
            """,
            (list(_REQUIRED_TABLES),),
        )
        rows = await cursor.fetchall()
        assert {row[0] for row in rows} == _REQUIRED_TABLES
    finally:
        await connection.rollback()
        await connection.close()
