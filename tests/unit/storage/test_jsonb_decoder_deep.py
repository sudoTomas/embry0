"""The JSONB codec decodes a value of any depth Postgres stores (#479).

``json.loads`` recurses once per level and raised ``RecursionError`` on a value
nested past about 10,000 levels, on every read of its row. The codec now falls
back to ``_loads_deep``, which builds the same value with a stack of its own.
"""

from __future__ import annotations

import json
import random
from typing import Any

import pytest

from embry0.storage.database import _jsonb_decoder, _loads_deep


def _deep_text(levels: int) -> str:
    """A value ``levels`` deep, objects and arrays in turn, with a leaf at the bottom: the text Postgres sends."""
    opening = "".join('{"a": ' if level % 2 == 0 else "[1, " for level in range(levels))
    closing = "".join("}" if level % 2 == 0 else "]" for level in reversed(range(levels)))
    return opening + '"leaf \\u00e9"' + closing


def _depth_and_leaf(value: Any) -> tuple[int, Any]:
    depth = 0
    while isinstance(value, (dict, list)):
        value = value["a"] if isinstance(value, dict) else value[1]
        depth += 1
    return depth, value


def _same(a: Any, b: Any) -> bool:
    """Equal, and of one type at every level: ``1 == 1.0`` and ``True == 1``, but they are not the same JSON."""
    pairs = [(a, b)]
    while pairs:
        x, y = pairs.pop()
        if type(x) is not type(y):
            return False
        if isinstance(x, dict):
            if list(x) != list(y):
                return False
            pairs.extend((x[key], y[key]) for key in x)
        elif isinstance(x, list):
            if len(x) != len(y):
                return False
            pairs.extend(zip(x, y, strict=True))
        elif x != y:
            return False
    return True


def test_same_tells_apart_what_equality_does_not() -> None:
    assert not _same(1, 1.0) and not _same([True], [1]) and not _same({"a": 1, "b": 2}, {"b": 2, "a": 1})
    assert _same({"a": [1, 2.5, "x", None]}, {"a": [1, 2.5, "x", None]})


def _random_value(rng: random.Random, depth: int = 0) -> Any:
    kind = rng.randrange(8 if depth < 6 else 6)
    if kind == 0:
        return rng.choice([True, False, None])
    if kind == 1:
        return rng.randint(-(10**20), 10**20)
    if kind == 2:
        return rng.choice([0.0, -1.5, 3.25e-7, 6.02e23, 1e300, -0.0001, 1.0, -2.0, 100.0])
    if kind in (3, 4, 5):
        alphabet = 'ab "\\\\/\b\f\n\r\té中\U0001f600\x01'
        return "".join(rng.choice(alphabet) for _ in range(rng.randrange(12)))
    if kind == 6:
        return [_random_value(rng, depth + 1) for _ in range(rng.randrange(4))]
    return {f"k{rng.randrange(5)} \\u00e9": _random_value(rng, depth + 1) for _ in range(rng.randrange(4))}


@pytest.mark.parametrize("compact", [True, False], ids=["compact", "spaced"])
def test_it_reads_what_json_loads_reads(compact: bool) -> None:
    rng = random.Random(479)
    for _ in range(2_000):
        value = _random_value(rng)
        text = json.dumps(value, separators=(",", ":") if compact else (", ", ": "), ensure_ascii=rng.random() < 0.5)
        assert _same(_loads_deep(text), json.loads(text)), text


@pytest.mark.parametrize("levels", [10_000, 19_000])
def test_a_value_deeper_than_json_loads_goes_is_decoded(levels: int) -> None:
    text = _deep_text(levels)
    with pytest.raises(RecursionError):
        json.loads(text)
    assert _depth_and_leaf(_jsonb_decoder(text)) == (levels, "leaf é")
    assert _same(_loads_deep('{"a": [1, 1.0, true, null]}'), {"a": [1, 1.0, True, None]})


@pytest.mark.parametrize(
    "text",
    [
        "1\u0661",
        "[\u0661]",
        "1.\u0661",
        '{"a": 1]',
        "[1}",
        '{"a": 1',
        "[1, 2",
        '{"a" 1}',
        '{"a": 1,}',
        "[1 2]",
        "tru",
        '{"a": 1}]',
        '"open',
        "[1] x",
        "",
    ],
)
def test_text_that_is_not_json_is_refused_as_json_loads_refuses_it(text: str) -> None:
    with pytest.raises(ValueError):
        json.loads(text)
    with pytest.raises(ValueError):
        _loads_deep(text)
