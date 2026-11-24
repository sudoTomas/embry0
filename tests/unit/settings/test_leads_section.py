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

from embry0.config_retired import MOVED_TO_DOCUMENTS
from embry0.services import lead_kinds
from embry0.settings.document import DocumentInvalid, check, load_yaml, read_sections
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
    read = _read(data)
    assert read is not None and {key: read[key] for key in data["leads"]} == data["leads"], (
        "and read back the same by a tick"
    )


def test_nothing_written_is_how_leads_ran_before_the_section_but_off() -> None:
    assert _read({}) == {
        "enabled": False,
        "finders": ["ticket", "ci", "alert", "flaky", "review"],
        "interval_hours": 6.0,
        "backlog_interval_hours": 24.0,
        "labels": ["embry0-lead"],
        "max_new_per_repo_per_day": 5,
        "review_stale_days": 3,
        "flaky_commits": 10,
        "offer_min": 40,
        "ask_min": 60,
        "ask_severities": {"crack": ["high", "critical"], "weed": ["high", "critical"]},
        "readiness_labels": {
            "ready": [
                "bug",
                "chore",
                "dependencies",
                "docs",
                "documentation",
                "maintenance",
                "refactor",
                "tech debt",
                "tech-debt",
                "test",
                "tests",
            ],
            "small": [
                "easy",
                "good first issue",
                "good-first-issue",
                "s",
                "size/s",
                "size/xs",
                "size:s",
                "size:xs",
                "small",
                "xs",
            ],
            "large": ["l", "large", "size/l", "size/xl", "size:l", "size:xl", "xl"],
            "not_ready": [
                "blocked",
                "design",
                "discussion",
                "epic",
                "idea",
                "needs design",
                "needs discussion",
                "needs-design",
                "question",
                "research",
                "rfc",
                "spike",
                "wontfix",
            ],
            "urgent": ["critical", "p0", "security", "sev1", "urgent"],
            "high": ["high", "p1", "regression", "sev2"],
        },
        "readiness_weights": {
            "description_short": 5,
            "description": 20,
            "description_full": 25,
            "paths": 20,
            "error": 10,
            "done": 15,
            "ready_label": 10,
            "small": 10,
            "large": -10,
            "not_ready": -30,
            "short_title": -10,
        },
        "done_phrases": [
            "acceptance criteria",
            "done when",
            "definition of done",
            "expected behavior",
            "expected behaviors",
            "expected behaviour",
            "expected behaviours",
            "expected result",
            "expected results",
            "steps to reproduce",
            "should",
            "- [ ]",
        ],
        "epic_title_words": ["epic"],
        "small_estimate_max": 2.0,
        "large_estimate_min": 5.0,
    }, (
        "every default is the value the LEADS_* variables and PROPOSALS_TICKET_MIN_READINESS had, "
        "except enabled: leads are opt-in now; which severities of a crack or a weed are asked in "
        "chat is what the code held before (#166); and the words a ticket's readiness is read in are "
        "the ones the backlog intake held in its code (#168)"
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


def test_what_an_organization_may_override_and_where_each_key_came_from() -> None:
    section = leads_section()
    assert section.org_keys == {
        "finders",
        "labels",
        "max_new_per_repo_per_day",
        "review_stale_days",
        "flaky_commits",
        "offer_min",
        "ask_min",
        "ask_severities",
        "readiness_labels",
        "readiness_weights",
        "done_phrases",
        "epic_title_words",
        "small_estimate_max",
        "large_estimate_min",
    }, (
        "an organization chooses what one of its repositories' passes does: its finders, its issues' labels, "
        "its daily cap, "
        "when a review is stale, how far flakiness is read, which tickets are offered and asked, the words "
        "its tickets' readiness is read in, and which severities of a kind its people are asked about in "
        "chat; whether the one loop runs and how often stay the deployment's"
    )
    assert section.merged_keys == {"ask_severities", "readiness_labels", "readiness_weights"}, (
        "the mappings a layer writes entry by entry: kinds, label roles and signals"
    )
    assert section.repo_keys == frozenset(), "which repositories leads read is the repository scope rule's (#165)"
    variables = {
        "enabled": "LEADS_ENABLED",
        "finders": "LEADS_FINDERS",
        "interval_hours": "LEADS_INTERVAL_HOURS",
        "backlog_interval_hours": "LEADS_BACKLOG_INTERVAL_HOURS",
        "max_new_per_repo_per_day": "LEADS_MAX_NEW_PER_REPO_PER_DAY",
        "review_stale_days": "LEADS_REVIEW_STALE_DAYS",
        "flaky_commits": "LEADS_FLAKY_COMMITS",
        "offer_min": "LEADS_TICKET_MIN_SCORE",
        "ask_min": "PROPOSALS_TICKET_MIN_READINESS",
    }
    assert {key: MOVED_TO_DOCUMENTS.get(name) for key, name in variables.items()} == {
        key: f"the settings document's leads.{key}" for key in variables
    }, "every LEADS_* variable, and the readiness floor of chat, no longer read names the key that holds it"


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
    from embry0.settings.effective import effective_view
    from embry0.settings.scope import DEPLOYMENT
    from embry0.settings.store import build_snapshot

    leads_section()
    rows = [
        {"scope": DEPLOYMENT, "data": {"leads": {"ask_severities": {"alert": ["critical"]}}}, "generation": 1},
        {"scope": "organization:acme", "data": {"leads": {"ask_severities": {"weed": []}}}, "generation": 1},
    ]
    snapshot = build_snapshot(CATALOG, [{**row, "updated_at": None} for row in rows], token=(), log=False)

    def key_of(view: dict[str, Any]) -> dict[str, Any]:
        leads = next(s for s in view["sections"] if s["name"] == "leads")
        return next(k for k in leads["keys"] if k["key"] == "ask_severities")

    deployment = key_of(effective_view(CATALOG, snapshot, []))
    assert deployment.get("merged_by_entry") is True, f"the key is marked merged entry by entry: {deployment}"
    assert [(e["entry"], e["value"], e["source"]) for e in deployment["entries"]] == [
        ("crack", ["high", "critical"], "default"),
        ("weed", ["high", "critical"], "default"),
        ("alert", ["critical"], "document"),
    ], deployment["entries"]
    acme = key_of(effective_view(CATALOG, snapshot, {}, owner="acme"))
    assert [(e["entry"], e["source"]) for e in acme["entries"]] == [
        ("crack", "default"),
        ("weed", "organization"),
        ("alert", "document"),
    ], acme["entries"]
    finders = next(k for s in effective_view(CATALOG, snapshot, [])["sections"] for k in s["keys"])
    assert "entries" not in finders, "only a merged key carries entries"


# The words a ticket's readiness is read in (#168, spec 2026-11-23).

#: A value each vocabulary key takes, so a test of what it refuses first shows the key exists: a section without
#: it refuses every value as an unknown key, which would pass for the refusal tested.
VOCABULARY = {
    "readiness_labels": {"ready": ["kind/bug"]},
    "readiness_weights": {"done": 20},
    "done_phrases": ["acceptance criteria"],
    "epic_title_words": ["initiative"],
    "small_estimate_max": 3,
    "large_estimate_min": 8,
}


def _vocabulary_is_a_key(key: str) -> None:
    assert _problems({key: VOCABULARY[key]}) == [], f"{key} is a key of the leads section and takes a good value"


def test_an_organization_s_vocabulary_reads_as_its_operator_writes_it() -> None:
    text = (
        "leads:\n"
        "  readiness_labels:\n"
        "    ready: [kind/bug, Type::Bug]\n"
        "    not_ready: []\n"
        "  readiness_weights: {done: 20, short_title: 0}\n"
        "  done_phrases: [acceptance criteria, Akzeptanzkriterien, '- [x]']\n"
        "  epic_title_words: [initiative]\n"
        "  small_estimate_max: 3\n"
        "  large_estimate_min: 8\n"
    )
    stored = _stored(load_yaml(text))
    assert not isinstance(stored, list), f"a vocabulary in other words and another language is accepted: {stored}"
    read = _read(stored)
    assert read is not None
    assert (read["readiness_labels"]["ready"], read["readiness_labels"]["not_ready"]) == (
        ["kind/bug", "Type::Bug"],
        [],
    ), "labels are kept as written; they are compared case-folded"
    assert read["readiness_labels"]["urgent"] == ["critical", "p0", "security", "sev1", "urgent"], (
        "a role the document does not name keeps the default's labels"
    )
    assert (read["readiness_weights"]["done"], read["readiness_weights"]["paths"]) == (20, 20)
    assert (read["small_estimate_max"], read["large_estimate_min"]) == (3, 8)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("readiness_labels", {"omen-role": ["x"]}),
        ("readiness_labels", {"ready": "bug"}),
        ("readiness_labels", {"ready": [7]}),
        ("readiness_labels", ["ready"]),
        ("readiness_labels", {"ready": ["bug", " BUG "]}),
        ("readiness_labels", {"ready": ["  "]}),
        ("readiness_labels", {"ready": ["x" * 65]}),
        ("readiness_labels", {"ready": [f"label-{n}" for n in range(65)]}),
        ("readiness_labels", {"ready": ["kind\tbug"]}),
        ("readiness_weights", {"omen-signal": 5}),
        ("readiness_weights", {"done": 101}),
        ("readiness_weights", {"done": -101}),
        ("readiness_weights", {"done": 2.5}),
        ("readiness_weights", {"done": True}),
        ("readiness_weights", {"done": "5"}),
        ("readiness_weights", [5]),
        ("done_phrases", "should"),
        ("done_phrases", ["should", "Should"]),
        ("done_phrases", [""]),
        ("done_phrases", ["x" * 81]),
        ("done_phrases", [f"phrase {n}" for n in range(101)]),
        ("done_phrases", ["done\nwhen"]),
        ("done_phrases", ["done‮when"]),
        ("epic_title_words", ["epic", "EPIC"]),
        ("epic_title_words", ["x" * 41]),
        ("epic_title_words", [f"w{n}" for n in range(33)]),
        ("small_estimate_max", -1),
        ("small_estimate_max", 1001),
        ("large_estimate_min", "5"),
    ],
)
def test_a_vocabulary_value_out_of_bounds_is_refused_naming_the_key(key: str, value: Any) -> None:
    _vocabulary_is_a_key(key)
    found = _problems({key: value})
    assert found and all(loc[:2] == ("leads", key) for loc, _ in found), (
        f"{value!r} must be refused as leads.{key}: {found}"
    )
    assert all(len(loc) == 2 or isinstance(loc[2], int) for loc, _ in found), (
        f"a refusal's place names the key, at most an index, never a name the operator wrote: {found}"
    )


def test_every_vocabulary_problem_is_named_at_once_and_none_is_repeated_back() -> None:
    for key in VOCABULARY:
        _vocabulary_is_a_key(key)
    found = _problems(
        {
            "readiness_labels": {"zz-omen-role": ["zz-a"], "ready": ["zz-dup", "ZZ-DUP"], "small": ["zz-" * 30]},
            "readiness_weights": {"zz-omen-signal": 5, "done": 777},
            "done_phrases": ["zz-should", "ZZ-Should"],
        }
    )
    assert sorted(loc for loc, _ in found) == [
        ("leads", "done_phrases"),
        ("leads", "readiness_labels"),
        ("leads", "readiness_weights"),
    ], f"every bad key is named by its own key, all at once: {found}"
    messages = dict(((loc[1], msg) for loc, msg in found))
    assert "name only the roles" in messages["readiness_labels"], messages
    assert "ready lists a label twice" in messages["readiness_labels"], messages
    assert "small lists a label longer than 64 characters" in messages["readiness_labels"], messages
    assert "name only the signals" in messages["readiness_weights"], messages
    assert "done is worth from -100 to 100 points" in messages["readiness_weights"], messages
    for _, msg in found:
        assert "zz-" not in msg.casefold() and "777" not in msg, f"a refusal never repeats what was written: {msg!r}"


def test_an_organization_s_layer_replaces_the_label_roles_and_signals_it_names_and_inherits_the_rest() -> None:
    from tests.fixtures.leads import leads_policy

    _vocabulary_is_a_key("readiness_labels")
    assert {"readiness_labels", "readiness_weights"} <= leads_section().merged_keys, (
        "a layer merges the label roles and signals it names over what it inherits"
    )
    policy = leads_policy(
        {"readiness_labels": {"small": ["xs"]}, "readiness_weights": {"done": 30}},
        organizations={
            "acme": {"readiness_labels": {"ready": ["kind/bug"]}, "readiness_weights": {"paths": 5}},
        },
    )
    acme, globex = policy.repo("acme/web"), policy.repo("globex/api")
    assert acme.readiness_labels["ready"] == ("kind/bug",), "acme's ready labels replace the role whole"
    assert acme.readiness_labels["small"] == ("xs",), "the role acme does not name is the deployment's"
    assert acme.readiness_labels["not_ready"] == globex.readiness_labels["not_ready"] != (), (
        "and a role neither names is the default's"
    )
    assert (acme.readiness_weights["paths"], acme.readiness_weights["done"]) == (5, 30), (
        "acme's paths weight, the deployment's done weight"
    )
    assert (globex.readiness_labels["ready"][0], globex.readiness_weights["paths"]) == ("bug", 20), (
        "an organization without a layer reads the deployment's document over the defaults"
    )


def test_an_organization_s_lists_of_phrases_and_words_replace_the_inherited_ones_whole() -> None:
    from tests.fixtures.leads import leads_policy

    _vocabulary_is_a_key("done_phrases")
    policy = leads_policy(organizations={"acme": {"done_phrases": ["akzeptanzkriterien"], "epic_title_words": []}})
    assert policy.repo("acme/web").done_phrases == ("akzeptanzkriterien",)
    assert policy.repo("acme/web").epic_title_words == ()
    assert "should" in policy.repo("globex/api").done_phrases


@pytest.mark.parametrize(
    ("key", "value", "says"),
    [
        ("readiness_labels", ["ready"], "write a mapping of role to a list of labels"),
        ("readiness_weights", [5], "write a mapping of signal to a whole number of points"),
        ("done_phrases", "should", "write a list of phrases"),
        ("epic_title_words", {"epic": 1}, "write a list of words"),
    ],
)
def test_a_vocabulary_key_of_another_shape_says_what_shape_to_write(key: str, value: Any, says: str) -> None:
    _vocabulary_is_a_key(key)
    found = _problems({key: value})
    assert [(loc, says in msg) for loc, msg in found] == [(("leads", key), True)], f"{says}: {found}"


def test_a_value_of_the_wrong_shape_hides_none_of_its_key_s_other_problems() -> None:
    """A weight that is not a whole number, a role's labels that are not a list or a phrase that is not text is one
    problem among the key's others, all named in one 422."""
    _vocabulary_is_a_key("readiness_weights")
    found = dict(
        (loc[1], msg)
        for loc, msg in _problems(
            {
                "readiness_weights": {"done": 101, "zz-omen": 5, "ready_label": 1.5},
                "readiness_labels": {"ready": "bug", "zz-omen": ["x"], "small": [1, "zz-x", "ZZ-X"]},
                "done_phrases": [1, "zz-a", "ZZ-A", ""],
            }
        )
    )
    expected = {
        "readiness_weights": [
            "name only the signals",
            "done is worth from -100 to 100 points",
            "ready_label is a whole number of points",
        ],
        "readiness_labels": [
            "name only the roles",
            "ready is a list of labels",
            "small lists a label that is not text",
            "small lists a label twice",
        ],
        "done_phrases": ["lists a phrase that is not text", "lists an empty phrase", "lists a phrase twice"],
    }
    for key, parts in expected.items():
        assert key in found and all(part in found[key] for part in parts), f"leads.{key} names {parts}: {found}"
        assert "zz-" not in found[key].casefold(), f"and repeats nothing written: {found[key]}"


def test_a_phrase_may_join_its_parts_as_persian_and_emoji_do_and_holds_no_other_format_character() -> None:
    """A zero-width non-joiner and joiner are parts of a word (Unicode's word boundaries, UAX #29): a Persian phrase
    holds one. Every other format character, such as a bidirectional override, is still refused."""
    _vocabulary_is_a_key("done_phrases")
    assert _problems({"done_phrases": ["\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645", "a\u200db"]}) == [], (
        "a phrase holding a zero-width non-joiner or joiner is refused"
    )
    assert _problems({"readiness_labels": {"ready": ["\u0628\u0627\u06af\u200c\u0647\u0627"]}}) == []
    for other in ("done\u202ewhen", "done\u200bwhen", "done\u2066when"):
        assert [loc for loc, _ in _problems({"done_phrases": [other]})] == [("leads", "done_phrases")], other


@pytest.mark.parametrize(
    ("value", "named"),
    [
        (
            {"crack": "high", "zz-omen-kind": ["high"], "weed": ["zz-urgent-ish"]},
            [
                "crack is a list of severities",
                "name only kinds whose proposals carry a severity",
                "weed lists a severity",
            ],
        ),
        (
            {"crack": [3], "weed": ["high", "high"]},
            ["crack lists a severity that is not text", "weed names a severity twice"],
        ),
        ({"weed": ["zz-x", "high", "high"]}, ["weed lists a severity that is none of", "weed names a severity twice"]),
        ({"weed": [3, "zz-x"]}, ["weed lists a severity that is not text", "weed lists a severity that is none of"]),
        (
            {"crack": [3, "high", "high"], "alert": [{"zz-k": 1}, ["zz-v"], "critical"]},
            [
                "crack lists a severity that is not text",
                "crack names a severity twice",
                "alert lists a severity that is not text",
            ],
        ),
        ({"crack": {"zz-high": 1}, "alert": ["zz-x"]}, ["crack is a list of severities", "alert lists a severity"]),
    ],
)
def test_ask_severities_names_every_problem_at_once_a_wrong_shape_included(value: Any, named: list[str]) -> None:
    """An entry of the wrong shape is one problem among the key's others, not the only one reported (#655)."""
    assert _problems({"ask_severities": {"crack": ["high"]}}) == [], "ask_severities is a key of the leads section"
    found = _problems({"ask_severities": value})
    assert [loc for loc, _ in found] == [("leads", "ask_severities")], f"one problem, at the key: {found}"
    message = found[0][1]
    assert all(part in message for part in named), f"every problem is named, {named}: {message!r}"
    assert "zz-" not in message.casefold(), f"and none of what was written: {message!r}"


def test_ask_severities_of_another_shape_than_a_mapping_says_what_to_write() -> None:
    found = _problems({"ask_severities": ["crack"]})
    assert [(loc, "write a mapping of kind to a list of severities" in msg) for loc, msg in found] == [
        (("leads", "ask_severities"), True)
    ], found
