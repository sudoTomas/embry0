"""Local-model proxy: a sandbox's one way to the operator's model servers (ADR 0032).

A model server on the operator's network (vLLM, Ollama, LM Studio) sits in the
private ranges the sandbox egress drop blocks. Declaring its host as an egress
exception would open every port of that machine to the sandbox, which is more
than a model call needs when the machine also runs other services, and would
put the server's key in the sandbox. Every job on the custom provider reaches
its server through this proxy instead. It runs on ``sandbox-restricted`` like
the git and xAI proxies, and forwards each sandbox to the one server its job
resolved, the custom credential the orchestrator enrolled it with
(``local_llm``: the credential's id, its API root and its key), the way the
git-proxy scopes an enrolment to one repository:

- only ``POST /v1/chat/completions`` and ``GET /v1/models``; every other path
  is 404 and never reaches a server;
- only from an enrolled sandbox, and only to its own server: the per-sandbox
  bearer is checked and stripped, and the server's key (when it checks one) is
  added here, so neither the key nor the server's address enters a sandbox;
- a request body over ``MAX_REQUEST_BYTES`` (``LOCAL_LLM_MAX_REQUEST_BYTES``)
  is refused with 413, in the OpenAI error shape (``model_request``);
- every chat completion is metered (``metering``): its tokens count toward the
  run the orchestrator declared for the sandbox.

The orchestrator enrolls only the sandboxes of jobs that resolved the custom
provider, so a sandbox with no local model has no bearer this proxy accepts.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
import structlog
from aiohttp import web

from embry0.execution.proxy.app_keys import HTTP_CLIENT
from embry0.execution.proxy.enrollment import EnrollRejected, check_bearer, register_admin_routes
from embry0.execution.proxy.metering import (
    METER,
    Metered,
    NoRunError,
    TalliesFullError,
    no_run_answer,
    read_call,
    register_meter_routes,
)
from embry0.execution.proxy.model_request import openai_error, read_body

logger = structlog.get_logger(__name__)

PROXY_NAME = "local_llm_proxy"
DEFAULT_MAX_REQUEST_BYTES = 8 * 1024 * 1024
# A long completion from a model on one card can take minutes; the run's own
# timeout bounds it. Connect fails fast so an upstream that is down says so.
_TIMEOUT = httpx.Timeout(900.0, connect=10.0)
# The request headers forwarded upstream. Everything else, the sandbox's
# Authorization above all, stays behind.
_FORWARDED_REQUEST_HEADERS = ("content-type", "accept")
_FORWARDED_RESPONSE_HEADERS = ("content-type",)
# path under /v1 -> the one method allowed on it
_ROUTES: dict[str, str] = {"chat/completions": "POST", "models": "GET"}


#: The enroll body's field naming the sandbox's server, and the field the answer confirms it under.
LOCAL_LLM_FIELD = "local_llm"
#: The longest API root and key an enrollment may name.
_MAX_ROOT = 2048
_MAX_KEY = 8192
_MAX_CREDENTIAL_ID = 200


@dataclass(frozen=True, slots=True)
class Upstream:
    """The model server one sandbox reaches: the custom credential's id (``''`` for the environment's), its API
    root (``http://host:port/v1``) and its key (``''`` sends none)."""

    credential_id: str
    root: str
    api_key: str

    def __repr__(self) -> str:  # never the key in a log or a traceback
        return f"Upstream(credential_id={self.credential_id!r}, root={self.root!r})"


# This proxy's own state keys; those several proxies share are in ``app_keys``.
#: Per enrolled sandbox: the server it reaches.
UPSTREAM_BY_SANDBOX: web.AppKey[dict[str, Upstream]] = web.AppKey("upstream_by_sandbox")


def refused_server_host(host: str | None) -> str | None:
    """Why ``host`` may not be a model server, or None when it may.

    Refused: this machine (``localhost``, a loopback address, or the unspecified address that reaches it) and the
    link-local range, where a cloud's metadata service answers (169.254.169.254). Every other host is trusted, since
    only a deployment admin writes a custom credential's address. A name other than ``localhost`` is not resolved
    here: the orchestrator sends an http server's name as the address it resolves to, and that address is checked
    again where the proxy takes it.
    """
    name = (host or "").strip().strip("[]").rstrip(".").lower()
    if name == "localhost" or name.endswith(".localhost"):
        return f"{host} is this machine"
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_loopback:
        return f"{host} is this machine (a loopback address)"
    if address.is_unspecified:
        return f"{host} is this machine (the unspecified address)"
    if address.is_link_local:
        return f"{host} is link-local, where a cloud's metadata service answers"
    return None


def parse_upstream(value: Any) -> Upstream:
    """The enroll body's ``local_llm`` object, checked; :class:`EnrollRejected` for anything else. A root is an
    http(s) URL with a host and nothing past its path, kept without its trailing slash."""
    if not isinstance(value, dict):
        raise EnrollRejected("local_llm must be an object naming the sandbox's model server")
    credential_id, root, api_key = value.get("credential_id", ""), value.get("upstream"), value.get("api_key", "")
    if not isinstance(credential_id, str) or len(credential_id) > _MAX_CREDENTIAL_ID:
        raise EnrollRejected("local_llm.credential_id must be a string")
    if not isinstance(root, str) or not root or len(root) > _MAX_ROOT:
        raise EnrollRejected("local_llm.upstream must be the server's API root")
    parts = urlsplit(root)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise EnrollRejected("local_llm.upstream must be an http(s) URL with a host and no credentials or query")
    refused = refused_server_host(parts.hostname)
    if refused is not None:
        raise EnrollRejected(f"local_llm.upstream may not be a model server: {refused}")
    if not isinstance(api_key, str) or len(api_key) > _MAX_KEY or any(c in api_key for c in "\r\n"):
        raise EnrollRejected("local_llm.api_key must be a string of one line")
    return Upstream(credential_id=credential_id, root=root.rstrip("/"), api_key=api_key)


class _UpstreamEnroll:
    """Every enrollment names its sandbox's server (``local_llm``), and the answer confirms which one."""

    def parse(self, app: web.Application, body: dict[str, Any]) -> Upstream:
        if LOCAL_LLM_FIELD not in body:
            raise EnrollRejected("an enrollment names the sandbox's model server (local_llm)")
        return parse_upstream(body[LOCAL_LLM_FIELD])

    def apply(self, app: web.Application, sandbox_id: str, parsed: Upstream) -> dict[str, Any]:
        app[UPSTREAM_BY_SANDBOX][sandbox_id] = parsed
        return {LOCAL_LLM_FIELD: {"credential_id": parsed.credential_id, "upstream": parsed.root}}


def create_local_llm_proxy_app(
    admin_token: str,
    *,
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
    require_run: bool = False,
) -> web.Application:
    """The proxy app, forwarding each enrolled sandbox to the server it was enrolled with. With ``require_run`` a
    call from a sandbox with no declared run is refused."""
    if max_request_bytes <= 0:
        raise ValueError("MAX_REQUEST_BYTES must be positive")
    app = web.Application(client_max_size=max_request_bytes)
    app[UPSTREAM_BY_SANDBOX] = {}
    register_admin_routes(
        app, proxy_name=PROXY_NAME, admin_token=admin_token, on_unenroll=_forget, enroll_extension=_UpstreamEnroll()
    )
    register_meter_routes(app, proxy_name=PROXY_NAME, require_run=require_run)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_get("/health", _health_handler)
    app.router.add_route("*", "/v1/{path:.*}", _proxy_handler)
    return app


def _forget(app: web.Application, sandbox_id: str) -> None:
    """An enrollment ended: drop its server, and end its tallies (they stay for the orchestrator to read)."""
    app[UPSTREAM_BY_SANDBOX].pop(sandbox_id, None)
    app[METER].sandbox_gone(sandbox_id)


async def _on_startup(app: web.Application) -> None:
    app[HTTP_CLIENT] = httpx.AsyncClient(timeout=_TIMEOUT)


async def _on_cleanup(app: web.Application) -> None:
    client = app.get(HTTP_CLIENT)
    if client:
        await client.aclose()


async def _health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "proxy": "local_llm"})


def _upstream_headers(request: web.Request, server: Upstream) -> dict[str, str]:
    headers = {name: request.headers[name] for name in _FORWARDED_REQUEST_HEADERS if name in request.headers}
    if server.api_key:
        headers["Authorization"] = f"Bearer {server.api_key}"
    return headers


async def _proxy_handler(request: web.Request) -> web.StreamResponse:
    path = request.match_info.get("path", "").strip("/")
    method = _ROUTES.get(path)
    if method is None:
        return web.json_response({"error": "not_found"}, status=404)
    if request.method != method:
        return web.json_response({"error": "method_not_allowed"}, status=405)
    sandbox_id = check_bearer(request, PROXY_NAME)
    if sandbox_id is None:
        return web.json_response({"error": "unauthorized"}, status=401)

    upstream = request.app[UPSTREAM_BY_SANDBOX].get(sandbox_id)
    if upstream is None:
        logger.error("local_llm_proxy_no_server", sandbox_id=sandbox_id)
        return openai_error("no_server", "no model server is enrolled for this sandbox", 503)

    body = b""
    if method == "POST":
        read = await read_body(request, "local-llm-proxy", setting="LOCAL_LLM_MAX_REQUEST_BYTES", shape="openai")
        if isinstance(read, web.Response):
            return read
        body = read

    metered: Metered | None = None
    if method == "POST":
        call = read_call("openai_chat", body)
        body = call.body
        try:
            metered = request.app[METER].open(sandbox_id, call)
        except NoRunError:
            logger.warning("local_llm_proxy_call_without_run", sandbox_id=sandbox_id)
            return no_run_answer("openai_chat")
        except TalliesFullError:
            logger.error("local_llm_proxy_meter_full", sandbox_id=sandbox_id)
            return web.json_response({"error": "meter_full"}, status=503)

    client: httpx.AsyncClient = request.app[HTTP_CLIENT]
    url = f"{upstream.root}/{path}"
    logger.info(
        "local_llm_proxy_forward",
        sandbox_id=sandbox_id,
        credential_id=upstream.credential_id,
        path=path,
        bytes=len(body),
    )
    try:
        return await _forward(request, client, method, url, body, sandbox_id, metered, upstream)
    finally:
        if metered is not None:
            metered.close()


async def _forward(
    request: web.Request,
    client: httpx.AsyncClient,
    method: str,
    url: str,
    body: bytes,
    sandbox_id: str,
    metered: Metered | None,
    server: Upstream,
) -> web.StreamResponse:
    """Send the call upstream and stream its answer back through the meter. A call whose answer never came back
    after it was sent is charged its ceiling; one that never connected costs nothing."""
    try:
        async with client.stream(
            method, url, headers=_upstream_headers(request, server), content=body or None
        ) as upstream:
            if metered is not None:
                metered.answer(upstream.status_code, upstream.headers.get("content-type"))
            response = web.StreamResponse(status=upstream.status_code)
            for name in _FORWARDED_RESPONSE_HEADERS:
                if name in upstream.headers:
                    response.headers[name] = upstream.headers[name]
            await response.prepare(request)
            try:
                async for chunk in upstream.aiter_bytes():
                    if metered is not None:
                        metered.feed(chunk)
                    await response.write(chunk)
            except httpx.TransportError as exc:
                logger.warning("local_llm_proxy_stream_truncated", sandbox_id=sandbox_id, error=str(exc))
            await response.write_eof()
            return response
    except httpx.ConnectError:
        return web.json_response({"error": "the local model server is unreachable"}, status=502)
    except httpx.TimeoutException:
        if metered is not None:
            metered.lost()
        return web.json_response({"error": "the local model server timed out"}, status=504)
    except BaseException:
        if metered is not None:
            metered.lost()
        raise
