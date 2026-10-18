"""#298: a branch job never QAs a `target: deployed` app.

The deployed site does not contain the branch under test, so its verdict says
nothing about the change (ERR_QA_FAILURES_UNRESOLVED, 7 jobs). Standalone QA
keeps deployed targets; every QA run inside the issue-to-PR graph drops them.
"""

from __future__ import annotations

import pytest

from embry0.workflows.qa.orchestrator import qa_orchestrator_node

_ALL_DEPLOYED = """
version: 2
qa_required: always
defaults:
  sandbox_profile: qa-external
apps:
  web:
    target: deployed
    frontend_url: "http://app.internal.example:8080/"
    ready_checks:
      - http: "http://app.internal.example:8080/health"
        expect_status: [200, 302]
"""


def _no_subtasks(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(resolved, **kw):
        raise AssertionError(f"QA sub-task started for {resolved.app_name!r}")

    monkeypatch.setattr("embry0.workflows.qa.orchestrator_helpers.run_subtask", _boom)


def _state(**qa: object) -> dict:
    return {
        "job_id": "33333333-3333-3333-3333-333333333333",
        "repo": "org/deployed-repo",
        "branch_name": "embry0/job-x",
        "qa": {"qa_yaml_v2_raw": _ALL_DEPLOYED, "changed_files": ["frontend/a.tsx"], **qa},
    }


@pytest.mark.asyncio
async def test_the_flag_drops_deployed_apps_even_when_qa_is_forced(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_subtasks(monkeypatch)
    out = await qa_orchestrator_node(_state(exclude_deployed_apps=True), {"configurable": {}})
    qa = out["qa"]
    assert qa["apps_to_qa"] == []
    assert qa["final_status"] == "passed"
    assert any("web" in w and "deployed" in w for w in qa["validation_warnings"])


@pytest.mark.asyncio
async def test_the_issue_to_pr_graph_sets_the_flag_on_every_qa_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whatever routed into QA — verify, triage's rerun_qa, a template without
    a review step — the graph's own QA node carries the rule."""
    _no_subtasks(monkeypatch)
    from embry0.workflows.issue_to_pr.graph import _branch_qa_orchestrator_node

    out = await _branch_qa_orchestrator_node(_state(), {"configurable": {}})
    assert out["qa"]["apps_to_qa"] == []
    assert out["qa"]["exclude_deployed_apps"] is True


def test_the_issue_to_pr_graph_uses_the_branch_variant_and_standalone_qa_does_not() -> None:
    from embry0.workflows.issue_to_pr.graph import IssueToprWorkflow, _branch_qa_orchestrator_node
    from embry0.workflows.qa.graph import QAWorkflow

    assert IssueToprWorkflow().compile().nodes["orchestrate_qa"].bound.afunc is _branch_qa_orchestrator_node
    assert QAWorkflow().compile().nodes["orchestrate_qa"].bound.afunc is qa_orchestrator_node


@pytest.mark.parametrize(
    "qa_extra",
    [
        {},
        {"changed_files": []},
        {"changed_files": [], "changed_files_unavailable": True},
    ],
    ids=["diff", "empty-diff", "diff-unavailable"],
)
@pytest.mark.asyncio
async def test_dropping_every_deployed_app_passes_with_its_own_reason(
    monkeypatch: pytest.MonkeyPatch, qa_extra: dict
) -> None:
    """A branch job whose selection was only deployed targets has nothing to
    verify in-sandbox (#298): it passes, and says why, whatever the diff was."""
    _no_subtasks(monkeypatch)
    out = await qa_orchestrator_node(_state(exclude_deployed_apps=True, **qa_extra), {"configurable": {}})
    qa = out["qa"]
    assert qa["final_status"] == "passed", f"an all-deployed branch job failed: {qa.get('error_message')}"
    assert qa.get("error_code") is None
    reasons = [w for w in qa["validation_warnings"] if w.startswith("QA selected no app")]
    assert reasons, f"the pass carries no empty-selection reason: {qa['validation_warnings']}"
    assert "every selected app is a deployed target" in reasons[0], f"misleading reason: {reasons[0]}"
    assert "dropped: ['web']" in reasons[0], reasons[0]


_MIXED = """
version: 2
workspace_provider:
  type: npm-workspaces-turbo
defaults:
  sandbox_profile: qa-external
apps:
  api:
    boot_command: "echo started"
    frontend_url: "http://localhost:3000"
    ready_checks:
      - http: "http://localhost:3000"
  web:
    target: deployed
    frontend_url: "http://app.internal.example:8080/"
    ready_checks:
      - http: "http://app.internal.example:8080/health"
"""


def _mixed_state(monkeypatch: pytest.MonkeyPatch, *, changed_files_unavailable: bool) -> dict:
    """A branch job on a managed `api` + deployed `web` config, with an empty diff."""
    from pathlib import Path

    from embry0.workspace_providers import AffectedSet, WorkspaceApp
    from embry0.workspace_providers.fakes import FakeWorkspaceProvider

    provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("api", Path("apps/api"), "@x/api")],
        packages=[],
        affected_result=AffectedSet(frozenset(), frozenset(), frozenset()),
    )
    monkeypatch.setattr("embry0.workflows.qa.orchestrator.load_provider", lambda name, root, config: provider)
    _no_subtasks(monkeypatch)
    state = _state(exclude_deployed_apps=True, changed_files=[], changed_files_unavailable=changed_files_unavailable)
    state["qa"]["qa_yaml_v2_raw"] = _MIXED
    return state


@pytest.mark.asyncio
async def test_a_failed_diff_still_fails_when_a_managed_app_could_have_been_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropping the deployed app must not hide that the diff, which selects the
    managed apps, never ran."""
    state = _mixed_state(monkeypatch, changed_files_unavailable=True)
    out = await qa_orchestrator_node(state, {"configurable": {}})
    qa = out["qa"]
    # Precondition: the deployed app was selected and then dropped.
    assert any("skipped web" in w for w in qa["validation_warnings"]), qa["validation_warnings"]
    assert qa["final_status"] == "failed", "a failed diff was hidden behind the deployed-app exclusion"
    assert qa["error_code"] == "ERR_QA_NO_APPS"
    assert "could not be computed" in qa["error_message"]


@pytest.mark.asyncio
async def test_an_empty_diff_still_fails_when_a_managed_app_could_have_been_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deployed app kept on the empty diff and then dropped must not turn
    the vacuous run from #65 into a pass while a managed app is declared."""
    state = _mixed_state(monkeypatch, changed_files_unavailable=False)
    out = await qa_orchestrator_node(state, {"configurable": {}})
    qa = out["qa"]
    # Precondition: the deployed app was selected and then dropped.
    assert any("skipped web" in w for w in qa["validation_warnings"]), qa["validation_warnings"]
    assert qa["final_status"] == "failed", "an empty diff was hidden behind the deployed-app exclusion"
    assert qa["error_code"] == "ERR_QA_NO_APPS"
    assert "is empty" in qa["error_message"], qa["error_message"]
