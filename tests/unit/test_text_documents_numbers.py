"""The number checks every operator-written document shares (#424): a whole number too long to convert, and a
number, true or false or date YAML cannot read, each at its location, never raising."""

from __future__ import annotations

import pytest
import yaml

from embry0.text_documents import load_json, structure_problems

LONG = "9" * 5_000


def test_load_json_reports_each_long_number_at_its_place_and_holds_none_there() -> None:
    value, problems = load_json(f'{{"a": [1, {LONG}], "b": {{"c": {LONG}}}, "d": 2}}')
    assert [(p.loc, p.type) for p in problems] == [(("a", 1), "number_too_long"), (("b", "c"), "number_too_long")]
    assert value == {"a": [1, None], "b": {"c": None}, "d": 2}, "no marker of the decoder is left in the value"


def test_load_json_reports_a_long_number_that_is_the_whole_document() -> None:
    value, problems = load_json(LONG)
    assert (value, [(p.loc, p.type) for p in problems]) == (None, [((), "number_too_long")])


@pytest.mark.parametrize(
    ("text", "found"),
    [
        (f"a: [1, {LONG}]", [(("a", 1), "number_too_long")]),
        (f"a: [[{LONG}]]", [(("a", 0, 0), "number_too_long")]),
        ("a: [1, 0x_]", [(("a", 1), "invalid_number")]),
        ("a: {b: !!float x}", [(("a", "b"), "invalid_number")]),
        ("a: [true, !!bool maybe]", [(("a", 1), "invalid_value")]),
        ("a: [1, 2.5, true, 2026-01-01, '0x_', ~]", []),
    ],
    ids=["a long number in a list", "in a nested list", "0x_ in a list", "!!float x", "!!bool maybe", "all readable"],
)
def test_structure_problems_reads_each_typed_scalar_wherever_it_sits(text: str, found: list[tuple]) -> None:
    problems = structure_problems(yaml.compose(text, Loader=yaml.SafeLoader))
    assert [(p.loc, p.type) for p in problems] == found
