"""Phase 5 Task 7 — triage_node QA-failure routing.

When triage is re-invoked after the QA agent reported ``final_status="failed"``,
the agent must emit a ``qa_failure_action`` field on its inline JSON output
choosing one of three actions:

  - ``retry_developer`` → route to ``developer`` with a prompt addendum +
    focus files, so the developer can fix the QA-identified defect.
  - ``rerun_qa``        → route back into ``init_qa`` unchanged (flaky /
    environmental failure).
  - ``ask_user``        → route to ``ask_user_interrupt`` with the question
    on ``pending_agent_questions`` in the standard shape (RAV-1271).

A missing or malformed action gets ONE repair retry that feeds the
validation error back to the agent (#304); a second invalid answer ends the
job with ``ErrorCode.TRIAGE_QA_ACTION_INVALID`` and
``state["qa"]["final_status"] = "exhausted"``.

``state["qa"]["failure_rounds"]`` is bumped by the graph's
``_qa_failure_bookkeeping_node`` BEFORE this node is re-entered, so triage
does NOT bump it again.
"""

from __future__ import annotations

import json as _json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph import END
from langgraph.types import Command

from embry0.settings.sections.agent_questions import AGENT_QUESTIONS

#: The cap a job reads when no layer sets agent_questions.rounds_cap.
ROUNDS_CAP = AGENT_QUESTIONS.defaults().rounds_cap


def _triage_decision_with_qa_failure_action(
    qa_failure_action: dict[str, Any] | None,
) -> str:
    """Build a TriageDecisionModel-shaped JSON string with qa_failure_action inlined."""
    payload: dict[str, Any] = {
        "action": "proceed",
        "confidence": 0.9,
        "pipeline_template": "standard",
        "pipeline_config": {
            "sandbox_profile": "default",
            "max_feedback_loops": 3,
            "reviewer_enabled": True,
            "validator_modes": [],
            "agent_models": {"developer": "claude-sonnet-4-6"},
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
        "reasoning": "Re-invoked after QA failure",
    }
    if qa_failure_action is not None:
        payload["qa_failure_action"] = qa_failure_action
    return _json.dumps(payload)


def _make_qa_failed_state() -> dict[str, Any]:
    """State as it would arrive at triage after qa_report → qa_failure_bookkeeping
    bumped failure_rounds and routed back to triage."""
    return {
        "job_id": "job-qa-fail",
        "repo": "owner/repo",
        "task": "Add a dashboard widget",
        "sandbox_container_id": "container-abc",
        "agent_outputs": [],
        "errors": [],
        "qa": {
            "needs_qa": True,
            "final_status": "failed",
            "failure_rounds": 1,
            "max_qa_failure_rounds": 2,
            "acceptance_criteria": ["dashboard renders"],
            "attempts": [
                {
                    "attempt_n": 1,
                    "result_summary": {"e2e": {"passed": False, "failures": 1}},
                }
            ],
        },
    }


def _make_config() -> dict[str, Any]:
    return {
        "configurable": {
            "agent_runner": MagicMock(),
            "credentials": {},
            "repo_preferences_repo": None,
        }
    }


def _runner_returning(output: str) -> AsyncMock:
    """Mock run_agent_node that returns a successful triage agent output."""

    async def _impl(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "agent_outputs": [{"agent_type": "triage", "is_error": False, "output": output}],
            "total_cost_usd": 0.02,
            "current_stage": "triage_complete",
        }

    return AsyncMock(side_effect=_impl)


@pytest.mark.asyncio
async def test_triage_routes_retry_developer_after_qa_failure() -> None:
    """When qa.final_status == "failed" and the agent emits
    ``qa_failure_action.kind = retry_developer``, triage_node returns a
    Command(goto="developer") with developer_prompt_addendum and
    developer_focus_files populated."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    output = _triage_decision_with_qa_failure_action(
        {
            "kind": "retry_developer",
            "prompt": "QA failed: dashboard widget shows 500. Fix the data fetch in widget.tsx.",
            "focus_files": ["frontend/widget.tsx", "api/widget.py"],
        }
    )
    runner_mock = _runner_returning(output)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_qa_failed_state(), _make_config())

    assert isinstance(result, Command)
    assert result.goto == "developer"
    update = result.update or {}
    assert (
        update.get("developer_prompt_addendum")
        == "QA failed: dashboard widget shows 500. Fix the data fetch in widget.tsx."
    )
    assert update.get("developer_focus_files") == ["frontend/widget.tsx", "api/widget.py"]
    assert update.get("current_stage") == "qa_retry_developer"


@pytest.mark.asyncio
async def test_triage_routes_rerun_qa_after_qa_failure() -> None:
    """When qa.final_status == "failed" and the agent emits
    ``qa_failure_action.kind = rerun_qa``, triage_node routes to init_qa
    with qa_rerun_reason populated."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    output = _triage_decision_with_qa_failure_action(
        {
            "kind": "rerun_qa",
            "reason": "Boot timed out and prior attempt had partial success — likely flaky DinD startup.",
        }
    )
    runner_mock = _runner_returning(output)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_qa_failed_state(), _make_config())

    assert isinstance(result, Command)
    # RAV-601 fix: the issue-to-pr graph's QA chain head is init_orchestrator
    # (the old "init_qa" target exists only in the QA sub-task graph, so the
    # rerun path could never actually dispatch).
    assert result.goto == "init_orchestrator"
    update = result.update or {}
    assert update.get("qa_rerun_reason") == (
        "Boot timed out and prior attempt had partial success — likely flaky DinD startup."
    )
    assert update.get("current_stage") == "qa_rerun"


@pytest.mark.asyncio
async def test_triage_routes_ask_user_after_qa_failure() -> None:
    """When qa.final_status == "failed" and the agent emits
    ``qa_failure_action.kind = ask_user``, triage_node routes to
    ask_user_interrupt with the question on ``pending_agent_questions`` in
    the standard shape (RAV-1271 — the bespoke ``pending_user_question``
    key was read by nobody, so the interrupt raised with zero questions
    and the job ended corrupt)."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    output = _triage_decision_with_qa_failure_action(
        {
            "kind": "ask_user",
            "question": "Acceptance criterion 'X is visible' conflicts with the change that hides X. Which is intended?",
        }
    )
    runner_mock = _runner_returning(output)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_qa_failed_state(), _make_config())

    assert isinstance(result, Command)
    assert result.goto == "ask_user_interrupt"
    update = result.update or {}
    assert update.get("pending_agent_questions") == [
        {
            "question": (
                "Acceptance criterion 'X is visible' conflicts with the change that hides X. Which is intended?"
            ),
            "asking_node": "triage",
            "importance": "blocking",
        }
    ]
    assert "pending_user_question" not in update
    # The job-wide ask_user cap counts this round like every other ask path.
    assert update.get("agent_question_rounds") == 1
    assert update.get("current_stage") == "qa_ask_user"


@pytest.mark.asyncio
async def test_triage_ask_user_after_qa_failure_respects_question_cap() -> None:
    """RAV-1271: the QA-escalation question rides the job-wide ask_user cap —
    at the cap it routes to max_retries with the standard exhausted shape
    instead of looping the interrupt forever."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    output = _triage_decision_with_qa_failure_action(
        {"kind": "ask_user", "question": "Which behavior is intended here?"}
    )
    state = _make_qa_failed_state()
    state["agent_question_rounds"] = ROUNDS_CAP
    runner_mock = _runner_returning(output)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(state, _make_config())

    assert isinstance(result, Command)
    assert result.goto == "max_retries"
    update = result.update or {}
    assert update.get("agent_questions_exhausted") is True
    assert not update.get("pending_agent_questions")


@pytest.mark.asyncio
async def test_triage_rerun_qa_with_over_long_reason_still_dispatches() -> None:
    """RAV-1248 regression (job-54b89ff5c429): a well-formed rerun_qa whose
    advisory ``reason`` exceeds max_length=500 must be clamped and dispatched,
    not terminated as qa_failure_unresolved."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    long_reason = "The QA harness recorded no result_summary at all which suggests " * 20
    assert len(long_reason) > 500
    output = _triage_decision_with_qa_failure_action({"kind": "rerun_qa", "reason": long_reason})
    runner_mock = _runner_returning(output)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_qa_failed_state(), _make_config())

    assert isinstance(result, Command)
    assert result.goto == "init_orchestrator"
    update = result.update or {}
    reason = update.get("qa_rerun_reason") or ""
    assert len(reason) == 500
    assert reason.endswith("…")
    assert reason.startswith("The QA harness recorded no result_summary")
    assert update.get("current_stage") == "qa_rerun"


@pytest.mark.asyncio
async def test_triage_retry_developer_with_over_long_prompt_still_dispatches() -> None:
    """Same cliff on the sibling action: RetryDeveloper.prompt max_length=4000."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    long_prompt = "QA failed: fix the widget fetch in widget.tsx because " * 100
    assert len(long_prompt) > 4000
    output = _triage_decision_with_qa_failure_action(
        {"kind": "retry_developer", "prompt": long_prompt, "focus_files": ["widget.tsx"]}
    )
    runner_mock = _runner_returning(output)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_qa_failed_state(), _make_config())

    assert isinstance(result, Command)
    assert result.goto == "developer"
    update = result.update or {}
    assert len(update.get("developer_prompt_addendum") or "") == 4000
    assert update.get("developer_focus_files") == ["widget.tsx"]


@pytest.mark.asyncio
async def test_triage_qa_failure_no_action_ends_with_error_code() -> None:
    """When qa.final_status == "failed" but the agent omits qa_failure_action,
    triage_node ends the job with ErrorCode.TRIAGE_QA_ACTION_INVALID and sets
    qa.final_status = "exhausted"."""
    from embry0.safety.error_codes import ErrorCode
    from embry0.workflows.issue_to_pr.nodes import triage_node

    # No qa_failure_action — agent forgot the contract.
    output = _triage_decision_with_qa_failure_action(None)
    runner_mock = _runner_returning(output)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_qa_failed_state(), _make_config())

    assert isinstance(result, Command)
    assert result.goto == END
    update = result.update or {}
    assert update.get("error_code") == ErrorCode.TRIAGE_QA_ACTION_INVALID.value
    qa_block = update.get("qa") or {}
    assert qa_block.get("final_status") == "exhausted"


@pytest.mark.asyncio
async def test_triage_qa_failure_malformed_payload_ends_with_error_code() -> None:
    """A qa_failure_action with the right kind but a payload that fails
    Pydantic validation (e.g. ``retry_developer`` without ``prompt``) should
    also terminate with TRIAGE_QA_ACTION_INVALID rather than crashing or
    silently routing on garbage."""
    from embry0.safety.error_codes import ErrorCode
    from embry0.workflows.issue_to_pr.nodes import triage_node

    # retry_developer requires prompt with min_length=10; "hi" is too short.
    output = _triage_decision_with_qa_failure_action({"kind": "retry_developer", "prompt": "hi"})
    runner_mock = _runner_returning(output)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_qa_failed_state(), _make_config())

    assert isinstance(result, Command)
    assert result.goto == END
    update = result.update or {}
    assert update.get("error_code") == ErrorCode.TRIAGE_QA_ACTION_INVALID.value
    qa_block = update.get("qa") or {}
    assert qa_block.get("final_status") == "exhausted"


@pytest.mark.asyncio
async def test_triage_qa_failure_unknown_kind_ends_with_error_code() -> None:
    """A qa_failure_action with an unknown kind should terminate with
    TRIAGE_QA_ACTION_INVALID rather than fall through silently."""
    from embry0.safety.error_codes import ErrorCode
    from embry0.workflows.issue_to_pr.nodes import triage_node

    output = _triage_decision_with_qa_failure_action({"kind": "halt_universe", "reason": "no"})
    runner_mock = _runner_returning(output)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(_make_qa_failed_state(), _make_config())

    assert isinstance(result, Command)
    assert result.goto == END
    update = result.update or {}
    assert update.get("error_code") == ErrorCode.TRIAGE_QA_ACTION_INVALID.value


@pytest.mark.asyncio
async def test_triage_does_not_bump_failure_rounds() -> None:
    """failure_rounds is owned by the graph's _qa_failure_bookkeeping_node;
    triage_node must not increment it on top. The state arriving at triage
    already has the bumped count."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    state = _make_qa_failed_state()
    # bookkeeping bumped this from 0 → 1 before re-entering triage.
    assert state["qa"]["failure_rounds"] == 1

    output = _triage_decision_with_qa_failure_action(
        {
            "kind": "rerun_qa",
            "reason": "Likely flaky network blip during seed phase.",
        }
    )
    runner_mock = _runner_returning(output)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        result = await triage_node(state, _make_config())

    assert isinstance(result, Command)
    update = result.update or {}
    qa_in_update = update.get("qa")
    # If triage emitted a qa block at all (e.g. via set_qa_decision), it must
    # not have re-incremented failure_rounds. The happy-path rerun_qa update
    # doesn't touch qa, so failure_rounds stays at the pre-triage value via
    # the unmodified state.
    if qa_in_update is not None:
        assert qa_in_update.get("failure_rounds", 1) == 1


# ---- #304: one bounded repair retry ------------------------------------------
#
# job-d05a769e7865 died with triage_qa_failure_action_invalid_payload after
# its PR existed. Its exact payload (an empty sibling-kind field) is now
# dropped deterministically — see test_qa_failure_action.py; the repair turn
# covers what cannot be fixed without the agent: unknown keys, NON-empty
# sibling fields, short/missing fields, unknown kinds.

_STRAY_FIELD_ACTION = {
    "kind": "ask_user",
    "question": "QA cannot log in with the stored E2E credentials - were they rotated?",
    "focus_files": ["e2e/auth.setup.ts"],
}
_VALID_ASK_USER_ACTION = {k: v for k, v in _STRAY_FIELD_ACTION.items() if k != "focus_files"}


def _runner_returning_sequence(outputs: list[str]) -> AsyncMock:
    """Mock run_agent_node that returns ``outputs`` one call at a time."""
    remaining = list(outputs)

    async def _impl(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "agent_outputs": [{"agent_type": "triage", "is_error": False, "output": remaining.pop(0)}],
            "total_cost_usd": 0.02,
            "current_stage": "triage_complete",
        }

    return AsyncMock(side_effect=_impl)


async def _run_triage(runner_mock: AsyncMock) -> Any:
    from embry0.workflows.issue_to_pr.nodes import triage_node

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=runner_mock),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        return await triage_node(_make_qa_failed_state(), _make_config())


@pytest.mark.asyncio
async def test_invalid_action_is_repaired_with_the_validation_error() -> None:
    runner_mock = _runner_returning_sequence(
        [
            _triage_decision_with_qa_failure_action(_STRAY_FIELD_ACTION),
            _triage_decision_with_qa_failure_action(_VALID_ASK_USER_ACTION),
        ]
    )

    result = await _run_triage(runner_mock)

    assert isinstance(result, Command)
    assert result.goto == "ask_user_interrupt"
    assert runner_mock.await_count == 2
    repair_prompt = runner_mock.await_args_list[1].kwargs["prompt"]
    assert "focus_files" in repair_prompt  # the validation error is fed back verbatim
    assert "Extra inputs are not permitted" in repair_prompt
    update = result.update or {}
    assert "error_code" not in update
    # Both runs stay on the record (operator.add reducer appends the update).
    assert len(update["agent_outputs"]) == 2


@pytest.mark.asyncio
async def test_repair_is_bounded_to_one_retry_then_a_specific_error_code() -> None:
    from embry0.safety.error_codes import ErrorCode

    bad = _triage_decision_with_qa_failure_action(_STRAY_FIELD_ACTION)
    runner_mock = _runner_returning_sequence([bad, bad, bad])

    result = await _run_triage(runner_mock)

    assert isinstance(result, Command)
    assert result.goto == END
    assert runner_mock.await_count == 2  # the original run + exactly one repair
    update = result.update or {}
    assert update["error_code"] == ErrorCode.TRIAGE_QA_ACTION_INVALID.value
    assert (update.get("qa") or {}).get("final_status") == "exhausted"
    assert "focus_files" in update["errors"][0]  # operator sees WHAT was invalid


@pytest.mark.asyncio
async def test_repair_run_that_errors_ends_with_the_specific_error_code() -> None:
    from embry0.safety.error_codes import ErrorCode

    calls = 0

    async def _impl(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            output = _triage_decision_with_qa_failure_action(_STRAY_FIELD_ACTION)
            return {"agent_outputs": [{"agent_type": "triage", "is_error": False, "output": output}]}
        return {"agent_outputs": [{"agent_type": "triage", "is_error": True, "error_message": "boom"}]}

    result = await _run_triage(AsyncMock(side_effect=_impl))

    assert isinstance(result, Command)
    assert result.goto == END
    assert calls == 2
    assert (result.update or {})["error_code"] == ErrorCode.TRIAGE_QA_ACTION_INVALID.value


@pytest.mark.asyncio
async def test_valid_action_never_triggers_a_repair_run() -> None:
    runner_mock = _runner_returning_sequence([_triage_decision_with_qa_failure_action(_VALID_ASK_USER_ACTION)])

    result = await _run_triage(runner_mock)

    assert isinstance(result, Command)
    assert result.goto == "ask_user_interrupt"
    assert runner_mock.await_count == 1


@pytest.mark.asyncio
async def test_the_production_payload_routes_without_a_repair_run() -> None:
    """job-d05a769e7865 verbatim: fixed at zero cost, no second agent run."""
    action = {**_VALID_ASK_USER_ACTION, "focus_files": []}
    runner_mock = _runner_returning_sequence([_triage_decision_with_qa_failure_action(action)])

    result = await _run_triage(runner_mock)

    assert isinstance(result, Command)
    assert result.goto == "ask_user_interrupt"
    assert runner_mock.await_count == 1


@pytest.mark.asyncio
async def test_repair_answer_may_be_the_bare_action_object() -> None:
    """The paid retry must not be wasted because the model answered with
    just the corrected object instead of a full decision."""
    runner_mock = _runner_returning_sequence(
        [
            _triage_decision_with_qa_failure_action(_STRAY_FIELD_ACTION),
            "```json\n" + _json.dumps({"kind": "rerun_qa", "reason": "auth setup flaked"}) + "\n```",
        ]
    )

    result = await _run_triage(runner_mock)

    assert isinstance(result, Command)
    assert result.goto == "init_orchestrator"
    assert (result.update or {})["qa_rerun_reason"] == "auth setup flaked"
    # The original decision's config stands; only the action was replaced.
    assert (result.update or {})["triage_decision"]["pipeline_template"] == "standard"


@pytest.mark.asyncio
async def test_repair_prompt_without_a_resumable_session_replays_the_rejected_answer() -> None:
    runner_mock = _runner_returning_sequence(
        [
            _triage_decision_with_qa_failure_action(_STRAY_FIELD_ACTION),
            _triage_decision_with_qa_failure_action(_VALID_ASK_USER_ACTION),
        ]
    )

    await _run_triage(runner_mock)  # the mock outputs carry no session ⇒ non-resumable

    repair_call = runner_mock.await_args_list[1].kwargs
    assert repair_call["resume_session"] is None
    assert "Add a dashboard widget" in repair_call["prompt"]  # full task replayed
    assert "e2e/auth.setup.ts" in repair_call["prompt"]  # and the answer that was rejected
