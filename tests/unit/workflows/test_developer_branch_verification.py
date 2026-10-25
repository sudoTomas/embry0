"""developer_node verifies the branch was pushed before routing to review."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_state(**over):
    base = {
        "job_id": "J",
        "issue_id": "iss-abc12345",
        "repo": "o/r",
        "task": "test task",
        "sandbox_container_id": "C",
        "agent_outputs": [],
        "errors": [],
        "pipeline_config": {"agent_skills": {"developer": []}, "budget_usd": 50.0},
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_developer_routes_to_review_when_branch_pushed():
    """ls-remote returns non-empty (branch present on origin) → goto=review."""
    from embry0.workflows.issue_to_pr.nodes import developer_node

    docker = MagicMock()
    # The branch is free when the developer starts (#478 probes it once) and on
    # origin after its push. Realistic ls-remote output: "<sha>\trefs/heads/<branch>".
    docker.run_cmd = AsyncMock(side_effect=["", "abc123def\trefs/heads/embry0/iss-abc12345-test-task"])
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])

    fake_result = {
        "agent_outputs": [
            {
                "agent_type": "developer",
                "is_error": False,
                "output": '{"pr_url": "https://github.com/o/r/pull/9", "branch": "embry0/iss-abc12345-test-task"}',
                "messages": [{"role": "assistant", "content": "ok"}],
                "mode": "anthropic_api",
            }
        ],
        "events": [],
        "total_cost_usd": 0.05,
    }

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=fake_result)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = await developer_node(
            _make_state(),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )

    assert cmd.goto == "review"
    # The verification call ran
    docker.run_cmd.assert_awaited()
    last_call = docker.run_cmd.await_args[0][0]
    assert "ls-remote" in last_call
    assert "--heads" in last_call


@pytest.mark.asyncio
async def test_developer_routes_to_max_retries_when_branch_not_pushed():
    """ls-remote returns empty → branch missing from remote → goto=max_retries
    with DEV_BRANCH_NOT_PUSHED error_code."""
    from embry0.safety.error_codes import ErrorCode
    from embry0.workflows.issue_to_pr.nodes import developer_node

    docker = MagicMock()
    docker.run_cmd = AsyncMock(return_value="")  # empty stdout = no such branch
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])

    fake_result = {
        "agent_outputs": [
            {
                "agent_type": "developer",
                "is_error": False,
                "output": "I made the changes locally",  # no PR URL — agent forgot to push
                "messages": [{"role": "assistant", "content": "done"}],
                "mode": "anthropic_api",
            }
        ],
        "events": [],
        "total_cost_usd": 0.05,
    }

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=fake_result)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = await developer_node(
            _make_state(),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )

    assert cmd.goto == "max_retries"
    assert cmd.update["error_code"] == ErrorCode.DEV_BRANCH_NOT_PUSHED.value
    assert any("embry0/iss-abc12345-test-task" in e for e in cmd.update["errors"])
    assert any("cannot clone what was never pushed" in e for e in cmd.update["errors"])
    assert cmd.update["current_stage"] == "dev_branch_missing"


@pytest.mark.asyncio
async def test_developer_skips_verification_when_no_docker():
    """No docker in config → skip verification, default to review (don't block on infra absence)."""
    from embry0.workflows.issue_to_pr.nodes import developer_node

    fake_result = {
        "agent_outputs": [
            {
                "agent_type": "developer",
                "is_error": False,
                "output": "{}",
                "messages": [],
                "mode": "anthropic_api",
            }
        ],
        "events": [],
        "total_cost_usd": 0.05,
    }

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=fake_result)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = await developer_node(
            _make_state(),
            {"configurable": {"agent_runner": object(), "credentials": {}}},  # no docker
        )

    assert cmd.goto == "review"


@pytest.mark.asyncio
async def test_developer_skips_verification_when_docker_call_raises():
    """ls-remote raises (network glitch) → benefit of doubt, default to review."""
    from embry0.workflows.issue_to_pr.nodes import developer_node

    docker = MagicMock()
    docker.run_cmd = AsyncMock(side_effect=RuntimeError("network glitch"))
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])

    fake_result = {
        "agent_outputs": [
            {"agent_type": "developer", "is_error": False, "output": "{}", "messages": [], "mode": "anthropic_api"}
        ],
        "events": [],
        "total_cost_usd": 0.05,
    }

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=fake_result)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = await developer_node(
            _make_state(),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )

    assert cmd.goto == "review"


@pytest.mark.asyncio
async def test_developer_rescue_pushes_branch_the_agent_left_local():
    """Agent committed but never pushed → orchestrator pushes → goto=review.

    A turn-cap death or a run spent retrying a policy-blocked tool can end the
    session with the work committed and unpushed. Pushing is mechanical, so the
    orchestrator does it rather than stranding a finished branch behind a human
    decision whose only sensible answer is "retry".
    """
    from embry0.workflows.issue_to_pr.nodes import developer_node

    calls: list[list[str]] = []

    async def fake_run_cmd(cmd, timeout=None):  # noqa: ARG001
        calls.append(cmd)
        joined = " ".join(cmd)
        if "rev-list" in joined:
            return "3\n"  # three local commits waiting
        if "push" in joined:
            return ""
        if "ls-remote" in joined:
            # absent on the first probe, present after the rescue push
            return (
                ""
                if not any("push" in " ".join(c) for c in calls)
                else ("abc123\trefs/heads/embry0/iss-abc12345-test-task")
            )
        return ""

    docker = MagicMock()
    docker.run_cmd = AsyncMock(side_effect=fake_run_cmd)
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])

    fake_result = {
        "agent_outputs": [
            {
                "agent_type": "developer",
                "is_error": True,
                "error_message": "Reached maximum number of turns (70)",
                "output": "committed the work",
                "messages": [{"role": "assistant", "content": "done"}],
                "mode": "anthropic_api",
            }
        ],
        "events": [],
        "total_cost_usd": 0.05,
    }

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=fake_result)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = await developer_node(
            _make_state(),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )

    assert cmd.goto == "review"
    assert any("push" in " ".join(c) for c in calls), "orchestrator never attempted the push"


@pytest.mark.asyncio
async def test_developer_does_not_rescue_when_there_is_nothing_to_push():
    """No local commits → no rescue attempt → still goto=max_retries.

    An empty branch means the agent produced nothing; pausing is correct and
    pushing would be a no-op that hides a real failure.
    """
    from embry0.safety.error_codes import ErrorCode
    from embry0.workflows.issue_to_pr.nodes import developer_node

    calls: list[list[str]] = []

    async def fake_run_cmd(cmd, timeout=None):  # noqa: ARG001
        calls.append(cmd)
        if "rev-list" in " ".join(cmd):
            return "0\n"  # nothing to push
        return ""

    docker = MagicMock()
    docker.run_cmd = AsyncMock(side_effect=fake_run_cmd)
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])

    fake_result = {
        "agent_outputs": [
            {
                "agent_type": "developer",
                "is_error": False,
                "output": "nothing to do",
                "messages": [],
                "mode": "anthropic_api",
            }
        ],
        "events": [],
        "total_cost_usd": 0.01,
    }

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=fake_result)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = await developer_node(
            _make_state(),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )

    assert cmd.goto == "max_retries"
    assert cmd.update["error_code"] == ErrorCode.DEV_BRANCH_NOT_PUSHED.value
    assert not any("push" in " ".join(c) for c in calls), "rescue ran with nothing to push"


# ---------------------------------------------------------------------------
# #358 — a turn-cap death must not discard the run's work
#
# _rescue_push_branch only ever pushed a branch that already had commits. After
# a turn-cap death the work is typically UNCOMMITTED, so the rescue skipped it
# and the sandbox took everything. That is how the RAV-1466 canary lost a
# complete two-language change (22 edits, 3 new files, $2.17) three turns short
# of a commit.
# ---------------------------------------------------------------------------

_BRANCH = "embry0/iss-abc12345-test-task"


def _turn_cap_result(output: str = "mid-stride when the cap hit") -> dict:
    return {
        "agent_outputs": [
            {
                "agent_type": "developer",
                "is_error": True,
                "error_message": "Reached maximum number of turns (50)",
                "output": output,
                "messages": [{"role": "assistant", "content": "…"}],
                "mode": "anthropic_api",
            }
        ],
        "events": [],
        "total_cost_usd": 2.17,
    }


def _dirty_tree_docker() -> tuple[MagicMock, list[list[str]]]:
    """A worktree with uncommitted edits and NO commits until one is made."""
    calls: list[list[str]] = []
    state = {"committed": False, "pushed": False}

    async def fake_run_cmd(cmd, timeout=None):  # noqa: ARG001
        calls.append(cmd)
        joined = " ".join(cmd)
        if "status" in joined and "--porcelain" in joined:
            # Three edits and a new file, none of them committed.
            return " M src/a.py\n M src/b.py\n M src/c.py\n?? src/new.py\n"
        if "commit" in joined:
            state["committed"] = True
            return "[branch abc1234] wip"
        if "rev-list" in joined:
            # The whole point: nothing to push until the rescue commit exists.
            return "1\n" if state["committed"] else "0\n"
        if "push" in joined:
            state["pushed"] = True
            return ""
        if "ls-remote" in joined:
            return f"abc123\trefs/heads/{_BRANCH}" if state["pushed"] else ""
        return ""

    docker = MagicMock()
    docker.run_cmd = AsyncMock(side_effect=fake_run_cmd)
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])
    return docker, calls


@pytest.mark.asyncio
async def test_turn_cap_death_commits_and_pushes_the_uncommitted_work():
    """The core #358 fix: dirty worktree + turn cap → committed, pushed, preserved."""
    from embry0.safety.error_codes import ErrorCode
    from embry0.workflows.issue_to_pr.nodes import developer_node

    docker, calls = _dirty_tree_docker()

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=_turn_cap_result())),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = await developer_node(
            _make_state(),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )

    joined = [" ".join(c) for c in calls]
    assert any("add -A" in j for j in joined), "never staged the uncommitted work"
    assert any(" commit " in f" {j} " for j in joined), "never committed the uncommitted work"
    assert any("push" in j for j in joined), "committed but never pushed — work still lost"

    # Preserved, but explicitly NOT presented as a delivery.
    assert cmd.goto == "max_retries"
    assert cmd.update["current_stage"] == "dev_run_unfinished"
    assert cmd.update["error_code"] == ErrorCode.DEV_RUN_UNFINISHED.value
    assert cmd.update["error_code"] != ErrorCode.DEV_BRANCH_NOT_PUSHED.value
    assert any("UNFINISHED" in e for e in cmd.update["errors"])


@pytest.mark.asyncio
async def test_commit_rescue_is_gated_on_a_stopped_run():
    """A clean finish that left a dirty tree keeps the old behaviour.

    Only a run that STOPPED (its turn cap, any error, the job's budget) gets
    its work committed on its behalf (salvage, spec 2026-10-25). A clean
    ending with no commits still means "the agent produced nothing worth
    pushing", and pausing is correct — committing there would invent a delivery.
    """
    from embry0.safety.error_codes import ErrorCode
    from embry0.workflows.issue_to_pr.nodes import developer_node

    docker, calls = _dirty_tree_docker()
    clean_finish = {
        "agent_outputs": [
            {"agent_type": "developer", "is_error": False, "output": "nothing to do", "messages": []},
        ],
        "events": [],
        "total_cost_usd": 0.01,
    }

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=clean_finish)),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = await developer_node(
            _make_state(),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )

    joined = [" ".join(c) for c in calls]
    assert not any(" commit " in f" {j} " for j in joined), "committed on a clean ending"
    assert cmd.goto == "max_retries"
    assert cmd.update["error_code"] == ErrorCode.DEV_BRANCH_NOT_PUSHED.value


@pytest.mark.asyncio
async def test_turn_cap_with_committed_work_still_just_pushes_and_advances():
    """Regression guard: the pre-#358 path is untouched.

    Cap death with work already committed → pushed and the job moves forward,
    exactly as before. It does not become a dev_run_unfinished pause.
    """
    from embry0.workflows.issue_to_pr.nodes import developer_node

    calls: list[list[str]] = []
    pushed = {"yes": False}

    async def fake_run_cmd(cmd, timeout=None):  # noqa: ARG001
        calls.append(cmd)
        joined = " ".join(cmd)
        if "status" in joined and "--porcelain" in joined:
            return ""  # the agent committed everything
        if "rev-list" in joined:
            return "2\n"
        if "push" in joined:
            pushed["yes"] = True
            return ""
        if "ls-remote" in joined:
            return f"abc123\trefs/heads/{_BRANCH}" if pushed["yes"] else ""
        return ""

    docker = MagicMock()
    docker.run_cmd = AsyncMock(side_effect=fake_run_cmd)
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=_turn_cap_result())),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = await developer_node(
            _make_state(),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )

    joined = [" ".join(c) for c in calls]
    assert not any(" commit " in f" {j} " for j in joined), "committed when nothing was uncommitted"
    assert cmd.goto == "review"
    assert cmd.update.get("current_stage") != "dev_run_unfinished"


@pytest.mark.asyncio
async def test_unfinished_pause_names_the_branch_and_the_code_maps():
    """The person must be told the work survived, and where."""
    from embry0.safety.error_codes import ErrorCode, FailureCategory, category_for_code, classify_failure

    assert classify_failure(stage="dev_run_unfinished") == ErrorCode.DEV_RUN_UNFINISHED
    assert category_for_code(ErrorCode.DEV_RUN_UNFINISHED) == FailureCategory.AGENT
    # dev_branch_missing keeps its own code — the two must not collapse.
    assert classify_failure(stage="dev_branch_missing") == ErrorCode.DEV_BRANCH_NOT_PUSHED

    import embry0.workflows.issue_to_pr.nodes as nodes_module

    captured: dict = {}

    def _interrupt(summary):
        captured.update(summary)
        return "abandon"

    with (
        patch.object(nodes_module, "get_stream_writer", return_value=lambda _: None),
        patch.object(nodes_module, "interrupt", new=_interrupt),
    ):
        await nodes_module.max_retries_node(
            _make_state(current_stage="dev_run_unfinished", branch_name=_BRANCH, pr_url=""),
            config=None,
        )

    assert _BRANCH in captured["message"]
    assert "UNFINISHED" in captured["message"]
    assert "continue_retrying" in captured["options"]


# ---------------------------------------------------------------------------
# #402 — the "is there anything to push?" probe must not depend on origin/HEAD
#
# The probe was `git rev-list --count origin/HEAD..<branch>`. origin/HEAD is a
# symref plenty of sandbox clones never get (a `--branch <ref> --single-branch`
# clone does not set it — the reason resolve_base_branch exists). Where it is
# missing git exits 128 "ambiguous argument … unknown revision", so:
#   * the rescue was skipped for a reason that says nothing about the branch,
#     stranding a finished-but-unpushed branch behind a human decision, and
#   * the non-zero exit was logged at ERROR as docker_cmd_failed, so a handled
#     probe failure looked like an unhandled fatal error sitting next to
#     dev_branch_not_pushed.
# ---------------------------------------------------------------------------


class _NoOriginHeadDocker:
    """Fake exec for a clone whose origin/HEAD symref does not exist.

    Any command naming ``origin/HEAD`` fails the way real git does — a non-zero
    exit, which ``DockerClient.run_cmd`` turns into a RuntimeError *and* an
    ERROR-level ``docker_cmd_failed`` log line. Those attempts are recorded so
    a test can assert the orchestrator never made one.
    """

    def __init__(self, *, unpushed: str = "3", branch_exists: bool = True):
        self.unpushed = unpushed
        self.branch_exists = branch_exists
        self.calls: list[str] = []
        self.unknown_revision_attempts: list[str] = []

    def build_exec_cmd(self, container_id, cmd, **_kw):
        return ["docker", "exec", container_id, *cmd]

    async def run_cmd(self, cmd, timeout=None):  # noqa: ARG002
        joined = " ".join(cmd)
        self.calls.append(joined)
        if "origin/HEAD" in joined:
            self.unknown_revision_attempts.append(joined)
            raise RuntimeError(
                "Docker command failed: fatal: ambiguous argument "
                f"'origin/HEAD..{_BRANCH}': unknown revision or path not in the working tree."
            )
        if "rev-list" in joined:
            # The probe asks for the branch's own commits, naming no base ref,
            # so a clone without origin/HEAD can still answer it.
            if not self.branch_exists:
                return "__EMBRY0_NO_LOCAL_BRANCH__"
            return self.unpushed
        if "push" in joined:
            return ""
        if "ls-remote" in joined:
            pushed = any("push" in c for c in self.calls[:-1])
            return f"abc123\trefs/heads/{_BRANCH}" if pushed else ""
        return ""


def _turn_cap_result(output: str = "committed the work"):
    return {
        "agent_outputs": [
            {
                "agent_type": "developer",
                "is_error": True,
                "error_message": "Reached maximum number of turns (70)",
                "output": output,
                "messages": [{"role": "assistant", "content": "done"}],
                "mode": "anthropic_api",
            }
        ],
        "events": [],
        "total_cost_usd": 0.05,
    }


@pytest.mark.asyncio
async def test_rescue_push_works_when_the_clone_has_no_origin_head():
    """Committed work is rescued even where origin/HEAD does not exist."""
    from embry0.workflows.issue_to_pr.nodes import developer_node

    docker = _NoOriginHeadDocker(unpushed="3")

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=_turn_cap_result())),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = await developer_node(
            _make_state(),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )

    assert docker.unknown_revision_attempts == [], (
        "the orchestrator asked git about origin/HEAD, which this clone does not have — "
        f"git exits 128 and the failure is logged at ERROR: {docker.unknown_revision_attempts}"
    )
    assert any("push" in c for c in docker.calls), (
        "the rescue push never happened, so a finished branch is stranded behind a human decision"
    )
    assert cmd.goto == "review"


@pytest.mark.asyncio
async def test_branch_never_created_fails_cleanly_and_says_so():
    """No branch in the sandbox → one self-explaining failure, no git fatal."""
    from embry0.safety.error_codes import ErrorCode
    from embry0.workflows.issue_to_pr.nodes import developer_node

    docker = _NoOriginHeadDocker(branch_exists=False)

    with (
        patch(
            "embry0.workflows.issue_to_pr.nodes.run_agent_node",
            new=AsyncMock(return_value=_turn_cap_result(output="I could not work out what to change")),
        ),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = await developer_node(
            _make_state(),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )

    assert cmd.goto == "max_retries"
    assert cmd.update["error_code"] == ErrorCode.DEV_BRANCH_NOT_PUSHED.value
    assert docker.unknown_revision_attempts == [], (
        f"one root cause also produced an ERROR-level git failure: {docker.unknown_revision_attempts}"
    )
    assert not any("push" in c for c in docker.calls), "pushed a branch that does not exist"
    assert any("never created it" in e for e in cmd.update["errors"]), cmd.update["errors"]


def test_branch_missing_failure_says_why_not_just_that_it_is_missing():
    """The human-facing failure names the reason.

    "Push the branch manually" is wrong advice when there is no branch: it
    sends whoever is triaging to look for something that does not exist.
    """
    from embry0.workflows.issue_to_pr.nodes._developer import _branch_missing_explanation

    missing = _branch_missing_explanation(_BRANCH, {"probe": "no_local_branch"})
    assert _BRANCH in missing
    assert "never created it" in missing
    assert "push the branch manually" not in missing.lower()

    empty = _branch_missing_explanation(_BRANCH, {"probe": "counted", "unpushed_commits": 0})
    assert "no commits of its own" in empty

    unknown = _branch_missing_explanation(_BRANCH, {"probe": "failed"})
    assert "could not determine" in unknown

    push_failed = _branch_missing_explanation(
        _BRANCH,
        {"probe": "counted", "unpushed_commits": 3, "push_error": "permission denied"},
    )
    assert "3 unpushed commit(s)" in push_failed
    assert "permission denied" in push_failed
