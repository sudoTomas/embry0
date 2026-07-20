"""EMB-35 delta-only prompts: resumed session ⇒ send only the new context.

Each node (developer / triage / review) must send its FULL prompt on a
fresh conversation and a DELTA prompt when a resumable prior session
exists — a delta into a fresh conversation would strand the agent, a
full prompt into a resumed one re-bills the whole context.
"""

from unittest.mock import AsyncMock, patch

import pytest

DEV_STATE = {
    "job_id": "J",
    "issue_id": "iss",
    "repo": "o/r",
    "task": "x",
    "sandbox_container_id": "C",
    "agent_outputs": [],
    "errors": [],
    "pipeline_config": {"agent_skills": {"developer": []}, "budget_usd": 50.0},
}

RESUMABLE_ROW = {
    "job_id": "J",
    "agent_type": "developer",
    "mode": "anthropic_api",
    "messages": [{"role": "user", "content": "earlier"}],
    "session_id": None,
    "session_blob": None,
}


def _sessions_repo(prior_row):
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=prior_row)
    repo.upsert = AsyncMock()
    return repo


def _dev_result():
    return {
        "agent_outputs": [
            {
                "agent_type": "developer",
                "is_error": False,
                "output": "...",
                "messages": [{"role": "assistant", "content": "now"}],
                "mode": "anthropic_api",
            }
        ],
        "events": [],
        "total_cost_usd": 0.05,
    }


async def _run_developer(state, repo, captured):
    from embry0.workflows.issue_to_pr.nodes import developer_node

    async def _run(*args, **kwargs):
        captured["prompt"] = kwargs.get("prompt")
        return _dev_result()

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
        patch("embry0.workflows.issue_to_pr.nodes._verify_branch_pushed", new=AsyncMock(return_value=True)),
    ):
        return await developer_node(
            state,
            {"configurable": {"agent_runner": object(), "credentials": {}, "agent_sessions_repo": repo}},
        )


@pytest.mark.asyncio
async def test_developer_full_prompt_without_prior_session():
    captured = {}
    await _run_developer({**DEV_STATE}, _sessions_repo(None), captured)
    prompt = captured["prompt"]
    assert "Repository: o/r" in prompt
    assert "Instructions:" in prompt
    assert "1. Create branch:" in prompt


@pytest.mark.asyncio
async def test_developer_delta_prompt_with_resumable_session():
    captured = {}
    state = {**DEV_STATE, "feedback_context": "fix the null check in api.py"}
    cmd = await _run_developer(state, _sessions_repo(dict(RESUMABLE_ROW)), captured)
    prompt = captured["prompt"]
    # Delta: feedback present, full-task preamble absent.
    assert "fix the null check in api.py" in prompt
    assert "resuming your previous session" in prompt
    assert "Instructions:" not in prompt
    assert "1. Create branch:" not in prompt
    assert "Task:" not in prompt
    # Response contract still re-sent.
    assert "pr_url, branch, summary, files_changed" in prompt
    # Consumed one-shot context is cleared.
    assert cmd.update["feedback_context"] == ""


@pytest.mark.asyncio
async def test_developer_delta_includes_qa_addendum_and_focus_files():
    captured = {}
    state = {
        **DEV_STATE,
        "developer_prompt_addendum": "The QA run showed the login form 500s",
        "developer_focus_files": ["src/login.ts"],
    }
    cmd = await _run_developer(state, _sessions_repo(dict(RESUMABLE_ROW)), captured)
    prompt = captured["prompt"]
    assert "The QA run showed the login form 500s" in prompt
    assert "src/login.ts" in prompt
    assert cmd.update["developer_prompt_addendum"] is None
    assert cmd.update["developer_focus_files"] == []


@pytest.mark.asyncio
async def test_developer_full_prompt_also_carries_qa_addendum():
    """The QA-failure addendum reaches the developer even when no session
    can be resumed (closes the state.py consumer-wiring follow-up)."""
    captured = {}
    state = {**DEV_STATE, "developer_prompt_addendum": "QA follow-up detail"}
    await _run_developer(state, _sessions_repo(None), captured)
    assert "QA follow-up detail" in captured["prompt"]


@pytest.mark.asyncio
async def test_developer_delta_includes_user_retry_guidance():
    captured = {}
    state = {**DEV_STATE, "user_retry_guidance": "try the other API version"}
    cmd = await _run_developer(state, _sessions_repo(dict(RESUMABLE_ROW)), captured)
    assert "try the other API version" in captured["prompt"]
    assert cmd.update["user_retry_guidance"] is None


@pytest.mark.asyncio
async def test_triage_delta_prompt_on_qa_failure_reentry():
    from embry0.workflows.issue_to_pr.nodes import triage_node

    captured = {}
    fake_result = {
        "agent_outputs": [
            {
                "agent_type": "triage",
                "is_error": False,
                "output": (
                    '{"action": "proceed", "reasoning": "retry dev", "confidence": 0.9,'
                    ' "qa_failure_action": {"kind": "retry_developer", "prompt": "fix the broken search",'
                    ' "focus_files": []}}'
                ),
                "messages": [{"role": "assistant", "content": "now"}],
                "mode": "anthropic_api",
            }
        ],
        "events": [],
        "total_cost_usd": 0.01,
    }

    async def _run(*args, **kwargs):
        captured["prompt"] = kwargs.get("prompt")
        return fake_result

    state = {
        "job_id": "J",
        "issue_id": "iss",
        "repo": "o/r",
        "task": "x",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "qa": {
            "final_status": "failed",
            "failure_rounds": 1,
            "max_qa_failure_rounds": 2,
            "outcome": {"failure_summary": "search box does not filter deals"},
        },
    }
    repo = _sessions_repo({**RESUMABLE_ROW, "agent_type": "triage"})

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
        patch(
            "embry0.orchestration.nodes.triage.apply_repo_preferences_override",
            new=AsyncMock(side_effect=lambda d, *_a, **_kw: d),
        ),
    ):
        await triage_node(
            state,
            {"configurable": {"agent_runner": object(), "credentials": {}, "agent_sessions_repo": repo}},
        )

    prompt = captured["prompt"]
    assert "search box does not filter deals" in prompt
    assert "QA FAILED" in prompt
    # Delta: the 7KB system prompt is NOT re-sent into the resumed session.
    assert "Model selection (CRITICAL)" not in prompt


@pytest.mark.asyncio
async def test_triage_full_prompt_includes_qa_failure_block_when_fresh():
    """A QA-failure re-entry with no resumable session still tells the
    agent what failed."""
    from embry0.workflows.issue_to_pr.nodes import triage_node

    captured = {}
    fake_result = {
        "agent_outputs": [
            {
                "agent_type": "triage",
                "is_error": False,
                "output": (
                    '{"action": "proceed", "reasoning": "rerun", "confidence": 0.8,'
                    ' "qa_failure_action": {"kind": "rerun_qa", "reason": "looks flaky to me"}}'
                ),
                "messages": [{"role": "assistant", "content": "now"}],
                "mode": "anthropic_api",
            }
        ],
        "events": [],
        "total_cost_usd": 0.01,
    }

    async def _run(*args, **kwargs):
        captured["prompt"] = kwargs.get("prompt")
        return fake_result

    state = {
        "job_id": "J",
        "issue_id": "iss",
        "repo": "o/r",
        "task": "x",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "qa": {
            "final_status": "failed",
            "failure_rounds": 1,
            "max_qa_failure_rounds": 2,
            "outcome": {"failure_summary": "boot timed out"},
        },
    }

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
        patch(
            "embry0.orchestration.nodes.triage.apply_repo_preferences_override",
            new=AsyncMock(side_effect=lambda d, *_a, **_kw: d),
        ),
    ):
        await triage_node(
            state,
            {
                "configurable": {
                    "agent_runner": object(),
                    "credentials": {},
                    "agent_sessions_repo": _sessions_repo(None),
                }
            },
        )

    prompt = captured["prompt"]
    assert "boot timed out" in prompt
    # Full prompt: system prompt IS present on a fresh conversation.
    assert "Repository: o/r" in prompt


@pytest.mark.asyncio
async def test_review_delta_prompt_with_resumable_session():
    from embry0.workflows.issue_to_pr.nodes import review_node

    captured = {}
    fake_result = {
        "agent_outputs": [
            {
                "agent_type": "review",
                "is_error": False,
                "output": '{"decision": "approved", "validation": {}, "review_comments": [], "summary": "ok"}',
                "messages": [{"role": "assistant", "content": "now"}],
                "mode": "anthropic_api",
            }
        ],
        "events": [],
        "total_cost_usd": 0.02,
    }

    async def _run(*args, **kwargs):
        captured["prompt"] = kwargs.get("prompt")
        return fake_result

    state = {
        "job_id": "J",
        "repo": "o/r",
        "branch_name": "embry0/x",
        "pr_url": "https://github.com/o/r/pull/1",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "retry_count": 1,
        "pipeline_config": {},
    }
    repo = _sessions_repo({**RESUMABLE_ROW, "agent_type": "review"})

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        await review_node(
            state,
            {"configurable": {"agent_runner": object(), "credentials": {}, "agent_sessions_repo": repo}},
        )

    prompt = captured["prompt"]
    assert "pushed a new revision" in prompt
    # Response schema is re-sent in full; the review preamble is not.
    assert "Return ONLY a JSON object:" in prompt
    assert '"decision": "approved" | "changes_requested"' in prompt
    assert "You are reviewing code changes made by the developer agent" not in prompt


@pytest.mark.asyncio
async def test_review_full_prompt_without_prior_session():
    from embry0.workflows.issue_to_pr.nodes import review_node

    captured = {}
    fake_result = {
        "agent_outputs": [
            {
                "agent_type": "review",
                "is_error": False,
                "output": '{"decision": "approved", "validation": {}, "review_comments": [], "summary": "ok"}',
                "messages": [{"role": "assistant", "content": "now"}],
                "mode": "anthropic_api",
            }
        ],
        "events": [],
        "total_cost_usd": 0.02,
    }

    async def _run(*args, **kwargs):
        captured["prompt"] = kwargs.get("prompt")
        return fake_result

    state = {
        "job_id": "J",
        "repo": "o/r",
        "branch_name": "embry0/x",
        "pr_url": None,
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "pipeline_config": {},
    }

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        await review_node(
            state,
            {
                "configurable": {
                    "agent_runner": object(),
                    "credentials": {},
                    "agent_sessions_repo": _sessions_repo(None),
                }
            },
        )

    assert "You are reviewing code changes made by the developer agent" in captured["prompt"]
