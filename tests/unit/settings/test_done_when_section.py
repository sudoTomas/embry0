"""The ``done_when`` section of the settings document (#182): the YAML an operator writes, and what it refuses.

Nothing written keeps a job exactly as it ran before the section: one retry,
and the ticket heading "Acceptance criteria". Each bound is refused naming its
key and never repeating the value. The section is read through the catalog,
so each test fails with its own message on a tree that has no such section.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.settings.document import DocumentInvalid, check, load_yaml, read_sections
from embry0.settings.registry import Section
from embry0.settings.sections import CATALOG


def _section() -> Section[Any]:
    found = CATALOG.get("done_when")
    assert found is not None, "the settings document has no done_when section: a job's retries and headings are code"
    return found


def _stored(document: dict[str, Any], layer: str = "deployment") -> Any:
    """What a write of ``document`` stores, or each of its problems as ``(loc, msg)``."""
    _section()
    try:
        return check(CATALOG, document, stored={}, layer=layer).data
    except DocumentInvalid as exc:
        return [(problem.loc, problem.msg) for problem in exc.problems]


def _problems(values: dict[str, Any]) -> list[tuple[tuple[str | int, ...], str]]:
    found = _stored({"done_when": values})
    return found if isinstance(found, list) else []


def _read(data: dict[str, Any]) -> dict[str, Any] | None:
    _section()
    model = read_sections(CATALOG, data).models.get("done_when")
    return None if model is None else model.model_dump(mode="json")


def test_the_section_reads_as_an_operator_writes_it() -> None:
    text = "done_when:\n  retries: 2\n  ticket_sections:\n    - Definition of done\n    - ' Acceptance criteria '\n"
    data = _stored(load_yaml(text))
    assert data == {"done_when": {"retries": 2, "ticket_sections": ["Definition of done", "Acceptance criteria"]}}, (
        f"stored as written, each heading trimmed: {data}"
    )
    assert _read(data) == data["done_when"], "and read back the same"


def test_nothing_written_is_how_a_job_ran_before_the_section() -> None:
    assert _read({}) == {"retries": 1, "ticket_sections": ["Acceptance criteria"]}, (
        "the defaults are the code's old values: one retry, the heading Acceptance criteria"
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("retries", -1),
        ("retries", 6),
        ("retries", 1.5),
        ("retries", True),
        ("retries", "1"),
        ("ticket_sections", "Acceptance criteria"),
        ("ticket_sections", []),
        ("ticket_sections", [""]),
        ("ticket_sections", ["   "]),
        ("ticket_sections", ["Done\nwhen"]),
        ("ticket_sections", ["## Acceptance criteria"]),
        ("ticket_sections", ["x" * 81]),
        ("ticket_sections", ["Acceptance criteria", "acceptance CRITERIA"]),
        ("ticket_sections", ["Done\twhen"]),
        ("ticket_sections", ["Done\x85when"]),
        ("ticket_sections", ["Done\u2028when"]),
        ("ticket_sections", ["Done\u2029when"]),
        ("ticket_sections", ["Done\u202ewhen"]),
        ("ticket_sections", ["Done\u200bwhen"]),
        ("ticket_sections", ["Straße", "STRASSE"]),
        ("ticket_sections", ["Done in C#", "done in c"]),
        ("ticket_sections", ["Acceptance", "Acceptance criteria"]),
        ("ticket_sections", ["Definition of done", "Done", "Done when"]),
        ("ticket_sections", [f"Heading {n}" for n in range(6)]),
        ("ticket_sections", [7]),
    ],
)
def test_a_value_out_of_bounds_is_refused_naming_the_key(key: str, value: Any) -> None:
    found = _problems({key: value})
    assert found and {loc[:2] for loc, _ in found} == {("done_when", key)}, (
        f"{key} {value!r} must be refused as done_when.{key}: {found}"
    )


def test_a_refusal_never_repeats_the_heading() -> None:
    found = _problems({"ticket_sections": ["## Secret heading text"], "retries": 99})
    assert sorted(loc[:2] for loc, _ in found) == [("done_when", "retries"), ("done_when", "ticket_sections")], (
        f"every bad value is named by its own key, all at once: {found}"
    )
    assert all("Secret heading text" not in msg and "99" not in msg for _, msg in found), found


@pytest.mark.parametrize("layer", ["organization", "repository"])
def test_an_organization_and_a_repository_may_set_both_keys(layer: str) -> None:
    document = {"done_when": {"retries": 0, "ticket_sections": ["Definition of done"]}}
    stored = _stored(document, layer)
    assert stored == document, f"{layer} layer must be able to set retries and ticket_sections: {stored}"
    assert _section().org_keys == _section().repo_keys == {"retries", "ticket_sections"}


def test_a_heading_an_earlier_one_covers_is_refused_and_the_other_order_is_not() -> None:
    """A heading is read when no earlier one found a list, so one an earlier heading covers is never read."""
    found = _problems({"ticket_sections": ["Acceptance", "Acceptance criteria"]})
    assert found and {loc[:2] for loc, _ in found} == {("done_when", "ticket_sections")}, (
        f"Acceptance covers every section Acceptance criteria names, so the second can never be read: {found}"
    )
    assert all("Acceptance" not in msg for _, msg in found), f"a refusal never repeats the heading: {found}"
    longer_first = {"done_when": {"ticket_sections": ["Acceptance criteria", "Acceptance"]}}
    assert _stored(longer_first) == longer_first, "the longer heading first, then the shorter, is a fallback"


@pytest.mark.parametrize(
    "headings",
    [["Done in C#"], ["Critères d'acceptation"], ["Definition of done", "Acceptance criteria"], ["A", "B"]],
)
def test_headings_that_each_can_be_read_are_kept(headings: list[str]) -> None:
    document = {"done_when": {"ticket_sections": headings}}
    assert _stored(document) == document, f"each of {headings} reads a section no earlier one does"
