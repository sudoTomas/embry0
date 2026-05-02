"""MinIO reverse proxy — transparent HTTP forwarder so DinD-spawned sandboxes
can reach the host-side MinIO via this proxy on a known hostname.

Listens on LISTEN_PORT (default 9100) and forwards every request to
UPSTREAM_URL (e.g., http://minio:9000) preserving method, headers, and body.

Critically: does NOT rewrite the Host header. MinIO presigned URLs sign the
Host header used by the client, so the signature only validates if the request
arrives at MinIO with the same Host the client used. The orchestrator mints
URLs with the proxy's externally-visible hostname (``minio-proxy:9100``); this
proxy forwards verbatim.
"""

from __future__ import annotations

import aiohttp
import structlog
from aiohttp import web

logger = structlog.get_logger(__name__)


# Headers that must NOT be forwarded across a proxy hop. RFC 7230 § 6.1
# (hop-by-hop) plus content-length / transfer-encoding which aiohttp
# manages itself.
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
}


def create_minio_proxy_app(upstream_url: str) -> web.Application:
    """Build the aiohttp app that forwards every request to MinIO.

    Args:
        upstream_url: e.g., ``http://minio:9000`` (orchestrator-side reachable URL).
    """
    if not upstream_url:
        raise ValueError("UPSTREAM_URL is required")
    upstream_url = upstream_url.rstrip("/")

    app = web.Application(client_max_size=0)  # 0 = no limit; MinIO handles its own caps
    app["upstream_url"] = upstream_url

    async def _on_startup(app: web.Application) -> None:
        # Build the session inside startup so it binds to the running loop
        # used by web.run_app, not whatever thread/loop created the app.
        app["http"] = aiohttp.ClientSession(auto_decompress=False)

    async def _on_cleanup(app: web.Application) -> None:
        await app["http"].close()

    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_get("/health", _health_handler)
    # Catch-all for everything else — forward to upstream
    app.router.add_route("*", "/{path:.*}", _proxy_handler)
    return app


async def _health_handler(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def _proxy_handler(request: web.Request) -> web.StreamResponse:
    upstream_url: str = request.app["upstream_url"]
    http: aiohttp.ClientSession = request.app["http"]

    # rel_url preserves the path AND the original query string (signed by S3).
    target = f"{upstream_url}{request.rel_url}"

    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}

    # MinIO can serve large objects; read into memory for now (Phase 1.5
    # artifacts are kilobytes). Streaming both directions is a future enhancement.
    body = await request.read()
    try:
        async with http.request(
            method=request.method,
            url=target,
            headers=headers,
            data=body,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as upstream_resp:
            response_body = await upstream_resp.read()
            response_headers = {
                k: v
                for k, v in upstream_resp.headers.items()
                if k.lower() not in _HOP_BY_HOP
            }
            return web.Response(
                status=upstream_resp.status,
                headers=response_headers,
                body=response_body,
            )
    except aiohttp.ClientError as exc:
        logger.warning("minio_proxy_upstream_error", error=str(exc), target=target)
        return web.Response(status=502, text=f"Upstream error: {exc}")
