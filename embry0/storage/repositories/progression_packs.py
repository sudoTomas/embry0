"""The stored progression content pack (#176, migration 130): its versions, its draft and the defaults it was based on.

One pack per deployment. Each activation is a new row with status
``active``, under the next version number, and the row it replaces becomes
``superseded``: the versions are the pack's revision history, as for
standards (ADR 0014), and the active one is always the newest. There is one
``draft`` at most; saving another replaces it under a new version number, so
an ``If-Match`` that names the old draft never matches the new one, and
activating it copies it into a new active row. Every row records the active
version it was written over (``parent_version``). No row at all means the
deployment follows the shipped default.

Every write takes one transaction-scoped advisory lock first, so two writers
never interleave a read of the active version with the switch. The
application checks the content; the database only keeps its shape.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

import asyncpg

from embry0.storage.database import DatabasePool

PackStatus = Literal["draft", "active", "superseded"]
PackSource = Literal["person", "file", "restore", "reset", "upgrade"]

#: Superseded versions kept; each activation deletes the older ones.
KEEP_SUPERSEDED: Final = 100
#: The advisory lock every write takes ("e0pack").
WRITE_LOCK_KEY: Final = 0x6530_7061_636B

_COLUMNS = (
    "version, status, schema_version, follows_default, text, document, based_on, source, reason, "
    "created_by, created_at, activated_at, parent_version"
)
_SUMMARY_COLUMNS = (
    "version, status, schema_version, follows_default, NULL AS text, NULL::jsonb AS document, based_on, source, "
    "reason, created_by, created_at, activated_at, parent_version"
)


@dataclass(frozen=True)
class StoredPack:
    """One row: a version of the pack, the draft, or a superseded version.

    ``text`` and ``document`` are None when the version follows the shipped
    default, and in a summary read without them.
    """

    version: int
    status: PackStatus
    schema_version: int
    follows_default: bool
    text: str | None
    document: dict[str, Any] | None
    based_on: str | None
    source: PackSource
    reason: str
    created_by: str | None
    created_at: datetime
    activated_at: datetime | None
    #: The active version when this row was written (0: nothing was stored). A draft activates only over it.
    parent_version: int = 0

    @classmethod
    def from_row(cls, row: Any) -> StoredPack:
        return cls(
            version=int(row["version"]),
            status=row["status"],
            schema_version=int(row["schema_version"]),
            follows_default=bool(row["follows_default"]),
            text=row["text"],
            document=dict(row["document"]) if row["document"] is not None else None,
            based_on=row["based_on"],
            source=row["source"],
            reason=row["reason"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            activated_at=row["activated_at"],
            parent_version=int(row["parent_version"]),
        )


class ProgressionPacksRepository:
    """Reads and writes ``progression_packs`` and ``progression_pack_defaults``."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # ---- reading ----

    async def active_version(self) -> int | None:
        """The active version's number, the one query a pack read makes when nothing changed."""
        value = await self._db.fetchval("SELECT version FROM progression_packs WHERE status = 'active'")
        return int(value) if value is not None else None

    async def active(self) -> StoredPack | None:
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM progression_packs WHERE status = 'active'")
        return StoredPack.from_row(row) if row is not None else None

    async def draft(self) -> StoredPack | None:
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM progression_packs WHERE status = 'draft'")
        return StoredPack.from_row(row) if row is not None else None

    async def get(self, version: int) -> StoredPack | None:
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM progression_packs WHERE version = $1", version)
        return StoredPack.from_row(row) if row is not None else None

    async def versions(self, limit: int = 100) -> list[StoredPack]:
        """The active and superseded versions, newest first, without their text."""
        rows = await self._db.fetch(
            f"SELECT {_SUMMARY_COLUMNS} FROM progression_packs WHERE status <> 'draft' ORDER BY version DESC LIMIT $1",
            limit,
        )
        return [StoredPack.from_row(row) for row in rows]

    async def timezone_known(self, name: str, conn: asyncpg.Connection | None = None) -> bool:
        """Whether Postgres knows the timezone ``name``; the facts query reads the pack's calendar through it.

        Python's ``zoneinfo`` and Postgres's tz database can disagree (Python
        knows ``localtime``, Postgres does not). On ``conn`` the check runs in
        a savepoint, so an unknown name does not abort the caller's transaction.
        """

        async def ask(on: asyncpg.Connection) -> bool:
            try:
                async with on.transaction():
                    await on.fetchval("SELECT now() AT TIME ZONE $1", name)
            except asyncpg.InvalidParameterValueError:
                return False
            return True

        if conn is not None:
            return await ask(conn)
        async with self._db.transaction() as own:
            return await ask(own)

    async def default_text(self, digest: str) -> str | None:
        """The text of a shipped default a stored pack was based on, when it was recorded."""
        value = await self._db.fetchval("SELECT text FROM progression_pack_defaults WHERE hash = $1", digest)
        return str(value) if value is not None else None

    # ---- writing ----

    @asynccontextmanager
    async def writing(self) -> AsyncIterator[asyncpg.Connection]:
        """A transaction holding the pack's write lock; everything in it commits or rolls back together."""
        async with self._db.transaction() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock($1)", WRITE_LOCK_KEY)
            yield conn

    @staticmethod
    async def active_on(conn: asyncpg.Connection) -> StoredPack | None:
        row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM progression_packs WHERE status = 'active'")
        return StoredPack.from_row(row) if row is not None else None

    @staticmethod
    async def draft_on(conn: asyncpg.Connection) -> StoredPack | None:
        row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM progression_packs WHERE status = 'draft'")
        return StoredPack.from_row(row) if row is not None else None

    @staticmethod
    async def get_on(conn: asyncpg.Connection, version: int) -> StoredPack | None:
        row = await conn.fetchrow(f"SELECT {_COLUMNS} FROM progression_packs WHERE version = $1", version)
        return StoredPack.from_row(row) if row is not None else None

    @staticmethod
    async def insert(
        conn: asyncpg.Connection,
        *,
        status: Literal["draft", "active"],
        schema_version: int,
        follows_default: bool,
        text: str | None,
        document: dict[str, Any] | None,
        based_on: str | None,
        source: PackSource,
        reason: str,
        created_by: str | None,
        parent_version: int,
    ) -> StoredPack:
        """A new row under a new version number; an active one is stamped with its activation time.

        ``parent_version`` is the active version the row was written over
        (0 when nothing was stored).
        """
        row = await conn.fetchrow(
            f"""
            INSERT INTO progression_packs
                (status, schema_version, follows_default, text, document, based_on, source, reason, created_by,
                 activated_at, parent_version)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, CASE WHEN $1 = 'active' THEN NOW() END, $10)
            RETURNING {_COLUMNS}
            """,
            status,
            schema_version,
            follows_default,
            text,
            document,
            based_on,
            source,
            reason,
            created_by,
            parent_version,
        )
        return StoredPack.from_row(row)

    @staticmethod
    async def supersede_active(conn: asyncpg.Connection) -> int | None:
        """The active version becomes superseded; answers its number, None when there was none."""
        value = await conn.fetchval(
            "UPDATE progression_packs SET status = 'superseded' WHERE status = 'active' RETURNING version"
        )
        return int(value) if value is not None else None

    @staticmethod
    async def delete_draft(conn: asyncpg.Connection) -> int | None:
        value = await conn.fetchval("DELETE FROM progression_packs WHERE status = 'draft' RETURNING version")
        return int(value) if value is not None else None

    @staticmethod
    async def trim(conn: asyncpg.Connection, keep: int = KEEP_SUPERSEDED) -> int:
        """Delete the superseded versions older than the newest ``keep``; answers how many went."""
        result = await conn.execute(
            """
            DELETE FROM progression_packs
            WHERE status = 'superseded' AND version < (
                SELECT version FROM progression_packs WHERE status = 'superseded'
                ORDER BY version DESC OFFSET $1 LIMIT 1
            )
            """,
            keep - 1,
        )
        return int(result.split()[-1]) if result.startswith("DELETE") else 0

    @staticmethod
    async def record_default(conn: asyncpg.Connection, digest: str, text: str) -> None:
        """Keep the text of a shipped default a pack is based on; the first text seen for a hash stays."""
        await conn.execute(
            "INSERT INTO progression_pack_defaults (hash, text) VALUES ($1, $2) ON CONFLICT (hash) DO NOTHING",
            digest,
            text,
        )
