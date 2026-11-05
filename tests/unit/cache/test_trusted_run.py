"""embry0.cache.trusted_run: the forge's default branch and head decide which runs may populate the shared cache."""

from __future__ import annotations

import importlib
import importlib.util
from unittest.mock import MagicMock

import pytest

SHA = "a" * 40
OTHER = "b" * 40


def _module():
    assert importlib.util.find_spec("embry0.cache.trusted_run") is not None, (
        "embry0.cache.trusted_run is missing: nothing decides trust from the forge's default branch"
    )
    return importlib.import_module("embry0.cache.trusted_run")


def test_the_symref_and_the_head_are_read_from_ls_remote():
    m = _module()
    head = m.parse_symref_head(f"ref: refs/heads/main\tHEAD\n{SHA}\tHEAD\n")
    assert head == m.DefaultHead(branch="main", sha=SHA), f"parsed {head}"
    sha256 = "c" * 64
    assert m.parse_symref_head(f"ref: refs/heads/release/2026\tHEAD\n{sha256}\tHEAD\n") == m.DefaultHead(
        "release/2026", sha256
    ), "a SHA-256 repository's head is read too"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("", id="empty"),
        pytest.param(f"{SHA}\tHEAD\n", id="no-symref"),
        pytest.param("ref: refs/heads/main\tHEAD\n", id="no-commit"),
        pytest.param("ref: refs/heads/main\tHEAD\nnot-a-sha\tHEAD\n", id="bad-commit"),
        pytest.param(f"ref: refs/heads/-x\tHEAD\n{SHA}\tHEAD\n", id="bad-branch"),
        pytest.param(f"ref: refs/heads/main\tHEADS\n{SHA}\tHEAD\n", id="other-ref"),
        pytest.param(f"ref: refs/tags/v1\tHEAD\n{SHA}\tHEAD\n", id="tag"),
        pytest.param(f"ref: refs/heads/main\tHEAD\nref: refs/heads/evil\tHEAD\n{SHA}\tHEAD\n", id="two-symrefs"),
        pytest.param(f"ref: refs/heads/main\tHEAD\nref: refs/heads/main\tHEAD\n{SHA}\tHEAD\n", id="symref-twice"),
        pytest.param(f"ref: refs/heads/main\tHEAD\n{SHA}\tHEAD\n{OTHER}\tHEAD\n", id="two-head-shas"),
        pytest.param(f"ref: refs/heads/main\tHEAD\n{SHA}\tHEAD\n{SHA}\tHEAD\n", id="head-sha-twice"),
        pytest.param(f"ref: refs/heads/main\tHEAD\n{SHA}\tHEAD\nnot-a-sha\tHEAD\n", id="extra-head-line"),
    ],
)
def test_anything_else_reads_as_no_answer(text):
    m = _module()
    assert m.parse_symref_head(text) is None, f"{text!r} must not read as a default branch"


@pytest.mark.parametrize(
    ("scope", "branch", "head", "default", "trusted"),
    [
        pytest.param("per-repo", "main", SHA, ("main", SHA), True, id="default-at-head"),
        pytest.param("per-repo", "main", OTHER, ("main", SHA), False, id="default-name-other-commit"),
        pytest.param("per-repo", "feature", SHA, ("main", SHA), False, id="other-branch-same-commit"),
        pytest.param("per-repo", "main", SHA, None, False, id="forge-did-not-answer"),
        pytest.param("per-job", "main", SHA, ("main", SHA), False, id="per-job-requested"),
        pytest.param("per-repo", None, SHA, ("main", SHA), False, id="no-branch"),
        pytest.param("per-repo", "main", "", ("main", SHA), False, id="no-head"),
    ],
)
def test_only_the_default_branch_at_its_head_is_trusted(scope, branch, head, default, trusted):
    m = _module()
    got = m.trusted_per_repo_volume(
        scope, branch=branch, head_sha=head, default=m.DefaultHead(*default) if default else None
    )
    assert got is trusted, f"scope={scope} branch={branch} head={head} default={default}: expected {trusted}"


def _docker(answer):
    docker = MagicMock()
    docker.build_exec_cmd = lambda c, cmd, **_kw: ["docker", "exec", c, *cmd]
    seen: list[list[str]] = []

    async def run_cmd(cmd, timeout=60, **_kw):
        seen.append(cmd)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    docker.run_cmd = run_cmd
    return docker, seen


@pytest.mark.asyncio
async def test_the_forge_is_asked_through_sandbox_git_outside_the_workspace():
    m = _module()
    docker, seen = _docker(f"ref: refs/heads/trunk\tHEAD\n{SHA}\tHEAD\n")
    head = await m.forge_default_head(docker, "c1", origin_url="https://forge.example/acme/widgets.git")
    assert head == m.DefaultHead("trunk", SHA), f"got {head}"
    script = seen[0][-1]
    assert script.rstrip().endswith("embry0_git ls-remote --symref https://forge.example/acme/widgets.git HEAD"), (
        "the lookup is one ls-remote of the repository's forge URL"
    )
    assert "\nembry0_git_repo /workspace\n" not in script, (
        "the lookup runs outside the workspace, so nothing in its .git is read"
    )


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(RuntimeError("fatal: could not read from remote repository"), id="failed"),
        pytest.param(RuntimeError("embry0-git-refused: config_missing /run/embry0/git/config"), id="refused"),
        pytest.param("garbage\n", id="unreadable"),
    ],
)
@pytest.mark.asyncio
async def test_no_answer_from_the_forge_is_none_never_an_error(answer):
    m = _module()
    docker, _ = _docker(answer)
    assert await m.forge_default_head(docker, "c1", origin_url="https://forge.example/acme/widgets.git") is None, (
        "a lookup that fails or is refused must read as no answer (nothing trusted), never fail the run"
    )
