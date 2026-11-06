"""A code job judged slop is routed as its repository's ``slop_policy`` says (#169).

plan_route reads the policy the job read when it started (``job_settings``):
whether it routes, from which score, to which template, and which kinds a
verdict may name. A job that read none routes as the code did before the
section: every slop verdict to ``slop-cleanup``. A route template that is
gone or cannot take a code job is logged and the job takes its kind's
default route; a slop route never fails a job.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
import structlog

from embry0.services.job_settings import STATE_KEY
from embry0.settings.sections import CATALOG
from embry0.storage.seeds.pipeline_templates_builtin import BUILTIN_PIPELINE_TEMPLATES
from embry0.workflows.issue_to_pr.plan_route import TemplateInvalidError, plan_route_node

SLOP = {"nature": "slop", "slop_score": 72, "kinds": ["symptom_patch"], "evidence": []}


class _Templates:
    """Template rows as the repository answers them: the built-ins named, and any of the operator's."""

    def __init__(self, *names: str, **own: dict[str, Any]) -> None:
        rows = [{"name": name, **BUILTIN_PIPELINE_TEMPLATES[name]} for name in names]
        rows += [{"name": name, **row} for name, row in own.items()]
        self._rows = {str(i): {"id": str(i), **row} for i, row in enumerate(rows)}

    async def list_all(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in r.items() if k != "graph_definition"} for r in self._rows.values()]

    async def get(self, template_id: str) -> dict[str, Any] | None:
        return self._rows.get(template_id)


ALL = ("full-magnum-opus", "quick-fix", "slop-cleanup", "research-default")


def _state(policy: dict[str, Any] | None = None, *, verdict: Any = SLOP, **extra: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "job_id": "job-1",
        "repo": "acme/widgets",
        "triage_decision": {"job_kind": "code", "work_nature": verdict, **extra.pop("decision", {})},
        **extra,
    }
    if policy is not None:
        section = CATALOG.get("slop_policy")
        if section is None:
            pytest.fail("the settings document has no slop_policy section: a job judged slop is routed by code")
        state[STATE_KEY] = {"slop_policy": section.validate(policy).model_dump(mode="json")}
    return state


async def _route(state: dict[str, Any], templates: Any) -> dict[str, Any]:
    with patch("embry0.workflows.issue_to_pr.plan_route.get_stream_writer", return_value=lambda _e: None):
        return await plan_route_node(state, {"configurable": {"pipeline_templates_repo": templates}})


@pytest.mark.asyncio
async def test_a_job_that_read_no_policy_routes_as_before() -> None:
    """Passes before #169 by design: it pins what the code did."""
    out = await _route(_state(), _Templates(*ALL))
    assert (out["route_template_name"], out["coding_agent"]) == ("slop-cleanup", "slopmaster")


@pytest.mark.asyncio
async def test_the_policy_s_defaults_route_as_the_code_did() -> None:
    out = await _route(_state({}), _Templates(*ALL))
    assert (out["route_template_name"], out["coding_agent"]) == ("slop-cleanup", "slopmaster")


@pytest.mark.asyncio
async def test_with_the_policy_off_a_job_judged_slop_takes_its_normal_route() -> None:
    off = {"enabled": False}
    out = await _route(_state(off), _Templates(*ALL))
    assert (out["route_template_name"], out["coding_agent"]) == ("full-magnum-opus", "developer"), (
        "slop_policy.enabled: false, so a job judged slop runs its kind's default"
    )
    assert out["work_nature"] == SLOP, "the verdict is still recorded"
    out = await _route(_state(off, decision={"pipeline_template": "quick-fix"}), _Templates(*ALL))
    assert out["route_template_name"] == "quick-fix", "and triage's own pick stands"


@pytest.mark.asyncio
async def test_a_verdict_below_the_threshold_keeps_its_route() -> None:
    out = await _route(_state({"min_score_to_route": 80}), _Templates(*ALL))
    assert out["route_template_name"] == "full-magnum-opus", "72 is below 80"
    out = await _route(_state({"min_score_to_route": 72}), _Templates(*ALL))
    assert out["route_template_name"] == "slop-cleanup", "72 reaches 72"


@pytest.mark.asyncio
async def test_a_job_judged_slop_runs_the_template_its_policy_names() -> None:
    tidy = {**BUILTIN_PIPELINE_TEMPLATES["slop-cleanup"], "description": "the operator's own cleanup"}
    out = await _route(_state({"route_template": "tidy-up"}), _Templates(*ALL, **{"tidy-up": tidy}))
    assert (out["route_template_name"], out["coding_agent"]) == ("tidy-up", "slopmaster")
    out = await _route(
        _state({"route_template": "tidy-up"}, pipeline_template_request="quick-fix"),
        _Templates(*ALL, **{"tidy-up": tidy}),
    )
    assert out["route_template_name"] == "quick-fix", "a template a person named still wins"


@pytest.mark.asyncio
async def test_a_route_template_that_is_gone_or_unfit_falls_to_the_kind_default() -> None:
    with structlog.testing.capture_logs() as logs:
        gone = await _route(_state({"route_template": "tidy-up"}), _Templates(*ALL))
    assert gone["route_template_name"] == "full-magnum-opus"
    assert any(e["event"] == "plan_route_template_not_found" and e.get("slop_route") for e in logs), logs

    with structlog.testing.capture_logs() as logs:
        unfit = await _route(_state({"route_template": "research-default"}), _Templates(*ALL))
    assert unfit["route_template_name"] == "full-magnum-opus", "a research template never runs a code job"
    assert unfit["job_kind"] == "code"
    assert any(e["event"] == "plan_route_slop_template_unfit" for e in logs), logs


@pytest.mark.asyncio
async def test_a_broken_slop_route_never_fails_the_job() -> None:
    """With no kind default, a person's invalid template fails the job; the policy's falls to the legacy plan."""
    graph = BUILTIN_PIPELINE_TEMPLATES["slop-cleanup"]["graph_definition"]
    nodes = [*graph["nodes"], {"node_id": "stray", "agent_type": "no-such-agent", "position": {"x": 0, "y": 0}}]
    broken = {**BUILTIN_PIPELINE_TEMPLATES["slop-cleanup"], "graph_definition": {**graph, "nodes": nodes}}
    with structlog.testing.capture_logs() as logs:
        try:
            out = await _route(_state(), _Templates(**{"slop-cleanup": broken}))
        except TemplateInvalidError:
            pytest.fail("an invalid slop route with no code default failed the job")
    assert any(e["event"] == "plan_route_template_invalid" for e in logs), "the template is invalid"
    assert out["route_template_name"] is None and out["coding_agent"] == "developer", "the legacy plan runs"


@pytest.mark.asyncio
async def test_a_verdict_names_only_the_kinds_its_policy_lists() -> None:
    verdict = {**SLOP, "kinds": ["naming", "symptom_patch", "dead-code"]}
    out = await _route(_state({"kinds": ["naming", "dead_code"]}, verdict=verdict), _Templates(*ALL))
    assert out["work_nature"]["kinds"] == ["naming", "dead_code"], "an organization's own kind is kept"
    out = await _route(_state({}, verdict=verdict), _Templates(*ALL))
    assert out["work_nature"]["kinds"] == ["symptom_patch", "dead_code"], "the default kinds, as before"


@pytest.mark.asyncio
async def test_a_job_pinned_to_the_full_pipeline_keeps_review_and_qa_when_judged_slop() -> None:
    """#306: a job working a watcher-origin case runs review and QA, whatever route a slop policy names."""
    pinned = await _route(_state({"route_template": "quick-fix"}, pin_full_pipeline=True), _Templates(*ALL))
    steps = [step["agent_type"] for step in pinned["route_plan"]]
    assert "qa" in steps and "reviewer" in steps, (
        f"a job pinned to the full pipeline judged slop ran {pinned['route_template_name']}: {steps}"
    )
    assert pinned["route_template_name"] == "full-magnum-opus", "its kind's default route, which has both"
    cleanup = await _route(_state({}, pin_full_pipeline=True), _Templates(*ALL))
    assert cleanup["route_template_name"] == "slop-cleanup", "the cleanup has review and QA, so it is taken"
    unpinned = await _route(_state({"route_template": "quick-fix"}), _Templates(*ALL))
    assert unpinned["route_template_name"] == "quick-fix", "a job not pinned may take a lighter route"

    # A route with QA and no review step: the pinned job keeps both, so it takes its kind's default.
    chain = ["triage", "developer", "qa", "output"]
    checked = {
        "description": "developer, then QA",
        "graph_definition": {
            "name": "checked-fix",
            "nodes": [
                {"node_id": t, "agent_type": t, "label": t, "position": {"x": 240 * i, "y": 0}}
                for i, t in enumerate(chain)
            ],
            "edges": [
                {"edge_id": f"e{i}", "source": a, "target": b, "edge_type": "flow"}
                for i, (a, b) in enumerate(zip(chain, chain[1:], strict=False), start=1)
            ],
            "metadata": {},
        },
    }
    with structlog.testing.capture_logs() as logs:
        no_review = await _route(
            _state({"route_template": "checked-fix"}, pin_full_pipeline=True),
            _Templates(*ALL, **{"checked-fix": checked}),
        )
    assert no_review["route_template_name"] == "full-magnum-opus", (
        f"a job pinned to the full pipeline judged slop ran {no_review['route_template_name']}, which has no review step"
    )
    assert any(e["event"] == "plan_route_slop_template_unfit" and "review" in e["problem"] for e in logs), logs
