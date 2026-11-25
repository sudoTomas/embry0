"""The unit suite reaches no network but loopback and its own services (tests/fixtures/network_guard.py, #702)."""

from __future__ import annotations

import asyncio
import importlib
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

#: These tests install guards of their own: the one every unit test runs under would refuse what they let through.
pytestmark = pytest.mark.allow_network("the guard's own tests install their own guards")

ROOT = Path(__file__).resolve().parents[2]
#: TEST-NET-1 (RFC 5737): an address no test may reach, and that nothing answers if the guard lets it through.
FAR = ("192.0.2.1", 9)


def _guard() -> Any:
    try:
        return importlib.import_module("tests.fixtures.network_guard")
    except ImportError:
        pytest.fail("the unit suite has no network guard: a test may reach any host")


def _connect(address: tuple[str, int], *, family: int = socket.AF_INET) -> None:
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        sock.connect(address)


def test_a_connection_beyond_loopback_is_refused_and_recorded() -> None:
    guard_mod = _guard()
    with guard_mod.refusing_network(set()) as guard, pytest.raises(guard_mod.NetworkUseRefused) as refused:
        _connect(FAR)
    assert "192.0.2.1:9" in str(refused.value) and guard.refused == ["192.0.2.1:9"], guard.refused
    assert not isinstance(refused.value, OSError), "a network error handler would take it for one, and retry"


def test_connect_ex_is_refused_too() -> None:
    guard_mod = _guard()
    with (
        guard_mod.refusing_network(set()) as guard,
        socket.socket() as sock,
        pytest.raises(guard_mod.NetworkUseRefused),
    ):
        sock.settimeout(0.5)
        sock.connect_ex(FAR)
    assert guard.refused == ["192.0.2.1:9"]


def test_loopback_and_the_run_s_services_are_reached() -> None:
    guard_mod = _guard()
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with guard_mod.refusing_network(set()) as guard:
            _connect(server.getsockname())
        assert guard.refused == [], "loopback was refused"
    with guard_mod.refusing_network({"192.0.2.1"}) as guard, pytest.raises(OSError):
        _connect(FAR)  # let through, so the socket's own timeout ends it
    assert guard.refused == [], "an allowed service host was refused"


def test_the_services_are_read_from_the_run_s_environment() -> None:
    guard_mod = _guard()
    hosts = guard_mod.service_hosts(
        {
            "TEST_DATABASE_URL": "postgresql://u:p@db.internal:5432/unit",
            "MINIO_ENDPOINT": "minio.internal:9000",
            "DIND_ENDPOINT": "tcp://dind.internal:2376",
            "OTHER_URL": "https://api.github.com",
        }
    )
    assert hosts == {"db.internal", "minio.internal", "dind.internal"}, hosts


async def test_an_asyncio_connection_is_refused() -> None:
    guard_mod = _guard()
    with guard_mod.refusing_network(set()) as guard, pytest.raises(guard_mod.NetworkUseRefused):
        await asyncio.wait_for(asyncio.open_connection(*FAR), timeout=2)
    assert guard.refused == ["192.0.2.1:9"]


@pytest.mark.parametrize("client", ["sync", "async"])
async def test_an_http_client_is_refused_at_its_transport(client: str) -> None:
    guard_mod = _guard()
    url = f"http://{FAR[0]}:{FAR[1]}/"
    raised: BaseException | None = None
    with guard_mod.refusing_network(set()) as guard:
        try:
            if client == "sync":
                httpx.get(url, timeout=2)
            else:
                async with httpx.AsyncClient(timeout=2) as http:
                    await http.get(url)
        except BaseException as exc:  # noqa: BLE001 - anyio may wrap it in an exception group
            raised = exc
    assert guard.refused == ["192.0.2.1:9"], guard.refused
    found = raised.exceptions if isinstance(raised, BaseExceptionGroup) else (raised,)
    assert any(isinstance(exc, guard_mod.NetworkUseRefused) for exc in found), repr(raised)


def test_a_unit_test_that_connects_fails_naming_itself_and_the_address(tmp_path: Path) -> None:
    """The fixture every unit test runs under (tests/unit/conftest.py), in a pytest of its own: a test whose code
    swallows the refusal still fails, naming the test and the address; one marked allow_network runs."""
    _guard()
    (tmp_path / "test_reaches.py").write_text(
        "import socket\n"
        "import pytest\n"
        "def test_swallows_it():\n"
        "    try:\n"
        "        with socket.socket() as s:\n"
        "            s.settimeout(0.5)\n"
        "            s.connect(('192.0.2.1', 9))\n"
        "    except BaseException:\n"
        "        pass\n"
        "@pytest.mark.allow_network('the probe of this test')\n"
        "def test_marked():\n"
        "    try:\n"
        "        with socket.socket() as s:\n"
        "            s.settimeout(0.2)\n"
        "            s.connect(('192.0.2.1', 9))\n"
        "    except OSError:\n"
        "        pass\n"
    )
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "tests.unit.conftest",
         "-o", "markers=allow_network(reason): probe", "--rootdir", str(tmp_path), str(tmp_path)],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )  # fmt: skip
    out = done.stdout
    assert "test_reaches.py::test_swallows_it tried to connect to 192.0.2.1:9" in out, out[-1500:]
    assert "2 passed, 1 error" in out, out[-600:]
