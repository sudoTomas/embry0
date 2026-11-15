"""The JSONB codec reads an integer longer than ``int`` reads (#523).

Python refuses to turn more than 4,300 digits into an ``int``
(``sys.get_int_max_str_digits()``), and ``json.loads`` raised that
``ValueError`` for the whole value, so one such number in a row made every
read of it fail. The codec now reads such an integer as its digits, a ``str``,
in a second pass only a value holding one takes.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest

from embry0.storage import database
from embry0.storage.database import _jsonb_decoder, _loads_deep
from tests.unit.storage.test_jsonb_decoder_deep import _deep_text, _depth_and_leaf, _same

LIMIT = sys.get_int_max_str_digits()
TOO_LONG = "1" + "0" * LIMIT  # one digit past the limit: 1e4300 as Postgres writes it
LONGEST = "9" * LIMIT


def test_the_interpreter_refuses_the_integer_it_is_about() -> None:
    assert LIMIT == 4300, "the default limit since Python 3.11"
    with pytest.raises(ValueError, match="Exceeds the limit"):
        json.loads(f'{{"n": {TOO_LONG}}}')


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f'{{"n": {TOO_LONG}, "m": 1, "x": 2.5}}', {"n": TOO_LONG, "m": 1, "x": 2.5}),
        (f'{{"n": -{TOO_LONG}}}', {"n": f"-{TOO_LONG}"}),
        (f'[1, [{TOO_LONG}], {{"k": [true, null, "s"]}}]', [1, [TOO_LONG], {"k": [True, None, "s"]}]),
        (TOO_LONG, TOO_LONG),
    ],
    ids=["beside-others", "negative", "nested", "alone"],
)
def test_an_integer_past_the_digit_limit_is_read_as_its_digits(text: str, expected: Any) -> None:
    assert _same(_jsonb_decoder(text), expected), "the value is not read, or the rest of it changes"


def test_an_integer_at_the_limit_is_still_an_int() -> None:
    value = _jsonb_decoder(f'{{"n": {LONGEST}, "m": {TOO_LONG}}}')
    assert type(value["n"]) is int and value["n"] == int(LONGEST), "an integer int can read stays an int"
    assert value["m"] == TOO_LONG


def test_the_stack_based_reader_reads_one_as_its_digits_too() -> None:
    assert _same(_loads_deep(f'{{"n": {TOO_LONG}, "m": [{LONGEST}, -0]}}'), {"n": TOO_LONG, "m": [int(LONGEST), 0]})


def test_a_value_both_too_deep_and_too_long_is_read() -> None:
    """Whichever ``json.loads`` meets first, the depth or the integer, the value is read whole."""
    deep = _deep_text(12_000)
    for text in (f"[{TOO_LONG}, {deep}]", f"[{deep}, {TOO_LONG}]"):
        value = _jsonb_decoder(text)
        long_one = value[0] if isinstance(value[0], str) else value[1]
        deep_one = value[1] if isinstance(value[0], str) else value[0]
        assert long_one == TOO_LONG and _depth_and_leaf(deep_one) == (12_000, "leaf é"), text[:40]


@pytest.mark.parametrize("text", [f'{{"n": {TOO_LONG}', f"[{TOO_LONG}, ]", f'{{"n": {TOO_LONG}}} x'])
def test_malformed_text_with_such_an_integer_is_still_refused(text: str) -> None:
    with pytest.raises(ValueError):
        _jsonb_decoder(text)


def test_an_ordinary_value_is_read_by_one_plain_json_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    """The common path is the call it always was: no second pass, no ``parse_int`` callback slowing it."""
    calls: list[dict[str, Any]] = []
    loads = json.loads

    def counted(text: str, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return loads(text, **kwargs)

    monkeypatch.setattr(database.json, "loads", counted)
    assert _jsonb_decoder('{"a": [1, 2.5, "x", null], "n": 12345678901234567890}')["n"] == 12345678901234567890
    assert calls == [{}], f"an ordinary value is read differently: {calls}"
    calls.clear()
    _jsonb_decoder(f'{{"n": {TOO_LONG}}}')
    assert len(calls) == 2 and "parse_int" in calls[1], f"only a value holding such an integer is read again: {calls}"
    calls.clear()
    with pytest.raises(json.JSONDecodeError):
        _jsonb_decoder('{"n": 1')
    assert calls == [{}], f"text that is not JSON is not read again: {calls}"
