"""With nothing written, a coding job behaves byte for byte as it did before #182.

The done_when and salvage sections took over three values fixed in the code:
one retry, the ticket heading "Acceptance criteria" and the salvage commit's
subject, ``wip: unfinished — <agent> stopped (<reason>)``. Every text below is
the one the code wrote before the sections, but for the one ``UNFINISHED``
says, and every node is driven through its state: once as a job that read no
settings (one started before the release) and once as a job that read the
defaults from an empty settings document. Both must give exactly that text.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import embry0.workflows.issue_to_pr.nodes as nodes_module
from embry0.services.done_when import parse_done_when
from tests.unit.workflows.test_developer_salvage import _BRANCH, _Sandbox, _stopped
from tests.unit.workflows.test_developer_salvage import _run as _run_developer

#: The triage prompt's "Done when" section as the code wrote it.
OLD_GUIDE = """\
## Done when

For every code job, set `done_when`: the finish line the developer works to,
and the list review and QA judge the change against. Without it a developer
does not know when to stop.
  "done_when": {
    "items": [{"done": "<a condition>", "shown_by": "<how it is shown>"}],
    "out_of_scope": ["<what this job does not take on>"]
  }
Rules:
- 2 to 5 items. Each is a condition a reviewer can check from the branch or
  the pull request, with how it is shown: a named test passes, a command
  prints something, a log line appears, a document says something, a written
  diagnosis names the cause with its evidence.
- When the task has an "Acceptance criteria" section, those are your items:
  keep their meaning, add `shown_by`, add none of your own beyond what the
  task asks.
- A diagnose or investigate task ends in a deliverable that can be reached,
  e.g. {"done": "the cause is written down with the log and code evidence
  that shows it, or 'cannot tell' with what is missing", "shown_by": "a
  section in the pull request description"}, plus the fix when the task asks
  for one. Never an open-ended "investigate X".
- out_of_scope names the nearby work this job must not take on (other
  services, refactors, "while I'm here" changes), 0 to 4 lines.
- Plain words, no ids. Omit `done_when` for jobs that are not code."""

OLD_CONTRACT_END = (
    "Say met: false, with what is missing, for any item you did not reach. embry0 checks this report: "
    "an unmet or unreported item sends the work back once, then pauses the job."
)

OLD_COMMIT = (
    "wip: unfinished — developer stopped (turn cap)\n\n"
    "Committed by embry0, not by the agent. The developer run stopped (turn cap)\n"
    "before it committed, so this change is incomplete and was never\n"
    "self-reviewed. It is kept here so the work is not lost: the next run\n"
    "continues from this commit. Do not merge it as it is."
)

#: The next run's note on the salvage commit. The one text here that is not the old one: it named the commit
#: by its first words, `wip: unfinished …`, and now quotes its whole subject, which the job's
#: salvage.commit_subject template may shape any way (#182 review).
UNFINISHED = (
    "\nUnfinished work from your earlier run:\nThat run stopped (turn cap) "
    "before it committed. embry0 committed what it left in the working tree as "
    "`wip: unfinished — developer stopped (turn cap)` "
    f"on {_BRANCH} and pushed it, so it is checked out here. Do not start over: review that commit "
    "against the task, finish what is missing, run the checks, then commit and push."
)

LIST = parse_done_when({"items": [{"done": "The cause is written down", "shown_by": "a PR section"}]})
TICKET = "Fix the export\n\n## Acceptance Criteria\n- [ ] CSV has a header row\n\n## Definition of done\n- not this"


def _defaults_read_at_start() -> dict[str, Any]:
    try:
        from embry0.services.job_settings import read_for_job
    except ImportError:
        pytest.fail("embry0.services.job_settings is missing: a job cannot read the done_when and salvage defaults")
    return {"job_settings": asyncio.run(read_for_job(None, "acme/widgets"))}


@pytest.fixture(params=["read nothing", "read the defaults"])
def settings(request: pytest.FixtureRequest) -> dict[str, Any]:
    """What a job keeps on its state: nothing (it started before #182), or the defaults it read."""
    return {} if request.param == "read nothing" else _defaults_read_at_start()


def _unreported() -> dict[str, Any]:
    final = {"pr_url": None, "summary": "s"}
    return {"agent_outputs": [{"agent_type": "developer", "is_error": False, "output": json.dumps(final)}]}


def test_triage_is_told_the_old_done_when_section(settings: dict[str, Any]) -> None:
    from embry0.workflows.issue_to_pr.nodes import triage_node

    captured: dict[str, Any] = {}

    async def _run(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        captured["definition"] = kwargs.get("agent_definition")
        output = '{"action": "proceed", "reasoning": "ok", "confidence": 0.9}'
        return {"agent_outputs": [{"agent_type": "triage", "is_error": False, "output": output}], "events": []}

    state = {"job_id": "J", "repo": "o/r", "task": "x", "sandbox_container_id": "C", "agent_outputs": []}
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        asyncio.run(triage_node({**state, **settings}, {"configurable": {"agent_runner": object(), "credentials": {}}}))
    prompt = captured["definition"]["system_prompt"]
    assert f"\n\n{OLD_GUIDE}\n\n" in prompt, "the triage prompt's Done when section changed with nothing written"


def test_the_developer_s_contract_ends_as_it_did(settings: dict[str, Any]) -> None:
    from embry0.workflows.issue_to_pr.nodes import developer_node

    docker = MagicMock()
    docker.run_cmd = AsyncMock(side_effect=["", f"abc\trefs/heads/{_BRANCH}"])
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])
    run = AsyncMock(return_value={**_unreported(), "events": [], "total_cost_usd": 0.05})
    state = {
        "job_id": "J",
        "issue_id": "iss-abc12345",
        "repo": "o/r",
        "task": "test task",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "pipeline_config": {"agent_skills": {"developer": []}, "budget_usd": 50.0},
        "done_when": LIST,
    }
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=run),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = asyncio.run(
            developer_node(
                {**state, **settings}, {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}}
            )
        )
    assert run.await_args.kwargs["prompt"].endswith(OLD_CONTRACT_END), "the contract's last sentence changed"
    assert cmd.goto == "developer", "an unreported first run goes back once, as before"


def test_one_retry_then_the_old_pause(settings: dict[str, Any]) -> None:
    from embry0.workflows.issue_to_pr.nodes._done_when import check_done_when

    gotos, errors = [], []
    for done in (0, 1):
        updates, goto = asyncio.run(
            check_done_when(
                {"job_id": "J", "errors": [], "done_when_retries": done, **settings},
                {},
                _unreported(),
                LIST,
                writer=lambda _e: None,
            )
        )
        gotos.append(goto)
        errors = updates.get("errors") or errors
    assert gotos == ["developer", "max_retries"], f"one retry, then the pause: {gotos}"
    assert errors == ["Done-when items still not met after a retry: The cause is written down"]

    seen: dict[str, Any] = {}

    def _interrupt(summary: dict[str, Any]) -> Any:
        seen.update(summary)
        return {"choice": "abandon"}

    listed = {**LIST, "report": {"items": [], "unmet": ["d1"], "reported": True}}
    paused = {"job_id": "J", "current_stage": "done_when_unmet", "done_when": listed, "retry_count": 0, **settings}

    async def go() -> None:
        with (
            patch.object(nodes_module, "get_stream_writer", return_value=lambda _: None),
            patch.object(nodes_module, "interrupt", new=_interrupt),
        ):
            await nodes_module.max_retries_node(paused, config=None)

    asyncio.run(go())
    assert seen["message"] == "Done-when items still not met after a retry: The cause is written down"


def test_the_ticket_s_acceptance_criteria_are_read_as_before(settings: dict[str, Any]) -> None:
    from embry0.workflows.issue_to_pr.nodes._review import format_review_task
    from embry0.workflows.issue_to_pr.plan_route import plan_route_node

    config = {"configurable": {"pipeline_templates_repo": None, "jobs_repo": None}}
    state = {"job_id": "J", "triage_decision": {"job_kind": "code"}, "task": TICKET, **settings}
    with patch("embry0.workflows.issue_to_pr.plan_route.get_stream_writer", return_value=lambda _e: None):
        out = asyncio.run(plan_route_node(state, config))
    assert out["done_when"]["items"] == [{"id": "d1", "done": "CSV has a header row", "shown_by": ""}]
    block, _step, _field = format_review_task({"task": TICKET, **settings})
    assert "Acceptance criteria:\n- [ ] CSV has a header row\n" in block


def test_the_salvage_commit_reads_as_before_and_the_next_prompt_quotes_it(settings: dict[str, Any]) -> None:
    from embry0.workflows.issue_to_pr.nodes import _developer_delta_sections

    sandbox = _Sandbox()
    asyncio.run(_run_developer(_stopped("max turns", error_code="ERR_AGENT_MAX_TURNS"), sandbox, **settings))
    assert sandbox.messages == [OLD_COMMIT], f"the salvage commit changed with nothing written: {sandbox.messages}"
    record = {"branch": _BRANCH, "agent": "developer", "reason": "turn cap", "committed": True}
    sections = _developer_delta_sections({"unfinished_work": record, **settings})
    assert sections == [UNFINISHED], sections
