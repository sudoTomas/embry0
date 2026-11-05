"""Move bytes into and out of a sandbox without following a path the agent controls.

embry0 stages files into a running sandbox for its own steps (a delivery's
file, a resumed session) and reads files back out (a session blob, a
screenshot, a job's deliverables). The straightforward way — ``docker cp`` —
runs in the daemon as root and **follows a symlink at the path it is given**,
so a copy-in could overwrite a file the agent cannot write and a copy-out
could read a file it cannot read. This module is the only place that moves
bytes across the sandbox boundary.

Writing in — :func:`put_bytes_for_reader` — writes the bytes **as root, from
stdin**, into a freshly made directory under :data:`DROP_ROOT` that only root
may write, so nothing in the sandbox can have planted the destination. The
file is created ``O_EXCL | O_NOFOLLOW`` (a plain new file, no link followed)
and left **root-owned and readable through its other bits**: the sandbox drops
every capability (``cap_drop: ALL``), so root cannot ``chown`` it to the agent,
and a chowned file would be agent-writable anyway. A caller that must land the
bytes at an agent path copies them there **as the agent**, with
:func:`place_as_reader_script` (which never follows a link at the destination).

Reading out — :func:`read_regular_file` — reads **as the reader (the agent),
not root**. That is the safety property: the agent only ever gets bytes it
could already read itself, so a path that resolves to a file it cannot read —
however the parent directories are arranged, and even if a parent is swapped
mid-read — yields nothing new. The read opens the path
``O_RDONLY | O_NOFOLLOW | O_NONBLOCK`` (no final-link following; a FIFO or
device returns at once instead of blocking), requires a regular file by
``fstat`` on the open descriptor, and refuses a file past its caller's byte
limit — enforced in the sandbox by the reader and again on the orchestrator,
which kills the read once its pipe carries more (another agent process can
write into that pipe). Each helper runs ``python3 -I`` with the working directory ``/``, so no
agent-owned directory is on ``sys.path`` or is the cwd.

A guard test (``tests/unit/execution/test_sandbox_files_guard.py``) keeps every
other module off ``docker cp``, off a ``docker exec`` as anyone but the agent,
and off the archive API.
"""

from __future__ import annotations

import posixpath
import re
import shlex
from typing import TYPE_CHECKING

from embry0.execution.docker_client import OutputTooLargeError

if TYPE_CHECKING:
    from embry0.execution.docker_client import DockerClient

#: The unprivileged account every sandbox image runs as (``useradd agent``).
SANDBOX_USER = "agent"

#: Root-owned parent of every drop directory. Each call makes a fresh random
#: child here; the parent and child are root-only to write, so an agent cannot
#: plant a symlink at a staged file's path. On ``/run`` on purpose: in the
#: sandbox image ``/run`` is root-owned and not writable by the agent (it is
#: part of the container's own filesystem, not a tmpfs), unlike ``/tmp``.
DROP_ROOT = "/run/embry0/drop"

#: The random name of one drop directory (``secrets.token_hex(16)``).
_DROP_TOKEN = re.compile(r"[0-9a-f]{32}")

#: Extra bytes the orchestrator lets a read's pipe carry past its limit before
#: it kills the read; anything over the limit itself is still refused.
_READ_SLACK = 64

#: Refusal reasons :func:`read_regular_file` distinguishes.
READ_MISSING = "missing"
READ_PERMISSION = "permission_denied"
READ_LINK = "link"
READ_NOT_REGULAR = "not_regular"
READ_TOO_LARGE = "too_large"
_EXIT_REASON = {3: READ_LINK, 4: READ_MISSING, 5: READ_PERMISSION, 6: READ_NOT_REGULAR, 7: READ_TOO_LARGE}

# Written as root (-I, cwd /). Makes the root-only drop directory, writes stdin
# into a fresh O_EXCL|O_NOFOLLOW file inside it, and leaves it root-owned and
# readable through its other bits (no chown: the sandbox has no CAP_CHOWN, and
# a chowned file would be agent-writable).
_WRITE = r"""
import os, sys
drop_root, token, name, mode_s = sys.argv[1:5]
mode = int(mode_s, 8) | 0o044
os.makedirs(drop_root, mode=0o711, exist_ok=True)
os.chmod(drop_root, 0o711)
dropdir = os.path.join(drop_root, token)
os.mkdir(dropdir, 0o711)  # fresh: a reused name (never, it is random) would raise
path = os.path.join(dropdir, name)
data = sys.stdin.buffer.read()
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
try:
    while data:
        data = data[os.write(fd, data):]
    os.fchmod(fd, mode)
finally:
    os.close(fd)
sys.stdout.write(path)
"""

# Read as the agent (-I, cwd /). No final-link following; a FIFO/device returns
# at once; a regular file is required; bytes past the limit are refused. The
# distinct exit codes map to _EXIT_REASON.
_READ = r"""
import errno, os, stat, sys
path, limit_s = sys.argv[1], sys.argv[2]
limit = int(limit_s)
try:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
except FileNotFoundError:
    sys.stderr.write("embry0-read-refused: missing"); raise SystemExit(4)
except PermissionError:
    sys.stderr.write("embry0-read-refused: permission denied"); raise SystemExit(5)
except OSError as exc:
    if exc.errno == errno.ELOOP:
        sys.stderr.write("embry0-read-refused: a link at the path"); raise SystemExit(3)
    sys.stderr.write("embry0-read-refused: not a regular file (%d)" % (exc.errno or 0)); raise SystemExit(6)
try:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        sys.stderr.write("embry0-read-refused: not a regular file"); raise SystemExit(6)
    buf = b""
    while len(buf) <= limit:
        chunk = os.read(fd, (limit + 1) - len(buf))
        if not chunk:
            break
        buf += chunk
    if len(buf) > limit:
        sys.stderr.write("embry0-read-refused: over the %d-byte limit" % limit); raise SystemExit(7)
    os.write(1, buf)
finally:
    os.close(fd)
"""


class SandboxReadRefused(RuntimeError):
    """A copy-out refused: ``reason`` is one of the ``READ_*`` constants."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"sandbox read refused ({reason}): {detail}")
        self.reason = reason


def _bare_name(name: str) -> str:
    """A single path component, never a directory traversal."""
    if not name or "/" in name or name in (".", ".."):
        raise ValueError(f"drop file name must be a bare filename, got {name!r}")
    return name


async def put_bytes_for_reader(
    docker: DockerClient,
    container: str,
    data: bytes,
    *,
    name: str,
    mode: int = 0o644,
    timeout: int = 60,
) -> str:
    """Stage ``data`` in ``container`` and return the path it lives at.

    Written as root into a fresh root-only directory, so no path the agent
    controls is followed, and left root-owned and reader-readable. The caller
    reads it with :func:`read_regular_file` (as the agent) or copies it to an
    agent path with :func:`place_as_reader_script`; it never writes to an
    agent-chosen destination.
    """
    import secrets

    token = secrets.token_hex(16)
    command = ["python3", "-I", "-c", _WRITE, DROP_ROOT, token, _bare_name(name), format(mode, "o")]
    path = await docker.run_cmd(
        docker.build_exec_cmd(container, command, user="root", workdir="/", interactive=True),
        timeout=timeout,
        stdin_data=data,
    )
    return path.strip()


async def read_regular_file(
    docker: DockerClient,
    container: str,
    path: str,
    *,
    limit: int,
    reader: str = SANDBOX_USER,
    timeout: int = 60,
) -> bytes:
    """Read ``path`` from ``container`` as ``reader`` (the agent), up to ``limit`` bytes.

    The read runs as the unprivileged reader, so it can only ever return bytes
    that user could already read — a path that resolves elsewhere, or a file it
    does not own, yields nothing new. It refuses a non-regular file (a FIFO or
    device never blocks it) and a file larger than ``limit``. The limit holds on
    both sides: the reader in the sandbox stops at it, and the orchestrator
    kills the read once its pipe carries more than ``limit`` (plus a few bytes),
    so another process writing into that pipe cannot make embry0 buffer an
    unbounded amount. Raises :class:`SandboxReadRefused` with a distinct
    ``reason`` on any refusal.
    """
    limit = int(limit)
    command = ["python3", "-I", "-c", _READ, path, str(limit)]
    try:
        data = await docker.run_cmd_bytes(
            docker.build_exec_cmd(container, command, user=reader, workdir="/"),
            timeout=timeout,
            max_bytes=limit + _READ_SLACK,
        )
    except OutputTooLargeError as exc:
        raise SandboxReadRefused(READ_TOO_LARGE, str(exc)) from exc
    except RuntimeError as exc:
        message = str(exc)
        for code, reason in _EXIT_REASON.items():
            if f"exit code {code}" in message or _MARKERS[reason] in message:
                raise SandboxReadRefused(reason, message) from exc
        raise
    if len(data) > limit:
        raise SandboxReadRefused(READ_TOO_LARGE, f"{len(data)} bytes over the {limit}-byte limit")
    return data


#: The stderr marker each reason prints, for mapping a failure back to a reason.
_MARKERS = {
    READ_MISSING: "refused: missing",
    READ_PERMISSION: "refused: permission denied",
    READ_LINK: "refused: a link at the path",
    READ_NOT_REGULAR: "refused: not a regular file",
    READ_TOO_LARGE: "refused: over the",
}


def place_as_reader_script(src: str, dst: str) -> str:
    """A shell fragment, run **as the reader**, that copies a staged file to ``dst``.

    ``cp --remove-destination`` unlinks ``dst`` before copying, so it never
    follows a link the agent left at ``dst``. The reader copying into its own
    area cannot escalate: following a link in a parent directory only reaches
    where it may already write. The fragment does not remove the drop (the
    reader cannot: the drop directory is root-only); callers run
    :func:`discard_drop` afterwards.
    """
    dst_dir = shlex.quote(dst.rsplit("/", 1)[0])
    src_q, dst_q = shlex.quote(src), shlex.quote(dst)
    return f"mkdir -p {dst_dir} && cp --remove-destination {src_q} {dst_q}"


def _drop_dir_of(path: str) -> str | None:
    """The drop directory ``path`` was staged in, or None unless it is exactly ``DROP_ROOT/<token>/<name>``."""
    dropdir = posixpath.dirname(posixpath.normpath(path))
    parent, token = posixpath.split(dropdir)
    if parent != DROP_ROOT or not _DROP_TOKEN.fullmatch(token):
        return None
    return dropdir


async def discard_drop(docker: DockerClient, container: str, path: str, *, timeout: int = 15) -> None:
    """Remove a staged file's drop directory, as root (best-effort).

    The drop directory is root-only, so only root can remove it. Callers run
    this once the reader is done with the staged path; a failure is ignored
    (removing the sandbox removes its drops regardless). Only a path of the
    exact shape ``DROP_ROOT/<32 hex>/<name>``, after normalisation, is acted on,
    so no ``..`` or other path can turn this into a wider ``rm -rf``.
    """
    dropdir = _drop_dir_of(path)
    if dropdir is None:
        return
    try:
        await docker.run_cmd(
            docker.build_exec_cmd(container, ["rm", "-rf", dropdir], user="root", workdir="/"), timeout=timeout
        )
    except Exception:  # noqa: BLE001 - teardown clears /run anyway
        pass
