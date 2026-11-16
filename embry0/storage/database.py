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
from collections.abc import AsyncIterator, Callable
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
    a value is decoded by :func:`_loads_deep`, which keeps its own stack,
    when it is no longer than :data:`DEEP_DECODE_MAX_CHARS`. A longer one is
    not read at all: the read raises :class:`StoredValueTooDeep` (#527).

    ``json.loads`` also refuses an integer longer than ``int`` reads (4,300
    digits by default, :func:`sys.get_int_max_str_digits`), with a plain
    ``ValueError`` for the whole value, so one such number in a row made every
    read of it fail, a list with it (#523). Such an integer is read as its
    digits, a ``str``: past that many digits it is far past a ``float``'s range
    (``inf``, which JSON cannot write), and an ``int`` that long cannot be
    written out either. Only a value that holds one is read a second time, so
    every other value is read by the one ``json.loads`` call it always was.
    """
    try:
        return json.loads(value)
    except RecursionError:
        return _loads_deep(value)
    except json.JSONDecodeError:
        raise
    except ValueError:
        # Not malformed text, which is a JSONDecodeError: an integer past int's digit limit (#523).
        try:
            return json.loads(value, parse_int=_int_or_digits)
        except RecursionError:
            return _loads_deep(value)


def _jsonb_decoder_for_display(value: str) -> Any:
    """:func:`_jsonb_decoder`, with a value too deep to read as :func:`~embry0.json_text.too_deep_to_read`'s stand-in.

    Only :meth:`DatabasePool.fetch_for_display` reads with it (#527).
    """
    from embry0.json_text import too_deep_to_read

    try:
        return _jsonb_decoder(value)
    except StoredValueTooDeep:
        return too_deep_to_read(value.lstrip()[:1])


def read_jsonb_text(text: str) -> Any:
    """A JSONB value's stored text, read as the codec reads it: for a query that selected it ``::text`` because the
    codec raised :class:`StoredValueTooDeep` for its row (#527), to read the other columns."""
    return _jsonb_decoder(text)


def _int_or_digits(digits: str) -> int | str:
    """The integer ``digits`` spell, or ``digits`` themselves when ``int`` will not read that many (#523)."""
    try:
        return int(digits)
    except ValueError:
        return digits


_WHITESPACE = " \t\n\r"
#: A JSON number, as ``json``'s own scanner reads one: its integer part, fraction and exponent. ASCII digits only:
#: ``\d`` also takes other scripts' digits, which JSON does not, and ``int()`` would read them.
_NUMBER = re.compile(r"(-?(?:0|[1-9][0-9]*))(\.[0-9]+)?([eE][-+]?[0-9]+)?")
_LITERALS = (("true", True), ("false", False), ("null", None))

#: ``json``'s C string scanner (its ``scanstring``: the string from just after an opening quote, and the index after
#: the closing one), which the stubs do not declare.
_scanstring: Callable[[str, int], tuple[str, int]] = json.decoder.scanstring  # type: ignore[attr-defined]

#: The longest value :func:`_loads_deep` reads (#527). It is pure Python and runs where the codec runs, in
#: asyncpg's protocol on the event loop, at about 4 MB/s on the densest JSON: this much holds the loop for some
#: 30 ms, where a 7.5 MB value held it, and every request with it, for seconds. The codec has to answer then and
#: there, so the reading cannot be handed to a thread. The deepest value Postgres stores (about 16,300 levels) is
#: shorter than this in its barest form.
DEEP_DECODE_MAX_CHARS = 128 * 1024


class StoredValueTooDeep(Exception):  # noqa: N818 - says what the value is, as a person reads the error
    """A stored JSONB value too deep for ``json.loads`` and longer than :data:`DEEP_DECODE_MAX_CHARS` (#527).

    The codec does not read it: the read that meets it raises this, so no code
    acts on part of it, or on a default in its place, and nothing writes it
    back. Only a direct database write stores one: the API refuses a document
    nested past 200 levels. Not a ``ValueError``, so a reader that answers a
    malformed value with a default does not take this for one. ``where`` names
    what was being read, when the read knows it (:class:`DatabasePool`'s own
    queries name the tables).
    """

    def __init__(self, where: str = "", *, then: str = "replace it with a value the API accepts") -> None:
        self.where, self.then = where, then
        place = f" in {where}" if where else ""
        super().__init__(
            f"a stored JSONB value{place} is nested too deep for json.loads and longer than "
            f"{DEEP_DECODE_MAX_CHARS // 1024} KiB, so it is not read (#527); only a direct database write stores "
            f"one: {then}"
        )


_TABLE = re.compile(r"\b(?:from|join|update|into)\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)


def _tables(query: str) -> str:
    """The tables ``query`` names, for :class:`StoredValueTooDeep`: ``jobs``, or ``jobs, issues``."""
    return ", ".join(dict.fromkeys(_TABLE.findall(query)))


def _loads_deep(text: str) -> Any:
    """``json.loads(text)`` for a value of any depth: the same value, built with a stack of its own.

    Postgres hands JSONB back as canonical JSON, so this reads only what that
    can hold: objects, arrays, strings, numbers, ``true``, ``false``,
    ``null``. Anything else raises ``ValueError``, as ``json.loads`` does.
    An integer longer than ``int`` reads is its digits, as
    :func:`_jsonb_decoder` reads it (#523).

    A text longer than :data:`DEEP_DECODE_MAX_CHARS` is not read: reading it
    would hold the event loop, so this raises :class:`StoredValueTooDeep`
    (#527). Strings are read by ``json``'s C scanner, so the bound holds for a
    value of long strings too.
    """
    if len(text) > DEEP_DECODE_MAX_CHARS:
        raise StoredValueTooDeep()
    containers: list[Any] = []  # the objects and arrays still open, outermost first
    keys: list[str | None] = []  # for each open object, the key its next value goes under
    end = len(text)

    def skip(i: int) -> int:
        while i < end and text[i] in _WHITESPACE:
            i += 1
        return i

    def string_at(i: int) -> tuple[str, int]:
        """The string whose opening quote is at ``i``, and the index after its closing one: ``json``'s own C
        scanner reads it, so a long string costs no Python step per character on the event loop (#527)."""
        return _scanstring(text, i + 1)

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
                    if fraction or exponent:
                        value = float(whole + (fraction or "") + (exponent or ""))
                    else:
                        value = _int_or_digits(whole)
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
        #: The few connections :meth:`fetch_for_display` reads on (#527), opened when first needed.
        self._display_pool: asyncpg.Pool | None = None

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
        if self._display_pool is not None:
            await self._display_pool.close()
            self._display_pool = None
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
            try:
                return await conn.fetchval(query, *args)
            except StoredValueTooDeep as exc:
                raise StoredValueTooDeep(_tables(query), then=exc.then) from None

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        """Execute a query and return a single row."""
        if self.pool is None:
            raise RuntimeError("Call connect() first")
        async with self.pool.acquire() as conn:
            try:
                return await conn.fetchrow(query, *args)
            except StoredValueTooDeep as exc:
                raise StoredValueTooDeep(_tables(query), then=exc.then) from None

    async def fetch(self, query: str, *args: Any) -> list[Any]:
        """Execute a query and return all rows."""
        if self.pool is None:
            raise RuntimeError("Call connect() first")
        async with self.pool.acquire() as conn:
            try:
                return list(await conn.fetch(query, *args))
            except StoredValueTooDeep as exc:
                raise StoredValueTooDeep(_tables(query), then=exc.then) from None

    # ---- reads that show a stored value too deep to read (#527) -------------

    async def fetch_for_display(self, query: str, *args: Any) -> list[Any]:
        """:meth:`fetch` for a read that only shows its rows: a JSONB value too deep to read is a stand-in.

        For the few reads a person needs to see the other rows through, or to
        find the one to replace (the job list and a job's detail): the stand-in
        (:func:`embry0.json_text.too_deep_to_read`) is served as a document
        too deep to show (#449), and nothing acts on it. These rows come from
        connections of their own, so no other read ever gets a stand-in.
        """
        async with (await self._display()).acquire() as conn:
            return list(await conn.fetch(query, *args))

    async def fetchrow_for_display(self, query: str, *args: Any) -> asyncpg.Record | None:
        """:meth:`fetchrow`, as :meth:`fetch_for_display` reads."""
        async with (await self._display()).acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def _display(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("Call connect() first")
        if self._display_pool is None:

            async def _init_connection(conn: asyncpg.Connection) -> None:
                await conn.set_type_codec(
                    "jsonb", encoder=_jsonb_encoder, decoder=_jsonb_decoder_for_display, schema="pg_catalog"
                )

            self._display_pool = await asyncpg.create_pool(self._url, min_size=0, max_size=2, init=_init_connection)
        return self._display_pool

    @property
    def _redacted_url(self) -> str:
        """The URL for logs: user and password masked, host/port/database kept.

        #535: the one URL masker every renderer of a setting uses. Anything
        that is not a URL (a libpq keyword DSN) is not shown at all.
        """
        from embry0.config_redaction import mask_url_credentials

        return mask_url_credentials(self._url) if "://" in self._url else "***"
