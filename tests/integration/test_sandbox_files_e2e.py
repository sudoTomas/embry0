"""sandbox_files and the deliverables collector against a real, capability-dropped container.

Proves the copy-out path end to end under the production isolation flags
(``--cap-drop=ALL --security-opt=no-new-privileges``), with the container's
default user the unprivileged agent, as in the sandbox image:

F1 — a ``0600`` file an unprivileged agent wrote in a ``0700`` directory (the
shape the Claude CLI writes its session file and the shape of a private
deliverable) is read back out, because the read runs as the agent, not root.

F2 — the real ``_collect_artifacts`` over a deliverables directory holding a
newline-named directory, a symlink to ``/root``, a FIFO and a name ending in a
space uploads exactly the real deliverables, never the root-only canary, and
no read blocks; and ``read_regular_file`` refuses the planted paths directly.

Skips when Docker is unavailable (``requires_docker``); the image is a plain
``python:3.12`` with an unprivileged ``agent`` user added, so the test needs no
embry0 image built.
"""

from __future__ import annotations

import subprocess
import uuid
from typing import Any

import pytest

from embry0.execution.docker_client import DockerClient
from embry0.execution.sandbox_files import SandboxReadRefused, read_regular_file
from embry0.workflows.issue_to_pr.plan_route import DELIVERABLES_BUCKET, _collect_artifacts

pytestmark = pytest.mark.requires_docker

_IMAGE = "python:3.12.15-slim-bookworm"
_DELIVERABLES = "/workspace/deliverables"


def _sh(name: str, script: str, *, user: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["docker", "exec", "-u", user, name, "sh", "-c", script], capture_output=True, timeout=30)


class _Minio:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def ensure_bucket(self, bucket: str) -> None:
        pass

    async def put_object(self, bucket: str, key: str, data: bytes, content_type: str = "") -> None:
        self.objects[f"{bucket}/{key}"] = data


@pytest.fixture
def agent_container():
    name = f"sfe2e-{uuid.uuid4().hex[:8]}"
    subprocess.run(
        [
            "docker", "run", "-d", "--name", name, "--user", "1000:1000",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", "--network=none",
            _IMAGE, "sleep", "infinity",
        ],
        check=True, capture_output=True, timeout=120,
    )  # fmt: skip
    try:
        # An unprivileged agent (uid 1000) by name; the shadow-write warning is harmless.
        subprocess.run(
            ["docker", "exec", "-u", "root", name, "useradd", "-M", "-u", "1000", "agent"], capture_output=True
        )
        yield name
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)


async def test_a_private_agent_file_is_read_back_out(agent_container):
    # F1: the agent writes a 0600 file in a 0700 directory; root cannot read it
    # (the sandbox has no DAC_OVERRIDE), but the read — run as the agent — can.
    _sh(agent_container, "umask 077; mkdir -p -m 700 /tmp/s; printf SESSIONBYTES > /tmp/s/blob", user="agent")
    assert _sh(agent_container, "cat /tmp/s/blob", user="root").returncode != 0, "root cannot read it"

    data = await read_regular_file(DockerClient(), agent_container, "/tmp/s/blob", limit=1_000_000)
    assert data == b"SESSIONBYTES"


async def test_the_collector_uploads_only_the_real_deliverables(agent_container):
    assert (
        _sh(agent_container, f"mkdir -p {_DELIVERABLES}; chmod 777 /workspace {_DELIVERABLES}", user="root").returncode
        == 0
    )
    assert _sh(agent_container, "printf TOPSECRET > /root/canary; chmod 600 /root/canary", user="root").returncode == 0
    plants = (
        f"cd {_DELIVERABLES} && umask 077 && printf REAL > report.txt && printf NOTES > 'notes ' && "
        "mkdir -p \"$(printf 'a\\nb')\" && printf N > \"$(printf 'a\\nb')/f\" && ln -s /root link && mkfifo pipe"
    )
    assert _sh(agent_container, plants, user="agent").returncode == 0
    # A file outside the directory, reachable only through a symlinked
    # directory `find` never descends; and a newline-named path whose part
    # after the newline names it. Splitting the listing on newlines would read
    # it; the NUL-delimited listing never names it.
    outside = (
        "mkdir -p /tmp/outside && printf OUTSIDE > /tmp/outside/x && "
        f"ln -s /tmp/outside {_DELIVERABLES}/lnk2 && "
        "nl=$(printf '\\nX'); nl=${nl%X}; "  # a bare newline: $(...) strips a trailing one
        f'd="{_DELIVERABLES}/c$nl{_DELIVERABLES}/lnk2" && mkdir -p "$d" && printf DEEP > "$d/x"'
    )
    assert _sh(agent_container, outside, user="agent").returncode == 0

    minio = _Minio()
    state = {"sandbox_container_id": agent_container, "job_id": "job-e2e"}
    artifacts = await _collect_artifacts(state, {"configurable": {"docker": DockerClient(), "qa_minio": minio}})

    # The private report and the trailing-space name come out whole; the
    # newline-named file is refused (a control character); the link and the
    # FIFO are not regular files; nothing from /root is ever read.
    assert sorted(a["title"] for a in artifacts) == ["notes ", "report.txt"]
    assert minio.objects == {
        f"{DELIVERABLES_BUCKET}/job-e2e/report.txt": b"REAL",
        f"{DELIVERABLES_BUCKET}/job-e2e/notes ": b"NOTES",
    }
    assert not any(b"TOPSECRET" in data or b"OUTSIDE" in data for data in minio.objects.values()), (
        "nothing outside the deliverables directory is read"
    )


async def test_planted_paths_are_refused_and_never_block(agent_container):
    _sh(agent_container, f"mkdir -p {_DELIVERABLES}; chmod 777 /workspace {_DELIVERABLES}", user="root")
    _sh(agent_container, "printf TOPSECRET > /root/canary; chmod 600 /root/canary", user="root")
    _sh(agent_container, f"cd {_DELIVERABLES} && ln -s /root link && mkfifo pipe", user="agent")
    docker = DockerClient()
    found: dict[str, Any] = {}

    # A path that resolves through the planted link into /root yields nothing:
    # the read is the agent, which cannot read the root canary.
    with pytest.raises(SandboxReadRefused) as through_link:
        await read_regular_file(docker, agent_container, f"{_DELIVERABLES}/link/canary", limit=1_000_000)
    found["through_link"] = through_link.value.reason
    # The link itself is refused (no following).
    with pytest.raises(SandboxReadRefused) as at_link:
        await read_regular_file(docker, agent_container, f"{_DELIVERABLES}/link", limit=1_000_000)
    found["at_link"] = at_link.value.reason
    # The FIFO is refused at once, not after blocking (this call times out in
    # 30 s if it blocks; O_NONBLOCK + the regular-file check return instantly).
    with pytest.raises(SandboxReadRefused) as at_fifo:
        await read_regular_file(docker, agent_container, f"{_DELIVERABLES}/pipe", limit=1_000_000, timeout=30)
    found["at_fifo"] = at_fifo.value.reason

    assert found == {"through_link": "permission_denied", "at_link": "link", "at_fifo": "not_regular"}, found
