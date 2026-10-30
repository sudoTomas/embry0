"""Some keys of one section, written into the stored text (:func:`patch_section`, :func:`with_keys`).

Pure functions, no database: the layouts a hand-written document has, and
the problems a change of keys is refused for before its text is touched.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from embry0.settings.document import (
    MAX_TEXT_BYTES,
    DocumentInvalid,
    DocumentTooLarge,
    load_yaml,
    parse_keys,
    patch_section,
    with_keys,
)
from tests.fixtures.settings_sections import LAYERED_CATALOG, TEST_CATALOG

REWRITTEN = "# Written again by embry0, which could not keep this document's comments.\n"


def _keys(text: str, section: str, keys: dict[str, object]) -> tuple[str, list[str]]:
    """``with_keys`` over a stored text, and the warnings it logged."""
    with capture_logs() as logs:
        out = with_keys(text, load_yaml(text), section, keys)
    return out, [entry["event"] for entry in logs if entry["log_level"] == "warning"]


# ---- the text: each layout keeps what is around the key ------------------------------------


@pytest.mark.parametrize(
    ("text", "keys", "expected"),
    [
        pytest.param(
            "sample:\n  per_tick: 4  # was 3\n  mode: shadow\n",
            {"per_tick": 6},
            "sample:\n  per_tick: 6  # was 3\n  mode: shadow\n",
            id="a value on its line, the comment after it kept",
        ),
        pytest.param(
            "sample:\n  per_tick:\n  mode: shadow\n",
            {"per_tick": 6},
            "sample:\n  per_tick: 6\n  mode: shadow\n",
            id="a key written with no value",
        ),
        pytest.param(
            "sample:\n  mode: shadow  # c\n",
            {"mode": "true"},
            "sample:\n  mode: 'true'  # c\n",
            id="text that would read as a switch is quoted",
        ),
        pytest.param(
            "sample:\n  labels: []  # none yet\n",
            {"labels": ["a", "b c"]},
            "sample:\n  labels: [a, b c]  # none yet\n",
            id="a list on one line stays on one line",
        ),
        pytest.param(
            "sample:\n  labels:\n    - a  # first\n    - b\n\n  # about the zone\n  zone: UTC\n",
            {"labels": ["x"]},
            "sample:\n  labels:\n  - x\n\n  # about the zone\n  zone: UTC\n",
            id="a block list is replaced with its entry, the comment below it kept",
        ),
        pytest.param(
            "sample:\n  zone: |\n    UTC\n\n  # about per_tick\n  per_tick: 4\n",
            {"zone": "Europe/Berlin"},
            "sample:\n  zone: Europe/Berlin\n\n  # about per_tick\n  per_tick: 4\n",
            id="a block text is replaced with its entry, the comment below it kept",
        ),
        pytest.param(
            "# top\nsample:\n  # about per_tick\n  per_tick: 4  # was 3\n  mode: 'on'\nother:\n  limit: 2\n",
            {"per_tick": None},
            "# top\nsample:\n  # about per_tick\n  mode: 'on'\nother:\n  limit: 2\n",
            id="a key taken out loses its own line only",
        ),
        pytest.param(
            "sample:\n  labels:\n    - a\n    - b\n  # about the zone\n  zone: UTC\n",
            {"labels": None},
            "sample:\n  # about the zone\n  zone: UTC\n",
            id="a block list taken out loses all its lines",
        ),
        pytest.param(
            "sample:\n  per_tick: 4\nother:\n  limit: 2\n",
            {"per_tick": None},
            "sample:\nother:\n  limit: 2\n",
            id="the last key taken out leaves the section's own key",
        ),
        pytest.param(
            "sample:\n  per_tick: 4\n\n# the second\nother:\n  limit: 2\n",
            {"mode": "on"},
            "sample:\n  per_tick: 4\n  mode: 'on'\n\n# the second\nother:\n  limit: 2\n",
            id="a new key goes at the end of its section, above the next one's comment",
        ),
        pytest.param(
            "sample: ~\nother:\n  limit: 2\n",
            {"per_tick": 3},
            "sample:\n  per_tick: 3\nother:\n  limit: 2\n",
            id="a section written as null becomes a block",
        ),
        pytest.param(
            "# top\nother:\n  limit: 2",
            {"per_tick": 3, "mode": None},
            "# top\nother:\n  limit: 2\n\nsample:\n  per_tick: 3\n",
            id="a section the text does not hold is appended",
        ),
        pytest.param(
            "sample:\n  per_tick: 4\n",
            {"per_tick": None, "mode": "off"},
            "sample:\n  mode: 'off'\n",
            id="a key taken out and one added",
        ),
        pytest.param(
            "sample:\r\n  per_tick: 4  # c\r\n",
            {"per_tick": 9, "mode": "on"},
            "sample:\r\n  per_tick: 9  # c\r\n  mode: 'on'\r\n",
            id="the document's own line ending is kept",
        ),
    ],
)
def test_the_change_is_written_where_the_key_stands(text: str, keys: dict[str, object], expected: str) -> None:
    out, warnings = _keys(text, "sample", keys)
    assert out == expected, f"the change did not keep the text around the key:\n{out!r}"
    assert warnings == [], f"the text was written again although it could be kept: {warnings}"


@pytest.mark.parametrize(
    ("text", "keys"),
    [
        pytest.param("sample:\n  per_tick: 4  # c\n", {"per_tick": 4}, id="the same value"),
        pytest.param("sample:\n  per_tick: 4  # c\n", {"mode": None}, id="a key taken out that is not written"),
        pytest.param("other:\n  limit: 2\n", {"mode": None}, id="only keys taken out of a section not written"),
        pytest.param("sample:\n  per_tick: 4\n", {}, id="no key"),
        pytest.param('sample:\n  mode: "on"  # c\n', {"mode": "on"}, id="the same text, quoted another way"),
        pytest.param("sample:\n  labels:\n    - a  # first\n", {"labels": ["a"]}, id="the same list, on its lines"),
    ],
)
def test_a_change_that_changes_nothing_leaves_the_text_as_it_is(text: str, keys: dict[str, object]) -> None:
    out, warnings = _keys(text, "sample", keys)
    assert (out, warnings) == (text, []), f"a change of nothing rewrote the text: {out!r}"


def test_a_value_of_another_type_is_written_though_it_compares_equal() -> None:
    """``1 == True`` in Python; the stored value is still 1, so the change must be written."""
    out, _ = _keys("sample:\n  enabled: 1  # c\n", "sample", {"enabled": True})
    assert out == "sample:\n  enabled: true  # c\n"


def test_text_on_several_lines_is_written_as_its_entry() -> None:
    out, warnings = _keys("sample:\n  zone: UTC  # c\n  per_tick: 4\n", "sample", {"zone": "a\nb"})
    assert load_yaml(out) == {"sample": {"zone": "a\nb", "per_tick": 4}}, out
    assert warnings == []


def test_a_flow_style_section_is_written_again_and_says_so() -> None:
    """The rule ``with_section`` follows: keys go into a block, never into a flow mapping."""
    text = "sample: {per_tick: 4}  # flow\nother:\n  limit: 2\n"
    out, warnings = _keys(text, "sample", {"per_tick": 6, "mode": None})
    assert out == REWRITTEN + "sample:\n  per_tick: 6\nother:\n  limit: 2\n"
    assert warnings == ["settings_text_rewritten"], "a text written again must be logged"


def test_written_again_a_section_left_empty_is_left_out() -> None:
    out, _ = _keys("sample: {per_tick: 4}\nother:\n  limit: 2\n", "sample", {"per_tick": None})
    assert out == REWRITTEN + "other:\n  limit: 2\n"


# ---- what a change of keys is refused for ---------------------------------------------------------


def _problems(exc: pytest.ExceptionInfo[DocumentInvalid]) -> list[tuple[tuple[object, ...], str]]:
    return [(p.loc, p.type) for p in exc.value.problems]


@pytest.mark.parametrize(
    ("section", "message"),
    [
        ("sampel", "'sampel' is not a settings section. Did you mean 'sample'?"),
        ("managed_by", "'managed_by' is a key of the document, not a section: change it in the whole document."),
        ("version", "'version' is a key of the document, not a section: change it in the whole document."),
    ],
)
def test_a_name_that_is_no_section_is_refused(section: str, message: str) -> None:
    with pytest.raises(DocumentInvalid) as refused:
        patch_section(TEST_CATALOG, section, {"per_tick": 5}, stored={})
    assert [(p.loc, p.type, p.msg) for p in refused.value.problems] == [((section,), "unknown_section", message)]


def test_every_problem_is_named_at_once_and_none_echoes_its_value() -> None:
    with pytest.raises(DocumentInvalid) as refused:
        patch_section(
            TEST_CATALOG,
            "sample",
            {"per_tik": None, "per_tick": 50, "remind_hours": float("nan"), "zone": "s3cret-ish/zone"},
            stored={},
        )
    assert sorted(_problems(refused)) == [
        (("sample", "per_tick"), "less_than_equal"),
        (("sample", "per_tik"), "extra_forbidden"),
        (("sample", "remind_hours"), "finite_number"),
        (("sample", "zone"), "value_error"),
    ], "each bad key, a key taken out included, must be named in one refusal"
    assert "s3cret-ish" not in str(refused.value), "a refused value was echoed"


def test_a_layer_may_not_take_out_a_key_it_may_not_set_unless_it_holds_one() -> None:
    with pytest.raises(DocumentInvalid) as refused:
        patch_section(TEST_CATALOG, "sample", {"mode": None}, stored={}, layer="organization", inherited={})
    assert _problems(refused) == [(("sample", "mode"), "not_overridable")]
    # A release that stopped letting the layer set it left one stored: taking it out is how to be rid of it.
    stored = {"sample": {"mode": "on"}}
    text, checked = patch_section(
        TEST_CATALOG,
        "sample",
        {"mode": None},
        stored=stored,
        stored_text="sample:\n  mode: 'on'\n",
        layer="organization",
        inherited={},
    )
    assert (text, checked.data) == ("sample:\n", {})


def test_a_layer_change_is_checked_with_what_it_inherits() -> None:
    with pytest.raises(DocumentInvalid) as refused:
        patch_section(
            LAYERED_CATALOG, "window", {"low": 70}, stored={}, layer="organization", inherited={"window": {"high": 50}}
        )
    assert _problems(refused) == [(("window",), "conflicts_with_inherited")]


def test_the_change_answers_its_text_and_the_values_it_stores() -> None:
    stored_text = "# keep me\nsample:\n  per_tick: 4\n"
    text, checked = patch_section(
        TEST_CATALOG, "sample", {"remind_hours": 2}, stored={"sample": {"per_tick": 4}}, stored_text=stored_text
    )
    assert text == "# keep me\nsample:\n  per_tick: 4\n  remind_hours: 2\n"
    assert checked.data == {"sample": {"per_tick": 4, "remind_hours": 2.0}}, "the values are the checked ones"


def test_a_number_the_section_reads_as_the_one_stored_is_left_as_written() -> None:
    """``12`` for a number stored as ``12.0`` changes no setting: the text, its comment included, stays."""
    stored_text = "sample:\n  remind_hours: 12.0  # half a day\n"
    stored = {"sample": {"remind_hours": 12.0}}
    text, checked = patch_section(TEST_CATALOG, "sample", {"remind_hours": 12}, stored=stored, stored_text=stored_text)
    assert text == stored_text, f"a value equal to the stored one was written again: {text!r}"
    assert checked.data == stored
    changed, _ = patch_section(TEST_CATALOG, "sample", {"remind_hours": 12.5}, stored=stored, stored_text=stored_text)
    assert changed == "sample:\n  remind_hours: 12.5  # half a day\n", "another value must still be written"


def test_a_change_that_makes_the_document_too_large_is_refused() -> None:
    stored_text = "# " + "x" * (MAX_TEXT_BYTES - 100) + "\nsample:\n  per_tick: 4\n"
    with pytest.raises(DocumentTooLarge):
        patch_section(
            TEST_CATALOG, "sample", {"labels": ["y" * 200]}, stored={"sample": {"per_tick": 4}}, stored_text=stored_text
        )


# ---- the body -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        (b"\xff", "encoding"),
        (b"[]", "not_a_mapping"),
        (b'{"a": 1, "a": 2}', "duplicate_key"),
        (b"{a", "json_syntax"),
    ],
)
def test_a_body_is_one_json_object(body: bytes, kind: str) -> None:
    with pytest.raises(DocumentInvalid) as refused:
        parse_keys(body)
    assert [(p.loc, p.type) for p in refused.value.problems] == [((), kind)]


def test_an_empty_body_names_no_key_and_a_body_too_large_is_refused() -> None:
    assert parse_keys(b"") == {} and parse_keys(b" {} ") == {}
    assert parse_keys(b'{"per_tick": null}') == {"per_tick": None}
    with pytest.raises(DocumentTooLarge):
        parse_keys(b" " * (MAX_TEXT_BYTES + 1))
