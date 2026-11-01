"""Counting every statement one call sends to Postgres, whichever repository sends it.

:class:`tests.fixtures.asks.CountingDb` counts what one repository sends. A
read built from many repositories, such as the decisions a tick reads, needs
every statement they send together, and none from the app's background loops
on the same pool. :func:`count_statements` patches
:class:`~embry0.storage.database.DatabasePool` so that the statements sent
from inside :func:`counted`, and from the tasks it starts, are recorded: one
entry per round trip, a transaction's ``BEGIN`` and ``COMMIT`` included.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import pytest

from embry0.storage.database import DatabasePool

#: What a transaction's connection sends a statement with.
_SENDS = ("execute", "executemany", "fetch", "fetchrow", "fetchval")


@dataclass
class Statements:
    """The statements one counted call sent: each one's text, flattened to one line, and its arguments."""

    texts: list[str] = field(default_factory=list)
    arguments: list[tuple[Any, ...]] = field(default_factory=list)

    def record(self, query: str, args: tuple[Any, ...]) -> None:
        self.texts.append(" ".join(query.split()))
        self.arguments.append(args)

    def __len__(self) -> int:
        return len(self.texts)

    def naming(self, fragment: str) -> list[int]:
        """The positions of the statements whose text holds ``fragment``."""
        return [i for i, text in enumerate(self.texts) if fragment in text]


_COUNTING: ContextVar[Statements | None] = ContextVar("counting_statements", default=None)


class _CountingConnection:
    """A transaction's connection that records what it sends."""

    def __init__(self, conn: Any, statements: Statements) -> None:
        self._conn = conn
        self._statements = statements

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._conn, name)
        if name not in _SENDS:
            return attr

        async def send(query: str, *args: Any, **kwargs: Any) -> Any:
            self._statements.record(query, args)
            return await attr(query, *args, **kwargs)

        return send


def _counting(real: Any) -> Any:
    async def send(self: DatabasePool, query: str, *args: Any) -> Any:
        statements = _COUNTING.get()
        if statements is not None:
            statements.record(query, args)
        return await real(self, query, *args)

    send.counts_statements = True  # type: ignore[attr-defined]
    return send


def count_statements(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch every pool so that :func:`counted` records what its call sends; undone with ``monkeypatch``."""
    for name in ("execute", "fetch", "fetchrow", "fetchval"):
        monkeypatch.setattr(DatabasePool, name, _counting(getattr(DatabasePool, name)))
    real_transaction = DatabasePool.transaction

    @asynccontextmanager
    async def transaction(self: DatabasePool) -> AsyncIterator[Any]:
        statements = _COUNTING.get()
        async with real_transaction(self) as conn:
            if statements is None:
                yield conn
                return
            statements.record("BEGIN", ())
            yield _CountingConnection(conn, statements)
        statements.record("COMMIT", ())

    monkeypatch.setattr(DatabasePool, "transaction", transaction)


async def counted[T](call: Awaitable[T]) -> tuple[T, Statements]:
    """Await ``call`` and return its result with every statement it sent."""
    if not getattr(DatabasePool.fetch, "counts_statements", False):
        raise RuntimeError("count_statements(monkeypatch) first: without it nothing is counted")
    statements = Statements()
    token = _COUNTING.set(statements)
    try:
        result = await call
    finally:
        _COUNTING.reset(token)
    return result, statements
