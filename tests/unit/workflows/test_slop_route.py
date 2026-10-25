"""Slop is not a bug (spec 2026-10-25, slop dungeon): a slop verdict routes the job to the Slopmaster's cleanup."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from embry0.storage.seeds.pipeline_templates_builtin import BUILTIN_PIPELINE_TEMPLATES
from embry0.workflows._validation import validate_graph_definition
from embry0.workflows.issue_to_pr.plan_route import plan_route_node
from embry0.workflows.issue_to_pr.route_plan import (
    AGENT_TYPE_TO_NODE,
    advance,
    build_route_plan,
    coding_agent_of,
    cursor_at,
    next_route,
    step_template_config,
)

SLOP = {"nature": "slop", "slop_score": 72, "kinds": ["symptom_patch"], "evidence": [{"path": "a.py", "why": "x"}]}


class _Templates:
    def __init__(self, *names: str) -> None:
        self._rows = {
            str(i): {
                "id": str(i),
                "name": name,
                "graph_definition": BUILTIN_PIPELINE_TEMPLATES[name]["graph_definition"],
                "default_for_kind": BUILTIN_PIPELINE_TEMPLATES[name].get("default_for_kind"),
                "description": "",
            }
            for i, name in enumerate(names)
        }

    async def list_all(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in r.items() if k != "graph_definition"} for r in self._rows.values()]

    async def get(self, template_id: str) -> dict[str, Any] | None:
        return self._rows.get(template_id)


def _config(repo: Any, jobs_repo: Any = None) -> dict[str, Any]:
    return {"configurable": {"pipeline_templates_repo": repo, "jobs_repo": jobs_repo}}


@pytest.fixture(autouse=True)
def _no_stream_writer():
    with patch("embry0.workflows.issue_to_pr.plan_route.get_stream_writer", return_value=lambda _e: None):
        yield


def test_the_cleanup_template_is_valid_and_its_slopmaster_runs_on_the_developer_node() -> None:
    graph = BUILTIN_PIPELINE_TEMPLATES["slop-cleanup"]["graph_definition"]
    assert validate_graph_definition("slop-cleanup", graph) == []
    plan = build_route_plan(graph)
    assert [s["agent_type"] for s in plan] == ["slopmaster", "reviewer", "qa", "output"]
    assert AGENT_TYPE_TO_NODE["slopmaster"] == "developer"
    assert next_route({"route_plan": plan, "route_cursor": 0}) == "developer"
    assert coding_agent_of(plan) == "slopmaster"
    assert cursor_at({"route_plan": plan}, "developer") == 0, "a QA retry of the coder returns to the Slopmaster"
    assert advance({"route_plan": plan, "route_cursor": 0}) == {"route_cursor": 1}
    assert step_template_config({"route_plan": plan, "route_cursor": 0}, "slopmaster") is None
    assert BUILTIN_PIPELINE_TEMPLATES["slop-cleanup"].get("default_for_kind") is None, "triage's verdict picks it"


@pytest.mark.asyncio
async def test_a_slop_verdict_runs_the_cleanup_and_the_row_keeps_the_verdict() -> None:
    jobs_repo = AsyncMock()
    state = {
        "job_id": "job-1",
        "triage_decision": {"job_kind": "code", "pipeline_template": "quick-fix", "work_nature": SLOP},
    }
    out = await plan_route_node(state, _config(_Templates("full-magnum-opus", "quick-fix", "slop-cleanup"), jobs_repo))
    assert out["route_template_name"] == "slop-cleanup"
    assert out["coding_agent"] == "slopmaster"
    assert out["work_nature"] == SLOP
    kwargs = jobs_repo.update.await_args.kwargs
    assert (
        kwargs["pipeline_template"] == "slop-cleanup" and kwargs["work_nature"] == SLOP and kwargs["job_kind"] == "code"
    )


@pytest.mark.asyncio
async def test_a_watcher_case_judged_slop_may_take_the_cleanup() -> None:
    state = {
        "job_id": "job-1",
        "pin_full_pipeline": True,
        "triage_decision": {"job_kind": "code", "work_nature": SLOP},
    }
    out = await plan_route_node(state, _config(_Templates("full-magnum-opus", "slop-cleanup")))
    assert out["route_template_name"] == "slop-cleanup", "it keeps review and QA, which the pin protects"


@pytest.mark.asyncio
async def test_a_bug_and_a_named_template_keep_their_route() -> None:
    templates = _Templates("full-magnum-opus", "quick-fix", "slop-cleanup")
    bug = {"job_id": "j", "triage_decision": {"job_kind": "code", "work_nature": {"nature": "bug", "slop_score": 5}}}
    out = await plan_route_node(bug, _config(templates))
    assert out["route_template_name"] == "full-magnum-opus" and out["coding_agent"] == "developer"
    named = {
        "job_id": "j",
        "pipeline_template_request": "quick-fix",
        "triage_decision": {"job_kind": "code", "work_nature": SLOP},
    }
    out = await plan_route_node(named, _config(templates))
    assert out["route_template_name"] == "quick-fix", "a template a person named wins"
    assert out["coding_agent"] == "developer" and out["work_nature"] == SLOP


@pytest.mark.asyncio
async def test_slop_without_the_template_falls_to_the_developer_and_a_bad_verdict_is_dropped() -> None:
    out = await plan_route_node(
        {"job_id": "j", "triage_decision": {"job_kind": "code", "work_nature": SLOP}},
        _config(_Templates("full-magnum-opus")),
    )
    assert out["route_template_name"] == "full-magnum-opus" and out["coding_agent"] == "developer"
    out = await plan_route_node(
        {"job_id": "j", "triage_decision": {"job_kind": "code", "work_nature": "slop!"}},
        _config(_Templates("full-magnum-opus", "slop-cleanup")),
    )
    assert out["work_nature"] is None and out["route_template_name"] == "full-magnum-opus"


@pytest.mark.asyncio
async def test_research_is_never_routed_as_slop() -> None:
    out = await plan_route_node(
        {"job_id": "j", "triage_decision": {"job_kind": "research", "work_nature": SLOP}},
        _config(_Templates("research-default", "slop-cleanup")),
    )
    assert out["route_template_name"] == "research-default"
