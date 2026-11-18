"""The JSONB codec reads a number past a ``float``'s range as its text, not as ``inf`` (#537).

Postgres stores a JSONB number in full and writes it in plain notation, with no
exponent. ``json.loads`` reads a number with a fraction by ``float()``, which
gives ``inf`` for one past about ``1.8e308``, 309 integer digits or more, and
raises nothing. JSON cannot write ``inf``: a route with no response model
answered 500, and one with a response model served the number as ``null``.
"""

from __future__ import annotations

import json
import math
import sys
from typing import Any

import pytest

from embry0.storage.database import _jsonb_decoder, _loads_deep
from tests.unit.storage.test_jsonb_decoder_deep import _deep_text, _depth_and_leaf, _same

PAST = "1" + "0" * 400 + ".5"  # 1e400 + 0.5, as Postgres writes it
FITS = "1" + "0" * 308 + ".5"  # 1e308 + 0.5: a float holds it
JUST_PAST = "2" + "0" * 308 + ".5"  # 2e308 + 0.5: as many digits, past the largest float
TOO_LONG = "1" + "0" * sys.get_int_max_str_digits()  # #523's integer past int's digit limit
LARGEST = f"{int(sys.float_info.max)}.0"  # the largest float, as Postgres writes it with a fraction


def test_the_interpreter_reads_such_a_number_as_inf() -> None:
    assert json.loads(f'{{"n": {PAST}}}') == {"n": math.inf}, "the behaviour this is about"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f'{{"n": {PAST}, "m": 1, "x": 2.5}}', {"n": PAST, "m": 1, "x": 2.5}),
        (f'{{"n": -{PAST}}}', {"n": f"-{PAST}"}),
        (f'[1, [{PAST}], {{"k": [true, null, "s", 0.25]}}]', [1, [PAST], {"k": [True, None, "s", 0.25]}]),
        (PAST, PAST),
    ],
    ids=["beside-others", "negative", "nested", "alone"],
)
def test_a_number_past_a_floats_range_is_read_as_its_text(text: str, expected: Any) -> None:
    value = _jsonb_decoder(text)
    assert _same(value, expected), f"the number is not read as its text, or the rest changes: {str(value)[:80]}"
    json.dumps(value, allow_nan=False)  # what a route writes: it raised for inf


def test_a_number_a_float_holds_is_still_a_float() -> None:
    value = _jsonb_decoder(f'{{"fits": {FITS}, "past": {JUST_PAST}, "max": {LARGEST}, "min": -{LARGEST}}}')
    assert type(value["fits"]) is float and value["fits"] == 1e308, "a number a float holds stays a float"
    assert (value["max"], value["min"]) == (sys.float_info.max, -sys.float_info.max), "the largest float stays one"
    assert value["past"] == JUST_PAST, "a number of as many digits past the largest float is read as its text"


def test_the_stack_based_reader_reads_one_as_its_text_too() -> None:
    assert _same(_loads_deep(f'{{"n": {PAST}, "m": [-{PAST}, 2.5]}}'), {"n": PAST, "m": [f"-{PAST}", 2.5]})


def test_a_value_too_deep_for_json_loads_holding_one_is_read() -> None:
    deep = _deep_text(12_000)
    for text in (f"[{PAST}, {deep}]", f"[{deep}, {PAST}]"):
        value = _jsonb_decoder(text)
        past, other = (value[0], value[1]) if isinstance(value[0], str) else (value[1], value[0])
        assert past == PAST and _depth_and_leaf(other) == (12_000, "leaf é"), text[:40]


def test_a_value_holding_one_and_an_integer_past_ints_limit_is_read() -> None:
    assert _same(_jsonb_decoder(f"[{TOO_LONG}, {PAST}, 1.5, 7]"), [TOO_LONG, PAST, 1.5, 7])


@pytest.mark.parametrize("text", [f'{{"n": {PAST}', f"[{PAST}, ]", f'{{"n": {PAST}}} x'])
def test_malformed_text_with_such_a_number_is_still_refused(text: str) -> None:
    with pytest.raises(ValueError):
        _jsonb_decoder(text)
