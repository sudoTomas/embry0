"""Orchestrator presign endpoint reverse proxy.

Forwards POST /api/v1/internal/qa/presign from sandboxes (in DinD) to the
host-side orchestrator. Auth (sandbox bearer token in body) is validated
upstream by the orchestrator. This proxy is just network plumbing — it
exists because DinD-spawned sandboxes can't resolve ``orchestrator`` via
Docker DNS (the orchestrator lives on the host's ``backend`` network, not
in DinD).
"""

from __future__ import annotations

import aiohttp
import structlog
from aiohttp import web

logger = structlog.get_logger(__name__)

_PRESIGN_PATH = "/api/v1/internal/qa/presign"


def create_presign_proxy_app(upstream_url: str) -> web.Application:
    """Build the aiohttp app that forwards /internal/qa/presign to the orchestrator.

    Args:
        upstream_url: e.g., ``http://orchestrator:8000``.
    """
    if not upstream_url:
        raise ValueError("UPSTREAM_URL is required")
    upstream_url = upstream_url.rstrip("/")

    app = web.Application()
    app["upstream_url"] = upstream_url

    async def _on_startup(app: web.Application) -> None:
        app["http"] = aiohttp.ClientSession()

    async def _on_cleanup(app: web.Application) -> None:
        await app["http"].close()

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_get("/health", _health_handler)
    app.router.add_post(_PRESIGN_PATH, _proxy_handler)
    return app


async def _health_handler(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def _proxy_handler(request: web.Request) -> web.Response:
    upstream_url: str = request.app["upstream_url"]
    http: aiohttp.ClientSession = request.app["http"]

    target = f"{upstream_url}{_PRESIGN_PATH}"
    body = await request.read()
    headers = {"Content-Type": request.headers.get("Content-Type", "application/json")}
    try:
        async with http.post(
            target,
            headers=headers,
            data=body,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as upstream_resp:
            text = await upstream_resp.text()
            return web.Response(
                status=upstream_resp.status,
                content_type=upstream_resp.content_type or "application/json",
                text=text,
            )
    except aiohttp.ClientError as exc:
        logger.warning("presign_proxy_upstream_error", error=str(exc), target=target)
        return web.Response(status=502, text=f"Upstream error: {exc}")
