"""QA live-progress streaming — parent-writer threading and lifecycle events.

Standalone QA runs execute as one giant orchestrator node whose sub-graphs
run under ainvoke(), where get_stream_writer() is dead — so job_logs stayed
empty until the very end. These tests pin the explicit channel that fixes it:
configurable["qa_parent_writer"].
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.workflows.qa import orchestrator_helpers
from embry0.workflows.qa.orchestrator import _qa_orchestrator_node_impl
from embry0.workflows.qa.subtask_result_schema import CacheHits, SubTaskResult, SubTaskStatus


def _resolved(app_name: str):
    return SimpleNamespace(app_name=app_name, target="deployed")


def _result(app_name: str, status=SubTaskStatus.PASSED) -> SubTaskResult:
    return SubTaskResult(
        app_name=app_name,
        status=status,
        duration_ms=1234,
        cache_hits=CacheHits(),
        failure_summary=None,
    )


@pytest.mark.asyncio
async def test_fan_out_emits_started_and_finished_through_parent_writer(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(
        orchestrator_helpers,
        "run_subtask",
        AsyncMock(side_effect=lambda resolved, **kw: _result(resolved.app_name)),
    )
    config = {"configurable": {"qa_parent_writer": events.append}}

    results = await orchestrator_helpers.fan_out_subtasks(
        [_resolved("hub"), _resolved("companion")],
        parent_run_id="job-p1",
        repo="x/y",
        branch_name="main",
        max_concurrent=2,
        config=config,
    )

    assert [r.app_name for r in results] == ["hub", "companion"]
    started = [e for e in events if e["type"] == "qa_subtask_started"]
    finished = [e for e in events if e["type"] == "qa_subtask_finished"]
    assert {e["app"] for e in started} == {"hub", "companion"}
    assert {e["app"] for e in finished} == {"hub", "companion"}
    assert all(e["status"] == str(SubTaskStatus.PASSED) for e in finished)
    assert all(e["duration_ms"] == 1234 for e in finished)


@pytest.mark.asyncio
async def test_fan_out_publishes_running_status_to_qa_bus(monkeypatch):
    monkeypatch.setattr(
        orchestrator_helpers,
        "run_subtask",
        AsyncMock(side_effect=lambda resolved, **kw: _result(resolved.app_name)),
    )
    bus = MagicMock()
    bus.publish = AsyncMock()
    config = {"configurable": {"qa_event_bus": bus}}

    await orchestrator_helpers.fan_out_subtasks(
        [_resolved("hub")],
        parent_run_id="job-p1",
        repo="x/y",
        branch_name=None,
        max_concurrent=1,
        config=config,
    )

    bus.publish.assert_awaited_once()
    run_id, event = bus.publish.await_args.args
    assert run_id == "job-p1"
    assert event["type"] == "subtask_status"
    assert event["app"] == "hub"
    assert event["status"] == "running"


@pytest.mark.asyncio
async def test_fan_out_crash_still_emits_finished_event(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(
        orchestrator_helpers,
        "run_subtask",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    config = {"configurable": {"qa_parent_writer": events.append}}

    results = await orchestrator_helpers.fan_out_subtasks(
        [_resolved("hub")],
        parent_run_id="job-p1",
        repo="x/y",
        branch_name=None,
        max_concurrent=1,
        config=config,
    )

    assert results[0].status == SubTaskStatus.INFRA_FAILURE
    finished = [e for e in events if e["type"] == "qa_subtask_finished"]
    assert len(finished) == 1
    assert finished[0]["status"] == str(SubTaskStatus.INFRA_FAILURE)
    assert "crashed" in finished[0]["failure_summary"]


@pytest.mark.asyncio
async def test_fan_out_without_writer_or_bus_is_silent(monkeypatch):
    """Legacy callers pass no writer/bus — nothing crashes, results unchanged."""
    monkeypatch.setattr(
        orchestrator_helpers,
        "run_subtask",
        AsyncMock(side_effect=lambda resolved, **kw: _result(resolved.app_name)),
    )
    results = await orchestrator_helpers.fan_out_subtasks(
        [_resolved("hub")],
        parent_run_id="job-p1",
        repo="x/y",
        branch_name=None,
        max_concurrent=1,
        config={},
    )
    assert results[0].app_name == "hub"


@pytest.mark.asyncio
async def test_bus_publish_failure_does_not_break_subtask(monkeypatch):
    monkeypatch.setattr(
        orchestrator_helpers,
        "run_subtask",
        AsyncMock(side_effect=lambda resolved, **kw: _result(resolved.app_name)),
    )
    bus = MagicMock()
    bus.publish = AsyncMock(side_effect=RuntimeError("bus down"))
    results = await orchestrator_helpers.fan_out_subtasks(
        [_resolved("hub")],
        parent_run_id="job-p1",
        repo="x/y",
        branch_name=None,
        max_concurrent=1,
        config={"configurable": {"qa_event_bus": bus}},
    )
    assert results[0].status == SubTaskStatus.PASSED


@pytest.mark.asyncio
async def test_orchestrator_persists_qa_running_stage():
    """The orchestrator node writes current_stage=qa_running best-effort at
    entry — 'updates'-mode stage persistence only fires at node boundaries
    and this node IS the whole run."""
    jobs_repo = MagicMock()
    jobs_repo.update = AsyncMock()
    state = {
        "job_id": "job-p1",
        # infra_error outcome short-circuits the impl right after the stage
        # write — the test needs no qa.yaml fixture.
        "qa": {"outcome": {"overall_status": "infra_error"}},
    }
    out = await _qa_orchestrator_node_impl(state, {"configurable": {"jobs_repo": jobs_repo}})
    jobs_repo.update.assert_awaited_once_with("job-p1", current_stage="qa_running")
    assert out["qa"]["outcome"]["overall_status"] == "infra_error"


@pytest.mark.asyncio
async def test_orchestrator_stage_persist_failure_is_swallowed():
    jobs_repo = MagicMock()
    jobs_repo.update = AsyncMock(side_effect=RuntimeError("db down"))
    state = {"job_id": "job-p1", "qa": {"outcome": {"overall_status": "infra_error"}}}
    out = await _qa_orchestrator_node_impl(state, {"configurable": {"jobs_repo": jobs_repo}})
    assert out["qa"]["outcome"]["overall_status"] == "infra_error"


@pytest.mark.asyncio
async def test_qa_node_prefers_parent_writer_and_tags_app():
    """Inside a sub-task (synthetic '<parent>__<app>' id) qa_node must emit
    through configurable['qa_parent_writer'] with the app tag — the
    contextvar writer is dead under the sub-graph's ainvoke()."""
    from embry0.workflows.qa.nodes import qa_node

    state = {
        "job_id": "job-p1__hub",
        "repo": "x/y",
        "qa": {
            "attempts": [{"attempt_n": 1, "sandbox_id": "c1", "artifact_prefix": "job-p1/hub/"}],
            "qa_yaml_parsed": {"mode": "dind"},
            "acceptance_criteria": ["home loads"],
        },
    }
    captured: list[dict] = []
    config = {
        "configurable": {
            "agent_runner": MagicMock(),
            "db": MagicMock(),
            "qa_parent_writer": captured.append,
        }
    }

    fake_run = AsyncMock(return_value={"agent_outputs": []})
    fake_repo = MagicMock()
    fake_repo.get = AsyncMock(
        return_value={
            "model": "claude-sonnet-4-6",
            "tools": [],
            "skills": [],
            "system_prompt": "sp",
            "mcp_servers": {},
            "execution_mode": None,
            "auth_mode": None,
        }
    )

    with (
        patch("embry0.storage.repositories.agent_definitions.AgentDefinitionsRepository", return_value=fake_repo),
        patch("embry0.orchestration.nodes.agent.run_agent_node", fake_run),
    ):
        await qa_node(state, config)

    on_event = fake_run.call_args.kwargs["on_event"]
    on_event({"type": "turn_start", "turn_number": 1})
    assert captured == [{"type": "turn_start", "turn_number": 1, "app": "hub"}]


@pytest.mark.asyncio
async def test_fan_out_emits_running_status_through_the_parent_writer(monkeypatch):
    """spec 3.7: the status change rides the job stream, not only the QA bus."""
    events_out: list[dict] = []
    monkeypatch.setattr(
        orchestrator_helpers, "run_subtask", AsyncMock(side_effect=lambda resolved, **kw: _result(resolved.app_name))
    )
    await orchestrator_helpers.fan_out_subtasks(
        [_resolved("hub")],
        parent_run_id="job-p1",
        repo="x/y",
        branch_name="main",
        max_concurrent=1,
        config={"configurable": {"qa_parent_writer": events_out.append}},
    )
    running = [e for e in events_out if e["type"] == "qa_subtask_status"]
    assert running and running[0]["app"] == "hub" and running[0]["status"] == "running"
    assert "run_id" not in running[0], (
        "the job id is not the contract's run_id (an agent run); the orchestrator stamps job_id"
    )
    types = [e["type"] for e in events_out]
    assert types.index("qa_subtask_started") < types.index("qa_subtask_status") < types.index("qa_subtask_finished")
