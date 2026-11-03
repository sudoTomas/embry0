"""embry0's own git commands in a sandbox: rendering, the key allowlist, refusals and the config writers.

The prelude itself is run against real git in ``test_sandbox_git_prelude.py``;
these tests read what the module renders and what it does with a result.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from embry0.execution import sandbox_git
from embry0.execution.sandbox_git import (
    EXCEPTED,
    INERT,
    PINS,
    GitStep,
    Refusal,
    SandboxGitRefused,
    Shell,
    git,
    local_key_allowed,
    parse_refusal,
)
from embry0.sandbox.git_identity import GitIdentity
from embry0.sandbox.github.git_ops import proxy_route

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "git" / "config-keys-2.39.5.txt"
BEARER = "bearer-0000000000000000000000000000000000000001"

# Every config key whose value git runs as a program, or which makes git start
# one or read more configuration, from git-config(1) for 2.39 and the keys later
# versions added. Placeholders (<driver>, <url>, ...) become a concrete name.
PROGRAM_KEYS = (
    "core.fsmonitor",
    "core.hookspath",
    "core.sshcommand",
    "core.gitproxy",
    "core.askpass",
    "core.pager",
    "core.editor",
    "sequence.editor",
    "core.alternaterefscommand",
    "credential.helper",
    "credential.https://example.com.helper",
    "filter.x.clean",
    "filter.x.smudge",
    "filter.x.process",
    "filter.lfs.process",
    "lfs.extension.foo.clean",
    "lfs.extension.foo.smudge",
    "lfs.customtransfer.x.path",
    "lfs.standalonetransferagent",
    "diff.external",
    "diff.x.command",
    "diff.x.textconv",
    "diff.tool",
    "difftool.x.cmd",
    "difftool.x.path",
    "merge.x.driver",
    "merge.tool",
    "mergetool.x.cmd",
    "mergetool.x.path",
    "gpg.program",
    "gpg.ssh.program",
    "gpg.x509.program",
    "gpg.openpgp.program",
    "gpg.ssh.defaultkeycommand",
    "commit.gpgsign",
    "tag.gpgsign",
    "push.gpgsign",
    "log.showsignature",
    "merge.verifysignatures",
    "include.path",
    "includeif.gitdir:/x/.path",
    "remote.origin.uploadpack",
    "remote.origin.receivepack",
    "remote.origin.vcs",
    "protocol.allow",
    "protocol.ext.allow",
    "url.ext::sh.insteadof",
    "url.x.pushinsteadof",
    "uploadpack.packobjectshook",
    "alias.st",
    "pager.status",
    "interactive.difffilter",
    "help.browser",
    "web.browser",
    "browser.x.cmd",
    "browser.x.path",
    "man.viewer",
    "man.x.cmd",
    "man.x.path",
    "sendemail.smtpserver",
    "sendemail.sendmailcmd",
    "sendemail.tocmd",
    "sendemail.cccmd",
    "imap.tunnel",
    "instaweb.httpd",
    "guitool.x.cmd",
    "ssh.variant",
    "submodule.x.update",
    "submodule.recurse",
    "fetch.recursesubmodules",
    "push.recursesubmodules",
    "status.submodulesummary",
    "init.templatedir",
    "hook.x.command",
    "hook.x.event",
)

#: Program keys a repository may carry because embry0 pins them at command scope.
_PINNED_PROGRAM_KEYS = {key.lower() for key, _ in PINS} | {"credential.https://example.com.helper"}


@pytest.mark.parametrize("key", PROGRAM_KEYS)
def test_no_key_that_runs_a_program_is_allowed_unless_embry0_pins_it(key):
    if key in _PINNED_PROGRAM_KEYS:
        assert local_key_allowed(key), f"{key} is pinned at command scope, so a repository may carry it"
    else:
        assert not local_key_allowed(key), f"{key} names or starts a program, and the allowlist lets it through"


def test_no_inert_family_admits_a_key_that_runs_a_program():
    admitted = [
        (key, f.pattern)
        for key in PROGRAM_KEYS
        for f in INERT
        if f.matches(key) and not any(e.matches(key) for e in EXCEPTED) and key not in _PINNED_PROGRAM_KEYS
    ]
    assert not admitted, f"an INERT family admits keys that run a program: {admitted}"


def _git_239_keys() -> list[str]:
    return [line.strip().lower() for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


# Patterns that name no key git 2.39 knows, on purpose.
_NOT_IN_GIT_239 = {
    "remote.*.gh-resolved": "gh's own marker on a remote, which git itself never reads",
    "core.longpaths": "Git for Windows only",
}


def test_every_inert_family_names_keys_git_2_39_knows():
    """A typo in a pattern would admit nothing and refuse a key git writes; each one matches git's own list."""
    known = [k.replace("<name>", "x").replace("<pattern>", "x").replace("<url>", "x") for k in _git_239_keys()]
    dead = [f.pattern for f in INERT if f.pattern not in _NOT_IN_GIT_239 and not any(f.matches(k) for k in known)]
    assert not dead, f"INERT patterns that match no key git 2.39 knows: {dead}"


def test_the_keys_a_clone_and_a_push_write_are_allowed():
    written = [
        "core.repositoryformatversion",
        "core.filemode",
        "core.bare",
        "core.logallrefupdates",
        "remote.origin.url",
        "remote.origin.fetch",
        "branch.main.remote",
        "branch.main.merge",
        "branch.feature/x.merge",
        "extensions.objectformat",
        "core.hookspath",  # husky
        "submodule.vendor.url",
        "submodule.vendor.active",
    ]
    refused = [key for key in written if not local_key_allowed(key)]
    assert not refused, f"keys git (or husky) writes by itself are refused: {refused}"


def test_the_coordinators_families_are_allowed_with_their_exceptions():
    allowed = [
        "advice.detachedhead",
        "color.ui",
        "column.ui",
        "help.autocorrect",
        "i18n.commitencoding",
        "init.defaultbranch",
        "gc.auto",
        "rerere.enabled",
        "status.showuntrackedfiles",
        "pull.rebase",
        "push.default",
        "push.autosetupremote",
        "fetch.prune",
        "fetch.prunetags",
        "rebase.instructionformat",
        "merge.conflictstyle",
        "merge.ff",
        "merge.log",
        "diff.algorithm",
        "diff.renames",
        "diff.colormoved",
        "diff.mnemonicprefix",
        "log.date",
        "user.email",
        "index.version",
        "pack.threads",
        "feature.manyfiles",
        "core.autocrlf",
        "core.untrackedcache",
        "remote.origin.promisor",
        "remote.origin.partialclonefilter",
        "remote.origin.gh-resolved",
        "extensions.partialclone",
        "extensions.worktreeconfig",
        # fix #2: harmless local config real tools write
        "maintenance.auto",
        "maintenance.strategy",
        "commit.template",
        "blame.ignorerevsfile",
        "core.excludesfile",
        "checkout.defaultremote",
        "push.followtags",
        "http.postbuffer",
        "safe.directory",
        "submodule.active",
        "core.sparsecheckout",
        "core.sparsecheckoutcone",
        "diff.noprefix",
    ]
    assert [k for k in allowed if not local_key_allowed(k)] == []
    # fix #1: lfs.* is refused wholesale (git-lfs runs its extension/customtransfer keys);
    # a URL-scoped credential key that is not a helper is refused (not credential.*).
    refused = [
        "help.browser",
        "init.templatedir",
        "filter.lfs.clean",
        "lfs.extension.foo.clean",
        "lfs.customtransfer.x.path",
        "lfs.url",
        "lfs.repositoryformatversion",
        "http.proxy",
        "core.worktree",
        "credential.https://example.com.username",
    ]
    assert [k for k in refused if local_key_allowed(k)] == []


# ---- rendering ---------------------------------------------------------------------


def test_git_renders_a_call_to_the_prelude_function_with_every_argument_quoted():
    assert git("commit", "-m", "it's done; rm -rf /") == "embry0_git commit -m 'it'\"'\"'s done; rm -rf /'"
    assert git("rev-parse", Shell('"$mb"')) == 'embry0_git rev-parse "$mb"'


@pytest.mark.parametrize(
    ("subcommand", "flags"),
    [
        ("status", "--ignore-submodules=all"),
        ("diff", "--no-ext-diff --no-textconv"),
        ("fetch", "--no-recurse-submodules"),
        ("push", "--no-recurse-submodules"),
    ],
)
def test_subcommands_a_gitmodules_could_steer_carry_their_flags(subcommand, flags):
    assert git(subcommand, "x").startswith(f"embry0_git {subcommand} {flags} "), (
        f"git {subcommand} must not start git in a nested repository"
    )


def test_a_per_command_option_cannot_outrank_a_pin():
    assert git("checkout", "x", options={"advice.detachedHead": "false"}).startswith(
        "embry0_git -c advice.detachedHead=false checkout"
    )
    for key in ("core.hooksPath", "core.fsmonitor", "filter.x.clean", "include.path", "help.browser"):
        with pytest.raises(ValueError, match="may set per command"):
            git("status", options={key: "x"})


def test_a_subcommand_is_a_word():
    with pytest.raises(ValueError, match="not a git subcommand"):
        git("status; id")


def test_script_starts_with_the_prelude_and_checks_the_repository():
    argv = sandbox_git.script("embry0_git status")
    assert argv[:2] == ["bash", "-c"]
    assert argv[2].startswith("embry0_git_refuse() {")
    assert "embry0_git_repo /workspace\nembry0_git status" in argv[2]
    assert "embry0_git_repo /workspace" not in sandbox_git.script("true", repo=None)[2]


def test_the_prelude_sets_every_pin_and_the_environment():
    text = sandbox_git.prelude()
    for key, value in PINS:
        assert f"embry0_git_pin {key} " in text, key
    for name in ("GIT_CONFIG_GLOBAL=/run/embry0/git/config", "GIT_ALLOW_PROTOCOL=http:https", "GIT_ASKPASS=''"):
        assert name in text, name
    # GIT_CONFIG_GLOBAL has one source: prelude() adds it, ENVIRONMENT does not carry it.
    assert text.count("GIT_CONFIG_GLOBAL=") == 1
    assert "GIT_CONFIG_GLOBAL" not in dict(sandbox_git.ENVIRONMENT)
    # The author of embry0's commits is pinned from embry0's own config, above a local user.*.
    assert "embry0_git_pin_from_config user.name" in text and "embry0_git_pin_from_config user.email" in text
    assert "remote.origin.mirror" in dict(PINS) and dict(PINS)["remote.origin.mirror"] == "false"
    # The config reads carry --no-includes so include.path/includeIf is always
    # seen as a key and refused. git 2.39 does not follow includes for a --file
    # read anyway, so no behavioural test distinguishes it; pin it as a source
    # invariant (a cross-version guarantee), and check config.worktree too.
    assert '--file "$embry0_git_dir/config" --no-includes --name-only --list' in text
    assert '--file "$embry0_git_dir/config.worktree" --no-includes --name-only --list' in text
    assert '[ "${embry0_git_stat%% *}" = "0" ]' in text, "production requires root (uid 0)"


def test_command_is_one_checked_git_command_with_its_arguments_as_words():
    argv = sandbox_git.command("push", "-u", "origin", "feature/x; id")
    assert argv[2].endswith('embry0_git_repo /workspace\nembry0_git "$@"')
    assert argv[3:] == ["embry0-git", "push", "--no-recurse-submodules", "-u", "origin", "feature/x; id"]
    assert sandbox_git.command("add", "-A")[2].endswith('embry0_git_add "$@"')
    assert sandbox_git.command("add", "-A")[4:] == ["-A"]
    with pytest.raises(ValueError, match="verbatim"):
        sandbox_git.command("rev-parse", Shell("$x"))


# ---- refusals ----------------------------------------------------------------------


def test_a_refusal_names_the_keys_and_how_to_clear_them_never_a_value():
    stderr = "Docker command failed: embry0-git-refused: keys filter.x.clean include.path"
    refusal = parse_refusal(stderr)
    assert refusal == Refusal("keys", keys=("filter.x.clean", "include.path"))
    text = refusal.explain()
    assert "filter.x.clean, include.path" in text
    assert "git config --unset-all" in text
    assert "docs/sandbox-git.md" in text


def test_a_refusal_caps_and_cleans_the_key_names():
    many = " ".join(f"k{i}.x" for i in range(30))
    refusal = parse_refusal(f"embry0-git-refused: keys {many} \x1b[31mred")
    assert refusal is not None
    assert len(refusal.keys) == 21 and refusal.keys[-1] == "and 11 more"
    assert all("\x1b" not in k for k in refusal.keys)


def test_no_mark_is_no_refusal():
    assert parse_refusal("fatal: not a git repository") is None


class _Docker:
    def __init__(self, error: str | None = None, out: str = "ok") -> None:
        self.error, self.out = error, out
        self.calls: list[dict[str, Any]] = []

    def build_exec_cmd(self, container: str, command: list[str], **kwargs: Any) -> list[str]:
        user = ["-u", kwargs["user"]] if kwargs.get("user") else []
        return ["docker", "exec", *(["-i"] if kwargs.get("interactive") else []), *user, container, *command]

    async def run_cmd(self, cmd: list[str], timeout: int = 60, **kwargs: Any) -> str:
        self.calls.append({"cmd": cmd, "timeout": timeout, **kwargs})
        if self.error:
            raise RuntimeError(f"Docker command failed: {self.error}")
        return self.out


async def test_run_raises_a_named_refusal_logs_it_and_counts_it(monkeypatch):
    steps: list[str] = []
    logged: list[dict[str, Any]] = []
    monkeypatch.setattr(sandbox_git, "_refusal_observer", steps.append)
    monkeypatch.setattr(sandbox_git.logger, "warning", lambda event, **kw: logged.append({"event": event, **kw}))
    docker = _Docker(error="embry0-git-refused: keys filter.x.clean")
    with pytest.raises(SandboxGitRefused) as caught:
        await sandbox_git.run(
            docker, "c1", sandbox_git.command("status"), step=GitStep.SALVAGE_COMMIT, job_id="job-000000000001"
        )
    assert isinstance(caught.value, RuntimeError), "callers that catch RuntimeError keep working"
    assert "filter.x.clean" in str(caught.value) and "salvage_commit" in str(caught.value)
    assert steps == ["salvage_commit"], "the refusal was not counted"
    assert logged == [
        {
            "event": "sandbox_git_refused",
            "job_id": "job-000000000001",
            "step": "salvage_commit",
            "container": "c1",
            "reason": "keys",
            "keys": ["filter.x.clean"],
            "detail": None,
        }
    ]


async def test_run_passes_other_failures_through():
    with pytest.raises(RuntimeError, match="fatal: bad revision") as caught:
        await sandbox_git.run(
            _Docker(error="fatal: bad revision"), "c1", sandbox_git.command("log"), step=GitStep.CLONE
        )
    assert not isinstance(caught.value, SandboxGitRefused)


async def test_run_takes_only_a_rendered_script():
    with pytest.raises(ValueError, match="sandbox_git.script"):
        await sandbox_git.run(_Docker(), "c1", ["git", "status"], step=GitStep.CLONE)


async def test_a_metric_that_raises_does_not_fail_the_step(monkeypatch):
    def boom(step: str) -> None:
        raise RuntimeError("metrics down")

    monkeypatch.setattr(sandbox_git, "_refusal_observer", boom)
    with pytest.raises(SandboxGitRefused):
        await sandbox_git.run(
            _Docker(error="embry0-git-refused: commondir /workspace/.git/commondir"),
            "c1",
            sandbox_git.command("status"),
            step=GitStep.CLONE,
        )


def test_refusal_of_finds_a_refusal_in_a_cause_chain():
    refused = SandboxGitRefused(GitStep.CLONE, Refusal("config_missing"))
    try:
        try:
            raise refused
        except SandboxGitRefused as exc:
            raise RuntimeError("clone failed") from exc
    except RuntimeError as outer:
        assert sandbox_git.refusal_of(outer) is refused
    assert sandbox_git.refusal_of(RuntimeError("x")) is None


# ---- configuration -----------------------------------------------------------------

_ROUTE = proxy_route("http://git-proxy:9101", origin="https://github.com", repo="acme/widgets")
_IDENTITY = GitIdentity(name='Acme "Bot" \\ CI', email="bot@example.com")


def test_config_text_holds_the_identity_the_rewrite_and_the_bearer():
    text = sandbox_git.config_text(route=_ROUTE, sandbox_token=BEARER, identity=_IDENTITY)
    assert '\tname = "Acme \\"Bot\\" \\\\ CI"' in text
    assert (
        '[url "http://git-proxy:9101/git/acme/widgets.git"]\n\tinsteadOf = "https://github.com/acme/widgets.git"'
        in text
    )
    assert f'[http "http://git-proxy:9101/git/"]\n\textraHeader = "Authorization: Bearer {BEARER}"' in text


def test_config_text_refuses_a_line_break_in_a_value():
    with pytest.raises(ValueError, match="line break"):
        sandbox_git.config_text(route=None, sandbox_token="", identity=GitIdentity(name="a\n[core]", email="e"))


async def test_configure_writes_embry0s_file_as_root_and_the_agents_with_the_bearer_on_stdin_only():
    docker = _Docker()
    await sandbox_git.configure(docker, "c1", route=_ROUTE, sandbox_token=BEARER, identity=_IDENTITY, job_id="j")
    root, agent = docker.calls
    assert root["cmd"][:5] == ["docker", "exec", "-i", "-u", "root"], "embry0's file is written as root"
    assert root["cmd"][5:] == ["c1", *sandbox_git.WRITE_CONFIG_ARGV]
    assert BEARER in root["stdin_data"].decode()
    assert agent["cmd"][:3] == ["docker", "exec", "-i"] and "-u" not in agent["cmd"], "the agent's file as the agent"
    assert agent["stdin_data"] == f"{BEARER}\n".encode()
    for call in docker.calls:
        assert not any(BEARER in word for word in call["cmd"]), "the bearer must never be on a docker exec command line"


async def test_configure_without_a_proxy_writes_embry0s_file_only():
    docker = _Docker()
    await sandbox_git.configure(docker, "c1", route=None, identity=_IDENTITY)
    assert len(docker.calls) == 1 and "-u" in docker.calls[0]["cmd"]


async def test_configure_rejects_a_malformed_bearer_before_writing():
    docker = _Docker()
    with pytest.raises(ValueError, match="sandbox_token"):
        await sandbox_git.configure(docker, "c1", route=_ROUTE, sandbox_token="short", identity=None)
    assert docker.calls == []


async def test_configure_swallows_a_failing_agent_config_write(monkeypatch):
    """A shell-less agent that broke ~/.gitconfig must not fail create or resume (#353 review, fix #3)."""

    class _RootOkAgentFails(_Docker):
        async def run_cmd(self, cmd, timeout=60, **kwargs):
            self.calls.append({"cmd": cmd, "timeout": timeout, **kwargs})
            if "-u" in cmd and "root" in cmd:
                return "ok"  # embry0's own root-owned config writes fine
            raise RuntimeError("Docker command failed: embry0_git_refuse ... or mkdir: not a directory")

    logged: list[str] = []
    monkeypatch.setattr(sandbox_git.logger, "warning", lambda event, **kw: logged.append(event))
    docker = _RootOkAgentFails()
    # Must not raise, even though the agent-config write fails.
    await sandbox_git.configure(docker, "c1", route=_ROUTE, sandbox_token=BEARER, identity=_IDENTITY, job_id="j")
    assert docker.calls[0]["cmd"][:5] == ["docker", "exec", "-i", "-u", "root"], (
        "embry0's root config was still written"
    )
    assert "sandbox_git_agent_config_failed" in logged, "the swallowed failure is logged"


def test_the_refusal_counter_is_seeded_and_counts_by_step():
    from prometheus_client import CollectorRegistry

    from embry0.metrics.loop import LoopMetrics

    registry = CollectorRegistry()
    metrics = LoopMetrics(registry)
    name = "embry0_sandbox_git_refusals_total"
    for step in GitStep:
        assert registry.get_sample_value(name, {"step": step.value}) == 0, f"{step} is not seeded"
    metrics.sandbox_git_refused("verify_gate")
    metrics.sandbox_git_refused("something else")
    assert registry.get_sample_value(name, {"step": "verify_gate"}) == 1
    assert registry.get_sample_value(name, {"step": "other"}) == 1


def test_identity_fakes_are_plain():
    """configure reads .name and .email only, so any identity-like object works."""
    text = sandbox_git.config_text(route=None, sandbox_token="", identity=SimpleNamespace(name="a", email="b"))
    assert '\tname = "a"\n\temail = "b"' in text


def test_the_docs_list_every_allowed_family_and_every_exception():
    """docs/sandbox-git.md is what an operator reads when a step is refused; it must not drift from the data."""
    doc = (Path(__file__).resolve().parents[3] / "docs" / "sandbox-git.md").read_text(encoding="utf-8").lower()
    missing = []
    for family in (*INERT, *EXCEPTED):
        p = family.pattern
        needle = p[p.rindex(".") :] if ".*." in p else p
        if needle not in doc:
            missing.append(p)
    assert not missing, f"docs/sandbox-git.md does not name: {missing}"
    for step in GitStep:
        assert f"`{step.value}`" in doc, f"docs/sandbox-git.md does not explain the {step.value} step"
