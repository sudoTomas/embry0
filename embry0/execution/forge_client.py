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

from embry0.safety.error_codes import ErrorCode

if TYPE_CHECKING:
    from embry0.services.credentials.resolver import CredentialResolver

logger = structlog.get_logger(__name__)

DEFAULT_TIMEOUT = 15.0

# 429 is the documented rate-limit response on both forges; 5xx are transient
# by definition. 403 is NOT retried: it means "your token cannot do that" at
# least as often as it means rate limiting, and retrying that burns quota.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# #45: what proves the forge did not act on a request, so that sending it again
# cannot do anything twice, whatever its method. A 429 is a refusal for its
# rate. A connect error, a connect timeout or a pool timeout: the request was
# never sent. Every other transient failure (a 5xx, a read timeout, an answer
# lost in transit) leaves it unknown whether the forge acted.
NOT_PROCESSED_STATUSES = frozenset({429})
NOT_SENT_ERRORS: tuple[type[httpx.TransportError], ...] = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)

# Methods whose effect is the same however often they are sent (RFC 9110
# §9.2.2): a request of one of them is sent again on any transient failure.
# POST and PATCH are not among them: a create sent twice makes two.
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})

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


def rate_limited(response: httpx.Response) -> bool:
    """Does this response carry a rate-limit signal despite its status?

    GitHub answers a secondary rate limit with ``403`` plus ``Retry-After``
    and/or ``x-ratelimit-remaining: 0``, and a primary one with ``403`` and
    ``x-ratelimit-remaining: 0`` — a status that otherwise means "your token
    cannot do that". Reading the headers is what tells the two apart.
    """
    advertised = retry_after_seconds(response)
    if advertised is not None and advertised > 0:
        return True
    remaining: str | None = response.headers.get("X-RateLimit-Remaining") or response.headers.get("RateLimit-Remaining")
    return remaining == "0"


@dataclass(frozen=True)
class ForgeWrite:
    """What a forge answered to one write: its status, and its own words when it refused."""

    status_code: int
    detail: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


@dataclass(frozen=True)
class Listing:
    """What :meth:`ForgeClient.paginate_listing` read: the items, and whether
    they are the whole collection."""

    items: list[Any]
    complete: bool


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


class ChosenTokenRefused(ValueError):
    """A client built with a token chosen for one owner was asked to call for another owner's repository."""

    #: The token cannot be used for that repository, and another owner's must not be: a credential that cannot be used.
    error_code = ErrorCode.CREDENTIAL_UNAVAILABLE

    def __init__(self, repo: str, owner: str | None) -> None:
        whose = f"chosen for {owner}" if owner else "chosen for no owner"
        super().__init__(f"a forge client built with a token {whose} does not call for {repo}")
        self.repo = repo
        self.owner = owner


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
        before_wait: Callable[[httpx.Response | None, float | None, float], Awaitable[None]] | None = None,
        verify: ssl.SSLContext | bool | str = True,
        credential_resolver: CredentialResolver | None = None,
        owner_env_tokens: bool = True,
        token_owner: str | None = None,
    ) -> None:
        """``owner_env_tokens`` False is for a client built with a token its caller chose (a credential to probe, the
        account an organization's discovery or actions use, one listing's own token): that token is sent on every
        call, and a call naming a repository never reads its owner's environment token (``GITHUB_TOKEN__<OWNER>``,
        ``GITLAB_TOKEN__<GROUP>``) in its place. Such a variable is not one owner's alone: ``my-org`` and
        ``my_org`` read the same one.

        ``token_owner`` is the owner such a token was chosen for (an organization's): a call naming a repository
        outside it is refused (:class:`ChosenTokenRefused`), since the token is that owner's to use, and with no
        ``token_owner`` (a credential being verified, a listing's own token) every call naming one is.

        ``before_wait`` is awaited before each wait for a retry with the response being retried (None after a
        transport error), the wait the forge advertised (``Retry-After``, a rate-limit reset; uncapped, None when
        it named none) and the wait about to be slept; one that raises ends the request with its exception. A
        caller with a deadline of its own uses it to stop rather than wait (the organization probe, #571)."""
        self._default_token = default_token or ""
        self._owner_env_tokens = owner_env_tokens
        self._token_owner = token_owner.strip("/").lower() if token_owner else None
        self._http = http_client
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")
        self._max_attempts = max(1, max_attempts)
        self._sleep = sleep
        self._before_wait = before_wait
        # Spec 1A §3.2: a self-managed forge behind a private CA needs a
        # context carrying that CA. Only used for the per-request client — a
        # caller-supplied ``http_client`` owns its own TLS configuration.
        self._verify = verify
        self._credential_resolver = credential_resolver

    def _check_chosen_token(self, repo: str | None) -> None:
        """Refuse a call naming a repository outside the owner a chosen token is for (``__init__``)."""
        if self._owner_env_tokens or not repo:
            return
        if self._token_owner is None or not repo.lower().startswith(self._token_owner + "/"):
            raise ChosenTokenRefused(repo, self._token_owner)

    def _check_chosen_path(self, path: str) -> None:
        """Refuse a call whose path names an owner or repository outside the one a chosen token is for, as a call
        naming it with ``repo=`` is refused: ``/repos/other/x`` on GitHub, ``/projects/other%2Fx`` on GitLab."""
        if self._owner_env_tokens:
            return
        named = self.path_names(path)
        if named is None:
            return
        named = named.lower()
        if self._token_owner is None or (named != self._token_owner and not named.startswith(self._token_owner + "/")):
            raise ChosenTokenRefused(named, self._token_owner)

    def path_names(self, path: str) -> str | None:
        """The owner, or the owner and repository, a request path names (``/repos/acme/app`` names ``acme``); None
        when it names none or one it cannot tell (a numeric id). Each forge's client reads its own paths."""
        return None

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
        retry_transient: bool | None = None,
        **header_kwargs: Any,
    ) -> httpx.Response:
        """Issue one request, retrying the failures it is safe to retry (#45).

        Returns the response whatever its status. Raises the transport error
        of the last attempt when no attempt was answered.

        Any request is sent again on a failure that proves the forge did not
        act on it: a 429, or a request that never reached it (a connect error,
        a connect or pool timeout). On any other transient failure (a 5xx, a
        read timeout, an answer lost in transit) the forge may have acted, so
        only a request that is safe to repeat is sent again:

        - ``retry_transient=None`` (the default): one of an idempotent method
          (GET, HEAD, OPTIONS, PUT, DELETE). A POST or a PATCH is not: a write
          the forge accepted and answered 502 must not be posted twice;
        - ``retry_transient=True``: the caller says it is safe to repeat (a
          GraphQL query, a label added, a field set);
        - ``retry_transient=False``: none, whatever its method.
        """
        self._check_chosen_path(path)
        headers = self.headers(repo, token=await self.token_for(repo), **header_kwargs)
        if extra_headers:
            headers.update(extra_headers)

        client = self._http or httpx.AsyncClient(timeout=self._timeout, verify=self._verify)
        owned = self._http is None
        try:
            repeatable = method.upper() in IDEMPOTENT_METHODS if retry_transient is None else retry_transient
            attempts = self._max_attempts
            for attempt in range(1, attempts + 1):
                try:
                    response = await client.request(
                        method,
                        self.url(path),
                        headers=headers,
                        params=dict(params) if params else None,
                        json=json,
                        timeout=self._timeout,
                    )
                except httpx.TransportError as exc:
                    if attempt == attempts or not (repeatable or isinstance(exc, NOT_SENT_ERRORS)):
                        raise
                    await self._backoff(attempt, None, method=method, path=path, reason="transport")
                    continue

                retry = response.status_code in NOT_PROCESSED_STATUSES or (
                    repeatable and response.status_code in RETRY_STATUSES
                )
                if retry and attempt < attempts:
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
        if self._before_wait is not None:
            await self._before_wait(response, advertised, delay)
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
        listing = await self.paginate_listing(
            path, repo=repo, params=params, per_page=per_page, max_pages=max_pages, strict=strict
        )
        return listing.items

    async def paginate_listing(
        self,
        path: str,
        *,
        repo: str | None = None,
        params: Mapping[str, Any] | None = None,
        per_page: int = 100,
        max_pages: int = 20,
        strict: bool = False,
    ) -> Listing:
        """:meth:`paginate`, saying whether it read the whole collection.

        ``complete`` is False when ``max_pages`` stopped it with a next page
        unread, or when a non-200 ended it under ``strict=False``. A caller
        that reads what is absent from a listing as gone needs to know.
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
                return Listing(items, complete=False)
            body = response.json()
            if not isinstance(body, list):
                return Listing([body], complete=True)
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
        return Listing(items, complete=not next_path)
