"""HTTP workspace initializer — orchestrator-side fetch staged into /workspace.

The fetch happens on the ORCHESTRATOR, never inside the sandbox: sandbox
egress is deliberately proxy-mediated, and punching per-URL holes in the
restricted network would undo that design. The downloaded body is staged
into the container via ``docker cp`` alongside a provenance note.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from embry0.workspace_init.base import HttpFetchError, InitContext

logger = structlog.get_logger(__name__)

_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")

_DEFAULT_MAX_BYTES = 52_428_800  # 50 MiB
_DEFAULT_TIMEOUT_SECONDS = 60


def _staged_filename(url: str) -> str:
    """Filename for the staged body: 'source' + sanitized extension from the URL path."""
    suffix = Path(urlparse(url).path).suffix
    suffix = _FILENAME_SAFE_RE.sub("", suffix)[:16]
    return f"source{suffix}" if suffix else "source"


def _assert_host_allowed(url: str, *, allow_private: bool) -> None:
    """SSRF guard: refuse loopback/link-local/private targets unless opted in."""
    if allow_private:
        return
    host = urlparse(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise HttpFetchError(f"http context host {host!r} does not resolve: {exc}") from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.is_loopback or addr.is_link_local or addr.is_private:
            raise HttpFetchError(
                f"http context host {host!r} resolves to a private address ({addr}) — "
                "set HTTP_CONTEXT_ALLOW_PRIVATE=true to permit internal URLs"
            )


class HttpWorkspaceInitializer:
    name = "http"

    def validate(self, context: dict[str, Any], config: Any | None) -> None:
        url = context.get("url") or ""
        if not re.match(r"^https?://", url):
            raise HttpFetchError(f"http context requires an http(s) url, got {url!r}")
        allow_private = bool(getattr(config, "http_context_allow_private", False))
        _assert_host_allowed(url, allow_private=allow_private)

    async def initialize(self, ctx: InitContext) -> dict[str, Any]:
        url = ctx.context.get("url") or ""
        max_bytes = int(getattr(ctx.config, "http_context_max_bytes", _DEFAULT_MAX_BYTES))
        timeout = int(getattr(ctx.config, "http_context_timeout_seconds", _DEFAULT_TIMEOUT_SECONDS))
        allow_private = bool(getattr(ctx.config, "http_context_allow_private", False))
        # Re-check at fetch time — DNS may have changed since validate().
        _assert_host_allowed(url, allow_private=allow_private)

        tmp = tempfile.NamedTemporaryFile(prefix="embry0-http-ctx-", delete=False)
        tmp_path = Path(tmp.name)
        received = 0
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, max_redirects=5) as client:
                async with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    declared = resp.headers.get("Content-Length")
                    if declared and int(declared) > max_bytes:
                        raise HttpFetchError(f"http context body {declared} bytes exceeds cap {max_bytes}")
                    async for chunk in resp.aiter_bytes():
                        received += len(chunk)
                        if received > max_bytes:
                            raise HttpFetchError(f"http context body exceeds cap {max_bytes} bytes (aborted)")
                        tmp.write(chunk)
            tmp.close()

            filename = _staged_filename(url)
            await ctx.docker.run_cmd(
                ctx.docker.build_exec_cmd(ctx.container_id, ["mkdir", "-p", "/workspace"]),
                timeout=10,
            )
            await ctx.docker.copy_into(ctx.container_id, str(tmp_path), f"/workspace/{filename}")
            await ctx.docker.copy_bytes_into(
                ctx.container_id,
                f"{url}\n".encode(),
                "/workspace/SOURCE_URL.txt",
            )
        except httpx.HTTPError as exc:
            raise HttpFetchError(f"http context fetch failed for {url}: {exc}") from exc
        finally:
            tmp.close()
            tmp_path.unlink(missing_ok=True)

        ctx.emit({"type": "progress", "message": f"Fetched {received} bytes from {url} into /workspace"})
        logger.info("http_context_staged", job_id=ctx.job_id, url=url, bytes=received)
        return {}
