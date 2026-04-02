"""PostgreSQL connection pool management via asyncpg."""

from typing import Any

import asyncpg
import structlog

logger = structlog.get_logger(__name__)


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
        self.pool = await asyncpg.create_pool(
            self._url,
            min_size=self._min_size,
            max_size=self._max_size,
        )
        logger.info("database_connected", url=self._redacted_url)

    async def close(self) -> None:
        """Close the connection pool."""
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("database_disconnected")

    async def execute(self, query: str, *args: Any) -> str:
        """Execute a query and return the status string."""
        if self.pool is None:
            raise RuntimeError("Call connect() first")
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

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

    async def fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        """Execute a query and return all rows."""
        if self.pool is None:
            raise RuntimeError("Call connect() first")
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

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
