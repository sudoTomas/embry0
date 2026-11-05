"""sandbox_files: staging bytes in as root, reading bytes out as the agent.

Two layers: the command shape with a fake docker (write runs as root from
stdin into a root-only drop; read runs as the agent, isolated, cwd ``/``), and
the embedded scripts run for real on the host (they are plain ``python3 -I``),
proving the write makes a fresh root-only reader-readable file and the read
refuses a link, a non-regular file (without blocking on a FIFO), a file past
its limit, a missing file and one it may not read, each distinctly. The
cross-container, cap-dropped behaviour is proven in
``tests/integration/test_sandbox_files_e2e.py``.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from embry0.execution import sandbox_files
from embry0.execution.sandbox_files import SandboxReadRefused


class _FakeDocker:
    def __init__(self, write_returns: str = "/run/embry0/drop/t/f", read_returns: bytes = b"") -> None:
        self.calls: list[dict] = []
        self._w, self._r = write_returns, read_returns

    def build_exec_cmd(self, container, command, **kw):
        user = ["-u", kw["user"]] if kw.get("user") else []
        wd = ["-w", kw["workdir"]] if kw.get("workdir") else []
        interactive = ["-i"] if kw.get("interactive") else []
        return ["docker", "exec", *interactive, *user, *wd, container, *command]

    async def run_cmd(self, cmd, timeout=60, stdin_data=None):
        self.calls.append({"cmd": cmd, "timeout": timeout, "stdin_data": stdin_data})
        return self._w

    async def run_cmd_bytes(self, cmd, timeout=60, max_bytes=None):
        self.calls.append({"cmd": cmd, "timeout": timeout, "max_bytes": max_bytes})
        return self._r


# ---- command shape --------------------------------------------------------


async def test_put_writes_as_root_isolated_from_stdin_into_a_root_only_drop():
    docker = _FakeDocker(write_returns="/run/embry0/drop/abc/areas.yml\n")
    path = await sandbox_files.put_bytes_for_reader(docker, "C1", b"payload", name="areas.yml")
    assert path == "/run/embry0/drop/abc/areas.yml"
    (call,) = docker.calls
    cmd = call["cmd"]
    assert cmd[:7] == ["docker", "exec", "-i", "-u", "root", "-w", "/"], "root, isolated, cwd /"
    assert cmd[7] == "C1" and cmd[8:11] == ["python3", "-I", "-c"]
    assert cmd[11] == sandbox_files._WRITE
    assert cmd[12] == sandbox_files.DROP_ROOT and cmd[14:] == ["areas.yml", "644"]
    assert cmd[13] and cmd[13] != "areas.yml", "a fresh random drop token"
    assert call["stdin_data"] == b"payload" and not any("payload" in str(w) for w in cmd)


@pytest.mark.parametrize("bad", ["a/b", "/abs", "", ".", "..", "x/"])
async def test_put_refuses_a_name_that_is_not_a_bare_file(bad):
    with pytest.raises(ValueError, match="bare filename"):
        await sandbox_files.put_bytes_for_reader(_FakeDocker(), "C1", b"x", name=bad)


async def test_read_runs_as_the_agent_isolated_cwd_root_with_a_limit():
    docker = _FakeDocker(read_returns=b"\x89PNG")
    data = await sandbox_files.read_regular_file(docker, "C1", "/tmp/shot.png", limit=1234)
    assert data == b"\x89PNG"
    cmd = docker.calls[0]["cmd"]
    assert cmd[:6] == ["docker", "exec", "-u", "agent", "-w", "/"], "read as the agent, not root"
    assert cmd[7:10] == ["python3", "-I", "-c"] and cmd[10] == sandbox_files._READ
    assert cmd[-2:] == ["/tmp/shot.png", "1234"]


async def test_read_caps_what_the_orchestrator_buffers():
    # The orchestrator-side cap: the pipe may carry the limit plus a few bytes, never more.
    docker = _FakeDocker(read_returns=b"x")
    await sandbox_files.read_regular_file(docker, "C1", "/x", limit=1024)
    max_bytes = docker.calls[0]["max_bytes"]
    assert max_bytes is not None and 1024 <= max_bytes <= 1024 + 4096, f"run_cmd_bytes got max_bytes={max_bytes}"


async def test_a_pipe_past_the_cap_is_refused_as_too_large():
    from embry0.execution import docker_client

    OutputTooLargeError = getattr(docker_client, "OutputTooLargeError", None)
    assert OutputTooLargeError is not None, "a read past its cap must be refused (docker_client.OutputTooLargeError)"

    class _Flooded(_FakeDocker):
        async def run_cmd_bytes(self, cmd, timeout=60, max_bytes=None):
            raise OutputTooLargeError(f"command output passed its {max_bytes}-byte cap")

    with pytest.raises(SandboxReadRefused) as caught:
        await sandbox_files.read_regular_file(_Flooded(), "C1", "/x", limit=1024)
    assert caught.value.reason == "too_large"


async def test_bytes_over_the_limit_but_inside_the_slack_are_still_refused():
    # Another process can write into the reader's pipe; anything past the limit is refused.
    docker = _FakeDocker(read_returns=b"x" * 1030)
    with pytest.raises(SandboxReadRefused) as caught:
        await sandbox_files.read_regular_file(docker, "C1", "/x", limit=1024)
    assert caught.value.reason == "too_large"


@pytest.mark.parametrize(
    ("exit_code", "reason"),
    [(3, "link"), (4, "missing"), (5, "permission_denied"), (6, "not_regular"), (7, "too_large")],
)
async def test_read_maps_each_exit_to_a_distinct_reason(exit_code, reason):
    class _Fail(_FakeDocker):
        async def run_cmd_bytes(self, cmd, timeout=60, max_bytes=None):
            raise RuntimeError(f"Docker command failed: exit code {exit_code}")

    with pytest.raises(SandboxReadRefused) as caught:
        await sandbox_files.read_regular_file(_Fail(), "C1", "/x", limit=10)
    assert caught.value.reason == reason


def test_place_as_reader_script_never_follows_a_link_at_the_destination():
    frag = sandbox_files.place_as_reader_script("/run/embry0/drop/t/f", "/home/agent/.claude/x/s.jsonl")
    assert (
        frag
        == "mkdir -p /home/agent/.claude/x && cp --remove-destination /run/embry0/drop/t/f /home/agent/.claude/x/s.jsonl"
    )


def test_drop_root_sits_under_a_root_only_parent_not_tmp():
    # Under /run (root-owned and not agent-writable in the sandbox image), never /tmp:
    # a /tmp drop could be planted by the agent before the write.
    assert sandbox_files.DROP_ROOT == "/run/embry0/drop"
    assert sandbox_files.DROP_ROOT.startswith("/run/")
    assert not sandbox_files.DROP_ROOT.startswith("/tmp")


def test_the_write_opens_a_fresh_file_without_following_a_link():
    # Source invariants: a fresh drop dir + O_EXCL + O_NOFOLLOW together mean no
    # path the agent controls is ever written through. (Behaviourally
    # indistinguishable in a fresh root-only dir, so pinned here.)
    assert "os.mkdir(dropdir" in sandbox_files._WRITE, "the drop dir is made fresh, not reused"
    assert "O_EXCL" in sandbox_files._WRITE and "O_NOFOLLOW" in sandbox_files._WRITE
    assert "os.fchown" not in sandbox_files._WRITE, "no chown: the file stays root-owned and reader-readable"


def test_the_read_opens_no_follow_and_non_blocking():
    assert "O_NOFOLLOW" in sandbox_files._READ and "O_NONBLOCK" in sandbox_files._READ
    assert "S_ISREG" in sandbox_files._READ, "a non-regular file is refused"


_HEX32 = "a" * 32  # a drop directory name: 32 lowercase hex digits


@pytest.mark.parametrize(
    "path",
    [f"/run/embry0/drop/{_HEX32}/areas.yml", f"/run/embry0/./drop//{_HEX32}/areas.yml"],
    ids=["exact", "an equivalent spelling, normalised"],
)
async def test_discard_drop_removes_the_dir_as_root(path):
    docker = _FakeDocker()
    await sandbox_files.discard_drop(docker, "C1", path)
    (call,) = docker.calls
    assert call["cmd"] == ["docker", "exec", "-u", "root", "-w", "/", "C1", "rm", "-rf", f"/run/embry0/drop/{_HEX32}"]


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        f"/run/embry0/drop/{_HEX32}/../../../etc/x",  # .. climbs out
        f"/run/embry0/drop/{_HEX32}/..",
        "/run/embry0/drop/../git/config",
        "/run/embry0/drop/tok/areas.yml",  # not a drop token
        f"/run/embry0/drop/{_HEX32}",  # the directory itself, no file
        f"/run/embry0/drop/{_HEX32}/sub/f",  # one level too deep
        f"/run/embry0/drop/{_HEX32.upper()}/f",
    ],
)
async def test_discard_drop_ignores_anything_but_an_exact_drop_path(path):
    docker = _FakeDocker()
    await sandbox_files.discard_drop(docker, "C1", path)
    assert docker.calls == [], f"discard must not touch {path!r}"


# ---- the embedded scripts, run for real on the host -----------------------


def _run_write(drop_root: Path, name: str, mode: str, data: bytes):
    return subprocess.run(
        [sys.executable, "-I", "-c", sandbox_files._WRITE, str(drop_root), "tok0", name, mode],
        input=data,
        capture_output=True,
    )


def test_write_script_makes_a_fresh_root_only_reader_readable_file(tmp_path):
    res = _run_write(tmp_path / "drop", "areas.yml", "600", b"hello")
    assert res.returncode == 0, res.stderr
    staged = Path(res.stdout.decode())
    assert staged.read_bytes() == b"hello"
    assert stat.S_IMODE(staged.parent.stat().st_mode) == 0o711, "drop dir is root-only to write"
    assert stat.S_IMODE(staged.stat().st_mode) & 0o044, "the reader can read it (other bits set)"


def test_write_script_will_not_reuse_a_drop_dir(tmp_path):
    drop = tmp_path / "drop"
    (drop / "tok0").mkdir(parents=True)  # pre-made: the fresh os.mkdir must refuse
    assert _run_write(drop, "f", "600", b"x").returncode != 0


def _run_read(path, limit: int = 1 << 20):
    return subprocess.run([sys.executable, "-I", "-c", sandbox_files._READ, str(path), str(limit)], capture_output=True)


def test_read_script_returns_a_regular_files_bytes(tmp_path):
    f = tmp_path / "f"
    f.write_bytes(b"\x00\x01regular")
    res = _run_read(f)
    assert res.returncode == 0 and res.stdout == b"\x00\x01regular"


def test_read_script_refuses_a_symlink(tmp_path):
    secret = tmp_path / "secret"
    secret.write_bytes(b"not-yours")
    link = tmp_path / "link"
    link.symlink_to(secret)
    res = _run_read(link)
    assert res.returncode == 3 and res.stdout == b"" and b"link" in res.stderr


def test_read_script_refuses_a_directory(tmp_path):
    res = _run_read(tmp_path)
    assert res.returncode == 6 and res.stdout == b""


def test_read_script_refuses_a_fifo_without_blocking(tmp_path):
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    # No writer is attached; a blocking open would hang. O_NONBLOCK + the
    # regular-file check must return at once with a refusal.
    res = subprocess.run(
        [sys.executable, "-I", "-c", sandbox_files._READ, str(fifo), "1024"], capture_output=True, timeout=10
    )
    assert res.returncode in (3, 6) and res.stdout == b"", res.stderr


def test_read_script_refuses_a_file_over_the_limit(tmp_path):
    f = tmp_path / "big"
    f.write_bytes(b"x" * 100)
    assert _run_read(f, limit=99).returncode == 7
    assert _run_read(f, limit=100).returncode == 0, "exactly the limit is allowed"


def test_read_script_reports_missing_and_permission_distinctly(tmp_path):
    assert _run_read(tmp_path / "nope").returncode == 4
    f = tmp_path / "noread"
    f.write_bytes(b"secret")
    f.chmod(0o000)
    try:
        assert _run_read(f).returncode == 5, "a file the reader may not read is a permission refusal"
    finally:
        f.chmod(0o600)
