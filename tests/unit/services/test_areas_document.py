"""The areas document (spec 2026-11-10): what a write may hold, every problem at once, and what readers read."""

from __future__ import annotations

import json
import sys

import pytest

from embry0.services.areas import Area
from embry0.services.areas_document import (
    MAX_TEXT_BYTES,
    DocumentInvalid,
    DocumentTooLarge,
    areas_of,
    changes,
    check,
    dump,
    load_yaml,
    parse_body,
)

DOCUMENT = """# Areas of acme/widgets.
version: 1
areas:
  - name: "  Billing "
    paths: ["services/billing/**", "web/apps/billing/", "services/billing/**"]
    logs: ["billing-api"]
    owners: ["@octo", "Octo", "hubot"]
  - name: Search
    paths: ["services/search/**"]
"""


def _problems(text: str) -> list[dict[str, object]]:
    with pytest.raises(DocumentInvalid) as raised:
        check(load_yaml(text))
    return [p.to_dict() for p in raised.value.problems]


def test_a_document_reads_as_the_values_every_reader_reads() -> None:
    assert check(load_yaml(DOCUMENT)) == {
        "areas": [
            {
                "name": "Billing",
                "paths": ["services/billing/**", "web/apps/billing/**"],
                "logs": ["billing-api"],
                "owners": ["octo", "hubot"],
            },
            {"name": "Search", "paths": ["services/search/**"], "logs": [], "owners": []},
        ]
    }, "a name stripped, a trailing / expanded, a glob and an owner written twice kept once, @ dropped"


@pytest.mark.parametrize("text", ["", "# only a comment\n", "areas:\n", "areas: []\n", "version: 1\n"])
def test_an_empty_document_names_no_areas(text: str) -> None:
    assert check(load_yaml(text)) == {"areas": []}


def test_every_problem_is_placed_at_once_and_no_value_or_written_key_is_echoed() -> None:
    """Each problem at its key, but a key the document does not take at the mapping that holds it: the key is
    what the operator wrote, and the message says the keys there are, with the nearest."""
    text = """
versoin: 1
areas:
  - paths: ["a/**"]
  - name: Billing
    paths: ["/services/billing/**", "services/billing**", "a/../b", "a//b", "c/[x"]
    owners: ["octo cat", 7]
    logs: ["", "x"]
    teams: ["payments"]
  - name: billing
    paths: services/billing/**
  - name: "!!!"
  - Plain string
"""
    found = _problems(text)
    locs = [(".".join(map(str, p["loc"])), p["type"]) for p in found]
    assert locs == [
        ("", "extra_forbidden"),
        ("areas.0.name", "missing"),
        ("areas.1", "extra_forbidden"),
        ("areas.1.paths.0", "invalid_glob"),
        ("areas.1.paths.1", "invalid_glob"),
        ("areas.1.paths.2", "invalid_glob"),
        ("areas.1.paths.3", "invalid_glob"),
        ("areas.1.paths.4", "invalid_glob"),
        ("areas.1.logs.0", "string_too_short"),
        ("areas.1.owners.0", "invalid_login"),
        ("areas.1.owners.1", "string_type"),
        ("areas.2.paths", "list_type"),
        ("areas.3.name", "value_error"),
        ("areas.4", "dict_type"),
    ], locs
    assert "Did you mean 'version'?" in found[0]["msg"]
    said = json.dumps(found)
    for value in ("services/billing**", "octo cat", "payments", "c/[x", "Plain string", "versoin", "teams"):
        assert value not in said, f"a refused value or a key written is echoed: {value}"


def test_two_areas_cannot_share_a_label() -> None:
    found = _problems("areas:\n  - name: Web App\n  - name: web-app\n")
    assert [(p["loc"], p["type"]) for p in found] == [(["areas", 1, "name"], "duplicate_slug")]
    assert "the same label as area 0's name" in str(found[0]["msg"]), found
    assert "web" not in str(found[0]["msg"]).lower(), "the name the operator wrote is never repeated"


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("version: 2\n", "version"),
        ("version: 0\n", "version"),
        ("version: true\n", "int_type"),
        ("- a list\n", "dict_type"),
        ("areas: {name: x}\n", "list_type"),
        (f"areas: [{', '.join(f'{{name: a{i}}}' for i in range(201))}]\n", "too_long"),
    ],
)
def test_the_document_s_shape_is_checked(text: str, kind: str) -> None:
    assert [p["type"] for p in _problems(text)] == [kind]


@pytest.mark.parametrize(
    "text",
    ["areas: [", "a: &x 1\nb: *x\n", "areas: []\nareas: []\n", "a: !!int abc\n", "<<: {a: 1}\n"],
)
def test_what_no_operator_document_may_use_is_refused(text: str) -> None:
    with pytest.raises(DocumentInvalid):
        load_yaml(text)


@pytest.mark.parametrize(
    ("text", "loc"),
    [
        ("areas:\n  - name: zz-written-key\n    zz-written-key: a\n    zz-written-key: b\n", ["areas", 0]),
        ("zz-written-key: 1\nzz-written-key: 2\nareas: []\n", []),
    ],
)
def test_a_key_written_twice_is_placed_at_the_mapping_that_holds_it(text: str, loc: list[object]) -> None:
    with pytest.raises(DocumentInvalid) as raised:
        load_yaml(text)
    found = [p.to_dict() for p in raised.value.problems]
    assert [(p["loc"], p["type"]) for p in found] == [(loc, "duplicate_key")], found
    assert "zz-written-key" not in json.dumps(found), f"the problem repeats the key written: {found}"


_LONG = "9" * (sys.get_int_max_str_digits() + 1)


@pytest.mark.parametrize(
    ("body", "content_type", "loc", "kind"),
    [
        (f"zzA: {{name: {_LONG}}}\n", "application/yaml", [], "number_too_long"),
        (f"zzA: [{{name: {_LONG}}}]\n", "application/yaml", [], "number_too_long"),
        (f"areas:\n  - name: Web\n    zzA: {{k: {_LONG}}}\n", "application/yaml", ["areas", 0], "number_too_long"),
        ("areas:\n  - name: Web\n    zzA: {k: !!int zzBAD}\n", "application/yaml", ["areas", 0], "invalid_number"),
        ("areas:\n  - name: Web\n    zzA: {b: 1, b: 2}\n", "application/yaml", ["areas", 0], "duplicate_key"),
        (
            f"areas:\n  - name: Web\n    paths: [{{zzA: {_LONG}}}]\n",
            "application/yaml",
            ["areas", 0, "paths", 0],
            "number_too_long",
        ),
        (f'{{"zzA": {{"name": {_LONG}}}}}', "application/json", [], "number_too_long"),
        (f'{{"zzA": [{{"name": {_LONG}}}]}}', "application/json", [], "number_too_long"),
        (f'{{"areas": [{{"name": "Web", "zzA": [{_LONG}]}}]}}', "application/json", ["areas", 0], "number_too_long"),
    ],
)
def test_a_problem_of_the_text_under_a_written_key_is_placed_at_the_first_key_the_document_does_not_take(
    body: str, content_type: str, loc: list[object], kind: str
) -> None:
    """A number too long or unreadable, or a key written twice, under a key the operator wrote is placed at the
    mapping that holds that key, as an unknown key is, so the place never repeats it; YAML's line says where."""
    with pytest.raises(DocumentInvalid) as raised:
        parse_body(body.encode(), content_type)
    found = [p.to_dict() for p in raised.value.problems]
    assert [(p["loc"], p["type"]) for p in found] == [(loc, kind)], found
    assert "zz" not in json.dumps(found), f"the problem repeats a key or value the operator wrote: {found}"


@pytest.mark.parametrize(
    ("body", "content_type", "loc", "kind"),
    [
        (f"areas:\n  - name: {_LONG}\n", "application/yaml", ["areas", 0, "name"], "number_too_long"),
        (
            f"areas:\n  - name: Web\n    paths: [{_LONG}]\n",
            "application/yaml",
            ["areas", 0, "paths", 0],
            "number_too_long",
        ),
        ("areas:\n  - name: !!int abc\n", "application/yaml", ["areas", 0, "name"], "invalid_number"),
        (f"version: {_LONG}\n", "application/yaml", ["version"], "number_too_long"),
        (f'{{"areas": [{{"name": {_LONG}}}]}}', "application/json", ["areas", 0, "name"], "number_too_long"),
        (
            f'{{"areas": [{{"name": "Web", "paths": [{_LONG}]}}]}}',
            "application/json",
            ["areas", 0, "paths", 0],
            "number_too_long",
        ),
    ],
)
def test_a_problem_of_the_text_at_a_key_the_document_takes_keeps_its_whole_place(
    body: str, content_type: str, loc: list[object], kind: str
) -> None:
    """Only a key the operator wrote is cut away: at the document's own keys the place is the field itself."""
    with pytest.raises(DocumentInvalid) as raised:
        parse_body(body.encode(), content_type)
    found = [p.to_dict() for p in raised.value.problems]
    assert [(p["loc"], p["type"]) for p in found] == [(loc, kind)], found
    assert "99999" not in json.dumps(found), f"the problem repeats the value: {found}"


def test_a_body_is_yaml_kept_as_written_or_json_with_no_text() -> None:
    text, values = parse_body(b"areas:\n  - name: Web\n", "application/yaml")
    assert text == "areas:\n  - name: Web\n" and values == {"areas": [{"name": "Web"}]}
    text, values = parse_body(b'{"areas": [{"name": "Web"}]}', "application/json; charset=utf-8")
    assert text is None and values == {"areas": [{"name": "Web"}]}
    assert parse_body(b"", "application/json") == (None, None)
    with pytest.raises(DocumentInvalid):
        parse_body(b'{"areas": [], "areas": []}', "application/json")
    with pytest.raises(DocumentInvalid):
        parse_body(b"\xff\xfe", "application/yaml")
    with pytest.raises(DocumentInvalid):
        parse_body(b"areas: []\x00", "application/yaml")
    with pytest.raises(DocumentTooLarge):
        parse_body(b"#" * (MAX_TEXT_BYTES + 1), "application/yaml")


def test_a_json_body_is_stored_as_the_yaml_it_dumps_to() -> None:
    data = check({"areas": [{"name": "Web", "paths": ["web/"], "owners": ["@octo"]}, {"name": "Docs"}]})
    text = dump(data, header="written from JSON\nby a test")
    assert text.startswith("# written from JSON\n# by a test\n")
    assert check(load_yaml(text)) == data, "the dumped text reads back as the same values"
    assert "logs" not in text, "an empty list is left out"


def test_a_reader_reads_what_it_knows_of_a_newer_document() -> None:
    data = {
        "areas": [
            {"name": "Web", "paths": ["web/**"], "owners": ["octo", "group:web"], "budget": 3},
            {"paths": ["nameless/**"]},
            "junk",
            {"name": " Docs ", "logs": ["docs", 4]},
        ],
        "people": [],
    }
    assert areas_of(data) == [Area("Web", ("web/**",), (), ("octo",)), Area("Docs", (), ("docs",), ())]
    assert areas_of(None) == [] and areas_of({"areas": "x"}) == []


def test_changes_name_each_area_with_owners_first() -> None:
    before = {
        "areas": [
            {"name": "Web", "paths": ["web/**"], "owners": ["octo"]},
            {"name": "Api", "paths": ["api/**"]},
            {"name": "Docs", "paths": ["docs/**"]},
        ]
    }
    after = {
        "areas": [
            {"name": "Api", "paths": ["api/**", "lib/**"]},
            {"name": "Web", "paths": ["web/**"], "owners": ["hubot"]},
            {"name": "Search", "paths": ["search/**"], "owners": ["ada"]},
        ]
    }
    found = changes(before, after)
    assert [(c["area"], c["change"]) for c in found][:2] == [("Search", "added"), ("Web", "changed")], found
    web = next(c for c in found if c["area"] == "Web")
    assert web["owners_added"] == ["hubot"] and web["owners_removed"] == ["octo"]
    assert web["keys"] == ["owners", "order"], "Web moved after Api, which changes which area a file falls in"
    assert {(c["area"], c["change"]) for c in found} == {
        ("Search", "added"),
        ("Web", "changed"),
        ("Api", "changed"),
        ("Docs", "removed"),
    }
    assert changes(after, after) == []


@pytest.mark.parametrize(
    ("glob", "says"),
    [(" web/** ", "no space at its start or end"), ("web/** ", "no space"), ("!web/**", "negation is not supported")],
)
def test_a_glob_qa_yaml_would_read_otherwise_is_refused(glob: str, says: str) -> None:
    """#458's review: a padded glob was stripped where qa.yaml refuses it, and a leading '!' read as the character."""
    with pytest.raises(DocumentInvalid, match="paths.0") as raised:
        check(load_yaml(f"areas:\n  - name: Web\n    paths: [{json.dumps(glob)}]\n"))
    found = [p.to_dict() for p in raised.value.problems]
    assert [(p["loc"], p["type"]) for p in found] == [(["areas", 0, "paths", 0], "invalid_glob")], found
    assert says in str(found[0]["msg"]), found[0]["msg"]


def test_a_name_in_any_script_names_an_area() -> None:
    text = "areas:\n  - name: Платежи\n    paths: [pay/**]\n  - name: 決済 API\n"
    try:
        data = check(load_yaml(text))
    except DocumentInvalid as exc:
        pytest.fail(f"a name with letters of another script is refused: {exc}")
    assert [a["name"] for a in data["areas"]] == ["Платежи", "決済 API"]
    assert [a.slug for a in areas_of(data)] == ["платежи", "決済-api"]


def test_a_literal_bracket_is_written_as_a_class() -> None:
    data = check(load_yaml("areas:\n  - name: Web\n    paths: ['app/[[]id]/']\n"))
    assert data["areas"][0]["paths"] == ["app/[[]id]/**"]
    unclosed = _problems("areas:\n  - name: Web\n    paths: ['app/[id/x']\n")
    assert "[[]" in str(unclosed[0]["msg"]), "the refusal says how to write a literal '['"


def test_a_name_is_one_area_however_its_letters_are_encoded_or_cased() -> None:
    """#458's second review: a precomposed and a combining accent were two areas, and İstanbul split in two."""
    composed, combining = "Caf\u00e9", "Cafe\u0301"
    try:
        check(load_yaml(f"areas:\n  - name: {composed}\n  - name: {combining}\n"))
        pytest.fail("the same name with its accent encoded two ways is two areas")
    except DocumentInvalid as exc:
        found = [p.to_dict() for p in exc.problems]
    assert [(p["loc"], p["type"]) for p in found] == [(["areas", 1, "name"], "duplicate_slug")], found
    data = check(load_yaml(f"areas:\n  - name: Cafe\n  - name: {combining}\n  - name: \u0130stanbul\n"))
    slugs = [a.slug for a in areas_of(data)]
    assert slugs[:2] == ["cafe", "caf\u00e9"], f"an accent is part of the letter it marks: {slugs}"
    assert "-" not in slugs[2] and slugs[2].endswith("stanbul"), f"a dotted capital I splits the word: {slugs[2]}"


def test_lookalike_spellings_of_a_name_are_one_area() -> None:
    """#458's third review: İstanbul and istanbul, Ⅻ and XII were two areas each."""
    for first, second in (("\u0130stanbul", "istanbul"), ("\u216b", "XII"), ("\ufb01le", "file"), ("\uff21bc", "Abc")):
        try:
            check(load_yaml(f"areas:\n  - name: {first}\n  - name: {second}\n"))
            pytest.fail(f"{first!r} and {second!r} are two areas")
        except DocumentInvalid as exc:
            assert [p.type for p in exc.problems] == ["duplicate_slug"], exc.problems
    data = check(load_yaml("areas:\n  - name: Caf\u00e9\n  - name: Cafe\n"))
    assert [a.slug for a in areas_of(data)] == ["caf\u00e9", "cafe"], "an accent still tells two names apart"


#: What an operator wrote as a key, a tag or an alias name: no problem's place or message may repeat it.
_WRITTEN = "zz-written"


@pytest.mark.parametrize(
    "text",
    [
        f"{_WRITTEN}: 1\nareas: []\n",
        f"areas:\n  - name: web\n    {_WRITTEN}: 1\n",
        f"areas: !{_WRITTEN} []\n",
        f"areas: !{_WRITTEN}'x []\n",
        f"areas: !<tag:{_WRITTEN}> []\n",
        f"%TAG !e! tag:{_WRITTEN}:\n---\nareas: !e!x []\n",
        f"areas: *{_WRITTEN}\n",
        f"areas: [{{name: web}}]\nareas: [{{name: {_WRITTEN}}}]\n",
    ],
    ids=[
        "unknown top key",
        "unknown area key",
        "tag",
        "tag with a quote",
        "verbatim tag",
        "expanded tag",
        "undefined alias",
        "key written twice",
    ],
)
def test_no_problem_repeats_a_key_a_tag_or_an_alias_the_operator_wrote(text: str) -> None:
    with pytest.raises(DocumentInvalid) as raised:
        check(load_yaml(text))
    found = [p.to_dict() for p in raised.value.problems]
    assert found, text
    assert _WRITTEN not in json.dumps(found), f"a problem repeats what was written: {found}"


def test_a_yaml_message_keeps_its_line_column_and_pyyaml_s_token_names() -> None:
    with pytest.raises(DocumentInvalid) as raised:
        load_yaml("areas:\n  - name: a\n b: 1\n")
    assert [p.msg for p in raised.value.problems] == [
        "The YAML cannot be read: line 3, column 2: expected <block end>, but found '<block mapping start>'"
    ]


def test_two_keys_an_area_does_not_take_are_one_problem_at_the_area() -> None:
    found = _problems("areas:\n  - name: web\n    zz-one: 1\n    zz-two: 2\n")
    assert [(p["loc"], p["type"]) for p in found] == [(["areas", 0], "extra_forbidden")], found
