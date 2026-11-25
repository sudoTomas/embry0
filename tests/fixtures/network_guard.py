"""The unit suite reaches no network but loopback and its own services (#702).

A unit test that reaches a real host measures that host, not the code: a
build test that asked a public forge about a repository that does not exist
hung past its timeout when the forge rate-limited the runner. So the unit
suite runs with every connection refused but to loopback, a Unix socket, and
the hosts of the services the run was given: the test database
(``TEST_DATABASE_URL``), and MinIO or DinD when their endpoint is set. The
hosts are read once, when the guard is installed, so a test that changes the
environment opens nothing to the tests after it.

**When.** ``tests/unit/conftest.py`` installs one :class:`Guard` per process
when it is imported, before the test modules are, and never removes it: test
module imports, collection, module and session fixtures and threads that
outlive their test all run under it. A refusal during a test (its setup, call
or teardown) fails that test, naming the address; one outside any test fails
the session. A thread started during a test carries that test: if it connects
after its test ended, the session fails naming it, and the test running then is
not blamed (a thread started outside any test is blamed on the test running
when it connects). A process forked from a guarded one keeps the guard, and its
refusals are reported by its parent, through a file in a directory of the
guard's own.

**Kept.** Before and after each test the hooks check that ``socket.socket`` is
still the class the guard patched and that the seven attributes it replaced are
still its own. A test that leaves one changed (assigns the C method back,
deletes the attribute, reloads ``socket``) would take the guard away from every
test after it: it fails, naming what changed, and the guard is put back. A
change outside any test fails the session. A patch that is undone before the
test ends (``monkeypatch``, a fixture that restores what it saved) is fine.

**What.** It patches, in Python, ``socket.socket.connect``, ``connect_ex``,
``sendto`` and ``sendmsg`` (an address given there is a UDP datagram or a TCP
Fast Open connect), and ``psycopg``'s ``Connection.connect``,
``AsyncConnection.connect`` and ``psycopg.connect``, whose libpq connects in C
and never meets the Python socket class: their ``host`` and ``hostaddr``
(and ``PGHOST``/``PGHOSTADDR``) are checked by the same rule. It does not see
what never goes through these: a C extension's own sockets (another C client,
or a subclass of ``_socket.socket``), uvloop's event loop, a subprocess, and a
process started by ``multiprocessing``'s spawn or forkserver, which imports a
fresh interpreter. A Unix socket is always allowed, Docker's included: that
the unit suite reaches no Docker daemon rests on ``DOCKER_HOST`` pointing at
none (``docs/ops/verification.md``). Name lookups go through the system
resolver and are not stopped.

A refused connection raises :class:`NetworkUseRefused`, which is not an
``OSError``, so code that retries on a network error does not take it for one
and wait; and it is recorded, so a test whose code swallows the exception
still fails. A test that must reach the network says so with
``@pytest.mark.allow_network(reason)``.
"""

from __future__ import annotations

import ipaddress
import os
import shutil
import socket
import sys
import tempfile
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

#: The variables that name the services a run may reach.
_SERVICE_URLS: Final = ("TEST_DATABASE_URL",)
_SERVICE_ENDPOINTS: Final = ("MINIO_ENDPOINT", "DIND_ENDPOINT")
#: What a patched attribute restores to when the class only inherited it.
_ABSENT: Final = object()
#: The attribute a thread started during a test carries: that test's node id.
_STARTED_BY: Final = "_network_guard_started_by"


class NetworkUseRefused(RuntimeError):
    """A unit test tried to connect to an address the suite does not reach. Not an ``OSError``: code that retries
    on network errors must not take it for one and wait."""


def service_hosts(environ: Mapping[str, str] | None = None) -> set[str]:
    """The hosts of the services this run was given (the test database, MinIO, DinD), by name or address."""
    env = os.environ if environ is None else environ
    hosts: set[str] = set()
    for name in _SERVICE_URLS:
        value = env.get(name, "")
        if value:
            host = urlsplit(value).hostname
            if host:
                hosts.add(host)
    for name in _SERVICE_ENDPOINTS:
        value = env.get(name, "")
        if value:
            host = urlsplit(value if "//" in value else f"//{value}").hostname
            if host:
                hosts.add(host)
    return hosts


def allowed_addresses(environ: Mapping[str, str] | None = None) -> frozenset[str]:
    """The service hosts and what they resolve to now; a name that does not resolve adds only itself."""
    found: set[str] = set()
    for host in service_hosts(environ):
        found.add(host)
        try:
            found.update(str(info[4][0]) for info in socket.getaddrinfo(host, None))
        except OSError:
            continue
    return frozenset(found)


def is_loopback(host: str) -> bool:
    """Whether ``host`` is a loopback address, an IPv4-mapped one included, or ``localhost``. A name is not: it
    would be looked up, and could be anything."""
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return host == "localhost"
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


@dataclass(frozen=True)
class Refusal:
    """One connection refused: where to, during which test (None: outside any), from which thread and process."""

    where: str
    test: str | None
    thread: str
    pid: int


@dataclass
class Patched:
    """One attribute the guard replaced: the object it lives on (looked up again each time, as a reload replaces
    it), what that was when the guard patched it, the attribute and the guard's value."""

    label: str
    owner_of: Callable[[], Any]
    owner: Any
    name: str
    value: Any

    def changed(self) -> str | None:
        """What is no longer the guard's, named; None while it all is."""
        try:
            owner = self.owner_of()
        except (KeyError, AttributeError):
            return f"{self.label} (gone)"
        if owner is not self.owner:
            return f"{self.label} (replaced, as a reload does)"
        if owner.__dict__.get(self.name, _ABSENT) is not self.value:
            return f"{self.label}.{self.name}"
        return None

    def put_back(self) -> None:
        """The guard's value on the object it lives on now (a reloaded module's new class, too)."""
        try:
            owner = self.owner_of()
        except (KeyError, AttributeError):
            return
        setattr(owner, self.name, self.value)
        self.owner = owner


@dataclass
class Guard:
    """The addresses a process may connect to, the test running in it, and every connection it refused."""

    allowed: frozenset[str]
    #: The test running now (its node id), set by the unit suite's hooks; None between tests.
    current: str | None = None
    #: Whether the test running now is marked ``allow_network``.
    allowing: bool = False
    refused: list[Refusal] = field(default_factory=list)
    #: Where a process forked from this one writes its refusals, for this one to report: a file in a directory
    #: of the guard's own (``mkdtemp``: private, its name not known in advance), made when it is installed.
    children: Path | None = None
    pid: int = field(default_factory=os.getpid)
    #: What install() replaced, checked between tests.
    patched: list[Patched] = field(default_factory=list)

    def permits(self, host: str) -> bool:
        return is_loopback(host) or host in self.allowed

    def check_address(self, family: int, address: Any) -> None:
        if family not in (socket.AF_INET, socket.AF_INET6) or not isinstance(address, tuple):
            return  # a Unix socket (Postgres's, Docker's), or a family no host is reached by
        self.check_host(str(address[0]), address[1])

    def check_host(self, host: str, port: Any) -> None:
        if self.permits(host) or self.allowing:
            return
        where = f"{host}:{port}"
        self._record(where)
        raise NetworkUseRefused(
            f"the unit suite reaches no network: a connection to {where} was refused "
            "(tests/fixtures/network_guard.py, #702); replace what makes it, or mark the test allow_network"
        )

    def check_conninfo(self, conninfo: str, kwargs: Mapping[str, Any]) -> None:
        """libpq's hosts for a psycopg connect: its ``host`` and ``hostaddr``, else ``PGHOST``/``PGHOSTADDR``. A
        path, or none at all, is a Unix socket."""
        from psycopg.conninfo import conninfo_to_dict

        params = conninfo_to_dict(conninfo, **{k: v for k, v in kwargs.items() if isinstance(v, str | int)})
        port = params.get("port") or os.environ.get("PGPORT") or 5432
        for key, env in (("host", "PGHOST"), ("hostaddr", "PGHOSTADDR")):
            value = str(params.get(key) or os.environ.get(env) or "")
            for host in (part.strip() for part in value.split(",")):
                if host and not host.startswith("/") and not host.startswith("@"):
                    self.check_host(host, port)

    def _record(self, where: str) -> None:
        thread = threading.current_thread()
        test, name = self.current, thread.name
        origin = getattr(thread, _STARTED_BY, None)
        if origin is not None and origin != test:
            # A thread a finished test started: not the running test's doing. The run fails, naming the test.
            test, name = None, f"{thread.name}, started by {origin}"
        refusal = Refusal(where, test, name, os.getpid())
        if os.getpid() != self.pid:
            if self.children is not None:
                try:
                    with self.children.open("a", encoding="utf-8") as log:  # a forked child: its parent reports it
                        log.write(f"{where}\t{refusal.thread}\t{refusal.pid}\n")
                except OSError:
                    pass  # its parent has ended; the refusal is still raised
            return
        self.refused.append(refusal)

    def _children(self) -> list[Refusal]:
        if self.children is None or not self.children.exists():
            return []
        lines = self.children.read_text(encoding="utf-8").splitlines()
        self.children.unlink(missing_ok=True)
        found = []
        for line in lines:
            where, thread, pid = (line.split("\t") + ["", ""])[:3]
            found.append(Refusal(where, self.current, f"{thread} (forked process {pid})", self.pid))
        return found

    def remove_children(self) -> None:
        """Remove the directory forked children report in; only the process that made it does."""
        if self.children is not None and os.getpid() == self.pid:
            shutil.rmtree(self.children.parent, ignore_errors=True)

    def take(self, test: str) -> list[Refusal]:
        """The refusals during ``test``, a forked child's included, removed from what is left to report."""
        self.refused.extend(self._children())
        mine = [r for r in self.refused if r.test == test]
        self.refused = [r for r in self.refused if r.test != test]
        return mine

    def outside(self) -> list[Refusal]:
        """The refusals during no test: imports, collection, a thread between tests."""
        self.refused.extend(self._children())
        return [r for r in self.refused if r.test is None]

    def changed(self) -> list[str]:
        """What no longer holds the guard (``socket.socket`` replaced, an attribute it patched reassigned or
        deleted), named, after putting it back."""
        changed = [what for what in (patched.changed() for patched in self.patched) if what is not None]
        if changed:
            for patched in self.patched:
                patched.put_back()
        return sorted(set(changed))

    def stamp_threads(self) -> None:
        """Mark each thread started during a test with that test, so a refusal from a thread that outlives it is
        not blamed on the test running when it connects."""
        guard = self
        start = threading.Thread.start

        def stamped(thread: threading.Thread) -> None:
            if guard.current is not None and getattr(thread, _STARTED_BY, None) is None:
                setattr(thread, _STARTED_BY, guard.current)
            start(thread)

        threading.Thread.start = stamped  # type: ignore[method-assign]

    @contextmanager
    def installed(self) -> Iterator[Guard]:
        """This guard over ``socket.socket`` and ``psycopg`` while the block runs (a test's own guard)."""
        undo = self.install()
        try:
            yield self
        finally:
            undo()

    def install(self) -> Any:
        """Patch the socket class and psycopg; return what restores them."""
        guard = self
        restore: list[tuple[Any, str, Any]] = []
        self.children = Path(tempfile.mkdtemp(prefix="embry0-net-")) / "children.log"

        def patch(label: str, owner_of: Callable[[], Any], name: str, value: Any) -> None:
            owner = owner_of()
            restore.append((owner, name, owner.__dict__.get(name, _ABSENT)))
            setattr(owner, name, value)
            self.patched.append(Patched(label, owner_of, owner, name, value))

        real = {name: getattr(socket.socket, name) for name in ("connect", "connect_ex", "sendto", "sendmsg")}

        def connect(sock: socket.socket, address: Any) -> None:
            guard.check_address(sock.family, address)
            return real["connect"](sock, address)

        def connect_ex(sock: socket.socket, address: Any) -> int:
            guard.check_address(sock.family, address)
            return real["connect_ex"](sock, address)  # type: ignore[no-any-return]

        def sendto(sock: socket.socket, data: Any, *args: Any) -> int:
            if args:
                guard.check_address(sock.family, args[-1])
            return real["sendto"](sock, data, *args)  # type: ignore[no-any-return]

        def sendmsg(sock: socket.socket, buffers: Any, *args: Any, **kwargs: Any) -> int:
            address = args[2] if len(args) > 2 else kwargs.get("address")
            if address is not None:
                guard.check_address(sock.family, address)
            return real["sendmsg"](sock, buffers, *args, **kwargs)  # type: ignore[no-any-return]

        for name, value in (("connect", connect), ("connect_ex", connect_ex), ("sendto", sendto), ("sendmsg", sendmsg)):
            patch("socket.socket", lambda: sys.modules["socket"].socket, name, value)
        self._install_psycopg(patch)

        def undo() -> None:
            for owner, name, value in reversed(restore):
                if value is _ABSENT:
                    delattr(owner, name)  # it was inherited (socket.socket's connect is _socket.socket's)
                else:
                    setattr(owner, name, value)
            self.patched.clear()
            self.remove_children()

        return undo

    def _install_psycopg(self, patch: Any) -> None:
        try:
            import psycopg
        except ImportError:
            return
        guard = self
        sync_connect = psycopg.Connection.connect.__func__
        async_connect = psycopg.AsyncConnection.connect.__func__

        def connect(cls: Any, conninfo: str = "", **kwargs: Any) -> Any:
            guard.check_conninfo(conninfo, kwargs)
            return sync_connect(cls, conninfo, **kwargs)

        async def aconnect(cls: Any, conninfo: str = "", **kwargs: Any) -> Any:
            guard.check_conninfo(conninfo, kwargs)
            return await async_connect(cls, conninfo, **kwargs)

        patch("psycopg.Connection", lambda: sys.modules["psycopg"].Connection, "connect", classmethod(connect))
        patch(
            "psycopg.AsyncConnection", lambda: sys.modules["psycopg"].AsyncConnection, "connect", classmethod(aconnect)
        )
        patch("psycopg", lambda: sys.modules["psycopg"], "connect", psycopg.Connection.connect)


@contextmanager
def refusing_network(allowed: Iterable[str]) -> Iterator[Guard]:
    """A guard of a test's own, refusing every address but loopback, Unix sockets and ``allowed`` while it holds."""
    with Guard(allowed=frozenset(allowed)).installed() as guard:
        yield guard
