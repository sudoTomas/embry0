"""Name lookups and loopback for tests of where embry0 connects to a model server (ADR 0032).

Every connection to an operator's server resolves its host with the system resolver and refuses this machine and
link-local (``embry0.execution.proxy.server_address``). A test never reaches real DNS: :func:`fake_dns` answers the
names it gives and refuses every other. Fake model servers listen on 127.0.0.1, which no real server may be:
:func:`allow_loopback_test_servers` lets exactly that address through for a test that needs one, and every other
refusal stays.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

try:
    from embry0.execution.proxy import server_address
except ImportError:  # a tree from before the check: these fakes change nothing, and its tests fail on their own terms
    server_address = None  # type: ignore[assignment]


def fake_dns(monkeypatch: pytest.MonkeyPatch, names: Mapping[str, Sequence[str]]) -> list[str]:
    """Answer each of ``names`` with its addresses, and every other name with "not known". Returns the names looked
    up, in order."""
    asked: list[str] = []

    def lookup(host: str, port: Any, *args: Any, **kwargs: Any) -> list[Any]:
        asked.append(host)
        if host not in names:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        answers: list[Any] = []
        for text in names[host]:
            address = ipaddress.ip_address(text)
            if address.version == 6:
                answers.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (text, port, 0, 0)))
            else:
                answers.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (text, port)))
        return answers

    if server_address is not None:
        monkeypatch.setattr(server_address, "_getaddrinfo", lookup)
    return asked


def allow_loopback_test_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let exactly 127.0.0.1 be a model server, for a test whose fake server listens there."""
    if server_address is None:
        return
    real = server_address._reason

    def reason(address: Any) -> str | None:
        return None if str(server_address._unmapped(address)) == "127.0.0.1" else real(address)

    monkeypatch.setattr(server_address, "_reason", reason)
