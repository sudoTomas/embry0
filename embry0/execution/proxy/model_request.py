"""The request size the model proxies (xAI, Anthropic, the local model's) take, and their answer to one over it.

aiohttp refuses a request body over 1 MiB unless an app says otherwise, and an
agent's conversation outgrows that long before its model's window: every turn
resends the whole conversation, and a 300k-token prompt is more than a
megabyte of JSON. The cap is the operator's, handed to each proxy as
``MAX_REQUEST_BYTES``: ``MODEL_PROXY_MAX_REQUEST_BYTES`` for the xai- and
auth-proxy, 32 MiB by default, the size the Anthropic API itself takes;
``LOCAL_LLM_MAX_REQUEST_BYTES`` for the local-llm-proxy, 8 MiB by default.

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
) -> bytes | web.Response:
    """The request's body, or the 413 to answer when it is over the app's cap: ``setting`` is the variable the
    operator sets that cap with, and ``shape`` the error shape the proxy's clients read."""
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
        message = (
            f"request_too_large: the request is over the {limit}-byte cap of embry0's {proxy_name} "
            f"({setting}); the conversation outgrew what can be sent"
        )
        if shape == "openai":
            return openai_error("request_too_large", message, 413)
        return anthropic_error("request_too_large", message, 413)


def anthropic_error(error_type: str, message: str, status: int) -> web.Response:
    """An answer in the Anthropic API's error shape, which the agent SDK and the CLI read."""
    return web.json_response({"type": "error", "error": {"type": error_type, "message": message}}, status=status)


def openai_error(error_type: str, message: str, status: int) -> web.Response:
    """An answer in the OpenAI API's error shape, which the chat-completions executor reads (``error.message``)."""
    return web.json_response(
        {"error": {"message": message, "type": error_type, "param": None, "code": error_type}}, status=status
    )
