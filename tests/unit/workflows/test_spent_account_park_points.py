"""Spent account waits for qa, onboard and pr-review (spec 2026-10-25, addendum): where each parks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from embry0.execution.agent_runner import AgentOutput
from embry0.services.account_waits import AccountWait, AccountWaitInterrupt
from embry0.workflows.qa.subtask_result_schema import CacheHits, SubTaskResult, SubTaskStatus

RESET = datetime.now(UTC) + timedelta(hours=15)
WAIT = AccountWait("anthropic", "quota_exhausted", RESET, model="claude-opus-5", agent="pr-reviewer", watch=True)


def _spent(agent_type: str) -> AgentOutput:
    out = AgentOutput(
        agent_type=agent_type,
        is_error=True,
        error_message="You've hit your weekly limit · resets 12am (UTC)",
        error_code="ERR_PROVIDER_QUOTA_EXHAUSTED",
    )
    out.provider_resets_at = RESET
    return out


# --- pr-review ---------------------------------------------------------------


async def test_a_spent_reviewer_parks_from_cleanup_once_the_sandbox_is_gone() -> None:
    from embry0.workflows.pr_review.nodes import cleanup_node, publish_node, review_node
    from tests.unit.workflows.pr_review.test_nodes import _config, _ready

    class _SpentRunner:
        async def run(
            self,
            container: str,
            config: dict[str, Any],
            network: Any = None,
            on_event: Any = None,
            *,
            resume_session: Any = None,
        ) -> AgentOutput:  # noqa: E501
            return _spent("pr-reviewer")

    config = _config(agent_runner=_SpentRunner())
    state = _ready()
    reviewed = await review_node(state, config)
    rv = reviewed["pr_review"]
    assert rv["final_status"] == "failed" and rv["error_code"] == "ERR_PROVIDER_QUOTA_EXHAUSTED"
    assert AccountWait.from_record(rv["account_wait"]) is not None, "the run left its wait for cleanup"
    state["pr_review"] = rv
    assert await publish_node(state, config) == {}, "nothing is posted for a failed review"
    with pytest.raises(AccountWaitInterrupt) as parked:
        await cleanup_node(state, config)
    assert parked.value.wait.until == RESET
    assert config["configurable"]["sandbox_manager"].destroyed == ["sandbox-1"], "the sandbox went first"
    assert config["configurable"]["pr_reviews_repo"].outcomes == [], "no outcome recorded"


# --- onboard -----------------------------------------------------------------


async def test_a_spent_onboarding_agent_goes_to_cleanup_which_parks(monkeypatch: pytest.MonkeyPatch) -> None:
    from embry0.workflows.onboard.nodes import analyze_node, cleanup_node, route_after_validate, validate_node

    class _Defs:
        def __init__(self, _db: Any) -> None:
            pass

        async def get(self, agent_type: str) -> dict[str, Any]:
            return {"agent_type": agent_type}

    async def spent_run(**_kwargs: Any) -> dict[str, Any]:
        return {
            "agent_outputs": [
                {"is_error": True, "error_code": "ERR_PROVIDER_QUOTA_EXHAUSTED", "account_wait": WAIT.record()}
            ]
        }

    monkeypatch.setattr("embry0.storage.repositories.agent_definitions.AgentDefinitionsRepository", _Defs)
    monkeypatch.setattr("embry0.orchestration.nodes.agent.run_agent_node", spent_run)

    class _Sandbox:
        def __init__(self) -> None:
            self.destroyed: list[str] = []

        async def destroy(self, sandbox_id: str) -> None:
            self.destroyed.append(sandbox_id)

    sandbox = _Sandbox()
    config = {"configurable": {"agent_runner": object(), "db": object(), "sandbox_manager": sandbox}}
    state: dict[str, Any] = {"job_id": "job-1", "repo": "acme/widgets", "onboard": {"round": 0, "sandbox_id": "sb-1"}}
    out = await analyze_node(state, config)
    assert out["onboard"]["final_status"] == "failed" and out["onboard"]["round"] == 1
    state["onboard"] = out["onboard"]
    assert await validate_node(state, config) == {}
    assert route_after_validate(state) == "cleanup", "no second round on a spent account"
    with pytest.raises(AccountWaitInterrupt) as parked:
        await cleanup_node(state, config)
    assert parked.value.wait.provider == "anthropic"
    assert sandbox.destroyed == ["sb-1"]


# --- qa ----------------------------------------------------------------------


async def test_an_apps_result_carries_its_runs_code_and_wait() -> None:
    from embry0.workflows.qa.subtask_nodes import emit_result_node

    base = {"app_name": "hub", "parent_run_id": "job-1", "status": SubTaskStatus.INCONCLUSIVE, "started_at": 0.0}
    spent = {
        **base,
        "agent_outputs": [
            {"is_error": True, "error_code": "ERR_PROVIDER_QUOTA_EXHAUSTED", "account_wait": WAIT.record()}
        ],
    }
    result = (await emit_result_node(spent, {"configurable": {}}))["subtask_result"]
    assert result.error_code == "ERR_PROVIDER_QUOTA_EXHAUSTED" and result.account_wait == WAIT.record()
    recovered = {**base, "agent_outputs": [{"is_error": True, "error_code": "X"}, {"is_error": False}]}
    later = (await emit_result_node(recovered, {"configurable": {}}))["subtask_result"]
    assert later.error_code is None and later.account_wait is None, "only the last run counts"


def _qa_state() -> dict[str, Any]:
    from tests.unit.workflows.qa.test_orchestrator import _QA_YAML_V2

    return {
        "job_id": "11111111-1111-1111-1111-111111111111",
        "repo": "org/repo",
        "branch_name": "main",
        "qa": {"qa_yaml_v2_raw": _QA_YAML_V2, "changed_files": ["apps/hub/app/page.tsx"]},
    }


def _two_apps(monkeypatch: pytest.MonkeyPatch) -> None:
    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    provider = FakeWorkspaceProvider(
        apps=[
            WorkspaceApp("hub", Path("apps/hub"), "@x/hub"),
            WorkspaceApp("companion", Path("apps/companion"), "@x/companion"),
        ],
        packages=[],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub", "@x/companion"}),
            cascade_closure=frozenset({"@x/hub", "@x/companion"}),
            apps_to_qa=frozenset({"@x/hub", "@x/companion"}),
        ),
    )
    monkeypatch.setattr("embry0.workflows.qa.orchestrator.load_provider", lambda name, root, config: provider)


async def test_qa_parks_after_every_app_ended_and_before_anything_is_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    from embry0.workflows.qa.orchestrator import qa_orchestrator_node

    _two_apps(monkeypatch)
    later = AccountWait("anthropic", "quota_exhausted", RESET + timedelta(hours=1))
    seen_configs: list[dict[str, Any]] = []
    ended: list[str] = []

    async def fake_run_subtask(resolved: Any, **kw: Any) -> SubTaskResult:
        seen_configs.append(kw["config"]["configurable"])
        ended.append(resolved.app_name)
        wait = WAIT if resolved.app_name == "hub" else later
        return SubTaskResult(
            app_name=resolved.app_name,
            status=SubTaskStatus.INCONCLUSIVE,
            duration_ms=1,
            cache_hits=CacheHits(),
            error_code="ERR_PROVIDER_QUOTA_EXHAUSTED",
            account_wait=wait.record(),
        )

    class _Results:
        def __init__(self) -> None:
            self.rows: list[str] = []

        async def upsert_with_boot_phase(self, **kw: Any) -> None:
            self.rows.append(kw["app_name"])

    results = _Results()
    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)
    with pytest.raises(AccountWaitInterrupt) as parked:
        await qa_orchestrator_node(
            _qa_state(), {"configurable": {"account_waits": True, "qa_app_results_repo": results}}
        )
    assert sorted(ended) == ["companion", "hub"], "every app ran to its end first"
    assert parked.value.wait.until == later.until, "the latest reset"
    assert results.rows == [], "nothing stored for a run that starts again"
    assert all(c["account_waits"] is False for c in seen_configs), "no app's run parks from inside the fan-out"


async def test_a_qa_app_the_account_refused_gives_the_job_its_code(monkeypatch: pytest.MonkeyPatch) -> None:
    from embry0.workflows.qa.orchestrator import qa_orchestrator_node

    _two_apps(monkeypatch)

    async def fake_run_subtask(resolved: Any, **_kw: Any) -> SubTaskResult:
        if resolved.app_name == "hub":
            return SubTaskResult(
                app_name="hub",
                status=SubTaskStatus.INCONCLUSIVE,
                duration_ms=1,
                cache_hits=CacheHits(),
                error_code="ERR_AUTH_REJECTED",
            )
        return SubTaskResult(
            app_name=resolved.app_name, status=SubTaskStatus.PASSED, duration_ms=1, cache_hits=CacheHits()
        )

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", fake_run_subtask)
    out = await qa_orchestrator_node(_qa_state(), {"configurable": {}})
    assert out["qa"]["error_code"] == "ERR_AUTH_REJECTED"
    assert out["qa"]["per_app_results"][0]["error_code"] in ("ERR_AUTH_REJECTED", None)
