import inspect

from alignment_memory.adapters.postgres import PostgresRepository


def test_postgres_repository_requires_explicit_async_factory_to_open_pool() -> None:
    assert inspect.iscoroutinefunction(PostgresRepository.create)
    assert inspect.isasyncgenfunction(PostgresRepository.transaction.__wrapped__)
