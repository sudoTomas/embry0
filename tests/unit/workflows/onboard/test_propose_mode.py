"""Propose mode: the onboarding agent drafts a test setup for a person to apply (ADR 0019).

With a proposal id the onboard job never writes ``repo_qa_configs``. Its
write_config node only marks the draft ready, and the runner stores the
validated and smoke-tested draft on the proposal (``pending``), or marks the
proposal ``failed`` with the reason. Without one, the job writes the setup as
before, and that write is now recorded as an ``onboard`` revision.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from embry0.safety.error_codes import ErrorCode
from embry0.services.issue_executor import IssueExecutor
from embry0.workflows.onboard import nodes as onboard_nodes
from embry0.workflows.onboard.nodes import cleanup_node, write_config_node

REPO = "acme/widgets"
PROPOSAL = "prop-0123456789ab"
DRAFT = """\
version: 2
workspace_provider:
  type: static-apps
  config:
    apps:
      web: {path: "."}
apps:
  web:
    boot_command: "npm ci && npm run dev"
    frontend_url: "http://localhost:5173"
    ready_checks:
      - http: "http://localhost:5173"
        expect_status: 200
"""
NOTES = "Found one web app started with npm run dev."
SMOKE = {"results": {"web": "ready"}, "failures": []}


class _FakeConfigsRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def upsert(self, repo: str, yaml_text: str, updated_by: str = "") -> dict[str, Any]:
        self.rows[repo] = {"repo": repo, "yaml_text": yaml_text, "updated_by": updated_by}
        return self.rows[repo]


def _validated(**extra: Any) -> dict[str, Any]:
    """The onboard block once validate and smoke both passed."""
    return {"validated": True, "qa_yaml_text": DRAFT, "notes_md": NOTES, "smoke": SMOKE, "round": 1, **extra}


# --- the graph -------------------------------------------------------------


def _graph_with_a_draft(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The real onboard graph, with the sandbox and agent steps standing in a validated, smoked draft."""
    from embry0.workflows.onboard.graph import OnboardWorkflow

    async def init_onboard(state: dict[str, Any]) -> dict[str, Any]:
        return {"onboard": {"round": 0, "final_status": "pending"}}

    async def analyze(state: dict[str, Any]) -> dict[str, Any]:
        return {"onboard": {**(state.get("onboard") or {}), "round": 1}}

    async def validate(state: dict[str, Any]) -> dict[str, Any]:
        return {"onboard": {**(state.get("onboard") or {}), **_validated()}}

    async def smoke(state: dict[str, Any]) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(onboard_nodes, "init_onboard_node", init_onboard)
    monkeypatch.setattr(onboard_nodes, "analyze_node", analyze)
    monkeypatch.setattr(onboard_nodes, "validate_node", validate)
    monkeypatch.setattr(onboard_nodes, "smoke_node", smoke)
    return OnboardWorkflow().compile()


@pytest.mark.parametrize("propose", [True, False])
async def test_the_graph_writes_the_setup_only_outside_propose_mode(
    monkeypatch: pytest.MonkeyPatch, propose: bool
) -> None:
    """The flag must survive LangGraph's state schema: an undeclared input key is dropped silently."""
    configs = _FakeConfigsRepo()
    graph = _graph_with_a_draft(monkeypatch)

    final = await graph.ainvoke(
        {"job_id": "job-1", "repo": REPO, "branch_name": "main", "onboard": {}, "onboard_propose": propose},
        config={"configurable": {"qa_configs_repo": configs}},
    )

    ob = final["onboard"]
    assert ob["final_status"] == "completed"
    if propose:
        assert configs.rows == {}, "propose mode wrote the test setup"
        assert ob["store_path"] == "proposal"
    else:
        assert configs.rows[REPO]["yaml_text"] == DRAFT
        assert ob["store_path"] == f"db:repo_qa_configs/{REPO}"


# --- write_config ----------------------------------------------------------


async def test_write_config_in_propose_mode_marks_the_draft_ready_for_review(monkeypatch: pytest.MonkeyPatch) -> None:
    events_out: list[dict[str, Any]] = []
    monkeypatch.setattr(onboard_nodes, "_writer", lambda: events_out.append)

    out = await write_config_node(
        {"repo": REPO, "onboard_propose": True, "onboard": _validated()},
        {"configurable": {}},  # no store at all: propose mode needs none
    )

    ob = out["onboard"]
    assert (ob["final_status"], ob["store_path"]) == ("completed", "proposal")
    progress = [e["message"] for e in events_out if e["type"] == "progress"]
    assert progress == ["Test setup ready for review"]
    completed = [e.get("ok") for e in events_out if e["type"] == "node_completed" and e.get("node") == "write_config"]
    assert completed == [True]


async def test_write_config_in_propose_mode_still_refuses_an_unvalidated_draft() -> None:
    out = await write_config_node(
        {"repo": REPO, "onboard_propose": True, "onboard": {"validated": False, "last_error": "schema"}},
        {"configurable": {}},
    )
    assert out["onboard"]["final_status"] == "failed"


@pytest.mark.requires_postgres
async def test_direct_mode_records_an_onboard_revision(db_with_migrations: Any) -> None:
    from embry0.storage.repositories.repo_config_revisions import RepoConfigRevisionsRepository
    from embry0.storage.repositories.repo_qa_configs import RepoQAConfigsRepository

    configs = RepoQAConfigsRepository(db_with_migrations)

    out = await write_config_node(
        {"repo": REPO, "onboard": _validated()}, {"configurable": {"qa_configs_repo": configs}}
    )

    assert out["onboard"]["final_status"] == "completed"
    row = await configs.get(REPO)
    assert row is not None and (row["yaml_text"], row["updated_by"]) == (DRAFT, "onboard-pipeline")
    revision = await RepoConfigRevisionsRepository(db_with_migrations).latest(REPO, "qa_config")
    assert revision is not None
    assert (revision["source"], revision["actor"], revision["after"]) == (
        "onboard",
        "onboard-pipeline",
        {"yaml_text": DRAFT},
    )


# --- analyze ---------------------------------------------------------------


async def test_the_agent_is_told_to_keep_deployed_apps_look_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from embry0.workflows.onboard.nodes import analyze_node

    class _Defs:
        def __init__(self, db: Any) -> None:
            pass

        async def get(self, agent_type: str) -> dict[str, Any]:
            return {"agent_type": agent_type}

    prompts: list[str] = []

    async def fake_run_agent_node(**kwargs: Any) -> dict[str, Any]:
        prompts.append(kwargs["prompt"])
        return {"agent_outputs": []}

    monkeypatch.setattr("embry0.storage.repositories.agent_definitions.AgentDefinitionsRepository", _Defs)
    monkeypatch.setattr("embry0.orchestration.nodes.agent.run_agent_node", fake_run_agent_node)

    await analyze_node(
        {"job_id": "job-1", "repo": REPO, "onboard": {"round": 0}},
        {"configurable": {"agent_runner": object(), "db": object()}},
    )

    assert prompts, "analyze never ran the agent"
    assert (
        "Every app with target: deployed must carry this guardrail: 'Only look. Never press Send, Save, "
        "Submit or Delete, and never submit a form.'"
    ) in prompts[0]


# --- the runner ------------------------------------------------------------


class _FakeProposals:
    """RepoSetupProposalsRepository.finish over a dict: only a running proposal finishes."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {PROPOSAL: {"status": "running"}}
        self.calls: list[str] = []
        self.broken = False

    async def finish(
        self,
        proposal_id: str,
        *,
        status: str,
        proposed_yaml: str | None = None,
        notes_md: str = "",
        smoke: dict[str, Any] | None = None,
        failure: str = "",
    ) -> bool:
        self.calls.append(f"proposal {status}")
        if self.broken:
            raise RuntimeError("database is down")
        row = self.rows.get(proposal_id)
        if row is None or row["status"] != "running":
            return False
        row.update(status=status, proposed_yaml=proposed_yaml, notes_md=notes_md, smoke=smoke, failure=failure)
        return True


class _FakeJobs:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.updates: list[dict[str, Any]] = []
        self.error_message: str | None = None

    async def get(self, job_id: str) -> dict[str, Any]:
        return {"job_id": job_id, "trace_id": None, "error_message": self.error_message}

    async def update(self, job_id: str, **fields: Any) -> None:
        self.calls.append(f"job {fields['status']}")
        self.updates.append(fields)


@pytest.fixture
def proposals(monkeypatch: pytest.MonkeyPatch) -> _FakeProposals:
    store = _FakeProposals()
    monkeypatch.setattr("embry0.services._executor_run.RepoSetupProposalsRepository", lambda db: store)
    return store


def _executor(proposals: _FakeProposals, stream: Any) -> tuple[IssueExecutor, _FakeJobs]:
    ex = IssueExecutor.__new__(IssueExecutor)
    jobs = _FakeJobs(proposals.calls)
    ex._jobs = jobs
    ex._db = object()
    ex._refuse_if_over_budget = AsyncMock(return_value=False)  # type: ignore[method-assign]
    ex._load_merged_env_vars = AsyncMock(return_value=None)  # type: ignore[method-assign]
    ex._finish_credential_uses = AsyncMock()  # type: ignore[method-assign]
    ex._announce_finished = AsyncMock()  # type: ignore[method-assign]
    ex._execute_workflow_stream = stream  # type: ignore[method-assign]
    return ex, jobs


async def _run(ex: IssueExecutor, *, proposal_id: str | None = PROPOSAL) -> None:
    await ex._run_onboard_workflow("job-1", repo=REPO, branch="main", skip_smoke=False, proposal_id=proposal_id)


async def test_a_propose_run_whose_smoke_passes_leaves_one_pending_proposal_and_no_setup(proposals) -> None:
    configs = _FakeConfigsRepo()
    seen: list[dict[str, Any]] = []

    async def stream(initial_state: dict[str, Any], **_kwargs: Any) -> tuple[dict[str, Any], bool, None]:
        seen.append(initial_state)
        state = {**initial_state, "onboard": _validated()}
        out = await write_config_node(state, {"configurable": {"qa_configs_repo": configs}})
        return {**state, **out}, False, None

    ex, jobs = _executor(proposals, stream)
    await _run(ex)

    assert seen[0]["onboard_propose"] is True
    assert configs.rows == {}, "propose mode wrote the test setup"
    assert proposals.rows[PROPOSAL] == {
        "status": "pending",
        "proposed_yaml": DRAFT,
        "notes_md": NOTES,
        "smoke": SMOKE,
        "failure": "",
    }
    assert proposals.calls == ["job running", "proposal pending", "job completed"], "the draft is stored first"


async def test_a_propose_run_whose_rounds_run_out_leaves_one_failed_proposal(proposals) -> None:
    async def stream(initial_state: dict[str, Any], **_kwargs: Any) -> tuple[dict[str, Any], bool, None]:
        state = {**initial_state, "onboard": _validated(validated=False, last_error="smoke run failed: web")}
        state["onboard"]["final_status"] = "pending"
        return {**state, **(await cleanup_node(state, {"configurable": {}}))}, False, None

    ex, jobs = _executor(proposals, stream)
    await _run(ex)

    row = proposals.rows[PROPOSAL]
    assert row["status"] == "failed"
    assert row["failure"].startswith("no valid config after 3 analysis rounds; last error:\nsmoke run failed: web")
    assert row["notes_md"] == NOTES
    assert row["proposed_yaml"] is None
    assert proposals.calls == ["job running", "proposal failed", "job failed"]
    assert jobs.updates[-1]["error_code"] == ErrorCode.ONBOARD_FAILED.value
    assert jobs.updates[-1]["error_message"] == row["failure"]


async def test_a_crashed_propose_run_fails_its_proposal(proposals) -> None:
    async def stream(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("sandbox vanished")

    ex, jobs = _executor(proposals, stream)
    await _run(ex)

    assert proposals.rows[PROPOSAL]["status"] == "failed"
    assert proposals.rows[PROPOSAL]["failure"] == "onboard workflow crashed: sandbox vanished"
    assert proposals.calls == ["job running", "proposal failed", "job failed"]


async def test_a_refused_propose_run_fails_its_proposal_with_the_jobs_reason(proposals) -> None:
    stream = AsyncMock()
    ex, jobs = _executor(proposals, stream)
    ex._refuse_if_over_budget = AsyncMock(return_value=True)  # type: ignore[method-assign]
    jobs.error_message = "dispatch refused: daily budget reached"

    await _run(ex)

    stream.assert_not_awaited()
    assert proposals.rows[PROPOSAL]["status"] == "failed"
    assert proposals.rows[PROPOSAL]["failure"] == "dispatch refused: daily budget reached"


async def test_a_proposal_that_cannot_be_stored_fails_the_job(proposals) -> None:
    async def stream(initial_state: dict[str, Any], **_kwargs: Any) -> tuple[dict[str, Any], bool, None]:
        return {**initial_state, "onboard": _validated(final_status="completed")}, False, None

    proposals.broken = True
    ex, jobs = _executor(proposals, stream)
    await _run(ex)

    assert [u["status"] for u in jobs.updates] == ["running", "failed"]
    assert jobs.updates[-1]["error_message"] == "could not store the proposal: database is down"
    assert jobs.updates[-1]["error_code"] == ErrorCode.ONBOARD_FAILED.value


async def test_a_proposal_no_longer_running_fails_the_job(proposals) -> None:
    async def stream(initial_state: dict[str, Any], **_kwargs: Any) -> tuple[dict[str, Any], bool, None]:
        return {**initial_state, "onboard": _validated(final_status="completed")}, False, None

    proposals.rows[PROPOSAL]["status"] = "failed"
    ex, jobs = _executor(proposals, stream)
    await _run(ex)

    assert jobs.updates[-1]["status"] == "failed"
    assert jobs.updates[-1]["error_message"].startswith("could not store the proposal: ")


async def test_direct_mode_never_touches_a_proposal(proposals) -> None:
    seen: list[dict[str, Any]] = []

    async def stream(initial_state: dict[str, Any], **_kwargs: Any) -> tuple[dict[str, Any], bool, None]:
        seen.append(initial_state)
        return {**initial_state, "onboard": _validated(final_status="completed")}, False, None

    ex, jobs = _executor(proposals, stream)
    await _run(ex, proposal_id=None)

    assert seen[0]["onboard_propose"] is False
    assert proposals.calls == ["job running", "job completed"]
    assert proposals.rows[PROPOSAL] == {"status": "running"}


# --- starting the job ------------------------------------------------------


def _starter() -> IssueExecutor:
    ex = IssueExecutor.__new__(IssueExecutor)
    ex._jobs = MagicMock()
    ex._jobs.create = AsyncMock(return_value="job-1")
    ex._db = None
    ex._audit_log_path = None
    ex._track_task = MagicMock()  # type: ignore[method-assign]
    ex._run_onboard_workflow = MagicMock()  # type: ignore[method-assign]
    return ex


@pytest.mark.parametrize(
    ("proposal_id", "task"),
    [
        (PROPOSAL, f"Onboard {REPO}@main: propose a test setup for review"),
        (None, f"Onboard {REPO}@main — generate qa.yaml into the config store"),
    ],
)
async def test_the_proposal_id_rides_on_the_job_its_launch_and_its_runner(proposal_id: str | None, task: str) -> None:
    ex = _starter()
    with patch("embry0.services.issue_executor.emit_audit", new=AsyncMock()):
        await ex.start_onboard_job(REPO, "main", proposal_id=proposal_id)

    created = ex._jobs.create.await_args.kwargs
    assert created["task"] == task
    assert created["pipeline_config"].get("proposal_id") == proposal_id
    assert ex._run_onboard_workflow.call_args.kwargs["proposal_id"] == proposal_id
    assert ex._track_task.call_args.kwargs["launch"]["proposal_id"] == proposal_id


async def test_a_relaunch_keeps_propose_mode() -> None:
    ex = _starter()
    ex._run_onboard_workflow = AsyncMock()  # type: ignore[method-assign]
    ex._announce_recovery = AsyncMock()  # type: ignore[method-assign]

    await ex._relaunch(
        "job-1",
        {"runner": "onboard", "repo": REPO, "branch": "main", "skip_smoke": False, "proposal_id": PROPOSAL},
    )

    assert ex._run_onboard_workflow.await_args.kwargs["proposal_id"] == PROPOSAL
