"""developer_node verifies the branch was pushed before routing to review."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.fixtures.sandbox_git_local import MAIN_SHA, readable, reads_branch_tip, reads_head


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


#: The URL embry0 cloned ``_make_state()``'s repository from: where the push check reads the forge (#435).
_FORGE_URL = "https://github.com/o/r.git"


async def _no_pause(_seconds: float) -> None:
    return None


@pytest.mark.asyncio
async def test_developer_routes_to_review_when_branch_pushed():
    """ls-remote returns non-empty (branch present on origin) → goto=review."""
    from embry0.workflows.issue_to_pr.nodes import developer_node

    docker = MagicMock()
    # The branch is free when the developer starts (#478 probes it once) and on
    # the forge after its push, at the sandbox's own tip of it (#435). Realistic ls-remote
    # output: "<sha>\trefs/heads/<branch>".
    docker.run_cmd = AsyncMock(side_effect=["", "abc123def\n", "abc123def\trefs/heads/embry0/iss-abc12345-test-task"])
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd, **_kw: ["docker", "exec", cid, *cmd])

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
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd, **_kw: ["docker", "exec", cid, *cmd])

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
async def test_a_forge_read_that_keeps_failing_is_unknown_and_the_job_pauses(monkeypatch):
    """ls-remote fails on every try (#435) → unknown, not pushed → the job pauses and says why.

    It used to read as pushed, the benefit of the doubt, so the job went on to
    review and QA with work that may never have reached the forge.
    """
    from embry0.execution import sandbox_git
    from embry0.safety.error_codes import ErrorCode
    from embry0.workflows.issue_to_pr.nodes import developer_node

    monkeypatch.setattr(sandbox_git, "_pause", _no_pause, raising=False)
    forge_reads: list[str] = []

    async def fake_run_cmd(cmd, timeout=None, **_kwargs):  # noqa: ARG001
        joined = readable(cmd)
        if reads_branch_tip(cmd):
            return "abc123\n"
        if reads_head(cmd):
            return f"{MAIN_SHA}\n"
        if "ls-remote" in joined:
            forge_reads.append(joined)
            raise RuntimeError("Docker command failed: fatal: unable to access the forge: Could not resolve host")
        if "rev-list" in joined:
            return "0\n"  # every commit is on a remote-tracking ref: the agent did push, as far as the sandbox knows
        return ""

    docker = MagicMock()
    docker.run_cmd = AsyncMock(side_effect=fake_run_cmd)
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd, **_kw: ["docker", "exec", cid, *cmd])

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

    assert cmd.goto == "max_retries", (
        f"a push check that could not read the forge counted as pushed: the job went on to {cmd.goto!r}"
    )
    assert cmd.update["error_code"] == ErrorCode.DEV_BRANCH_NOT_PUSHED.value
    why = cmd.update["errors"][-1]
    assert "could not confirm" in why and "Could not resolve host" in why, f"the pause does not say why: {why}"
    at_forge = [r for r in forge_reads if _FORGE_URL in r]
    assert len(at_forge) == sandbox_git.FORGE_ATTEMPTS, (
        f"the forge read was not tried {sandbox_git.FORGE_ATTEMPTS} times at the forge's URL: {forge_reads}"
    )


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

    async def fake_run_cmd(cmd, timeout=None, **_kwargs):  # noqa: ARG001
        calls.append(cmd)
        joined = readable(cmd)
        if reads_branch_tip(cmd):
            return "abc123\n"
        if reads_head(cmd):
            return f"{MAIN_SHA}\n"
        if "rev-list" in joined:
            return "3\n"  # three local commits waiting
        if "push" in joined:
            return ""
        if "ls-remote" in joined:
            # absent on the first probe, present after the rescue push
            return (
                ""
                if not any("push" in readable(c) for c in calls)
                else ("abc123\trefs/heads/embry0/iss-abc12345-test-task")
            )
        return ""

    docker = MagicMock()
    docker.run_cmd = AsyncMock(side_effect=fake_run_cmd)
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd, **_kw: ["docker", "exec", cid, *cmd])

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
    assert any("push" in readable(c) for c in calls), "orchestrator never attempted the push"


@pytest.mark.asyncio
async def test_developer_does_not_rescue_when_there_is_nothing_to_push():
    """No local commits → no rescue attempt → still goto=max_retries.

    An empty branch means the agent produced nothing; pausing is correct and
    pushing would be a no-op that hides a real failure.
    """
    from embry0.safety.error_codes import ErrorCode
    from embry0.workflows.issue_to_pr.nodes import developer_node

    calls: list[list[str]] = []

    async def fake_run_cmd(cmd, timeout=None, **_kwargs):  # noqa: ARG001
        calls.append(cmd)
        if "rev-list" in readable(cmd):
            return "0\n"  # nothing to push
        return ""

    docker = MagicMock()
    docker.run_cmd = AsyncMock(side_effect=fake_run_cmd)
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd, **_kw: ["docker", "exec", cid, *cmd])

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
    assert not any("push" in readable(c) for c in calls), "rescue ran with nothing to push"


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

    async def fake_run_cmd(cmd, timeout=None, **_kwargs):  # noqa: ARG001
        calls.append(cmd)
        joined = readable(cmd)
        if reads_branch_tip(cmd):
            return "abc123\n"
        if reads_head(cmd):
            return f"{MAIN_SHA}\n"
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
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd, **_kw: ["docker", "exec", cid, *cmd])
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

    joined = [readable(c) for c in calls]
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

    joined = [readable(c) for c in calls]
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

    async def fake_run_cmd(cmd, timeout=None, **_kwargs):  # noqa: ARG001
        calls.append(cmd)
        joined = readable(cmd)
        if reads_branch_tip(cmd):
            return "abc123\n"
        if reads_head(cmd):
            return f"{MAIN_SHA}\n"
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
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd, **_kw: ["docker", "exec", cid, *cmd])

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=_turn_cap_result())),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        cmd = await developer_node(
            _make_state(),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )

    joined = [readable(c) for c in calls]
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

    async def run_cmd(self, cmd, timeout=None, **_kwargs):  # noqa: ARG002
        joined = readable(cmd)
        self.calls.append(joined)
        if reads_branch_tip(cmd):
            return "abc123\n"
        if reads_head(cmd):
            return f"{MAIN_SHA}\n"
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


# ---- #435: the forge's tip, read at the forge's URL, against the sandbox's own tip of the branch ---


class _ForgeDocker:
    """A sandbox whose branch is at ``branch_tip`` (None: it has no such branch; "": it cannot be read)
    and whose forge has it at ``forge_tip`` ("" for none).

    Its ``HEAD`` is another commit (:data:`MAIN_SHA`): the run ended on ``main``.
    ``forge`` answers each read of the forge in turn: a string is its output,
    an exception is raised. Every exec is recorded, as the plain git it runs.
    """

    def __init__(
        self, *, branch_tip: str | None = "abc123", forge_tip: str = "abc123", forge=None, push_lands: bool = True
    ):
        self.branch_tip = branch_tip
        self.forge_tip = forge_tip
        self.forge = list(forge) if forge is not None else None
        self.push_lands = push_lands
        self.calls: list[str] = []
        self.forge_reads: list[list[str]] = []

    def build_exec_cmd(self, container_id, cmd, **_kw):
        return ["docker", "exec", container_id, *cmd]

    async def run_cmd(self, cmd, timeout=None, **_kwargs):  # noqa: ARG002
        joined = readable(cmd)
        self.calls.append(joined)
        if reads_branch_tip(cmd):
            from embry0.workflows.issue_to_pr.nodes._developer import _PROBE_NO_BRANCH

            return f"{_PROBE_NO_BRANCH if self.branch_tip is None else self.branch_tip}\n"
        if reads_head(cmd):
            return f"{MAIN_SHA}\n"
        if "rev-list" in joined:
            return "2\n" if self.branch_tip is not None else "__EMBRY0_NO_LOCAL_BRANCH__\n"
        if "push" in joined:
            if self.push_lands and self.branch_tip:
                self.forge_tip = self.branch_tip
            return ""
        if "ls-remote" in joined:
            self.forge_reads.append([str(w) for w in cmd[-4:]])
            if self.forge:
                answer = self.forge.pop(0)
                if isinstance(answer, BaseException):
                    raise answer
                return answer
            return f"{self.forge_tip}\trefs/heads/{_BRANCH}\n" if self.forge_tip else ""
        return ""


async def _verify(docker, diagnostics=None, **kw):
    from embry0.workflows.issue_to_pr.nodes._developer import _verify_branch_pushed

    try:
        return await _verify_branch_pushed(
            docker=docker,
            container_id="C",
            branch=_BRANCH,
            diagnostics=diagnostics,
            **{"forge_url": _FORGE_URL, **kw},
        )
    except TypeError as exc:
        pytest.fail(f"the push check cannot be told the forge's URL or asked for its diagnostics: {exc}")


@pytest.mark.asyncio
async def test_the_push_check_reads_the_forge_at_the_url_embry0_cloned_from():
    """Not through ``origin``, a name the repository's own config defines (#435, #403)."""
    docker = _ForgeDocker()
    assert await _verify(docker) is True
    assert docker.forge_reads == [["ls-remote", "--heads", _FORGE_URL, _BRANCH]], (
        f"the push check did not read the forge at the URL embry0 cloned from: {docker.forge_reads}"
    )


@pytest.mark.asyncio
async def test_a_branch_the_forge_has_at_another_commit_is_not_pushed():
    """The branch exists, from an earlier run, but the sandbox's branch is at another commit (#435)."""
    diagnostics: dict = {}
    docker = _ForgeDocker(branch_tip="c0ffee2", forge_tip="abc123")
    assert await _verify(docker, diagnostics) is False, (
        "a branch the forge has at an earlier run's commit counted as this run's work pushed"
    )
    assert diagnostics == {"verify": "stale", "forge_tip": "abc123", "local_tip": "c0ffee2"}, (
        f"the push check did not name both tips: {diagnostics}"
    )


@pytest.mark.asyncio
async def test_a_branch_the_forge_does_not_have_is_not_pushed():
    diagnostics: dict = {}
    assert await _verify(_ForgeDocker(forge_tip=""), diagnostics) is False
    assert diagnostics == {"verify": "absent"}, f"the push check did not say the branch is absent: {diagnostics}"


@pytest.mark.asyncio
async def test_a_forge_read_that_fails_once_is_tried_again(monkeypatch):
    """A short outage of the forge is not a verdict: sandbox_git.run_from_forge tries again."""
    from embry0.execution import sandbox_git

    monkeypatch.setattr(sandbox_git, "_pause", _no_pause, raising=False)
    docker = _ForgeDocker(forge=[RuntimeError("Docker command failed: The requested URL returned error: 502")])
    assert await _verify(docker) is True, "a forge read that failed once was not tried again"
    assert len(docker.forge_reads) == 2, f"a forge read that failed once was not tried again: {docker.forge_reads}"


@pytest.mark.parametrize(
    ("sandbox", "forge_url", "why"),
    [
        pytest.param(
            {"forge": [RuntimeError("Docker command failed: 502")] * 3},
            _FORGE_URL,
            "Docker command failed: 502",
            id="the forge cannot be read",
        ),
        pytest.param({"branch_tip": ""}, _FORGE_URL, "the sandbox's branch could not be read", id="branch unreadable"),
        pytest.param({}, "", "the forge's URL could not be resolved", id="no forge URL"),
    ],
)
@pytest.mark.asyncio
async def test_a_push_check_that_cannot_know_answers_unknown(monkeypatch, sandbox, forge_url, why):
    """None, never True: callers that deliver treat it as not pushed (#435)."""
    from embry0.execution import sandbox_git

    monkeypatch.setattr(sandbox_git, "_pause", _no_pause, raising=False)
    diagnostics: dict = {}
    answer = await _verify(_ForgeDocker(**sandbox), diagnostics, forge_url=forge_url)
    assert answer is None, f"a push check that could not know answered {answer!r}"
    assert diagnostics == {"verify": "unknown", "verify_error": why}, diagnostics


@pytest.mark.asyncio
async def test_a_rescue_push_whose_landing_cannot_be_read_is_not_pushed(monkeypatch):
    """The rescue pushed, and the forge could not be read afterwards: not delivered on a guess (#435)."""
    from embry0.execution import sandbox_git
    from embry0.workflows.issue_to_pr.nodes._developer import _rescue_push_branch

    monkeypatch.setattr(sandbox_git, "_pause", _no_pause, raising=False)
    docker = _ForgeDocker(forge=[RuntimeError("Docker command failed: 502")] * 3)
    try:
        pushed = await _rescue_push_branch(docker=docker, container_id="C", branch=_BRANCH, forge_url=_FORGE_URL)
    except TypeError as exc:
        pytest.fail(f"the rescue push cannot be told the forge's URL: {exc}")
    assert any("push" in c for c in docker.calls), "the rescue never pushed"
    assert pushed is False, "a rescue push whose landing could not be read counted as pushed"


@pytest.mark.asyncio
async def test_a_push_that_does_not_land_on_an_existing_branch_pauses_and_says_so():
    """An earlier run's branch is on the forge; this run's push, and the rescue's, never moved it (#435)."""
    from embry0.safety.error_codes import ErrorCode
    from embry0.workflows.issue_to_pr.nodes import developer_node

    docker = _ForgeDocker(branch_tip="c0ffee2", forge_tip="abc123", push_lands=False)
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
            _make_state(branch_name=_BRANCH),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )

    assert cmd.goto == "max_retries", (
        f"a branch the forge has at an earlier run's commit counted as this run's push: the job went on to {cmd.goto!r}"
    )
    assert cmd.update["error_code"] == ErrorCode.DEV_BRANCH_NOT_PUSHED.value
    why = cmd.update["errors"][-1]
    assert "on the forge at abc123" in why and "c0ffee2" in why, f"the pause does not say which commit is where: {why}"
    assert "cannot confirm" in why and "did not reach" not in why and "never pushed" not in why, (
        f"the pause claims what comparing two commits cannot know: {why}"
    )


def _finished_run() -> dict:
    return {
        "agent_outputs": [
            {"agent_type": "developer", "is_error": False, "output": "{}", "messages": [], "mode": "anthropic_api"}
        ],
        "events": [],
        "total_cost_usd": 0.05,
    }


async def _develop(docker: _ForgeDocker):
    from embry0.workflows.issue_to_pr.nodes import developer_node

    with (
        patch("embry0.workflows.issue_to_pr.nodes.run_agent_node", new=AsyncMock(return_value=_finished_run())),
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _: None),
    ):
        return await developer_node(
            _make_state(branch_name=_BRANCH),
            {"configurable": {"agent_runner": object(), "credentials": {}, "docker": docker}},
        )


@pytest.mark.asyncio
async def test_a_pushed_branch_goes_on_when_the_run_ended_on_main():
    """The agent pushed its branch, then checked out main: the forge has the branch's tip, not HEAD (#435)."""
    docker = _ForgeDocker(branch_tip="b" * 40, forge_tip="b" * 40)
    cmd = await _develop(docker)
    assert cmd.goto == "review", (
        f"a branch pushed whole read as not pushed because HEAD was on main: {cmd.goto!r}, "
        f"{(cmd.update.get('errors') or [''])[-1][:300]}"
    )
    assert not any("push" in c.split() for c in docker.calls), "a branch already on the forge was pushed again"


@pytest.mark.asyncio
async def test_a_rescued_branch_goes_on_when_the_run_ended_on_main():
    """The branch was left local and the run ended on main: the rescue pushes the branch, and that counts (#435)."""
    docker = _ForgeDocker(branch_tip="b" * 40, forge_tip="")
    cmd = await _develop(docker)
    assert any("push" in c.split() for c in docker.calls), "the rescue never pushed"
    assert cmd.goto == "review", (
        f"the rescue pushed the branch and it still read as not pushed, HEAD being on main: {cmd.goto!r}, "
        f"{(cmd.update.get('errors') or [''])[-1][:300]}"
    )


@pytest.mark.asyncio
async def test_a_forge_branch_the_sandbox_does_not_have_is_not_this_runs_work():
    """The forge has the branch from an earlier run; this run never created it (#435)."""
    from embry0.safety.error_codes import ErrorCode

    diagnostics: dict = {}
    assert await _verify(_ForgeDocker(branch_tip=None), diagnostics) is False, (
        "a branch the sandbox does not have counted as this run's work pushed"
    )
    assert diagnostics == {"verify": "stale", "forge_tip": "abc123", "local_tip": None}, (
        f"the push check did not say the sandbox has no such branch: {diagnostics}"
    )
    cmd = await _develop(_ForgeDocker(branch_tip=None))
    assert cmd.goto == "max_retries" and cmd.update["error_code"] == ErrorCode.DEV_BRANCH_NOT_PUSHED.value
    why = cmd.update["errors"][-1]
    assert "on the forge at abc123" in why and "the sandbox has no local branch of that name" in why, (
        f"the pause does not say what it compared: {why}"
    )
    assert "not this run's work" not in why, f"the pause claims what the check cannot know: {why}"


async def _pause_of(state: dict) -> dict:
    """The summary ``max_retries_node`` pauses ``state`` with."""
    import embry0.workflows.issue_to_pr.nodes as nodes_module
    from embry0.workflows.issue_to_pr.nodes import max_retries_node

    captured: dict = {}

    def _interrupt(summary: dict) -> str:
        captured.update(summary)
        return "abandon"

    with (
        patch.object(nodes_module, "interrupt", _interrupt),
        patch.object(nodes_module, "get_stream_writer", return_value=lambda _event: None),
    ):
        await max_retries_node(state, config=None)
    return captured


@pytest.mark.asyncio
async def test_a_pause_on_an_unreadable_forge_says_so_where_a_person_reads_it(monkeypatch):
    """The forge could not be read: the pause and the card say embry0 could not confirm, not that nothing was pushed.

    The developer's explanation went into ``errors``, which no page reads; the pause's own message said
    "Developer finished without pushing its branch to origin", and the card "the developer stopped without
    pushing its branch, so its work was not saved" (#435, review).
    """
    from embry0.execution import sandbox_git
    from embry0.services.job_status_words import status_words

    monkeypatch.setattr(sandbox_git, "_pause", _no_pause, raising=False)
    docker = _ForgeDocker(forge=[""] + [RuntimeError("Docker command failed: unable to access the forge: 502")] * 3)
    cmd = await _develop(docker)
    assert cmd.update["current_stage"] == "dev_branch_missing", cmd.update.get("current_stage")
    summary = await _pause_of({**_make_state(branch_name=_BRANCH), **cmd.update})
    message = summary["message"]
    assert "could not confirm" in message and "502" in message, f"the pause does not say what it found: {message}"
    assert "without pushing" not in message and "never pushed" not in message, (
        f"the pause says the branch was not pushed: {message}"
    )
    headline = status_words({"status": "paused", "current_stage": "dev_branch_missing"}, {"summary": summary})[
        "headline"
    ]
    assert "could not confirm" in headline and "without pushing" not in headline and "not saved" not in headline, (
        f"the card's line claims more than the check knows: {headline}"
    )


@pytest.mark.asyncio
async def test_a_pause_keeps_the_runs_error_beside_the_explanation():
    """The developer's own error and blocked tools still reach the pause, after the push check's explanation."""
    from embry0.workflows.issue_to_pr.nodes._developer import _UNCONFIRMED_TAIL

    why = "embry0 could not confirm that branch 'b' is on the forge with this run's work (502)." + _UNCONFIRMED_TAIL
    state = {
        "current_stage": "dev_branch_missing",
        "retry_count": 0,
        "pr_url": "",
        "errors": ["developer: Reached maximum number of turns (70)", why],
        "agent_outputs": [
            {
                "agent_type": "developer",
                "is_error": True,
                "error_message": "Reached maximum number of turns (70)",
                "policy_denials": {"Blocked by safety policy: docker": 3},
            }
        ],
    }
    message = (await _pause_of(state))["message"]
    assert message.startswith(why), f"the pause does not lead with the push check's explanation: {message}"
    assert "Reached maximum number of turns (70)" in message and "3 tool call(s) were blocked" in message, message
