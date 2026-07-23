"""plan_route_node template resolution + finalize_output_node (RAV-601)."""

from unittest.mock import AsyncMock, patch

import pytest

from embry0.storage.seeds.pipeline_templates_builtin import BUILTIN_PIPELINE_TEMPLATES
from embry0.workflows.issue_to_pr.plan_route import (
    TemplateInvalidError,
    finalize_output_node,
    plan_route_node,
)


class _FakeTemplatesRepo:
    def __init__(self, rows):
        self._rows = {r["id"]: r for r in rows}

    async def list_all(self):
        return [{k: v for k, v in r.items() if k != "graph_definition"} for r in self._rows.values()]

    async def get(self, template_id):
        return self._rows.get(template_id)


def _row(rid, name, graph, kind=None):
    return {"id": rid, "name": name, "graph_definition": graph, "default_for_kind": kind, "description": ""}


def _repo():
    return _FakeTemplatesRepo(
        [
            _row("1", "quick-fix", BUILTIN_PIPELINE_TEMPLATES["quick-fix"]["graph_definition"]),
            _row("2", "review-only", BUILTIN_PIPELINE_TEMPLATES["review-only"]["graph_definition"], kind="research"),
            _row("3", "broken", {"nodes": [], "edges": []}),
        ]
    )


def _config(repo=None, jobs_repo=None):
    return {"configurable": {"pipeline_templates_repo": repo, "jobs_repo": jobs_repo}}


def _state(**decision):
    return {"job_id": "job-1", "triage_decision": decision}


@pytest.fixture(autouse=True)
def _no_stream_writer():
    with patch("embry0.workflows.issue_to_pr.plan_route.get_stream_writer", return_value=lambda _e: None):
        yield


@pytest.mark.asyncio
async def test_explicit_template_resolves():
    out = await plan_route_node(_state(pipeline_template="quick-fix", job_kind="code"), _config(_repo()))
    assert out["route_template_name"] == "quick-fix"
    assert [s["agent_type"] for s in out["route_plan"]] == ["developer"]
    assert out["route_cursor"] == 0
    assert out["job_kind"] == "code"


@pytest.mark.asyncio
async def test_kind_default_resolves():
    out = await plan_route_node(_state(job_kind="research"), _config(_repo()))
    assert out["route_template_name"] == "review-only"
    assert [s["agent_type"] for s in out["route_plan"]] == ["reviewer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", ["", "standard", "routine"])
async def test_legacy_template_values_use_kind_default_or_fallback(legacy):
    out = await plan_route_node(_state(pipeline_template=legacy, job_kind="code"), _config(_repo()))
    # kind=code has no default in the fixture → legacy fallback
    assert out["route_template_name"] is None
    assert [s["agent_type"] for s in out["route_plan"]] == ["developer", "reviewer", "qa"]


@pytest.mark.asyncio
async def test_no_repo_falls_back_to_legacy():
    out = await plan_route_node(_state(job_kind="code"), _config(None))
    assert [s["agent_type"] for s in out["route_plan"]] == ["developer", "reviewer", "qa"]


@pytest.mark.asyncio
async def test_unknown_explicit_template_falls_back():
    out = await plan_route_node(_state(pipeline_template="nope", job_kind="code"), _config(_repo()))
    assert out["route_template_name"] is None  # legacy fallback, job proceeds


@pytest.mark.asyncio
async def test_invalid_explicit_template_without_default_raises():
    with pytest.raises(TemplateInvalidError):
        await plan_route_node(_state(pipeline_template="broken", job_kind="code"), _config(_repo()))


@pytest.mark.asyncio
async def test_invalid_explicit_template_with_kind_default_falls_through():
    out = await plan_route_node(_state(pipeline_template="broken", job_kind="research"), _config(_repo()))
    assert out["route_template_name"] == "review-only"


@pytest.mark.asyncio
async def test_job_row_mirrored():
    jobs_repo = AsyncMock()
    await plan_route_node(
        _state(pipeline_template="quick-fix", job_kind="code"),
        _config(_repo(), jobs_repo=jobs_repo),
    )
    kwargs = jobs_repo.update.await_args.kwargs
    assert kwargs["job_kind"] == "code"
    assert kwargs["pipeline_template"] == "quick-fix"


# ---- finalize_output -------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_output_picks_last_non_error_output():
    with patch("embry0.workflows.issue_to_pr.plan_route.get_stream_writer", return_value=lambda _e: None):
        out = await finalize_output_node(
            {
                "agent_outputs": [
                    {"agent_type": "developer", "is_error": False, "output": "first"},
                    {"agent_type": "review", "is_error": True, "output": "boom"},
                    {"agent_type": "review", "is_error": False, "output": "  the answer  "},
                ]
            },
            {"configurable": {}},
        )
    assert out["result_summary"] == "the answer"
    assert out["current_stage"] == "output_finalized"


@pytest.mark.asyncio
async def test_finalize_output_truncates():
    with patch("embry0.workflows.issue_to_pr.plan_route.get_stream_writer", return_value=lambda _e: None):
        out = await finalize_output_node(
            {"agent_outputs": [{"is_error": False, "output": "x" * 50_000}]},
            {"configurable": {}},
        )
    assert len(out["result_summary"]) == 10_000
