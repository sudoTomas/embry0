"""SkillSourceSync: git, path and inline sources into the store (spec 3.2)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.skills.store import SkillStore
from embry0.skills.sync import (
    SkillSourceSync,
    SkillSyncError,
    _git_env,
    parse_skill_frontmatter,
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def upstream(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "upstream"
    (repo / "skills" / "plan").mkdir(parents=True)
    (repo / ".claude-plugin").mkdir()
    (repo / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "fixture-plugin"}), encoding="utf-8")
    (repo / "skills" / "plan" / "SKILL.md").write_text(
        "---\nname: plan\ndescription: Plans the work.\n---\n# Plan\n", encoding="utf-8"
    )
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", ".")
    _git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "skills")
    _git(repo, "tag", "v1.0.0")
    sha = _git(repo, "rev-parse", "HEAD")
    return repo, sha


def _repo_mock(row: dict) -> MagicMock:
    repo = MagicMock()
    repo.get = AsyncMock(return_value=row)
    repo.mark_synced = AsyncMock(side_effect=lambda sid, **kw: {**row, **kw, "status": "ready"})
    repo.mark_error = AsyncMock(side_effect=lambda sid, error: {**row, "status": "error", "error": error})
    repo.list_all = AsyncMock(return_value=[row])
    return repo


async def test_git_sync_by_tag_publishes_tree_and_catalog(tmp_path: Path, upstream) -> None:
    repo_path, sha = upstream
    store = SkillStore(tmp_path / "skills")
    row = {"id": "fx", "kind": "git", "url": f"file://{repo_path}", "ref": "v1.0.0", "enabled": True}
    repo = _repo_mock(row)
    sync = SkillSourceSync(store, repo, MagicMock(), allow_file_urls_for_tests=True)
    out = await sync.sync("fx")
    assert out["status"] == "ready"
    kwargs = repo.mark_synced.await_args.kwargs
    assert kwargs["resolved_sha"] == sha
    assert kwargs["plugin_name"] == "fixture-plugin"
    assert kwargs["skills"] == [{"name": "plan", "description": "Plans the work.", "path": "skills/plan/SKILL.md"}]
    tree = store.tree_path("fx", sha)
    assert (tree / "skills" / "plan" / "SKILL.md").is_file()
    assert not (tree / ".git").exists(), "the .git directory is not published"


async def test_git_sync_by_sha(tmp_path: Path, upstream) -> None:
    repo_path, sha = upstream
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "fx", "kind": "git", "url": f"file://{repo_path}", "ref": sha, "enabled": True})
    await SkillSourceSync(store, repo, MagicMock(), allow_file_urls_for_tests=True).sync("fx")
    assert store.current_sha("fx") == sha


async def test_git_sync_never_reads_a_global_config_with_hooks_or_filters(
    tmp_path: Path, upstream, monkeypatch
) -> None:
    """The temp HOME git runs under carries a hostile .gitconfig; only GIT_CONFIG_GLOBAL keeps it out."""
    repo_path, _ = upstream
    sentinel = tmp_path / "sentinel"
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "post-checkout").write_text(f"#!/bin/sh\ntouch {sentinel}\n", encoding="utf-8")
    (hooks / "post-checkout").chmod(0o755)
    home = tmp_path / "home"

    def hostile_mkdtemp(prefix: str = "") -> str:
        home.mkdir()
        (home / ".gitconfig").write_text(
            f'[filter "evil"]\n\tsmudge = touch {sentinel}\n[core]\n\thooksPath = {hooks}\n', encoding="utf-8"
        )
        return str(home)

    monkeypatch.setattr("embry0.skills.sync.tempfile", SimpleNamespace(mkdtemp=hostile_mkdtemp))
    (repo_path / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
    _git(repo_path, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", ".gitattributes")
    _git(repo_path, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "attrs")
    _git(repo_path, "tag", "-f", "v1.0.0")
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "fx", "kind": "git", "url": f"file://{repo_path}", "ref": "v1.0.0", "enabled": True})
    out = await SkillSourceSync(store, repo, MagicMock(), allow_file_urls_for_tests=True).sync("fx")
    assert not sentinel.exists(), "a hook or filter from a global git config ran on the orchestrator"
    assert out["status"] == "ready", out


def test_git_env_is_built_from_scratch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    monkeypatch.setenv("GIT_SSH_COMMAND", "evil")
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'core.hooksPath'='/x'")
    env = _git_env(tmp_path)
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["HOME"] == str(tmp_path)
    assert {k for k in env if k.startswith("GIT_")} == {
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_TERMINAL_PROMPT",
        "GIT_ASKPASS",
    }


def _fake_git(tmp_path: Path, monkeypatch, body: str) -> Path:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    script = bin_dir / "git"
    script.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return bin_dir


async def _git_args(tmp_path: Path, monkeypatch, url: str, **kwargs) -> list[str]:
    args_file = tmp_path / "args"
    _fake_git(tmp_path, monkeypatch, f'printf "%s\\n" "$@" > {args_file}\nexit 1\n')
    repo = _repo_mock({"id": "fx", "kind": "git", "url": url, "ref": "v1.0.0", "enabled": True})

    async def public(host: str) -> list[str]:
        return ["93.184.216.34"]

    out = await SkillSourceSync(SkillStore(tmp_path / "skills"), repo, MagicMock(), resolve_host=public, **kwargs).sync(
        "fx"
    )
    assert out["status"] == "error"
    return args_file.read_text(encoding="utf-8").splitlines()


async def test_git_runs_with_every_safety_flag(tmp_path: Path, monkeypatch) -> None:
    args = await _git_args(tmp_path, monkeypatch, "https://skills.example.com/x.git")
    for flag in (
        "core.hooksPath=/dev/null",
        "protocol.allow=never",
        "protocol.https.allow=always",
        "http.followRedirects=false",
    ):
        assert flag in args and args[args.index(flag) - 1] == "-c", (flag, args)
    assert "--no-recurse-submodules" in args
    assert "protocol.file.allow=always" not in args


async def test_file_protocol_is_allowed_only_for_tests(tmp_path: Path, monkeypatch) -> None:
    args = await _git_args(tmp_path, monkeypatch, "file:///nowhere", allow_file_urls_for_tests=True)
    assert "protocol.file.allow=always" in args


async def test_file_urls_are_refused_without_the_test_flag(tmp_path: Path, upstream) -> None:
    repo_path, _ = upstream
    repo = _repo_mock({"id": "fx", "kind": "git", "url": f"file://{repo_path}", "ref": "v1.0.0", "enabled": True})
    store = SkillStore(tmp_path / "skills")
    out = await SkillSourceSync(store, repo, MagicMock()).sync("fx")
    assert out["error"] == "file:// urls are not accepted"
    assert store.current_sha("fx") is None


async def test_git_timeout_kills_the_whole_process_group(tmp_path: Path, monkeypatch) -> None:
    pid_file = tmp_path / "child.pid"
    _fake_git(tmp_path, monkeypatch, f"sleep 30 &\necho $! > {pid_file}\nsleep 30\n")
    repo = _repo_mock({"id": "fx", "kind": "git", "url": "file:///nowhere", "ref": "v1.0.0", "enabled": True})
    sync = SkillSourceSync(
        SkillStore(tmp_path / "skills"), repo, MagicMock(), git_timeout=0.5, allow_file_urls_for_tests=True
    )
    out = await asyncio.wait_for(sync.sync("fx"), timeout=5)
    assert out["status"] == "error" and "timed out" in out["error"]
    child = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.05)
    os.kill(child, 9)
    pytest.fail("git's background child outlived the timeout")


def _commit(repo: Path, msg: str) -> None:
    _git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", "-A")
    _git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", msg)


async def test_git_sync_refuses_a_committed_symlink(tmp_path: Path, upstream) -> None:
    repo_path, _ = upstream
    (repo_path / "skills" / "plan" / "secret.md").symlink_to("/etc/hosts")
    _commit(repo_path, "link")
    _git(repo_path, "tag", "-f", "v1.0.0")
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "fx", "kind": "git", "url": f"file://{repo_path}", "ref": "v1.0.0", "enabled": True})
    out = await SkillSourceSync(store, repo, MagicMock(), allow_file_urls_for_tests=True).sync("fx")
    assert out["error"] == "the tree contains a symlink: skills/plan/secret.md"
    assert store.current_sha("fx") is None


@pytest.mark.parametrize("kind", ["file", "directory"])
async def test_path_sync_refuses_a_symlink(tmp_path: Path, kind: str) -> None:
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "token").write_text("s3cret", encoding="utf-8")
    src = tmp_path / "mine"
    (src / "skills" / "house").mkdir(parents=True)
    (src / "skills" / "house" / "SKILL.md").write_text("---\nname: house\n---\n", encoding="utf-8")
    if kind == "file":
        (src / "skills" / "house" / "link").symlink_to(secret / "token")
        rel = "skills/house/link"
    else:
        (src / "skills" / "x").symlink_to(secret, target_is_directory=True)
        rel = "skills/x"
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "mine", "kind": "path", "url": str(src), "ref": "", "enabled": True})
    out = await SkillSourceSync(store, repo, MagicMock(), path_root=tmp_path).sync("mine")
    assert out["error"] == f"the tree contains a symlink: {rel}"
    assert store.current_sha("mine") is None
    assert not list((tmp_path / "skills").rglob("token")), "the symlink target was copied into the store"


async def test_an_unexpected_failure_marks_the_row_errored(tmp_path: Path) -> None:
    src = tmp_path / "mine"
    (src / "skills" / "house").mkdir(parents=True)
    (src / "skills" / "house" / "SKILL.md").write_text("---\nname: house\n---\n", encoding="utf-8")
    (src / ".claude-plugin").mkdir()
    (src / ".claude-plugin" / "plugin.json").write_bytes(b"\xff\xfe not utf-8")
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "mine", "kind": "path", "url": str(src), "ref": "", "enabled": True})
    out = await SkillSourceSync(store, repo, MagicMock(), path_root=tmp_path).sync("mine")
    assert out["status"] == "error" and out["error"]
    assert store.current_sha("mine") is None


async def test_git_sync_refuses_a_tree_without_skills(tmp_path: Path) -> None:
    repo_path = tmp_path / "empty"
    repo_path.mkdir()
    (repo_path / "README.md").write_text("no skills\n", encoding="utf-8")
    _git(repo_path, "init", "-q", "-b", "main")
    _git(repo_path, "-c", "user.email=t@example.com", "-c", "user.name=t", "add", ".")
    _git(repo_path, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "x")
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "fx", "kind": "git", "url": f"file://{repo_path}", "ref": "main", "enabled": True})
    out = await SkillSourceSync(store, repo, MagicMock(), allow_file_urls_for_tests=True).sync("fx")
    assert out["status"] == "error"
    assert "no skills/*/SKILL.md" in repo.mark_error.await_args.args[1]
    assert store.current_sha("fx") is None


async def test_git_sync_refuses_an_oversized_tree(tmp_path: Path, upstream) -> None:
    repo_path, _ = upstream
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "fx", "kind": "git", "url": f"file://{repo_path}", "ref": "v1.0.0", "enabled": True})
    out = await SkillSourceSync(store, repo, MagicMock(), max_tree_bytes=10, allow_file_urls_for_tests=True).sync("fx")
    assert out["status"] == "error" and "larger than" in out["error"]


async def test_git_sync_timeout_is_an_error_not_a_hang(tmp_path: Path, upstream) -> None:
    repo_path, _ = upstream
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "fx", "kind": "git", "url": f"file://{repo_path}", "ref": "v1.0.0", "enabled": True})
    sync = SkillSourceSync(store, repo, MagicMock(), git_timeout=0.0001, allow_file_urls_for_tests=True)
    out = await sync.sync("fx")
    assert out["status"] == "error" and "timed out" in out["error"]


async def test_failed_sync_keeps_the_previous_tree(tmp_path: Path, upstream) -> None:
    repo_path, sha = upstream
    store = SkillStore(tmp_path / "skills")
    row = {"id": "fx", "kind": "git", "url": f"file://{repo_path}", "ref": "v1.0.0", "enabled": True}
    repo = _repo_mock(row)
    sync = SkillSourceSync(store, repo, MagicMock(), allow_file_urls_for_tests=True)
    await sync.sync("fx")
    row["ref"] = "does-not-exist"
    out = await sync.sync("fx")
    assert out["status"] == "error"
    assert store.current_sha("fx") == sha and store.tree_path("fx", sha).is_dir()


def _gated_inline(first_rows: list[dict], gate: asyncio.Event, entered: asyncio.Event) -> MagicMock:
    """Inline rows whose first read waits for ``gate``: a sync held mid-render."""
    inline = MagicMock()
    state = {"rows": first_rows, "calls": 0}

    async def list_all() -> list[dict]:
        state["calls"] += 1
        rows = list(state["rows"])
        if state["calls"] == 1:
            entered.set()
            await gate.wait()
        return rows

    inline.list_all = AsyncMock(side_effect=list_all)
    inline.state = state
    return inline


async def test_a_sync_requested_during_a_run_runs_once_more_and_reads_the_row_afresh(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    current = {"row": {"id": "local", "kind": "inline", "url": "", "ref": "", "enabled": True, "marker": 1}}
    repo = _repo_mock(current["row"])
    repo.get = AsyncMock(side_effect=lambda sid: current["row"])
    gate, entered = asyncio.Event(), asyncio.Event()
    inline = _gated_inline([], gate, entered)
    sync = SkillSourceSync(store, repo, inline)
    filled: list[dict] = []
    real_fill = sync._fill

    async def counting_fill(row, staged):
        filled.append(row)
        return await real_fill(row, staged)

    sync._fill = counting_fill  # type: ignore[method-assign]
    first = asyncio.create_task(sync.sync("local"))
    await entered.wait()
    assert sync.is_syncing("local")
    current["row"] = {**current["row"], "marker": 2}
    later = [asyncio.create_task(sync.sync("local")) for _ in range(3)]
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(first, *later)
    assert [row["marker"] for row in filled] == [1, 2], "requests during a run are one more run, on the new row"
    assert all(r == results[0] for r in results), "every caller gets the row the last run left"
    assert not sync.is_syncing("local")


async def test_an_inline_write_during_a_render_is_not_lost(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "local", "kind": "inline", "url": "", "ref": "", "enabled": True})
    gate, entered = asyncio.Event(), asyncio.Event()
    inline = _gated_inline([{"name": "old", "description": "Old.", "body": "old\n"}], gate, entered)
    sync = SkillSourceSync(store, repo, inline)
    first = asyncio.create_task(sync.sync("local"))
    await entered.wait()
    inline.state["rows"] = [{"name": "new", "description": "New.", "body": "new\n"}]
    second = asyncio.create_task(sync.sync("local"))  # the PUT /skills/inline/{name} that lands now
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(first, second)
    tree = store.tree_path("local", store.current_sha("local"))
    assert [p.parent.name for p in (tree / "skills").glob("*/SKILL.md")] == ["new"], "the second write was dropped"
    assert repo.mark_synced.await_args.kwargs["skills"][0]["name"] == "new"


async def test_after_publish_runs_after_a_publish_and_never_after_a_failure(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    row = {"id": "local", "kind": "inline", "url": "", "ref": "", "enabled": True}
    repo = _repo_mock(row)
    inline = MagicMock()
    inline.list_all = AsyncMock(return_value=[])
    after = AsyncMock(side_effect=RuntimeError("daemon down"))
    sync = SkillSourceSync(store, repo, inline, after_publish=after)
    out = await sync.sync("local")
    assert out["status"] == "ready", "a failing collection does not fail the sync"
    after.assert_awaited_once()
    row["kind"] = "nonsense"
    out = await sync.sync("local")
    assert out["status"] == "error"
    after.assert_awaited_once()


def _https_sync(tmp_path: Path, addresses: list[str]) -> tuple[SkillSourceSync, MagicMock, AsyncMock]:
    repo = _repo_mock(
        {"id": "fx", "kind": "git", "url": "https://skills.example.com/x.git", "ref": "v1.0.0", "enabled": True}
    )

    async def resolve(host: str) -> list[str]:
        assert host == "skills.example.com"
        return addresses

    sync = SkillSourceSync(SkillStore(tmp_path / "skills"), repo, MagicMock(), resolve_host=resolve)
    git = AsyncMock(side_effect=SkillSyncError("git reached"))
    sync._git = git  # type: ignore[method-assign]
    return sync, repo, git


@pytest.mark.parametrize(
    "addresses",
    [
        ["10.0.0.5"],
        ["127.0.0.1"],
        ["169.254.169.254"],
        ["::1"],
        ["fd00::1"],
        ["0.0.0.0"],
        ["224.0.0.1"],
        ["100.64.0.1"],
        ["64:ff9b::a00:5"],
        ["93.184.216.34", "10.0.0.5"],
    ],
)
async def test_git_sync_refuses_a_host_that_resolves_to_a_private_address(tmp_path: Path, addresses) -> None:
    sync, repo, git = _https_sync(tmp_path, addresses)
    out = await sync.sync("fx")
    assert out["status"] == "error"
    assert out["error"] == "skills.example.com resolves to a private, loopback or link-local address"
    git.assert_not_awaited()


async def test_git_sync_refuses_a_host_that_does_not_resolve(tmp_path: Path) -> None:
    sync, _, git = _https_sync(tmp_path, [])
    out = await sync.sync("fx")
    assert out["error"] == "skills.example.com does not resolve"
    git.assert_not_awaited()


async def test_git_sync_lets_a_public_address_through_to_git(tmp_path: Path) -> None:
    sync, _, git = _https_sync(tmp_path, ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"])
    out = await sync.sync("fx")
    assert out["error"] == "git reached"
    git.assert_awaited()


async def test_path_sync_copies_and_generates_plugin_json(tmp_path: Path) -> None:
    src = tmp_path / "mine"
    (src / "skills" / "house").mkdir(parents=True)
    (src / "skills" / "house" / "SKILL.md").write_text(
        "---\nname: house\ndescription: House style.\n---\n", encoding="utf-8"
    )
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "mine", "kind": "path", "url": str(src), "ref": "", "enabled": True})
    out = await SkillSourceSync(store, repo, MagicMock(), path_root=tmp_path).sync("mine")
    assert out["status"] == "ready"
    sha = repo.mark_synced.await_args.kwargs["resolved_sha"]
    plugin = json.loads((store.tree_path("mine", sha) / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["name"] == "mine"
    assert repo.mark_synced.await_args.kwargs["plugin_name"] == "mine"


async def test_inline_sync_renders_rows(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "local", "kind": "inline", "url": "", "ref": "", "enabled": True})
    inline = MagicMock()
    inline.list_all = AsyncMock(
        return_value=[{"name": "house-style", "description": "How we write.", "body": "# Rules\n"}]
    )
    out = await SkillSourceSync(store, repo, inline).sync("local")
    assert out["status"] == "ready"
    sha = repo.mark_synced.await_args.kwargs["resolved_sha"]
    text = (store.tree_path("local", sha) / "skills" / "house-style" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith('---\nname: "house-style"\ndescription: "How we write."\n---\n# Rules\n')
    assert repo.mark_synced.await_args.kwargs["plugin_name"] == "local"


async def test_inline_sync_with_no_rows_is_an_empty_but_ready_plugin(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "local", "kind": "inline", "url": "", "ref": "", "enabled": True})
    inline = MagicMock()
    inline.list_all = AsyncMock(return_value=[])
    out = await SkillSourceSync(store, repo, inline).sync("local")
    assert out["status"] == "ready" and repo.mark_synced.await_args.kwargs["skills"] == []


async def test_sync_missing_syncs_only_enabled_sources_without_a_tree(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    (store.source_dir("have")).mkdir(parents=True)
    (store.source_dir("have") / "current").write_text("a" * 40 + "\n", encoding="utf-8")
    repo = MagicMock()
    repo.list_all = AsyncMock(
        return_value=[
            {"id": "have", "enabled": True},
            {"id": "off", "enabled": False},
            {"id": "need", "enabled": True},
        ]
    )
    after = AsyncMock()
    sync = SkillSourceSync(store, repo, MagicMock(), after_publish=after, startup_retry_delays=())
    sync.sync = AsyncMock()  # type: ignore[method-assign]
    await sync.sync_missing()
    assert [c.args for c in sync.sync.await_args_list] == [("need",)]
    after.assert_awaited_once()  # startup collects the trees left stale before the restart


async def test_sync_missing_never_raises(tmp_path: Path) -> None:
    """A startup background task must not die with an unretrieved exception."""
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=[{"id": "a", "enabled": True}, {"id": "b", "enabled": True}])
    sync = SkillSourceSync(SkillStore(tmp_path / "skills"), repo, MagicMock(), startup_retry_delays=())
    sync.sync = AsyncMock(side_effect=[PermissionError("/skills is read-only"), {"status": "ready"}])  # type: ignore[method-assign]
    await sync.sync_missing()
    assert sync.sync.await_count == 2, "one failing source does not stop the next"
    repo.list_all = AsyncMock(side_effect=OSError("database down"))
    await sync.sync_missing()


def test_inline_frontmatter_round_trips_awkward_text() -> None:
    from embry0.skills.sync import _render_skill_md

    text = _render_skill_md("house-style", 'line one\r\nkey: value\n> folded "quoted"', "# Body\n")
    assert text.endswith("---\n# Body\n")
    assert parse_skill_frontmatter(text) == ("house-style", 'line one key: value > folded "quoted"')


def test_parse_skill_frontmatter() -> None:
    assert parse_skill_frontmatter("---\nname: a\ndescription: B c.\n---\nbody") == ("a", "B c.")
    assert parse_skill_frontmatter("# no frontmatter") == (None, "")
    assert parse_skill_frontmatter("---\ndescription: 'quoted'\n---\n") == (None, "quoted")


def _publish_side_effect(store: SkillStore, succeed_on: int):
    """A stand-in for sync() that publishes a tree on the ``succeed_on``-th call."""
    calls = {"n": 0}

    async def fake_sync(source_id: str) -> dict:
        calls["n"] += 1
        if calls["n"] < succeed_on:
            return {"id": source_id, "status": "error"}
        (store.source_dir(source_id)).mkdir(parents=True, exist_ok=True)
        (store.source_dir(source_id) / "current").write_text("a" * 40 + "\n", encoding="utf-8")
        return {"id": source_id, "status": "ready"}

    return fake_sync


async def test_a_failed_startup_sync_is_retried_with_backoff(tmp_path: Path, monkeypatch) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("embry0.skills.sync._sleep", fake_sleep)
    store = SkillStore(tmp_path / "skills")
    repo = MagicMock()
    repo.list_all = AsyncMock(return_value=[{"id": "need", "enabled": True}])
    sync = SkillSourceSync(store, repo, MagicMock())
    sync.sync = AsyncMock(side_effect=_publish_side_effect(store, succeed_on=3))  # type: ignore[method-assign]
    await sync.sync_missing()
    assert slept == [60.0, 300.0], "retried after 60 s and 300 s, and stopped once the tree exists"
    assert sync.sync.await_count == 3


async def test_startup_sync_retries_stop_after_the_last_delay(tmp_path: Path, monkeypatch) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("embry0.skills.sync._sleep", fake_sleep)
    repo = MagicMock()
    repo.list_all = AsyncMock(side_effect=OSError("database down"))
    sync = SkillSourceSync(SkillStore(tmp_path / "skills"), repo, MagicMock())
    await sync.sync_missing()
    assert slept == [60.0, 300.0, 900.0]
    assert repo.list_all.await_count == 4, "an unreadable registry at startup is retried too"


def _path_source(tmp_path: Path, name: str = "mine") -> Path:
    src = tmp_path / "root" / name
    (src / "skills" / "house").mkdir(parents=True)
    (src / "skills" / "house" / "SKILL.md").write_text("---\nname: house\n---\n", encoding="utf-8")
    return src


async def _sync_path(tmp_path: Path, url: str, **kwargs) -> tuple[dict, SkillStore, MagicMock]:
    store = SkillStore(tmp_path / "skills")
    repo = _repo_mock({"id": "mine", "kind": "path", "url": url, "ref": "", "enabled": True})
    out = await asyncio.wait_for(SkillSourceSync(store, repo, MagicMock(), **kwargs).sync("mine"), timeout=10)
    return out, store, repo


async def test_a_path_source_outside_the_path_root_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    (outside / "skills" / "x").mkdir(parents=True)
    (outside / "skills" / "x" / "SKILL.md").write_text("---\nname: x\n---\n", encoding="utf-8")
    (tmp_path / "root").mkdir()
    (tmp_path / "root" / "link").symlink_to(outside, target_is_directory=True)
    for url in (str(outside), str(tmp_path / "root" / "link"), f"{tmp_path}/root/../elsewhere"):
        out, store, _ = await _sync_path(tmp_path, url, path_root=tmp_path / "root")
        assert out["error"] == "path sources must live under SKILLS_PATH_ROOT", url
        assert store.current_sha("mine") is None


async def test_path_sources_are_refused_without_a_path_root(tmp_path: Path) -> None:
    out, store, _ = await _sync_path(tmp_path, str(_path_source(tmp_path)))
    assert out["error"] == "path sources are refused: SKILLS_PATH_ROOT is not set"
    assert store.current_sha("mine") is None


async def test_a_fifo_in_a_path_source_is_refused(tmp_path: Path) -> None:
    src = _path_source(tmp_path)
    os.mkfifo(src / "skills" / "house" / "pipe")
    out, store, _ = await _sync_path(tmp_path, str(src), path_root=tmp_path / "root")
    assert out["error"] == "the tree contains a file that is not a regular file: skills/house/pipe"
    assert store.current_sha("mine") is None


async def test_a_path_source_comes_out_readable_by_the_sandbox(tmp_path: Path) -> None:
    src = _path_source(tmp_path)
    (src / "skills" / "house" / "private.md").write_text("x", encoding="utf-8")
    (src / "skills" / "house" / "private.md").chmod(0o600)
    (src / "skills" / "house" / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (src / "skills" / "house" / "run.sh").chmod(0o4700)
    (src / "skills" / "house").chmod(0o700)
    out, store, repo = await _sync_path(tmp_path, str(src), path_root=tmp_path / "root")
    assert out["status"] == "ready", out
    tree = store.tree_path("mine", repo.mark_synced.await_args.kwargs["resolved_sha"])
    assert (tree / "skills" / "house" / "private.md").stat().st_mode & 0o7777 == 0o644
    assert (tree / "skills" / "house" / "run.sh").stat().st_mode & 0o7777 == 0o755, "no setuid; exec kept"
    assert (tree / "skills" / "house").stat().st_mode & 0o7777 == 0o755


async def test_the_size_cap_aborts_a_path_copy_midway(tmp_path: Path) -> None:
    src = _path_source(tmp_path)
    (src / "skills" / "house" / "a.bin").write_bytes(b"x" * 600)
    (src / "skills" / "house" / "b.bin").write_bytes(b"x" * 600)
    out, store, _ = await _sync_path(tmp_path, str(src), path_root=tmp_path / "root", max_tree_bytes=1000)
    assert out["error"] == "the tree is larger than the 1000 byte cap", "the cap was not applied during the copy"
    assert store.current_sha("mine") is None


@pytest.mark.parametrize("name", ["House", "a:b", "a,b", "a(b)", "a b", "-lead", "x" * 65])
async def test_a_skill_name_outside_the_grammar_is_refused(tmp_path: Path, name: str) -> None:
    src = _path_source(tmp_path)
    (src / "skills" / "house" / "SKILL.md").write_text(f'---\nname: "{name}"\n---\n', encoding="utf-8")
    out, _, _ = await _sync_path(tmp_path, str(src), path_root=tmp_path / "root")
    assert out["status"] == "error"
    assert out["error"].startswith(f"skill name {name!r} in skills/house/SKILL.md must match"), out["error"]


@pytest.mark.parametrize("name", ["Fixture", "a:b", "a.b", "a b", "a,b"])
async def test_a_plugin_name_outside_the_grammar_is_refused(tmp_path: Path, name: str) -> None:
    src = _path_source(tmp_path)
    (src / ".claude-plugin").mkdir()
    (src / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")
    out, _, _ = await _sync_path(tmp_path, str(src), path_root=tmp_path / "root")
    assert out["error"].startswith(f"plugin name {name!r} must match"), out["error"]


@pytest.mark.parametrize(
    ("other", "error"),
    [
        ({"id": "other", "plugin_name": "mine"}, "plugin name mine is already used by source other"),
        ({"id": "mine-too", "plugin_name": None}, "plugin name mine-too is already used by source mine-too"),
    ],
)
async def test_a_plugin_name_another_source_uses_is_refused(tmp_path: Path, other: dict, error: str) -> None:
    src = _path_source(tmp_path)
    if other["id"] == "mine-too":
        (src / ".claude-plugin").mkdir()
        (src / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "mine-too"}), encoding="utf-8")
    store = SkillStore(tmp_path / "skills")
    row = {"id": "mine", "kind": "path", "url": str(src), "ref": "", "enabled": True, "plugin_name": None}
    repo = _repo_mock(row)
    repo.list_all = AsyncMock(return_value=[row, other])
    out = await SkillSourceSync(store, repo, MagicMock(), path_root=tmp_path / "root").sync("mine")
    assert out["error"] == error
    assert store.current_sha("mine") is None


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("{not json", ".claude-plugin/plugin.json is not JSON"),
        (json.dumps({"version": "1.0.0"}), ".claude-plugin/plugin.json has no name"),
        (json.dumps(["superpowers"]), ".claude-plugin/plugin.json has no name"),
    ],
)
async def test_a_broken_plugin_json_fails_the_sync(tmp_path: Path, content: str, error: str) -> None:
    src = _path_source(tmp_path)
    (src / ".claude-plugin").mkdir()
    (src / ".claude-plugin" / "plugin.json").write_text(content, encoding="utf-8")
    out, store, _ = await _sync_path(tmp_path, str(src), path_root=tmp_path / "root")
    assert out["status"] == "error" and out["error"].startswith(error), out["error"]
    assert store.current_sha("mine") is None


async def test_git_is_pinned_to_the_addresses_that_were_checked(tmp_path: Path, monkeypatch) -> None:
    args = await _git_args(tmp_path, monkeypatch, "https://skills.example.com/x.git")
    pin = "http.curloptResolve=skills.example.com:443:93.184.216.34"
    assert pin in args and args[args.index(pin) - 1] == "-c", args


def test_curl_resolve_brackets_ipv6_and_drops_duplicates() -> None:
    from embry0.skills.sync import _curl_resolve

    assert _curl_resolve("h.example.com", 8443, ["93.184.216.34", "2606:2800::1", "93.184.216.34"]) == (
        "h.example.com:8443:93.184.216.34,[2606:2800::1]"
    )


async def test_a_skill_name_ending_in_a_newline_is_refused(tmp_path: Path) -> None:
    """Python's ``$`` also matches just before a final newline, so ``match`` took ``house\\n``."""
    src = _path_source(tmp_path)
    (src / "skills" / "house" / "SKILL.md").write_text('---\nname: "house\\n"\n---\n', encoding="utf-8")
    out, _, _ = await _sync_path(tmp_path, str(src), path_root=tmp_path / "root")
    assert out["status"] == "error", f"a skill name ending in a newline must fail the sync: {out}"
    assert out["error"].startswith("skill name 'house\\n' in skills/house/SKILL.md must match"), out["error"]
