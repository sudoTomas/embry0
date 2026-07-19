"""Verdict guarantee for stuck runs (EMB-37 workstream C).

When the QA agent RAN (agent_outputs non-empty) but produced no usable
/workspace/.qa/result.json — turn cap, wall-clock timeout, idle kill, or a
malformed report — collect_artifacts_node must synthesize a partial QAResult
(every criterion inconclusive) and report INCONCLUSIVE, not INFRA_FAILURE.
Runs where the agent never ran, or where docker itself fails, stay
INFRA_FAILURE (genuinely embry0's fault).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from embry0.workflows.qa.qa_yaml_v2 import QAReadyCheck
from embry0.workflows.qa.subtask_result_schema import SubTaskStatus

CRITERIA = ["dashboard renders", "nav is reachable"]


def _resolved():
    from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig

    return ResolvedAppConfig(
        app_name="web",
        boot_command=None,
        frontend_url="http://app.internal.example:8080/",
        mode="process",
        sandbox_profile="qa-external",
        ready_checks=[QAReadyCheck(http="http://app.internal.example:8080/health")],
        boot_timeout_seconds=120,
        seed_command=None,
        e2e=None,
        acceptance_criteria=list(CRITERIA),
        target="deployed",
    )


def _state(*, agent_outputs, raw_result=None):
    return {
        "status": None,
        "resolved": _resolved(),
        "sandbox_id": "cid",
        "parent_run_id": "job-1",
        "boot_outcome": None,
        "boot_duration_ms": None,
        "agent_outputs": agent_outputs,
        "raw_result": raw_result or {},
    }


def _docker(run_cmd):
    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(return_value=["docker", "exec"])
    docker.run_cmd = run_cmd
    return {"configurable": {"docker": docker}}


_AGENT_RAN = [{"agent_type": "qa", "is_error": True, "error_message": "Agent timed out after 1800s"}]


async def test_missing_result_json_after_agent_ran_synthesizes_inconclusive():
    from embry0.workflows.qa.subtask_nodes import collect_artifacts_node

    config = _docker(AsyncMock(side_effect=RuntimeError("cat: /workspace/.qa/result.json: No such file or directory")))
    out = await collect_artifacts_node(_state(agent_outputs=_AGENT_RAN), config)

    assert out["status"] == SubTaskStatus.INCONCLUSIVE
    rr = out["raw_result"]
    assert rr["overall"] == "inconclusive"
    assert rr["job_id"] == "job-1__web"
    assert [r["criterion"] for r in rr["acceptance_results"]] == CRITERIA
    assert all(r["status"] == "inconclusive" for r in rr["acceptance_results"])
    # the last agent error is surfaced in the notes for diagnosability
    assert "Agent timed out after 1800s" in rr["acceptance_results"][0]["notes"]


async def test_malformed_json_after_agent_ran_synthesizes_inconclusive():
    from embry0.workflows.qa.subtask_nodes import collect_artifacts_node

    config = _docker(AsyncMock(return_value="not-valid-json{{{{"))
    out = await collect_artifacts_node(_state(agent_outputs=_AGENT_RAN), config)
    assert out["status"] == SubTaskStatus.INCONCLUSIVE
    assert out["raw_result"]["overall"] == "inconclusive"


async def test_schema_invalid_json_after_agent_ran_synthesizes_inconclusive():
    from embry0.workflows.qa.subtask_nodes import collect_artifacts_node

    config = _docker(AsyncMock(return_value='{"schema_version": 1, "unexpected": true}'))
    out = await collect_artifacts_node(_state(agent_outputs=_AGENT_RAN), config)
    assert out["status"] == SubTaskStatus.INCONCLUSIVE
    assert out["raw_result"]["overall"] == "inconclusive"


async def test_missing_result_json_when_agent_never_ran_stays_infra_failure():
    from embry0.workflows.qa.subtask_nodes import collect_artifacts_node

    config = _docker(AsyncMock(side_effect=RuntimeError("cat: /workspace/.qa/result.json: No such file or directory")))
    out = await collect_artifacts_node(_state(agent_outputs=[]), config)
    assert out["status"] == SubTaskStatus.INFRA_FAILURE


async def test_non_missing_file_cat_error_stays_infra_failure():
    """A cat failure that is NOT file-absence (e.g. docker daemon down) is
    genuinely infra even if the agent ran."""
    from embry0.workflows.qa.subtask_nodes import collect_artifacts_node

    config = _docker(AsyncMock(side_effect=RuntimeError("Cannot connect to the Docker daemon")))
    out = await collect_artifacts_node(_state(agent_outputs=_AGENT_RAN), config)
    assert out["status"] == SubTaskStatus.INFRA_FAILURE


async def test_synthesis_preserves_accumulated_raw_result_markers():
    from embry0.workflows.qa.subtask_nodes import collect_artifacts_node

    config = _docker(AsyncMock(return_value="not-valid-json{{{{"))
    state = _state(agent_outputs=_AGENT_RAN, raw_result={"auth_setup": {"source": "command", "cookies": 5}})
    out = await collect_artifacts_node(state, config)
    assert out["raw_result"]["auth_setup"] == {"source": "command", "cookies": 5}


# ---------------------------------------------------------------------------
# EMB-37 workstream D: deployed-target agent budget
# ---------------------------------------------------------------------------


def _resolved_managed():
    from embry0.workflows.qa.qa_yaml_resolve import ResolvedAppConfig

    return ResolvedAppConfig(
        app_name="hub",
        boot_command="npm run start",
        frontend_url="http://localhost:3000",
        mode="process",
        sandbox_profile="slim",
        ready_checks=[QAReadyCheck(http="http://localhost:3000/health")],
        boot_timeout_seconds=120,
        seed_command=None,
        e2e=None,
        acceptance_criteria=["page loads"],
        target="managed",
    )


async def _run_exploratory_capture_budget(monkeypatch, resolved):
    from embry0.workflows.qa.subtask_nodes import exploratory_qa_node

    captured = {}

    async def fake_qa_node(state, config):
        captured["budget"] = state["qa"]["budget_seconds"]
        return {"agent_outputs": []}

    monkeypatch.setattr("embry0.workflows.qa.nodes.qa_node", fake_qa_node)
    state = {
        "status": None,
        "resolved": resolved,
        "sandbox_id": "cid",
        "parent_run_id": "job-1",
        "repo": "o/r",
    }
    await exploratory_qa_node(state, {"configurable": {}})
    return captured["budget"]


async def test_deployed_target_gets_reduced_agent_budget(monkeypatch):
    assert await _run_exploratory_capture_budget(monkeypatch, _resolved()) == 1800


async def test_managed_target_keeps_full_agent_budget(monkeypatch):
    assert await _run_exploratory_capture_budget(monkeypatch, _resolved_managed()) == 7200
