"""workspace_diff — base-branch resolution (RAV-1405) + changed-file diff."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from embry0.execution.workspace_diff import compute_changed_files_via_diff, diff_line_counts, resolve_base_branch


def _docker(run_output: str | Exception = "") -> Mock:
    docker = Mock()
    docker.build_exec_cmd = Mock(side_effect=lambda cid, cmd, **_kw: ["exec", cid, *cmd])
    if isinstance(run_output, Exception):
        docker.run_cmd = AsyncMock(side_effect=run_output)
    else:
        docker.run_cmd = AsyncMock(return_value=run_output)
    return docker


@pytest.mark.asyncio
async def test_configured_base_wins_without_docker_call() -> None:
    docker = _docker("origin/master\n")
    assert await resolve_base_branch(docker=docker, container_id="c1", configured="release/1.2") == "release/1.2"
    docker.run_cmd.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolves_origin_head_symbolic_ref() -> None:
    # `git symbolic-ref --short refs/remotes/origin/HEAD` prints origin/<name>.
    docker = _docker("origin/master\n")
    assert await resolve_base_branch(docker=docker, container_id="c1") == "master"


@pytest.mark.asyncio
async def test_accepts_bare_branch_name() -> None:
    docker = _docker("develop\n")
    assert await resolve_base_branch(docker=docker, container_id="c1") == "develop"


@pytest.mark.asyncio
async def test_empty_output_falls_back_to_main() -> None:
    # origin/HEAD unset (non-git workspace, detached edge cases) → `|| true` → "".
    docker = _docker("")
    assert await resolve_base_branch(docker=docker, container_id="c1") == "main"


@pytest.mark.asyncio
async def test_exec_failure_falls_back_to_main() -> None:
    docker = _docker(RuntimeError("exec failed"))
    assert await resolve_base_branch(docker=docker, container_id="c1") == "main"


@pytest.mark.asyncio
async def test_disallowed_characters_fall_back_to_main() -> None:
    docker = _docker("bad branch; rm -rf /\n")
    assert await resolve_base_branch(docker=docker, container_id="c1") == "main"


@pytest.mark.asyncio
async def test_diff_returns_stripped_file_list() -> None:
    docker = Mock()
    docker.build_exec_cmd = Mock(side_effect=lambda cid, cmd, **_kw: ["exec", cid, *cmd])
    docker.run_cmd = AsyncMock(side_effect=["", "a.py\n b/c.ts \n\n"])
    files = await compute_changed_files_via_diff(docker=docker, container_id="c1", base_branch="master")
    assert files == ["a.py", "b/c.ts"]
    # The fetch + diff both target the given base, not a hardcoded main.
    fetch_shell = " ".join(docker.run_cmd.await_args_list[0].args[0][-4:])
    diff_shell = docker.run_cmd.await_args_list[1].args[0][-1]
    assert "master" in fetch_shell and "main" not in fetch_shell
    # #398: the merge-base diff (three dots). Two dots is a tree comparison
    # that counts every commit main gained since the fork as the branch's own.
    assert "origin/master...HEAD" in diff_shell, diff_shell
    assert "origin/master..HEAD" not in diff_shell.replace("origin/master...HEAD", ""), (
        f"two-dot diff would attribute main's newer commits to the branch: {diff_shell}"
    )


@pytest.mark.asyncio
async def test_resolver_command_includes_ls_remote_fallback() -> None:
    """RAV-1400: single-branch clones (QA bootstrap: --branch <ref>) never set
    a local origin/HEAD — the resolver's shell must fall back to ls-remote."""
    docker = _docker("master\n")
    assert await resolve_base_branch(docker=docker, container_id="c1") == "master"
    shell = docker.run_cmd.await_args.args[0][-1]
    assert "symbolic-ref --short refs/remotes/origin/HEAD" in shell
    assert "git ls-remote --symref origin HEAD" in shell


@pytest.mark.asyncio
async def test_resolver_takes_first_line_of_multiline_output() -> None:
    # ls-remote emits the symref line first when the local lookup was empty;
    # only the first parsed line counts.
    docker = _docker("master\nsomething-else\n")
    assert await resolve_base_branch(docker=docker, container_id="c1") == "master"


def _diff_docker(*, fetch_error: Exception | None = None, diff_error: Exception | None = None) -> Mock:
    """run_cmd raises RuntimeError on a non-zero exit, as DockerClient.run_cmd does."""
    docker = Mock()
    docker.build_exec_cmd = Mock(side_effect=lambda cid, cmd, **_kw: ["exec", cid, *cmd])

    async def _run(cmd, timeout=60, **_kw):
        shell = " ".join(cmd)
        if "git fetch" in shell and fetch_error is not None:
            raise fetch_error
        if "git diff" in shell and diff_error is not None:
            raise diff_error
        return ""

    docker.run_cmd = AsyncMock(side_effect=_run)
    return docker


@pytest.mark.asyncio
async def test_strict_diff_raises_when_the_fetch_fails() -> None:
    from embry0.execution.workspace_diff import WorkspaceDiffUnavailable

    docker = _diff_docker(fetch_error=RuntimeError("couldn't find remote ref main"))
    with pytest.raises(WorkspaceDiffUnavailable, match="couldn't find remote ref"):
        await compute_changed_files_via_diff(docker=docker, container_id="c1", base_branch="main", strict=True)


@pytest.mark.asyncio
async def test_strict_diff_raises_when_git_diff_exits_non_zero() -> None:
    from embry0.execution.workspace_diff import WorkspaceDiffUnavailable

    docker = _diff_docker(diff_error=RuntimeError("fatal: no merge base"))
    with pytest.raises(WorkspaceDiffUnavailable, match="no merge base"):
        await compute_changed_files_via_diff(docker=docker, container_id="c1", base_branch="main", strict=True)
    diff_shell = docker.run_cmd.await_args_list[1].args[0][-1]
    assert "|| true" not in diff_shell, f"a strict diff must let git's exit status surface: {diff_shell}"


@pytest.mark.asyncio
async def test_non_strict_diff_still_returns_empty_on_either_failure() -> None:
    for docker in (
        _diff_docker(fetch_error=RuntimeError("fetch failed")),
        _diff_docker(diff_error=RuntimeError("diff failed")),
    ):
        assert await compute_changed_files_via_diff(docker=docker, container_id="c1", base_branch="main") == []
    docker = _diff_docker()
    await compute_changed_files_via_diff(docker=docker, container_id="c1", base_branch="main")
    assert "|| true" in docker.run_cmd.await_args_list[1].args[0][-1], "non-strict diff must keep `|| true`"


async def test_line_counts_refuse_a_base_ending_in_a_newline() -> None:
    """Python's ``$`` also matches just before a final newline, so ``match`` took ``main\\n``."""
    docker = _docker("1\t2\tsrc/a.py\n")
    counts = await diff_line_counts(docker=docker, container_id="c1", base_branch="main\n")
    assert counts is None, f"a base ending in a newline must not be diffed: {counts}"
    docker.run_cmd.assert_not_awaited()


def _process(out: bytes) -> SimpleNamespace:
    """A finished command that printed ``out``."""
    stdout, stderr = asyncio.StreamReader(), asyncio.StreamReader()
    stdout.feed_data(out)
    stdout.feed_eof()
    stderr.feed_eof()

    async def wait() -> int:
        return 0

    async def communicate(input: bytes | None = None) -> tuple[bytes, bytes]:
        # What a whole-output read does: the client this replaced called it.
        return await stdout.read(), await stderr.read()

    return SimpleNamespace(
        stdout=stdout, stderr=stderr, stdin=None, returncode=0, pid=4242, wait=wait, communicate=communicate
    )


@pytest.mark.asyncio
async def test_a_change_of_90_000_files_is_listed_whole() -> None:
    """Past 4 MiB of paths the strict diff must not read as unavailable: QA would then select no app."""
    from embry0.execution.docker_client import DockerClient
    from embry0.execution.workspace_diff import WorkspaceDiffUnavailable

    listing = b"".join(b"apps/web/src/components/generated/file_%06d.tsx\n" % i for i in range(90_000))
    procs = [_process(b""), _process(listing)]  # the fetch, then the diff
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, side_effect=procs):
        try:
            files = await compute_changed_files_via_diff(
                docker=DockerClient(), container_id="c1", base_branch="main", strict=True
            )
        except WorkspaceDiffUnavailable as exc:
            pytest.fail(f"a 90,000-file change read as an unavailable diff: {exc}")
    assert len(files) == 90_000, f"listed {len(files)} of 90000 changed files"


# ---- with the forge URL: read there only, with bounded tries -------------------------

FORGE = "https://forge.example/acme/widgets.git"


def _texts(docker: Mock) -> list[str]:
    from tests.fixtures.sandbox_git_local import readable

    return [readable(call.args[0]) for call in docker.run_cmd.await_args_list]


@pytest.fixture
def pauses(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    from embry0.execution import sandbox_git

    taken: list[float] = []

    async def pause(seconds: float) -> None:
        taken.append(seconds)

    monkeypatch.setattr(sandbox_git, "_pause", pause, raising=False)
    return taken


@pytest.mark.asyncio
async def test_with_the_forge_url_the_base_is_fetched_there_forced(pauses: list[float]) -> None:
    docker = _docker("")
    docker.run_cmd = AsyncMock(
        side_effect=[RuntimeError("Docker command failed: the server answered 503"), "", "a.py\n"]
    )
    try:
        files = await compute_changed_files_via_diff(
            docker=docker, container_id="c1", base_branch="main", strict=True, forge_url=FORGE
        )
    except Exception as exc:  # noqa: BLE001 - the failure is the finding
        pytest.fail(f"one failed fetch decided the diff: {exc}")
    assert files == ["a.py"]
    fetches = [text for text in _texts(docker) if " fetch " in text]
    assert len(fetches) == 2 and all(
        f"fetch --no-recurse-submodules {FORGE} +main:refs/remotes/origin/main" in text for text in fetches
    ), f"the base comes from the forge's URL, forced, tried again after a failure: {fetches}"
    assert not any("fetch --no-recurse-submodules origin" in text for text in _texts(docker))
    assert pauses == [2.0], pauses


@pytest.mark.asyncio
async def test_with_the_forge_url_the_default_branch_is_the_forges(pauses: list[float]) -> None:
    docker = _docker(f"ref: refs/heads/trunk\tHEAD\n{'a' * 40}\tHEAD\n")
    assert await resolve_base_branch(docker=docker, container_id="c1", forge_url=FORGE) == "trunk"
    (text,) = _texts(docker)
    assert text.rstrip().endswith(f"ls-remote --symref {FORGE} HEAD"), text
    assert "symbolic-ref" not in text, "the workspace's own origin/HEAD is not read"


@pytest.mark.asyncio
async def test_with_the_forge_url_an_unreadable_default_branch_is_main(pauses: list[float]) -> None:
    from embry0.execution import sandbox_git

    docker = _docker(RuntimeError("Docker command failed: the server answered 503"))
    assert await resolve_base_branch(docker=docker, container_id="c1", forge_url=FORGE) == "main"
    tries = docker.run_cmd.await_count
    assert tries == getattr(sandbox_git, "FORGE_ATTEMPTS", None) == 3, f"tried a bounded number of times: {tries}"
