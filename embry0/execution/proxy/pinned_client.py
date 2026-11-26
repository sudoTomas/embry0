"""An HTTP client whose every connection goes to a model server's checked address (ADR 0032).

A call to an operator's model server connects to the address its name was
resolved to and checked at (``server_address.pin_server``), never to the name
again. The URL keeps the name: it is the ``Host`` header, the TLS server name
and the certificate's name, and it is what the connection pool is keyed by. So
two servers that share an address but not a name never share a connection,
and each TLS handshake checks its own name. A pool keyed by the address would
hand a connection verified for one name to a call for another.

Only the socket is pinned: :func:`pinning_client` builds the client, and a call
made inside :func:`pinned` connects to that call's address. Its network backend
connects nowhere else; a connection opened outside ``pinned``, or to another
host or port, is refused, so the client can never reach a server by a lookup of
its own. A connection the pool keeps for a name was opened to an address that
was checked for that name, so a later call to that name may go over it.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx

_DEFAULT_PORTS = {"http": 80, "https": 443}

#: The pin of the call this task is making: the server's host and port as its URL names them, and the address
#: the connection goes to.
_PIN: ContextVar[tuple[str, int, str] | None] = ContextVar("model_server_pin", default=None)


class UnpinnedConnection(httpcore.ConnectError):
    """A connection a pinning client was asked to open with no pin for its host and port."""


def _host_port(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    host = parts.hostname or ""  # urlsplit lowers it, as httpx does
    port = parts.port or _DEFAULT_PORTS.get(parts.scheme)
    if not host or port is None:
        raise ValueError(f"not a model server's URL: {parts.scheme}://{parts.hostname or ''}")
    return host, port


@contextmanager
def pinned(url: str, address: str) -> Iterator[None]:
    """Within it, a :func:`pinning_client`'s connection to ``url``'s host and port goes to ``address``; the call
    itself goes to ``url`` unchanged."""
    host, port = _host_port(url)
    token = _PIN.set((host, port, str(ipaddress.ip_address(address))))
    try:
        yield
    finally:
        _PIN.reset(token)


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    """Opens each connection to the address pinned for its host and port, and refuses any other."""

    def __init__(self) -> None:
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        pin = _PIN.get()
        if pin is None or (host, port) != pin[:2]:
            raise UnpinnedConnection(f"no checked address for {host}:{port}: a model server is reached only pinned")
        return await self._inner.connect_tcp(
            pin[2], port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Iterable[Any] | None = None
    ) -> httpcore.AsyncNetworkStream:
        raise UnpinnedConnection("a model server is reached over TCP, pinned")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _PinningTransport(httpx.AsyncHTTPTransport):
    """httpx's own transport over a connection pool whose network backend is :class:`_PinnedBackend`."""

    def __init__(self, *, verify: Any = True, limits: httpx.Limits) -> None:
        super().__init__(verify=verify, trust_env=False, limits=limits)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=verify, trust_env=False),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            network_backend=_PinnedBackend(),
        )


#: httpx's own default limits.
_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=5.0)


def pinning_client(*, verify: Any = True, **kwargs: Any) -> httpx.AsyncClient:
    """A client for model servers: each call is made inside :func:`pinned`, to the server's URL with its name.
    ``verify`` is httpx's; the other arguments are the client's (no ``transport``, which this is)."""
    return httpx.AsyncClient(transport=_PinningTransport(verify=verify, limits=_LIMITS), **kwargs)
