"""The request size the model proxies (xAI, Anthropic) take, and their answer to one over it.

aiohttp refuses a request body over 1 MiB unless an app says otherwise, and an
agent's conversation outgrows that long before its model's window: every turn
resends the whole conversation, and a 300k-token prompt is more than a
megabyte of JSON. The cap is the operator's (``MODEL_PROXY_MAX_REQUEST_BYTES``,
handed to each proxy as ``MAX_REQUEST_BYTES``); 32 MiB by default, the size
the Anthropic API itself takes.

A request over the cap gets a 413 in the Anthropic error shape the agent SDK
reads, saying ``request_too_large``, so the run ends as
``ERR_CONTEXT_WINDOW_EXCEEDED`` instead of an unexplained error.
"""

from __future__ import annotations

import structlog
from aiohttp import web

logger = structlog.get_logger(__name__)

DEFAULT_MAX_REQUEST_BYTES = 32 * 1024 * 1024


async def read_body(request: web.Request, proxy_name: str) -> bytes | web.Response:
    """The request's body, or the 413 to answer when it is over the app's cap."""
    try:
        return await request.read()
    except web.HTTPRequestEntityTooLarge:
        limit = request.app._client_max_size  # noqa: SLF001 - the cap the app was built with
        logger.warning(
            "model_proxy_request_too_large",
            proxy=proxy_name,
            content_length=request.content_length,
            max_request_bytes=limit,
        )
        return web.json_response(
            {
                "type": "error",
                "error": {
                    "type": "request_too_large",
                    "message": (
                        f"request_too_large: the request is over the {limit}-byte cap of embry0's {proxy_name} "
                        "(MODEL_PROXY_MAX_REQUEST_BYTES); the conversation outgrew what can be sent"
                    ),
                },
            },
            status=413,
        )
