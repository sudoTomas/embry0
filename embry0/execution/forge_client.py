"""Forge-neutral HTTP transport: retry, backoff, lifecycle, pagination (#164).

Extracted from ``GitHubClient`` (#158) when GitLab arrived. Everything that
should never vary between forges lives here; a subclass supplies only
``headers()`` and a base URL.

The alternative — a second client with its own copy of the retry loop — is
exactly the shape #158 existed to delete. Adding a forge should not add a
retry policy.
"""

from __future__ import annotations

import asyncio
import ssl
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

import httpx
import structlog

if TYPE_CHECKING:
    from embry0.services.credentials.resolver import CredentialResolver

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT = 15.0

# 429 is the documented rate-limit response on both forges; 5xx are transient
# by definition. 403 is NOT retried: it means "your token cannot do that" at
# least as often as it means rate limiting, and retrying that burns quota.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# Never sleep longer than this for one attempt, however far out the advertised
# reset is — a webhook handler must not block for an hour.
MAX_BACKOFF_SECONDS = 30.0


def _http_date_delay(raw: str) -> float | None:
    """Seconds until an RFC 9110 ``Retry-After`` date, or ``None`` if unparsable.

    The header has two forms and servers pick freely between them; reading only
    the delta-seconds one silently drops the other on the floor.
    """
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


def retry_after_seconds(response: httpx.Response) -> float | None:
    """Delay advertised by the response, if any.

    ``Retry-After`` wins (both forges send it on rate limits), in either the
    delta-seconds or the HTTP-date form.
    ``RateLimit-Reset`` / ``X-RateLimit-Reset`` is an absolute epoch second and
    is only meaningful when the remaining count is actually zero.
    """
    raw = response.headers.get("Retry-After")
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            dated = _http_date_delay(raw)
            if dated is not None:
                return dated

    remaining = response.headers.get("X-RateLimit-Remaining") or response.headers.get("RateLimit-Remaining")
    if remaining == "0":
        reset = response.headers.get("X-RateLimit-Reset") or response.headers.get("RateLimit-Reset")
        if reset:
            try:
                return max(0.0, float(reset) - time.time())
            except ValueError:
                pass
    return None


@dataclass(frozen=True)
class ForgeWrite:
    """What a forge answered to one write: its status, and its own words when it refused."""

    status_code: int
    detail: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


def forge_write(response: httpx.Response) -> ForgeWrite:
    """``response`` as a :class:`ForgeWrite`; ``detail`` is the forge's message, at most 1000 characters."""
    if 200 <= response.status_code < 300:
        return ForgeWrite(status_code=response.status_code)
    detail = ""
    try:
        body: Any = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        message = body.get("message") or body.get("error") or ""
        errors = body.get("errors")
        detail = f"{message} {errors}".strip() if errors else str(message)
    return ForgeWrite(status_code=response.status_code, detail=(detail or response.text)[:1000])


class ForgeClient:
    """Retrying HTTP client over one forge's REST API.

    ``http_client`` is optional. When supplied the caller owns its lifecycle
    (a long-lived pooled client); when omitted one is created and closed per
    request. That branch was written five times across the codebase before
    #158 and is now written once.

    Subclasses override :meth:`headers`. Responses come back raw: callers
    differ on what a non-2xx means — a missing PR is normal for a dedupe path
    and fatal for bootstrap — so status interpretation stays with them.
    """

    #: Human name used in log lines.
    forge = "forge"

    def __init__(
        self,
        *,
        default_token: str | None = "",
        http_client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        base_url: str = "",
        max_attempts: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        verify: ssl.SSLContext | bool | str = True,
        credential_resolver: CredentialResolver | None = None,
    ) -> None:
        self._default_token = default_token or ""
        self._http = http_client
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")
        self._max_attempts = max(1, max_attempts)
        self._sleep = sleep
        # Spec 1A §3.2: a self-managed forge behind a private CA needs a
        # context carrying that CA. Only used for the per-request client — a
        # caller-supplied ``http_client`` owns its own TLS configuration.
        self._verify = verify
        self._credential_resolver = credential_resolver

    # -- subclass hooks ---------------------------------------------------

    def headers(self, repo: str | None = None, **kwargs: Any) -> dict[str, str]:  # pragma: no cover - abstract
        raise NotImplementedError

    async def token_for(self, repo: str | None) -> str | None:
        """Registry token for ``repo`` (spec 2026-09-29 §3.1); None means "no resolver, use the env lookup"."""
        if self._credential_resolver is None:
            return None
        from embry0.services.credentials.resolver import CredentialMissingError

        try:
            return (await self._credential_resolver.resolve_forge(repo or "", self.forge)).secret
        except CredentialMissingError:
            return ""

    def url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self._base_url}/{path.lstrip('/')}"

    # -- request ----------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        repo: str | None = None,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        extra_headers: Mapping[str, str] | None = None,
        **header_kwargs: Any,
    ) -> httpx.Response:
        """Issue one request, retrying transient failures.

        Returns the response whatever its status. Raises only when every
        attempt failed at the transport layer, which retrying will not fix.
        """
        headers = self.headers(repo, token=await self.token_for(repo), **header_kwargs)
        if extra_headers:
            headers.update(extra_headers)

        client = self._http or httpx.AsyncClient(timeout=self._timeout, verify=self._verify)
        owned = self._http is None
        try:
            for attempt in range(1, self._max_attempts + 1):
                try:
                    response = await client.request(
                        method,
                        self.url(path),
                        headers=headers,
                        params=dict(params) if params else None,
                        json=json,
                        timeout=self._timeout,
                    )
                except httpx.TransportError:
                    if attempt == self._max_attempts:
                        raise
                    await self._backoff(attempt, None, method=method, path=path, reason="transport")
                    continue

                if response.status_code in RETRY_STATUSES and attempt < self._max_attempts:
                    await self._backoff(
                        attempt,
                        response,
                        method=method,
                        path=path,
                        reason=f"status_{response.status_code}",
                    )
                    continue
                return response

            raise RuntimeError("unreachable: retry loop exhausted without returning")
        finally:
            if owned:
                await client.aclose()

    async def _backoff(
        self,
        attempt: int,
        response: httpx.Response | None,
        *,
        method: str,
        path: str,
        reason: str,
    ) -> None:
        advertised = retry_after_seconds(response) if response is not None else None
        delay = advertised if advertised is not None else float(2 ** (attempt - 1))
        delay = min(delay, MAX_BACKOFF_SECONDS)
        logger.warning(
            "forge_request_retry",
            forge=self.forge,
            method=method,
            path=path,
            attempt=attempt,
            reason=reason,
            delay_seconds=round(delay, 2),
            advertised=advertised is not None,
        )
        await self._sleep(delay)

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PATCH", path, **kwargs)

    async def put(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PUT", path, **kwargs)

    # -- pagination -------------------------------------------------------

    async def paginate(
        self,
        path: str,
        *,
        repo: str | None = None,
        params: Mapping[str, Any] | None = None,
        per_page: int = 100,
        max_pages: int = 20,
        strict: bool = False,
    ) -> list[Any]:
        """Follow ``Link: rel="next"`` and return the concatenated items.

        Both forges emit RFC 5988 Link headers for collections. ``max_pages``
        is a runaway guard, not a coverage decision: hitting it logs rather
        than silently truncating, because a caller that believes it saw every
        page and did not is the worse failure.

        ``strict=False`` (default) returns whatever was read before a non-200.
        ``strict=True`` raises ``HTTPStatusError`` instead, so a caller that
        fans out across accounts can tell a dead token from an empty page.
        """
        merged = dict(params or {})
        merged.setdefault("per_page", per_page)

        items: list[Any] = []
        next_path: str | None = path
        pages = 0
        while next_path and pages < max_pages:
            response = await self.get(next_path, repo=repo, params=merged if pages == 0 else None)
            if response.status_code != 200:
                logger.warning(
                    "forge_paginate_non_200",
                    forge=self.forge,
                    path=next_path,
                    status=response.status_code,
                    pages_read=pages,
                )
                if strict:
                    response.raise_for_status()
                return items
            body = response.json()
            if not isinstance(body, list):
                single: list[Any] = [body]
                return single
            items.extend(body)
            next_path = response.links.get("next", {}).get("url")
            pages += 1

        if next_path:
            logger.warning(
                "forge_paginate_truncated",
                forge=self.forge,
                path=path,
                max_pages=max_pages,
                items=len(items),
            )
        return items
