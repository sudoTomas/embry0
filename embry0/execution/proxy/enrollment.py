"""Shared per-sandbox bearer enrollment for the credential proxies (RAV-1070).

Every credential proxy (git, github, auth, xai, context) used to carry its own
copy of the three enrollment dicts plus enroll/unenroll handlers. This module
centralizes that state and hardens it:

- **TTL**: every enrollment expires. The orchestrator passes ``ttl_seconds``
  bound to the job's lifetime; entries left behind by a missed unenroll die on
  their own instead of living until the next proxy restart.
- **Registry cap**: enrollment is bounded so a compromised admin credential
  (or an orchestrator bug) cannot grow proxy memory without limit. Expired
  entries are purged before the cap is enforced.
- **Admin-auth backoff**: repeated failed ``X-Admin-Token`` presentations
  lock the admin endpoints for a cooldown window, throttling brute-force
  attempts from inside the sandbox network.
- **One lifecycle** (#536): a proxy's per-sandbox state beside the registry
  (a scope, a quota, a credential slot) is cleared by its ``on_unenroll``
  hook, which the registry runs whenever an enrollment ends: revoked,
  expired, or replaced. That state therefore never outlives its enrollment
  and is bounded by the same cap.

State stays in-memory by design — ProxyManager.start() re-enrolls after any
proxy restart (Task 9 contract), and a restart wiping bearers fails closed.
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable
from typing import Any, Protocol

import structlog
from aiohttp import web

__all__ = [
    "AdminGuard",
    "EnrollExtension",
    "EnrollRejected",
    "EnrollmentRegistry",
    "RegistryFullError",
    "check_bearer",
    "guard_admin",
    "git_identity",
    "payload_token",
    "register_admin_routes",
    "sha256_hex",
]

logger = structlog.get_logger(__name__)

_SANDBOX_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# Backstop lifetime when the orchestrator doesn't pass an explicit TTL.
# Generously above any legitimate job (longest pipeline runs are hours);
# the orchestrator normally passes a tighter, job-lifetime-bound value.
DEFAULT_TTL_SECONDS = 86_400
# Never accept an enrollment that outlives a week regardless of caller input.
MAX_TTL_SECONDS = 7 * 86_400
# Concurrent sandboxes are single-digit in practice; 500 is far above any
# legitimate fleet while still bounding memory.
DEFAULT_MAX_ENTRIES = 500

_ADMIN_MAX_FAILURES = 10
_ADMIN_FAILURE_WINDOW_SECONDS = 60.0
_ADMIN_LOCKOUT_SECONDS = 60.0


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class RegistryFullError(Exception):
    """Raised when the enrollment registry is at capacity."""


class EnrollmentRegistry:
    """Bearer-token registry: sandbox_id ↔ sha256(token), with TTL + cap.

    One bearer belongs to one enrollment, and one sandbox id holds at most one.

    ``on_unenroll(sandbox_id)`` runs once for every enrollment that ends, in
    the same call that drops it and after the registry has dropped it: revoked
    by :meth:`unenroll`; expired, when :meth:`purge_expired` or :meth:`lookup`
    finds it; or replaced, when :meth:`enroll` is given the same sandbox id or
    the same bearer again. A proxy clears its per-sandbox state there, so that
    state lives and dies with the enrollment.
    """

    def __init__(
        self,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        default_ttl_seconds: float = DEFAULT_TTL_SECONDS,
        now: Callable[[], float] = time.time,
        on_unenroll: Callable[[str], None] | None = None,
    ) -> None:
        self._max_entries = max_entries
        self._default_ttl = default_ttl_seconds
        self._now = now
        self._on_unenroll = on_unenroll
        self._hash_by_id: dict[str, str] = {}
        self._id_by_hash: dict[str, str] = {}
        self._payload_token_by_id: dict[str, str] = {}
        # Per-sandbox git identity (#166). A sandbox works on exactly one
        # repo, so the forge it should authenticate to is known at enrollment
        # time — which lets ONE git-proxy serve both forges instead of running
        # a second container per host.
        self._git_identity_by_id: dict[str, tuple[str, str]] = {}
        self._expires_at_by_id: dict[str, float] = {}

    def __len__(self) -> int:
        return len(self._hash_by_id)

    def __contains__(self, sandbox_id: object) -> bool:
        """Whether *sandbox_id* holds an enrollment (expired or not: lookup() decides that)."""
        return sandbox_id in self._hash_by_id

    def purge_expired(self) -> None:
        now = self._now()
        for sandbox_id in [sid for sid, exp in self._expires_at_by_id.items() if exp <= now]:
            self._end(sandbox_id)
            logger.info("proxy_enrollment_expired", sandbox_id=sandbox_id)

    def enroll(
        self,
        sandbox_id: str,
        sandbox_token: str,
        *,
        payload_token: str | None = None,
        git_host: str | None = None,
        git_username: str | None = None,
        ttl_seconds: float | None = None,
    ) -> None:
        """Register (or refresh) *sandbox_id* with *sandbox_token*.

        Raises RegistryFullError when at capacity after purging expired rows —
        the caller maps this to 429 so the orchestrator fails sandbox creation
        closed rather than silently running an unenrolled sandbox.
        """
        self.purge_expired()
        if sandbox_id not in self._hash_by_id and len(self._hash_by_id) >= self._max_entries:
            raise RegistryFullError(f"enrollment registry at capacity ({self._max_entries})")
        ttl = min(float(ttl_seconds or self._default_ttl), float(MAX_TTL_SECONDS))
        new_hash = sha256_hex(sandbox_token)
        # The new enrollment replaces whatever this id or this bearer held, so
        # no scope, quota or credential carries over into it.
        self._end(sandbox_id)
        holder = self._id_by_hash.get(new_hash)
        if holder is not None:
            self._end(holder)
            logger.warning("proxy_enrollment_bearer_reassigned", sandbox_id=holder, new_sandbox_id=sandbox_id)
        self._hash_by_id[sandbox_id] = new_hash
        self._id_by_hash[new_hash] = sandbox_id
        if payload_token is not None:
            self._payload_token_by_id[sandbox_id] = payload_token
        else:
            self._payload_token_by_id.pop(sandbox_id, None)
        if git_host:
            self._git_identity_by_id[sandbox_id] = (git_host, git_username or "oauth2")
        else:
            self._git_identity_by_id.pop(sandbox_id, None)
        self._expires_at_by_id[sandbox_id] = self._now() + ttl

    def unenroll(self, sandbox_id: str) -> bool:
        """Revoke *sandbox_id*'s enrollment; False when it held none."""
        return self._end(sandbox_id)

    def _end(self, sandbox_id: str) -> bool:
        """Drop one enrollment, then run ``on_unenroll`` for it. False when there was none."""
        old_hash = self._hash_by_id.pop(sandbox_id, None)
        if old_hash is not None:
            self._id_by_hash.pop(old_hash, None)
        self._payload_token_by_id.pop(sandbox_id, None)
        self._git_identity_by_id.pop(sandbox_id, None)
        self._expires_at_by_id.pop(sandbox_id, None)
        if old_hash is None:
            return False
        if self._on_unenroll is not None:
            self._on_unenroll(sandbox_id)
        return True

    def lookup(self, presented_token: str) -> str | None:
        """Resolve a presented bearer to its sandbox_id; expired entries fail (and drop)."""
        sandbox_id = self._id_by_hash.get(sha256_hex(presented_token))
        if sandbox_id is None:
            return None
        if self._expires_at_by_id.get(sandbox_id, 0.0) <= self._now():
            self._end(sandbox_id)
            logger.info("proxy_enrollment_expired", sandbox_id=sandbox_id)
            return None
        return sandbox_id

    def payload_token_for(self, sandbox_id: str) -> str | None:
        """Per-sandbox payload credential (e.g. per-owner GitHub PAT), if enrolled with one."""
        return self._payload_token_by_id.get(sandbox_id)

    def git_identity_for(self, sandbox_id: str) -> tuple[str, str] | None:
        """``(host, username)`` this sandbox should present to git, if enrolled."""
        return self._git_identity_by_id.get(sandbox_id)


class AdminGuard:
    """X-Admin-Token check with failed-attempt backoff.

    After ``_ADMIN_MAX_FAILURES`` bad presentations inside the sliding window,
    admin endpoints answer 429 for ``_ADMIN_LOCKOUT_SECONDS`` — bounding
    brute-force throughput from anything that can reach the admin port.
    """

    def __init__(self, admin_token: str, *, now: Callable[[], float] = time.time) -> None:
        if not admin_token:
            raise ValueError("admin_token is required (PROXY_ADMIN_TOKEN must be set)")
        self._token_hash = sha256_hex(admin_token)
        self._now = now
        self._failures: list[float] = []
        self._locked_until = 0.0

    def check(self, presented: str) -> str:
        """Return ``"ok"``, ``"unauthorized"``, or ``"locked"``."""
        now = self._now()
        if now < self._locked_until:
            return "locked"
        if presented and hmac.compare_digest(sha256_hex(presented), self._token_hash):
            self._failures.clear()
            return "ok"
        self._failures = [t for t in self._failures if t > now - _ADMIN_FAILURE_WINDOW_SECONDS]
        self._failures.append(now)
        if len(self._failures) >= _ADMIN_MAX_FAILURES:
            self._locked_until = now + _ADMIN_LOCKOUT_SECONDS
            self._failures.clear()
            return "locked"
        return "unauthorized"


class EnrollRejected(ValueError):
    """An enroll body field a proxy-specific extension refuses (HTTP 400)."""


class EnrollExtension(Protocol):
    """Proxy-specific handling of extra ``/admin/enroll`` body fields."""

    def parse(self, app: web.Application, body: dict[str, Any]) -> Any:
        """Validate the extra fields. Raise :class:`EnrollRejected` to refuse."""

    def apply(self, app: web.Application, sandbox_id: str, parsed: Any) -> dict[str, Any]:
        """Store ``parsed`` for the now-enrolled sandbox; return response fields."""


def guard_admin(request: web.Request, proxy_name: str) -> web.Response | None:
    """Shared admin gate for aiohttp handlers; None means authorized."""
    guard: AdminGuard = request.app["admin_guard"]
    verdict = guard.check(request.headers.get("X-Admin-Token", ""))
    if verdict == "ok":
        return None
    logger.warning(f"{proxy_name}_unauthorized", path=request.path, admin=verdict)
    if verdict == "locked":
        return web.json_response(
            {"error": "too_many_failures"},
            status=429,
            headers={"Retry-After": str(int(_ADMIN_LOCKOUT_SECONDS))},
        )
    return web.json_response({"error": "unauthorized"}, status=401)


def register_admin_routes(
    app: web.Application,
    *,
    proxy_name: str,
    admin_token: str,
    accepts_payload_token: bool = False,
    on_unenroll: Callable[[web.Application, str], None] | None = None,
    enroll_extension: EnrollExtension | None = None,
) -> None:
    """Attach the shared /admin/enroll + /admin/enroll/{sandbox_id} handlers.

    Stores an :class:`EnrollmentRegistry` at ``app["enrollment"]`` and an
    :class:`AdminGuard` at ``app["admin_guard"]``. ``accepts_payload_token``
    lets the enroll body carry a per-sandbox ``github_token`` (git/github
    proxies). ``on_unenroll`` clears the proxy's per-sandbox state: the
    registry runs it whenever an enrollment ends, whether revoked here,
    expired, or replaced by a new enrollment of the same sandbox id or bearer.
    ``enroll_extension`` lets one proxy read extra enroll-body fields (a
    container, repository or credential scope): ``parse`` runs BEFORE the
    sandbox is enrolled and refuses the whole enrollment by raising
    :class:`EnrollRejected`; ``apply`` runs after it, on an enrollment whose
    predecessor's state ``on_unenroll`` has already cleared, and returns fields
    to add to the response.
    """
    app["enrollment"] = EnrollmentRegistry(
        on_unenroll=None if on_unenroll is None else functools.partial(on_unenroll, app)
    )
    app["admin_guard"] = AdminGuard(admin_token)

    async def _enroll_handler(request: web.Request) -> web.Response:
        denied = guard_admin(request, proxy_name)
        if denied is not None:
            return denied
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"error": "bad_request"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "bad_request"}, status=400)
        sandbox_id = body.get("sandbox_id", "")
        sandbox_token = body.get("sandbox_token", "")
        if not sandbox_id or not sandbox_token or len(sandbox_token) < 32:
            return web.json_response({"error": "bad_request"}, status=400)
        if not _SANDBOX_ID_RE.fullmatch(sandbox_id):
            return web.json_response({"error": "bad_request"}, status=400)
        ttl_raw = body.get("ttl_seconds")
        ttl_seconds: float | None = None
        if ttl_raw is not None:
            if not isinstance(ttl_raw, (int, float)) or isinstance(ttl_raw, bool) or ttl_raw <= 0:
                return web.json_response({"error": "bad_request"}, status=400)
            ttl_seconds = float(ttl_raw)
        payload_token = body.get("github_token") if accepts_payload_token else None
        git_host = body.get("git_host") if accepts_payload_token else None
        git_username = body.get("git_username") if accepts_payload_token else None
        extra: Any = None
        if enroll_extension is not None:
            try:
                extra = enroll_extension.parse(request.app, body)
            except EnrollRejected as exc:
                logger.warning(f"{proxy_name}_enrollment_rejected", sandbox_id=sandbox_id, reason=str(exc))
                return web.json_response({"error": "bad_request", "detail": str(exc)}, status=400)
        registry: EnrollmentRegistry = request.app["enrollment"]
        try:
            registry.enroll(
                sandbox_id,
                sandbox_token,
                payload_token=payload_token if isinstance(payload_token, str) and payload_token else None,
                git_host=git_host if isinstance(git_host, str) and git_host else None,
                git_username=git_username if isinstance(git_username, str) and git_username else None,
                ttl_seconds=ttl_seconds,
            )
        except RegistryFullError:
            logger.error(f"{proxy_name}_enrollment_registry_full", sandbox_id=sandbox_id)
            return web.json_response({"error": "registry_full"}, status=429)
        response: dict[str, Any] = {"status": "enrolled"}
        if enroll_extension is not None:
            response.update(enroll_extension.apply(request.app, sandbox_id, extra))
        logger.info(f"{proxy_name}_sandbox_enrolled", sandbox_id=sandbox_id)
        return web.json_response(response)

    async def _unenroll_handler(request: web.Request) -> web.Response:
        denied = guard_admin(request, proxy_name)
        if denied is not None:
            return denied
        sandbox_id = request.match_info["sandbox_id"]
        if not _SANDBOX_ID_RE.fullmatch(sandbox_id):
            return web.json_response({"error": "bad_request"}, status=400)
        registry: EnrollmentRegistry = request.app["enrollment"]
        registry.unenroll(sandbox_id)  # runs on_unenroll when there was an enrollment
        logger.info(f"{proxy_name}_sandbox_unenrolled", sandbox_id=sandbox_id)
        return web.json_response({"status": "unenrolled"})

    app.router.add_post("/admin/enroll", _enroll_handler)
    app.router.add_delete("/admin/enroll/{sandbox_id}", _unenroll_handler)


def check_bearer(request: web.Request, proxy_name: str) -> str | None:
    """Shared bearer validation: return the matched sandbox_id or None (logged)."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        logger.warning(f"{proxy_name}_unauthorized", path=request.path)
        return None
    presented = auth[len("Bearer ") :].strip()
    if not presented:
        logger.warning(f"{proxy_name}_unauthorized", path=request.path)
        return None
    registry: EnrollmentRegistry = request.app["enrollment"]
    sandbox_id = registry.lookup(presented)
    if sandbox_id is None:
        logger.warning(f"{proxy_name}_unauthorized", path=request.path)
        return None
    return sandbox_id


def git_identity(request: web.Request, sandbox_id: str) -> tuple[str, str] | None:
    """``(host, username)`` enrolled for this sandbox, or None."""
    registry: EnrollmentRegistry = request.app["enrollment"]
    return registry.git_identity_for(sandbox_id)


def payload_token(request: web.Request, sandbox_id: str, fallback: str) -> str:
    """Per-sandbox payload credential with global fallback."""
    registry: EnrollmentRegistry = request.app["enrollment"]
    return registry.payload_token_for(sandbox_id) or fallback
