"""RAV-1079: open-PR context injection + duplicate skip across the pipeline.

- triage: open-PR list rides the USER turn; a validated duplicate_of_pr flag
  ends the job as skipped_duplicate linking the PR (skip+link policy); an
  unmatched flag is ignored and the job proceeds.
- developer: fresh prompt carries the list; a job with a known PR is never
  told to open one (the RAV-1092 donor-lookup-failed gap).
- reviewer: prompt carries the list + the duplicates_open_pr verdict field,
  and the review_decision event surfaces the flag.
"""

from unittest.mock import AsyncMock, patch

import pytest
from langgraph.graph import END

_OPEN_PRS = [
    {
        "number": 366,
        "title": "fix(msgraph): stop stub deals",
        "head_ref": "embry0/iss-eafd1fec-eng-1066-watcher",
        "url": "https://github.com/o/r/pull/366",
        "author": "someone",
        "issue_refs": ["ENG-1066"],
        "closes_numbers": [],
        "issue_prefix": "iss-eafd1fec",
    }
]


def _sessions_repo(prior_row):
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=prior_row)
    repo.upsert = AsyncMock()
    return repo


def _triage_state(**extra):
    return {
        "job_id": "J",
        "issue_id": "iss-00000000",
        "repo": "o/r",
        "task": "x",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        **extra,
    }


def _triage_output(payload: str):
    return {
        "agent_outputs": [
            {
                "agent_type": "triage",
                "is_error": False,
                "output": payload,
                "messages": [{"role": "assistant", "content": "now"}],
                "mode": "anthropic_api",
            }
        ],
        "events": [],
        "total_cost_usd": 0.01,
    }


async def _run_triage(state, output_payload, captured):
    from embry0.workflows.issue_to_pr.nodes import triage_node

    async def _run(*args, **kwargs):
        captured["prompt"] = kwargs.get("prompt")
        return _triage_output(output_payload)

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        return await triage_node(
            state,
            {
                "configurable": {
                    "agent_runner": object(),
                    "credentials": {},
                    "agent_sessions_repo": _sessions_repo(None),
                }
            },
        )


_PROCEED = '{"action": "proceed", "confidence": 0.9, "reasoning": "clear task"}'
_PROCEED_DUP = (
    '{"action": "proceed", "confidence": 0.9, "reasoning": "already fixed",'
    ' "duplicate_of_pr": {"pr_number": 366, "reason": "PR 366 is the same fix"}}'
)
_PROCEED_DUP_UNKNOWN = (
    '{"action": "proceed", "confidence": 0.9, "reasoning": "already fixed",'
    ' "duplicate_of_pr": {"pr_number": 999, "reason": "hallucinated"}}'
)


@pytest.mark.asyncio
async def test_triage_prompt_carries_open_pr_list():
    captured = {}
    await _run_triage(_triage_state(open_prs=list(_OPEN_PRS)), _PROCEED, captured)
    assert "Existing OPEN pull requests" in captured["prompt"]
    assert "#366" in captured["prompt"]


@pytest.mark.asyncio
async def test_triage_prompt_clean_without_open_prs():
    captured = {}
    await _run_triage(_triage_state(), _PROCEED, captured)
    assert "Existing OPEN pull requests" not in captured["prompt"]


@pytest.mark.asyncio
async def test_triage_duplicate_flag_ends_job_naming_the_existing_pr():
    captured = {}
    cmd = await _run_triage(_triage_state(open_prs=list(_OPEN_PRS)), _PROCEED_DUP, captured)
    assert cmd.goto == END
    assert cmd.update["current_stage"] == "skipped_duplicate"
    # #474: the existing PR is not this job's — never its pr_url.
    assert "pr_url" not in cmd.update, "a PR the job did not open was put on its pr_url"
    assert "#366" in cmd.update["result_summary"]
    assert "already exists" in cmd.update["result_summary"]
    assert "https://github.com/o/r/pull/366" in cmd.update["result_summary"]
    # No error markers — the executor ends this run skipped, not failed.
    assert not cmd.update.get("error_code")


@pytest.mark.asyncio
async def test_triage_duplicate_flag_with_unknown_pr_is_ignored():
    captured = {}
    cmd = await _run_triage(_triage_state(open_prs=list(_OPEN_PRS)), _PROCEED_DUP_UNKNOWN, captured)
    assert cmd.goto == "plan_route"
    assert cmd.update.get("current_stage") == "triage_complete"


@pytest.mark.asyncio
async def test_triage_duplicate_flag_without_open_prs_is_ignored():
    """A dup flag can never fire on a job the executor exempted (pr-followup):
    no state.open_prs → nothing validates → normal routing."""
    captured = {}
    cmd = await _run_triage(_triage_state(), _PROCEED_DUP, captured)
    assert cmd.goto == "plan_route"


# ---- developer ----

_DEV_STATE = {
    "job_id": "J",
    "issue_id": "iss",
    "repo": "o/r",
    "task": "x",
    "issue_number": 7,
    "sandbox_container_id": "C",
    "agent_outputs": [],
    "errors": [],
    "pipeline_config": {"agent_skills": {"developer": []}, "budget_usd": 50.0},
}


async def _run_developer(state, captured):
    from embry0.workflows.issue_to_pr.nodes import developer_node

    async def _run(*args, **kwargs):
        captured["prompt"] = kwargs.get("prompt")
        return {
            "agent_outputs": [
                {
                    "agent_type": "developer",
                    "is_error": False,
                    "output": "done",
                    "messages": [{"role": "assistant", "content": "now"}],
                    "mode": "anthropic_api",
                }
            ],
            "events": [],
            "total_cost_usd": 0.05,
        }

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
        patch("embry0.workflows.issue_to_pr.nodes._verify_branch_pushed", new=AsyncMock(return_value=True)),
    ):
        return await developer_node(
            state,
            {
                "configurable": {
                    "agent_runner": object(),
                    "credentials": {},
                    "agent_sessions_repo": _sessions_repo(None),
                }
            },
        )


@pytest.mark.asyncio
async def test_developer_full_prompt_carries_open_prs_and_guard():
    captured = {}
    await _run_developer({**_DEV_STATE, "open_prs": list(_OPEN_PRS)}, captured)
    prompt = captured["prompt"]
    assert "Existing OPEN pull requests" in prompt
    assert "do NOT push a duplicate" in prompt
    # Fresh job with no existing PR still creates the branch; the PR itself
    # is orchestrator-side now (RAV-1282) and carries the Closes #N linkage
    # in the fallback body instead of the prompt.
    assert "1. Create branch:" in prompt
    assert "the orchestrator opens the PR automatically" in prompt


@pytest.mark.asyncio
async def test_developer_with_existing_pr_never_told_to_open_one():
    """RAV-1092 gap: a pr-followup job whose donor-session lookup failed got
    the full prompt with 'Create a PR'. With state.pr_url seeded it must get
    the branch-exists / no-new-PR instructions instead."""
    captured = {}
    state = {
        **_DEV_STATE,
        "pr_url": "https://github.com/o/r/pull/366",
        "context": {"type": "git", "repo": "o/r", "ref": "embry0/iss-eafd1fec-eng-1066-watcher"},
    }
    cmd = await _run_developer(state, captured)
    prompt = captured["prompt"]
    assert "already exists and is checked out" in prompt
    assert "do NOT create a new PR" in prompt
    assert "Create a PR with" not in prompt
    assert "1. Create branch:" not in prompt
    # Branch comes from context.ref (RAV-1092), and the known pr_url is kept
    # even though this run's output carried none.
    assert "embry0/iss-eafd1fec-eng-1066-watcher" in prompt
    assert cmd.update["pr_url"] == "https://github.com/o/r/pull/366"


# ---- reviewer ----


async def _run_review(state, captured, output_json):
    from embry0.workflows.issue_to_pr.nodes import review_node

    events: list = []
    captured["events"] = events

    async def _run(*args, **kwargs):
        captured["prompt"] = kwargs.get("prompt")
        return {
            "agent_outputs": [
                {
                    "agent_type": "review",
                    "is_error": False,
                    "output": output_json,
                    "messages": [{"role": "assistant", "content": "now"}],
                    "mode": "anthropic_api",
                }
            ],
            "events": [],
            "total_cost_usd": 0.02,
        }

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=events.append),
    ):
        return await review_node(
            state,
            {
                "configurable": {
                    "agent_runner": object(),
                    "credentials": {},
                    "agent_sessions_repo": _sessions_repo(None),
                }
            },
        )


_REVIEW_STATE = {
    "job_id": "J",
    "repo": "o/r",
    "branch_name": "embry0/x",
    "pr_url": "https://github.com/o/r/pull/1",
    "sandbox_container_id": "C",
    "agent_outputs": [],
    "errors": [],
    "retry_count": 0,
    "pipeline_config": {},
}

_APPROVED_WITH_DUP = (
    '{"decision": "approved", "validation": {}, "review_comments": [],'
    ' "duplicates_open_pr": {"number": 366, "reason": "overlaps the msgraph fix"}, "summary": "ok"}'
)
_APPROVED = '{"decision": "approved", "validation": {}, "review_comments": [], "summary": "ok"}'


@pytest.mark.asyncio
async def test_review_prompt_carries_open_prs_and_schema_field():
    captured = {}
    await _run_review({**_REVIEW_STATE, "open_prs": list(_OPEN_PRS)}, captured, _APPROVED)
    prompt = captured["prompt"]
    assert "Existing OPEN pull requests" in prompt
    assert "duplicates_open_pr" in prompt
    assert "7. If one of the open PRs" in prompt


@pytest.mark.asyncio
async def test_review_prompt_without_open_prs_skips_step_but_keeps_schema():
    captured = {}
    await _run_review(dict(_REVIEW_STATE), captured, _APPROVED)
    prompt = captured["prompt"]
    assert "Existing OPEN pull requests" not in prompt
    assert "7. If one of the open PRs" not in prompt
    assert '"duplicates_open_pr"' in prompt  # schema field is unconditional


@pytest.mark.asyncio
async def test_review_decision_event_surfaces_duplicate_flag():
    captured = {}
    await _run_review({**_REVIEW_STATE, "open_prs": list(_OPEN_PRS)}, captured, _APPROVED_WITH_DUP)
    decision_events = [e for e in captured["events"] if e.get("type") == "review_decision"]
    assert decision_events and decision_events[0]["duplicates_open_pr"] == {
        "number": 366,
        "reason": "overlaps the msgraph fix",
    }


@pytest.mark.asyncio
async def test_review_decision_event_omits_flag_when_absent():
    captured = {}
    await _run_review(dict(_REVIEW_STATE), captured, _APPROVED)
    decision_events = [e for e in captured["events"] if e.get("type") == "review_decision"]
    assert decision_events and "duplicates_open_pr" not in decision_events[0]
