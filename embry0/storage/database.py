"""PostgreSQL connection pool management via asyncpg.

JSONB encoding policy
---------------------
The connection pool registers a single JSONB type codec (``_jsonb_encoder`` /
``_jsonb_decoder``) so JSONB values cross the wire as Python objects in BOTH
directions. Concretely:

* **Write path**: repository code passes the *Python value* (dict, list, bool,
  int, str, None) as a query parameter. The codec calls ``json.dumps()`` on it
  exactly once. Repos must NOT pre-encode with ``json.dumps()`` — doing so
  causes a JSON-string-scalar to land inside the JSONB column (the value
  becomes ``"{\\"foo\\": 1}"`` rather than ``{"foo": 1}``), which silently
  breaks server-side ``->>`` and ``->`` operators.

* **Read path**: asyncpg returns JSONB as a Python object. Repos must NOT
  call ``json.loads()`` on the result. The decoder runs once.

The ``::jsonb`` cast in SQL is fine to keep — it gives Postgres an
unambiguous target type when the parameter inference would otherwise be
ambiguous. The cast does NOT cause double-encoding; only manual
``json.dumps()`` in the parameter list does.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


def _jsonb_encoder(value: Any) -> str:
    """Encode a Python value as a JSON string for JSONB columns.

    Called by asyncpg once per JSONB parameter. Repository code MUST pass a
    Python value (dict/list/scalar/None), not a pre-encoded JSON string —
    pre-encoding double-wraps the value inside a JSON string scalar.
    """
    return json.dumps(value)


def _jsonb_decoder(value: str) -> Any:
    """Decode a JSONB value from PostgreSQL into a Python object."""
    return json.loads(value)


class DatabasePool:
    """Manages an asyncpg connection pool.

    Usage:
        db = DatabasePool(database_url)
        await db.connect()
        row = await db.fetchrow("SELECT * FROM jobs WHERE job_id = $1", job_id)
        await db.close()
    """

    def __init__(self, database_url: str, min_size: int = 2, max_size: int = 10) -> None:
        self._url = database_url
        self._min_size = min_size
        self._max_size = max_size
        self.pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        """Create the connection pool."""

        async def _init_connection(conn: asyncpg.Connection) -> None:
            """Set up JSONB codec so asyncpg returns Python objects, not strings."""
            await conn.set_type_codec(
                "jsonb",
                encoder=_jsonb_encoder,
                decoder=_jsonb_decoder,
                schema="pg_catalog",
            )

        self.pool = await asyncpg.create_pool(
            self._url,
            min_size=self._min_size,
            max_size=self._max_size,
            init=_init_connection,
        )
        logger.info("database_connected", url=self._redacted_url)

    async def close(self) -> None:
        """Close the connection pool."""
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("database_disconnected")

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        """Yield a connection with BEGIN/COMMIT wrapped around the block.

        Usage:
            async with db.transaction() as conn:
                await conn.execute("INSERT ...")
                await conn.execute("UPDATE ...")
            # Commit on normal exit; rollback on any exception.
        """
        if self.pool is None:
            raise RuntimeError("Call connect() first")
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                yield conn

    async def execute(self, query: str, *args: Any) -> str:
        """Execute a query and return the status string."""
        if self.pool is None:
            raise RuntimeError("Call connect() first")
        async with self.pool.acquire() as conn:
            return str(await conn.execute(query, *args))

    async def fetchval(self, query: str, *args: Any) -> Any:
        """Execute a query and return a single value."""
        if self.pool is None:
            raise RuntimeError("Call connect() first")
        async with self.pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        """Execute a query and return a single row."""
        if self.pool is None:
            raise RuntimeError("Call connect() first")
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        """Execute a query and return all rows."""
        if self.pool is None:
            raise RuntimeError("Call connect() first")
        async with self.pool.acquire() as conn:
            return list(await conn.fetch(query, *args))

    @property
    def _redacted_url(self) -> str:
        """Redact password from URL for logging."""
        try:
            from urllib.parse import urlparse, urlunparse

            parsed = urlparse(self._url)
            if parsed.password:
                redacted = parsed._replace(netloc=f"{parsed.username}:***@{parsed.hostname}:{parsed.port}")
                return urlunparse(redacted)
        except Exception:
            pass
        return "***"
