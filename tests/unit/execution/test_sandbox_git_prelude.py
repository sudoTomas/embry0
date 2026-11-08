"""The sandbox_git prelude against real bash and git: what it refuses, and what it never lets run.

Each test builds a workspace in a temporary directory with git, plants what an
agent with file tools could write, and runs embry0's rendered script. A planted
program leaves a mark file when it runs; GIT_TRACE shows any git started in a
nested repository. The prelude's root-owner check takes the test's own uid,
since a test cannot create a root-owned file; production's uid 0 is pinned in
``test_sandbox_git.py``.
"""

from __future__ import annotations

import http.server
import os
import shutil
import stat
import subprocess
import threading
from pathlib import Path

import pytest

from embry0.execution import sandbox_git
from embry0.execution.sandbox_git import REFUSED_EXIT, Shell, git

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None, reason="needs git and bash"
)

g = git


class Box:
    """A workspace, a home and embry0's config, as a sandbox has them."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.home.mkdir()
        self.marks = root / "marks"
        self.marks.mkdir()
        self.config = root / "run" / "embry0" / "git" / "config"
        self.config.parent.mkdir(parents=True)
        for d in (self.config.parent, self.config.parent.parent):
            d.chmod(0o755)
        self.config.write_text('[user]\n\tname = "embry0"\n\temail = "embry0@example.com"\n')
        self.config.chmod(0o644)
        self.ws = root / "ws"
        self.ws.mkdir()
        self.plain("init", "-q", "-b", "main", ".")
        (self.ws / "a.txt").write_text("one\n")
        self.plain("add", "-A")
        self.plain("commit", "-q", "-m", "one")

    def env(self) -> dict[str, str]:
        return {"HOME": str(self.home), "PATH": os.environ["PATH"], "LANG": "C"}

    def plain(self, *args: str, cwd: Path | None = None) -> str:
        """git as the agent runs it, with no embry0 prelude."""
        env = {
            **self.env(),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "dev",
            "GIT_AUTHOR_EMAIL": "dev@example.com",
            "GIT_COMMITTER_NAME": "dev",
            "GIT_COMMITTER_EMAIL": "dev@example.com",
        }
        done = subprocess.run(
            ["git", *args],
            cwd=cwd or self.ws,
            env=env,
            capture_output=True,
            text=True,
            check=True,
            stdin=subprocess.DEVNULL,
        )
        return done.stdout

    def program(self, name: str, *, filter_: bool = False) -> str:
        """An executable that leaves the mark ``name`` when anything runs it; a filter passes its input on."""
        path = self.root / f"{name}.sh"
        path.write_text(f"#!/bin/sh\ntouch {self.marks / name}\n{'cat' if filter_ else 'exit 0'}\n")
        path.chmod(0o755)
        return str(path)

    def ran(self) -> list[str]:
        return sorted(p.name for p in self.marks.iterdir())

    def run(
        self, body: str, *, repo: bool = True, owner: int | None = None, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        argv = sandbox_git.script(
            body,
            repo=str(self.ws) if repo else None,
            config_path=str(self.config),
            owner_uid=os.getuid() if owner is None else owner,
        )
        return subprocess.run(
            argv,
            cwd=self.ws,
            env={**self.env(), **(env or {})},
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
        )


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


def _refused(result: subprocess.CompletedProcess[str], reason: str) -> None:
    assert result.returncode == REFUSED_EXIT, f"expected a refusal ({reason}), got {result.returncode}: {result.stderr}"
    assert f"embry0-git-refused: {reason}" in result.stderr, result.stderr


# ---- the prelude fails closed --------------------------------------------------------


def test_a_checked_step_runs(box):
    result = box.run(g("status", "--porcelain"))
    assert result.returncode == 0, result.stderr


def test_missing_embry0_config_refuses(box):
    box.config.unlink()
    _refused(box.run(g("status")), "config_missing")


def test_embry0_config_owned_by_someone_else_refuses(box):
    _refused(box.run(g("status"), owner=os.getuid() + 1), "config_not_root_owned")


@pytest.mark.parametrize("which", ["file", "dir", "parent"])
def test_embry0_config_writable_by_others_refuses(box, which):
    target = {"file": box.config, "dir": box.config.parent, "parent": box.config.parent.parent}[which]
    target.chmod(target.stat().st_mode | stat.S_IWOTH)
    _refused(box.run(g("status")), "config_writable")


def test_embry0_config_as_a_symlink_refuses(box):
    # The target passes the owner and mode checks (it is the real config), so
    # only the -L check can refuse it: it pins the symlink check in embry0_git_owned.
    real = box.root / "elsewhere"
    box.config.rename(real)
    box.config.symlink_to(real)
    _refused(box.run(g("status")), "config_not_root_owned")


def test_embry0_config_dir_as_a_symlink_refuses(box):
    # Same, one level up: the config directory is a symlink to a real,
    # owner-and-mode-clean directory, so only the -L check catches it.
    real_dir = box.root / "realgitdir"
    box.config.parent.rename(real_dir)
    box.config.parent.symlink_to(real_dir)
    _refused(box.run(g("status")), "config_not_root_owned")


def test_an_include_directive_is_refused_even_when_the_included_file_is_harmless(box):
    # include.path pulls in an unbounded, agent-writable file; the step is
    # refused on the directive itself, which is visible only because the config
    # is read with --no-includes. Pins --no-includes: without it, git would
    # process the include and list the harmless key instead.
    included = box.root / "extra.cfg"
    included.write_text("[core]\n\tautocrlf = true\n")
    box.plain("config", "include.path", str(included))
    result = box.run(g("status"))
    _refused(result, "keys")
    assert "include.path" in result.stderr


def test_sparse_checkout_worktree_config_runs(box):
    # git sparse-checkout writes extensions.worktreeConfig to .git/config and
    # core.sparseCheckout(Cone) to .git/config.worktree. Both are allowed, and
    # the prelude reads config.worktree.
    box.plain("config", "extensions.worktreeConfig", "true")
    (box.ws / ".git" / "config.worktree").write_text("[core]\n\tsparseCheckout = true\n\tsparseCheckoutCone = true\n")
    result = box.run(g("status", "--porcelain"))
    assert result.returncode == 0, result.stderr


def test_a_program_key_in_config_worktree_refuses(box):
    # A key that runs a program is refused wherever git reads it, including the
    # worktree config.
    box.plain("config", "extensions.worktreeConfig", "true")
    (box.ws / ".git" / "config.worktree").write_text(
        f'[filter "x"]\n\tclean = {box.program("wt_clean", filter_=True)}\n'
    )
    result = box.run(g("status", "--porcelain"))
    _refused(result, "keys")
    assert "filter.x.clean" in result.stderr


# Realistic local configs real tools write (fix #2 measurement): which make
# embry0 refuse its own git step, and which run as normal.
_REALISTIC_RUNS = {
    "plain clone": {
        "core.repositoryformatversion": "0",
        "remote.origin.url": "https://github.com/acme/widgets.git",
        "remote.origin.fetch": "+refs/heads/*:refs/remotes/origin/*",
        "branch.main.remote": "origin",
        "branch.main.merge": "refs/heads/main",
    },
    "gh repo clone": {"remote.origin.gh-resolved": "base"},
    "git maintenance register": {"maintenance.auto": "false", "maintenance.strategy": "incremental"},
    "husky": {"core.hooksPath": ".husky/_"},
    "git sparse-checkout": {"extensions.worktreeConfig": "true"},
    "a developer's own settings": {
        "commit.template": ".gitmessage",
        "core.excludesFile": ".git/info/exclude",
        "blame.ignoreRevsFile": ".git-blame-ignore-revs",
        "http.postBuffer": "524288000",
        "push.followTags": "true",
        "checkout.defaultRemote": "origin",
        "diff.noprefix": "true",
        "safe.directory": "/workspace",
        "submodule.active": ".",
    },
}
_REALISTIC_REFUSES = {
    "git lfs install --local": {"filter.lfs.process": "git-lfs filter-process"},
    "a planted lfs extension": {"lfs.extension.foo.clean": "foo-clean %f"},
}


@pytest.mark.parametrize("name", sorted(_REALISTIC_RUNS))
def test_realistic_local_config_runs(box, name):
    for key, value in _REALISTIC_RUNS[name].items():
        box.plain("config", key, value)
    result = box.run(g("status", "--porcelain"))
    assert result.returncode == 0, f"{name} must not make embry0 refuse: {result.stderr}"


@pytest.mark.parametrize("name", sorted(_REALISTIC_REFUSES))
def test_realistic_local_config_that_runs_a_program_refuses(box, name):
    for key, value in _REALISTIC_REFUSES[name].items():
        box.plain("config", key, value)
    _refused(box.run(g("status", "--porcelain")), "keys")


def test_dot_git_as_a_symlink_refuses(box):
    (box.ws / ".git").rename(box.root / "real.git")
    (box.ws / ".git").symlink_to(box.root / "real.git")
    _refused(box.run(g("status")), "not_a_repository")


def test_dot_git_as_a_file_refuses(box):
    (box.ws / ".git").rename(box.root / "real.git")
    (box.ws / ".git").write_text(f"gitdir: {box.root / 'real.git'}\n")
    _refused(box.run(g("status")), "not_a_repository")


def test_a_commondir_refuses(box):
    elsewhere = box.root / "common"
    elsewhere.mkdir()
    (box.ws / ".git" / "commondir").write_text(f"{elsewhere}\n")
    _refused(box.run(g("status")), "commondir")


def test_an_unreadable_local_config_refuses(box):
    (box.ws / ".git" / "config").write_text("[core\n\tbroken")
    _refused(box.run(g("status")), "config_unreadable")


def test_a_git_older_than_2_32_refuses(box):
    fake = box.root / "bin"
    fake.mkdir()
    (fake / "git").write_text("#!/bin/sh\necho 'git version 2.31.8'\n")
    (fake / "git").chmod(0o755)
    argv = sandbox_git.script(g("status"), config_path=str(box.config), owner_uid=os.getuid(), repo=str(box.ws))
    env = {**box.env(), "PATH": f"{fake}:{os.environ['PATH']}"}
    result = subprocess.run(
        argv, cwd=box.ws, env=env, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL
    )
    _refused(result, "git_too_old 2.31.8")


def test_a_rendered_command_without_the_prelude_fails(box):
    result = subprocess.run(
        ["bash", "-c", g("status")], cwd=box.ws, env=box.env(), capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    assert result.returncode == 127, "embry0_git exists only in the prelude"


# ---- the repository's own config ---------------------------------------------------


def test_a_key_that_runs_a_program_refuses_and_is_named_without_its_value(box):
    secret = "value-that-must-not-be-echoed"
    box.plain("config", "filter.x.clean", f"{box.program('clean', filter_=True)} {secret}")
    box.plain("config", "include.path", str(box.root / "more.cfg"))
    result = box.run(g("status"))
    _refused(result, "keys")
    assert "filter.x.clean" in result.stderr and "include.path" in result.stderr
    assert secret not in result.stderr
    assert box.ran() == []


@pytest.mark.parametrize("key", ["init.templateDir", "help.browser"])
def test_a_key_excepted_from_an_allowed_family_refuses(box, key):
    box.plain("config", key, str(box.root))
    result = box.run(g("status"))
    _refused(result, "keys")
    assert key.lower() in result.stderr


def test_husky_hooks_path_passes_and_its_hook_does_not_run(box):
    hooks = box.ws / ".husky" / "_"
    hooks.mkdir(parents=True)
    (hooks / "pre-commit").write_text(f"#!/bin/sh\n{box.program('husky')}\n")
    (hooks / "pre-commit").chmod(0o755)
    box.plain("config", "core.hooksPath", ".husky/_")
    (box.ws / "a.txt").write_text("two\n")
    result = box.run(f"{g('add', '-A')} && {g('commit', '-q', '-m', 'two')}")
    assert result.returncode == 0, result.stderr
    assert box.ran() == [], "embry0's commit ran the repository's hook"
    assert box.plain("log", "-1", "--format=%an") == "embry0\n", "embry0's commit uses embry0's identity"


def test_a_developers_own_commit_still_runs_the_hook(box):
    hooks = box.ws / ".husky" / "_"
    hooks.mkdir(parents=True)
    (hooks / "pre-commit").write_text(f"#!/bin/sh\n{box.program('husky')}\n")
    (hooks / "pre-commit").chmod(0o755)
    box.plain("config", "core.hooksPath", ".husky/_")
    (box.ws / "a.txt").write_text("three\n")
    box.plain("commit", "-qam", "dev")
    assert box.ran() == ["husky"]


def test_planted_hooks_fsmonitor_and_global_config_do_not_run(box):
    box.plain("config", "core.fsmonitor", box.program("fsmonitor"))
    hook = box.ws / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\n{box.program('hook')}\n")
    hook.chmod(0o755)
    # A driver no pin can name: only ignoring the agent's global config keeps it from running.
    (box.home / ".gitconfig").write_text(
        f"[core]\n\tfsmonitor = {box.program('global')}\n\thooksPath = {box.root}\n"
        f"[include]\n\tpath = {box.root / 'global-include.cfg'}\n"
        f'[filter "g"]\n\tclean = {box.program("global_filter", filter_=True)}\n'
    )
    (box.ws / ".gitattributes").write_text("*.txt filter=g\n")
    xdg = box.home / ".config" / "git"
    xdg.mkdir(parents=True)
    (xdg / "config").write_text(f"[core]\n\tpager = {box.program('xdg')}\n\teditor = {box.program('editor')}\n")
    (xdg / "attributes").write_text("* filter=lfs\n")
    (box.ws / "a.txt").write_text("four\n")
    result = box.run(f"{g('status', '--porcelain')} && {g('add', '-A')} && {g('commit', '-q', '-m', 'four')}")
    assert result.returncode == 0, result.stderr
    assert box.ran() == [], f"planted programs ran: {box.ran()}"


def test_an_ext_remote_does_not_run(box):
    box.plain("config", "remote.origin.url", f"ext::sh -c {box.program('ext')}")
    result = box.run(g("ls-remote", "origin"))
    assert result.returncode != 0
    assert "transport 'ext' not allowed" in result.stderr
    assert box.ran() == []


class _Unauthorized(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - the stdlib's name
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="x"')
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def unauthorized_url():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Unauthorized)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_credential_helpers_and_askpass_do_not_run_on_a_401(box, unauthorized_url):
    box.plain("config", "credential.helper", box.program("helper"))
    box.plain("config", f"credential.{unauthorized_url}.helper", box.program("url_helper"))
    box.plain("config", "core.askPass", box.program("askpass"))
    box.plain("config", "remote.origin.url", f"{unauthorized_url}/x.git")
    result = box.run(g("ls-remote", "origin"))
    assert result.returncode != 0 and "embry0-git-refused" not in result.stderr, result.stderr
    assert box.ran() == [], f"a credential helper or askpass ran: {box.ran()}"
    # The same repository without the prelude does run them: the test can fail.
    env = {**box.env(), "GIT_TERMINAL_PROMPT": "0"}
    subprocess.run(
        ["git", "ls-remote", "origin"], cwd=box.ws, env=env, capture_output=True, check=False, stdin=subprocess.DEVNULL
    )
    assert box.ran() != []


# ---- nested repositories ------------------------------------------------------------


def _plant_nested(box: Box, gitmodules: str | None) -> None:
    """A nested repository written as plain files, then made a gitlink, as embry0's own add -A would."""
    sub = box.ws / "sub" / ".git"
    (sub / "refs" / "heads").mkdir(parents=True)
    (sub / "objects" / "info").mkdir(parents=True)
    (sub / "HEAD").write_text("ref: refs/heads/main\n")
    (sub / "refs" / "heads" / "main").write_text("1" * 40 + "\n")
    (sub / "config").write_text(
        f"[core]\n\trepositoryformatversion = 0\n\tfsmonitor = {box.program('nested_fsmonitor')}\n"
        f'[filter "x"]\n\tclean = {box.program("nested_clean", filter_=True)}\n'
    )
    (box.ws / "sub" / ".gitattributes").write_text("* filter=x\n")
    (box.ws / "sub" / "f.txt").write_text("x\n")
    box.plain("add", "-A")
    box.plain("commit", "-q", "-m", "gitlink")
    text = '[submodule "sub"]\n\tpath = sub\n\turl = ./sub\n\tignore = none\n\tfetchRecurseSubmodules = yes\n'
    if gitmodules in ("worktree", "index", "head"):
        (box.ws / ".gitmodules").write_text(text)
    if gitmodules in ("index", "head"):
        box.plain("add", ".gitmodules")
    if gitmodules == "head":
        box.plain("commit", "-q", "-m", "gitmodules")
        box.plain("rm", "-q", "--cached", ".gitmodules")
    if gitmodules in ("index", "head"):
        (box.ws / ".gitmodules").unlink()


@pytest.mark.parametrize("gitmodules", [None, "worktree", "index", "head"])
def test_no_git_starts_in_a_nested_repository(box, gitmodules):
    """Every subcommand embry0 runs, against a populated gitlink whose .gitmodules entry asks to be inspected."""
    _plant_nested(box, gitmodules)
    # Local settings that would recurse: the pins must outrank every one.
    for key, value in [
        ("submodule.recurse", "true"),
        ("diff.ignoreSubmodules", "none"),
        ("status.submoduleSummary", "true"),
        ("fetch.recurseSubmodules", "true"),
        ("push.recurseSubmodules", "check"),
    ]:
        box.plain("config", key, value)
    bare = box.root / "remote.git"
    box.plain("init", "-q", "--bare", str(bare), cwd=box.root)
    box.plain("remote", "add", "origin", str(bare))
    box.plain("push", "-q", "origin", "HEAD:refs/heads/main")
    trace = box.root / "trace"
    patch = box.root / "p.diff"
    patch.write_text("--- a/c.txt\n+++ b/c.txt\n@@ -0,0 +1 @@\n+c\n")
    (box.ws / "b.txt").write_text("b\n")
    (box.ws / "c.txt").write_text("")
    steps = [
        g("status", "--porcelain"),
        g("add", "-A"),
        g("add", "b.txt"),
        g("commit", "-q", "-m", "b"),
        f"{g('commit', '-q', '-m', 'nothing')} || true",
        g("diff", "--name-only", "HEAD~1", "HEAD"),
        g("diff", "--numstat", "HEAD~1...HEAD"),
        g("diff", "--cached", "--quiet"),
        g("rev-parse", "--abbrev-ref", "HEAD"),
        g("rev-list", "--count", "HEAD", "--not", "--remotes=origin"),
        g("for-each-ref", "--count=1", "refs/remotes/origin/"),
        g("log", "-1", "--format=%cs%x09%s", "HEAD"),
        g("merge-base", "HEAD", "HEAD~1"),
        g("cat-file", "-e", "HEAD^{commit}"),
        f"{g('symbolic-ref', '--short', 'refs/remotes/origin/HEAD')} || true",
        g("ls-remote", "--heads", "origin"),
        g("fetch", "-q", "origin", "main:refs/remotes/origin/main"),
        g("push", "-q", "origin", "HEAD:refs/heads/x"),
        g("reset", "-q", "--hard", "HEAD"),
        g("checkout", "-q", "-B", "y"),
        g("checkout", "-q", "-f", "-B", "z", "HEAD"),
        g("switch", "-q", "-c", "w"),
        g("apply", "--index", str(patch)),
        g("worktree", "add", "--detach", str(box.root / "base"), "HEAD~1"),
        g("worktree", "remove", "--force", str(box.root / "base")),
        g("worktree", "prune"),
    ]
    body = "\n".join(
        [
            f"export GIT_TRACE={trace}",
            # The push and fetch go to a local bare repository in this test only.
            "export GIT_ALLOW_PROTOCOL=file:http:https",
            "set -e",
            *steps,
        ]
    )
    result = box.run(body)
    assert result.returncode == 0, result.stderr
    spawned = [line for line in trace.read_text().splitlines() if "run_command: cd" in line]
    assert spawned == [], f"git started in the nested repository ({gitmodules}): {spawned}"
    assert box.ran() == [], f"the nested repository's config ran: {box.ran()}"


def test_without_the_prelude_git_does_start_in_the_nested_repository(box):
    """The nested-repository test can fail: plain git does start there when .gitmodules asks."""
    _plant_nested(box, "worktree")
    trace = box.root / "trace"
    env = {**box.env(), "GIT_TRACE": str(trace)}
    subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=box.ws,
        env=env,
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    assert "cd sub" in trace.read_text()
    assert box.ran() == ["nested_fsmonitor"]


def test_gitmodules_names_are_pinned_from_every_source(box):
    _plant_nested(box, "head")
    result = box.run(f"env | grep '^GIT_CONFIG_KEY_' | sort; {g('config', '--get', 'submodule.sub.ignore')}")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("all"), result.stdout


def test_a_clone_runs_without_a_repository_check(box):
    target = box.root / "clone"
    result = box.run(f"export GIT_ALLOW_PROTOCOL=file\n{g('clone', '-q', str(box.ws), str(target))}", repo=False)
    assert result.returncode == 0, result.stderr
    assert (target / "a.txt").exists()


def test_shell_words_reach_the_script_unquoted(box):
    result = box.run(f"h=HEAD; {g('rev-parse', Shell('$h'))}")
    assert result.returncode == 0 and len(result.stdout.strip()) == 40, result.stderr


def test_a_command_passes_each_argument_as_one_word(box):
    argv = sandbox_git.command("commit", "-q", "-m", "it's; $(touch nope) done", repo=str(box.ws))
    # The production prelude, pointed at the test's config and uid.
    local = sandbox_git.prelude(config_path=str(box.config), owner_uid=os.getuid())
    argv[2] = argv[2].replace(sandbox_git.prelude(), local)
    (box.ws / "a.txt").write_text("five\n")
    box.plain("add", "-A")
    result = subprocess.run(argv, cwd=box.ws, env=box.env(), capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    assert box.plain("log", "-1", "--format=%s") == "it's; $(touch nope) done\n"
    assert not (box.ws / "nope").exists()


# --- git-lfs (fix #1): a system LFS filter + agent-planted lfs.* keys ---------
#
# With git-lfs installed system-wide (trusted), git runs its clean/smudge
# filter on an attributed file. git-lfs reads lfs.extension.<x>.{clean,smudge}
# and lfs.customtransfer.<x>.path from .git/config and runs them. embry0 refuses
# the whole lfs.* family, so a repository that carries those keys makes the step
# refuse before any of them runs.

_needs_git_lfs = pytest.mark.skipif(shutil.which("git-lfs") is None, reason="needs git-lfs")


@_needs_git_lfs
@pytest.mark.parametrize(
    ("key", "value_uses_prog"),
    [
        ("lfs.extension.foo.clean", True),
        ("lfs.extension.foo.smudge", True),
        ("lfs.customtransfer.evil.path", False),
    ],
)
def test_a_planted_lfs_program_does_not_run_under_a_system_filter(box, key, value_uses_prog):
    sysconfig = box.root / "sysconfig"
    env = {"GIT_CONFIG_SYSTEM": str(sysconfig)}
    # Install the LFS filter as a trusted *system* setting (relocated so the
    # host's /etc/gitconfig is untouched), the way an image's `git lfs install
    # --system` would.
    subprocess.run(
        ["git", "lfs", "install", "--system"],
        env={**box.env(), "GIT_CONFIG_SYSTEM": str(sysconfig), "GIT_CONFIG_GLOBAL": str(box.config)},
        check=True,
        capture_output=True,
    )
    prog = box.program("lfs_prog")
    box.plain("config", key, f"{prog} %f" if value_uses_prog else prog)
    if value_uses_prog:
        box.plain("config", "lfs.extension.foo.priority", "0")
        box.plain("config", "lfs.customtransfer.evil.path", prog)
        box.plain("config", "lfs.standalonetransferagent", "evil")
    (box.ws / ".gitattributes").write_text("*.txt filter=lfs\n")
    (box.ws / "b.txt").write_text("content\n")
    result = box.run(f"{g('add', '-A')} && {g('commit', '-q', '-m', 'x')}", env=env)
    _refused(result, "keys")
    assert box.ran() == [], f"a planted lfs program ran: {box.ran()}"


@_needs_git_lfs
def test_the_lfs_threat_is_real_without_the_prelude(box):
    """Plain git add, with the system filter and a planted lfs.extension, runs the program: the guard has something to stop."""
    sysconfig = box.root / "sysconfig"
    subprocess.run(
        ["git", "lfs", "install", "--system"],
        env={**box.env(), "GIT_CONFIG_SYSTEM": str(sysconfig), "GIT_CONFIG_GLOBAL": str(box.config)},
        check=True,
        capture_output=True,
    )
    prog = box.program("lfs_threat")
    box.plain("config", "lfs.extension.foo.clean", f"{prog} %f")
    box.plain("config", "lfs.extension.foo.smudge", "cat")
    box.plain("config", "lfs.extension.foo.priority", "0")
    (box.ws / ".gitattributes").write_text("*.txt filter=lfs\n")
    (box.ws / "b.txt").write_text("content\n")
    subprocess.run(
        ["git", "add", "-A"],
        cwd=box.ws,
        env={**box.env(), "GIT_CONFIG_SYSTEM": str(sysconfig), "GIT_CONFIG_GLOBAL": str(box.config)},
        check=True,
        capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    assert box.ran() == ["lfs_threat"], "the lfs extension clean program should run under plain git"


# --- fix #3: the agent's ~/.gitconfig write runs from / (resume not blocked) --


def _agent_config_run(box, body_env=None):
    from embry0.sandbox.github.git_ops import proxy_route

    route = proxy_route("http://git-proxy:9101", origin="https://github.com", repo="acme/widgets")
    argv = sandbox_git.script(
        sandbox_git.agent_config_body(route, None), repo=None, config_path=str(box.config), owner_uid=os.getuid()
    )
    return subprocess.run(
        argv,
        cwd=box.ws,  # docker's WORKDIR is /workspace; the prelude's `cd /` moves off it
        env={**box.env(), **(body_env or {})},
        input="bearer-000000000000000000000000000000000001\n",
        capture_output=True,
        text=True,
    )


def test_the_agent_config_write_ignores_a_malformed_workspace_config(box):
    # A shell-less agent that left /workspace/.git/config malformed must not
    # block the write of ~/.gitconfig: the prelude's `cd /` keeps git off the repo.
    (box.ws / ".git" / "config").write_text("[core\n\tbroken = ")
    result = _agent_config_run(box)
    assert result.returncode == 0, f"the agent config write must not read /workspace/.git/config: {result.stderr}"
    assert "insteadof" in (box.home / ".gitconfig").read_text().lower()


def test_the_agent_config_write_from_a_dir_home_does_not_raise_to_the_caller():
    # ~/.gitconfig as a directory makes the write fail; configure() swallows it
    # (tested in test_sandbox_git.py). Here we confirm the script itself exits
    # non-zero rather than hang, so the caller's swallow has something to catch.
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        box = Box(Path(td))
        (box.home / ".gitconfig").mkdir()  # a directory where git wants a file
        result = _agent_config_run(box)
        assert result.returncode != 0, "a ~/.gitconfig directory must make the write fail, not pass silently"


# --- fix #2: embry0's identity and remote.origin.mirror are pinned ------------


def test_embry0s_commit_uses_embry0s_identity_over_a_local_user(box):
    # The agent can set user.* in .git/config; embry0's own commit must still be embry0's.
    box.plain("config", "user.name", "Attacker")
    box.plain("config", "user.email", "attacker@evil.example")
    (box.ws / "a.txt").write_text("changed\n")
    result = box.run(f"{g('add', '-A')} && {g('commit', '-q', '-m', 'salvage')}")
    assert result.returncode == 0, result.stderr
    assert box.plain("log", "-1", "--format=%an <%ae>") == "embry0 <embry0@example.com>\n"


def test_remote_origin_mirror_is_pinned_off(box):
    box.plain("config", "remote.origin.mirror", "true")
    result = box.run(g("config", "--get", "remote.origin.mirror"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "false", "a mirror remote must be pinned off for embry0's push"


# ---- objects and commits read as they are stored ------------------------------------


def test_embry0s_git_reads_objects_and_commits_as_stored(box):
    """A replacement ref and a graft file in the workspace change what the agent's own git reads, not embry0's."""
    first = box.plain("rev-parse", "HEAD").strip()
    (box.ws / "a.txt").write_text("two\n")
    box.plain("commit", "-q", "-am", "two")
    head = box.plain("rev-parse", "HEAD").strip()
    blob = box.plain("rev-parse", "HEAD:a.txt").strip()
    other = box.plain("hash-object", "-w", "--stdin")  # the empty blob
    box.plain("replace", blob, other.strip())
    (box.ws / ".git" / "info").mkdir(exist_ok=True)
    (box.ws / ".git" / "info" / "grafts").write_text(f"{head}\n")
    assert box.plain("cat-file", "-p", blob) == "" and box.plain("rev-list", "--count", "HEAD").strip() == "1", (
        "the agent's own git reads the replacement and the graft: the case below would measure nothing"
    )

    shown = box.run(g("cat-file", "-p", blob))
    counted = box.run(g("rev-list", "--count", "HEAD"))
    parent = box.run(g("rev-parse", "HEAD^"))
    assert shown.returncode == 0 and shown.stdout == "two\n", f"a replacement ref stood in for the blob: {shown}"
    assert counted.stdout.strip() == "2", f"a graft file rewrote the head's parents: {counted.stdout!r}"
    assert parent.stdout.strip() == first, parent
    assert "graft" not in shown.stderr + counted.stderr, "no deprecation hint about a graft file it does not read"
