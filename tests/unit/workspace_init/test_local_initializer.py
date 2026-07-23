"""Local initializer: allowlist enforcement + tar filter defenses."""

import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from embry0.workspace_init.base import InitContext, LocalContextDeniedError
from embry0.workspace_init.local import LocalWorkspaceInitializer, _resolve_allowed, build_workspace_tar


def _config(allowlist: str, **kw):
    return SimpleNamespace(
        local_context_allowlist=allowlist,
        local_context_max_bytes=kw.get("max_bytes", 209_715_200),
        local_context_max_files=kw.get("max_files", 50_000),
    )


# ---- allowlist -------------------------------------------------------------


def test_empty_allowlist_denies(tmp_path):
    with pytest.raises(LocalContextDeniedError, match="fail closed"):
        _resolve_allowed(str(tmp_path), _config(""))


def test_none_config_denies(tmp_path):
    with pytest.raises(LocalContextDeniedError):
        _resolve_allowed(str(tmp_path), None)


def test_path_outside_allowlist_denies(tmp_path):
    allowed = tmp_path / "allowed"
    other = tmp_path / "other"
    allowed.mkdir()
    other.mkdir()
    with pytest.raises(LocalContextDeniedError, match="outside the allowlist"):
        _resolve_allowed(str(other), _config(str(allowed)))


def test_path_inside_allowlist_allows(tmp_path):
    sub = tmp_path / "data"
    sub.mkdir()
    assert _resolve_allowed(str(sub), _config(str(tmp_path))) == sub


def test_symlink_escaping_allowlist_denies(tmp_path):
    allowed = tmp_path / "allowed"
    secret = tmp_path / "secret"
    allowed.mkdir()
    secret.mkdir()
    link = allowed / "leak"
    link.symlink_to(secret)
    # Resolution follows the symlink out of the allowlisted root.
    with pytest.raises(LocalContextDeniedError, match="outside the allowlist"):
        _resolve_allowed(str(link), _config(str(allowed)))


def test_nonexistent_path_denies(tmp_path):
    with pytest.raises(LocalContextDeniedError, match="does not resolve"):
        _resolve_allowed(str(tmp_path / "missing"), _config(str(tmp_path)))


# ---- tar filter ------------------------------------------------------------


def _members(tar_path: Path) -> dict[str, tarfile.TarInfo]:
    with tarfile.open(tar_path) as tar:
        return {m.name: m for m in tar.getmembers()}


def test_tar_ships_regular_files_and_inside_symlinks(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("hello")
    (src / "sub" / "b.txt").write_text("world")
    (src / "link_in").symlink_to(src / "a.txt")

    out = tmp_path / "out.tar"
    files, size = build_workspace_tar(src, out, max_bytes=10_000, max_files=100)
    members = _members(out)
    assert {"a.txt", "sub/b.txt", "link_in"} <= set(members)
    assert members["link_in"].issym()
    assert files == 3  # two files + one symlink counted
    assert size == 10  # 5 + 5 bytes


def test_tar_drops_escaping_and_dangling_symlinks(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (tmp_path / "outside.txt").write_text("secret")
    (src / "escape").symlink_to(tmp_path / "outside.txt")
    (src / "dangling").symlink_to(src / "missing.txt")
    (src / "ok.txt").write_text("x")

    out = tmp_path / "out.tar"
    build_workspace_tar(src, out, max_bytes=10_000, max_files=100)
    members = _members(out)
    assert "escape" not in members
    assert "dangling" not in members
    assert "ok.txt" in members


def test_tar_byte_cap_enforced(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "big.bin").write_bytes(b"x" * 1000)
    with pytest.raises(LocalContextDeniedError, match="exceeds 100 bytes"):
        build_workspace_tar(src, tmp_path / "out.tar", max_bytes=100, max_files=100)


def test_tar_file_cap_enforced(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for i in range(5):
        (src / f"f{i}.txt").write_text("x")
    with pytest.raises(LocalContextDeniedError, match="exceeds 3 files"):
        build_workspace_tar(src, tmp_path / "out.tar", max_bytes=10_000, max_files=3)


def test_single_file_source(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("# hi")
    out = tmp_path / "out.tar"
    build_workspace_tar(f, out, max_bytes=10_000, max_files=10)
    assert set(_members(out)) == {"doc.md"}


# ---- initialize wiring -----------------------------------------------------


@pytest.mark.asyncio
async def test_initialize_streams_tar_into_workspace(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("hello")

    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd, **kw: ["docker", "exec", cid, *cmd])
    docker.run_cmd = AsyncMock(return_value="")
    captured: dict = {}

    async def _exec_with_stdin(container, command, stdin_path, timeout=300):
        captured["command"] = command
        captured["tar_members"] = set(_members(Path(stdin_path)))

    docker.exec_with_stdin = AsyncMock(side_effect=_exec_with_stdin)

    ctx = InitContext(
        job_id="job-1",
        context={"type": "local", "path": str(src)},
        container_id="c1",
        sandbox_token="t",
        docker=docker,
        config=_config(str(tmp_path)),
    )
    await LocalWorkspaceInitializer().initialize(ctx)
    assert captured["command"] == ["tar", "-xpf", "-", "-C", "/workspace"]
    assert captured["tar_members"] == {"a.txt"}
