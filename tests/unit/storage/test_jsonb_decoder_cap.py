"""The JSONB codec reads a value too deep for ``json.loads`` only so far on the event loop (#527).

#479 decodes such a value with ``_loads_deep``, which keeps its own stack. It is
pure Python and runs where the codec runs, in asyncpg's protocol on the event
loop: a 7.5 MB value 15,000 levels deep held the loop, and every other request,
for seconds. It now reads no value that starts past ``DEEP_DECODE_MAX_CHARS``:
that value is a marker standing for the rest, and every open container closes.
"""

from __future__ import annotations

import json

import pytest

from embry0.storage.database import (
    DEEP_DECODE_MAX_CHARS,
    TOO_LONG_TO_READ,
    _jsonb_decoder,
    _jsonb_encoder,
    _loads_deep,
)
from tests.unit.storage.test_jsonb_decoder_deep import _depth_and_leaf, _same

LEVELS = 12_000
BESIDE = json.dumps("s" * 80)


def test_a_value_that_starts_past_the_limit_is_not_read() -> None:
    assert _same(_loads_deep("[1, 2, 3]", limit=7), [1, 2, 3]), "a value starting at the limit is read"
    assert _same(_loads_deep("[1, 2, 3]", limit=6), [1, 2, TOO_LONG_TO_READ]), "a value past the limit is read"
    text = '{"a": [1, {"b": 2, "c": 3}], "d": 4}'
    assert _same(_loads_deep(text, limit=text.index("3") - 1), {"a": [1, {"b": 2, "c": TOO_LONG_TO_READ}]}), (
        "the open containers do not close around the marker, each under its own key"
    )


def test_the_codec_reads_a_long_deep_value_only_as_far_as_the_limit() -> None:
    text = '{"top": 1, "x": ' + f'{{"s": {BESIDE}, "z": ' * LEVELS + '"leaf"' + "}" * (LEVELS + 1)
    assert len(text) > 8 * DEEP_DECODE_MAX_CHARS
    value = _jsonb_decoder(text)
    assert isinstance(value, dict) and value["top"] == 1, "the value is not the object it is stored as"
    depth, leaf = 0, value["x"]
    while isinstance(leaf, dict):
        leaf, depth = leaf["z"], depth + 1
    assert leaf == TOO_LONG_TO_READ, f"the value is read past the limit, to {leaf!r}"
    per_level = len(f'{{"s": {BESIDE}, "z": ')
    assert DEEP_DECODE_MAX_CHARS // per_level - 2 <= depth <= DEEP_DECODE_MAX_CHARS // per_level + 1, (
        f"read {depth} of {LEVELS} levels, {per_level} characters each, for a limit of {DEEP_DECODE_MAX_CHARS:,}"
    )


def test_the_deepest_value_postgres_stores_is_read_whole() -> None:
    """Postgres refuses a JSONB value past about 16,300 levels (``stack depth limit exceeded``)."""
    barest = '{"a": ' * 16_300 + "1" + "}" * 16_300
    assert len(barest) <= DEEP_DECODE_MAX_CHARS
    assert _depth_and_leaf(_jsonb_decoder(barest)) == (16_300, 1), "the limit cuts a value Postgres can store"


def test_a_value_read_only_in_part_is_not_written_back() -> None:
    """Shallow enough for ``json.dumps``, it would store what was read in place of the whole: refused."""
    level = '{"p": ' + json.dumps("x" * 500) + ', "a": ['
    text = level * LEVELS + '"leaf"' + "]}" * LEVELS
    read = _jsonb_decoder(text)
    assert json.dumps(TOO_LONG_TO_READ) in json.dumps(read), "the value was read whole: nothing to refuse"
    with pytest.raises(ValueError, match="read only in part"):
        _jsonb_encoder(read)
    assert _jsonb_encoder({"a": [1, "x"]}) == '{"a": [1, "x"]}', "an ordinary value is written as before"
