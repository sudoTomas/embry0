"""The ``watcher`` section of the settings document (#738): the YAML an operator writes, and what it refuses.

Nothing written keeps the watcher as the seven ``WATCHER_*`` variables' defaults
ran it. Each bound is refused naming its key and never repeating the value.
The section is the deployment's alone, and a stack row checks its keys with
the section's own types and bounds. The section is read through the catalog,
so each test fails with its own message on a tree that has no watcher section.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.config_retired import MOVED_TO_DOCUMENTS
from embry0.settings.document import DocumentInvalid, check, load_yaml, read_sections
from embry0.settings.sections import CATALOG

#: What the seven variables defaulted to before the section, each under its key.
BEFORE = {
    "auto_accept": False,
    "auto_accept_after_hours": 24.0,
    "auto_accept_min_severity": "high",
    "auto_accept_min_occurrences": 10,
    "auto_accept_max_per_day": 2,
    "skip_unchanged_mode": "shadow",
    "skip_known_hours": 24,
}
#: The variable each key was before the section.
VARIABLES = {
    "auto_accept": "WATCHER_AUTO_ACCEPT",
    "auto_accept_after_hours": "WATCHER_AUTO_ACCEPT_AFTER_HOURS",
    "auto_accept_min_severity": "WATCHER_AUTO_ACCEPT_MIN_SEVERITY",
    "auto_accept_min_occurrences": "WATCHER_AUTO_ACCEPT_MIN_OCCURRENCES",
    "auto_accept_max_per_day": "WATCHER_AUTO_ACCEPT_MAX_PER_DAY",
    "skip_unchanged_mode": "WATCHER_SKIP_UNCHANGED_MODE",
    "skip_known_hours": "WATCHER_SKIP_KNOWN_HOURS",
}


def _section() -> Any:
    section = CATALOG.get("watcher")
    if section is None:
        pytest.fail("the settings document has no watcher section: the watcher's policy is read from the environment")
    return section


def _problems(values: dict[str, Any]) -> list[tuple[tuple[str | int, ...], str]]:
    """Each problem a deployment's document holding ``watcher: values`` has, as ``(loc, msg)``."""
    try:
        check(CATALOG, {"watcher": values}, stored={})
    except DocumentInvalid as exc:
        return [(problem.loc, problem.msg) for problem in exc.problems]
    return []


def _read(data: dict[str, Any]) -> dict[str, Any] | None:
    """The watcher section as a tick reads it from stored ``data``; None when there is no such section."""
    model = read_sections(CATALOG, data).models.get("watcher")
    return None if model is None else model.model_dump(mode="json")


def test_nothing_written_runs_the_watcher_as_the_variables_defaults_did() -> None:
    assert _section().defaults().model_dump(mode="json") == BEFORE, (
        "a deployment that writes nothing must auto-accept nothing, hold a clean signature 24 hours and skip an "
        "unchanged window in shadow only, as before"
    )


def test_the_section_reads_as_an_operator_writes_it() -> None:
    _section()
    text = (
        "watcher:\n"
        "  auto_accept: true\n"
        "  auto_accept_after_hours: 6\n"
        "  auto_accept_min_severity: Medium\n"
        "  auto_accept_min_occurrences: 3\n"
        "  auto_accept_max_per_day: 5\n"
        '  skip_unchanged_mode: "ON"\n'
        "  skip_known_hours: 0\n"
    )
    stored = check(CATALOG, load_yaml(text), stored={}).data
    assert _read(stored) == {
        "auto_accept": True,
        "auto_accept_after_hours": 6.0,
        "auto_accept_min_severity": "medium",
        "auto_accept_min_occurrences": 3,
        "auto_accept_max_per_day": 5,
        "skip_unchanged_mode": "on",
        "skip_known_hours": 0,
    }, "each key reads as written, the severity and the mode without regard to case"


@pytest.mark.parametrize(
    ("values", "key"),
    [
        ({"auto_accept": "yes"}, "auto_accept"),
        ({"auto_accept_after_hours": -1}, "auto_accept_after_hours"),
        ({"auto_accept_after_hours": 8761}, "auto_accept_after_hours"),
        ({"auto_accept_min_severity": "severe"}, "auto_accept_min_severity"),
        ({"auto_accept_min_occurrences": -1}, "auto_accept_min_occurrences"),
        ({"auto_accept_min_occurrences": 1_000_001}, "auto_accept_min_occurrences"),
        ({"auto_accept_max_per_day": -1}, "auto_accept_max_per_day"),
        ({"auto_accept_max_per_day": 1001}, "auto_accept_max_per_day"),
        ({"skip_known_hours": -1}, "skip_known_hours"),
        ({"skip_known_hours": 8761}, "skip_known_hours"),
        ({"skip_known_hours": 1.5}, "skip_known_hours"),
        ({"skip_unchanged_mode": "maybe"}, "skip_unchanged_mode"),
        ({"skip_unchanged_mode": True}, "skip_unchanged_mode"),  # a bare on, as YAML reads it
    ],
)
def test_a_value_out_of_bounds_is_refused_naming_its_key_not_the_value(values: dict[str, Any], key: str) -> None:
    _section()
    problems = _problems(values)
    assert [loc for loc, _ in problems] == [("watcher", key)], f"refused, naming watcher.{key}: {problems}"
    value = str(next(iter(values.values())))
    assert not any(value in msg for _, msg in problems), f"the refusal never repeats the value: {problems}"


def test_the_section_is_the_deployment_s_alone() -> None:
    section = _section()
    assert (section.org_keys, section.repo_keys) == (frozenset(), frozenset()), (
        "an auto-accepted draft spends from the shared budget: no organization or repository sets these"
    )
    for layer in ("organization", "repository"):
        with pytest.raises(DocumentInvalid) as invalid:
            check(CATALOG, {"watcher": {"auto_accept": True}}, stored={}, layer=layer, inherited={})
        assert [(p.loc, p.type) for p in invalid.value.problems] == [(("watcher", "auto_accept"), "not_overridable")]


def test_each_variable_no_longer_read_names_the_key_that_holds_what_it_set() -> None:
    _section()
    assert {key: MOVED_TO_DOCUMENTS.get(name) for key, name in VARIABLES.items()} == {
        key: f"the settings document's watcher.{key}" for key in VARIABLES
    }, "the seven WATCHER_* policy variables no longer read name the key that holds what each set"
    assert set(VARIABLES) == set(_section().fields), "every key of the section was one of the seven variables"
