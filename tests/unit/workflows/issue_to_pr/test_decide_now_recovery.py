"""RAV-1260 — decide-now cap recovery + honest errored-review interrupts.

An agent with investigation tools that dies at max_turns mid-investigation
must get ONE automatic session resume with a decide-now nudge (triage and
review); only a second failure ends/pauses the job. An errored review that
reaches max_retries must be reported as an error, never as "requested
changes", and its truncated narration must not be presented as the verdict.
"""

from __future__ import annotations

import json as _json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph import END
from langgraph.types import Command

from embry0.agents.session import AgentSession, is_max_turns_error
from embry0.workflows.issue_to_pr.nodes import review_node, triage_node
from embry0.workflows.issue_to_pr.nodes._common import DECIDE_NOW_MAX_TURNS


@pytest.fixture(autouse=True)
def _stub_stream_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    import embry0.workflows.issue_to_pr.nodes as nodes_module

    monkeypatch.setattr(nodes_module, "get_stream_writer", lambda: lambda _e: None)


# ---------------------------------------------------------------------------
# is_max_turns_error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Reached maximum number of turns (15)", True),
        ("reached max_turns (40)", True),
        ("Agent timed out after 300s", False),
        ("", False),
        (None, False),
    ],
)
def test_is_max_turns_error(message: str | None, expected: bool) -> None:
    assert is_max_turns_error(message) is expected


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _max_turns_result(agent_type: str, *, with_session: bool = True, cost: float = 0.4) -> dict[str, Any]:
    """run_agent_node updates for a turn-cap death (session state kept by the
    executor for exactly this error class — RAV-1260)."""
    entry: dict[str, Any] = {
        "agent_type": agent_type,
        "is_error": True,
        "error_message": "Reached maximum number of turns (15)",
        "output": "…investigation narration…",
        "cost_usd": cost,
        "duration_ms": 90_000,
        "tools_called": {"Read": 12},
    }
    if with_session:
        entry["messages"] = [{"role": "user", "content": "task"}, {"role": "assistant", "content": "digging"}]
        entry["session_id"] = "sess-cap-1"
    return {
        "agent_outputs": [entry],
        "total_cost_usd": cost,
        "current_stage": f"{agent_type}_failed",
        "errors": [f"{agent_type}: Reached maximum number of turns (15)"],
    }


def _triage_decision_json() -> str:
    return _json.dumps(
        {
            "action": "proceed",
            "confidence": 0.9,
            "pipeline_template": "standard",
            "pipeline_config": {
                "sandbox_profile": "default",
                "max_feedback_loops": 3,
                "reviewer_enabled": True,
                "validator_modes": [],
                "agent_models": {},
                "agent_tools": {},
                "agent_skills": {},
                "budget_usd": 10.0,
                "execution_modes": {},
                "auth_modes": {},
                "system_prompts": {},
                "mcp_servers": {},
            },
            "questions": [],
            "sub_tasks": [],
            "reasoning": "decided from partial evidence",
        }
    )


def _success_result(agent_type: str, output: str, *, cost: float = 0.05) -> dict[str, Any]:
    return {
        "agent_outputs": [
            {
                "agent_type": agent_type,
                "is_error": False,
                "output": output,
                "cost_usd": cost,
                "duration_ms": 8_000,
                "tools_called": {},
            }
        ],
        "total_cost_usd": cost,
        "current_stage": f"{agent_type}_complete",
    }


def _triage_state() -> dict[str, Any]:
    return {
        "job_id": "j-cap",
        "repo": "o/r",
        "task": "do the thing",
        "sandbox_container_id": "c1",
        "agent_outputs": [],
        "errors": [],
        "total_cost_usd": 0.0,
    }


def _triage_config() -> dict[str, Any]:
    return {"configurable": {"agent_runner": MagicMock(), "credentials": {}}}


def _review_state(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "job_id": "j-cap",
        "repo": "o/r",
        "task": "t",
        "pipeline_config": {},
        "total_cost_usd": 0.0,
        "pending_agent_questions": [],
        "agent_question_rounds": 0,
        "agent_questions_exhausted": False,
        "retry_count": 0,
        "sandbox_container_id": "c1",
        "agent_outputs": [],
        "pr_url": "http://x",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Triage decide-now recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_triage_max_turns_recovers_with_decide_now_resume() -> None:
    """Turn-cap death → one session resume with a decide-now prompt and a
    tiny turn budget → the second run's decision JSON is parsed normally."""
    mock_rn = AsyncMock(
        side_effect=[
            _max_turns_result("triage"),
            _success_result("triage", _triage_decision_json()),
        ]
    )
    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=mock_rn):
        result = await triage_node(_triage_state(), _triage_config())

    assert mock_rn.await_count == 2
    retry_kwargs = mock_rn.await_args_list[1].kwargs
    assert retry_kwargs["max_turns"] == DECIDE_NOW_MAX_TURNS
    assert "Do not investigate further" in retry_kwargs["prompt"]
    session = retry_kwargs["resume_session"]
    assert isinstance(session, AgentSession)
    assert session.session_id == "sess-cap-1"
    # First run's cost is carried into the retry's state so run_agent_node
    # accumulates rather than resets.
    assert retry_kwargs["state"]["total_cost_usd"] == pytest.approx(0.4)

    # The job proceeds instead of dying with TRIAGE_MALFORMED.
    assert isinstance(result, Command)
    update = result.update or {}
    assert update.get("error_code") is None
    assert update.get("triage_decision", {}).get("action") == "proceed"
    # Both attempts stay on the trace.
    assert [o.get("is_error") for o in update.get("agent_outputs", [])] == [True, False]


@pytest.mark.asyncio
async def test_triage_max_turns_without_session_fails_as_before() -> None:
    """No resumable session state → no recovery attempt; the existing error
    guard ends the job after the single run."""
    mock_rn = AsyncMock(return_value=_max_turns_result("triage", with_session=False))
    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=mock_rn):
        result = await triage_node(_triage_state(), _triage_config())

    assert mock_rn.await_count == 1
    assert isinstance(result, Command)
    assert result.goto == END
    assert (result.update or {}).get("current_stage") == "failed"


@pytest.mark.asyncio
async def test_triage_second_max_turns_death_ends_the_job() -> None:
    """Only ONE decide-now resume: a second cap death ends the job via the
    normal error guard, not an endless recovery loop."""
    mock_rn = AsyncMock(
        side_effect=[
            _max_turns_result("triage"),
            _max_turns_result("triage"),
        ]
    )
    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=mock_rn):
        result = await triage_node(_triage_state(), _triage_config())

    assert mock_rn.await_count == 2
    assert isinstance(result, Command)
    assert result.goto == END
    assert (result.update or {}).get("current_stage") == "failed"


# ---------------------------------------------------------------------------
# Review decide-now recovery + honest stages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_max_turns_recovers_and_approves() -> None:
    mock_rn = AsyncMock(
        side_effect=[
            _max_turns_result("review"),
            _success_result("review", '{"decision": "approved", "summary": "ok from evidence"}'),
        ]
    )
    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=mock_rn):
        result = await review_node(_review_state(), config={"configurable": {"agent_runner": object()}})

    assert mock_rn.await_count == 2
    retry_kwargs = mock_rn.await_args_list[1].kwargs
    assert retry_kwargs["max_turns"] == DECIDE_NOW_MAX_TURNS
    assert "Do not investigate further" in retry_kwargs["prompt"]
    assert isinstance(retry_kwargs["resume_session"], AgentSession)

    assert isinstance(result, Command)
    assert result.update.get("current_stage") == "review_passed"
    assert result.goto == "verify"  # #298/#299: approved changes are verified next


@pytest.mark.asyncio
async def test_review_non_max_turns_error_goes_to_max_retries_as_review_errored() -> None:
    """A non-cap error gets NO recovery, and the stage marker says the review
    errored — not that it requested changes."""
    mock_rn = AsyncMock(
        return_value={
            "agent_outputs": [
                {
                    "agent_type": "review",
                    "is_error": True,
                    "error_message": "Agent timed out after 300s",
                    "output": "partial narration",
                    "cost_usd": 0.1,
                    "duration_ms": 300_000,
                    "tools_called": {},
                }
            ],
            "total_cost_usd": 0.1,
            "current_stage": "review_failed",
        }
    )
    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=mock_rn):
        result = await review_node(_review_state(), config={"configurable": {"agent_runner": object()}})

    assert mock_rn.await_count == 1
    assert isinstance(result, Command)
    assert result.goto == "max_retries"
    assert result.update.get("current_stage") == "review_errored"


@pytest.mark.asyncio
async def test_review_retry_cap_exhaustion_keeps_its_stage() -> None:
    """A real changes_requested verdict at the retry cap still arrives as
    review_retries_exhausted (the honest RAV-1152 wording path)."""
    mock_rn = AsyncMock(
        return_value=_success_result("review", '{"decision": "changes_requested", "feedback": "still broken"}')
    )
    state = _review_state(retry_count=10)
    state["pipeline_config"] = {"pipeline_config": {"max_feedback_loops": 2}}
    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=mock_rn):
        result = await review_node(state, config={"configurable": {"agent_runner": object()}})

    assert result.goto == "max_retries"
    assert result.update.get("current_stage") == "review_retries_exhausted"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output",
    ["", "I read the diff and it seems fine.", "VERDICT: REJECT. Do not merge.", '{"decision": "rejected"}'],
    ids=["empty", "prose", "prose_reject", "unknown_decision"],
)
async def test_review_unparseable_verdict_pauses_with_its_own_stage(output: str) -> None:
    """#356: a review that ran, did not error, and emitted no readable verdict
    pauses as review_unparseable — not review_passed (the old silent approval)
    and not review_retries_exhausted (it never requested changes)."""
    mock_rn = AsyncMock(return_value=_success_result("review", output))
    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=mock_rn):
        result = await review_node(_review_state(), config={"configurable": {"agent_runner": object()}})

    assert isinstance(result, Command)
    assert result.goto == "max_retries"
    assert result.update.get("current_stage") == "review_unparseable"


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_missing_compiler_pauses_as_its_own_stage() -> None:
    """An approval that skipped tests because the compiler is missing pauses.
    It is not review_passed, and it is not an exhausted change-request loop."""
    output = _json.dumps(
        {
            "decision": "approved",
            "validation": {"tests": {"status": "skipped", "reason": "no_toolchain", "output": "java: not found"}},
            "summary": "No JVM in the sandbox.",
        }
    )
    mock_rn = AsyncMock(return_value=_success_result("review", output))
    with patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=mock_rn):
        result = await review_node(_review_state(), config={"configurable": {"agent_runner": object()}})

    assert isinstance(result, Command)
    assert result.goto == "max_retries", "a missing compiler must not approve"
    assert result.update.get("current_stage") == "review_toolchain_missing"


@pytest.mark.asyncio
async def test_toolchain_stage_maps_to_its_error_code() -> None:
    from embry0.safety.error_codes import ErrorCode, FailureCategory, category_for_code, classify_failure

    code = classify_failure(stage="review_toolchain_missing")
    assert code.value == "ERR_REVIEW_TOOLCHAIN_MISSING", "a missing compiler must not collapse to ERR_UNKNOWN"
    assert code == ErrorCode.REVIEW_TOOLCHAIN_MISSING
    assert category_for_code(code) == FailureCategory.CONFIG


@pytest.mark.asyncio
async def test_review_unparseable_stage_maps_to_its_error_code() -> None:
    """The stage a human sees and the code /stats groups on must agree."""
    from embry0.safety.error_codes import ErrorCode, FailureCategory, category_for_code, classify_failure

    assert classify_failure(stage="review_unparseable") == ErrorCode.REVIEW_VERDICT_UNPARSEABLE
    assert category_for_code(ErrorCode.REVIEW_VERDICT_UNPARSEABLE) == FailureCategory.AGENT
    # It must not collapse back into the code a real exhausted retry loop gets.
    assert classify_failure(stage="review_retries_exhausted") == ErrorCode.MAX_RETRIES


# ---------------------------------------------------------------------------
# max_retries_node honest wording for errored reviews
# ---------------------------------------------------------------------------


async def _run_max_retries(state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run max_retries_node with a capturing interrupt that answers abandon."""
    import embry0.workflows.issue_to_pr.nodes as nodes_module
    from embry0.workflows.issue_to_pr.nodes import max_retries_node

    captured: dict[str, Any] = {}

    def _fake_interrupt(summary: dict[str, Any]) -> str:
        captured.update(summary)
        return "abandon"

    # Every coding pause has the job's branch (the developer names it): a state without one has nothing a "merge as
    # is" could deliver, and that pause says so (#459).
    with patch.object(nodes_module, "interrupt", _fake_interrupt):
        result = await max_retries_node({"branch_name": "embry0/x", **state}, config=None)
    return captured, result


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_max_retries_message_for_missing_toolchain() -> None:
    state = {
        "current_stage": "review_toolchain_missing",
        "retry_count": 0,
        "pr_url": "",
        "branch_name": "embry0/iss-abc",
        "agent_outputs": [
            {
                "agent_type": "review",
                "is_error": False,
                "output": '{"decision": "approved", "validation": {"tests": {"status": "skipped"}}}',
            }
        ],
    }
    summary, result = await _run_max_retries(state)

    assert "compiler" in summary["message"].lower() or "toolchain" in summary["message"].lower()
    assert summary["options"] == ["abandon"]
    assert result["current_stage"] == "abandoned"


@pytest.mark.asyncio
async def test_max_retries_message_for_errored_review() -> None:
    state = {
        "current_stage": "review_errored",
        "retry_count": 0,
        "pr_url": "",
        "agent_outputs": [
            {
                "agent_type": "review",
                "is_error": True,
                "error_message": "Reached maximum number of turns (25)",
                "output": "…investigation narration…",
            }
        ],
    }
    summary, _ = await _run_max_retries(state)

    assert summary["message"].startswith("Review errored before emitting a verdict")
    assert "Reached maximum number of turns (25)" in summary["message"]
    # Narration must not be passed off as a verdict.
    assert "latest_review" not in summary
    assert summary["review_error"] == "Reached maximum number of turns (25)"


@pytest.mark.asyncio
async def test_max_retries_message_for_real_retry_exhaustion_unchanged() -> None:
    state = {
        "current_stage": "review_retries_exhausted",
        "retry_count": 3,
        "pr_url": "",
        "agent_outputs": [
            {
                "agent_type": "review",
                "is_error": False,
                "output": '{"decision": "changes_requested", "feedback": "fix X"}',
            }
        ],
    }
    summary, _ = await _run_max_retries(state)

    assert summary["message"] == "Review requested changes and the retry budget is exhausted (3 attempts)"
    assert "review_error" not in summary
    assert summary["latest_review"].startswith('{"decision"')


@pytest.mark.asyncio
async def test_max_retries_message_for_dev_branch_missing_names_the_real_cause() -> None:
    """The branch really is absent, but say WHY the agent never got to push.

    "Developer finished without pushing its branch" reads as though the agent
    forgot. The usual cause is a turn-cap death or a run burnt retrying a
    policy-blocked tool — information the operator needs to pick between
    continue_retrying and abandon, and which the summary used to swallow.
    """
    state = {
        "current_stage": "dev_branch_missing",
        "retry_count": 0,
        "pr_url": "",
        "agent_outputs": [
            {
                "agent_type": "developer",
                "is_error": True,
                "error_message": "Reached maximum number of turns (70)",
                "output": "…implementation narration…",
            }
        ],
    }
    summary, _ = await _run_max_retries(state)

    assert summary["message"].startswith("Developer finished without pushing its branch to origin")
    assert "Reached maximum number of turns (70)" in summary["message"]


@pytest.mark.asyncio
async def test_max_retries_message_for_dev_branch_missing_without_an_error() -> None:
    """No recorded developer error → keep the original wording, add nothing."""
    state = {
        "current_stage": "dev_branch_missing",
        "retry_count": 0,
        "pr_url": "",
        "agent_outputs": [{"agent_type": "developer", "is_error": False, "output": "done"}],
    }
    summary, _ = await _run_max_retries(state)

    assert summary["message"] == "Developer finished without pushing its branch to origin"


@pytest.mark.asyncio
async def test_max_retries_message_reports_blocked_tool_calls() -> None:
    """Reconstructs the real failure this counter was built for.

    A developer run died at the turn cap having spent it retrying `docker`,
    which the sandbox policy will never allow. Before the counter existed the
    summary said only "finished without pushing its branch", and the obvious
    reading — the agent forgot — was wrong.
    """
    state = {
        "current_stage": "dev_branch_missing",
        "retry_count": 0,
        "pr_url": "",
        "agent_outputs": [
            {
                "agent_type": "developer",
                "is_error": True,
                "error_message": "Reached maximum number of turns (70)",
                "policy_denials": {
                    "Blocked by safety policy: dangerous bash pattern: \\bdocker\\b": 3,
                    "tool 'Bash' is not in this agent's allowlist": 1,
                },
                "output": "…narration…",
            }
        ],
    }
    summary, _ = await _run_max_retries(state)

    assert "Reached maximum number of turns (70)" in summary["message"]
    assert "4 tool call(s) blocked by safety policy" in summary["message"]
    assert "docker" in summary["message"]


@pytest.mark.asyncio
async def test_max_retries_message_omits_denials_when_there_were_none() -> None:
    """A clean run must not gain a noisy trailing clause."""
    state = {
        "current_stage": "dev_branch_missing",
        "retry_count": 0,
        "pr_url": "",
        "agent_outputs": [
            {
                "agent_type": "developer",
                "is_error": True,
                "error_message": "Reached maximum number of turns (70)",
                "policy_denials": {},
                "output": "…narration…",
            }
        ],
    }
    summary, _ = await _run_max_retries(state)

    assert "blocked by safety policy" not in summary["message"]
