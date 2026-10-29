"""Fake upstreams on a loopback port, and which of their requests a test counts (#214, #221).

A fake upstream listens on a ``127.0.0.1`` port, and any process on the host
can connect to it: a ``GET /`` from an unrelated client once failed a test that
expected nothing to reach its fake. So a fake counts a request only when its
path is one the test's traffic uses, or when it carries a credential the code
under test is expected to send, wherever it goes: a proxy that leaks may send
it to another path, and no unrelated process has it. Everything else is kept
apart, where it can neither fail a test that expects nothing to arrive nor pass
one that expects something to. A test that sends strays itself checks them as
a subset of what was kept apart: a real stray from another process lands there
too.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from aiohttp import web


def carries_credential(headers: Mapping[str, str], credentials: Iterable[str]) -> bool:
    """Whether any header holds one of ``credentials``, as sent or inside HTTP Basic."""
    wanted = [c for c in credentials if c]
    if not wanted:
        return False
    values = list(headers.values())
    for value in headers.values():
        scheme, _, encoded = value.partition(" ")
        if scheme.lower() == "basic":
            try:
                values.append(base64.b64decode(encoded, validate=True).decode(errors="replace"))
            except (binascii.Error, ValueError):
                pass
    return any(credential in value for credential in wanted for value in values)


def is_test_request(path: str, headers: Mapping[str, str], *, paths: Iterable[str], credentials: Iterable[str]) -> bool:
    """Whether a request is the test's: under one of the ``paths`` prefixes, or carrying one of ``credentials``."""
    return path.startswith(tuple(paths)) or carries_credential(headers, credentials)


@dataclass
class Seen:
    method: str
    path: str
    query: str
    headers: Mapping[str, str]  # aiohttp's own, so a lookup ignores case
    body: bytes = b""
    raw_path: str = ""  # the target as sent, query included; ``path`` decodes an encoded ``/``


@dataclass
class RecordingUpstream:
    """Answers every request with ``status``/``body``; keeps the test's requests in ``seen``.

    ``seen`` holds the requests under one of ``paths`` (prefixes, such as
    ``/acme/widgets.git/``) and those carrying one of ``credentials``, wherever
    they go. Any other request is answered the same way and kept in
    ``ignored``. With neither set, every request is ignored, so a test that
    builds one itself names both; ``ProxyRig`` fills them from what it enrolls.
    """

    status: int = 200
    body: bytes = b"upstream-body"
    content_type: str = "application/x-git-upload-pack-advertisement"
    extra_headers: dict[str, str] = field(default_factory=dict)
    paths: set[str] = field(default_factory=set)
    credentials: set[str] = field(default_factory=set)
    seen: list[Seen] = field(default_factory=list)
    ignored: list[Seen] = field(default_factory=list)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", self._handle)
        return app

    async def _handle(self, request: web.Request) -> web.Response:
        body = await request.read()
        seen = Seen(request.method, request.path, request.query_string, request.headers, body, request.raw_path)
        counted = is_test_request(request.path, request.headers, paths=self.paths, credentials=self.credentials)
        (self.seen if counted else self.ignored).append(seen)
        return web.Response(
            status=self.status, body=self.body, content_type=self.content_type, headers=self.extra_headers
        )

    def authorizations(self) -> list[str]:
        return [s.headers.get("Authorization", "") for s in self.seen]
