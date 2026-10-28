"""The stored settings documents (migration 129, spec 2026-10-28).

One row per scope; only the deployment's exists today. This layer stores
what it is given: :mod:`embry0.settings.document` checks a document before
the store writes it, and SQL cannot.
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

    async def head(self, scope: str) -> tuple[int, datetime] | None:
        """The scope's write counter and the time of its last write, or None when it has no row.

        One primary-key read. The time tells two rows with the same counter
        apart, as after a database is restored to an older copy.
        """
        row = await self._db.fetchrow("SELECT generation, updated_at FROM settings_documents WHERE scope = $1", scope)
        return None if row is None else (int(row["generation"]), row["updated_at"])

    async def get(self, scope: str) -> dict[str, Any] | None:
        """The whole row in one statement, so its generation and values belong together."""
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM settings_documents WHERE scope = $1", scope)
        return _row(row) if row else None

    async def lock(self, conn: asyncpg.Connection, scope: str) -> dict[str, Any]:
        """The row, locked for the caller's transaction; created empty when missing."""
        await conn.execute("INSERT INTO settings_documents (scope) VALUES ($1) ON CONFLICT (scope) DO NOTHING", scope)
        row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM settings_documents WHERE scope = $1 FOR UPDATE", scope)
        return _row(row)

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
        """Store the document on the caller's locked row and count the write. Returns the new row."""
        row = await conn.fetchrow(
            f"""
            UPDATE settings_documents SET
                yaml_text = $2, data = $3::jsonb, schema_version = $4, bootstrapped_keys = $5,
                updated_by = $6, generation = generation + 1, updated_at = NOW()
            WHERE scope = $1
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
