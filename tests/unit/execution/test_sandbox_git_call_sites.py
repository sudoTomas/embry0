"""Every embry0 step that runs git in a sandbox runs it behind embry0's git prelude.

Each test drives one real step with a docker that records what it is asked to
run, and checks every command that runs git: it must start with the prelude
(embry0's own config, the pins, the repository check), and a step that works
in an existing workspace must check that workspace first. A step that ran
plain git would read the configuration, hooks and drivers the agent can write.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

PRELUDE_START = "embry0_git_refuse() {"
_GIT = re.compile(r"(?:^|[\s;&|({'\"`$])(?:git|embry0_git|embry0_git_add)\s")


def _text(cmd: list[str]) -> str:
    try:
        from tests.fixtures.sandbox_git_local import readable
    except ImportError:  # a tree without embry0's git module: its commands are plain git
        return " ".join(map(str, cmd))
    return readable(cmd)


class Recorder:
    """A docker that records each exec and answers it with ``answer(text of the command)``."""

    def __init__(self, answer=lambda text: "") -> None:
        self.answer = answer
        self.cmds: list[list[str]] = []
        self.stdin: list[bytes | None] = []

    def _build_base_cmd(self) -> list[str]:
        return ["docker"]

    def build_exec_cmd(self, container: str, cmd: list[str], **kwargs: Any) -> list[str]:
        return ["docker", "exec", *(["-u", kwargs["user"]] if kwargs.get("user") else []), container, *cmd]

    async def run_cmd(self, cmd: list[str], timeout: int = 60, stdin_data: bytes | None = None, **_kw: Any) -> str:
        self.cmds.append(list(cmd))
        self.stdin.append(stdin_data)
        return self.answer(_text(cmd))

    async def run_cmd_status(self, cmd: list[str], timeout: int = 60, **_kw: Any) -> tuple[int, str, str]:
        """A command whose answer is its exit status: this one always exits 0, printing the answer."""
        self.cmds.append(list(cmd))
        self.stdin.append(None)
        return 0, self.answer(_text(cmd)), ""

    async def copy_bytes_into(self, container: str, data: bytes, path: str) -> None:
        return None

    def git_commands(self) -> list[list[str]]:
        """The execs that run git: an argv starting with git, or a shell that calls it."""
        found = []
        for cmd in self.cmds:
            words = [str(w) for w in cmd]
            if "git" in words[3:5] or any(_GIT.search(w) for w in words):
                found.append(cmd)
        return found


def _unprotected(docker: Recorder, *, repo_check: bool = True) -> list[str]:
    bad = []
    for cmd in docker.git_commands():
        shells = [str(w) for w in cmd if str(w).startswith(PRELUDE_START)]
        if not shells:
            bad.append(f"no prelude: {_text(cmd)[:200]}")
        elif repo_check and "\nembry0_git_repo /workspace\n" not in shells[0]:
            bad.append(f"no workspace check: {_text(cmd)[-200:]}")
    return bad


def _assert_protected(docker: Recorder, step: str, *, repo_check: bool = True) -> None:
    assert docker.git_commands(), f"{step} ran no git at all: the test drove nothing"
    bad = _unprotected(docker, repo_check=repo_check)
    assert not bad, f"{step} ran git on the agent's configuration, without embry0's prelude:\n" + "\n".join(bad)


# ---- the developer node ---------------------------------------------------------------


async def test_the_salvage_commit_runs_on_embry0s_git():
    from embry0.workflows.issue_to_pr.nodes._developer import _commit_unfinished_work

    docker = Recorder(lambda t: " M a.py\n" if "--porcelain" in t else ("feature/x\n" if "--abbrev-ref" in t else ""))
    assert await _commit_unfinished_work(
        docker=docker, container_id="c1", branch="feature/x", job_id="job-1", message="wip"
    ), "the salvage commit did not run"
    _assert_protected(docker, "the salvage commit")


async def test_the_rescue_push_runs_on_embry0s_git():
    from embry0.workflows.issue_to_pr.nodes._developer import _rescue_push_branch

    pushed: list[bool] = []

    def answer(t: str) -> str:
        if "rev-list" in t:
            return "2\n"
        if re.search(r"\bpush\b", t):
            pushed.append(True)
        if "ls-remote" in t and pushed:
            return "abc\trefs/heads/feature/x\n"
        return ""

    docker = Recorder(answer)
    assert await _rescue_push_branch(docker=docker, container_id="c1", branch="feature/x"), "nothing was pushed"
    _assert_protected(docker, "the rescue push")


async def test_the_branch_probes_run_on_embry0s_git():
    from embry0.workflows.issue_to_pr.nodes._developer import _branch_on_origin, _verify_branch_pushed

    docker = Recorder(lambda t: "abc\trefs/heads/feature/x\n" if "ls-remote" in t else "2026-11-01\tsubject\n")
    assert await _branch_on_origin(docker=docker, container_id="c1", branch="feature/x")
    assert await _verify_branch_pushed(docker=docker, container_id="c1", branch="feature/x")
    _assert_protected(docker, "the branch probes")


# ---- diffs ----------------------------------------------------------------------------


async def test_the_changed_file_diff_and_base_branch_run_on_embry0s_git():
    from embry0.execution.workspace_diff import compute_changed_files_via_diff, diff_line_counts, resolve_base_branch

    docker = Recorder(
        lambda t: "a.py\n" if "--name-only" in t else ("main\n" if "symbolic-ref" in t else "1\t2\ta.py\n")
    )
    assert await resolve_base_branch(docker=docker, container_id="c1") == "main"
    assert await compute_changed_files_via_diff(docker=docker, container_id="c1", base_branch="main") == ["a.py"]
    assert await diff_line_counts(docker=docker, container_id="c1", base_branch="main") is not None
    _assert_protected(docker, "the workspace diff")


# ---- deliveries -----------------------------------------------------------------------

_AREAS_REPORT = "## Areas file\n\n```yaml\nareas:\n  - name: api\n    paths: [api/]\n```\n"
_AUDIT_REPORT = (
    "Stale: 1\nFiles: 1\n\n## Pull\n\n```diff\n--- a/CLAUDE.md\n+++ b/CLAUDE.md\n@@ -1 +1 @@\n-old\n+new\n```\n"
)


def _delivery_state() -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "task": "map the areas of acme/widgets",
        "sandbox_container_id": "c1",
        "context": {"type": "git", "repo": "acme/widgets"},
    }


async def test_the_areas_delivery_runs_on_embry0s_git():
    from embry0.workflows.issue_to_pr.areas_delivery import deliver_areas_file

    docker = Recorder()
    out = await deliver_areas_file(_delivery_state(), {"configurable": {"docker": docker}}, _AREAS_REPORT)
    assert out, "the areas file was not delivered"
    _assert_protected(docker, "the areas delivery")


async def test_the_audit_delivery_runs_on_embry0s_git():
    from embry0.workflows.issue_to_pr.audit_delivery import deliver_audit_patch

    docker = Recorder()
    out = await deliver_audit_patch(_delivery_state(), {"configurable": {"docker": docker}}, _AUDIT_REPORT)
    assert out, "the audit patch was not delivered"
    _assert_protected(docker, "the audit delivery")


# ---- the verify gate ------------------------------------------------------------------


async def test_the_verify_gate_runs_on_embry0s_git():
    from embry0.workflows.verification.config import TestTarget, VerificationConfig
    from embry0.workflows.verification.runner import run_gate

    def answer(t: str) -> str:
        if "EMBRY0_VERIFY_HEAD" in t:
            return f"EMBRY0_VERIFY_HEAD={'a' * 40}\nEMBRY0_VERIFY_BASE={'b' * 40}\n"
        if "EMBRY0_SWEEP" in t:
            return "EMBRY0_SWEEP stopped=0 passes=0 survivors=0 unread=0 pids=\n"
        if " tail -c " in t:
            return "1 passed\n"
        return ""  # a run's exec prints nothing; its log is read back on its own

    docker = Recorder(answer)
    config = VerificationConfig(test_targets=[TestTarget(name="unit", command="true", paths=[])])
    await run_gate(docker=docker, container_id="c1", config=config, changed_files=["a.py"], base_ref="main")
    _assert_protected(docker, "the verify gate")


# ---- setup: the clone, QA prep and a review's checkout ---------------------------------

_TOKEN = "bearer-" + "0" * 40


async def test_the_init_clone_runs_on_embry0s_git():
    from embry0.workspace_init.base import InitContext
    from embry0.workspace_init.git import GitWorkspaceInitializer

    docker = Recorder()
    ctx = InitContext(
        job_id="job-1",
        context={"type": "git", "repo": "acme/widgets"},
        container_id="c1",
        sandbox_token=_TOKEN,
        docker=docker,
        git_proxy_url="http://git-proxy:9101",
    )
    await GitWorkspaceInitializer().initialize(ctx)
    _assert_protected(docker, "the init clone", repo_check=False)


@pytest.mark.parametrize("cached_volume", [None, "vol-1"])
async def test_qa_prep_runs_on_embry0s_git_and_checks_a_baked_workspace(cached_volume):
    from embry0.workflows.qa._subtask_prep import prep_qa_sandbox_clone

    docker = Recorder(lambda t: "abc123\n")

    class _Proxy:
        git_proxy_url = "http://git-proxy:9101"
        xai_proxy_url = ""

    await prep_qa_sandbox_clone(
        docker=docker,
        proxy_mgr=_Proxy(),
        container_id="c1",
        sandbox_token=_TOKEN,
        job_id="job-1",
        repo="acme/widgets",
        branch="main",
        is_dind=False,
        qa_net="",
        cached_volume=cached_volume,
    )
    _assert_protected(docker, "QA prep", repo_check=False)
    baked = [_text(c) for c in docker.cmds if "git clone" in _text(c)]
    for shell in baked:
        assert "embry0_git_repo /workspace" in shell, "a checkout another sandbox made is checked before git runs in it"


def test_a_reviews_checkout_script_calls_only_embry0s_git():
    from embry0.workflows.pr_review.checkout import build_checkout_script

    script = build_checkout_script(
        fetch_ref="refs/pull/7/head", head_sha="a" * 40, base_ref="main", previous_sha="b" * 40, runs_nothing=True
    )
    plain = [line for line in script.splitlines() if re.search(r"(?:^|[\s;&|($])git\s", line)]
    assert not plain, "the review checkout runs plain git, which reads the agent's configuration:\n" + "\n".join(plain)


async def test_no_setup_step_puts_the_bearer_on_a_docker_exec_command_line():
    """The per-sandbox bearer reaches the sandbox on stdin only, at init, QA prep and re-enroll."""
    from embry0.workflows.qa._subtask_prep import prep_qa_sandbox_clone
    from embry0.workspace_init.base import InitContext
    from embry0.workspace_init.git import GitWorkspaceInitializer

    docker = Recorder(lambda t: "abc123\n")
    ctx = InitContext(
        job_id="job-1",
        context={"type": "git", "repo": "acme/widgets"},
        container_id="c1",
        sandbox_token=_TOKEN,
        docker=docker,
        git_proxy_url="http://git-proxy:9101",
    )
    await GitWorkspaceInitializer().initialize(ctx)

    class _Proxy:
        git_proxy_url = "http://git-proxy:9101"
        xai_proxy_url = "http://xai-proxy:9106"

    await prep_qa_sandbox_clone(
        docker=docker,
        proxy_mgr=_Proxy(),
        container_id="c1",
        sandbox_token=_TOKEN,
        job_id="job-1",
        repo="acme/widgets",
        branch="main",
        is_dind=False,
        qa_net="",
    )
    on_argv = [_text(c)[:160] for c in docker.cmds if any(_TOKEN in str(w) for w in c)]
    assert not on_argv, f"the bearer was on a docker exec command line: {on_argv}"
    assert any(data and _TOKEN.encode() in data for data in docker.stdin), "the bearer never reached the sandbox"


# ---- a refusal says which keys, and how to clear them -------------------------------

_REFUSED = "Docker command failed: embry0-git-refused: keys filter.x.clean include.path"


class _Refusing(Recorder):
    async def run_cmd(self, cmd: list[str], timeout: int = 60, stdin_data: bytes | None = None, **_kw: Any) -> str:
        await super().run_cmd(cmd, timeout, stdin_data)
        if docker_runs_git(cmd):
            raise RuntimeError(_REFUSED)
        return ""


def docker_runs_git(cmd: list[str]) -> bool:
    return any(_GIT.search(str(w)) for w in cmd) or "git" in [str(w) for w in cmd[3:5]]


async def test_a_refused_verify_gate_says_which_keys_and_how_to_clear_them():
    from embry0.workflows.verification.config import TestTarget, VerificationConfig
    from embry0.workflows.verification.runner import run_gate

    config = VerificationConfig(test_targets=[TestTarget(name="unit", command="true", paths=[])])
    report = await run_gate(docker=_Refusing(), container_id="c1", config=config, changed_files=[], base_ref="main")
    assert report.verdict == "error"
    assert "filter.x.clean" in report.note and "include.path" in report.note, (
        f"the gate's note must name the keys that stopped it: {report.note!r}"
    )
    assert "git config --unset-all" in report.note, f"the gate's note must say how to clear them: {report.note!r}"


async def test_a_refused_delivery_logs_the_keys_and_how_to_clear_them():
    from structlog.testing import capture_logs

    from embry0.workflows.issue_to_pr.areas_delivery import deliver_areas_file

    with capture_logs() as logs:
        out = await deliver_areas_file(_delivery_state(), {"configurable": {"docker": _Refusing()}}, _AREAS_REPORT)
    assert out == {}
    (failed,) = [e for e in logs if e["event"] == "map_areas_pr_not_pushed"]
    assert "filter.x.clean" in failed["error"] and "git config --unset-all" in failed["error"], failed
    refused = [e for e in logs if e["event"] == "sandbox_git_refused"]
    assert refused and refused[0]["log_level"] == "warning", f"a refusal is logged at warning: {logs}"
    assert refused[0]["step"] == "areas_delivery" and refused[0]["job_id"] == "job-1"
    assert refused[0]["keys"] == ["filter.x.clean", "include.path"]


async def test_a_refused_rescue_explains_itself_in_the_jobs_failure():
    from embry0.workflows.issue_to_pr.nodes._developer import _branch_missing_explanation, _rescue_push_branch

    diagnostics: dict[str, Any] = {}
    assert not await _rescue_push_branch(
        docker=_Refusing(), container_id="c1", branch="feature/x", diagnostics=diagnostics
    )
    why = _branch_missing_explanation("feature/x", diagnostics)
    assert "filter.x.clean" in why and "git config --unset-all" in why, (
        f"the job's failure must name what stopped embry0's push: {why}"
    )
