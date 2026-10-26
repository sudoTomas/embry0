"""On a slop-cleanup route the developer node runs the Slopmaster (spec 2026-10-25, slop dungeon)."""

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from embry0.services.slop import SLOPMASTER_SYSTEM_PROMPT
from embry0.storage.seeds.pipeline_templates_builtin import BUILTIN_PIPELINE_TEMPLATES
from embry0.workflows.issue_to_pr.route_plan import build_route_plan

SLOP = {
    "nature": "slop",
    "slop_score": 72,
    "kinds": ["symptom_patch"],
    "evidence": [{"path": "svc/outbox.py", "why": "three retries"}],
}


def _state(**extra: Any) -> dict[str, Any]:
    return {
        "job_id": "J",
        "issue_id": "iss",
        "repo": "o/r",
        "task": "Clean up the outbox retry wrappers",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "pipeline_config": {"budget_usd": 50.0},
        **extra,
    }


async def _run_node(state: dict[str, Any], jobs_repo: Any = None) -> tuple[Any, dict[str, Any]]:
    from embry0.workflows.issue_to_pr.nodes import developer_node

    seen: dict[str, Any] = {}

    async def _run(*args: Any, **kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"agent_outputs": [{"agent_type": kwargs["agent_type"], "is_error": False, "output": "{}"}]}

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
        patch("embry0.workflows.issue_to_pr.nodes._verify_branch_pushed", new=AsyncMock(return_value=True)),
        patch("embry0.execution.workspace_diff.resolve_base_branch", new=AsyncMock(return_value="main")),
        patch(
            "embry0.execution.workspace_diff.diff_line_counts",
            new=AsyncMock(return_value={"added": 12, "removed": 140}),
        ),
    ):
        command = await developer_node(
            state,
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": object(), "jobs_repo": jobs_repo}},
        )
    return command, seen


@pytest.mark.asyncio
async def test_the_slopmaster_runs_under_its_own_name_with_its_brief_and_its_diff_is_counted() -> None:
    plan = build_route_plan(BUILTIN_PIPELINE_TEMPLATES["slop-cleanup"]["graph_definition"])
    jobs_repo = AsyncMock()
    command, seen = await _run_node(
        _state(coding_agent="slopmaster", work_nature=SLOP, route_plan=plan, route_cursor=0), jobs_repo
    )
    assert seen["agent_type"] == "slopmaster"
    assert seen["agent_definition"]["system_prompt"] == SLOPMASTER_SYSTEM_PROMPT
    assert "## Slop verdict" in seen["prompt"] and "svc/outbox.py: three retries" in seen["prompt"]
    assert command.update["work_nature"]["cleanup"] == {"added": 12, "removed": 140}
    assert jobs_repo.update.await_args.kwargs["work_nature"]["cleanup"] == {"added": 12, "removed": 140}
    assert command.update["route_cursor"] == 1, "the cursor moves past the Slopmaster's step"
    assert command.goto == "review"


@pytest.mark.asyncio
async def test_the_developer_is_unchanged_without_a_coding_agent() -> None:
    command, seen = await _run_node(_state(work_nature=SLOP))
    assert seen["agent_type"] == "developer"
    assert "## Slop verdict" not in seen["prompt"]
    assert "work_nature" not in command.update
