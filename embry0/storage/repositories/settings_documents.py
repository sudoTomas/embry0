"""The stored settings documents (migration 129, spec 2026-10-28; layers, migration 134, spec 2026-10-29).

One row per scope: the deployment's, inserted by migration 129, and one per
organization or repository layer, inserted by the layer's first write. An
empty text is how a layer is cleared; the one row ever removed is a renamed
repository's layer, which :meth:`SettingsDocumentsRepository.move` moves to
its new slug with its write counter. This layer
stores what it is given: :mod:`embry0.settings.document` checks a document
before the store writes it, and SQL cannot.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

import asyncpg

from embry0.storage.database import DatabasePool

DEPLOYMENT: Final = "deployment"

_COLUMNS = "scope, yaml_text, data, schema_version, generation, bootstrapped_keys, updated_by, updated_at"


def _row(record: Any) -> dict[str, Any]:
    row = dict(record)
    row["bootstrapped_keys"] = list(row.get("bootstrapped_keys") or [])
    row["data"] = dict(row.get("data") or {})
    return row


class SettingsDocumentsRepository:
    """Read and write the ``settings_documents`` table."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    async def token(self) -> tuple[int, int, datetime | None]:
        """What every reader's freshness check compares: the rows, the sum of their write counters, the newest write.

        One query over the table. Every write adds one to its row's counter
        and no counter is lost (a row that moves to a renamed repository's
        new slug takes its counter along), so the sum moves on every committed
        write in any scope; a database restored to an older copy moves it, or the
        newest write time, in either direction.
        """
        row = await self._db.fetchrow(
            "SELECT count(*) AS n, COALESCE(sum(generation), 0)::bigint AS total, max(updated_at) AS newest "
            "FROM settings_documents"
        )
        return token_of(row)

    async def all(self) -> list[dict[str, Any]]:
        """Every document's values in one statement, so a snapshot's rows belong together; never the text."""
        rows = await self._db.fetch(
            "SELECT scope, data, schema_version, generation, updated_by, updated_at FROM settings_documents"
        )
        out = []
        for record in rows:
            row = dict(record)
            row["data"] = dict(row.get("data") or {})
            out.append(row)
        return out

    async def get(self, scope: str) -> dict[str, Any] | None:
        """The whole row in one statement, so its generation and values belong together."""
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM settings_documents WHERE scope = $1", scope)
        return _row(row) if row else None

    async def hold(self, conn: asyncpg.Connection, scope: str) -> None:
        """Queue behind every other writer of ``scope`` until the caller's transaction ends, row or not.

        A layer's write holds the documents it inherits from, most general
        first, so it is checked against what they hold once their writers
        commit. Every writer takes them in that order, so none waits on
        another in a circle.
        """
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('settings_documents:' || $1, 0))", scope)

    async def repository_scopes(self, conn: asyncpg.Connection | None = None) -> list[str]:
        """Every stored repository layer's scope string."""
        sql = "SELECT scope FROM settings_documents WHERE scope LIKE 'repository:%'"
        rows = await (conn.fetch(sql) if conn is not None else self._db.fetch(sql))
        return [str(r["scope"]) for r in rows]

    async def rows(self, conn: asyncpg.Connection, scopes: list[str]) -> list[dict[str, Any]]:
        """The values of the documents at ``scopes``, read on the caller's connection, as :meth:`all` reads them."""
        rows = await conn.fetch(
            "SELECT scope, data, schema_version, generation, updated_by, updated_at FROM settings_documents "
            "WHERE scope = ANY($1::text[])",
            scopes,
        )
        return [{**dict(r), "data": dict(r["data"] or {})} for r in rows]

    async def move(self, conn: asyncpg.Connection, old: str, new: str) -> None:
        """Move the locked document at ``old`` to ``new``: its text, values, ledger and write counter, unchanged.

        For a renamed repository's layer, on the caller's transaction, which
        holds the locks of both scopes; ``new`` has no row.
        """
        await conn.execute(
            """
            INSERT INTO settings_documents
                (scope, yaml_text, data, schema_version, generation, bootstrapped_keys, updated_by, created_at, updated_at)
            SELECT $2, yaml_text, data, schema_version, generation, bootstrapped_keys, updated_by, created_at, updated_at
              FROM settings_documents WHERE scope = $1
            """,
            old,
            new,
        )
        await conn.execute("DELETE FROM settings_documents WHERE scope = $1", old)

    async def lock(self, conn: asyncpg.Connection, scope: str) -> dict[str, Any]:
        """The row, locked for the caller's transaction.

        The deployment's row is created empty when missing, as it always was.
        A layer that has no row reads as an empty document at generation 0
        (``exists`` false), and the caller's :meth:`write` creates it: a dry
        run or an unchanged write leaves no row behind. Writers of one scope
        queue on a transaction lock taken first, so two first writes of a
        layer cannot both create it.
        """
        await self.hold(conn, scope)
        if scope == DEPLOYMENT:
            await conn.execute(
                "INSERT INTO settings_documents (scope) VALUES ($1) ON CONFLICT (scope) DO NOTHING", scope
            )
        row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM settings_documents WHERE scope = $1 FOR UPDATE", scope)
        if row is None:
            return {
                "scope": scope,
                "yaml_text": "",
                "data": {},
                "schema_version": 1,
                "generation": 0,
                "bootstrapped_keys": [],
                "updated_by": "",
                "updated_at": None,
                "exists": False,
            }
        return {**_row(row), "exists": True}

    async def write(
        self,
        conn: asyncpg.Connection,
        scope: str,
        *,
        yaml_text: str,
        data: dict[str, Any],
        schema_version: int,
        bootstrapped_keys: list[str],
        updated_by: str,
    ) -> dict[str, Any]:
        """Store the document on the caller's locked row, or create it at generation 1, and count the write.

        Returns the new row.
        """
        row = await conn.fetchrow(
            f"""
            INSERT INTO settings_documents
                (scope, yaml_text, data, schema_version, bootstrapped_keys, updated_by, generation, updated_at)
            VALUES ($1, $2, $3::jsonb, $4, $5, $6, 1, NOW())
            ON CONFLICT (scope) DO UPDATE SET
                yaml_text = EXCLUDED.yaml_text, data = EXCLUDED.data, schema_version = EXCLUDED.schema_version,
                bootstrapped_keys = EXCLUDED.bootstrapped_keys, updated_by = EXCLUDED.updated_by,
                generation = settings_documents.generation + 1, updated_at = NOW()
            RETURNING {_COLUMNS}
            """,
            scope,
            yaml_text,
            data,
            schema_version,
            bootstrapped_keys,
            updated_by,
        )
        return _row(row)

    async def layers(self) -> list[dict[str, Any]]:
        """Every stored organization and repository layer, by scope: values and who wrote them, not the text."""
        rows = await self._db.fetch(
            "SELECT scope, data, generation, updated_by, updated_at FROM settings_documents "
            "WHERE scope <> $1 ORDER BY scope",
            DEPLOYMENT,
        )
        return [{**dict(r), "data": dict(r["data"] or {})} for r in rows]


def token_of(row: Any) -> tuple[int, int, datetime | None]:
    """A freshness token from the aggregate of :meth:`SettingsDocumentsRepository.token`."""
    return (int(row["n"]), int(row["total"]), row["newest"])


def token_of_rows(rows: list[dict[str, Any]]) -> tuple[int, int, datetime | None]:
    """The token the rows of one :meth:`SettingsDocumentsRepository.all` read add up to."""
    times = [r["updated_at"] for r in rows if r.get("updated_at") is not None]
    return (len(rows), sum(int(r["generation"]) for r in rows), max(times) if times else None)
