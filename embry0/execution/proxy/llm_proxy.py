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
  added here, so neither the key nor the server's address enters a sandbox; an
  answer that names a key the proxy holds (an error message quoting the key it
  was sent) has it replaced on its way back (``_Scrubber``, each key as written,
  not an escaped or encoded form of it, and none shorter than 8 characters). A
  server is asked for answers uncompressed, and one that comes compressed in a
  way the proxy does not decode (only gzip and deflate are) is refused, never
  relayed: its bytes could not be scrubbed;
- a request body over the sandbox's cap (``local_models.max_request_bytes``,
  sent with its enrollment under the ceiling ``MAX_REQUEST_BYTES``) is refused
  with 413, in the OpenAI error shape (``model_request``), and a server has the
  enrollment's timeout to answer: one set of limits per sandbox, on every path
  it calls, the custom server's and each provider's;
- every chat completion is metered (``metering``): its tokens count toward the
  run the orchestrator declared for the sandbox.

The other chat-completions providers (OpenAI, OpenRouter, Google, Groq,
DeepSeek) take the same route (ADR 0033): an enrollment names, under
``provider_servers``, the server a sandbox reaches each of them through (the
job's credential for it: its id, API root and key), and the sandbox reaches it
at ``/p/<provider>/v1/chat/completions`` and ``/p/<provider>/v1/models``, with
the same checks, so no provider's key enters a sandbox. ``/p/custom/v1/...`` is
the custom server's path too. A provider the sandbox's enrollment does not
name is refused, and so is every provider but the one its current run was
declared on (``POST /admin/runs``): a run's calls, and its tally, are its own
provider's. A provider's path takes no call outside a run: before the
sandbox's first run is declared, and after its run ends, ``/p/<provider>/v1``
is refused, so every call there is some run's. (``/v1``, which older images
call for the custom provider, still forwards outside a run, counted as the
sandbox's calls under no run; ``METER_REQUIRE_RUN`` decides that for every
path.)

The orchestrator enrolls only the sandboxes of jobs that resolved a
chat-completions provider, so any other sandbox has no bearer this proxy
accepts.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
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
from embry0.execution.proxy.model_request import openai_error, read_body, unreadable_encoding
from embry0.execution.proxy.pinned_client import pinned, pinning_client
from embry0.execution.proxy.server_address import refused_address, refused_server_host
from embry0.safety.redaction import REDACTED

logger = structlog.get_logger(__name__)

PROXY_NAME = "llm_proxy"
#: The settings document's local_models defaults: a sandbox's request cap and the time a server has to answer it.
DEFAULT_MAX_REQUEST_BYTES = 8 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 900.0
#: The most an enrollment may ask for, whatever the settings say: the proxy reads no body larger, and waits no
#: longer. ``local_models``'s own bounds are these (a test holds them equal).
MAX_REQUEST_BYTES_CEILING = 64 * 1024 * 1024
TIMEOUT_SECONDS_CEILING = 3600.0
# A long completion from a model on one card can take minutes; the enrollment's timeout bounds it. Connect fails
# fast so an upstream that is down says so.
_CONNECT_SECONDS = 10.0
_TIMEOUT = httpx.Timeout(DEFAULT_TIMEOUT_SECONDS, connect=_CONNECT_SECONDS)
# The request headers forwarded upstream. Everything else, the sandbox's
# Authorization above all, stays behind.
_FORWARDED_REQUEST_HEADERS = ("content-type", "accept")
_FORWARDED_RESPONSE_HEADERS = ("content-type",)
#: The shortest key scrubbed from an answer: a shorter one (a placeholder) would replace ordinary words.
_MIN_SCRUBBED_KEY = 8
# path under /v1 -> the one method allowed on it
_ROUTES: dict[str, str] = {"chat/completions": "POST", "models": "GET"}


#: The enroll body's field naming the sandbox's server, and the field the answer confirms it under.
LOCAL_LLM_FIELD = "local_llm"
#: The enroll body's field with the sandbox's limits at every server it is enrolled for, and the field the answer
#: confirms them under.
LIMITS_FIELD = "limits"
#: The enroll body's field naming, per chat-completions provider other than custom, the server the sandbox reaches it
#: through; the answer confirms each under the same field, without its key.
PROVIDER_SERVERS_FIELD = "provider_servers"
#: The provider ``local_llm`` serves.
CUSTOM_PROVIDER = "custom"
#: A provider's name, in a path and in ``provider_servers``.
_PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
#: The most providers one enrollment may name.
_MAX_PROVIDERS = 16
#: The longest API root and key an enrollment may name.
_MAX_ROOT = 2048
_MAX_KEY = 8192
_MAX_CREDENTIAL_ID = 200


@dataclass(frozen=True, slots=True)
class Upstream:
    """The model server one sandbox reaches: the custom credential's id (``''`` for the environment's), its API
    root (``http://host:port/v1``), its key (``''`` sends none) and the address the orchestrator resolved and
    checked for it, which every call connects to (``server_address``)."""

    credential_id: str
    root: str
    api_key: str
    address: str = ""

    def __repr__(self) -> str:  # never the key in a log or a traceback
        return f"Upstream(credential_id={self.credential_id!r}, root={self.root!r}, address={self.address!r})"


@dataclass(frozen=True, slots=True)
class Limits:
    """A sandbox's limits at every server it is enrolled for (the settings document's ``local_models``): the largest
    request body it may send, and how long a server has to answer one."""

    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def to_body(self) -> dict[str, Any]:
        """As the enroll body carries them, and the answer confirms them."""
        return {"max_request_bytes": self.max_request_bytes, "timeout_seconds": float(self.timeout_seconds)}


# This proxy's own state keys; those several proxies share are in ``app_keys``.
#: Per enrolled sandbox: the server it reaches.
UPSTREAM_BY_SANDBOX: web.AppKey[dict[str, Upstream]] = web.AppKey("upstream_by_sandbox")
#: Per enrolled sandbox: the server of each other chat-completions provider it was enrolled for.
SERVERS_BY_SANDBOX: web.AppKey[dict[str, dict[str, Upstream]]] = web.AppKey("servers_by_sandbox")
#: Per enrolled sandbox: its limits, on every path it calls.
LIMITS_BY_SANDBOX: web.AppKey[dict[str, Limits]] = web.AppKey("limits_by_sandbox")
#: The ceiling of an enrollment's request cap: the largest body the proxy reads at all.
MAX_REQUEST_BYTES: web.AppKey[int] = web.AppKey("max_request_bytes")


def _limit(value: Any, name: str, ceiling: float) -> float:
    """A positive number no larger than ``ceiling``; :class:`EnrollRejected` for anything else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= ceiling:
        raise EnrollRejected(f"{LIMITS_FIELD}.{name} must be a positive number no larger than {ceiling:g}")
    return value


def parse_limits(value: Any, *, max_request_bytes: int = MAX_REQUEST_BYTES_CEILING) -> Limits:
    """The enroll body's ``limits``, checked against the ceilings (``max_request_bytes`` the one this proxy started
    with); :class:`EnrollRejected` for anything else."""
    if not isinstance(value, dict):
        raise EnrollRejected(f"{LIMITS_FIELD} must be an object: the sandbox's max_request_bytes and timeout_seconds")
    cap = _limit(value.get("max_request_bytes"), "max_request_bytes", max_request_bytes)
    if not isinstance(cap, int):
        raise EnrollRejected(f"{LIMITS_FIELD}.max_request_bytes must be a whole number of bytes")
    timeout = _limit(value.get("timeout_seconds"), "timeout_seconds", TIMEOUT_SECONDS_CEILING)
    return Limits(max_request_bytes=cap, timeout_seconds=float(timeout))


def parse_upstream(value: Any, named: str = LOCAL_LLM_FIELD) -> Upstream:
    """The enroll body's ``local_llm`` object (or one provider's, named ``named`` in the messages), checked;
    :class:`EnrollRejected` for anything else. A root is an http(s) URL with a host and nothing past its path, kept
    without its trailing slash."""
    if not isinstance(value, dict):
        raise EnrollRejected(f"{named} must be an object naming the sandbox's model server")
    credential_id, root, api_key = value.get("credential_id", ""), value.get("upstream"), value.get("api_key", "")
    if not isinstance(credential_id, str) or len(credential_id) > _MAX_CREDENTIAL_ID:
        raise EnrollRejected(f"{named}.credential_id must be a string")
    if not isinstance(root, str) or not root or len(root) > _MAX_ROOT:
        raise EnrollRejected(f"{named}.upstream must be the server's API root")
    parts = urlsplit(root)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise EnrollRejected(f"{named}.upstream must be an http(s) URL with a host and no credentials or query")
    refused = refused_server_host(parts.hostname)
    if refused is not None:
        raise EnrollRejected(f"{named}.upstream may not be a model server: {refused}")
    address = value.get("address")
    try:
        pinned = ipaddress.ip_address(address) if isinstance(address, str) else None
    except ValueError:
        pinned = None
    if pinned is None:
        raise EnrollRejected(f"{named}.address must be the address the server resolved to, as an IP address")
    refused = refused_address(pinned)
    if refused is not None:
        raise EnrollRejected(f"{named}.address may not be a model server's: {refused}")
    if not isinstance(api_key, str) or len(api_key) > _MAX_KEY or any(c in api_key for c in "\r\n"):
        raise EnrollRejected(f"{named}.api_key must be a string of one line")
    return Upstream(credential_id=credential_id, root=root.rstrip("/"), api_key=api_key, address=str(pinned))


def parse_provider_servers(value: Any) -> dict[str, Upstream]:
    """The enroll body's ``provider_servers`` map, checked: each key a provider's name other than custom (whose
    server is ``local_llm``), each value a server as :func:`parse_upstream` reads it."""
    if not isinstance(value, dict) or len(value) > _MAX_PROVIDERS:
        raise EnrollRejected(f"{PROVIDER_SERVERS_FIELD} must map at most {_MAX_PROVIDERS} providers to their servers")
    servers: dict[str, Upstream] = {}
    for provider, server in value.items():
        if not isinstance(provider, str) or not _PROVIDER_RE.fullmatch(provider):
            raise EnrollRejected(f"{PROVIDER_SERVERS_FIELD} names a provider that is not one")
        if provider == CUSTOM_PROVIDER:
            raise EnrollRejected(f"the custom provider's server is {LOCAL_LLM_FIELD}, not {PROVIDER_SERVERS_FIELD}")
        servers[provider] = parse_upstream(server, f"{PROVIDER_SERVERS_FIELD}.{provider}")
    return servers


@dataclass(frozen=True)
class Servers:
    """What one enrollment names: the custom server, each other provider's, and the sandbox's limits at all of them."""

    local: Upstream | None = None
    providers: dict[str, Upstream] = field(default_factory=dict)
    limits: Limits = field(default_factory=Limits)


class _UpstreamEnroll:
    """Every enrollment names its sandbox's servers (``local_llm``, ``provider_servers``, or both) and its limits at
    every one of them (``limits``), and the answer confirms them. An enrollment again replaces all of it: a server it
    no longer names is gone."""

    def parse(self, app: web.Application, body: dict[str, Any]) -> Servers:
        if LOCAL_LLM_FIELD not in body and PROVIDER_SERVERS_FIELD not in body:
            raise EnrollRejected("an enrollment names the sandbox's model server (local_llm or provider_servers)")
        local = parse_upstream(body[LOCAL_LLM_FIELD]) if LOCAL_LLM_FIELD in body else None
        providers = parse_provider_servers(body[PROVIDER_SERVERS_FIELD]) if PROVIDER_SERVERS_FIELD in body else {}
        if local is None and not providers:
            raise EnrollRejected(f"{PROVIDER_SERVERS_FIELD} names no provider")
        if LIMITS_FIELD not in body:
            raise EnrollRejected(f"an enrollment names the sandbox's limits at its servers ({LIMITS_FIELD})")
        return Servers(local, providers, parse_limits(body[LIMITS_FIELD], max_request_bytes=app[MAX_REQUEST_BYTES]))

    def apply(self, app: web.Application, sandbox_id: str, parsed: Servers) -> dict[str, Any]:
        # An enrollment again ends the sandbox's earlier one first (``on_unenroll``, :func:`_forget`), so a server
        # this one does not name is already gone.
        app[LIMITS_BY_SANDBOX][sandbox_id] = parsed.limits
        answer: dict[str, Any] = {LIMITS_FIELD: parsed.limits.to_body()}
        if parsed.local is not None:
            app[UPSTREAM_BY_SANDBOX][sandbox_id] = parsed.local
            answer[LOCAL_LLM_FIELD] = _confirmed(parsed.local)
        if parsed.providers:
            app[SERVERS_BY_SANDBOX][sandbox_id] = dict(parsed.providers)
            answer[PROVIDER_SERVERS_FIELD] = {
                provider: _confirmed(server) for provider, server in parsed.providers.items()
            }
        return answer


def _confirmed(server: Upstream) -> dict[str, str]:
    """What the answer confirms of a server: which credential, its root and the address calls connect to; never its
    key."""
    return {"credential_id": server.credential_id, "upstream": server.root, "address": server.address}


def create_llm_proxy_app(
    admin_token: str,
    *,
    max_request_bytes: int = MAX_REQUEST_BYTES_CEILING,
    require_run: bool = False,
) -> web.Application:
    """The proxy app, forwarding each enrolled sandbox to the server it was enrolled with, under that enrollment's
    request cap and timeout. ``max_request_bytes`` is the ceiling: no enrollment may ask for more, and no body
    larger is read. With ``require_run`` a call from a sandbox with no declared run is refused."""
    if not 0 < max_request_bytes <= MAX_REQUEST_BYTES_CEILING:
        # A container's MAX_REQUEST_BYTES lowers the ceiling, never raises it: the settings' bounds are this one.
        raise ValueError(f"MAX_REQUEST_BYTES must be positive and at most {MAX_REQUEST_BYTES_CEILING}")
    app = web.Application(client_max_size=max_request_bytes)
    app[MAX_REQUEST_BYTES] = max_request_bytes
    app[UPSTREAM_BY_SANDBOX] = {}
    app[SERVERS_BY_SANDBOX] = {}
    app[LIMITS_BY_SANDBOX] = {}
    register_admin_routes(
        app, proxy_name=PROXY_NAME, admin_token=admin_token, on_unenroll=_forget, enroll_extension=_UpstreamEnroll()
    )
    register_meter_routes(app, proxy_name=PROXY_NAME, require_run=require_run)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_get("/health", _health_handler)
    app.router.add_route("*", "/v1/{path:.*}", _proxy_handler)
    app.router.add_route("*", "/p/{provider}/v1/{path:.*}", _provider_handler)
    return app


def _forget(app: web.Application, sandbox_id: str) -> None:
    """An enrollment ended: drop its server, and end its tallies (they stay for the orchestrator to read)."""
    app[UPSTREAM_BY_SANDBOX].pop(sandbox_id, None)
    app[SERVERS_BY_SANDBOX].pop(sandbox_id, None)
    app[LIMITS_BY_SANDBOX].pop(sandbox_id, None)
    app[METER].sandbox_gone(sandbox_id)


async def _on_startup(app: web.Application) -> None:
    # Every connection goes to the address the sandbox was enrolled with, and is pooled by the server's name, so
    # each TLS handshake checks its own name (pinned_client).
    app[HTTP_CLIENT] = pinning_client(timeout=_TIMEOUT)


async def _on_cleanup(app: web.Application) -> None:
    client = app.get(HTTP_CLIENT)
    if client:
        await client.aclose()


async def _health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "proxy": "llm"})


class _Scrubber:
    """Replaces every key the proxy holds in an answer on its way back, across the chunks it streams in. Bytes where a
    key could still begin (the last ``len(longest key) - 1``) wait for the next chunk, so a key split between chunks is
    found whole, and a key that is the start of a longer one is never taken out of the longer one."""

    def __init__(self, keys: list[str]) -> None:
        found = sorted({key.encode() for key in keys if len(key) >= _MIN_SCRUBBED_KEY}, key=len, reverse=True)
        # Longest first: where two keys match at one place, the longer is the one replaced.
        self._pattern = re.compile(b"|".join(re.escape(key) for key in found)) if found else None
        self._hold = max((len(key) for key in found), default=1) - 1
        self._pending = b""

    def feed(self, chunk: bytes) -> bytes:
        """What may go back now of everything fed so far."""
        if self._pattern is None:
            return chunk
        data = self._pending + chunk
        # A match from here on may be a shorter key standing for the start of a longer one not yet whole.
        settled = len(data) - self._hold
        out: list[bytes] = []
        sent = 0
        for match in self._pattern.finditer(data):
            if match.start() >= settled:
                break
            out += (data[sent : match.start()], REDACTED.encode())
            sent = match.end()
        cut = max(sent, settled)
        out.append(data[sent:cut])
        self._pending = data[cut:]
        return b"".join(out)

    def finish(self) -> bytes:
        """The rest, at the end of the answer."""
        rest, self._pending = self._pending, b""
        return self._pattern.sub(REDACTED.encode(), rest) if self._pattern is not None else rest


def _enrolled_keys(app: web.Application) -> list[str]:
    """Every key the proxy holds, whichever sandbox's enrollment it came with: a server two credentials share could
    name the other's. One per credential, so few."""
    keys = {server.api_key for server in app[UPSTREAM_BY_SANDBOX].values() if server.api_key}
    keys.update(server.api_key for servers in app[SERVERS_BY_SANDBOX].values() for server in servers.values())
    return sorted(key for key in keys if key)


def _upstream_headers(request: web.Request, server: Upstream) -> dict[str, str]:
    headers = {name: request.headers[name] for name in _FORWARDED_REQUEST_HEADERS if name in request.headers}
    # Uncompressed: what comes back is scrubbed as it is read.
    headers["Accept-Encoding"] = "identity"
    if server.api_key:
        headers["Authorization"] = f"Bearer {server.api_key}"
    return headers


async def _proxy_handler(request: web.Request) -> web.StreamResponse:
    """``/v1/...``: the custom server's path for the images that read its root from their env."""
    return await _serve(request, CUSTOM_PROVIDER, path_per_provider=False)


async def _provider_handler(request: web.Request) -> web.StreamResponse:
    """``/p/<provider>/v1/...``: the path of each chat-completions provider."""
    provider = request.match_info.get("provider", "")
    if not _PROVIDER_RE.fullmatch(provider):
        return web.json_response({"error": "not_found"}, status=404)
    return await _serve(request, provider, path_per_provider=True)


def _server_for(app: web.Application, sandbox_id: str, provider: str) -> Upstream | None:
    if provider == CUSTOM_PROVIDER:
        return app[UPSTREAM_BY_SANDBOX].get(sandbox_id)
    return app[SERVERS_BY_SANDBOX].get(sandbox_id, {}).get(provider)


async def _serve(request: web.Request, provider: str, *, path_per_provider: bool) -> web.StreamResponse:
    path = request.match_info.get("path", "").strip("/")
    method = _ROUTES.get(path)
    if method is None:
        return web.json_response({"error": "not_found"}, status=404)
    if request.method != method:
        return web.json_response({"error": "method_not_allowed"}, status=405)
    sandbox_id = check_bearer(request, PROXY_NAME)
    if sandbox_id is None:
        return web.json_response({"error": "unauthorized"}, status=401)
    # A provider's path, only for a run: none of its calls is left to no job.
    if path_per_provider and not request.app[METER].has_run(sandbox_id):
        logger.warning("llm_proxy_call_outside_a_run", sandbox_id=sandbox_id, provider=provider)
        return openai_error(
            "run_not_declared", f"this sandbox has no agent run in progress: {provider} is refused outside one", 403
        )
    # While a run is current, only its own provider's path: its tally then holds that provider's calls alone, which
    # the orchestrator prices at the run's rates.
    declared = request.app[METER].declared_provider(sandbox_id)
    if declared is not None and declared != provider:
        logger.warning("llm_proxy_provider_not_declared", sandbox_id=sandbox_id, provider=provider, declared=declared)
        return openai_error(
            "provider_not_declared", f"this sandbox's current run is on {declared}: {provider} is refused", 403
        )

    upstream = _server_for(request.app, sandbox_id, provider)
    if upstream is None:
        if path_per_provider:
            # Only the providers the sandbox's job resolved a credential for: anything else is refused.
            logger.warning("llm_proxy_provider_not_enrolled", sandbox_id=sandbox_id, provider=provider)
            return openai_error("provider_not_enrolled", f"this sandbox is not enrolled for {provider}", 403)
        logger.error("llm_proxy_no_server", sandbox_id=sandbox_id)
        return openai_error("no_server", "no model server is enrolled for this sandbox", 503)

    # Set with the server by the same enrollment. A server with no limits is a bug: refuse rather than forward
    # under defaults the sandbox was never given.
    limits = request.app[LIMITS_BY_SANDBOX].get(sandbox_id)
    if limits is None:
        logger.error("llm_proxy_no_limits", sandbox_id=sandbox_id, provider=provider)
        return openai_error("no_limits", "no limits are enrolled for this sandbox", 503)
    body = b""
    if method == "POST":
        read = await read_body(
            request,
            "llm-proxy",
            setting="local_models.max_request_bytes",
            shape="openai",
            limit=limits.max_request_bytes,
        )
        if isinstance(read, web.Response):
            return read
        body = read

    metered: Metered | None = None
    if method == "POST":
        call = read_call("openai_chat", body)
        if call.refused is not None:
            logger.warning("llm_proxy_request_refused", sandbox_id=sandbox_id, reason=call.refused)
            return openai_error("unreadable_request", call.refused, 400)
        body = call.body
        try:
            metered = request.app[METER].open(sandbox_id, call)
        except NoRunError:
            logger.warning("llm_proxy_call_without_run", sandbox_id=sandbox_id)
            return no_run_answer("openai_chat")
        except TalliesFullError:
            logger.error("llm_proxy_meter_full", sandbox_id=sandbox_id)
            return web.json_response({"error": "meter_full"}, status=503)

    client: httpx.AsyncClient = request.app[HTTP_CLIENT]
    # The URL keeps the server's name, for Host, TLS and the pool; the connection goes to the address the
    # orchestrator checked, never to the name again (pinned_client).
    url = f"{upstream.root}/{path}"
    logger.info(
        "llm_proxy_forward",
        sandbox_id=sandbox_id,
        provider=provider,
        credential_id=upstream.credential_id,
        path=path,
        bytes=len(body),
    )
    try:
        with pinned(url, upstream.address):
            return await _forward(
                request,
                client,
                method,
                url,
                body,
                sandbox_id,
                metered,
                upstream,
                limits,
                _Scrubber(_enrolled_keys(request.app)),
            )
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
    limits: Limits,
    scrubber: _Scrubber,
) -> web.StreamResponse:
    """Send the call upstream and stream its answer back through the meter, with every key the proxy holds scrubbed
    from it, an error answer and a streamed one alike (:class:`_Scrubber` finds a key however the chunks split it). A
    call whose answer never came back after it was sent, or broke off before its usage (the upstream cut it, it could
    not be decoded, the sandbox went away), is charged its ceiling; one that never connected costs nothing."""
    try:
        async with client.stream(
            method,
            url,
            headers=_upstream_headers(request, server),
            content=body or None,
            timeout=httpx.Timeout(limits.timeout_seconds, connect=_CONNECT_SECONDS),
        ) as upstream:
            unreadable = unreadable_encoding(upstream.headers.get("content-encoding", ""))
            if unreadable is not None:
                # Its bytes cannot be scrubbed, so they never go back. A completion was answered all the same: it is
                # charged its ceiling, as one whose usage cannot be read.
                if metered is not None:
                    if 200 <= upstream.status_code < 300:
                        metered.lost()
                    else:
                        metered.answer(upstream.status_code, upstream.headers.get("content-type"))
                logger.warning("llm_proxy_answer_encoding_refused", sandbox_id=sandbox_id, encoding=unreadable[:80])
                return openai_error(
                    "unreadable_answer", "the model server answered in an encoding the proxy does not read", 502
                )
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
                        metered.feed(chunk)  # what the provider sent: its usage is read from it as it was
                    out = scrubber.feed(chunk)
                    if out:
                        await response.write(out)
            except (httpx.TransportError, httpx.DecodingError) as exc:
                # Broken off: the upstream cut it, or what came could not be decoded. Charged its ceiling unless its
                # usage was read in full (``Metered.lost``).
                if metered is not None:
                    metered.lost()
                logger.warning("llm_proxy_stream_truncated", sandbox_id=sandbox_id, error=str(exc))
            rest = scrubber.finish()
            if rest:
                await response.write(rest)
            await response.write_eof()
            return response
    except httpx.ConnectError:
        return web.json_response({"error": "the model server is unreachable"}, status=502)
    except httpx.TimeoutException:
        if metered is not None:
            metered.lost()
        return web.json_response({"error": "the model server timed out"}, status=504)
    except BaseException:
        if metered is not None:
            metered.lost()
        raise
