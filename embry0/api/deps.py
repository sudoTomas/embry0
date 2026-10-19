"""FastAPI dependency functions."""

import asyncio
import hmac
import time
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Header, HTTPException, Request

from embry0.api.route_table import route_template
from embry0.api.session import TIER_ADMIN, TIER_OPERATOR, SessionData, request_session

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Key-scope -> effective tier on the unified principal ladder
# (read < operate(=operator) < admin). `read` is additionally restricted to
# safe methods in the auth dependency.
#
# `merge` sits off the ladder (#319 phase 2): the key the chat relay holds
# for the operator's approval chat. It may read, relay a merge decision and
# answer agent questions — and nothing else, so a leaked copy cannot cancel
# or create jobs, edit env vars or trigger the watcher.
TIER_MERGE = "merge"
_SCOPE_TIER = {"read": "read", "operate": TIER_OPERATOR, "admin": TIER_ADMIN, "merge": TIER_MERGE}

#: The only mutations a merge-scope key may make, as (method, route template).
#: Matched on the full route template (``route_template``), not the raw path,
#: so an id can never be crafted to look like an allowed route.
MERGE_SCOPE_MUTATIONS = frozenset(
    {
        ("POST", "/api/v1/merge-approvals/{approval_id}/decide"),
        # Accepting a watcher finding of a stack THIS orchestrator watches.
        # The route refuses anything else (another origin, a stack that is
        # not watched here or is disabled), so the scope grants nothing
        # beyond this deployment's own findings. Within those it grants the
        # whole acceptance, in whichever tracker the draft was filed: since
        # #460 that includes putting the trigger label on a Linear draft and
        # dispatching it — one paid job per call, as for a GitHub draft. It
        # cannot label or dispatch a ticket the watcher did not file.
        ("POST", "/api/v1/cases/{case_id}/accept"),
        ("POST", "/api/v1/jobs/{job_id}/inputs/{input_id}/answer"),
        ("POST", "/api/v1/issues/{issue_id}/inputs/{input_id}/answer"),
        # Read-only despite the verb: validates a graph, stores nothing.
        ("POST", "/api/v1/pipelines/validate"),
    }
)


@dataclass(frozen=True, slots=True)
class Principal:
    """Who authenticated this request (RAV-1056 Phase B)."""

    kind: str  # "master" | "key" | "session"
    tier: str  # "read" | "operator" | "admin"
    key_id: str | None = None
    key_name: str | None = None
    email: str | None = None
    # Repositories this key may start work on; None = unrestricted. Enforced
    # by embry0.api.repo_allowlist at each route that starts or continues work.
    allowed_repos: tuple[str, ...] | None = None


# RAV-1056 Phase A: per-request session gate against the users table
# (epoch + disabled), cached briefly — single-process deploy, and the
# mutating endpoints bust entries directly.
_USER_GATE_TTL_SECONDS = 30.0
_user_gate_cache: dict[str, tuple[float, int, bool]] = {}  # sub -> (expires, epoch, disabled)


def bust_user_gate_cache(auth0_sub: str | None = None) -> None:
    if auth0_sub is None:
        _user_gate_cache.clear()
    else:
        _user_gate_cache.pop(auth0_sub, None)


async def validate_session_user(app: Any, session: SessionData) -> bool:
    """DB-backed session gate: user not disabled AND cookie epoch current.

    Fail-open when no users repo is wired (unit-test stub apps) — the
    signed cookie alone is then the whole credential, as before Phase A.
    """
    users_repo = getattr(app.state, "users_repo", None)
    if users_repo is None:
        return True
    now = time.monotonic()
    cached = _user_gate_cache.get(session.sub)
    if cached and cached[0] > now:
        _, epoch, disabled = cached
    else:
        row = await users_repo.get_by_sub(session.sub)
        if row is None:
            # No row for this sub: cookie predates the users table (or the
            # row was deleted). Stale by definition — force a fresh login,
            # which JIT-provisions the row.
            _user_gate_cache[session.sub] = (now + _USER_GATE_TTL_SECONDS, -1, True)
            return False
        epoch, disabled = row.session_epoch, row.disabled
        _user_gate_cache[session.sub] = (now + _USER_GATE_TTL_SECONDS, epoch, disabled)
    return (not disabled) and session.epoch == epoch


# RAV-1056 Phase B: DB-backed API keys. Cache mirrors the user-gate cache:
# 30s TTL, busted directly by mint/revoke (single-process deploy).
_KEY_CACHE_TTL_SECONDS = 30.0
# key_hash -> (expires, key_id, key_name, scope, user_id, revoked, expired, allowed_repos)
_api_key_cache: dict[str, tuple[float, str, str, str, str | None, bool, bool, tuple[str, ...] | None]] = {}
# user_id -> (expires, disabled, email) — owner gate for owned keys
_key_owner_cache: dict[str, tuple[float, bool, str]] = {}


def bust_api_key_cache() -> None:
    _api_key_cache.clear()
    _key_owner_cache.clear()


async def resolve_bearer_principal(app: Any, token: str) -> Principal | None:
    """Validate a bearer token: the master API_KEY, else a live DB key.

    Returns the Principal or None (invalid/revoked/expired/owner-disabled).
    Shared by the HTTP dependencies, ``authenticate_stream`` (the SSE
    streams) and the rate limiter. The jobs WebSocket handshake takes no key:
    it takes a token minted by ``POST /jobs/{id}/events-token`` or an SSO
    session.
    """
    config = app.state.config
    if config.api_key and hmac.compare_digest(token, config.api_key):
        return Principal(kind="master", tier=TIER_ADMIN)

    keys_repo = getattr(app.state, "api_keys_repo", None)
    if keys_repo is None or not token:
        return None

    from embry0.storage.repositories.api_keys import hash_token

    key_hash = hash_token(token)
    now = time.monotonic()
    cached = _api_key_cache.get(key_hash)
    if cached and cached[0] > now:
        _, key_id, key_name, scope, user_id, revoked, expired, allowed_repos = cached
    else:
        row = await keys_repo.get_by_hash(key_hash)
        if row is None:
            _api_key_cache[key_hash] = (now + _KEY_CACHE_TTL_SECONDS, "", "", "", None, True, True, None)
            return None
        expired = row.expires_at is not None and row.expires_at.timestamp() < time.time()
        key_id, key_name, scope, user_id, revoked, allowed_repos = (
            row.id,
            row.name,
            row.scope,
            row.user_id,
            row.revoked_at is not None,
            row.allowed_repos,
        )
        _api_key_cache[key_hash] = (
            now + _KEY_CACHE_TTL_SECONDS,
            key_id,
            key_name,
            scope,
            user_id,
            revoked,
            expired,
            allowed_repos,
        )
    if not key_id or revoked or expired:
        return None

    email: str | None = None
    if user_id is not None:
        users_repo = getattr(app.state, "users_repo", None)
        if users_repo is not None:
            owner = _key_owner_cache.get(user_id)
            if owner and owner[0] > now:
                _, disabled, email = owner
            else:
                user = await users_repo.get(user_id)
                disabled = user is None or user.disabled
                email = user.email if user else ""
                _key_owner_cache[user_id] = (now + _KEY_CACHE_TTL_SECONDS, disabled, email or "")
            if disabled:
                return None

    # Coarse activity signal; throttled repo-side, never blocks the request.
    task = asyncio.create_task(keys_repo.touch_last_used(key_id))
    task.add_done_callback(lambda t: t.exception())

    return Principal(
        kind="key",
        tier=_SCOPE_TIER[scope],
        key_id=key_id,
        key_name=key_name,
        email=email or None,
        allowed_repos=allowed_repos,
    )


async def _resolve_session_auth(request: Request) -> str | None:
    """Try SSO session auth; returns the tier on success, None when no session.

    A valid session with tier=none (authenticated account but no
    embry0 role — the default-deny gate) raises 403 with a distinct detail
    so the frontend can render access-denied instead of the login prompt.
    A session failing the users-table gate (disabled / stale epoch) is 401 —
    a fresh login re-evaluates everything.
    """
    session = request_session(request)
    if session is None:
        return None
    if session.tier in (TIER_OPERATOR, TIER_ADMIN):
        if not await validate_session_user(request.app, session):
            raise HTTPException(status_code=401, detail="Session no longer valid — sign in again")
        request.state.session = session
        return session.tier
    raise HTTPException(
        status_code=403,
        detail="sso_role_required: your account has no embry0 role — ask an admin for embry0-operator",
    )


async def _auth_dependency(
    request: Request,
    authorization: str = Header(default=""),
) -> None:
    """Dual auth (RAV-1069): API-key Bearer (any origin — MCP/CLI/LAN and the
    public hostname alike) OR an Auth0 session cookie with tier operator+.

    AUTH_DEV_MODE is the only switch that skips it (#518). An empty API_KEY
    does not: startup refuses one outside AUTH_DEV_MODE, and a process that
    runs with one anyway matches no master key, so only a DB key or an SSO
    session gets in."""
    config = request.app.state.config
    if config.auth_dev_mode:
        return
    if authorization:
        principal = await _bearer_principal_or_401(request, authorization)
        if principal.tier == "read" and request.method not in SAFE_METHODS:
            raise HTTPException(status_code=403, detail="read-scope key cannot perform mutations")
        if (
            principal.tier == TIER_MERGE
            and request.method not in SAFE_METHODS
            and (request.method, route_template(request)) not in MERGE_SCOPE_MUTATIONS
        ):
            raise HTTPException(
                status_code=403,
                detail="merge-scope key may only decide merge approvals, accept watcher findings and answer agent questions",
            )
        return
    if await _resolve_session_auth(request) is not None:
        return
    raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")


async def _bearer_principal_or_401(request: Request, authorization: str) -> Principal:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    principal = await resolve_bearer_principal(request.app, authorization[7:])
    if principal is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    request.state.principal = principal
    return principal


async def _admin_dependency(
    request: Request,
    authorization: str = Header(default=""),
) -> None:
    """Admin-tier gate (RAV-1069): API key (the master credential) OR an SSO
    session carrying embry0-admin. Applied on top of require_auth to the
    sensitive mutations: env-secret reveals, agent updates/resets, integration
    config writes. Skipped only under AUTH_DEV_MODE, as require_auth is."""
    config = request.app.state.config
    if config.auth_dev_mode:
        return
    if authorization:
        principal = await _bearer_principal_or_401(request, authorization)
        if principal.kind == "master" or principal.tier == TIER_ADMIN:
            return
        raise HTTPException(status_code=403, detail="admin-scope key or embry0-admin role required")
    tier = await _resolve_session_auth(request)
    if tier == TIER_ADMIN:
        return
    if tier == TIER_OPERATOR:
        raise HTTPException(status_code=403, detail="embry0-admin role required")
    raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")


def unidentified_caller_tier(request: Request) -> str:
    """Tier of a request that reached a handler with no session and no principal.

    Only AUTH_DEV_MODE lets one through the gate, and it then has full access
    and no identity. Outside it the route has lost its auth dependency, so the
    request is refused rather than treated as admin (#518).
    """
    if request.app.state.config.auth_dev_mode:
        return TIER_ADMIN
    raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")


def request_actor(request: Request) -> str:
    """Audit actor: session email > key owner email > key name > client IP."""
    session = getattr(request.state, "session", None)
    if session is not None and session.email:
        return str(session.email)
    principal = getattr(request.state, "principal", None)
    if principal is not None and principal.kind == "key":
        if principal.email:
            return str(principal.email)
        return f"key:{principal.key_name}"
    return (request.client.host if request.client else None) or "unknown"


# Reusable dependency: requires a valid API key OR an operator+ SSO session.
require_auth = Depends(_auth_dependency)


async def authenticate_stream(request: Request, scope: str, token: str | None) -> None:
    """Auth for a browser stream: a scoped ``token`` query param, a session or a Bearer header.

    EventSource cannot send an Authorization header, so a stream route also
    accepts a short-lived token minted for ``scope`` by an authenticated POST
    (RAV-1060). The API key itself is never accepted from the query string:
    a URL lands in access logs, history and Referer. Raises 401 otherwise.
    """
    from embry0.api.scoped_tokens import signing_material, verify_scoped_token

    config = request.app.state.config
    # #518: only AUTH_DEV_MODE skips this, never an empty API_KEY.
    if config.auth_dev_mode:
        return
    if token is not None and verify_scoped_token(signing_material(config), scope, token):
        return
    # RAV-1069: an SSO session (operator+) authenticates the stream directly —
    # same-origin EventSource sends the cookie with no header plumbing.
    # RAV-1056: subject to the users-table gate (disabled / stale epoch).
    session = request_session(request)
    if session is not None and session.tier in (TIER_ADMIN, TIER_OPERATOR):
        if await validate_session_user(request.app, session):
            return
    # RAV-1056: any live key (>= read) authenticates the stream.
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        principal = await resolve_bearer_principal(request.app, authorization[7:])
        if principal is not None:
            return
    raise HTTPException(status_code=401, detail="Missing or invalid credentials")


# Stricter dependency for admin-tier operations.
require_admin = Depends(_admin_dependency)


def get_config(request: Request) -> Any:
    """Return the app config from request state."""
    return request.app.state.config


def get_db(request: Request) -> Any:
    """Return the DatabasePool from request state."""
    return request.app.state.db


def get_jobs_repo(request: Request) -> Any:
    """Return the JobsRepository from request state."""
    return request.app.state.jobs_repo


def get_traces_repo(request: Request) -> Any:
    """Return the TracesRepository from request state."""
    return request.app.state.traces_repo


def get_deliverables_repo(request: Request) -> Any:
    """Return the DeliverablesRepository from request state."""
    return request.app.state.deliverables_repo


def get_profiles_repo(request: Request) -> Any:
    """Return the SandboxProfilesRepository from request state."""
    return request.app.state.profiles_repo


def get_budget_repo(request: Request) -> Any:
    """Return the BudgetConfigRepository from request state."""
    return request.app.state.budget_repo


def get_context_repo(request: Request) -> Any:
    """Return the ContextConfigRepository from request state."""
    return request.app.state.context_repo


def get_agent_defs_repo(request: Request) -> Any:
    """Return the AgentDefinitionsRepository from request state."""
    return request.app.state.agent_defs_repo


def get_templates_repo(request: Request) -> Any:
    """Return the PipelineTemplatesRepository from request state."""
    return request.app.state.templates_repo


def get_integration_repo(request: Request) -> Any:
    """Return the IntegrationConfigRepository from request state."""
    return request.app.state.integration_repo


def get_provider_repo(request: Request) -> Any:
    """Return the ProviderConfigRepository from request state."""
    return request.app.state.provider_repo


def get_users_repo(request: Request) -> Any:
    """Return the UsersRepository from request state."""
    return request.app.state.users_repo


def get_audit_repo(request: Request) -> Any:
    """Return the AuditRepository from request state."""
    return request.app.state.audit_repo


def get_issues_repo(request: Request) -> Any:
    """Return the IssuesRepository from request state."""
    return request.app.state.issues_repo


def get_healing_cases_repo(request: Request) -> Any:
    """Return the HealingCasesRepository from request state, or 503 before startup wired it."""
    repo = getattr(request.app.state, "healing_cases_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="healing-case storage not initialized")
    return repo


def get_github_sync(request: Request) -> Any:
    """Return the GitHubSyncService from request state."""
    return request.app.state.github_sync


def get_issue_executor(request: Request) -> Any:
    """Return the IssueExecutor from request state."""
    return request.app.state.issue_executor


def get_inputs_repo(request: Request) -> Any:
    """Return the IssueInputsRepository from request state."""
    return request.app.state.inputs_repo


def get_qa_minio(request: Request) -> Any:
    """Return the QA MinIO client from request state, or 503 if unconfigured."""
    from fastapi import HTTPException

    minio = getattr(request.app.state, "qa_minio", None)
    if minio is None:
        raise HTTPException(status_code=503, detail="QA artifact storage unavailable")
    return minio


def get_runtime(request: Request) -> Any:
    """Return the container runtime from request state, or 503 if unconfigured."""
    from fastapi import HTTPException

    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="container runtime unavailable")
    return runtime
