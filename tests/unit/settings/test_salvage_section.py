"""The ``salvage`` section of the settings document (#182): the YAML an operator writes, and what it refuses.

``commit_subject`` is the subject of the commit that keeps a stopped coding
run's work, as a template naming ``{agent}`` and ``{reason}``. Nothing written
keeps the subject exactly as it was: ``wip: unfinished — <agent> stopped
(<reason>)``. The section is read through the catalog, so each test fails
with its own message on a tree that has no such section or no such key.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.settings.document import DocumentInvalid, check, load_yaml, read_sections
from embry0.settings.registry import Section
from embry0.settings.sections import CATALOG

CONVENTIONAL = "chore(wip): unfinished — {agent} stopped ({reason})"


def _section() -> Section[Any]:
    found = CATALOG.get("salvage")
    assert found is not None, "the settings document has no salvage section: the wip: subject is code"
    assert "commit_subject" in found.model.model_fields, (
        "the salvage section has no commit_subject template: the subject is still built from a prefix in the code"
    )
    return found


def _stored(document: dict[str, Any], layer: str = "deployment") -> Any:
    _section()
    try:
        return check(CATALOG, document, stored={}, layer=layer).data
    except DocumentInvalid as exc:
        return [(problem.loc, problem.msg) for problem in exc.problems]


def test_the_section_reads_as_an_operator_writes_it() -> None:
    data = _stored(load_yaml(f'salvage:\n  commit_subject: " {CONVENTIONAL} "\n'))
    assert data == {"salvage": {"commit_subject": CONVENTIONAL}}, f"stored as written, trimmed: {data}"
    model = read_sections(CATALOG, data).models["salvage"]
    assert model.model_dump(mode="json") == {"commit_subject": CONVENTIONAL}


def test_nothing_written_is_the_old_subject() -> None:
    _section()
    model = read_sections(CATALOG, {}).models.get("salvage")
    assert model is not None and model.model_dump(mode="json") == {
        "commit_subject": "wip: unfinished — {agent} stopped ({reason})"
    }, "the default is the subject the code wrote, as a template"


@pytest.mark.parametrize(
    "value",
    [
        "wip {{draft}}: {agent} stopped ({reason})",
        "chore: keep the {agent}'s unfinished work",
        "{reason}",
        "x" * 80,
    ],
)
def test_a_subject_in_the_grammar_is_accepted(value: str) -> None:
    assert _stored({"salvage": {"commit_subject": value}}) == {"salvage": {"commit_subject": value}}, (
        f"{value!r} names only {{agent}} and {{reason}} (or none), with {{{{ and }}}} for a literal brace"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "wip: {agent}\nSigned-off-by: someone",
        "wip: {agent}\rx",
        "wip\x00: {agent}",
        "wip:\tunfinished {agent}",
        "wip:\x85unfinished {agent}",
        "wip: unfinished {agent}",
        "wip: unfinished {agent}",
        "wip:‮unfinished {agent}",
        "wip:​unfinished {agent}",
        "# wip: {agent}",
        "  #wip {agent}",
        "wip: {job} stopped",
        "wip: {Agent} stopped",
        "wip: {agent!r} stopped",
        "wip: {agent:,} stopped",
        "wip: {0} stopped",
        "wip: {} stopped",
        "wip: {agent stopped",
        "wip: agent} stopped",
        "wip: {{agent} stopped",
        "x" * 81,
        7,
        None,
        ["wip:"],
    ],
)
def test_a_subject_out_of_bounds_is_refused_naming_the_key(value: Any) -> None:
    found = _stored({"salvage": {"commit_subject": value}})
    assert isinstance(found, list) and {loc[:2] for loc, _ in found} == {("salvage", "commit_subject")}, (
        f"commit_subject {value!r} must be refused as salvage.commit_subject: {found}"
    )
    assert all("Signed-off-by" not in msg and "{job}" not in msg for _, msg in found), (
        f"a refusal never repeats the value: {found}"
    )


@pytest.mark.parametrize("layer", ["organization", "repository"])
def test_an_organization_and_a_repository_may_set_the_subject(layer: str) -> None:
    document = {"salvage": {"commit_subject": CONVENTIONAL}}
    assert _stored(document, layer) == document, f"{layer} layer must be able to set commit_subject"
    assert _section().org_keys == _section().repo_keys == {"commit_subject"}
