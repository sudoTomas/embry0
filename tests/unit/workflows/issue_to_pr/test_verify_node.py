"""verify node — tiers + the base-vs-head gate, wired into the graph (#298, #299)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph import END

from embry0.safety.error_codes import ErrorCode
from embry0.workflows.issue_to_pr.nodes._verify import should_verify, verification_mode, verify_node
from embry0.workflows.verification.gate import GateReport, TargetRun, build_report, compare_target

JAVA = "platform/api/src/main/java/Lead.java"
TSX = "frontend/apps/leads/src/Todo.tsx"
FULL_PLAN = [
    {"agent_type": "developer", "node_id": "developer", "config": {}},
    {"agent_type": "reviewer", "node_id": "reviewer", "config": {}},
    {"agent_type": "qa", "node_id": "qa", "config": {}},
    {"agent_type": "output", "node_id": "output", "config": {}},
]
_CORPUS = Path(__file__).parents[3] / "fixtures" / "qa-yaml-corpus" / "v2"
DEPLOYED_ONLY_QA = (_CORPUS / "deployed-target.yaml").read_text()
MANAGED_QA = (_CORPUS / "single-app.yaml").read_text()
VERIFICATION = {"test_targets": [{"name": "jvm", "paths": ["platform/**"], "command": "cd platform && ./mvnw test"}]}


def _gate(head: str, base: str, failed: list[str] | None = None) -> GateReport:
    def run(side: str, outcome: str) -> TargetRun:
        names = failed if outcome == "failed" and side == "head" else None
        return TargetRun("jvm", side, 1, outcome, 0 if outcome == "passed" else 1, 5, names, None, "")  # type: ignore[arg-type]

    h, b = run("head", head), run("base", base)
    return build_report(
        targets=[compare_target([h], b)], runs=[h, b], base_sha="b" * 40, head_sha="h" * 40, base_ref="main"
    )


def _state(files: list[str], **extra: Any) -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "repo": "acme/backend",
        "sandbox_container_id": "c1",
        "base_branch": "main",
        "base_branch_source": "preference",
        "branch_name": "feature/lead-timer",
        "changed_files": files,
        "route_plan": FULL_PLAN,
        "route_cursor": 2,  # review approved: the cursor is on the qa step
        "current_stage": "review_passed",
        "qa": {"needs_qa": True, "qa_required_reason": "qa_required=always"},
        **extra,
    }


def _config(
    *, verification: Any = VERIFICATION, qa_yaml: str | None = DEPLOYED_ONLY_QA, mode: str = "on"
) -> tuple[dict[str, Any], MagicMock]:
    prefs = MagicMock()
    prefs.get = AsyncMock(return_value={"verification": verification})
    qa_configs = MagicMock()
    qa_configs.get = AsyncMock(return_value={"yaml_text": qa_yaml} if qa_yaml else None)
    verifications = MagicMock()
    verifications.upsert = AsyncMock()
    configurable = {
        "docker": MagicMock(),
        "repo_preferences_repo": prefs,
        "qa_configs_repo": qa_configs,
        "job_verifications_repo": verifications,
        "embry0_config": SimpleNamespace(verification_mode=mode),
    }
    return {"configurable": configurable}, verifications


def _swept() -> Any:
    from embry0.workflows.verification.sweep import SweepResult

    return AsyncMock(return_value=SweepResult(clean=True))


async def _run(
    state: dict[str, Any], config: dict[str, Any], gate: GateReport | Exception | None, *, diff: list[str] | None = None
):
    """Run the node with the gate stubbed; the sandbox's fresh diff lists ``diff``, by default the stored files."""
    run_gate = AsyncMock(side_effect=gate) if isinstance(gate, Exception) else AsyncMock(return_value=gate)
    fresh = list(state.get("changed_files") or []) if diff is None else diff
    with (
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _e: None),
        patch("embry0.workflows.issue_to_pr.nodes._verify.run_gate", new=run_gate),
        patch("embry0.workflows.issue_to_pr.nodes._verify.sweep_sandbox", new=_swept()),
        patch("embry0.execution.workspace_diff.resolve_base_branch", new=AsyncMock(return_value="main")),
        patch("embry0.execution.workspace_diff.compute_changed_files_via_diff", new=AsyncMock(return_value=fresh)),
    ):
        return await verify_node(state, config), run_gate  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_backend_only_runs_the_gate_and_skips_browser_qa() -> None:
    config, stored = _config()
    cmd, run_gate = await _run(_state([JAVA]), config, _gate("passed", "passed"))
    assert run_gate.await_args.kwargs["base_ref"] == "main"
    assert run_gate.await_args.kwargs["changed_files"] == [JAVA]
    assert cmd.goto == "finalize_output"  # the qa step was pruned, output is next
    assert cmd.update["qa"]["needs_qa"] is False
    assert "backend_only" in cmd.update["qa"]["qa_required_reason"]
    assert cmd.update["verification"]["human_merge_only"] is False
    assert "error_code" not in cmd.update
    stored.upsert.assert_awaited_once()
    assert stored.upsert.await_args.kwargs["result"]["gate"]["verdict"] == "passed"


@pytest.mark.asyncio
async def test_output_tails_go_to_the_database_not_into_checkpoints() -> None:
    config, stored = _config()
    gate = _gate("passed", "passed")
    gate = GateReport.from_dict(
        {**gate.to_dict(), "runs": [{**r, "output_tail": "x" * 500} for r in gate.to_dict()["runs"]]}
    )
    cmd, _ = await _run(_state([JAVA]), config, gate)
    assert {r["output_tail"] for r in cmd.update["verification"]["gate"]["runs"]} == {""}
    assert {len(r["output_tail"]) for r in stored.upsert.await_args.kwargs["result"]["gate"]["runs"]} == {500}


@pytest.mark.asyncio
async def test_new_failures_end_the_job_with_their_own_error_code() -> None:
    config, stored = _config()
    cmd, _ = await _run(_state([JAVA]), config, _gate("failed", "passed", ["LeadTest.idleTimer"]))
    assert cmd.goto == END
    assert cmd.update["error_code"] == ErrorCode.VERIFY_NEW_TEST_FAILURES.value
    assert cmd.update["current_stage"] == "verify_failed"
    assert "LeadTest.idleTimer" in cmd.update["result_summary"]
    stored.upsert.assert_awaited_once()  # stored even though the job fails


@pytest.mark.asyncio
async def test_a_red_baseline_does_not_fail_the_job() -> None:
    config, _ = _config()
    cmd, _ = await _run(_state([JAVA]), config, _gate("failed", "failed"))
    assert cmd.goto == "finalize_output"
    assert "error_code" not in cmd.update
    assert cmd.update["verification"]["gate"]["verdict"] == "unstable"


@pytest.mark.asyncio
async def test_frontend_with_only_a_deployed_qa_target_never_enters_qa() -> None:
    """The 7-job failure mode: QA against the deployed site as a branch gate."""
    config, _ = _config()
    cmd, _ = await _run(_state([TSX, JAVA]), config, _gate("passed", "passed"))
    assert cmd.goto == "finalize_output"
    assert cmd.update["qa"]["needs_qa"] is False
    result = cmd.update["verification"]
    assert result["browser_qa"] == {"planned": "skipped", "reason_code": "no_branch_stack"}
    assert result["human_merge_only"] is True


@pytest.mark.asyncio
async def test_frontend_with_a_branch_booted_app_still_goes_to_qa() -> None:
    config, _ = _config(qa_yaml=MANAGED_QA)
    cmd, _ = await _run(_state([TSX]), config, _gate("passed", "passed"))
    assert cmd.goto == "init_orchestrator"
    assert "qa" not in cmd.update
    assert cmd.update["verification"]["human_merge_only"] is False


@pytest.mark.asyncio
async def test_no_stored_config_reports_not_configured_and_does_not_block() -> None:
    config, _ = _config(verification=None)
    cmd, run_gate = await _run(_state([JAVA]), config, None)
    run_gate.assert_not_awaited()
    assert cmd.goto == "finalize_output"
    assert cmd.update["verification"]["gate"]["verdict"] == "not_configured"
    assert cmd.update["verification"]["human_merge_reasons"] == ["gate_not_configured"]


@pytest.mark.asyncio
async def test_an_unusable_stored_config_degrades_instead_of_failing() -> None:
    config, _ = _config(verification={"test_targets": "nope"})
    cmd, run_gate = await _run(_state([JAVA]), config, None)
    run_gate.assert_not_awaited()
    assert cmd.update["verification"]["gate"]["verdict"] == "not_configured"
    assert "invalid" in cmd.update["verification"]["config_error"]
    assert "error_code" not in cmd.update


@pytest.mark.asyncio
async def test_a_crashing_gate_is_recorded_not_raised() -> None:
    config, _ = _config()
    cmd, _ = await _run(_state([JAVA]), config, RuntimeError("boom"))
    assert cmd.update["verification"]["gate"]["verdict"] == "error"
    assert cmd.update["verification"]["human_merge_only"] is True
    assert "error_code" not in cmd.update


@pytest.mark.asyncio
async def test_a_failing_result_store_does_not_fail_the_job() -> None:
    config, stored = _config()
    stored.upsert = AsyncMock(side_effect=RuntimeError("db down"))
    cmd, _ = await _run(_state([JAVA]), config, _gate("passed", "passed"))
    assert cmd.goto == "finalize_output"


@pytest.mark.asyncio
async def test_an_unknown_diff_is_treated_as_frontend() -> None:
    config, _ = _config()
    cmd, _ = await _run(_state([]), config, _gate("passed", "passed"), diff=[])
    assert cmd.update["verification"]["tier"]["tier"] == "frontend"
    assert cmd.update["verification"]["human_merge_only"] is True


@pytest.mark.asyncio
async def test_shadow_mode_records_and_changes_nothing() -> None:
    config, stored = _config(mode="shadow")
    cmd, _ = await _run(_state([JAVA]), config, _gate("failed", "passed", ["T.a"]))
    assert cmd.goto == "init_orchestrator"  # exactly where review would have sent it
    assert "error_code" not in cmd.update
    assert "qa" not in cmd.update
    assert stored.upsert.await_args.kwargs["result"]["mode"] == "shadow"
    assert stored.upsert.await_args.kwargs["result"]["blocking"] is True


def test_the_qa_fixtures_are_what_the_tests_say_they_are() -> None:
    from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

    deployed, managed = parse_qa_yaml_v2(DEPLOYED_ONLY_QA), parse_qa_yaml_v2(MANAGED_QA)
    assert deployed.deployed_app_names() and not deployed.managed_app_names()
    assert managed.managed_app_names() and not managed.deployed_app_names()


def test_mode_parsing_never_raises_and_fails_safe() -> None:
    assert verification_mode(SimpleNamespace(verification_mode="ON ")) == "on"
    assert verification_mode(SimpleNamespace(verification_mode="of")) == "shadow"
    # No config at all is not a licence to act (review of #341).
    assert verification_mode(None) == "shadow"
    assert verification_mode(SimpleNamespace(verification_mode="")) == "shadow"


def test_the_shipped_default_is_shadow() -> None:
    from embry0.config import Embry0Config

    assert Embry0Config(_env_file=None).verification_mode == "shadow"


def test_only_jobs_that_produce_a_change_are_verified() -> None:
    on = SimpleNamespace(verification_mode="on")
    assert should_verify({"route_plan": FULL_PLAN}, on) is True
    assert should_verify({}, on) is True  # legacy chain without a plan
    assert should_verify({"route_plan": [{"agent_type": "reviewer"}]}, on) is False  # review-only
    assert should_verify({"route_plan": FULL_PLAN}, SimpleNamespace(verification_mode="off")) is False


def test_graph_wires_verify_between_review_and_the_route_plan() -> None:
    from embry0.workflows.issue_to_pr.graph import IssueToprWorkflow

    graph = IssueToprWorkflow().compile().get_graph()
    edges = {(e.source, e.target) for e in graph.edges}
    assert ("review", "verify") in edges
    for target in ("init_orchestrator", "finalize_output", "__end__"):
        assert ("verify", target) in edges


# ── the real compiled graph ──────────────────────────────────────────────


async def _drive_real_graph(
    monkeypatch: pytest.MonkeyPatch, files: list[str], gate: GateReport
) -> tuple[dict, list[str]]:
    """The real issue-to-PR graph and the real verify node; agents stubbed.

    Returns (final state, QA/output nodes that ran).
    """
    from langgraph.types import Command

    import embry0.workflows.issue_to_pr.graph as graph_mod

    visited: list[str] = []

    async def _init(state):
        return {"current_stage": "init"}

    async def _triage(state):
        update = {"route_plan": FULL_PLAN, "route_cursor": 0, "qa": {"needs_qa": True}}
        return Command(goto="developer", update=update)

    async def _developer(state):
        return Command(goto="review", update={"route_cursor": 1, "branch_name": "embry0/job-1"})

    async def _review(state):
        update = {"current_stage": "review_passed", "route_cursor": 2, "changed_files": files}
        return Command(goto="verify", update=update)

    def _recorder(name: str, result: dict):
        async def _node(state, config=None):
            visited.append(name)
            return result

        return _node

    for name, stub in {
        "init_node": _init,
        "triage_node": _triage,
        "developer_node": _developer,
        "review_node": _review,
        "init_orchestrator_node": _recorder("init_orchestrator", {}),
        "_branch_qa_orchestrator_node": _recorder("orchestrate_qa", {"qa": {"final_status": "failed"}}),
        "qa_report_node": _recorder("qa_report", {}),
        "finalize_output_node": _recorder("finalize_output", {"result_summary": "done"}),
    }.items():
        monkeypatch.setattr(graph_mod, name, stub)

    config, _ = _config()
    with (
        patch("embry0.workflows.issue_to_pr.nodes._verify.run_gate", new=AsyncMock(return_value=gate)),
        patch("embry0.workflows.issue_to_pr.nodes._verify.sweep_sandbox", new=_swept()),
        patch("embry0.execution.workspace_diff.resolve_base_branch", new=AsyncMock(return_value="main")),
        patch("embry0.execution.workspace_diff.compute_changed_files_via_diff", new=AsyncMock(return_value=files)),
    ):
        final = (
            await graph_mod.IssueToprWorkflow()
            .compile()
            .ainvoke({"job_id": "job-1", "repo": "acme/backend", "sandbox_container_id": "c1"}, config)
        )
    return final, visited


@pytest.mark.asyncio
async def test_real_graph_an_approved_frontend_change_completes_without_deployed_qa(monkeypatch) -> None:
    """job-5877820e5175 replayed: frontend + backend diff, QA config deployed-only."""
    final, visited = await _drive_real_graph(monkeypatch, [TSX, JAVA], _gate("passed", "passed"))
    assert visited == ["finalize_output"]
    assert final.get("error_code") is None
    assert final["verification"]["human_merge_only"] is True
    assert final["qa"]["needs_qa"] is False


@pytest.mark.asyncio
async def test_real_graph_new_test_failures_end_the_job_before_qa_or_output(monkeypatch) -> None:
    final, visited = await _drive_real_graph(monkeypatch, [JAVA], _gate("failed", "passed", ["LeadTest.x"]))
    assert visited == []
    assert final["error_code"] == ErrorCode.VERIFY_NEW_TEST_FAILURES.value
    assert final["current_stage"] == "verify_failed"


# ── the change's files come from a fresh diff in a swept sandbox ─────────────

CLEAN_SWEEP = "EMBRY0_SWEEP stopped=0 passes=0 survivors=0 unread=0 pids=\n"
PARSED_HEAD, PARSED_BASE = "d" * 40, "e" * 40


class _Sandbox:
    """The dev sandbox as the verify node and the gate drive it: a sweep, the base, the diff, the runs.

    ``diff`` is what ``git diff --name-only`` prints, or an exception it fails
    with. Runs pass; their logs say so.
    """

    def __init__(self, *, diff: str | Exception = "", sweep: tuple[int, str, str] = (0, CLEAN_SWEEP, "")) -> None:
        self.diff, self.sweep = diff, sweep
        self.steps: list[str] = []
        self.diff_caps: list[int | None] = []
        self.checkouts: list[str] = []
        self.listed: list[str] = []
        self.run_dirs: list[str] = []

    def build_exec_cmd(self, container: str, command: list[str], **kwargs: Any) -> dict[str, Any]:
        return {"command": command, "env": kwargs.get("env") or {}}

    async def run_cmd_status(self, cmd: dict[str, Any], timeout: int = 60, *, max_bytes: Any = None):
        command, env = cmd["command"], cmd["env"]
        if "EMBRY0_SWEEP" in command[-1]:
            self.steps.append("sweep")
            return self.sweep
        if "EMBRY0_VERIFY_CMD" in env:
            self.steps.append("run")
            self.run_dirs.append(env["EMBRY0_VERIFY_DIR"])
            return 0, "", ""
        if command[0] == "tail":
            self.steps.append("log")
            return 0, "1 passed\n", ""
        raise AssertionError(f"unexpected exec: {command[-1][:80]}")

    async def run_cmd(self, cmd: dict[str, Any], timeout: int = 60, *, max_bytes: Any = None, **_: Any) -> str:
        from tests.fixtures.sandbox_git_local import readable

        text = readable(cmd["command"])  # the step's own git, without the prelude's
        if "--name-only" in text:
            self.steps.append("diff")
            self.diff_caps.append(max_bytes)
            if isinstance(self.diff, Exception):
                raise self.diff
            return self.diff.strip()
        if cmd["command"][:2] == ["sh", "-c"] and "rm -rf" in cmd["command"][-1]:
            self.steps.append("cleanup")
            return ""
        if "EMBRY0_VERIFY_HEAD" in text:
            self.steps.append("prepare")
            return f"EMBRY0_VERIFY_HEAD={PARSED_HEAD}\nEMBRY0_VERIFY_BASE={PARSED_BASE}"
        if "ls-remote" in text:
            self.steps.append("delivered")
            self.listed.append(text)
            return f"{PARSED_HEAD}\trefs/heads/{text.rsplit('refs/heads/', 1)[-1].strip()}"
        if "index-pack" in text:
            self.steps.append("checkout")
            self.checkouts.append(text)
            return ""
        if "worktree remove" in text:
            self.steps.append("cleanup")
            return ""
        if "fetch" in text:
            self.steps.append("fetch")
            return ""
        raise AssertionError(f"unexpected exec: {text[:80]}")


async def _run_in(sandbox: _Sandbox, state: dict[str, Any], *, gate: GateReport | None = None):
    config, stored = _config()
    config["configurable"]["docker"] = sandbox
    if gate is None:
        with patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _e: None):
            cmd = await verify_node(state, config)  # type: ignore[arg-type]
        return cmd, None, stored
    run_gate = AsyncMock(return_value=gate)
    with (
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _e: None),
        patch("embry0.workflows.issue_to_pr.nodes._verify.run_gate", new=run_gate),
    ):
        cmd = await verify_node(state, config)  # type: ignore[arg-type]
    return cmd, run_gate, stored


@pytest.mark.asyncio
async def test_the_change_is_listed_afresh_after_the_sweep_not_taken_from_the_review() -> None:
    from embry0.execution.sandbox_git import GIT_LISTING_MAX_BYTES

    sandbox = _Sandbox(diff=f"{JAVA}\n")
    cmd, run_gate, _ = await _run_in(sandbox, _state([TSX]), gate=_gate("passed", "passed"))
    assert run_gate is not None
    assert run_gate.await_args.kwargs["changed_files"] == [JAVA], (
        "the gate's files must come from a fresh diff, not the list the review stored: "
        f"{run_gate.await_args.kwargs['changed_files']}"
    )
    assert sandbox.steps[:1] == ["sweep"] and "diff" in sandbox.steps, (
        f"the sweep runs before the diff: {sandbox.steps}"
    )
    assert sandbox.diff_caps == [GIT_LISTING_MAX_BYTES], f"the diff is a listing of paths: {sandbox.diff_caps}"
    assert cmd.update["verification"]["tier"]["tier"] == "backend_only", cmd.update["verification"]["tier"]


@pytest.mark.asyncio
async def test_a_diff_that_fails_is_an_unknown_change_so_every_target_runs() -> None:
    from embry0.workflows.verification.config import load_verification_config
    from embry0.workflows.verification.runner import select_targets

    sandbox = _Sandbox(diff=RuntimeError("Docker command failed: fatal: no merge base"))
    cmd, run_gate, _ = await _run_in(sandbox, _state([JAVA]), gate=_gate("passed", "passed"))
    assert run_gate is not None
    files = run_gate.await_args.kwargs["changed_files"]
    assert files == [], f"a diff that failed is not the review's list; it is unknown, []: {files}"
    config, _ = load_verification_config(VERIFICATION)
    assert config is not None and select_targets(config.test_targets, files) == config.test_targets
    assert cmd.update["verification"]["tier"]["tier"] == "frontend", "an unknown change takes the strictest tier"


@pytest.mark.asyncio
async def test_a_sandbox_the_sweep_could_not_clear_leaves_the_change_unknown() -> None:
    survivors = (0, "EMBRY0_SWEEP stopped=4 passes=20 survivors=1 unread=0 pids=88\n", "")
    sandbox = _Sandbox(diff=f"{JAVA}\n", sweep=survivors)
    cmd, run_gate, _ = await _run_in(sandbox, _state([JAVA]), gate=_gate("passed", "passed"))
    assert run_gate is not None
    assert run_gate.await_args.kwargs["changed_files"] == [], "a diff read beside a surviving process is not trusted"
    assert "diff" not in sandbox.steps, f"no diff runs while a process could still change the tree: {sandbox.steps}"
    assert cmd.update["verification"]["tier"]["tier"] == "frontend"


@pytest.mark.asyncio
async def test_the_stored_head_sha_is_the_one_the_gate_parsed() -> None:
    """The merge-approval check compares the stored head with the PR's: it is the sha the prepare step printed."""
    sandbox = _Sandbox(diff=f"{JAVA}\n")
    cmd, _, stored = await _run_in(sandbox, _state([JAVA]))
    gate = stored.upsert.await_args.kwargs["result"]["gate"]
    assert gate["verdict"] == "passed", gate
    assert (gate["head_sha"], gate["base_sha"]) == (PARSED_HEAD, PARSED_BASE), (gate["head_sha"], gate["base_sha"])
    assert cmd.update["verification"]["gate"]["head_sha"] == PARSED_HEAD


@pytest.mark.asyncio
async def test_the_stored_head_is_the_commit_the_head_run_tested() -> None:
    """The head the result names, the tree the head run ran in and the change the targets were picked by agree."""
    from embry0.execution.sandbox_git import WORKSPACE
    from embry0.workflows.verification.runner import BASE_DIR, HEAD_DIR

    sandbox = _Sandbox(diff=f"{JAVA}\n")
    cmd, _, stored = await _run_in(sandbox, _state([TSX]))
    gate = stored.upsert.await_args.kwargs["result"]["gate"]
    assert WORKSPACE not in sandbox.run_dirs, f"the head run tests the commit, not the working copy: {sandbox.run_dirs}"
    assert sandbox.run_dirs == [HEAD_DIR, BASE_DIR], sandbox.run_dirs
    assert sandbox.checkouts, f"each run's tree is built from its commit: {sandbox.steps}"
    head_checkout = sandbox.checkouts[0]
    assert (
        f"git init --quiet --template= {HEAD_DIR};" in head_checkout and f"--no-walk {PARSED_HEAD} " in head_checkout
    ), f"the head tree is a checkout of the stored head {PARSED_HEAD[:7]}: {head_checkout[:300]}"
    listing = "ls-remote https://github.com/acme/backend.git refs/heads/feature/lead-timer"
    assert sandbox.listed and sandbox.listed[0].rstrip().endswith(listing), (
        f"the stored head is the tip of the job's branch on the forge: {sandbox.listed}"
    )
    assert gate["head_sha"] == PARSED_HEAD, gate["head_sha"]
    assert cmd.update["verification"]["tier"]["backend"] == [JAVA], "the tier is the fresh diff's"


@pytest.mark.asyncio
async def test_a_repository_without_test_targets_checks_nothing_out() -> None:
    config, _ = _config(verification=None)
    sandbox = _Sandbox(diff=f"{JAVA}\n")
    config["configurable"]["docker"] = sandbox
    with patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _e: None):
        cmd = await verify_node(_state([JAVA]), config)  # type: ignore[arg-type]
    assert cmd.update["verification"]["gate"]["verdict"] == "not_configured"
    assert sandbox.steps == ["sweep", "fetch", "diff"], f"no worktree and no run without a test target: {sandbox.steps}"


@pytest.mark.asyncio
async def test_the_node_gives_the_gate_the_jobs_branch() -> None:
    """Without it the gate cannot check that the head it tests is the one the pull request carries."""
    config, _ = _config()
    _, run_gate = await _run(_state([JAVA]), config, _gate("passed", "passed"))
    assert run_gate.await_args.kwargs.get("branch") == "feature/lead-timer", run_gate.await_args.kwargs


@pytest.mark.asyncio
async def test_the_node_gives_the_gate_the_url_embry0_cloned_from() -> None:
    """The gate reads the forge there, not through a remote the repository's own config names."""
    config, _ = _config()
    repos = MagicMock()
    repos.forge_for = AsyncMock(return_value="gitlab")
    config["configurable"]["repos_repo"] = repos
    config["configurable"]["embry0_config"] = SimpleNamespace(
        verification_mode="on", gitlab_url="https://gitlab.example"
    )
    _, run_gate = await _run(_state([JAVA]), config, _gate("passed", "passed"))
    assert run_gate.await_args.kwargs.get("forge_url") == "https://gitlab.example/acme/backend.git", (
        f"the gate reads the forge where the clone did: {run_gate.await_args.kwargs}"
    )


@pytest.mark.asyncio
async def test_the_nodes_base_and_diff_read_the_forge_at_the_clone_url() -> None:
    from embry0.services.base_branch import FALLBACK_SOURCE

    config, _ = _config()
    resolved = AsyncMock(return_value="main")
    diffed = AsyncMock(return_value=[JAVA])
    with (
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _e: None),
        patch(
            "embry0.workflows.issue_to_pr.nodes._verify.run_gate", new=AsyncMock(return_value=_gate("passed", "passed"))
        ),
        patch("embry0.workflows.issue_to_pr.nodes._verify.sweep_sandbox", new=_swept()),
        patch("embry0.execution.workspace_diff.resolve_base_branch", new=resolved),
        patch("embry0.execution.workspace_diff.compute_changed_files_via_diff", new=diffed),
    ):
        await verify_node(_state([JAVA], base_branch_source=FALLBACK_SOURCE), config)  # type: ignore[arg-type]
    url = "https://github.com/acme/backend.git"
    assert resolved.await_args.kwargs.get("forge_url") == url, (
        f"the base branch comes from the forge: {resolved.await_args}"
    )
    assert diffed.await_args.kwargs.get("forge_url") == url, (
        f"the diff's base comes from the forge: {diffed.await_args}"
    )


@pytest.mark.asyncio
async def test_without_a_forge_url_the_change_is_unknown() -> None:
    config, _ = _config()
    diffed = AsyncMock(return_value=[JAVA])
    run_gate = AsyncMock(return_value=_gate("passed", "passed"))
    with (
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _e: None),
        patch("embry0.workflows.issue_to_pr.nodes._verify.run_gate", new=run_gate),
        patch("embry0.workflows.issue_to_pr.nodes._verify.sweep_sandbox", new=_swept()),
        patch("embry0.workflows.issue_to_pr.nodes._verify._forge_url", new=AsyncMock(return_value="")),
        patch("embry0.execution.workspace_diff.compute_changed_files_via_diff", new=diffed),
    ):
        cmd = await verify_node(_state([JAVA]), config)  # type: ignore[arg-type]
    diffed.assert_not_awaited()
    assert run_gate.await_args.kwargs["changed_files"] == [], "no diff from anywhere else: the change is unknown"
    assert cmd.update["verification"]["tier"]["tier"] == "frontend"


@pytest.mark.asyncio
async def test_the_forge_url_names_the_repository_the_sandbox_cloned() -> None:
    """The clone takes the job context's repository first (``job_repository``); so does the forge URL."""
    from embry0.workflows.issue_to_pr.nodes._common import job_repository

    config, _ = _config()
    state = _state([JAVA], context={"type": "git", "repo": "acme/backend-fork"})
    assert job_repository(state) == "acme/backend-fork" and job_repository(_state([JAVA])) == "acme/backend"
    _, run_gate = await _run(state, config, _gate("passed", "passed"))
    assert run_gate.await_args.kwargs.get("forge_url") == "https://github.com/acme/backend-fork.git", (
        f"the gate reads the repository the sandbox cloned: {run_gate.await_args.kwargs.get('forge_url')}"
    )
