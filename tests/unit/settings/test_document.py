"""A settings document is checked as a whole and every problem names its key (spec 2026-10-28, #160)."""

from __future__ import annotations

from typing import Any

import pytest
from structlog.testing import capture_logs

from embry0.settings.document import (
    MAX_TEXT_BYTES,
    NO_NEWLINE,
    DocumentInvalid,
    DocumentTooLarge,
    changes,
    check,
    load_yaml,
    parse_body,
    read_sections,
    text_diff,
    with_section,
)
from embry0.settings.registry import Catalog
from tests.fixtures.settings_sections import BRITTLE, SAMPLE, TEST_CATALOG


def _problems(text: str, stored: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with pytest.raises(DocumentInvalid) as caught:
        check(TEST_CATALOG, load_yaml(text), stored=stored or {})
    return [p.to_dict() for p in caught.value.problems]


def _locs(text: str) -> list[tuple[Any, ...]]:
    return [tuple(p["loc"]) for p in _problems(text)]


# ---- #160 acceptance: an unknown key, a wrong type and an out-of-range value each name the key ----


def test_an_unknown_key_is_refused_naming_it_with_a_near_miss() -> None:
    problems = _problems("sample:\n  per_tik: 4\n")
    assert [(p["loc"], p["type"]) for p in problems] == [(["sample", "per_tik"], "extra_forbidden")]
    assert problems[0]["msg"] == "'per_tik' is not a setting of 'sample'. Did you mean 'per_tick'?"


def test_a_wrong_type_is_refused_naming_the_key() -> None:
    problems = _problems("sample:\n  enabled: maybe\n")
    assert [(p["loc"], p["type"]) for p in problems] == [(["sample", "enabled"], "bool_type")]


def test_an_out_of_range_value_is_refused_naming_the_key() -> None:
    problems = _problems("sample:\n  per_tick: 50\n")
    assert [(p["loc"], p["type"]) for p in problems] == [(["sample", "per_tick"], "less_than_equal")]


def test_one_document_with_every_kind_of_problem_names_each_one() -> None:
    text = "sample:\n  per_tik: 4\n  enabled: maybe\n  per_tick: 50\n  zone: Mars/Base\nother:\n  limit: -1\nsampel: {per_tick: 1}\n"
    assert sorted(_locs(text)) == sorted(
        [
            ("sample", "per_tik"),
            ("sample", "enabled"),
            ("sample", "per_tick"),
            ("sample", "zone"),
            ("other", "limit"),
            ("sampel",),
        ]
    ), "every problem of every section is reported at once"


def test_a_problem_never_echoes_the_rejected_value() -> None:
    problems = _problems("sample:\n  zone: hunter2-not-a-zone-but-maybe-a-secret\n  per_tick: 'abc123'\n")
    assert [tuple(p["loc"]) for p in problems] == [("sample", "per_tick"), ("sample", "zone")]
    assert all(set(p) == {"loc", "type", "msg"} for p in problems)
    for value in ("abc123", "hunter2-not-a-zone-but-maybe-a-secret"):
        assert not any(value in p["msg"] for p in problems), f"the rejected value {value!r} is in a message"


def test_text_is_not_a_number_and_a_word_is_not_a_boolean() -> None:
    assert sorted(_locs("sample:\n  per_tick: '5'\n  enabled: 'true'\n")) == [
        ("sample", "enabled"),
        ("sample", "per_tick"),
    ]


def test_a_bare_on_read_as_true_by_yaml_is_explained() -> None:
    (problem,) = _problems("sample:\n  mode: on\n")
    assert problem["loc"] == ["sample", "mode"]
    assert "put the word in quotes" in problem["msg"], problem["msg"]


def test_an_unknown_section_is_refused_with_a_near_miss() -> None:
    problems = _problems("sampel:\n  per_tick: 4\n")
    assert problems == [
        {
            "loc": ["sampel"],
            "type": "unknown_section",
            "msg": "'sampel' is not a settings section. Did you mean 'sample'?",
        }
    ]


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("sample: [1, 2]\n", "section_type"),
        ("sample:\n  zone: 2026-10-28\n", "date_value"),
        ("version: 2\n", "version_newer"),
        ("version: '1'\n", "int_type"),
    ],
)
def test_a_value_that_is_not_a_setting_is_refused(text: str, kind: str) -> None:
    assert [p["type"] for p in _problems(text)] == [kind]


def test_a_yaml_error_names_its_line() -> None:
    with pytest.raises(DocumentInvalid) as caught:
        load_yaml("sample:\n  per_tick: [1\n")
    (problem,) = caught.value.problems
    assert (problem.loc, problem.type) == ((), "yaml_syntax")
    assert "line 3" in problem.msg, problem.msg


def test_an_alias_is_refused() -> None:
    with pytest.raises(DocumentInvalid, match="Aliases"):
        load_yaml("a: &x [1, 2]\nb: *x\n")


def test_a_document_that_is_not_a_mapping_is_refused() -> None:
    with pytest.raises(DocumentInvalid, match="mapping of sections"):
        load_yaml("- sample\n")


def test_a_document_past_the_size_limit_is_refused() -> None:
    with pytest.raises(DocumentTooLarge):
        parse_body(b"#" * (MAX_TEXT_BYTES + 1), "application/yaml")


# ---- what is stored ---------------------------------------------------------------


def test_only_written_keys_are_stored_in_their_normal_form() -> None:
    checked = check(
        TEST_CATALOG, load_yaml("# a comment\nsample:\n  remind_hours: 12\n  labels: [a]\nother:\n"), stored={}
    )
    assert checked.data == {"sample": {"remind_hours": 12.0, "labels": ["a"]}}, (
        "a document is sparse: a key nobody wrote stays out, so a changed default reaches it"
    )
    assert isinstance(checked.data["sample"]["remind_hours"], float)
    assert checked.schema_version == 1


def test_an_empty_document_is_every_default() -> None:
    assert check(TEST_CATALOG, load_yaml("# nothing set\n"), stored={}).data == {}


def test_a_json_object_body_has_no_text_until_the_store_writes_one() -> None:
    text, values = parse_body(b'{"sample": {"per_tick": 4}}', "application/json; charset=utf-8")
    assert (text, values) == (None, {"sample": {"per_tick": 4}})
    with pytest.raises(DocumentInvalid, match="object of sections"):
        parse_body(b"[1]", "application/json")


def test_a_section_this_code_does_not_know_is_kept_only_as_stored() -> None:
    stored = {"retired": {"x": 1}}
    kept = check(TEST_CATALOG, load_yaml("retired:\n  x: 1\n"), stored=stored, stored_text="retired:\n  x: 1\n")
    assert (kept.data, kept.unknown_sections) == (stored, ("retired",))
    assert _locs("retired:\n  x: 2\n") == [("retired",)], "changing a section nobody knows is refused"


def test_a_section_kept_as_stored_is_compared_with_the_stored_text_not_its_normal_form() -> None:
    """A newer release stored ``08-17`` for the ``8-17`` its text says; the text is what an operator sends back."""
    text = "# from a newer release\nlater:\n  hours: 8-17\nempty:\n"
    kept = check(TEST_CATALOG, load_yaml(text), stored={"later": {"hours": "08-17"}}, stored_text=text)
    assert kept.data == {"later": {"hours": "08-17"}}, "the stored values are kept"
    assert kept.unknown_sections == ("empty", "later")


def test_a_key_a_known_section_does_not_have_is_kept_only_as_stored() -> None:
    text = "sample:\n  per_tick: 4\n  burst: 2\n"
    stored = {"sample": {"per_tick": 4, "burst": 2}}
    kept = check(TEST_CATALOG, load_yaml(text.replace("4", "5")), stored=stored, stored_text=text)
    assert (kept.data, kept.unknown_keys) == ({"sample": {"per_tick": 5, "burst": 2}}, ("sample.burst",))
    with pytest.raises(DocumentInvalid) as caught:
        check(TEST_CATALOG, load_yaml(text.replace("burst: 2", "burst: 3")), stored=stored, stored_text=text)
    assert [(tuple(p.loc), p.type) for p in caught.value.problems] == [(("sample", "burst"), "extra_forbidden")]
    sections = read_sections(TEST_CATALOG, stored)
    assert (sections.refused, sections.unknown_keys) == ((), ("sample.burst",)), "a key from a newer release is ignored"


# ---- reading back ----------------------------------------------------------------------


def test_a_stored_value_the_code_now_refuses_reads_as_its_default_and_keeps_its_siblings() -> None:
    sections = read_sections(TEST_CATALOG, {"sample": {"per_tick": 99, "remind_hours": 6.0}, "retired": {}})
    model = sections.models["sample"]
    assert (model.per_tick, model.remind_hours) == (3, 6.0), "only the refused key falls back to its default"  # type: ignore[attr-defined]
    assert [(r.section, r.key) for r in sections.refused] == [("sample", "per_tick")]
    assert sections.unknown == ("retired",)
    assert sections.models["other"] == TEST_CATALOG.get("other").defaults()  # type: ignore[union-attr]


def test_reading_back_what_was_stored_gives_the_same_values() -> None:
    checked = check(
        TEST_CATALOG, load_yaml("sample:\n  mode: 'on'\n  labels: [x, y]\n  zone: Europe/Berlin\n"), stored={}
    )
    model = read_sections(TEST_CATALOG, checked.data).models["sample"]
    assert model == SAMPLE.model(mode="on", labels=["x", "y"], zone="Europe/Berlin")


# ---- text --------------------------------------------------------------------------------


def test_a_section_is_appended_under_a_comment_and_every_comment_stays() -> None:
    text = "# kept\nother:\n  limit: 2  # also kept\n"
    out = with_section(text, {"other": {"limit": 2}}, "sample", {"per_tick": 4}, "Imported X.")
    assert out == "# kept\nother:\n  limit: 2  # also kept\n\n# Imported X.\nsample:\n  per_tick: 4\n"


def test_a_flow_style_document_is_written_again_and_says_its_comments_were_lost() -> None:
    out = with_section("{other: {limit: 2}}", {"other": {"limit": 2}}, "sample", {"per_tick": 4}, "Imported X.")
    assert out.startswith("# Written again by embry0, which could not keep this document's comments.\n")
    assert load_yaml(out) == {"other": {"limit": 2}, "sample": {"per_tick": 4}}


def test_changes_count_a_key_written_down_even_at_its_default() -> None:
    found = changes({"sample": {"per_tick": 4}}, {"sample": {"per_tick": 5, "mode": "shadow"}, "other": {}})
    assert [(c.path, c.before, c.after, c.set_before, c.set_after) for c in found] == [
        ("sample.per_tick", 4, 5, True, True),
        ("sample.mode", None, "shadow", False, True),
    ]


def test_an_empty_section_in_the_text_does_not_cost_the_comments() -> None:
    """``other:`` with nothing under it is stored as nothing, but is still in the text."""
    out = with_section("# kept\nother:\n", {}, "sample", {"per_tick": 4}, "Imported X.")
    assert out == "# kept\nother:\n\n# Imported X.\nsample:\n  per_tick: 4\n"


# ---- what a document may not hold, and checks that raise ----------------------------------


def _load_problems(text: str) -> list[tuple[tuple[Any, ...], str]] | str:
    """Each problem ``load_yaml`` refuses ``text`` with, ``[]`` when it loads, or what it raised instead."""
    try:
        load_yaml(text)
    except DocumentInvalid as exc:
        return [(tuple(p.loc), p.type) for p in exc.problems]
    except Exception as exc:  # noqa: BLE001 - the test reports what escaped
        return f"raised {type(exc).__name__}"
    return []


def test_a_section_written_twice_is_refused_not_merged_silently() -> None:
    assert _load_problems("sample:\n  per_tick: 1\nsample:\n  mode: 'on'\n") == [(("sample",), "duplicate_key")], (
        "YAML would keep only the second section, silently"
    )


def test_a_key_written_twice_in_a_section_is_refused() -> None:
    assert _load_problems("sample:\n  per_tick: 1\n  per_tick: 2\n") == [(("sample", "per_tick"), "duplicate_key")]


def test_a_document_nested_too_deeply_is_refused_not_a_crash() -> None:
    assert _load_problems("[" * 60_000) == [((), "too_deep")]


def test_the_nul_character_is_refused_in_yaml_and_in_json() -> None:
    from_yaml = load_yaml('sample:\n  labels: ["a\\0b"]\n')
    _, from_json = parse_body(b'{"sample": {"labels": ["a\\u0000b"]}}', "application/json")
    for raw in (from_yaml, from_json):
        try:
            check(TEST_CATALOG, raw, stored={})
            found: list[tuple[tuple[Any, ...], str]] = []
        except DocumentInvalid as exc:
            found = [(tuple(p.loc), p.type) for p in exc.problems]
        assert found == [(("sample", "labels", 0), "nul_character")], (
            "the NUL character passed the check, and Postgres refuses it in a stored value"
        )


def _brittle_outcome(raw: dict[str, Any]) -> list[tuple[tuple[Any, ...], str]] | str:
    try:
        check(Catalog((BRITTLE,)), raw, stored={})
    except DocumentInvalid as exc:
        return [(tuple(p.loc), p.type) for p in exc.problems]
    except Exception as exc:  # noqa: BLE001 - the test reports what escaped
        return f"raised {type(exc).__name__}"
    return []


def test_a_check_that_raises_keyerror_is_a_problem_naming_its_key() -> None:
    assert _brittle_outcome({"brittle": {"zone": "Mars/Base", "hours": 2}}) == [
        (("brittle", "zone"), "check_failed")
    ], "a validator's KeyError (zoneinfo's ZoneInfoNotFoundError) must not escape the check"


def test_reading_a_value_whose_check_raises_keyerror_falls_back_to_its_default() -> None:
    try:
        sections = read_sections(Catalog((BRITTLE,)), {"brittle": {"zone": "Mars/Base", "hours": 2.0}})
    except Exception as exc:  # noqa: BLE001 - the test reports what escaped
        pytest.fail(f"reading a stored value raised {type(exc).__name__}; a tick or a boot would stop")
    model = sections.models["brittle"]
    assert (model.zone, model.hours) == ("UTC", 2.0)  # type: ignore[attr-defined]
    assert [(r.section, r.key) for r in sections.refused] == [("brittle", "zone")]


def test_keys_for_a_section_the_text_holds_are_written_into_its_block_keeping_comments() -> None:
    text = "# top\nsample:\n  per_tick: 4  # kept\n\n# about other\nother:\n  limit: 2\n"
    out = with_section(
        text, {"sample": {"per_tick": 4}, "other": {"limit": 2}}, "sample", {"zone": "UTC"}, "Imported Z."
    )
    assert out == (
        "# top\nsample:\n  per_tick: 4  # kept\n  # Imported Z.\n  zone: UTC\n\n# about other\nother:\n  limit: 2\n"
    )


def test_an_empty_section_this_code_does_not_know_changes_nothing_and_is_reported() -> None:
    try:
        kept = check(TEST_CATALOG, load_yaml("sampel:\nother: {}\n"), stored={})
    except DocumentInvalid as exc:
        pytest.fail(f"an empty section holds no setting to refuse, but it was refused: {exc}")
    assert (kept.data, kept.unknown_sections) == ({}, ("sampel",))


def test_a_diff_keeps_each_line_apart_when_a_text_has_no_final_newline() -> None:
    diff = text_diff("a: 1\nb: 2", "a: 1\nb: 3")
    assert "-b: 2" in diff.splitlines() and "+b: 3" in diff.splitlines(), f"the last lines ran together: {diff!r}"


def test_a_diff_shows_a_change_of_the_final_newline_alone() -> None:
    assert text_diff("a: 1\n", "a: 1\n") == ""
    assert text_diff("a: 1\n", "a: 1").splitlines()[2:] == ["@@ -1 +1 @@", "-a: 1", "+a: 1", NO_NEWLINE]


def test_the_no_newline_marker_is_written_as_git_writes_it() -> None:
    """``git diff`` of the same two files, byte for byte: the marker has no prefix and is not counted."""
    assert text_diff("a: 1\nb: 2", "a: 1\nb: 3") == (
        "--- stored\n+++ proposed\n@@ -1,2 +1,2 @@\n a: 1\n-b: 2\n\\ No newline at end of file\n"
        "+b: 3\n\\ No newline at end of file\n"
    ), "the marker line is git's own, with no ' ', '-' or '+' before it"


# ---- a stored form is matched by type too, and in either of its forms ---------------------


def test_a_kept_value_changed_to_another_type_is_not_as_stored() -> None:
    text = "later:\n  flag: 1\nsample:\n  per_tick: 4\n  burst: 1\n"
    stored = {"later": {"flag": 1}, "sample": {"per_tick": 4, "burst": 1}}
    for changed in (text.replace("flag: 1", "flag: true"), text.replace("burst: 1", "burst: true")):
        try:
            check(TEST_CATALOG, load_yaml(changed), stored=stored, stored_text=text)
            found: list[tuple[Any, ...]] = []
        except DocumentInvalid as exc:
            found = [tuple(p.loc) for p in exc.problems]
        assert len(found) == 1, f"1 and true are equal in Python, but true is not what is stored: {changed!r}"


def test_a_kept_value_sent_back_in_its_stored_values_form_passes() -> None:
    """What a JSON client sends back from GET's ``data`` after a rollback: the newer release's normal form."""
    text = "later:\n  hours: 8-17\nsample:\n  per_tick: 4\n  start: 8\n"
    stored = {"later": {"hours": "08-17"}, "sample": {"per_tick": 4, "start": "08"}}
    raw = {"later": {"hours": "08-17"}, "sample": {"per_tick": 5, "start": "08"}}
    try:
        kept = check(TEST_CATALOG, raw, stored=stored, stored_text=text)
    except DocumentInvalid as exc:
        pytest.fail(f"the stored values' own form was refused after a rollback: {exc}")
    assert kept.data == {"later": {"hours": "08-17"}, "sample": {"per_tick": 5, "start": "08"}}


# ---- writing into a section's text ------------------------------------------------------------


def test_a_crlf_document_keeps_its_line_endings() -> None:
    text = "# top\r\nsample:\r\n  per_tick: 4\r\nother:\r\n  limit: 2\r\n"
    out = with_section(text, {"sample": {"per_tick": 4}, "other": {"limit": 2}}, "sample", {"zone": "UTC"}, "I.")
    assert "\n" not in out.replace("\r\n", ""), f"line endings were mixed: {out!r}"
    assert out == "# top\r\nsample:\r\n  per_tick: 4\r\n  # I.\r\n  zone: UTC\r\nother:\r\n  limit: 2\r\n"


def test_keys_go_above_the_comment_that_follows_a_block_scalar() -> None:
    text = "sample:\n  note: |\n    hello\n    # part of the note\n\n# about other\nother:\n  limit: 2\n"
    out = with_section(text, {}, "sample", {"zone": "UTC"}, "I.")
    assert out == (
        "sample:\n  note: |\n    hello\n    # part of the note\n  # I.\n  zone: UTC\n\n# about other\nother:\n  limit: 2\n"
    ), f"the keys went below the comment about the next section: {out!r}"


@pytest.mark.parametrize("empty", ["~", "null", "{}"])
def test_a_section_written_as_nothing_becomes_a_block_keeping_comments(empty: str) -> None:
    text = f"# top\nsample: {empty}\nother:\n  limit: 2\n"
    out = with_section(text, {"other": {"limit": 2}}, "sample", {"zone": "UTC"}, "I.")
    assert out == "# top\nsample:\n  # I.\n  zone: UTC\nother:\n  limit: 2\n", f"written again: {out!r}"


def test_writing_the_whole_document_again_says_so_in_the_log() -> None:
    with capture_logs() as logs:
        out = with_section(
            "# kept?\nsample: {per_tick: 3}\n", {"sample": {"per_tick": 3}}, "sample", {"zone": "UTC"}, "I."
        )
    assert out.startswith("# Written again by embry0")
    assert [e["event"] for e in logs if e["log_level"] == "warning"] == ["settings_text_rewritten"], (
        "the comments were dropped without a warning"
    )
