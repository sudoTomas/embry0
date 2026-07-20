"""EMB-35 PR-C policy tests: per-agent turn budgets, triage system-prompt
placement, TRIAGE_MODEL knob, and review input scoping by changed files."""

from unittest.mock import AsyncMock, patch

import pytest

from embry0.orchestration.nodes.agent import DEFAULT_AGENT_MAX_TURNS

# ---------------------------------------------------------------------------
# per-agent max_turns precedence (run_agent_node)
# ---------------------------------------------------------------------------


def _capture_invocation():
    """Patch resolve_agent_invocation to capture its kwargs and abort the run."""
    captured = {}

    def _resolve(**kwargs):
        captured.update(kwargs)
        raise _Sentinel

    return captured, _resolve


class _Sentinel(Exception):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent_type", "explicit", "pipeline_cfg", "expected"),
    [
        ("triage", None, {}, 15),  # role default
        ("developer", None, {}, 40),
        ("review", None, {}, 25),
        ("qa", None, {}, 40),
        ("explorer", None, {}, 40),  # unknown role -> flat fallback
        ("triage", None, {"agent_max_turns": {"triage": 22}}, 22),  # triage-emitted
        ("qa", 250, {"agent_max_turns": {"qa": 10}}, 250),  # explicit caller wins
        ("developer", None, {"agent_max_turns": {"developer": 0}}, 40),  # non-positive ignored
    ],
)
async def test_max_turns_precedence(agent_type, explicit, pipeline_cfg, expected):
    from embry0.orchestration.nodes.agent import run_agent_node

    captured, _resolve = _capture_invocation()
    state = {"pipeline_config": pipeline_cfg, "sandbox_container_id": "c"}
    with patch("embry0.orchestration.nodes.agent.resolve_agent_invocation", side_effect=_resolve):
        try:
            await run_agent_node(
                state=state,
                agent_runner=object(),
                agent_type=agent_type,
                prompt="p",
                max_turns=explicit,
            )
        except _Sentinel:
            pass
    assert captured["max_turns"] == expected


def test_default_map_covers_pipeline_roles():
    assert set(DEFAULT_AGENT_MAX_TURNS) == {"triage", "developer", "review", "qa"}
    assert DEFAULT_AGENT_MAX_TURNS["developer"] == 40  # unchanged from flat era


# ---------------------------------------------------------------------------
# PipelineConfigModel accepts agent_max_turns (extra="forbid" guard)
# ---------------------------------------------------------------------------


def test_pipeline_config_model_accepts_agent_max_turns():
    from embry0.orchestration.state import PipelineConfigModel

    model = PipelineConfigModel(agent_max_turns={"developer": 60})
    assert model.agent_max_turns == {"developer": 60}
    assert PipelineConfigModel().agent_max_turns == {}


# ---------------------------------------------------------------------------
# triage: system prompt rides agent_definition, not the user turn
# ---------------------------------------------------------------------------


TRIAGE_STATE = {
    "job_id": "J",
    "issue_id": "iss",
    "repo": "o/r",
    "task": "x",
    "sandbox_container_id": "C",
    "agent_outputs": [],
    "errors": [],
}


def _triage_result():
    return {
        "agent_outputs": [
            {
                "agent_type": "triage",
                "is_error": False,
                "output": '{"action": "proceed", "reasoning": "ok", "confidence": 0.9}',
                "messages": [{"role": "assistant", "content": "now"}],
                "mode": "anthropic_api",
            }
        ],
        "events": [],
        "total_cost_usd": 0.01,
    }


def _sessions_repo(prior_row):
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=prior_row)
    repo.upsert = AsyncMock()
    return repo


@pytest.mark.asyncio
async def test_triage_system_prompt_moves_to_agent_definition(monkeypatch):
    from embry0.orchestration.nodes.triage import _TRIAGE_SYSTEM_PROMPT
    from embry0.workflows.issue_to_pr.nodes import triage_node

    captured = {}

    async def _run(*args, **kwargs):
        captured["prompt"] = kwargs.get("prompt")
        captured["agent_definition"] = kwargs.get("agent_definition")
        return _triage_result()

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
        patch(
            "embry0.orchestration.nodes.triage.apply_repo_preferences_override",
            new=AsyncMock(side_effect=lambda d, *_a, **_kw: d),
        ),
    ):
        await triage_node(
            dict(TRIAGE_STATE),
            {
                "configurable": {
                    "agent_runner": object(),
                    "credentials": {},
                    "agent_sessions_repo": _sessions_repo(None),
                }
            },
        )

    assert captured["agent_definition"]["system_prompt"] == _TRIAGE_SYSTEM_PROMPT
    # The user turn no longer carries the system prompt.
    assert not captured["prompt"].startswith(_TRIAGE_SYSTEM_PROMPT[:60])
    assert "Model selection (CRITICAL)" not in captured["prompt"]
    assert "Repository: o/r" in captured["prompt"]


@pytest.mark.asyncio
async def test_triage_model_env_knob(monkeypatch):
    from embry0.workflows.issue_to_pr.nodes import triage_node

    monkeypatch.setenv("TRIAGE_MODEL", "claude-haiku-4-5-20251001")
    captured = {}

    async def _run(*args, **kwargs):
        captured["agent_definition"] = kwargs.get("agent_definition")
        return _triage_result()

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
        patch(
            "embry0.orchestration.nodes.triage.apply_repo_preferences_override",
            new=AsyncMock(side_effect=lambda d, *_a, **_kw: d),
        ),
    ):
        await triage_node(
            dict(TRIAGE_STATE),
            {
                "configurable": {
                    "agent_runner": object(),
                    "credentials": {},
                    "agent_sessions_repo": _sessions_repo(None),
                }
            },
        )

    assert captured["agent_definition"]["model"] == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# review: changed-files scoping
# ---------------------------------------------------------------------------


REVIEW_STATE = {
    "job_id": "J",
    "repo": "o/r",
    "branch_name": "embry0/x",
    "pr_url": None,
    "sandbox_container_id": "C",
    "agent_outputs": [],
    "errors": [],
    "pipeline_config": {},
}


def _review_result():
    return {
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


async def _run_review(state, configurable_extra, diff_result, captured):
    from embry0.workflows.issue_to_pr.nodes import review_node

    async def _run(*args, **kwargs):
        captured["prompt"] = kwargs.get("prompt")
        return _review_result()

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(side_effect=_run)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
        patch(
            "embry0.execution.workspace_diff.compute_changed_files_via_diff",
            new=AsyncMock(return_value=diff_result),
        ),
    ):
        return await review_node(
            state,
            {
                "configurable": {
                    "agent_runner": object(),
                    "credentials": {},
                    "agent_sessions_repo": _sessions_repo(None),
                    **configurable_extra,
                }
            },
        )


@pytest.mark.asyncio
async def test_review_prompt_scoped_by_changed_files():
    captured = {}
    result = await _run_review(
        dict(REVIEW_STATE),
        {"docker": object()},
        ["src/api.py", "tests/test_api.py"],
        captured,
    )
    prompt = captured["prompt"]
    assert "Changed files in this branch (2):" in prompt
    assert "- src/api.py" in prompt
    assert "Focus your review on these files" in prompt
    assert "git diff main...HEAD -- <path>" in prompt
    # Change set persisted for downstream reuse.
    assert result["changed_files"] == ["src/api.py", "tests/test_api.py"]


@pytest.mark.asyncio
async def test_review_prompt_unscoped_when_diff_empty():
    captured = {}
    result = await _run_review(dict(REVIEW_STATE), {"docker": object()}, [], captured)
    prompt = captured["prompt"]
    assert "Changed files in this branch" not in prompt
    assert "4. Review the git diff: `git diff main...HEAD`" in prompt
    assert "changed_files" not in result


@pytest.mark.asyncio
async def test_review_prompt_unscoped_without_docker():
    captured = {}
    await _run_review(dict(REVIEW_STATE), {}, ["ignored.py"], captured)
    assert "Changed files in this branch" not in captured["prompt"]


@pytest.mark.asyncio
async def test_review_scoping_respects_base_branch():
    captured = {}
    state = {**REVIEW_STATE, "base_branch": "develop"}
    await _run_review(state, {"docker": object()}, ["a.py"], captured)
    assert "git diff develop...HEAD" in captured["prompt"]
