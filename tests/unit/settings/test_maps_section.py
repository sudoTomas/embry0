"""The ``maps`` section and ``leads.labels`` (#180): what a write may set, and that no label starts a job.

The checks against the running deployment, the templates it has and its
trigger labels, run through the write's context (``embry0.settings.
write_context``); here the context is built by hand. On the real store and
Postgres: ``tests/unit/api/test_map_settings_live.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from embry0.settings.registry import STORED, Section, SectionRefused
from embry0.settings.sections import CATALOG
from embry0.settings.sections.leads import LEADS

try:
    from embry0.settings.sections.maps import ANALYSIS_TEMPLATES, MAPS, TRIGGER_LABELS
except ImportError:
    pytest.fail("there is no maps section: a map's analysis template and labels are fixed in code", pytrace=False)

#: The deployment's trigger labels as a write's context holds them: folded as the trigger compares them.
TRIGGERS = {TRIGGER_LABELS: frozenset({"embry0", "run-it"})}
BOTH = pytest.mark.parametrize("section", [MAPS, LEADS], ids=["maps", "leads"])


def _refused(section: Section[Any], raw: Mapping[str, Any], context: Mapping[str, Any] | None = None) -> list[Any]:
    """The errors of a write the section refuses; the test fails when it takes it."""
    with pytest.raises(SectionRefused) as refused:
        section.validate(raw, context=context)
    return refused.value.errors


def test_the_defaults_are_what_the_readers_used_before_and_both_layers_may_set_the_maps_keys() -> None:
    assert MAPS.name in CATALOG, "the maps section is registered"
    maps = MAPS.defaults()
    assert (maps.analysis_template, maps.labels) == ("analysis-default", ("embry0-map",)), (
        "nothing written runs a map's analysis and labels its issues as before the section"
    )
    assert MAPS.org_keys == MAPS.repo_keys == {"analysis_template", "labels"}, (
        "teams name their own labels and keep their own analysis templates, by organization and by repository"
    )
    assert LEADS.defaults().labels == ("embry0-lead",), "a lead's issue is labelled as before the key"
    assert "labels" in LEADS.org_keys and "labels" not in LEADS.repo_keys, (
        "leads.labels is the organization's, as the leads keys are"
    )


@BOTH
@pytest.mark.parametrize(
    ("labels", "words"),
    [
        ("embry0-map", "write a list of labels"),
        ([f"label-{i}" for i in range(11)], "more than 10 labels"),
        (["fine", 7], "a label that is not text"),
        (["fine", "  "], "an empty label"),
        ([" padded"], "blanks around it"),
        (["x" * 51], "longer than 50 characters"),
        (["two\nlines"], "not one line of plain text"),
        (["Again", "again"], "a label twice"),
    ],
    ids=["text", "too-many", "number", "empty", "blanks", "too-long", "two-lines", "twice"],
)
def test_labels_a_forge_would_refuse_are_refused_without_repeating_them(
    section: Section[Any], labels: Any, words: str
) -> None:
    errors = _refused(section, {"labels": labels})
    assert [tuple(error["loc"]) for error in errors] == [("labels",)], errors
    message = errors[0]["msg"]
    assert words in message, f"{section.name}.labels is refused for another reason: {message}"
    for label in labels if isinstance(labels, list) else [labels]:
        if isinstance(label, str) and label.strip():
            assert label not in message, f"the refusal repeats a label: {message}"


@BOTH
def test_a_trigger_label_is_refused_at_its_place_in_the_list_without_its_value(section: Section[Any]) -> None:
    errors = _refused(section, {"labels": ["ours", "Run-It", "EMBRY0"]}, TRIGGERS)
    assert [tuple(error["loc"]) for error in errors] == [("labels", 1), ("labels", 2)], (
        f"each trigger label in {section.name}.labels is refused at its place, compared as the trigger compares "
        f"labels: {errors}"
    )
    for error in errors:
        assert "trigger labels" in error["msg"] and "must not start a job" in error["msg"], error["msg"]
        assert "Run-It" not in error["msg"] and "EMBRY0" not in error["msg"], f"the value is repeated: {error}"
    assert section.validate({"labels": ["ours", "embry0-ish"]}, context=TRIGGERS).labels == ("ours", "embry0-ish")
    assert section.validate({"labels": ["embry0"]}).labels == ("embry0",), (
        "offline, with no deployment to read, the check is left to the write"
    )


@BOTH
def test_a_label_the_document_held_before_the_write_is_not_judged_again(section: Section[Any]) -> None:
    """A trigger label changed after the labels were written: a write of the same document goes through, and an
    issue opened later is not given the label (``embry0.services.own_issues``)."""
    held = {**TRIGGERS, STORED: {"labels": ["ours", "run-it"]}}
    kept = section.validate({"labels": ["ours", "Run-It", "new"]}, context=held)
    assert kept.labels == ("ours", "Run-It", "new"), "a label held before the write is kept"
    errors = _refused(section, {"labels": ["ours", "run-it", "embry0"]}, held)
    assert [tuple(error["loc"]) for error in errors] == [("labels", 2)], (
        f"a trigger label the write adds is still refused: {errors}"
    )


def test_the_analysis_template_is_checked_against_the_templates_only_when_a_write_changes_it() -> None:
    asked: list[str] = []

    def checker(name: str) -> str | None:
        asked.append(name)
        return None if name == "deep-look" else f"{name!r} records its jobs as research jobs"

    context = {ANALYSIS_TEMPLATES: checker}
    assert MAPS.validate({"analysis_template": "deep-look"}, context=context).analysis_template == "deep-look"
    errors = _refused(MAPS, {"analysis_template": "survey"}, context)
    assert [tuple(error["loc"]) for error in errors] == [("analysis_template",)], errors
    assert "records its jobs as research jobs" in errors[0]["msg"], errors[0]["msg"]
    asked.clear()
    MAPS.validate({"analysis_template": "survey"}, context={**context, STORED: {"analysis_template": "survey"}})
    MAPS.validate({"analysis_template": "analysis-default"}, context={**context, STORED: {}})
    assert asked == [], f"a template the document read before the write was checked again: {asked}"
    assert MAPS.validate({"analysis_template": "survey"}).analysis_template == "survey", (
        "offline, the template is left to the write"
    )


@pytest.mark.parametrize("name", [" deep-look", "deep\tlook", "", "x" * 101], ids=["blanks", "tab", "empty", "long"])
def test_a_template_name_no_template_could_have_is_refused(name: str) -> None:
    errors = _refused(MAPS, {"analysis_template": name})
    assert [tuple(error["loc"]) for error in errors] == [("analysis_template",)], errors
