"""The ``scout`` section of the settings document: the YAML an operator writes, and what it refuses.

Nothing written keeps the scout exactly as it ran before the section. Each
bound is refused naming its key and never repeating the value. The section is
read through the catalog, so each test fails with its own message on a tree
that has no scout section.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.config_retired import MOVED_TO_DOCUMENTS
from embry0.settings.document import DocumentInvalid, check, load_yaml, read_sections
from embry0.settings.sections import CATALOG


def _stored(document: dict[str, Any]) -> Any:
    """What a write of ``document`` stores, or each of its problems as ``(loc, msg)``."""
    try:
        return check(CATALOG, document, stored={}).data
    except DocumentInvalid as exc:
        return [(problem.loc, problem.msg) for problem in exc.problems]


def _problems(values: dict[str, Any]) -> list[tuple[tuple[str | int, ...], str]]:
    """Each problem a document holding ``scout: values`` has, as ``(loc, msg)``."""
    found = _stored({"scout": values})
    return found if isinstance(found, list) else []


def _read(data: dict[str, Any]) -> dict[str, Any] | None:
    """The scout section as a tick reads it from stored ``data``; None when there is no such section."""
    model = read_sections(CATALOG, data).models.get("scout")
    return None if model is None else model.model_dump(mode="json")


def test_the_section_reads_as_an_operator_writes_it() -> None:
    text = (
        "# one template, fewer jobs, a daily cap\n"
        "scout:\n"
        "  templates: [kintsugi-trace]\n"
        "  max_in_flight: 1\n"
        "  revisit_hours: 48\n"
        "  max_leads_per_job: 3\n"
        "  job_budget_usd: 0.5\n"
        "  max_jobs_per_day: 20\n"
    )
    data = _stored(load_yaml(text))
    assert data == {
        "scout": {
            "templates": ["kintsugi-trace"],
            "max_in_flight": 1,
            "revisit_hours": 48.0,
            "max_leads_per_job": 3,
            "job_budget_usd": 0.5,
            "max_jobs_per_day": 20,
        }
    }, f"the scout section is stored as written: {data}"
    assert _read(data) == data["scout"], "and read back the same by a tick"


def test_nothing_written_is_how_the_scout_ran_before_the_section() -> None:
    assert _read({}) == {
        "templates": ["garden", "kintsugi-trace"],
        "max_in_flight": 2,
        "revisit_hours": 24.0,
        "max_leads_per_job": 5,
        "job_budget_usd": 1.0,
        "max_jobs_per_day": None,
    }, "every default is the value the scout used before: SCOUT_* defaults, the 1.0 budget, no daily cap"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("max_in_flight", -1),
        ("max_in_flight", 65),
        ("max_in_flight", 1.5),
        ("revisit_hours", 0),
        ("revisit_hours", 8761),
        ("revisit_hours", 1e308),
        ("max_leads_per_job", 0),
        ("max_leads_per_job", 101),
        ("job_budget_usd", 0),
        ("job_budget_usd", -1),
        ("job_budget_usd", 1000.01),
        ("max_jobs_per_day", -1),
        ("max_jobs_per_day", 10001),
        ("max_jobs_per_day", "20"),
        ("templates", "garden"),
        ("templates", []),
        ("templates", ["garden", "garden"]),
    ],
)
def test_a_value_out_of_bounds_is_refused_naming_the_key(key: str, value: Any) -> None:
    """An upper bound too: past it a revisit overflows a timedelta, or a job's cap is not a cap."""
    found = _problems({key: value})
    assert [loc for loc, _ in found] == [("scout", key)], f"{key} {value!r} must be refused as scout.{key}: {found}"


def test_an_unknown_template_is_refused_naming_the_key_and_never_repeated() -> None:
    found = _problems({"templates": ["garden", "weed-whacker"]})
    assert [loc[:2] for loc, _ in found] == [("scout", "templates")], (
        f"a template the scout cannot read is refused when it is written, not ignored at every tick: {found}"
    )
    assert all("weed-whacker" not in msg for _, msg in found), f"a refusal never repeats the value: {found}"


def test_every_bad_value_is_named_at_once_and_none_is_repeated_back() -> None:
    found = _problems({"max_in_flight": 999, "job_budget_usd": 5000, "max_jobs_per_day": -7})
    assert sorted(loc for loc, _ in found) == [
        ("scout", "job_budget_usd"),
        ("scout", "max_in_flight"),
        ("scout", "max_jobs_per_day"),
    ], f"every bad value is named by its own key, all at once: {found}"
    for _, msg in found:
        for value in ("999", "5000", "-7"):
            assert value not in msg, f"a refusal never repeats the value it refused: {msg!r}"


def test_a_stored_value_the_code_refuses_reads_as_its_default_and_never_reaches_a_tick() -> None:
    """A value stored by a newer release (a template this one does not run) or written to the row by hand."""
    sections = read_sections(
        CATALOG, {"scout": {"templates": ["weed-whacker"], "job_budget_usd": 1e6, "max_in_flight": 1}}
    )
    model = sections.models.get("scout")
    read = None if model is None else (model.templates, model.job_budget_usd, model.max_in_flight)
    assert read == (("garden", "kintsugi-trace"), 1.0, 1), "each refused key reads as its default; its sibling keeps"
    assert sorted(r.key for r in sections.refused) == ["job_budget_usd", "templates"]


def test_what_an_organization_may_override_and_where_each_key_came_from() -> None:
    section = CATALOG.get("scout")
    assert section is not None and section.org_keys == {"templates", "revisit_hours", "max_leads_per_job"}, (
        "an organization may choose what is scouted in its repositories, how often and how many leads a job adds; "
        "the shared local server's load (in flight, per day) and each job's hard cap stay the deployment's"
    )
    assert section.repo_keys == frozenset()
    variables = {
        "templates": "SCOUT_TEMPLATES",
        "max_in_flight": "SCOUT_MAX_IN_FLIGHT",
        "revisit_hours": "SCOUT_REVISIT_HOURS",
        "max_leads_per_job": "SCOUT_MAX_LEADS_PER_JOB",
    }
    assert {key: MOVED_TO_DOCUMENTS.get(name) for key, name in variables.items()} == {
        key: f"the settings document's scout.{key}" for key in variables
    }, "the four SCOUT_* policy variables no longer read name the key that holds what each set"
