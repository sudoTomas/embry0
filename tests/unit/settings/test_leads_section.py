"""The ``leads`` section of the settings document: the YAML an operator writes, and what it refuses.

Nothing written keeps the finders, their schedule and the readiness
thresholds as they ran before the section, except that leads are now off
until turned on. Each bound is refused naming its key and never repeating the
value. The section is read through the catalog, so each test fails with its
own message on a tree that has no leads section.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.services import lead_kinds
from embry0.settings.document import DocumentInvalid, check, load_yaml, read_sections
from embry0.settings.env import env_name
from embry0.settings.sections import CATALOG
from tests.fixtures.leads import leads_section


def _stored(document: dict[str, Any]) -> Any:
    """What a write of ``document`` stores, or each of its problems as ``(loc, msg)``."""
    try:
        return check(CATALOG, document, stored={}).data
    except DocumentInvalid as exc:
        return [(problem.loc, problem.msg) for problem in exc.problems]


def _problems(values: dict[str, Any]) -> list[tuple[tuple[str | int, ...], str]]:
    """Each problem a document holding ``leads: values`` has, as ``(loc, msg)``."""
    found = _stored({"leads": values})
    return found if isinstance(found, list) else []


def _read(data: dict[str, Any]) -> dict[str, Any] | None:
    """The leads section as a tick reads it from stored ``data``; None when there is no such section."""
    model = read_sections(CATALOG, data).models.get("leads")
    return None if model is None else model.model_dump(mode="json")


def test_the_section_reads_as_an_operator_writes_it() -> None:
    text = (
        "# fewer finders, a slower schedule, stricter tickets\n"
        "leads:\n"
        "  enabled: true\n"
        "  finders: [ticket, ci, review]\n"
        "  interval_hours: 12\n"
        "  backlog_interval_hours: 48\n"
        "  max_new_per_repo_per_day: 3\n"
        "  review_stale_days: 5\n"
        "  flaky_commits: 20\n"
        "  offer_min: 50\n"
        "  ask_min: 70\n"
    )
    data = _stored(load_yaml(text))
    assert data == {
        "leads": {
            "enabled": True,
            "finders": ["ticket", "ci", "review"],
            "interval_hours": 12.0,
            "backlog_interval_hours": 48.0,
            "max_new_per_repo_per_day": 3,
            "review_stale_days": 5,
            "flaky_commits": 20,
            "offer_min": 50,
            "ask_min": 70,
        }
    }, f"the leads section is stored as written: {data}"
    assert _read(data) == data["leads"], "and read back the same by a tick"


def test_nothing_written_is_how_leads_ran_before_the_section_but_off() -> None:
    assert _read({}) == {
        "enabled": False,
        "finders": ["ticket", "ci", "alert", "flaky", "review"],
        "interval_hours": 6.0,
        "backlog_interval_hours": 24.0,
        "max_new_per_repo_per_day": 5,
        "review_stale_days": 3,
        "flaky_commits": 10,
        "offer_min": 40,
        "ask_min": 60,
    }, (
        "every default is the value the LEADS_* variables and PROPOSALS_TICKET_MIN_READINESS had, "
        "except enabled: leads are opt-in now"
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("enabled", "true"),
        ("finders", "ci"),
        ("finders", ["ci", "ci"]),
        ("finders", ["crack"]),
        ("interval_hours", 0),
        ("interval_hours", 0.2),
        ("interval_hours", 8761),
        ("interval_hours", 1e308),
        ("backlog_interval_hours", 0.5),
        ("backlog_interval_hours", 8761),
        ("max_new_per_repo_per_day", -1),
        ("max_new_per_repo_per_day", 1001),
        ("max_new_per_repo_per_day", 2.5),
        ("review_stale_days", -1),
        ("review_stale_days", 366),
        ("flaky_commits", 0),
        ("flaky_commits", 51),
        ("offer_min", -1),
        ("offer_min", 101),
        ("offer_min", "40"),
        ("ask_min", -1),
        ("ask_min", 101),
        ("ask_min", 60.5),
    ],
)
def test_a_value_out_of_bounds_is_refused_naming_the_key(key: str, value: Any) -> None:
    """An upper bound too: past it an interval overflows a timedelta, or a threshold is not on the 0 to 100 scale."""
    leads_section()
    found = _problems({key: value})
    assert [loc[:2] for loc, _ in found] == [("leads", key)], f"{key} {value!r} must be refused as leads.{key}: {found}"


def test_an_unknown_finder_is_refused_naming_the_key_and_never_repeated() -> None:
    leads_section()
    found = _problems({"finders": ["ci", "nonsense-finder"]})
    assert [loc[:2] for loc, _ in found] == [("leads", "finders")], (
        f"a finder the loop does not have is refused when it is written, not ignored at every start: {found}"
    )
    assert all("nonsense-finder" not in msg for _, msg in found), f"a refusal never repeats the value: {found}"


def test_every_bad_value_is_named_at_once_and_none_is_repeated_back() -> None:
    leads_section()
    found = _problems({"interval_hours": 9999, "offer_min": 777, "ask_min": -42})
    assert sorted(loc for loc, _ in found) == [
        ("leads", "ask_min"),
        ("leads", "interval_hours"),
        ("leads", "offer_min"),
    ], f"every bad value is named by its own key, all at once: {found}"
    for _, msg in found:
        for value in ("9999", "777", "-42"):
            assert value not in msg, f"a refusal never repeats the value it refused: {msg!r}"


def test_no_finder_and_thresholds_in_either_order_are_accepted() -> None:
    """An empty list runs no finder, as an empty LEADS_FINDERS did; ``offer_min`` above ``ask_min``
    only means every ticket offered is also asked."""
    leads_section()
    assert _problems({"finders": []}) == [], "finders: [] is how a deployment runs no finder at all"
    assert _problems({"offer_min": 80, "ask_min": 20}) == [], "the two thresholds are not checked against each other"


def test_a_stored_value_the_code_refuses_reads_as_its_default_and_never_reaches_a_tick() -> None:
    """A value stored by a newer release (a finder this one does not have) or written to the row by hand."""
    leads_section()
    sections = read_sections(CATALOG, {"leads": {"finders": ["omens"], "ask_min": 400, "offer_min": 55}})
    model = sections.models.get("leads")
    read = None if model is None else (model.finders, model.ask_min, model.offer_min)
    assert read == (("ticket", "ci", "alert", "flaky", "review"), 60, 55), (
        "each refused key reads as its default; its sibling keeps its value"
    )
    assert sorted(r.key for r in sections.refused) == ["ask_min", "finders"]


def _convert(key: str) -> Any:
    imported = leads_section().imports().get(key)
    convert = imported.convert if imported is not None else None
    assert convert is not None, f"leads.{key} is imported through a conversion"
    return convert


@pytest.mark.parametrize(
    ("variable", "stored"),
    [
        ("ticket,ci,alert,flaky,review", ["ticket", "ci", "alert", "flaky", "review"]),
        ("review, CI", ["ci", "review"]),
        ("ci,nonsense", ["ci"]),
        ("ci,crack,weed", ["ci"]),
        ("nonsense", []),
        ("", []),
    ],
)
def test_the_finders_variable_is_imported_as_what_the_loop_ran(variable: str, stored: list[str]) -> None:
    """Before the section names were read without case or spaces, a name that is no finder was ignored
    (crack and weed did nothing), and a list naming none ran no finder."""
    assert _convert("finders")(variable) == stored, f"LEADS_FINDERS={variable!r} ran {stored}, so that is imported"


@pytest.mark.parametrize(
    ("variable", "dropped"),
    [("ci,nonsense", ["nonsense"]), ("CI, Bogus ,crack", ["bogus", "crack"]), ("ticket,ci", []), ("", [])],
)
def test_the_import_names_each_finder_name_it_drops(variable: str, dropped: list[str]) -> None:
    imported = leads_section().imports().get("finders")
    names = getattr(imported, "dropped", None)
    assert names is not None, "LEADS_FINDERS's import says which names it drops, so a warning can name them"
    assert list(names(variable)) == dropped, f"LEADS_FINDERS={variable!r} drops {dropped}"


@pytest.mark.parametrize(
    ("key", "variable", "stored"),
    [
        ("interval_hours", 0.1, 0.25),
        ("interval_hours", 6.0, 6.0),
        ("backlog_interval_hours", 0.5, 1.0),
        ("backlog_interval_hours", 12.0, 12.0),
    ],
)
def test_an_interval_below_the_loop_s_floor_is_imported_as_the_floor_it_ran_at(
    key: str, variable: float, stored: float
) -> None:
    assert _convert(key)(variable) == stored, f"{key}={variable} ran every {stored} hours, so that is imported"


def test_what_an_organization_may_override_and_where_each_key_came_from() -> None:
    section = leads_section()
    assert section.org_keys == {
        "finders",
        "max_new_per_repo_per_day",
        "review_stale_days",
        "flaky_commits",
        "offer_min",
        "ask_min",
    }, (
        "an organization chooses what one of its repositories' passes does: its finders, its daily cap, "
        "when a review is stale, how far flakiness is read, and which tickets are offered and asked; "
        "whether the one loop runs and how often stay the deployment's"
    )
    assert section.repo_keys == frozenset(), "which repositories leads read is the repository scope rule's (#165)"
    assert {key: env_name(imported.attr) for key, imported in section.imports().items()} == {
        "enabled": "LEADS_ENABLED",
        "finders": "LEADS_FINDERS",
        "interval_hours": "LEADS_INTERVAL_HOURS",
        "backlog_interval_hours": "LEADS_BACKLOG_INTERVAL_HOURS",
        "max_new_per_repo_per_day": "LEADS_MAX_NEW_PER_REPO_PER_DAY",
        "review_stale_days": "LEADS_REVIEW_STALE_DAYS",
        "flaky_commits": "LEADS_FLAKY_COMMITS",
        "offer_min": "LEADS_TICKET_MIN_SCORE",
        "ask_min": "PROPOSALS_TICKET_MIN_READINESS",
    }, "every LEADS_* variable, and the readiness floor of chat, is imported once"
    relied = section.imports()["enabled"].relied_on
    assert relied is not None, (
        "leads.enabled says how to tell a deployment that relied on LEADS_ENABLED's default, so it keeps leads on"
    )
    assert getattr(relied, "value", None) is True, "what such a deployment keeps is declared: enabled: true"


def test_the_finders_are_the_lead_kinds_a_finder_stores_and_the_ones_the_loop_runs() -> None:
    """The section's list, the loop's finders and the lead catalogue name the same five kinds: a finder
    the section accepted and the loop did not have would be accepted and run nothing."""
    from embry0.services.leads.loop import LeadsLoop

    section = leads_section()
    finders = section.defaults().finders
    # crack and weed are lead kinds the scout and knowledge maps store, not a finder.
    found_by_a_finder = set(lead_kinds.ids(lead=True)) - {"crack", "weed"}
    assert set(finders) == found_by_a_finder, f"each finder stores its own lead kind: {finders}"
    loop = LeadsLoop.__new__(LeadsLoop)
    run = {"ticket"} | {kind for kind, _ in loop._github_finders("acme/widgets", None, None, section.defaults())}  # noqa: SLF001
    assert run == set(finders), f"the loop runs exactly the finders the section names: {sorted(run)}"
