"""The JSONB codec reads a value too deep for ``json.loads`` on the event loop only when it is short (#527).

#479 decodes such a value with ``_loads_deep``, which keeps its own stack. It is
pure Python and runs where the codec runs, in asyncpg's protocol on the event
loop: a 7.5 MB value 15,000 levels deep held the loop, and every other request,
for seconds. A text longer than ``DEEP_DECODE_MAX_CHARS`` is now not read at
all: the read raises ``StoredValueTooDeep``. Only the reads that show rows read
it as a stand-in, served as a document too deep to show (#449).
"""

from __future__ import annotations

import json

import pytest

from embry0.json_text import UNSERVABLE_TEXT, served, too_deep_to_read
from embry0.storage import database
from embry0.storage.database import (
    DEEP_DECODE_MAX_CHARS,
    StoredValueTooDeep,
    _jsonb_decoder,
    _jsonb_decoder_for_display,
    _loads_deep,
    _tables,
)
from tests.unit.storage.test_jsonb_decoder_deep import _depth_and_leaf

LEVELS = 12_000


def _deep(levels: int, leaf: str) -> str:
    return '{"a": ' * levels + json.dumps(leaf) + "}" * levels


def test_a_value_past_the_limit_is_not_read() -> None:
    text = '{"top": 1, "x": ' + ('{"s": "' + "s" * 80 + '", "z": ') * LEVELS + '"leaf"' + "}" * (LEVELS + 1)
    assert len(text) > 8 * DEEP_DECODE_MAX_CHARS
    with pytest.raises(StoredValueTooDeep, match="too deep for json.loads and longer than 128 KiB"):
        _jsonb_decoder(text)
    assert not issubclass(StoredValueTooDeep, ValueError), "a reader answering a ValueError with a default takes it"


def test_the_limit_is_on_the_text_read() -> None:
    """A text exactly as long as the limit is read whole; one character more is not read."""
    at = _deep(LEVELS, "x" * (DEEP_DECODE_MAX_CHARS - len(_deep(LEVELS, ""))))
    assert len(at) == DEEP_DECODE_MAX_CHARS
    assert _depth_and_leaf(_loads_deep(at))[0] == LEVELS
    with pytest.raises(StoredValueTooDeep):
        _loads_deep(at[:-LEVELS] + " " + at[-LEVELS:])


def test_the_deepest_value_postgres_stores_is_read_whole() -> None:
    """Postgres refuses a JSONB value past about 16,300 levels (``stack depth limit exceeded``)."""
    barest = '{"a": ' * 16_300 + "1" + "}" * 16_300
    assert len(barest) <= DEEP_DECODE_MAX_CHARS
    assert _depth_and_leaf(_jsonb_decoder(barest)) == (16_300, 1), "the limit refuses a value Postgres can store"


def test_a_string_is_read_by_json_s_own_scanner(monkeypatch: pytest.MonkeyPatch) -> None:
    """A long string is not stepped through character by character in Python on the event loop."""
    calls: list[int] = []
    real = database._scanstring

    def scan(text: str, end: int) -> tuple[str, int]:
        calls.append(end)
        return real(text, end)

    monkeypatch.setattr(database, "_scanstring", scan)
    long = "x" * 40_000 + '\\"\u00e9'
    text = _deep(LEVELS, long)
    assert len(text) <= DEEP_DECODE_MAX_CHARS
    assert _depth_and_leaf(_jsonb_decoder(text)) == (LEVELS, long)
    assert calls, "the strings are not read by json's scanner"


def test_the_pool_names_the_tables_a_query_reads() -> None:
    assert _tables("SELECT * FROM jobs WHERE job_id = $1") == "jobs"
    assert _tables("SELECT j.* FROM jobs j JOIN issues i ON i.id = j.issue_id") == "jobs, issues"
    assert StoredValueTooDeep("jobs").where == "jobs" and "in jobs is nested" in str(StoredValueTooDeep("jobs"))


def test_a_read_that_shows_rows_reads_it_as_a_stand_in_served_as_too_deep_to_show() -> None:
    long = '{"top": 1, "x": ' + _deep(LEVELS, "y" * 200_000) + "}"
    for text in (long, "[" + long + "]"):
        stand_in = _jsonb_decoder_for_display(text)
        assert isinstance(stand_in, list if text.startswith("[") else dict)
        assert served(stand_in) == UNSERVABLE_TEXT, "the stand-in is not served as a document too deep to show"
    assert _jsonb_decoder_for_display('{"a": [1, 2]}') == {"a": [1, 2]}, "a value read whole is read as before"
    assert served(too_deep_to_read("{")) == UNSERVABLE_TEXT


async def test_two_first_reads_for_display_open_one_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    opened: list[int] = []

    async def create_pool(*args: object, **kwargs: object) -> object:
        opened.append(1)
        await asyncio.sleep(0)
        return object()

    monkeypatch.setattr(database.asyncpg, "create_pool", create_pool)
    pool = database.DatabasePool("postgresql://unused")
    pool.pool = object()  # type: ignore[assignment]
    first, second = await asyncio.gather(pool._display(), pool._display())
    assert first is second and len(opened) == 1, f"two first reads opened {len(opened)} pools"
