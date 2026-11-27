"""The request size the model proxies (xAI, Anthropic, the local model's) take, and their answer to one over it.

aiohttp refuses a request body over 1 MiB unless an app says otherwise, and an
agent's conversation outgrows that long before its model's window: every turn
resends the whole conversation, and a 300k-token prompt is more than a
megabyte of JSON. The cap is the operator's, handed to each proxy as
``MAX_REQUEST_BYTES``: ``MODEL_PROXY_MAX_REQUEST_BYTES`` for the xai- and
auth-proxy, 32 MiB by default, the size the Anthropic API itself takes.
The llm-proxy reads each sandbox's body with that sandbox's own cap
(``read_body``'s ``limit``: the settings document's
``local_models.max_request_bytes``, 8 MiB by default), under its ceiling.

A request over the cap gets a 413 saying ``request_too_large``, in the error
shape its client reads: Anthropic's for the agent SDK, OpenAI's for the
chat-completions executor. Either way the run ends as
``ERR_CONTEXT_WINDOW_EXCEEDED`` instead of an unexplained error.
"""

from __future__ import annotations

from typing import Literal

import structlog
from aiohttp import web

logger = structlog.get_logger(__name__)

DEFAULT_MAX_REQUEST_BYTES = 32 * 1024 * 1024


async def read_body(
    request: web.Request,
    proxy_name: str,
    *,
    setting: str = "MODEL_PROXY_MAX_REQUEST_BYTES",
    shape: Literal["anthropic", "openai"] = "anthropic",
    limit: int | None = None,
) -> bytes | web.Response:
    """The request's body, or the 413 to answer when it is over the cap: ``limit`` when given (a cap of the
    caller's, no larger than the app's), else the app's own. ``setting`` is where the operator sets that cap, and
    ``shape`` the error shape the proxy's clients read."""
    cap: int = limit if limit is not None else request.app._client_max_size  # noqa: SLF001 - the app's own cap
    try:
        if request.content_length is not None and request.content_length > cap:
            raise web.HTTPRequestEntityTooLarge(max_size=cap, actual_size=request.content_length)
        body = await request.read()
        if len(body) > cap:
            raise web.HTTPRequestEntityTooLarge(max_size=cap, actual_size=len(body))
        return body
    except web.HTTPRequestEntityTooLarge:
        limit = cap
        logger.warning(
            "model_proxy_request_too_large",
            proxy=proxy_name,
            content_length=request.content_length,
            max_request_bytes=limit,
        )
        message = (
            f"request_too_large: the request is over the {limit}-byte cap of embry0's {proxy_name} "
            f"({setting}); the conversation outgrew what can be sent"
        )
        if shape == "openai":
            return openai_error("request_too_large", message, 413)
        return anthropic_error("request_too_large", message, 413)


#: The answer encodings a model proxy reads (httpx decodes them, so the meter and a scrub see the text). A proxy asks
#: its upstream for ``identity``; an answer in any other encoding is refused, never relayed.
READABLE_ENCODINGS = frozenset({"identity", "gzip", "deflate"})


def unreadable_encoding(header: str) -> str | None:
    """An answer's ``Content-Encoding`` when it names an encoding the proxy does not decode, else None."""
    named = [part.strip().lower() for part in header.split(",") if part.strip()]
    return header.strip() if any(part not in READABLE_ENCODINGS for part in named) else None


def anthropic_error(error_type: str, message: str, status: int) -> web.Response:
    """An answer in the Anthropic API's error shape, which the agent SDK and the CLI read."""
    return web.json_response({"type": "error", "error": {"type": error_type, "message": message}}, status=status)


def openai_error(error_type: str, message: str, status: int) -> web.Response:
    """An answer in the OpenAI API's error shape, which the chat-completions executor reads (``error.message``)."""
    return web.json_response(
        {"error": {"message": message, "type": error_type, "param": None, "code": error_type}}, status=status
    )
