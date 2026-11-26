"""Where an operator's model server is, and whether embry0 may connect there (ADR 0032).

A custom credential's address is the server every call on it reaches: the agent
runs, through the local-llm-proxy, and the orchestrator's own calls (background
models, the startup check, a credential's check). None of them may reach this
machine (loopback, or the unspecified address that reaches it) or the
link-local range, where a cloud's metadata service answers; every other host is
trusted, since only a deployment admin writes a custom credential's address.

A host is read the way the resolver reads it, not by its spelling: an IPv4
address in any form ``inet_aton`` takes (``2130706433``, ``0x7f000001``,
``0177.0.0.1``, ``127.1``, ``0``), an IPv6 address (IPv4-mapped too), or a
name, whose every address counts (``localtest.me`` resolves to 127.0.0.1).
And a call connects to the address that was checked (:func:`pin_server`), never
to the name again: a name that answers one address at the check and another at
the call (DNS rebinding) gets no second answer. The call keeps the name in its
URL, as the ``Host`` header, the TLS server name and the connection pool's key;
only its socket goes to the address (``pinned_client``).
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

Address = ipaddress.IPv4Address | ipaddress.IPv6Address

#: The system resolver, as every connection here uses it (a seam for tests, which fake name lookups).
_getaddrinfo = socket.getaddrinfo


class RefusedServer(ValueError):
    """A model server embry0 does not connect to: why, in words."""


class UnresolvedServer(ValueError):
    """A model server whose name resolves to no address here."""


def _unmapped(address: Address) -> Address:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def literal_address(host: str | None) -> Address | None:
    """The address ``host`` spells, in any form the resolver reads one; None for a name."""
    text = (host or "").strip().strip("[]")
    if not text:
        return None
    try:
        return _unmapped(ipaddress.ip_address(text.split("%", 1)[0]))
    except ValueError:
        pass
    try:
        return ipaddress.IPv4Address(socket.inet_aton(text.rstrip(".")))
    except (OSError, ValueError):
        return None


def _reason(address: Address) -> str | None:
    address = _unmapped(address)
    if address.is_loopback:
        return "this machine (a loopback address)"
    if address.is_unspecified:
        return "this machine (the unspecified address)"
    if address.is_link_local:
        return "link-local, where a cloud's metadata service answers"
    return None


def refused_address(address: Address) -> str | None:
    """Why ``address`` may not be a model server's, or None."""
    reason = _reason(address)
    return None if reason is None else f"{_unmapped(address)} is {reason}"


def refused_server_host(host: str | None) -> str | None:
    """Why ``host`` may not be a model server, read without a lookup: ``localhost`` and its subdomains, and an
    address in any spelling. None when only a lookup can tell (:func:`server_refusal`)."""
    name = (host or "").strip().strip("[]").rstrip(".").lower()
    if name == "localhost" or name.endswith(".localhost"):
        return f"{host} is this machine"
    address = literal_address(host)
    reason = None if address is None else _reason(address)
    if reason is None:
        return None
    return f"{host} is {reason}" if str(address) == str(host) else f"{host} is {address}, {reason}"


def resolve(host: str, port: int) -> list[Address]:
    """Every address ``host`` resolves to here, IPv4-mapped ones unmapped; :class:`UnresolvedServer` for none."""
    literal = literal_address(host)
    if literal is not None:
        return [literal]
    try:
        infos = _getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise UnresolvedServer(f"{host} resolves to no address here: {exc}") from exc
    found: list[Address] = []
    for info in infos:
        try:
            address = _unmapped(ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]))
        except ValueError:
            continue
        if address not in found:
            found.append(address)
    if not found:
        raise UnresolvedServer(f"{host} resolves to no address here")
    return found


def _host_port(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError as exc:
        raise RefusedServer(f"{url} is not an http(s) URL with a host and a port") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise RefusedServer(f"{url} is not an http(s) URL with a host")
    return parts.hostname, port or (443 if parts.scheme == "https" else 80)


def server_refusal(url: str) -> str | None:
    """Why the server ``url`` names may not be one, its every address read (a lookup: call it off the event loop);
    None when it may, or when its name resolves to nothing here (it is checked again where it is connected to)."""
    try:
        host, port = _host_port(url)
    except RefusedServer as exc:
        return str(exc)
    refused = refused_server_host(host)
    if refused is not None:
        return refused
    try:
        addresses = resolve(host, port)
    except UnresolvedServer:
        return None
    for address in addresses:
        reason = _reason(address)
        if reason is not None:
            return f"{host} resolves to {address}, {reason}"
    return None


@dataclass(frozen=True, slots=True)
class Pinned:
    """A model server's URL, and the one address a call to it connects to."""

    url: str
    address: str


def pin_server(url: str) -> Pinned:
    """The address calls to the server ``url`` names connect to: one it resolves to, after every one it resolves to
    is checked (a lookup: call it off the event loop). IPv4 first. :class:`RefusedServer` when any address may not be
    a model server's, :class:`UnresolvedServer` when there is none."""
    host, port = _host_port(url)
    refused = refused_server_host(host)
    if refused is not None:
        raise RefusedServer(refused)
    addresses = resolve(host, port)
    for address in addresses:
        reason = _reason(address)
        if reason is not None:
            raise RefusedServer(f"{host} resolves to {address}, {reason}")
    chosen = next((a for a in addresses if a.version == 4), addresses[0])
    return Pinned(url=url, address=str(chosen))
