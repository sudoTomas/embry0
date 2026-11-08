"""Each run tests a fresh tree of its commit: the gate's own scripts, run against real repositories.

``run_gate`` drives a sandbox that runs every exec on this host: embry0's git
steps behind the prelude (pointed at a config the test wrote, as
``tests/fixtures/sandbox_git_local.py`` does), the runs under ``sh``, the log
reads with ``tail`` and the cleanup, exactly as rendered. Only the sandbox's
two directories move, ``/workspace`` to the test's repository and
``/tmp/embry0-verify`` into ``tmp_path``, and the file transport is allowed so
the test's bare repository can stand in for the forge. The process sweep is
replaced by a clean result: its script only ever runs against a process
table a test writes (``test_sweep.py``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import zlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from embry0.execution import sandbox_git
from embry0.workflows.verification import runner
from embry0.workflows.verification.config import VerificationConfig
from tests.fixtures.sandbox_git_local import IDENTITY, local_config, localize

pytestmark = pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("git", "bash", "sh", "timeout", "tail")),
    reason="needs git, bash, sh, timeout and tail",
)

BRANCH = "feature/x"
#: A well-formed commit the repository does not have.
MISSING_SHA = "1" * 40

#: Each run writes where it ran, the commit there and how many paths git
#: reports as not exactly that commit; then it fails if it can see anything
#: the commit does not carry.
CLEAN_COMMAND = (
    'printf "%s %s %s\\n" "$PWD" "$(git rev-parse HEAD)" "$(git status --porcelain --ignored | wc -l)"'
    ' >> "$EMBRY0_TEST_RECORD"; '
    "ls -A; cat app.txt; "
    'test ! -e stray.txt && test ! -e build && test "$(cat app.txt)" != uncommitted'
)
#: Fails, naming one test, when the tree holds broken.txt with "boom" in it.
BROKEN_COMMAND = (
    'if [ -e broken.txt ] && grep -q boom broken.txt; then echo "FAILED t.py::test_broken - boom"; exit 1; fi; exit 0'
)


@pytest.fixture(autouse=True)
def _clean_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def clean(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(clean=True, describe=lambda: "the sandbox was cleared")

    monkeypatch.setattr(runner, "sweep_sandbox", clean)

    async def no_pause(_seconds: float) -> None:
        return None

    # The forge here is local: a retry needs no pause.
    monkeypatch.setattr(sandbox_git, "_pause", no_pause, raising=False)


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "advice.graftFileDeprecated=false",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def _commit(ws: Path, message: str, files: dict[str, str]) -> str:
    for name, text in files.items():
        (ws / name).parent.mkdir(parents=True, exist_ok=True)
        (ws / name).write_text(text)
    _git(ws, "add", "-A")
    _git(ws, "commit", "--quiet", "-m", message)
    return _git(ws, "rev-parse", "HEAD")


def _job_repository(tmp: Path, *head_commits: dict[str, str]) -> SimpleNamespace:
    """A forge (a bare repository) and a job's workspace cloned from it: main at the base commit, the job's
    branch one commit per ``head_commits`` on, pushed."""
    forge = tmp / "forge.git"
    _git(tmp, "init", "--quiet", "--bare", "--initial-branch=main", str(forge))
    ws = tmp / "workspace"
    ws.mkdir()
    _git(ws, "init", "--quiet", "--initial-branch=main")
    _git(ws, "remote", "add", "origin", f"file://{forge}")
    base = _commit(ws, "base", {"app.txt": "base\n", ".gitignore": "build/\n"})
    _git(ws, "push", "--quiet", "origin", "main")
    _git(ws, "fetch", "--quiet", "origin")
    _git(ws, "checkout", "--quiet", "-b", BRANCH)
    shas = [_commit(ws, f"head {n}", files) for n, files in enumerate(head_commits or ({"app.txt": "head\n"},))]
    _git(ws, "push", "--quiet", "origin", BRANCH)
    return SimpleNamespace(ws=ws, forge=forge, base=base, head=shas[-1], shas=shas)


def _dirty(ws: Path) -> None:
    (ws / "app.txt").write_text("uncommitted\n")
    (ws / "stray.txt").write_text("not in the commit\n")
    (ws / "build").mkdir()
    (ws / "build" / "out.txt").write_text("left by an earlier build\n")


class _HostSandbox:
    """The dev sandbox of ``repo``'s job, on this host. ``head_printed`` stands in for the head the prepare step and the
    forge name. ``forge_url`` is the job's repository on the forge, as the orchestrator knows it."""

    def __init__(
        self, tmp: Path, repo: SimpleNamespace, *, head_printed: str | None = None, config_text: str | None = None
    ) -> None:
        self.ws = repo.ws
        self.forge_url = f"file://{repo.forge}"
        self.scratch = tmp / "scratch"
        self.home = tmp / "home"
        self.home.mkdir(exist_ok=True)
        self.record = tmp / "record.txt"
        self.config = local_config(tmp) if config_text is None else local_config(tmp, config_text)
        self.head_printed = head_printed
        self.runs: list[str] = []

    def move(self, text: str) -> str:
        return text.replace(runner.SCRATCH_DIR, str(self.scratch)).replace(sandbox_git.WORKSPACE, str(self.ws))

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        return {"PATH": os.environ["PATH"], "HOME": str(self.home), **(extra or {})}

    def build_exec_cmd(self, container: str, command: list[str], workdir: Any = None, env: Any = None, **_: Any):
        return {"command": list(command), "env": dict(env or {})}

    async def run_cmd(self, cmd: dict[str, Any], timeout: int = 60, *, max_bytes: Any = None, **_: Any) -> str:
        argv = cmd["command"]
        if argv[:2] == ["bash", "-c"]:
            argv = localize(argv, self.config)
            # The forge here is a local bare repository, reached over file://.
            script = argv[2].replace("GIT_ALLOW_PROTOCOL=http:https", "GIT_ALLOW_PROTOCOL=http:https:file")
            argv = [argv[0], argv[1], self.move(script), *argv[3:]]
        else:
            argv = [self.move(word) for word in argv]
        done = subprocess.run(
            argv, env=self.env(), capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL
        )
        if done.returncode != 0:
            raise RuntimeError(f"Docker command failed: {done.stderr.strip() or f'exit code {done.returncode}'}")
        out = done.stdout
        if self.head_printed:
            lines = []
            for line in out.splitlines():
                if line.startswith("EMBRY0_VERIFY_HEAD="):
                    line = f"EMBRY0_VERIFY_HEAD={self.head_printed}"
                elif line.endswith(f"\trefs/heads/{BRANCH}"):
                    line = f"{self.head_printed}\trefs/heads/{BRANCH}"
                lines.append(line)
            out = "\n".join(lines)
        return out.strip()

    async def run_cmd_status(self, cmd: dict[str, Any], timeout: int = 60, *, max_bytes: Any = None):
        command, env = cmd["command"], cmd["env"]
        is_run = "EMBRY0_VERIFY_CMD" in env
        assert is_run or command[0] == "tail", f"unexpected exec: {command[:2]}"
        moved = {k: self.move(v) for k, v in env.items()}
        if is_run:
            self.runs.append(moved["EMBRY0_VERIFY_DIR"])
            moved["EMBRY0_TEST_RECORD"] = str(self.record)
        done = subprocess.run(
            [self.move(word) for word in command],
            env=self.env(moved),
            capture_output=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
        return done.returncode, done.stdout.decode(errors="replace"), done.stderr.decode(errors="replace")

    def recorded(self) -> list[tuple[str, str, int]]:
        if not self.record.exists():
            return []
        return [(d, sha, int(n)) for d, sha, n in (line.split() for line in self.record.read_text().splitlines())]


async def _gate(sandbox: _HostSandbox, command: str = CLEAN_COMMAND, *, head_reruns: int = 1) -> Any:
    config = VerificationConfig.model_validate(
        {"test_targets": [{"name": "unit", "command": command}], "head_reruns": head_reruns}
    )
    return await runner.run_gate(
        docker=sandbox,
        container_id="local",
        config=config,
        changed_files=["app.txt"],
        base_ref="main",
        branch=BRANCH,
        forge_url=sandbox.forge_url,
    )


def _worktrees(ws: Path) -> list[str]:
    listing = _git(ws, "worktree", "list", "--porcelain")
    return [line.removeprefix("worktree ") for line in listing.splitlines() if line.startswith("worktree ")]


def _runs(report: Any) -> list[tuple[str, str]]:
    return [(r.side, r.outcome) for r in report.runs]


async def test_the_head_run_tests_the_commit_not_the_working_copy(tmp_path: Path) -> None:
    repo = _job_repository(tmp_path)
    sandbox = _HostSandbox(tmp_path, repo)
    head_tree, base_tree = str(sandbox.scratch / "head"), str(sandbox.scratch / "base")
    _dirty(repo.ws)

    report = await _gate(sandbox)

    assert _runs(report) == [("head", "passed"), ("base", "passed")], (
        f"the head run saw files the commit does not carry: {_runs(report)}\n{report.runs[0].output_tail}"
    )
    assert sandbox.recorded() == [(head_tree, repo.head, 0), (base_tree, repo.base, 0)], (
        f"each run is in a clean checkout of its own commit (tree, commit, paths that differ): {sandbox.recorded()}"
    )
    assert (report.head_sha, report.base_sha) == (repo.head, repo.base)
    assert _worktrees(repo.ws) == [str(repo.ws)], f"the gate registers no worktree: {_worktrees(repo.ws)}"
    assert not sandbox.scratch.exists(), "the scratch directory is removed afterwards"
    assert _git(repo.ws, "rev-parse", "HEAD") == repo.head
    assert (repo.ws / "app.txt").read_text() == "uncommitted\n" and (repo.ws / "stray.txt").exists(), (
        "the working copy is left as it was"
    )
    assert (repo.ws / "build" / "out.txt").exists()


# ── what the tested tree holds is the commit's content, whatever the workspace's .git says ──


def _replace_head_commit(repo: SimpleNamespace) -> None:
    """A replacement for the head commit: the base's tree, so the head would read as the base."""
    tree = _git(repo.ws, "rev-parse", f"{repo.base}^{{tree}}")
    stand_in = _git(repo.ws, "commit-tree", tree, "-p", repo.base, "-m", "stand-in")
    _git(repo.ws, "replace", repo.head, stand_in)


def _replace_one_blob(repo: SimpleNamespace) -> None:
    broken = _git(repo.ws, "rev-parse", f"{repo.head}:broken.txt")
    empty = _git(repo.ws, "hash-object", "-w", "--stdin")
    _git(repo.ws, "replace", "-f", broken, empty)


def _sparse_in_config(repo: SimpleNamespace) -> None:
    _git(repo.ws, "config", "core.sparseCheckout", "true")
    (repo.ws / ".git" / "info").mkdir(exist_ok=True)
    (repo.ws / ".git" / "info" / "sparse-checkout").write_text("/*\n!/broken.txt\n")


def _sparse_in_worktree_config(repo: SimpleNamespace) -> None:
    _git(repo.ws, "config", "extensions.worktreeConfig", "true")
    _git(repo.ws, "config", "--worktree", "core.sparseCheckout", "true")
    (repo.ws / ".git" / "info").mkdir(exist_ok=True)
    (repo.ws / ".git" / "info" / "sparse-checkout").write_text("/*\n!/broken.txt\n")


def _rewrite_loose_object(repo: SimpleNamespace) -> None:
    """The loose object of the head's broken.txt rewritten in place with other content, under the same name."""
    broken = _git(repo.ws, "rev-parse", f"{repo.head}:broken.txt")
    path = repo.ws / ".git" / "objects" / broken[:2] / broken[2:]
    assert path.exists(), "the blob is a loose object"
    content = b"fine\n"
    path.chmod(0o644)
    path.write_bytes(zlib.compress(b"blob %d\0" % len(content) + content))


@pytest.mark.parametrize(
    ("change", "verdict"),
    [
        (_replace_head_commit, "new_failures"),
        (_replace_one_blob, "new_failures"),
        (_sparse_in_config, "new_failures"),
        (_sparse_in_worktree_config, "new_failures"),
        (_rewrite_loose_object, "error"),
    ],
    ids=[
        "replace-ref-on-the-commit",
        "replace-ref-on-a-blob",
        "sparse-checkout",
        "sparse-via-config-worktree",
        "rewritten-loose-object",
    ],
)
async def test_what_the_tested_tree_holds_is_the_commits_content(tmp_path: Path, change: Any, verdict: str) -> None:
    """The head adds a failing test. Whatever the workspace's .git says, the head run sees that test (the honest
    ``new_failures``), or its tree cannot be built (``error``): never a pass."""
    repo = _job_repository(tmp_path, {"broken.txt": "boom\n"})
    change(repo)
    report = await _gate(_HostSandbox(tmp_path, repo), BROKEN_COMMAND, head_reruns=0)
    assert report.verdict == verdict, (
        f"the tested tree must be the commit's content: {report.verdict} {_runs(report)} "
        f"{[r.output_tail[-200:] for r in report.runs]}"
    )
    if verdict == "new_failures":
        assert report.targets[0].new_failures == ["t.py::test_broken"], report.targets[0]
    else:
        assert report.runs[0].output_tail == "not run: the head commit could not be checked out in the sandbox"


async def test_the_base_is_the_merge_base_with_the_branch_the_forge_has(tmp_path: Path) -> None:
    """A remote-tracking ref in the workspace that points elsewhere does not choose the base."""
    repo = _job_repository(tmp_path, {"broken.txt": "boom\n"}, {"other.txt": "more\n"})
    # The workspace's own origin/main moved to the head's first commit, which already fails.
    _git(repo.ws, "update-ref", "refs/remotes/origin/main", repo.shas[0])
    report = await _gate(_HostSandbox(tmp_path, repo), BROKEN_COMMAND, head_reruns=0)
    assert report.base_sha == repo.base, f"the base is the fork point of the forge's main: {report.base_sha[:12]}"
    assert report.verdict == "new_failures", f"the head's failure is new against the real base: {report.verdict}"


async def test_a_base_branch_that_cannot_be_fetched_is_an_error(tmp_path: Path) -> None:
    """Without the forge's base branch the base would come from whatever the workspace's own ref says."""
    repo = _job_repository(tmp_path)
    shutil.move(repo.forge, tmp_path / "forge-gone.git")
    sandbox = _HostSandbox(tmp_path, repo)
    report = await _gate(sandbox)
    assert report.verdict == "error", f"a base that could not be fetched is not used: {report.verdict}"
    assert sandbox.runs == [], f"nothing runs: {sandbox.runs}"
    assert "the base branch could not be fetched" in report.note, report.note


async def test_a_head_the_branch_on_the_forge_does_not_carry_is_an_error(tmp_path: Path) -> None:
    repo = _job_repository(tmp_path)
    unpushed = _commit(repo.ws, "not pushed", {"later.txt": "later\n"})
    sandbox = _HostSandbox(tmp_path, repo)
    report = await _gate(sandbox)
    assert report.verdict == "error", f"a commit the pull request does not carry is not judged: {report.verdict}"
    assert report.runs == [] and sandbox.runs == [], f"nothing runs: {sandbox.runs}"
    assert report.head_sha == unpushed and f"({repo.head[:12]})" in report.note, report.note


async def test_a_head_that_cannot_be_checked_out_is_an_error_and_leaves_no_tree(tmp_path: Path) -> None:
    """The prepare step and the forge name a well-formed head the repository does not have."""
    repo = _job_repository(tmp_path)
    sandbox = _HostSandbox(tmp_path, repo, head_printed=MISSING_SHA)
    report = await _gate(sandbox)
    assert report.verdict == "error", f"a head that could not be checked out must not be judged: {_runs(report)}"
    assert sandbox.runs == [], f"nothing ran in place of the missing head: {sandbox.runs}"
    assert [r.output_tail for r in report.runs] == [
        "not run: the head commit could not be checked out in the sandbox"
    ], f"the head run is recorded as not run, with why: {report.runs}"
    assert report.head_sha == MISSING_SHA, "the error names the head it was asked to test"
    assert not sandbox.scratch.exists(), "the scratch directory is removed afterwards"


# ── every run starts from its commit, whatever an earlier run did ──


async def test_a_head_rerun_starts_from_a_fresh_tree(tmp_path: Path) -> None:
    """The first attempt leaves a file in its tree that would make the re-run pass."""
    repo = _job_repository(tmp_path, {"broken.txt": "boom\n"})
    command = (
        "if [ -e broken.txt ]; then [ -e .ran ] && exit 0; touch .ran; "
        'echo "FAILED t.py::test_broken - boom"; exit 1; fi; exit 0'
    )
    report = await _gate(_HostSandbox(tmp_path, repo), command, head_reruns=1)
    assert _runs(report) == [("head", "failed"), ("base", "passed"), ("head", "failed")], _runs(report)
    assert report.verdict == "new_failures", (
        f"the re-run tests the commit, not what the first run left: {report.verdict}"
    )


async def test_a_head_run_cannot_change_the_base_tree(tmp_path: Path) -> None:
    repo = _job_repository(tmp_path, {"broken.txt": "boom\n"})
    command = (
        'if [ "$(basename "$PWD")" = head ]; then mkdir -p ../base && printf boom > ../base/broken.txt; fi; '
        + BROKEN_COMMAND
    )
    report = await _gate(_HostSandbox(tmp_path, repo), command, head_reruns=0)
    assert _runs(report) == [("head", "failed"), ("base", "passed")], (
        f"the base run tests the base commit, not what the head run wrote: {_runs(report)}"
    )
    assert report.verdict == "new_failures"


# ── what an interrupted or untidy gate leaves does not stop the next one ──


async def test_a_read_only_directory_a_run_left_does_not_stop_the_next_gate(tmp_path: Path) -> None:
    """A module cache kept in the tree (Go makes its files and directories read-only)."""
    repo = _job_repository(tmp_path)
    command = "mkdir -p .gopath/pkg/mod/x && touch .gopath/pkg/mod/x/f && chmod -R a-w .gopath/pkg/mod; exit 0"
    first = _HostSandbox(tmp_path, repo)
    report = await _gate(first, command)
    assert report.verdict == "passed", _runs(report)
    assert not first.scratch.exists(), "a read-only directory in a tree does not stop the cleanup"
    (tmp_path / "second").mkdir()
    second = _HostSandbox(tmp_path / "second", repo)
    second.scratch = first.scratch
    report = await _gate(second, command)
    assert report.verdict == "passed", f"the next gate in the same sandbox runs: {_runs(report)}"


async def test_a_locked_worktree_left_at_the_tree_path_does_not_stop_the_gate(tmp_path: Path) -> None:
    """An interrupted checkout of an earlier version left a locked worktree registration where the head tree goes."""
    repo = _job_repository(tmp_path)
    sandbox = _HostSandbox(tmp_path, repo)
    stale = sandbox.scratch / "head"
    _git(repo.ws, "worktree", "add", "--quiet", "--detach", str(stale), repo.base)
    _git(repo.ws, "worktree", "lock", "--reason", "initializing", str(stale))
    (stale / "stray.txt").write_text("left by an earlier gate\n")
    report = await _gate(sandbox)
    assert _runs(report) == [("head", "passed"), ("base", "passed")], (
        f"a leftover registration does not stop the gate: {_runs(report)} {report.note!r}"
    )
    assert sandbox.recorded()[0][:2] == (str(stale), repo.head), sandbox.recorded()


async def test_a_symlink_at_the_scratch_path_is_removed_not_followed(tmp_path: Path) -> None:
    repo = _job_repository(tmp_path)
    sandbox = _HostSandbox(tmp_path, repo)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "head").mkdir(parents=True)
    kept = elsewhere / "kept.txt"
    kept.write_text("kept\n")
    kept.chmod(0o444)
    sandbox.scratch.symlink_to(elsewhere)
    report = await _gate(sandbox)
    assert report.verdict == "passed", _runs(report)
    assert not sandbox.scratch.exists() and not sandbox.scratch.is_symlink(), "the gate's own directory is gone"
    assert sorted(p.name for p in elsewhere.iterdir()) == ["head", "kept.txt"], "what the link pointed at is left alone"
    assert oct(kept.stat().st_mode & 0o777) == oct(0o444), "and its permissions are unchanged"


# ── the forge is read at the job's own URL, whatever the repository's config says ──


def _other_repository(tmp: Path, repo: SimpleNamespace, **refs: str) -> Path:
    """A second bare repository holding ``refs`` (branch name -> commit) from the workspace."""
    other = tmp / "other.git"
    _git(tmp, "init", "--quiet", "--bare", "--initial-branch=main", str(other))
    for name, sha in refs.items():
        _git(repo.ws, "push", "--quiet", "--force", f"file://{other}", f"{sha}:refs/heads/{name}")
    return other


async def test_the_branch_tip_is_read_from_the_forge_not_the_repositorys_remote(tmp_path: Path) -> None:
    """The workspace's origin points at another repository, which holds the unpushed local commit as the branch."""
    repo = _job_repository(tmp_path)
    unpushed = _commit(repo.ws, "not pushed", {"later.txt": "later\n"})
    other = _other_repository(tmp_path, repo, main=repo.base, **{BRANCH: unpushed})
    _git(repo.ws, "remote", "set-url", "origin", f"file://{other}")
    sandbox = _HostSandbox(tmp_path, repo)
    report = await _gate(sandbox)
    assert report.verdict == "error", f"the head is compared with the forge's tip, not the remote's: {report.verdict}"
    assert f"({repo.head[:12]})" in report.note, report.note
    assert sandbox.runs == []


async def test_the_base_is_fetched_from_the_forge_not_the_repositorys_remote(tmp_path: Path) -> None:
    """The workspace's origin points at another repository whose main is a commit that already fails."""
    repo = _job_repository(tmp_path, {"broken.txt": "boom\n"}, {"other.txt": "more\n"})
    other = _other_repository(tmp_path, repo, main=repo.shas[0], **{BRANCH: repo.head})
    _git(repo.ws, "remote", "set-url", "origin", f"file://{other}")
    report = await _gate(_HostSandbox(tmp_path, repo), BROKEN_COMMAND, head_reruns=0)
    assert report.base_sha == repo.base, f"the base is the fork point of the forge's main: {report.base_sha[:12]}"
    assert report.verdict == "new_failures", report.verdict


@pytest.mark.parametrize("rewrite", ["insteadOf", "pushInsteadOf"])
async def test_a_url_rewrite_in_the_repositorys_config_stops_the_gate(tmp_path: Path, rewrite: str) -> None:
    """A repository-local ``url.<base>.insteadOf`` would send the forge reads elsewhere: embry0's git refuses it."""
    repo = _job_repository(tmp_path)
    other = _other_repository(tmp_path, repo, main=repo.base, **{BRANCH: repo.base})
    _git(repo.ws, "config", f"url.file://{other}.{rewrite}", f"file://{repo.forge}")
    sandbox = _HostSandbox(tmp_path, repo)
    report = await _gate(sandbox)
    # The key is named (cut at the refusal's 100 characters: the test's path is long).
    assert report.verdict == "error" and f"url.file://{other}"[:60] in report.note, report.note
    assert "git config --unset-all" in report.note, f"the note says how to clear it: {report.note!r}"
    assert sandbox.runs == []


async def test_history_commands_work_in_the_tree(tmp_path: Path) -> None:
    """The tree's repository holds one commit, marked shallow: git log and git describe answer there."""
    repo = _job_repository(tmp_path)
    command = 'git log -1 --format=%H >> "$EMBRY0_TEST_RECORD" && git describe --always >> "$EMBRY0_TEST_RECORD"'
    sandbox = _HostSandbox(tmp_path, repo)
    report = await _gate(sandbox, command)
    assert _runs(report) == [("head", "passed"), ("base", "passed")], (
        f"git log and git describe fail in the tree: {[r.output_tail[-300:] for r in report.runs]}"
    )
    lines = sandbox.record.read_text().split()
    assert lines == [repo.head, repo.head[: len(lines[1])], repo.base, repo.base[: len(lines[3])]], lines


async def test_two_head_runs_in_a_row_each_get_a_fresh_tree(tmp_path: Path) -> None:
    """With ``on_head_failure`` and passing heads, two targets' head runs follow each other with no base run between."""
    repo = _job_repository(tmp_path)
    config = VerificationConfig.model_validate(
        {
            "base_run": "on_head_failure",
            "test_targets": [
                {"name": "first", "command": "touch .left-by-first"},
                {"name": "second", "command": "test ! -e .left-by-first"},
            ],
        }
    )
    sandbox = _HostSandbox(tmp_path, repo)
    report = await runner.run_gate(
        docker=sandbox,
        container_id="local",
        config=config,
        changed_files=["app.txt"],
        base_ref="main",
        branch=BRANCH,
        forge_url=sandbox.forge_url,
    )
    assert [(r.target, r.side, r.outcome) for r in report.runs] == [
        ("first", "head", "passed"),
        ("second", "head", "passed"),
    ], f"the second target's run starts from the commit, not from the first's tree: {report.runs}"


# ── the verify node's own reads (the base branch, the diff) go to the forge too ──

#: The forge URL the verify node computes for the job's repository (``clone_url``).
REPO = "acme/widgets"
CLONE_URL = f"https://github.com/{REPO}.git"


def _embry0_config(forge: Path) -> str:
    """embry0's own git config in the sandbox: the clone URL goes to the forge, as production sends it to the
    git-proxy."""
    return f'{IDENTITY}[url "file://{forge}"]\n\tinsteadOf = {CLONE_URL}\n'


async def _verify(sandbox: _HostSandbox, state: dict[str, Any], targets: list[dict[str, Any]]) -> Any:
    from unittest.mock import AsyncMock, MagicMock, patch

    from embry0.workflows.issue_to_pr.nodes._verify import verify_node

    prefs = MagicMock()
    prefs.get = AsyncMock(return_value={"verification": {"test_targets": targets, "head_reruns": 0}})
    stored = MagicMock()
    stored.upsert = AsyncMock()
    config = {
        "configurable": {
            "docker": sandbox,
            "repo_preferences_repo": prefs,
            "job_verifications_repo": stored,
            "embry0_config": SimpleNamespace(verification_mode="on", gitlab_url=""),
        }
    }

    async def clean(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(clean=True, describe=lambda: "the sandbox was cleared")

    with (
        patch("embry0.workflows.issue_to_pr.nodes.get_stream_writer", return_value=lambda _e: None),
        patch("embry0.workflows.issue_to_pr.nodes._verify.sweep_sandbox", new=clean),
    ):
        await verify_node(state, config)  # type: ignore[arg-type]
    return stored.upsert.await_args.kwargs["result"]


def _job_state(**extra: Any) -> dict[str, Any]:
    return {
        "job_id": "job-1",
        "repo": REPO,
        "sandbox_container_id": "local",
        "base_branch": "main",
        "base_branch_source": "preference",
        "branch_name": BRANCH,
        "route_plan": [{"agent_type": "developer", "node_id": "developer", "config": {}}],
        **extra,
    }


TIERED_TARGETS = [
    {"name": "backend", "paths": ["backend/**"], "command": "true"},
    {"name": "web", "paths": ["frontend/**"], "command": "true"},
]


async def test_the_change_is_diffed_against_the_forges_base_not_the_repositorys_remote(tmp_path: Path) -> None:
    """The workspace's origin names another repository whose main already holds the branch's frontend commit:
    against it the change would look backend-only."""
    repo = _job_repository(tmp_path, {"frontend/a.tsx": "x\n"}, {"backend/x.py": "y\n"})
    other = _other_repository(tmp_path, repo, main=repo.shas[0], **{BRANCH: repo.head})
    _git(repo.ws, "remote", "set-url", "origin", f"file://{other}")
    sandbox = _HostSandbox(tmp_path, repo, config_text=_embry0_config(repo.forge))
    result = await _verify(sandbox, _job_state(), TIERED_TARGETS)
    assert result["tier"]["tier"] == "frontend", f"the change's files come from the forge's base: {result['tier']}"
    assert sorted(result["tier"]["frontend"] + result["tier"]["backend"]) == ["backend/x.py", "frontend/a.tsx"]
    ran = sorted({run["target"] for run in result["gate"]["runs"]})
    assert ran == ["backend", "web"], f"every target the change touches runs: {ran}"
    assert result["gate"]["verdict"] == "passed", result["gate"]


async def test_an_unchosen_base_branch_is_the_forges_default_not_the_workspaces(tmp_path: Path) -> None:
    """No base was chosen for the job: the forge's default branch is the base, not the workspace's origin/HEAD
    or the default of the repository its origin names."""
    from embry0.services.base_branch import FALLBACK_SOURCE

    repo = _job_repository(tmp_path, {"backend/x.py": "y\n"})
    other = _other_repository(tmp_path, repo, dev=repo.base, **{BRANCH: repo.head})
    _git(other, "symbolic-ref", "HEAD", "refs/heads/dev")
    _git(repo.ws, "remote", "set-url", "origin", f"file://{other}")
    _git(repo.ws, "update-ref", "refs/remotes/origin/dev", repo.base)
    _git(repo.ws, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/dev")
    sandbox = _HostSandbox(tmp_path, repo, config_text=_embry0_config(repo.forge))
    result = await _verify(sandbox, _job_state(base_branch_source=FALLBACK_SOURCE), TIERED_TARGETS)
    assert result["gate"]["base_ref"] == "main", f"the base is the forge's default branch: {result['gate']['base_ref']}"
    assert result["gate"]["verdict"] == "passed", result["gate"]


async def test_a_url_rewrite_in_the_repositorys_config_leaves_the_change_unknown_and_the_gate_errored(
    tmp_path: Path,
) -> None:
    repo = _job_repository(tmp_path, {"backend/x.py": "y\n"})
    other = _other_repository(tmp_path, repo, main=repo.head, **{BRANCH: repo.head})
    _git(repo.ws, "config", f"url.file://{other}.insteadOf", CLONE_URL)
    sandbox = _HostSandbox(tmp_path, repo, config_text=_embry0_config(repo.forge))
    result = await _verify(sandbox, _job_state(), TIERED_TARGETS)
    assert result["tier"]["tier"] == "frontend", (
        f"a refused diff is an unknown change, the strictest tier: {result['tier']}"
    )
    # The key is named (cut at the refusal's 100 characters: the test's path is long).
    note = result["gate"]["note"]
    assert result["gate"]["verdict"] == "error" and f"url.file://{other}"[:60] in note, note
    assert "git config --unset-all" in note, note
