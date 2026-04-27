"""Shared proxy utilities."""

import asyncio

import structlog
from aiohttp import web

logger = structlog.get_logger(__name__)


async def start_proxy(
    app: web.Application,
    host: str = "0.0.0.0",
    port: int = 8080,
) -> tuple[web.AppRunner, asyncio.Event]:
    """Start an aiohttp proxy server. Returns (runner, ready_event)."""
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    ready = asyncio.Event()
    ready.set()
    logger.info("proxy_started", host=host, port=port)
    return runner, ready
