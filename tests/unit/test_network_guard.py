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

#: These tests install guards of their own: the one the unit suite runs under lets them through (allow_network), so
#: each test's own guard is the one that decides.
pytestmark = pytest.mark.allow_network("the guard's own tests install their own guards")

ROOT = Path(__file__).resolve().parents[2]
#: TEST-NET-1 (RFC 5737): an address no test may reach, and that nothing answers if the guard lets it through.
FAR = ("192.0.2.1", 9)


def _guard() -> Any:
    try:
        return importlib.import_module("tests.fixtures.network_guard")
    except ImportError:
        pytest.fail("the unit suite has no network guard: a test may reach any host")


def _connect(address: tuple[Any, ...], *, family: int = socket.AF_INET) -> None:
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        sock.connect(address)


def _wheres(guard: Any) -> list[str]:
    return [refusal.where for refusal in guard.refused]


def test_a_connection_beyond_loopback_is_refused_and_recorded() -> None:
    guard_mod = _guard()
    with guard_mod.refusing_network(set()) as guard, pytest.raises(guard_mod.NetworkUseRefused) as refused:
        _connect(FAR)
    assert "192.0.2.1:9" in str(refused.value) and _wheres(guard) == ["192.0.2.1:9"], _wheres(guard)
    assert not isinstance(refused.value, OSError), "a network error handler would take it for one, and retry"


@pytest.mark.parametrize(
    ("family", "address"),
    [
        (socket.AF_INET6, ("2001:db8::1", 9, 0, 0)),
        (socket.AF_INET6, ("::ffff:192.0.2.1", 9, 0, 0)),
        (socket.AF_INET, ("example.invalid", 9)),
        (socket.AF_INET, ("0.0.0.0", 9)),
        (socket.AF_INET6, ("::", 9, 0, 0)),
    ],
    ids=["IPv6", "IPv4-mapped", "a name", "0.0.0.0", "::"],
)
def test_every_kind_of_address_beyond_loopback_is_refused(family: int, address: tuple[Any, ...]) -> None:
    """An IPv6 address, an IPv4 one written in IPv6, a name (which the connect would look up), and the unspecified
    addresses, which a connect reaches the host itself by."""
    guard_mod = _guard()
    with guard_mod.refusing_network(set()) as guard, pytest.raises(guard_mod.NetworkUseRefused):
        _connect(address, family=family)
    assert len(guard.refused) == 1, _wheres(guard)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"])
def test_loopback_is_loopback_however_it_is_written(host: str) -> None:
    assert _guard().is_loopback(host)


def test_connect_ex_sendto_and_sendmsg_are_refused_too() -> None:
    """connect_ex, and a datagram (or a TCP Fast Open connect) given its address with the data."""
    guard_mod = _guard()
    with guard_mod.refusing_network(set()) as guard:
        with socket.socket() as sock, pytest.raises(guard_mod.NetworkUseRefused):
            sock.settimeout(0.5)
            sock.connect_ex(FAR)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            with pytest.raises(guard_mod.NetworkUseRefused):
                udp.sendto(b"x", FAR)
            with pytest.raises(guard_mod.NetworkUseRefused):
                udp.sendto(b"x", 0, FAR)
            with pytest.raises(guard_mod.NetworkUseRefused):
                udp.sendmsg([b"x"], [], 0, FAR)
    assert _wheres(guard) == ["192.0.2.1:9"] * 4, _wheres(guard)


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
    assert _wheres(guard) == ["192.0.2.1:9"]


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
    assert _wheres(guard) == ["192.0.2.1:9"], _wheres(guard)
    found = raised.exceptions if isinstance(raised, BaseExceptionGroup) else (raised,)
    assert any(isinstance(exc, guard_mod.NetworkUseRefused) for exc in found), repr(raised)


@pytest.mark.parametrize(
    "conninfo",
    [
        "host=192.0.2.1 port=9 dbname=x user=x connect_timeout=1",
        "postgresql://x:y@192.0.2.1:9/x?connect_timeout=1",
        "host=localhost,example.invalid port=9 dbname=x connect_timeout=1",
        "hostaddr=192.0.2.1 port=9 dbname=x connect_timeout=1",
    ],
    ids=["keywords", "URI", "a list with a far host", "hostaddr"],
)
async def test_psycopg_s_libpq_connects_are_refused(conninfo: str) -> None:
    """libpq connects in C and never meets the socket class, so psycopg's connects are checked as such."""
    guard_mod = _guard()
    import psycopg

    with guard_mod.refusing_network(set()) as guard:
        with pytest.raises(guard_mod.NetworkUseRefused):
            psycopg.connect(conninfo)
        with pytest.raises(guard_mod.NetworkUseRefused):
            await psycopg.AsyncConnection.connect(conninfo)
    assert len(guard.refused) == 2, _wheres(guard)


def test_psycopg_on_a_unix_socket_or_loopback_is_let_through(monkeypatch: pytest.MonkeyPatch) -> None:
    guard_mod = _guard()
    import psycopg

    monkeypatch.delenv("PGHOST", raising=False)
    with guard_mod.refusing_network(set()) as guard:
        for conninfo in (
            "host=/nonexistent/socket dbname=x connect_timeout=1",
            "host=127.0.0.1 port=1 connect_timeout=1",
        ):
            with pytest.raises(psycopg.OperationalError):
                psycopg.connect(conninfo)
    assert guard.refused == [], _wheres(guard)
    monkeypatch.setenv("PGHOST", "192.0.2.1")
    with guard_mod.refusing_network(set()) as guard, pytest.raises(guard_mod.NetworkUseRefused):
        psycopg.connect("dbname=x connect_timeout=1")


def _pytest_of(tmp_path: Path, files: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    """A pytest of its own over ``files``, under the unit suite's conftest as every unit test runs."""
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "tests.unit.conftest",
         "-o", "markers=allow_network(reason): probe", "--rootdir", str(tmp_path), *args, str(tmp_path)],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin", "MINIO_ENDPOINT": "", "DIND_ENDPOINT": ""},
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )  # fmt: skip


_SWALLOW = (
    "import socket\n"
    "def reach():\n"
    "    try:\n"
    "        with socket.socket() as s:\n"
    "            s.settimeout(0.3)\n"
    "            s.connect(('192.0.2.1', 9))\n"
    "    except BaseException:\n"
    "        pass\n"
)


def test_a_unit_test_that_connects_fails_naming_itself_and_the_address(tmp_path: Path) -> None:
    """A test whose code swallows the refusal still fails, naming the test and the address; one marked
    allow_network runs."""
    _guard()
    done = _pytest_of(
        tmp_path,
        {
            "test_reaches.py": _SWALLOW + "import pytest\n"
            "def test_swallows_it():\n    reach()\n"
            "@pytest.mark.allow_network('the probe of this test')\n"
            "def test_marked():\n    reach()\n"
        },
    )
    out = done.stdout
    assert "test_reaches.py::test_swallows_it tried to connect to 192.0.2.1:9" in out, out[-1500:]
    assert "2 passed, 1 error" in out, out[-600:]


def test_a_refusal_outside_any_test_fails_the_session(tmp_path: Path) -> None:
    """A test module that connects when it is imported: the guard is installed before it, and the run fails."""
    _guard()
    done = _pytest_of(tmp_path, {"test_imports.py": _SWALLOW + "reach()\ndef test_nothing():\n    pass\n"})
    assert "outside any test, a connection to 192.0.2.1:9 was refused" in done.stdout, done.stdout[-1500:]
    assert done.returncode != 0, "the session passed"


def test_a_refusal_outside_any_test_fails_a_distributed_session(tmp_path: Path) -> None:
    """Under pytest-xdist the worker that refused says so through ``shouldfail``, and the run stops failing."""
    _guard()
    done = _pytest_of(tmp_path, {"test_imports.py": _SWALLOW + "reach()\ndef test_nothing():\n    pass\n"}, "-n", "2")
    assert "outside any test, a connection to 192.0.2.1:9 was refused" in done.stdout, done.stdout[-1500:]
    assert done.returncode != 0, "the distributed session passed"


def test_a_module_fixture_s_refusal_is_its_first_test_s(tmp_path: Path) -> None:
    _guard()
    done = _pytest_of(
        tmp_path,
        {
            "test_fixture.py": _SWALLOW + "import pytest\n"
            "@pytest.fixture(scope='module')\n"
            "def far():\n    reach()\n"
            "def test_first(far):\n    pass\n"
            "def test_second(far):\n    pass\n"
        },
    )
    assert "test_fixture.py::test_first tried to connect to 192.0.2.1:9" in done.stdout, done.stdout[-1500:]
    assert "2 passed, 1 error" in done.stdout, done.stdout[-600:]  # test_first passed its call, failed its teardown


def test_a_test_that_opens_a_service_host_opens_it_to_no_later_test(tmp_path: Path) -> None:
    """The allowed hosts are read once, when the guard is installed: a test that sets MINIO_ENDPOINT changes none."""
    _guard()
    done = _pytest_of(
        tmp_path,
        {
            "test_env.py": _SWALLOW + "import os\n"
            "def test_a_sets_it():\n    os.environ['MINIO_ENDPOINT'] = '192.0.2.1:9'\n"
            "def test_b_reaches_it():\n    reach()\n"
        },
        "-p",
        "no:randomly",
    )
    assert "test_env.py::test_b_reaches_it tried to connect to 192.0.2.1:9" in done.stdout, done.stdout[-1500:]


def test_a_thread_that_outlives_its_test_is_not_blamed_on_the_next(tmp_path: Path) -> None:
    """The thread carries the test that started it: the next test passes, and the run fails naming the first."""
    _guard()
    done = _pytest_of(
        tmp_path,
        {
            "test_threads.py": _SWALLOW + "import threading, time\n"
            "def test_a_starts_it():\n"
            "    threading.Thread(target=lambda: (time.sleep(0.3), reach()), daemon=True).start()\n"
            "def test_b_waits():\n    time.sleep(1)\n"
        },
        "-p",
        "no:randomly",
    )
    out = done.stdout
    assert "2 passed" in out and "error" not in out.lower().split("passed")[-1], out[-1200:]
    assert "outside any test, a connection to 192.0.2.1:9 (thread Thread-1" in out, out[-1500:]
    assert "started by test_threads.py::test_a_starts_it" in out and done.returncode != 0, out[-1500:]


def test_a_forked_process_s_refusal_is_reported_by_its_parent(tmp_path: Path) -> None:
    _guard()
    done = _pytest_of(
        tmp_path,
        {
            "test_fork.py": _SWALLOW + "import os\n"
            "def test_forks():\n"
            "    pid = os.fork()\n"
            "    if pid == 0:\n"
            "        reach()\n"
            "        os._exit(0)\n"
            "    os.waitpid(pid, 0)\n"
        },
    )
    assert "test_fork.py::test_forks tried to connect to 192.0.2.1:9 (thread MainThread (forked process" in (
        done.stdout
    ), done.stdout[-1500:]
