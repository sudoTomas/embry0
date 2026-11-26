"""An agent's roles, on its row (#395): ``coder`` runs it on the developer node, ``docs`` lets another step put files
on the job's branch.

The developer node ran the agents a list of two names named, so an operator's own coding agent ran as a single
generic step. The roles are a column of the row now: plan_route records each planned step's roles, the dispatcher
sends the coder's step to the developer node, the job's coder is that step's agent, and a route has at most one coder
and any number of docs steps.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from embry0.workflows import row_behaviour as rb
from embry0.workflows.issue_to_pr import plan_route
from embry0.workflows.issue_to_pr.route_plan import (
    build_route_plan,
    coding_agent_of,
    cursor_at,
    next_route,
    with_roles,
)

#: An agent of the operator's own, with the coder role.
SCRIBE = {"type": "scribe", "roles": ["coder"], "runs_in_jobs": True}
#: The roles a plan of these tests reads: the operator's coder and the built-ins as seeded.
HELD = {"scribe": frozenset({"coder"}), "developer": frozenset({"coder"}), "review": frozenset({"docs"})}


def _graph(*agents: str) -> dict[str, Any]:
    nodes = [{"node_id": f"n{i}", "agent_type": a} for i, a in enumerate(("triage", *agents))]
    edges = [
        {"edge_id": f"e{i}", "source": f"n{i}", "target": f"n{i + 1}", "edge_type": "flow"} for i in range(len(agents))
    ]
    return {"nodes": nodes, "edges": edges}


# --- the row ---------------------------------------------------------------------------------------------------


def test_a_row_holds_the_roles_it_names_of_the_known_ones() -> None:
    assert rb.roles(SCRIBE) == {"coder"} and rb.is_coder(SCRIBE)
    assert rb.roles({"type": "scout"}) == frozenset(), "a row read without the column holds none"
    assert rb.roles({"type": "scout", "roles": ["coder", "wizard"]}) == {"coder"}, "an unknown role is no role"
    assert rb.roles({"type": "scout", "roles": "coder"}) == frozenset(), "text is not a list of roles"
    assert not rb.is_coder({"type": "scout", "roles": []})


def test_a_stored_roles_value_is_a_list_of_the_known_roles() -> None:
    assert rb.checked_roles([]) == [] and rb.checked_roles(("coder", "coder")) == ["coder"]
    assert rb.checked_roles(["docs", "coder"]) == ["coder", "docs"], "each once, in the list's order"
    for bad in ("coder", ["reviewer"], [""], None, {"coder": True}):
        with pytest.raises(ValueError, match="roles must be a list of coder, docs"):
            rb.checked_roles(bad)


def test_the_coders_are_the_rows_that_hold_the_role_and_run_in_jobs() -> None:
    rows = [
        SCRIBE,
        {"type": "developer", "roles": ["coder"]},
        {"type": "slopmaster", "roles": []},  # an operator took the role from it
        {"type": "voice", "roles": ["coder"], "runs_in_jobs": False},
        {"type": "review", "roles": ["coder"]},  # the graph's own review step never codes
    ]
    assert rb.coders_of(rows) == {"scribe", "developer"}
    assert rb.coders_of([]) == rb.builtin_coders() == {"developer", "slopmaster"}, (
        "a built-in coder with no row yet is as its seed says"
    )
    assert rb.builtin_roles() == {
        "developer": {"coder"},
        "slopmaster": {"coder"},
        "triage": {"docs"},
        "review": {"docs"},
        "context-auditor": {"docs"},
    }, "the seed's roles: two coders, and the three built-ins that put files on the branch"


def test_the_roles_by_agent_keep_docs_and_drop_coder_on_the_graphs_own_steps() -> None:
    rows = [
        {"type": "review", "roles": ["coder", "docs"]},  # the graph's own review step may write docs, never code
        {"type": "triage", "roles": []},  # an operator took docs from triage
        {"type": "scout", "roles": ["docs"]},
        {"type": "voice", "roles": ["docs"], "runs_in_jobs": False},
    ]
    held = rb.roles_by_type(rows)
    assert held["review"] == {"docs"} and held["scout"] == {"docs"}
    assert "triage" not in held and "voice" not in held, held
    assert held["developer"] == {"coder"} and held["context-auditor"] == {"docs"}, "built-ins with no row: their seed"
    assert rb.coder_types([{"type": "qa", "roles": ["coder"]}, {"type": "triage", "roles": ["coder"]}]) == frozenset()


def test_a_planned_step_runs_with_the_roles_recorded_on_it() -> None:
    assert rb.step_roles({"agent_type": "scribe", "roles": ["coder"]}) == {"coder"}
    assert rb.step_roles({"agent_type": "slopmaster", "roles": []}) == frozenset(), "recorded none wins over the seed"
    assert rb.step_roles({"agent_type": "slopmaster"}) == {"coder"}, "a step planned before roles reads its seed"
    assert rb.step_roles({"agent_type": "reviewer"}) == {"docs"}, "the reviewer step reads the review agent's seed"
    assert rb.step_roles({"agent_type": "scribe"}) == frozenset(), "an agent of the deployment's own had none then"
    assert rb.is_coder_step({"agent_type": "developer"}) and not rb.is_coder_step({"agent_type": "reviewer"})


def test_a_route_has_at_most_one_coder_step() -> None:
    coders = frozenset({"developer", "scribe"})
    assert rb.coder_steps_problem("one", _graph("scribe", "reviewer"), coders) is None
    assert rb.coder_steps_problem("none", _graph("why"), coders) is None
    problem = rb.coder_steps_problem("two", _graph("developer", "reviewer", "scribe"), coders)
    assert problem is not None and "'two' has 2 steps" in problem and "n1, n3" in problem, problem


def test_a_code_job_needs_a_coder_step_read_from_the_rows() -> None:
    template = {"name": "my-fix", "job_kind": "code", "graph_definition": _graph("scribe", "reviewer")}
    assert rb.code_job_problem(template, frozenset({"scribe"})) is None
    problem = rb.code_job_problem(template, rb.builtin_coders())
    assert problem is not None and "no coding step" in problem, problem


# --- the route --------------------------------------------------------------------------------------------------


def test_the_coders_step_runs_on_the_developer_node_whatever_its_name() -> None:
    plan = with_roles(build_route_plan(_graph("scribe", "reviewer", "qa", "output")), HELD)
    assert [s["roles"] for s in plan] == [["coder"], ["docs"], [], []]
    assert next_route({"route_plan": plan, "route_cursor": 0}) == "developer"
    assert coding_agent_of(plan) == "scribe"
    assert cursor_at({"route_plan": plan, "route_cursor": 3}, "developer") == 0, "QA's retry returns to the coder"

    without = with_roles(build_route_plan(_graph("scribe", "reviewer")), {})
    assert next_route({"route_plan": without, "route_cursor": 0}) == "agent", "without the role it is a generic step"
    assert coding_agent_of(without) == rb.DEFAULT_CODER


def test_a_built_in_whose_role_was_taken_away_runs_as_a_generic_step() -> None:
    plan = with_roles(build_route_plan(_graph("slopmaster", "reviewer")), {"developer": frozenset({"coder"})})
    assert next_route({"route_plan": plan, "route_cursor": 0}) == "agent"


class _Templates:
    def __init__(self, *rows: dict[str, Any]) -> None:
        self._rows = {row["id"]: row for row in rows}

    async def list_all(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in row.items() if k != "graph_definition"} for row in self._rows.values()]

    async def get(self, template_id: str) -> dict[str, Any] | None:
        return self._rows.get(template_id)


def _row(name: str, graph: dict[str, Any], **behaviour: Any) -> dict[str, Any]:
    return {"id": name, "name": name, "description": name, "graph_definition": graph, **behaviour}


async def _plan(templates: _Templates, request: str, held: dict[str, frozenset[str]]) -> dict[str, Any]:
    config = {"configurable": {"pipeline_templates_repo": templates, "jobs_repo": None, "db": None}}
    state = {
        "job_id": "job-1",
        "triage_decision": {"job_kind": "code", "pipeline_template": request},
        "pipeline_template_request": request,
    }
    with (
        patch("embry0.workflows.issue_to_pr.plan_route.get_stream_writer", return_value=lambda _e: None),
        patch.object(plan_route, "_known_agent_types", AsyncMock(return_value={"scribe", "developer", "reviewer"})),
        patch.object(plan_route, "_known_roles", AsyncMock(return_value=held)),
    ):
        return await plan_route.plan_route_node(state, config)


@pytest.mark.asyncio
async def test_planning_records_each_steps_role_and_the_coder() -> None:
    templates = _Templates(
        _row("scribe-fix", _graph("scribe", "reviewer"), fixed_route=True),
        _row("quick-fix", _graph("developer"), default_for_kind="code"),
    )
    out = await _plan(templates, "scribe-fix", HELD)
    assert out["route_template_name"] == "scribe-fix"
    assert [(s["agent_type"], s["roles"]) for s in out["route_plan"]] == [("scribe", ["coder"]), ("reviewer", ["docs"])]
    assert out["coding_agent"] == "scribe", "the job's coder, credited with its pull request, is the operator's agent"


@pytest.mark.asyncio
async def test_a_route_with_two_coder_steps_is_not_planned() -> None:
    """A role given after the template was saved can give it a second coder: the job takes its kind's default."""
    templates = _Templates(
        _row("two-coders", _graph("developer", "scribe"), fixed_route=True),
        _row("quick-fix", _graph("developer"), default_for_kind="code"),
    )
    out = await _plan(templates, "two-coders", HELD)
    assert out["route_template_name"] == "quick-fix", out["route_template_name"]
    assert out["coding_agent"] == "developer"


@pytest.mark.asyncio
async def test_without_rows_the_built_in_roles_are_known() -> None:
    assert await plan_route._known_roles({"configurable": {"db": None}}) == rb.builtin_roles()


# --- the developer node -----------------------------------------------------------------------------------------


async def _run_developer(state: dict[str, Any]) -> dict[str, Any]:
    from embry0.workflows.issue_to_pr.nodes import developer_node

    seen: dict[str, Any] = {}

    async def _run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"agent_outputs": [{"agent_type": kwargs["agent_type"], "is_error": False, "output": "{}"}]}

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
        patch("embry0.workflows.issue_to_pr.nodes._verify_branch_pushed", new=AsyncMock(return_value=True)),
    ):
        await developer_node(
            {
                "job_id": "J",
                "issue_id": "iss",
                "repo": "o/r",
                "task": "Fix the widget",
                "sandbox_container_id": "C",
                "agent_outputs": [],
                "errors": [],
                "pipeline_config": {"budget_usd": 50.0},
                **state,
            },
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": object()}},
        )
    return seen


@pytest.mark.asyncio
async def test_the_developer_node_runs_the_routes_coder() -> None:
    plan = with_roles(build_route_plan(_graph("scribe", "reviewer")), HELD)
    seen = await _run_developer({"route_plan": plan, "route_cursor": 0, "coding_agent": "scribe"})
    assert seen["agent_type"] == "scribe"


@pytest.mark.asyncio
async def test_a_coding_agent_the_route_does_not_run_as_its_coder_is_not_run() -> None:
    plan = with_roles(build_route_plan(_graph("scribe", "reviewer")), HELD)
    seen = await _run_developer({"route_plan": plan, "route_cursor": 0, "coding_agent": "reviewer"})
    assert seen["agent_type"] == "scribe", "the node runs the step the route recorded as its coder"


def test_salvage_reads_the_coders_own_run() -> None:
    from embry0.workflows.issue_to_pr.nodes import _salvage

    result = {
        "agent_outputs": [
            {"agent_type": "scribe", "is_error": True, "error_code": "ERR_AGENT_MAX_TURNS"},
            {"agent_type": "reviewer", "is_error": False},
        ]
    }
    assert _salvage.stop_reason(result, "scribe") == "turn cap"
    assert _salvage.stop_reason(result, "developer") is None, "another agent's output is not the coder's run"


def test_a_slop_policy_may_route_to_a_template_whose_coder_is_the_operators() -> None:
    from embry0.settings.write_context import route_template_checker

    rows = [{"name": "scribe-fix", "job_kind": "code", "graph_definition": _graph("scribe", "reviewer")}]
    assert route_template_checker(rows, frozenset({"scribe"}))("scribe-fix") is None
    problem = route_template_checker(rows, rb.builtin_coders())("scribe-fix")
    assert problem is not None and "no coding step" in problem, problem


class _Db:
    """A database whose agent_definitions rows are ``rows`` (by type)."""

    def __init__(self, rows: dict[str, dict[str, Any]]) -> None:
        self.rows = rows

    async def fetchrow(self, query: str, agent_type: str) -> dict[str, Any] | None:
        assert "FROM agent_definitions WHERE type" in query, query
        return self.rows.get(agent_type)


async def _run_with_rows(rows: dict[str, dict[str, Any]], coder: str) -> tuple[Any, dict[str, Any]]:
    from embry0.workflows.issue_to_pr.nodes import developer_node

    seen: dict[str, Any] = {}

    async def _run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"agent_outputs": [{"agent_type": kwargs["agent_type"], "is_error": False, "output": "{}"}]}

    plan = with_roles(build_route_plan(_graph(coder, "reviewer")), {coder: frozenset({"coder"})})
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
        patch("embry0.workflows.issue_to_pr.nodes._verify_branch_pushed", new=AsyncMock(return_value=True)),
    ):
        command = await developer_node(
            {
                "job_id": "J",
                "issue_id": "iss",
                "repo": "o/r",
                "task": "Fix the widget",
                "sandbox_container_id": "C",
                "agent_outputs": [],
                "errors": [],
                "pipeline_config": {"budget_usd": 50.0},
                "route_plan": plan,
                "route_cursor": 0,
                "coding_agent": coder,
            },
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": object(), "db": _Db(rows)}},
        )
    return command, seen


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rows", "says"),
    [
        ({}, "'scribe' this step was planned with no longer exists"),
        ({"scribe": {**SCRIBE, "roles": []}}, "'scribe' no longer holds the coder role"),
        ({"scribe": {**SCRIBE, "roles": ["docs"]}}, "'scribe' no longer holds the coder role"),
    ],
    ids=["deleted", "role-taken", "docs-only"],
)
async def test_a_coder_deleted_or_unroled_since_planning_does_not_run(rows: dict[str, Any], says: str) -> None:
    """Routing is what the rows said at planning (D5); the coder's own step asks its row again, so an agent deleted
    since never runs with the developer's default definition, and one the role was taken from never codes."""
    from embry0.safety.error_codes import ErrorCode

    command, seen = await _run_with_rows(rows, "scribe")
    assert seen == {}, "the agent ran"
    assert command.goto == "__end__" and command.update["error_code"] == ErrorCode.AGENT_ROLE_REVOKED.value
    assert says in command.update["errors"][0], command.update["errors"]


@pytest.mark.asyncio
async def test_a_coder_that_still_holds_the_role_runs_and_a_built_in_without_a_row_runs_as_seeded() -> None:
    _command, seen = await _run_with_rows({"scribe": SCRIBE}, "scribe")
    assert seen["agent_type"] == "scribe"
    _command, seen = await _run_with_rows({}, "developer")
    assert seen["agent_type"] == "developer", "a built-in with no row yet runs as its seed says"
    _command, seen = await _run_with_rows({"developer": {"type": "developer", "tools": ["Read"]}}, "developer")
    assert seen["agent_type"] == "developer", "a built-in's row read without the column holds what its seed says"
    command, seen = await _run_with_rows({"scribe": {"type": "scribe", "tools": ["Read"]}}, "scribe")
    assert seen == {} and command.update["error_code"] == "ERR_AGENT_ROLE_REVOKED", (
        "an agent of the operator's own does not"
    )


@pytest.mark.asyncio
async def test_a_row_that_cannot_be_read_stops_the_step() -> None:
    from embry0.workflows.issue_to_pr.role_check import role_revoked

    class _Down:
        async def fetchrow(self, *_a: Any) -> None:
            raise ConnectionError("database away")

    with pytest.raises(ConnectionError):
        await role_revoked({"configurable": {"db": _Down()}}, "scribe", "coder")
    assert await role_revoked({"configurable": {"db": None}}, "scribe", "coder") is None, "no database: code defaults"


@pytest.mark.asyncio
async def test_a_coder_whose_row_goes_after_its_check_never_runs_with_the_developers_definition() -> None:
    """The row read when the step starts, then gone when its definition is loaded: the code's developer definition
    stands in only for a built-in."""
    from embry0.safety.error_codes import ErrorCode

    reads: list[str] = []

    class _Vanishing:
        async def fetchrow(self, query: str, agent_type: str) -> dict[str, Any] | None:
            reads.append(agent_type)
            return SCRIBE if len(reads) == 1 else None

    from embry0.workflows.issue_to_pr.nodes import developer_node

    ran = AsyncMock()
    plan = with_roles(build_route_plan(_graph("scribe")), {"scribe": frozenset({"coder"})})
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=ran),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        command = await developer_node(
            {"job_id": "J", "task": "t", "sandbox_container_id": "C", "route_plan": plan, "coding_agent": "scribe"},
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": object(), "db": _Vanishing()}},
        )
    ran.assert_not_awaited()
    assert command.update["error_code"] == ErrorCode.AGENT_ROLE_REVOKED.value and reads == ["scribe", "scribe"]
