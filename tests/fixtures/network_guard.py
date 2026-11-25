"""The unit suite reaches no network but loopback and its own services (#702).

A unit test that reaches a real host measures that host, not the code: a
build test that asked a public forge about a repository that does not exist
hung past its timeout when the forge rate-limited the runner. So every unit
test runs with ``socket.socket.connect`` (and ``connect_ex``) refusing any
address but loopback, a Unix socket, and the hosts of the services the run
was given: the test database (``TEST_DATABASE_URL``), and MinIO or DinD when
their endpoint is set. A refused connection raises :class:`NetworkUseRefused`,
which no network error handler catches (it is not an ``OSError``), and is
recorded, so the test fails naming the address even if the code under test
swallowed the exception. A test that must reach the network says so with
``@pytest.mark.allow_network(reason)``.

The guard patches the socket class in this process, so it holds in every
pytest-xdist worker. It does not see a subprocess's connections, and it does
not stop a name lookup, which goes through the system resolver.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import urlsplit

#: The variables that name the services a run may reach, and how to read each one's host.
_SERVICE_URLS: Final = ("TEST_DATABASE_URL",)
_SERVICE_ENDPOINTS: Final = ("MINIO_ENDPOINT", "DIND_ENDPOINT")


class NetworkUseRefused(RuntimeError):
    """A unit test tried to connect to an address the suite does not reach. Not an ``OSError``: code that retries
    on network errors must not take it for one and wait."""


def service_hosts(environ: dict[str, str] | None = None) -> set[str]:
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


def _addresses(hosts: Iterable[str]) -> set[str]:
    """What ``hosts`` resolve to now; a name that does not resolve adds nothing."""
    found: set[str] = set()
    for host in hosts:
        try:
            found.update(str(info[4][0]) for info in socket.getaddrinfo(host, None))
        except OSError:
            continue
        found.add(host)
    return found


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return host == "localhost"


@dataclass
class Guard:
    """The addresses a test may connect to, and every connection it was refused."""

    allowed: set[str]
    refused: list[str] = field(default_factory=list)

    def check(self, family: int, address: Any) -> None:
        if family not in (socket.AF_INET, socket.AF_INET6):
            return  # a Unix socket (Docker's, Postgres's), or a family no host is reached by
        host, port = str(address[0]), address[1]
        if _is_loopback(host) or host in self.allowed:
            return
        where = f"{host}:{port}"
        self.refused.append(where)
        raise NetworkUseRefused(
            f"the unit suite reaches no network: a connection to {where} was refused "
            "(tests/fixtures/network_guard.py, #702); replace what makes it, or mark the test allow_network"
        )


_allowed_cache: dict[frozenset[str], set[str]] = {}


def allowed_addresses() -> set[str]:
    """The service hosts and their addresses, resolved once per process for each set of service hosts."""
    hosts = frozenset(service_hosts())
    if hosts not in _allowed_cache:
        _allowed_cache[hosts] = _addresses(hosts)
    return _allowed_cache[hosts]


@contextmanager
def refusing_network(allowed: set[str]) -> Iterator[Guard]:
    """``socket.socket.connect`` and ``connect_ex`` refuse every address but loopback, Unix sockets and
    ``allowed`` while this holds."""
    guard = Guard(allowed=set(allowed))
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def connect(sock: socket.socket, address: Any) -> None:
        guard.check(sock.family, address)
        return real_connect(sock, address)

    def connect_ex(sock: socket.socket, address: Any) -> int:
        guard.check(sock.family, address)
        return real_connect_ex(sock, address)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    try:
        yield guard
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = real_connect_ex  # type: ignore[method-assign]
