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
        "  ask_severities:\n"
        "    crack: [medium, high, critical]\n"
        "    weed: []\n"
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
            "ask_severities": {"crack": ["medium", "high", "critical"], "weed": []},
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
        "ask_severities": {"crack": ["high", "critical"], "weed": ["high", "critical"]},
    }, (
        "every default is the value the LEADS_* variables and PROPOSALS_TICKET_MIN_READINESS had, "
        "except enabled: leads are opt-in now; and which severities of a crack or a weed are asked in "
        "chat is what the code held before (#166)"
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


@pytest.mark.parametrize(
    "value",
    [
        {"omen-kind": ["high"]},
        {"crack": ["urgent-ish"]},
        {"crack": ["high", "high"]},
        {"slop": ["high"]},
        {"finding": ["high"]},
        {"issue": []},
        {"crack": ["warning"]},
        {"crack": "high"},
        {"crack": [3]},
        {"omen-kind": "high"},
        {"omen-kind": [7]},
        ["crack"],
    ],
)
def test_ask_severities_names_only_kinds_that_carry_a_severity_and_their_severities(value: Any) -> None:
    """Which leads reach chat is the operator's (#166). Refused, naming the key and nothing below it: a kind the
    catalogue does not have, one whose proposals carry no severity (an open issue, a context audit finding, slop),
    a severity its kind cannot carry, a severity twice, or a value of another shape. The 422's loc never holds
    the operator's own kind, and its message never repeats what was written."""
    leads_section()
    assert _problems({"ask_severities": {"crack": ["high"]}}) == [], "ask_severities is a key of the leads section"
    found = _problems({"ask_severities": value})
    assert found and all(loc == ("leads", "ask_severities") for loc, _ in found), (
        f"{value!r} must be refused as leads.ask_severities, and nothing below it: {found}"
    )
    for _, msg in found:
        for written in ("omen-kind", "urgent-ish"):
            assert written not in msg, f"a refusal never repeats the value it refused: {msg!r}"


def test_ask_severities_takes_each_kind_that_carries_a_severity_and_an_empty_list() -> None:
    leads_section()
    assert _problems({"ask_severities": {"crack": ["high"]}}) == [], "ask_severities is a key of the leads section"
    rated = lead_kinds.rated()
    assert set(rated) == {"case", *lead_kinds.ids(lead=True)}, "the watcher's cases and the leads carry a severity"
    for kind, severities in rated.items():
        assert _problems({"ask_severities": {kind: list(severities)}}) == [], f"{kind} may be named"
    assert _problems({"ask_severities": {"case": ["warning", "unrated"]}}) == [], "a case's own severities"
    assert _problems({"ask_severities": {"weed": []}}) == [], "weed: [] asks no weed in chat"
    assert _problems({"ask_severities": {}}) == [], "an empty mapping asks every kind at every severity"


def _lead(kind: str, repo: str, severity: str) -> Any:
    from embry0.services.proposals import lead_proposal

    return lead_proposal(
        {"id": f"{kind}:1", "kind": kind, "repo": repo, "title": "t", "first_seen": None, "severity": severity}
    )


def test_an_organization_s_layer_says_which_severities_its_people_are_asked_about() -> None:
    from embry0.services.proposal_briefs import held_by
    from tests.fixtures.leads import leads_policy

    policy = leads_policy(organizations={"acme": {"ask_severities": {"crack": ["medium", "high", "critical"]}}})
    assert held_by(_lead("crack", "acme/web", "medium"), policy) is False, "acme's layer asks its medium cracks"
    assert held_by(_lead("crack", "globex/api", "medium"), policy) is True, "every other repository keeps the default"
    assert held_by(_lead("crack", "acme/web", "low"), policy) is True, "and a low crack is still held in acme"


def test_an_organization_s_layer_overrides_only_the_kinds_it_names() -> None:
    """The reviewer's probe: an organization that restricts alerts must not reopen the deployment's cracks and
    weeds. Its layer is merged kind by kind over the deployment's mapping, its default included; every other
    key of a layer still replaces the inherited value whole."""
    from embry0.services.proposal_briefs import held_by
    from tests.fixtures.leads import leads_policy

    policy = leads_policy(organizations={"acme": {"ask_severities": {"alert": ["critical"]}}})
    acme = policy.repo("acme/web").ask_severities
    assert acme == {"crack": ("high", "critical"), "weed": ("high", "critical"), "alert": ("critical",)}, (
        f"acme reads its own alert entry and the deployment's crack and weed entries: {acme}"
    )
    held = {
        f"{kind} {severity}": held_by(_lead(kind, "acme/web", severity), policy)
        for kind, severity in (("crack", "medium"), ("weed", "low"), ("alert", "high"), ("alert", "critical"))
    }
    assert held == {"crack medium": True, "weed low": True, "alert high": True, "alert critical": False}, held

    written = leads_policy(
        {"ask_severities": {"crack": ["critical"]}},
        organizations={"acme": {"ask_severities": {"weed": [], "ci": ["high", "critical"]}}},
    )
    assert written.repo("acme/web").ask_severities == {"crack": ("critical",), "weed": (), "ci": ("high", "critical")}
    assert written.repo("globex/api").ask_severities == {"crack": ("critical",), "weed": ("high", "critical")}, (
        "the deployment's document merges over the default too: its crack entry, the default's weed entry"
    )


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
        "ask_severities",
    }, (
        "an organization chooses what one of its repositories' passes does: its finders, its daily cap, "
        "when a review is stale, how far flakiness is read, which tickets are offered and asked, and which "
        "severities of a kind its people are asked about in chat; whether the one loop runs and how often "
        "stay the deployment's"
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


def test_the_deployment_writes_ask_severities_entry_by_entry_over_the_default() -> None:
    """The reviewer's probe: the deployment's own document writing ``{alert: [critical]}`` must not open every
    crack and weed. It merges over the default, kind by kind, as an organization's layer merges over it."""
    from embry0.services.proposal_briefs import held_by
    from tests.fixtures.leads import leads_policy

    leads_section()
    policy = leads_policy({"ask_severities": {"alert": ["critical"]}})
    deployment = policy.deployment().ask_severities
    assert deployment == {"crack": ("high", "critical"), "weed": ("high", "critical"), "alert": ("critical",)}, (
        f"the deployment's entry for alerts, the default's for cracks and weeds: {deployment}"
    )
    held = {
        f"{kind} {severity}": held_by(_lead(kind, "globex/api", severity), policy)
        for kind, severity in (("crack", "medium"), ("weed", "low"), ("alert", "high"), ("alert", "critical"))
    }
    assert held == {"crack medium": True, "weed low": True, "alert high": True, "alert critical": False}, held
    opened = leads_policy({"ask_severities": {"crack": list(lead_kinds.SEVERITIES), "weed": []}}).deployment()
    assert opened.ask_severities == {"crack": tuple(lead_kinds.SEVERITIES), "weed": ()}, (
        "a kind the deployment names replaces the default's entry: every severity of a crack, no weed"
    )


def test_the_effective_view_gives_each_kind_of_a_merged_key_its_source() -> None:
    """``GET /config/settings/effective`` marks a key merged entry by entry and says, per kind, which document
    its entry comes from: the default, the deployment's document or an organization's layer."""
    import os
    from unittest.mock import patch

    from embry0.config import Embry0Config
    from embry0.settings.effective import effective_view
    from embry0.settings.scope import DEPLOYMENT
    from embry0.settings.store import build_snapshot

    leads_section()
    rows = [
        {"scope": DEPLOYMENT, "data": {"leads": {"ask_severities": {"alert": ["critical"]}}}, "generation": 1},
        {"scope": "organization:acme", "data": {"leads": {"ask_severities": {"weed": []}}}, "generation": 1},
    ]
    snapshot = build_snapshot(CATALOG, [{**row, "updated_at": None} for row in rows], token=(), log=False)
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None)  # type: ignore[call-arg]

    def key_of(view: dict[str, Any]) -> dict[str, Any]:
        leads = next(s for s in view["sections"] if s["name"] == "leads")
        return next(k for k in leads["keys"] if k["key"] == "ask_severities")

    deployment = key_of(effective_view(CATALOG, snapshot, [], config))
    assert deployment.get("merged_by_entry") is True, f"the key is marked merged entry by entry: {deployment}"
    assert [(e["entry"], e["value"], e["source"]) for e in deployment["entries"]] == [
        ("crack", ["high", "critical"], "default"),
        ("weed", ["high", "critical"], "default"),
        ("alert", ["critical"], "document"),
    ], deployment["entries"]
    acme = key_of(effective_view(CATALOG, snapshot, {}, config, owner="acme"))
    assert [(e["entry"], e["source"]) for e in acme["entries"]] == [
        ("crack", "default"),
        ("weed", "organization"),
        ("alert", "document"),
    ], acme["entries"]
    finders = next(k for s in effective_view(CATALOG, snapshot, [], config)["sections"] for k in s["keys"])
    assert "entries" not in finders, "only a merged key carries entries"
