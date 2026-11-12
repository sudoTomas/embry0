"""Unit tests for the advisory lock that serialises run_migrations (#249)."""

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.migrations import runner as runner_mod
from embry0.storage.migrations.runner import MIGRATIONS_ADVISORY_LOCK_KEY, run_migrations

APPLIED_SET_QUERY = "SELECT version FROM embry0_migrations"


class _FakeTransaction:
    async def __aenter__(self) -> "_FakeTransaction":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakeConnection:
    """Records every statement issued, optionally failing or blocking on one."""

    def __init__(self, fail_on: str | None = None, block_on: str | None = None) -> None:
        self.statements: list[str] = []
        self.args: list[tuple[Any, ...]] = []
        self._fail_on = fail_on
        self._block_on = block_on
        self.release = asyncio.Event()

    async def execute(self, query: str, *args: Any) -> str:
        self.statements.append(query)
        self.args.append(args)
        if self._block_on is not None and self._block_on in query:
            await self.release.wait()
        if self._fail_on is not None and self._fail_on in query:
            raise RuntimeError("boom")
        return "OK"

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        self.statements.append(query)
        self.args.append(args)
        return []

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction()

    def add_log_listener(self, _listener: Any) -> None:
        """The runner listens for a migration's warnings and notices while it runs it."""

    def remove_log_listener(self, _listener: Any) -> None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self) -> Any:
        yield self._conn


def _fake_db(conn: _FakeConnection) -> DatabasePool:
    db = DatabasePool("postgresql://unused/unused")
    db.pool = _FakePool(conn)  # type: ignore[assignment]
    return db


def _index_of(statements: list[str], needle: str) -> int:
    for i, stmt in enumerate(statements):
        if needle in stmt:
            return i
    raise AssertionError(f"{needle!r} never issued; got {statements}")


@pytest.fixture
def one_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner_mod, "MIGRATIONS", [(1, "test migration", "SELECT 1 /* migration body */")])


@pytest.mark.asyncio
async def test_lock_is_taken_before_the_applied_set_is_read(one_migration: None) -> None:
    """The applied set must be re-read under the lock, never before it."""
    conn = _FakeConnection()
    await run_migrations(_fake_db(conn))

    lock_at = _index_of(conn.statements, "pg_advisory_lock")
    applied_at = _index_of(conn.statements, APPLIED_SET_QUERY)
    assert lock_at < applied_at

    # The ledger table is created under the lock too — concurrent
    # CREATE TABLE IF NOT EXISTS can otherwise raise a duplicate-table error.
    assert lock_at < _index_of(conn.statements, "CREATE TABLE IF NOT EXISTS embry0_migrations")
    assert conn.args[lock_at] == (MIGRATIONS_ADVISORY_LOCK_KEY,)


@pytest.mark.asyncio
async def test_lock_is_released_after_the_loop(one_migration: None) -> None:
    conn = _FakeConnection()
    await run_migrations(_fake_db(conn))

    unlock_at = _index_of(conn.statements, "pg_advisory_unlock")
    assert unlock_at == len(conn.statements) - 1
    assert unlock_at > _index_of(conn.statements, "/* migration body */")
    assert conn.args[unlock_at] == (MIGRATIONS_ADVISORY_LOCK_KEY,)


@pytest.mark.asyncio
async def test_lock_is_released_when_a_migration_fails(one_migration: None) -> None:
    conn = _FakeConnection(fail_on="/* migration body */")

    with pytest.raises(RuntimeError, match="boom"):
        await run_migrations(_fake_db(conn))

    assert "pg_advisory_unlock" in conn.statements[-1]


def test_lock_key_is_ascii_embry0() -> None:
    """The header documents the key as ASCII "embry0" — keep the recipe true."""
    assert MIGRATIONS_ADVISORY_LOCK_KEY.to_bytes(6, "big") == b"embry0"


@pytest.mark.asyncio
async def test_a_prompt_acquisition_logs_only_acquired(one_migration: None, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(runner_mod.logger, "info", lambda event, **kw: events.append(event))

    await run_migrations(_fake_db(_FakeConnection()))

    assert "migrations_lock_waiting" not in events
    assert "migrations_lock_acquired" in events


@pytest.mark.asyncio
async def test_a_blocked_acquisition_is_logged_while_still_blocked(
    one_migration: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The waiting line must land during the stall, not after it clears."""
    monkeypatch.setattr(runner_mod, "LOCK_WAIT_LOG_THRESHOLD_SECONDS", 0.0)
    events: list[str] = []
    monkeypatch.setattr(runner_mod.logger, "info", lambda event, **kw: events.append(event))

    conn = _FakeConnection(block_on="pg_advisory_lock")
    pass_ = asyncio.ensure_future(run_migrations(_fake_db(conn)))
    for _ in range(200):
        if events:
            break
        await asyncio.sleep(0.005)

    # Still inside pg_advisory_lock, and the line is already out.
    assert events == ["migrations_lock_waiting"]
    assert not pass_.done()

    conn.release.set()
    await pass_
    assert events.index("migrations_lock_acquired") == 1
