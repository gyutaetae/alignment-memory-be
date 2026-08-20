import pytest

from alignment_memory.adapters.postgres import repository as repository_module
from alignment_memory.adapters.postgres.repository import PostgresRepository


@pytest.mark.asyncio
async def test_pool_disables_prepared_statements_for_transaction_poolers(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakePool:
        def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
            captured.update(kwargs)

        async def open(self, *, wait: bool, timeout: float) -> None:
            captured["open"] = (wait, timeout)

        async def close(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(repository_module, "AsyncConnectionPool", FakePool)

    repository = await PostgresRepository.create("postgresql://example", timeout=3)
    await repository.close()

    assert captured["kwargs"] == {
        "row_factory": repository_module.dict_row,
        "prepare_threshold": None,
    }
    assert captured["open"] == (True, 3)
    assert captured["closed"] is True
