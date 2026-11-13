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
import re
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
    """Decode a JSONB value from PostgreSQL into a Python object.

    ``json.loads`` recurses once per level, so a value nested past the
    interpreter's limit (about 10,000 levels; Postgres stores up to about
    20,000) raised ``RecursionError`` on every read of its row (#479). Such
    a value is decoded by :func:`_loads_deep`, which keeps its own stack.
    """
    try:
        return json.loads(value)
    except RecursionError:
        return _loads_deep(value)


_WHITESPACE = " \t\n\r"
#: A JSON number, as ``json``'s own scanner reads one: its integer part, fraction and exponent.
_NUMBER = re.compile(r"(-?(?:0|[1-9]\d*))(\.\d+)?([eE][-+]?\d+)?")
_LITERALS = (("true", True), ("false", False), ("null", None))


def _loads_deep(text: str) -> Any:
    """``json.loads(text)`` for a value of any depth: the same value, built with a stack of its own.

    Postgres hands JSONB back as canonical JSON, so this reads only what that
    can hold: objects, arrays, strings, numbers, ``true``, ``false``,
    ``null``. Anything else raises ``ValueError``, as ``json.loads`` does.
    """
    containers: list[Any] = []  # the objects and arrays still open, outermost first
    keys: list[str | None] = []  # for each open object, the key its next value goes under
    end = len(text)

    def skip(i: int) -> int:
        while i < end and text[i] in _WHITESPACE:
            i += 1
        return i

    def string_at(i: int) -> tuple[str, int]:
        """The string whose opening quote is at ``i``, and the index after its closing one."""
        j = i + 1
        while text[j] != '"':
            j += 2 if text[j] == "\\" else 1
        return json.loads(text[i : j + 1]), j + 1

    def key_at(i: int) -> tuple[str, int]:
        if text[i] != '"':
            raise ValueError(f"expected a key at {i}")
        key, i = string_at(i)
        i = skip(i)
        if text[i] != ":":
            raise ValueError(f"expected ':' at {i}")
        return key, skip(i + 1)

    try:
        i = skip(0)
        while True:
            # A value starts at i: open a container, or read a scalar.
            char = text[i]
            if char in "{[":
                i = skip(i + 1)
                if text[i] != ("}" if char == "{" else "]"):
                    containers.append({} if char == "{" else [])
                    if char == "{":
                        key, i = key_at(i)
                        keys.append(key)
                    else:
                        keys.append(None)
                    continue
                value: Any = {} if char == "{" else []
                i += 1
            elif char == '"':
                value, i = string_at(i)
            else:
                number = _NUMBER.match(text, i)
                if number is not None:
                    whole, fraction, exponent = number.groups()
                    value = float(whole + (fraction or "") + (exponent or "")) if fraction or exponent else int(whole)
                    i = number.end()
                else:
                    literal = next(((word, v) for word, v in _LITERALS if text.startswith(word, i)), None)
                    if literal is None:
                        raise ValueError(f"expected a value at {i}")
                    value, i = literal[1], i + len(literal[0])
            # Put the value in its container, and close every container that ends after it.
            while True:
                if not containers:
                    if skip(i) != end:
                        raise ValueError(f"extra data at {skip(i)}")
                    return value
                container = containers[-1]
                if isinstance(container, dict):
                    container[keys[-1]] = value
                else:
                    container.append(value)
                i = skip(i)
                if text[i] == ",":
                    i = skip(i + 1)
                    if isinstance(container, dict):
                        keys[-1], i = key_at(i)
                    break
                if text[i] != ("}" if isinstance(container, dict) else "]"):
                    raise ValueError(f"expected ',' or the container's end at {i}")
                i += 1
                value = containers.pop()
                keys.pop()
    except IndexError:
        raise ValueError("the JSON text ends early") from None


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
        """The URL for logs: user and password masked, host/port/database kept.

        #535: the one URL masker every renderer of a setting uses. Anything
        that is not a URL (a libpq keyword DSN) is not shown at all.
        """
        from embry0.config_redaction import mask_url_credentials

        return mask_url_credentials(self._url) if "://" in self._url else "***"
