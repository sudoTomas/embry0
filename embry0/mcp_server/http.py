"""Hosted MCP endpoint — the embry0 MCP server over streamable HTTP.

Runs inside the deployment (compose service ``mcp``) so a client needs no
local clone of this repo. The dashboard's API keys tab (Settings, then API
keys) shows the commands that connect Claude Code: the key goes to a file
only its owner can read, and Claude Code's settings hold a
``headersHelper`` command that reads it, so the key is never a command's
argument and never in those settings.

Auth model: the service itself holds NO credential. Every incoming MCP
request must carry ``Authorization: Bearer <key>``; the middleware stashes
it in a contextvar and :class:`embry0.mcp_server.client.Embry0Client` forwards it
upstream per request, so the orchestrator enforces the caller's own key and
scope on every tool call. A request without a bearer is rejected at the
door — the tool surface is not enumerable anonymously.
"""

from __future__ import annotations

import os

from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from embry0.mcp_server.client import caller_bearer
from embry0.mcp_server.server import mcp

_UNAUTHORIZED_BODY = b'{"error":"unauthorized","detail":"Authorization: Bearer <embry0 API key> is required"}'


class BearerForwardMiddleware:
    """Reject anonymous requests; stash the caller's bearer for the client."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

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
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": _UNAUTHORIZED_BODY})
            return
        token = caller_bearer.set(auth[7:].strip())
        try:
            await self._app(scope, receive, send)
        finally:
            caller_bearer.reset(token)


def transport_security() -> TransportSecuritySettings:
    """The endpoint's Host/Origin policy, read from ``MCP_ALLOWED_HOSTS``.

    The SDK's DNS-rebinding protection trusts only localhost by default and
    421s anything else ("Invalid Host header"). This service sits behind
    nginx on arbitrary deployment hostnames; rebinding attacks don't apply —
    every request already needs a Bearer key, and browsers can't attach one
    cross-origin. MCP_ALLOWED_HOSTS narrows it back when desired.
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
