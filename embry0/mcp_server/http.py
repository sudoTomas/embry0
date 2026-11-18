"""Hosted MCP endpoint — the embry0 MCP server over streamable HTTP.

Runs inside the deployment (compose service ``mcp``) so a client needs no
local clone of this repo: ``claude mcp add --transport http embry0
https://<host>/mcp --header "Authorization: Bearer <key>"``.

Auth model: the service itself holds NO credential. Every incoming MCP
request must carry ``Authorization: Bearer <key>``; the middleware stashes
it in a contextvar and :class:`embry0.mcp_server.client.Embry0Client` forwards it
upstream per request, so the orchestrator enforces the caller's own key and
scope on every tool call. Before anything is served (``initialize``, the
tool and prompt lists, a call), the orchestrator is asked whether it takes
the key, as a tool call would be answered (:class:`KeyCheck`): a request
without a bearer, or with one it refuses, is rejected at the door with 401,
and one it cannot check now with 503. The tool surface is not enumerable
without a key.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from embry0.mcp_server.client import Embry0APIError, caller_bearer
from embry0.mcp_server.server import _get_client, mcp

logger = logging.getLogger(__name__)

_UNAUTHORIZED_BODY = b'{"error":"unauthorized","detail":"Authorization: Bearer <embry0 API key> is required"}'
_REFUSED_BODY = b'{"error":"unauthorized","detail":"the embry0 API does not take this key"}'
_UNCHECKED_BODY = b'{"error":"unavailable","detail":"the embry0 API could not check this key; try again"}'

#: How long a key the orchestrator took is taken again without asking: tool calls are checked on every call.
KEY_CHECK_TTL_SECONDS = 30.0
#: The most keys kept taken at once; the least recently used goes first.
KEY_CHECK_MAX_ENTRIES = 1024


class KeyCheck:
    """Whether the orchestrator takes a bearer, asked as a tool call is answered (``Embry0Client.key_accepted``).

    True when it does, False when it refuses the key (401), None when it
    cannot say (unreachable, any other error). A key it took is kept, by its
    SHA-256, for ``ttl`` seconds, at most ``max_entries`` of them (the least
    recently used goes first); a refusal is never kept.
    """

    def __init__(
        self,
        *,
        ttl: float = KEY_CHECK_TTL_SECONDS,
        max_entries: int = KEY_CHECK_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl
        self._max_entries = max_entries
        self._clock = clock
        #: The digest of each key taken, and until when.
        self._taken: OrderedDict[str, float] = OrderedDict()

    async def __call__(self, bearer: str) -> bool | None:
        digest = hashlib.sha256(bearer.encode()).hexdigest()
        until = self._taken.get(digest)
        if until is not None and until > self._clock():
            self._taken.move_to_end(digest)
            return True
        self._taken.pop(digest, None)
        token = caller_bearer.set(bearer)
        try:
            taken = await _get_client().key_accepted()
        except Embry0APIError as exc:
            logger.warning("mcp_key_check_failed status=%s detail=%s", exc.status_code, exc.detail[:200])
            return None
        finally:
            caller_bearer.reset(token)
        if taken:
            self._taken[digest] = self._clock() + self._ttl
            while len(self._taken) > self._max_entries:
                self._taken.popitem(last=False)
        return taken


async def _refuse(send: Send, status: int, body: bytes) -> None:
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body})


class BearerForwardMiddleware:
    """Serve only a request whose bearer the orchestrator takes; stash the caller's bearer for the client."""

    def __init__(self, app: ASGIApp, check: Callable[[str], Awaitable[bool | None]] | None = None) -> None:
        self._app = app
        self._check = check if check is not None else KeyCheck()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        auth = ""
        for name, value in scope.get("headers") or []:
            if name == b"authorization":
                auth = value.decode("latin-1")
                break
        if not auth.lower().startswith("bearer ") or not auth[7:].strip():
            await _refuse(send, 401, _UNAUTHORIZED_BODY)
            return
        bearer = auth[7:].strip()
        taken = await self._check(bearer)
        if taken is None:
            await _refuse(send, 503, _UNCHECKED_BODY)
            return
        if not taken:
            await _refuse(send, 401, _REFUSED_BODY)
            return
        token = caller_bearer.set(bearer)
        try:
            await self._app(scope, receive, send)
        finally:
            caller_bearer.reset(token)


def transport_security() -> TransportSecuritySettings:
    """The endpoint's Host/Origin policy, read from ``MCP_ALLOWED_HOSTS``.

    The SDK's DNS-rebinding protection trusts only localhost by default and
    421s anything else ("Invalid Host header"). This service sits behind
    nginx on arbitrary deployment hostnames; rebinding attacks don't apply —
    every request already needs a Bearer key the orchestrator takes, and
    browsers can't attach one cross-origin. MCP_ALLOWED_HOSTS narrows it back
    when desired.
    """
    allowed = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(allowed),
        allowed_hosts=allowed,
    )


def build_app() -> BearerForwardMiddleware:
    """The ASGI app: the MCP server's streamable HTTP app behind the bearer middleware."""
    return BearerForwardMiddleware(
        mcp.streamable_http_app(
            streamable_http_path="/mcp",
            # Stateless: each request self-contained — no server-side session pinning,
            # so the endpoint works behind nginx/Cloudflare without sticky routing.
            stateless_http=True,
            # Always passed: given None, the SDK falls back to its localhost-only
            # policy and every request through the proxy gets 421.
            transport_security=transport_security(),
        )
    )


def main() -> None:
    """Console entry point: serve the hosted MCP endpoint."""
    import uvicorn

    uvicorn.run(
        build_app(),
        host=os.environ.get("MCP_HTTP_HOST", "0.0.0.0"),  # noqa: S104 — container-internal bind
        port=int(os.environ.get("MCP_HTTP_PORT", "9200")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
