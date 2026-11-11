"""What a template's or an agent's row says about its behaviour (#167), on its own.

The second half proves that every built-in row, as seeded, answers exactly
what the code said by its name before (tests/fixtures/name_lists_before_rows.py).
"""

from __future__ import annotations

import pytest

from embry0.orchestration.state import JobKind
from embry0.services import job_briefs
from embry0.storage.repositories.agent_definitions import BUILTIN_SEED
from embry0.storage.seeds.pipeline_templates_builtin import BUILTIN_PIPELINE_TEMPLATES
from embry0.workflows import row_behaviour as rb
from embry0.workflows._validation import FIXED_NODE_TYPES
from embry0.workflows.issue_to_pr.route_plan import build_route_plan, coding_agent_of
from tests.fixtures import name_lists_before_rows as before

# --- the helper on its own ----------------------------------------------------------------------------------


def test_a_row_without_the_columns_reads_as_a_template_of_the_deployments_own() -> None:
    for row in (None, {}, {"name": "mine", "fixed_route": None, "offered_to_triage": None}):
        assert rb.fixed_route(row) is False, f"{row!r} is no fixed route"
        assert rb.offered_to_triage(row) is True, f"{row!r} is offered to triage"
        assert rb.recorded_kind(row) is None, f"{row!r} records no kind"
        assert rb.delivers(row) == "", f"{row!r} says nothing it delivers"
        assert rb.leader(row) is None, f"{row!r} has no leader"


def _graph(*agents: str) -> dict:
    return {"nodes": [{"node_id": a, "agent_type": a} for a in ("triage", *agents)]}


def test_the_kind_a_template_records_is_its_default_kind_then_its_override_then_its_route() -> None:
    assert rb.recorded_kind({"default_for_kind": "ops", "job_kind": "analysis"}) == "ops"
    assert rb.recorded_kind({"job_kind": "research", "graph_definition": _graph("why")}) == "research", (
        "job_kind overrides what the route makes it"
    )
    assert rb.recorded_kind({"default_for_kind": "", "job_kind": ""}) is None


def test_a_route_of_specialists_only_is_analysis_whatever_it_is_named() -> None:
    """The column is an override; NULL reads the graph as it is now, as specialist_job_kind did (#167)."""
    assert rb.recorded_kind({"name": "my-pass", "graph_definition": _graph("why", "how", "output")}) == "analysis"
    assert rb.recorded_kind({"name": "why", "graph_definition": _graph("why", "developer")}) is None, (
        "a specialist route given a coding step since is no analysis"
    )
    assert rb.derived_kind(_graph("output")) is None and rb.derived_kind(None) is None
    assert rb.derived_kind(_graph("analysis")) is None, "the analysis agent is no specialist"


def test_the_coding_agents_are_the_developer_nodes_fixed_types() -> None:
    assert set(rb.CODING_AGENT_TYPES) <= FIXED_NODE_TYPES, "a coding agent is a node of the graph's vocabulary"
    assert rb.DEFAULT_CODER in rb.CODING_AGENT_TYPES


def test_the_job_kinds_are_the_job_kind_vocabulary() -> None:
    assert set(rb.JOB_KINDS) == {k.value for k in JobKind}


def test_a_station_is_one_the_hall_draws() -> None:
    assert rb.station({"station": "anvil"}) == "anvil"
    assert rb.station({"station": "throne"}) == "desk", "a station the art lacks is drawn as a desk"
    assert rb.station(None) == rb.station({}) == "desk"


def test_a_quest_template_is_none_triage_or_a_template() -> None:
    assert rb.quest({"quest_template": None}) == rb.Quest(False, None), "NULL: the guild gives it no task"
    assert rb.quest({}) == rb.Quest(False, None)
    assert rb.quest({"quest_template": ""}) == rb.Quest(True, None), "'': a task that names no template"
    assert rb.quest({"quest_template": "why"}) == rb.Quest(True, "why")
    assert rb.quest({"quest_template": "why"}, templates={"why"}) == rb.Quest(True, "why")
    assert rb.quest({"quest_template": "gone"}, templates={"why"}) == rb.Quest(False, None), (
        "a task naming a template that no longer exists would run the kind default: it is no task"
    )
    assert rb.quest({"quest_template": ""}, templates=set()) == rb.Quest(True, None)


@pytest.mark.parametrize(
    ("check", "value", "field"),
    [
        (rb.checked_job_kind, "chores", "job_kind"),
        (rb.checked_job_kind, 3, "job_kind"),
        (rb.checked_delivers, "a\nb", "delivers"),
        (rb.checked_delivers, "x" * (rb.DELIVERS_MAX + 1), "delivers"),
        (rb.checked_delivers, None, "delivers"),
        (rb.checked_leader_agent, "", "leader_agent"),
        (rb.checked_leader_agent, 7, "leader_agent"),
        (rb.checked_quest_template, "x" * (rb.NAME_MAX + 1), "quest_template"),
        (rb.checked_quest_template, ["why"], "quest_template"),
        (rb.checked_station, "throne", "station"),
        (rb.checked_station, None, "station"),
    ],
)
def test_a_malformed_value_is_refused_naming_its_field(check, value, field) -> None:
    with pytest.raises(ValueError, match=field):
        check(value)


def test_a_well_formed_value_is_stored_trimmed() -> None:
    assert rb.checked_job_kind(None) is None and rb.checked_job_kind("research") == "research"
    assert rb.checked_delivers("  a report  ") == "a report"
    assert rb.checked_leader_agent(None) is None and rb.checked_leader_agent(" why ") == "why"
    assert rb.checked_quest_template("") == "" and rb.checked_quest_template(None) is None
    assert rb.checked_station("map_table") == "map_table"


def test_a_task_naming_no_template_is_a_coding_agents_only() -> None:
    assert rb.untemplated_task_problem("developer", "") is None
    assert rb.untemplated_task_problem("slopmaster", "") is None
    assert rb.untemplated_task_problem("scout", "why") is None and rb.untemplated_task_problem("scout", None) is None
    problem = rb.untemplated_task_problem("scout", "")
    assert problem is not None and problem.startswith("quest_template: ") and "'scout'" in problem, problem


def test_a_template_cannot_take_a_code_pipelines_name() -> None:
    for name in rb.WORKFLOW_PIPELINES:
        problem = rb.template_name_problem(name)
        assert problem is not None and problem.startswith("name: "), (name, problem)
    assert rb.template_name_problem(" qa ") is not None
    assert rb.template_name_problem("qa-pass") is None and rb.template_name_problem(None) is None


def test_what_names_nothing_is_said() -> None:
    assert rb.leader_problem("ghost", {"why"}) == "leader_agent: no agent named 'ghost' runs in jobs"
    assert rb.leader_problem("why", {"why"}) is None and rb.leader_problem("ghost", None) is None
    assert rb.quest_template_problem("ghost", {"why"}) == "quest_template: no pipeline template named 'ghost'"
    assert rb.quest_template_problem("", {"why"}) is None, "'' names no template on purpose"
    assert rb.quest_template_problem("ghost", None) is None, "without the templates nothing is checked"


def test_the_pipelines_a_job_can_name_are_the_rows_and_the_codes_own() -> None:
    rows = rb.pipelines_by_name([{"name": "mine", "leader_agent": "why"}])
    assert set(rows) == {"mine", "qa", "pr-review", "onboard"}
    assert rb.leader(rows["mine"]) == "why" and rb.leader(rows["qa"]) == "qa"
    assert rb.pipelines_by_name([{"name": "qa", "leader_agent": "x"}])["qa"]["leader_agent"] == "x", (
        "a row would win over the code's pipeline, which is why no template may take its name"
    )


def test_the_codes_pipelines_are_the_workflows_with_no_template_row() -> None:
    from embry0.workflows.onboard.graph import OnboardWorkflow
    from embry0.workflows.pr_review.graph import PrReviewWorkflow
    from embry0.workflows.qa.graph import QAWorkflow

    assert set(rb.WORKFLOW_PIPELINES) == {QAWorkflow.name, PrReviewWorkflow.name, OnboardWorkflow.name}
    assert not set(rb.WORKFLOW_PIPELINES) & set(BUILTIN_PIPELINE_TEMPLATES), "no built-in template shares a name"


# --- every built-in row answers what the code said by its name --------------------------------------------


#: What a built-in template delivers that changed on purpose after #167, and why.
DELIVERS_SINCE: dict[str, str] = {
    # Spec 2026-11-10: a map's areas wait for a person in embry0; no pull request is opened (migration 145).
    "map-areas": "the repository's areas, for a person to apply",
}


def _delivers(name: str, kind: str) -> str:
    return DELIVERS_SINCE.get(name) or before.delivers(name, kind)


@pytest.mark.parametrize("name", sorted(BUILTIN_PIPELINE_TEMPLATES))
def test_each_builtin_template_says_what_its_name_said(name: str) -> None:
    seed = BUILTIN_PIPELINE_TEMPLATES[name]
    row = rb.builtin_template(name)
    assert row is not None and row["name"] == name
    old_kind = before.recorded_kind(name, seed["graph_definition"], seed.get("default_for_kind"))
    assert rb.fixed_route(row) is (name in before.SPECIALIST_TEMPLATE_NAMES), f"{name}: fixed_route"
    assert rb.offered_to_triage(row) is (name not in before.SPECIALIST_TEMPLATE_NAMES), f"{name}: offered_to_triage"
    assert rb.recorded_kind(row) == old_kind, f"{name}: the kind its jobs are recorded as"
    assert rb.leader(row) == before.LEADER_BY_TEMPLATE.get(name), f"{name}: its leader"
    plan = build_route_plan(seed["graph_definition"])
    assert coding_agent_of(plan) == before.coder(name), f"{name}: whom plan_route records its pull request is to"
    for kind in (*rb.JOB_KINDS, ""):
        job = {"pipeline_template": name, "job_kind": kind}
        assert job_briefs.what_it_delivers(job) == _delivers(name, kind), f"{name}/{kind}: what it delivers"
        read = {**job, "template_delivers": rb.delivers(row)}
        assert job_briefs.what_it_delivers(read) == _delivers(name, kind), f"{name}/{kind}, read from the row"


@pytest.mark.parametrize("name", sorted(rb.WORKFLOW_PIPELINES))
def test_each_pipeline_the_code_defines_says_what_its_name_said(name: str) -> None:
    row = rb.builtin_template(name)
    assert rb.leader(row) == before.LEADER_BY_TEMPLATE[name], f"{name}: its leader"
    assert before.coder(name) == rb.DEFAULT_CODER, "it is never planned: it records no coder, the developer"
    for kind in (*rb.JOB_KINDS, ""):
        job = {"pipeline_template": name, "job_kind": kind}
        assert job_briefs.what_it_delivers(job) == before.delivers(name, kind), f"{name}/{kind}"
        assert job_briefs.what_it_delivers({**job, "template_delivers": None}) == before.delivers(name, kind), (
            f"{name}/{kind}: a pipeline with no row, read from the database"
        )


def test_a_template_of_the_deployments_own_says_what_no_name_said() -> None:
    assert coding_agent_of([]) == before.coder("mine") == before.coder(None)
    for kind in (*rb.JOB_KINDS, ""):
        job = {"pipeline_template": "mine", "job_kind": kind, "template_delivers": ""}
        assert job_briefs.what_it_delivers(job) == before.delivers("mine", kind), kind


@pytest.mark.parametrize("agent_type", sorted(BUILTIN_SEED))
def test_each_builtin_agent_says_what_its_name_said(agent_type: str) -> None:
    row = rb.builtin_agent(agent_type)
    assert rb.station(row) == before.STATION_OF.get(agent_type, "desk"), f"{agent_type}: its station"
    task = rb.quest(row, templates=set(BUILTIN_PIPELINE_TEMPLATES))
    assert task.offered is (agent_type in before.QUEST_TEMPLATE_BY_AGENT), f"{agent_type}: can the guild send it"
    assert task.template == before.QUEST_TEMPLATE_BY_AGENT.get(agent_type), f"{agent_type}: what its task runs"


def test_the_coding_agents_are_the_ones_both_lists_named() -> None:
    assert frozenset(rb.CODING_AGENT_TYPES) == before.CODING_AGENTS
    assert rb.builtin_agent("my-own-agent") is None and rb.builtin_template("my-own-template") is None
