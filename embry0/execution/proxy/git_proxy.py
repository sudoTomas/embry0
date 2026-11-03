"""Git smart-HTTP proxy: the sandbox's git remote for the job's repository (#521, ADR 0007).

The sandbox's git config rewrites the job repository's forge URL to
``<proxy>/git/<repo>.git`` and sends its per-sandbox bearer to the proxy in an
``http.extraHeader``. The proxy checks the bearer and the enrollment's grant
(:mod:`embry0.execution.proxy.git_scope`), then forwards ``info/refs``,
``git-upload-pack`` and ``git-receive-pack`` to the forge with the forge
credential in ``Authorization``. Both bodies are streamed: a pack can be
gigabytes, and nothing here holds one in memory.

A push's ref updates are decided before its pack (#559, ADR 0009): the proxy
reads the command list at the head of the receive-pack body
(:mod:`embry0.execution.proxy.receive_pack`), refuses the whole push when any
update touches a ref outside the enrollment's ``git_push_branches`` or deletes
a branch, and otherwise re-sends the list and streams the rest unchanged.

The forge credential never leaves this process. ``/git-credentials``, which
used to return it to the sandbox's credential helper, now answers 410.

Bearers are enrolled by the orchestrator at sandbox-create time via
/admin/enroll (shared hardened registry: TTL, cap, admin backoff, in
:mod:`embry0.execution.proxy.enrollment`, RAV-1070).
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import aiohttp
import structlog
from aiohttp import web

from embry0.execution.proxy.app_keys import CA_PEM, GITHUB_TOKEN, HTTP_SESSION
from embry0.execution.proxy.enrollment import check_bearer, git_identity, payload_token, register_admin_routes
from embry0.execution.proxy.git_scope import (
    GIT_GRANT_BY_ID,
    GIT_ROUTE_PREFIX,
    RECEIVE_PACK,
    GitGrant,
    GitScope,
    GitVerdict,
    git_verdict,
)
from embry0.execution.proxy.receive_pack import (
    CommandList,
    MalformedCommandList,
    read_command_list,
    refusal_response,
    refused_updates,
)
from embry0.execution.tls import build_ssl_context

logger = structlog.get_logger(__name__)


# Identity when a sandbox enrolled without one: every pre-#166 sandbox meant
# GitHub. The host also binds the credential: it is sent only to an upstream
# on this host, as git's credential helper protocol did.
DEFAULT_GIT_HOST = "github.com"
DEFAULT_GIT_USERNAME = "x-access-token"

# Only what the git smart-HTTP protocol needs crosses the proxy. The sandbox
# bearer, cookies, spoofed X-Forwarded-* and everything else are dropped.
# Authorization is proxy-set, never forwarded.
# Content-Encoding and Content-Length are not forwarded either: aiohttp's server
# decompresses a request body before the handler reads it, so they would
# describe the gzip bytes git sent while the proxy streams the decompressed
# ones. The forge then rejects the request (HTTP 400, git: "expected
# 'acknowledgments'"); the body goes upstream chunked and identity-encoded.
_FORWARDED_REQUEST_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "content-type",
        "git-protocol",
        "user-agent",
    }
)
# Set-Cookie, WWW-Authenticate and Location never reach the sandbox.
_FORWARDED_RESPONSE_HEADERS = ("Content-Type", "Content-Encoding", "Cache-Control", "Expires", "Pragma")

_CHUNK_BYTES = 64 * 1024
# No total timeout: a large clone legitimately takes minutes. A stalled read does not.
_UPSTREAM_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=300)

_CREDENTIALS_GONE = (
    "This git-proxy no longer hands out forge credentials. The sandbox's git uses the proxy as its "
    "remote instead; this request comes from a sandbox configured by an orchestrator older than the "
    "proxy image. Deploy the orchestrator together with the proxy image.\n"
)


def create_git_proxy_app(github_token: str, admin_token: str, *, ca_pem: str = "") -> web.Application:
    """Create the git smart-HTTP proxy app.

    Args:
        github_token: credential used when a sandbox enrolled without its own
            payload token (pre-#166, GitHub-only enrollments).
        admin_token: shared secret with the orchestrator for /admin/enroll.
        ca_pem: private CA to trust in addition to the system store, for a
            self-managed forge behind an internal CA (spec 1A §3.2).
    """
    app = web.Application()
    app[GITHUB_TOKEN] = github_token
    app[CA_PEM] = ca_pem
    scope = GitScope(app, proxy_name="git_proxy")
    register_admin_routes(
        app,
        proxy_name="git_proxy",
        admin_token=admin_token,
        accepts_payload_token=True,
        on_unenroll=scope.forget,
        enroll_extension=scope,
    )
    scope.register_routes(app)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_get("/health", _health_handler)
    app.router.add_get("/git-credentials", _credentials_gone_handler)
    app.router.add_route("*", GIT_ROUTE_PREFIX + "{path:.*}", _git_handler)
    return app


async def _on_startup(app: web.Application) -> None:
    ca_pem = app.get(CA_PEM, "")
    connector = aiohttp.TCPConnector(ssl=build_ssl_context(ca_pem)) if ca_pem else aiohttp.TCPConnector()
    # One session serves every sandbox, so it must not keep cookies: a forge's
    # session cookie set for one sandbox would ride along on another's request.
    # auto_decompress=False passes a compressed body through as it came.
    app[HTTP_SESSION] = aiohttp.ClientSession(
        connector=connector,
        cookie_jar=aiohttp.DummyCookieJar(),
        auto_decompress=False,
        timeout=_UPSTREAM_TIMEOUT,
    )
    if ca_pem:
        logger.info("git_proxy_private_ca_trusted")


async def _on_cleanup(app: web.Application) -> None:
    session = app.get(HTTP_SESSION)
    if session is not None:
        await session.close()


async def _health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "proxy": "git"})


async def _credentials_gone_handler(request: web.Request) -> web.Response:
    """Refuse the retired credential-helper endpoint, loudly, without a credential."""
    sandbox_id = check_bearer(request, "git_proxy")
    if sandbox_id is None:
        return web.json_response({"error": "unauthorized"}, status=401)
    logger.warning(
        "git_proxy_credentials_refused",
        sandbox_id=sandbox_id,
        detail="a credential helper asked for the forge token; the orchestrator predates the proxy image",
    )
    return web.Response(status=410, text=_CREDENTIALS_GONE, content_type="text/plain")


def _refusal(status: int, message: str) -> web.Response:
    # text/plain: git prints the body of a failed info/refs as "remote: ..."
    return web.Response(status=status, text=f"embry0 git-proxy: {message}\n", content_type="text/plain")


def _basic(username: str, token: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{token}".encode()).decode("ascii")


def _upstream_headers(request: web.Request, sandbox_id: str, verdict: GitVerdict) -> dict[str, str]:
    headers = {k: v for k, v in request.headers.items() if k.lower() in _FORWARDED_REQUEST_HEADERS}
    # aiohttp would otherwise ask for encodings git never offered.
    headers.setdefault("Accept-Encoding", "identity")
    token = payload_token(request, sandbox_id, request.app[GITHUB_TOKEN])
    if not token:
        return headers
    host, username = git_identity(request, sandbox_id) or (DEFAULT_GIT_HOST, DEFAULT_GIT_USERNAME)
    upstream_host = urlsplit(verdict.upstream_url).hostname or ""
    if upstream_host != host.lower():
        logger.warning(
            "git_proxy_credential_host_mismatch",
            sandbox_id=sandbox_id,
            credential_host=host,
            upstream_host=upstream_host,
            detail="the credential is for another host; the request is forwarded without it",
        )
        return headers
    headers["Authorization"] = _basic(username, token)
    return headers


async def _git_handler(request: web.Request) -> web.StreamResponse:
    """Forward one git smart-HTTP request for the sandbox's repository."""
    sandbox_id = check_bearer(request, "git_proxy")
    if sandbox_id is None:
        # 403, not 401: a 401 makes git fall back to prompting for a username,
        # which hides this message behind "could not read Username".
        return _refusal(403, "this sandbox's proxy bearer is not enrolled (expired or revoked)")

    grant: GitGrant | None = request.app[GIT_GRANT_BY_ID].get(sandbox_id)
    verdict = git_verdict(request.method, request.rel_url.raw_path, request.rel_url.raw_query_string, grant)
    if not verdict.allowed:
        logger.warning(
            "git_proxy_policy_denied",
            sandbox_id=sandbox_id,
            method=request.method[:16],
            path=request.rel_url.raw_path[:256],
            reason=verdict.reason,
        )
        return _refusal(403, f"refused ({verdict.reason}): this proxy serves the job's own repository only")

    body: object = request.content if request.method == "POST" else None
    if request.method == "POST" and verdict.service == RECEIVE_PACK and grant is not None:
        commands_or_refusal = await _decide_push(request, sandbox_id, grant, verdict)
        if isinstance(commands_or_refusal, web.StreamResponse):
            return commands_or_refusal
        body = _replay(commands_or_refusal.raw, request)

    headers = _upstream_headers(request, sandbox_id, verdict)
    session: aiohttp.ClientSession = request.app[HTTP_SESSION]
    response: web.StreamResponse | None = None
    try:
        async with session.request(
            request.method,
            verdict.upstream_url,
            headers=headers,
            data=body,
            allow_redirects=False,
        ) as upstream:
            if 300 <= upstream.status < 400:
                # Following it would take the credential wherever the forge
                # points (a renamed repository is another path, out of scope).
                logger.warning(
                    "git_proxy_upstream_redirect",
                    sandbox_id=sandbox_id,
                    repo=verdict.repo,
                    status=upstream.status,
                )
                return _refusal(502, f"the forge redirected {verdict.repo} (renamed?); redirects are not followed")
            if upstream.status == 401:
                logger.warning("git_proxy_upstream_unauthorized", sandbox_id=sandbox_id, repo=verdict.repo)
                return _refusal(403, f"the forge refused the configured credential for {verdict.repo}")
            response = web.StreamResponse(status=upstream.status)
            for name in _FORWARDED_RESPONSE_HEADERS:
                if name in upstream.headers:
                    response.headers[name] = upstream.headers[name]
            if upstream.content_length is not None:
                response.content_length = upstream.content_length
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(_CHUNK_BYTES):
                await response.write(chunk)
            await response.write_eof()
    except (TimeoutError, aiohttp.ClientError) as exc:
        logger.warning(
            "git_proxy_upstream_failed",
            sandbox_id=sandbox_id,
            repo=verdict.repo,
            service=verdict.service,
            error=type(exc).__name__,
            streaming=response is not None,
        )
        if response is not None:
            # Headers are out: a clean end would hand git a truncated pack.
            raise
        status = 504 if isinstance(exc, TimeoutError) else 502
        return _refusal(status, f"could not reach the forge for {verdict.repo}")
    logger.info(
        "git_proxy_forwarded",
        sandbox_id=sandbox_id,
        repo=verdict.repo,
        service=verdict.service,
        method=request.method,
        status=response.status,
    )
    return response


async def _decide_push(
    request: web.Request, sandbox_id: str, grant: GitGrant, verdict: GitVerdict
) -> CommandList | web.StreamResponse:
    """Read the push's command list; return it when every update is allowed, else the refusal to send."""
    try:
        commands = await read_command_list(request.content)
    except MalformedCommandList as exc:
        logger.warning(
            "git_proxy_push_refused",
            sandbox_id=sandbox_id,
            job_id=grant.job_id,
            repo=verdict.repo,
            reason="malformed_command_list",
            detail=str(exc)[:300],
        )
        await _drain(request)
        return _refusal(403, f"push refused: {exc}")
    refused = refused_updates(commands.updates, grant.push_branches)
    if not refused:
        return commands
    allowed = ", ".join(sorted(grant.push_branches)) or "none (the enrollment names no branch)"
    logger.warning(
        "git_proxy_push_refused",
        sandbox_id=sandbox_id,
        job_id=grant.job_id,
        repo=verdict.repo,
        reason="ref_out_of_scope",
        refused=dict(sorted(refused.items())[:20]),
        updates=len(commands.updates),
        allowed_branches=sorted(grant.push_branches),
    )
    await _drain(request)
    message = f"push refused: this job may push only to {allowed}, and may not delete it"
    report = refusal_response(commands.updates, refused, commands.capabilities, message)
    if report is None:
        return _refusal(403, message)
    return web.Response(status=200, body=report, content_type="application/x-git-receive-pack-result")


async def _drain(request: web.Request) -> None:
    """Read and drop the rest of a refused push, so git reads the answer instead of a broken pipe."""
    while await request.content.readany():
        pass


async def _replay(head: bytes, request: web.Request) -> AsyncIterator[bytes]:
    """The command list as it came, then the rest of the body (push options, pack), streamed."""
    yield head
    async for chunk in request.content.iter_chunked(_CHUNK_BYTES):
        yield chunk
