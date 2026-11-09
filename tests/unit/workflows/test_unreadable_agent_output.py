"""An agent's final output holding JSON Python cannot decode ends no node.

The developer and review nodes read their agent's output as JSON first. A
line nested past the decoder's recursion limit, or holding an integer past
the digit limit, raises ``RecursionError`` or a plain ``ValueError``, not the
``JSONDecodeError`` each node handled; each node now takes its own fallback
for it, as for any output that is not JSON.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from tests.fixtures.unreadable_json import KINDS, handled, unreadable_object

PR_URL = "https://github.com/acme/widgets/pull/7"


def _result(agent_type: str, output: str) -> dict[str, Any]:
    return {
        "agent_outputs": [{"agent_type": agent_type, "is_error": False, "output": output, "mode": "anthropic_api"}],
        "events": [],
        "total_cost_usd": 0.01,
    }


def _state(**extra: Any) -> dict[str, Any]:
    return {
        "job_id": "J",
        "issue_id": "iss",
        "repo": "acme/widgets",
        "task": "x",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        **extra,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_the_developer_reads_its_pr_link_past_an_unreadable_json_line(kind: str) -> None:
    from embry0.workflows.issue_to_pr.nodes import developer_node

    output = "Opened the pull request.\n" + unreadable_object(kind, {"pr_url": PR_URL, "branch": "embry0/x"})

    with (
        patch(
            "embry0.workflows.issue_to_pr.nodes.run_agent_node",
            new=AsyncMock(return_value=_result("developer", output)),
        ),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        with handled("developer_node"):
            command = await developer_node(
                _state(pipeline_config={"agent_skills": {"developer": []}, "budget_usd": 50.0}),
                {"configurable": {"agent_runner": object(), "credentials": {}}},
            )

    assert command.update["pr_url"] == PR_URL, "the link is read from the text when the JSON line cannot be"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS)
async def test_an_unreadable_review_reports_no_readable_verdict(kind: str) -> None:
    from embry0.workflows.issue_to_pr.nodes import review_node

    written: list[dict[str, Any]] = []
    output = unreadable_object(kind, {"decision": "approved", "summary": "lgtm"})

    with (
        patch(
            "embry0.workflows.issue_to_pr.nodes.run_agent_node",
            new=AsyncMock(return_value=_result("review", output)),
        ),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=written.append),
    ):
        with handled("review_node"):
            await review_node(
                _state(
                    branch_name="embry0/test",
                    pr_url=PR_URL,
                    pipeline_config={"agent_models": {"review": "claude-sonnet-4-6"}, "budget_usd": 50.0},
                ),
                {"configurable": {"agent_runner": object(), "credentials": {}}},
            )

    decisions = [e for e in written if e.get("type") == "review_decision"]
    assert [(d["decision"], d["summary"]) for d in decisions] == [(None, "Review emitted no readable verdict")]
