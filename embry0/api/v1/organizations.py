"""Organizations API (ADR 0010): the forge owners embry0 maintains as a whole.

Reads are operator-tier like the rest of the registry; writes are admin-tier,
because registering an owner points embry0's credentials and, later, its
spend at every repository under it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import Scope

from embry0.api.deps import request_actor, require_admin
from embry0.audit.helpers import emit_audit
from embry0.services import org_discovery, org_webhooks
from embry0.services import organizations as org_service
from embry0.services.org_discovery import Discovery
from embry0.services.organizations import FORGES, STATUSES, AccessProbe, Organization
from embry0.storage.repositories.org_repos import OrgReposRepository
from embry0.storage.repositories.organizations import OrganizationsRepository

logger = structlog.get_logger(__name__)
router = APIRouter()


class OrganizationBody(BaseModel):
    owner: str = Field(
        min_length=1, max_length=200, description="the forge owner: a GitHub organization or user, or a GitLab group"
    )
    forge: str = Field(default="github", pattern="^(github|gitlab)$")
    credential_id: str | None = Field(
        default=None,
        description="a forge_token credential from the registry; absent means the environment token for this owner",
    )
    display_name: str | None = Field(default=None, max_length=200)
    settings: dict[str, Any] = Field(default_factory=dict, description="defaults for repositories under this owner")
    probe: bool = Field(default=True, description="call the forge to learn what the credential can do before storing")


class ProbeBody(BaseModel):
    owner: str = Field(
        min_length=1, max_length=200, description="the forge owner: a GitHub organization or user, or a GitLab group"
    )
    forge: str = Field(default="github", pattern="^(github|gitlab)$")
    credential_id: str | None = Field(
        default=None,
        description="a forge_token credential from the registry; absent means the environment token for this owner",
    )


class StatusBody(BaseModel):
    status: str = Field(pattern="^(active|paused|removed)$")


class PatchBody(BaseModel):
    # forbid: {"auto_register": true} at the top level is a mistake that would
    # otherwise answer 200 and change nothing.
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, max_length=200)
    settings: dict[str, Any] | None = Field(
        default=None, description="settings to set, merged into the stored ones; an unknown key is refused"
    )


def _repo(request: Request) -> OrganizationsRepository:
    repository = getattr(request.app.state, "organizations_repo", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="organization storage not initialized")
    return cast("OrganizationsRepository", repository)


async def _token_for(request: Request, owner: str, forge: str, credential_id: str | None) -> tuple[str, str]:
    """The secret embry0 would act with at ``owner``, and where it came from."""
    st = request.app.state
    if credential_id:
        credentials_repo = getattr(st, "credentials_repo", None)
        secrets = getattr(st, "secrets_provider", None)
        if credentials_repo is None or secrets is None:
            raise HTTPException(status_code=503, detail="credential storage not initialized")
        row = await credentials_repo.get(credential_id)
        if row is None:
            raise HTTPException(status_code=404, detail="credential not found")
        if row["kind"] != "forge_token" or row["provider"] != forge:
            raise HTTPException(status_code=422, detail=f"credential {credential_id} is not a {forge} forge token")
        if row["status"] != "ok":
            raise HTTPException(status_code=422, detail=f"credential {credential_id} is {row['status']}")
        return await secrets.decrypt(row["secret_ciphertext"]), f"registry:{row['label']}"
    config = getattr(st, "config", None)
    if forge == "gitlab":
        token = str(getattr(config, "gitlab_token", "") or "")
        key = "GITLAB_TOKEN"
    else:
        from embry0.execution.github_tokens import resolve_github_token

        token = resolve_github_token(owner, str(getattr(config, "github_token", "") or ""))
        key = "GITHUB_TOKEN__" + owner.upper().replace("-", "_")
    if not token:
        raise HTTPException(
            status_code=422,
            detail=f"no {forge} credential for {owner}: pick one from the registry or set {key}",
        )
    return token, f"env:{key}"


def _owner_name(raw: str) -> str:
    owner = raw.strip().lower()
    if "/" in owner:
        raise HTTPException(status_code=422, detail="an organization is a forge owner, with no slash")
    return owner


async def probe_access(request: Request, owner: str, forge: str, token: str) -> AccessProbe:
    config = getattr(request.app.state, "config", None)
    if forge == "gitlab":
        from embry0.execution.tls import load_ca_pem

        return await org_service.probe_gitlab(
            owner,
            token,
            gitlab_url=str(getattr(config, "gitlab_url", "") or ""),
            ca_pem=load_ca_pem(str(getattr(config, "gitlab_ca_bundle", "") or "")),
        )
    return await org_service.probe_github(owner, token)


@router.get("/orgs")
async def list_organizations(request: Request, include_removed: bool = False) -> dict[str, Any]:
    orgs = await _repo(request).list_all(include_removed=include_removed)
    return {"organizations": [o.to_dict() for o in orgs], "forges": list(FORGES), "statuses": list(STATUSES)}


# Declared before every /orgs/{owner} route, so "probe" is never read as an owner.
@router.post("/orgs/probe", dependencies=[require_admin])
async def probe_organization(body: ProbeBody, request: Request) -> dict[str, Any]:
    """Ask the forge what a credential can do at an owner, and store nothing.

    The same probe ``POST /orgs`` runs before it registers, so an operator
    sees the answer before committing. A credential that cannot list the
    owner is an answer here (``read`` false), not a refusal.
    """
    owner = _owner_name(body.owner)
    token, source = await _token_for(request, owner, body.forge, body.credential_id)
    probe = await probe_access(request, owner, body.forge, token)
    return {**probe.to_dict(), "source": source}


@router.get("/orgs/{owner}")
async def get_organization(owner: str, request: Request) -> dict[str, Any]:
    org = await _repo(request).get(owner)
    if org is None:
        raise HTTPException(status_code=404, detail=f"no organization {owner}")
    return org.to_dict()


@router.post("/orgs", status_code=201, dependencies=[require_admin])
async def register_organization(body: OrganizationBody, request: Request) -> dict[str, Any]:
    """Register an owner. With ``probe`` on, the forge is asked what the
    credential can do there first, and an owner the credential cannot even
    list is refused with the probe's own words."""
    owner = _owner_name(body.owner)
    access: dict[str, Any] = {}
    repo_count: int | None = None
    if body.probe:
        token, source = await _token_for(request, owner, body.forge, body.credential_id)
        probe = await probe_access(request, owner, body.forge, token)
        if not probe.read:
            raise HTTPException(status_code=422, detail=f"{source} cannot list {owner}: {probe.detail}")
        access = {**probe.to_dict(), "source": source}
        repo_count = probe.repo_count
    try:
        org = await _repo(request).register(
            owner,
            body.forge,
            credential_id=body.credential_id,
            display_name=body.display_name,
            settings=body.settings,
            created_by=request_actor(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if access:
        org = await _repo(request).update_access(owner, access, repo_count=repo_count) or org
    await emit_audit(
        getattr(request.app.state, "db", None),
        "organization.register",
        actor=request_actor(request),
        details={"owner": owner, "forge": body.forge, "credential_id": body.credential_id, "probed": body.probe},
    )
    return org.to_dict()


@router.post("/orgs/{owner}/probe", dependencies=[require_admin])
async def reprobe_organization(owner: str, request: Request) -> dict[str, Any]:
    """Ask the forge again what the stored credential can do at this owner."""
    repository = _repo(request)
    org = await repository.get(owner)
    if org is None:
        raise HTTPException(status_code=404, detail=f"no organization {owner}")
    token, source = await _token_for(request, org.owner, org.forge, org.credential_id)
    probe = await probe_access(request, org.owner, org.forge, token)
    updated = await repository.update_access(
        org.owner, {**probe.to_dict(), "source": source}, repo_count=probe.repo_count
    )
    # access.hooks authorises the webhook install and the hook action, so a
    # rewrite of it is audited like every other organization mutation.
    await emit_audit(
        getattr(request.app.state, "db", None),
        "organization.probe",
        actor=request_actor(request),
        details={"owner": org.owner, "source": source, "read": probe.read, "hooks": probe.hooks},
    )
    return (updated or org).to_dict()


@router.patch("/orgs/{owner}", dependencies=[require_admin])
async def patch_organization(owner: str, body: PatchBody, request: Request) -> dict[str, Any]:
    """Change the display name, or settings such as ``auto_register``.

    Settings merge: the keys given are set, the rest stay. Only the keys in
    :data:`embry0.services.organizations.SETTINGS` are accepted, with their
    types, so a typo cannot sit in the row as a setting nothing reads.
    """
    repository = _repo(request)
    org = await repository.get(owner)
    if org is None:
        raise HTTPException(status_code=404, detail=f"no organization {owner}")
    settings: dict[str, Any] | None = None
    if body.settings is not None:
        try:
            settings = org_service.validate_settings(body.settings)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    updated = await repository.patch(org.owner, display_name=body.display_name, settings=settings)
    await emit_audit(
        getattr(request.app.state, "db", None),
        "organization.patch",
        actor=request_actor(request),
        details={"owner": org.owner, "settings": settings, "display_name": body.display_name},
    )
    return (updated or org).to_dict()


@router.put("/orgs/{owner}/status", dependencies=[require_admin])
async def set_organization_status(owner: str, body: StatusBody, request: Request) -> dict[str, Any]:
    org = await _repo(request).set_status(owner, body.status)
    if org is None:
        raise HTTPException(status_code=404, detail=f"no organization {owner}")
    await emit_audit(
        getattr(request.app.state, "db", None),
        "organization.status",
        actor=request_actor(request),
        details={"owner": org.owner, "status": body.status},
    )
    return org.to_dict()


# ---- discovery (slice 2) ----


def _facts_repo(request: Request) -> OrgReposRepository:
    repository = getattr(request.app.state, "org_repos_repo", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="organization storage not initialized")
    return cast("OrgReposRepository", repository)


def _hook_origins(request: Request) -> tuple[str, ...]:
    """Where this deployment's webhooks point: WEBHOOK_PUBLIC_URL, the one
    public base URL forges deliver to, or nothing when it is unset or a
    loopback address no forge can reach, and discovery then reads a
    repository's hooks as unknown, not absent.

    The SSO and dashboard origins are not webhook origins: the public
    dashboard answers 404 on webhook paths, so a hook there reaches nothing.
    """
    config = getattr(request.app.state, "config", None)
    origin = org_discovery.public_origin(str(getattr(config, "webhook_public_url", "") or ""))
    return (origin,) if origin else ()


def _tracker_map(request: Request) -> dict[str, str]:
    config = getattr(request.app.state, "config", None)
    raw = str(getattr(config, "linear_repo_map", "") or "")
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def embry0_state_lookup(request: Request) -> org_discovery.StateLookup:
    """What embry0 already knows about a repository, from its own tables."""
    st = request.app.state
    repos_repo = getattr(st, "repos_repo", None)
    qa_repo = getattr(st, "qa_configs_repo", None)
    stacks_repo = getattr(st, "watcher_stacks_repo", None)
    env_repo = getattr(st, "env_repo", None)
    mapped = set(_tracker_map(request).values())
    default_deploy_logql = str(getattr(getattr(st, "config", None), "watcher_deploy_logql", "") or "")
    global_keys: list[str] | None = None

    async def env_keys_set(repo: str) -> list[str]:
        """The variable names embry0 already holds for the repository, its
        own and the global ones. Names only: a value never enters the facts."""
        nonlocal global_keys
        if env_repo is None:
            return []
        if global_keys is None:
            global_keys = [str(v["key"]) for v in await env_repo.get_global()]
        own = [str(v["key"]) for v in await env_repo.get_repo(repo)]
        return sorted({*global_keys, *own})

    async def lookup(repo: str) -> dict[str, Any]:
        row = await repos_repo.get(repo) if repos_repo is not None else None
        qa = await qa_repo.get(repo) if qa_repo is not None else None
        stack = await stacks_repo.get(repo) if stacks_repo is not None else None
        settings = dict(getattr(stack, "settings", None) or {})
        return {
            "registered": row is not None,
            "active": bool(row and row.get("active")),
            "qa_config": qa is not None,
            "watcher_stack": stack is not None,
            # Whether confirm-healed can anchor a fix: the stack names a deploy
            # query, or the deployment has a global one.
            "deploy_marker": bool(stack is not None and (settings.get("deploy_logql") or default_deploy_logql)),
            "tracker_mapped": repo in mapped,
            "env_keys_set": await env_keys_set(repo),
        }

    return lookup


#: Direct reads one run makes to confirm vanished repositories. Past it the
#: run confirms none: that many active registrations leaving at once is a
#: credential's sight narrowing more likely than deletions.
VANISHED_CONFIRM_LIMIT = 50


async def note_vanished(
    facts_repo: OrgReposRepository,
    owner: str,
    run_id: str,
    found: Discovery,
    embry0_state: org_discovery.StateLookup,
    client: Any,
) -> dict[str, int]:
    """Check every repository an earlier run listed and this one did not
    (ADR 0014); the summary's ``vanished`` and ``vanished_confirmed``.

    Such a repository left the listing, or the listing missed it: it pages
    over newest push first, so a push between two page reads moves a
    repository onto a page already read. Its row keeps what the forge said
    when it was last seen, but whether embry0 holds it as an active
    registration is read now. Each one still held is then read directly on
    the forge (:func:`org_discovery.confirm_gone`), and only a confirmed
    departure is stamped ``vanished_run``, the one mark the needs list
    proposes deactivation from. Nothing is read, and nothing stamped, when
    the page cap cut the listing short, or when more candidates than
    :data:`VANISHED_CONFIRM_LIMIT` are held. A row only the webhook receiver
    wrote was never listed, so it is not one; nor is a spelling this run
    listed under another case.
    """
    listed = {facts.repo.lower() for facts in found.repos}
    candidates = [
        str(row["repo"])
        for row in await facts_repo.list_for_owner(owner)
        if row.get("run_id") not in (None, run_id) and str(row["repo"]).lower() not in listed
    ]
    counts = {"vanished": len(candidates), "vanished_confirmed": 0}
    if not found.complete:
        logger.warning(
            "org_vanished_unchecked",
            owner=owner,
            run_id=run_id,
            reason="listing_truncated",
            candidates=len(candidates),
        )
        return counts
    states: dict[str, dict[str, Any]] = {}
    for repo in candidates:
        state = await embry0_state(repo)
        states[repo] = {"registered": bool(state.get("registered")), "active": bool(state.get("active"))}
    held = [repo for repo, state in states.items() if state["registered"] and state["active"]]
    verdicts: dict[str, bool | None] = {}
    if len(held) > VANISHED_CONFIRM_LIMIT:
        logger.warning(
            "org_vanished_unchecked",
            owner=owner,
            run_id=run_id,
            reason="over_confirm_limit",
            candidates=len(held),
            limit=VANISHED_CONFIRM_LIMIT,
        )
    else:
        for repo in held:
            verdicts[repo] = await org_discovery.confirm_gone(client, repo)
            if verdicts[repo] is not True:
                logger.info(
                    "org_vanished_not_confirmed",
                    owner=owner,
                    run_id=run_id,
                    repo=repo,
                    answer="still there" if verdicts[repo] is False else "unknown",
                )
    for repo, state in states.items():
        gone = verdicts.get(repo) is True
        if gone:
            counts["vanished_confirmed"] += 1
        await facts_repo.refresh_state(repo, {**state, "vanished_run": run_id if gone else None})
    return counts


async def run_discovery(
    request: Request,
    owner: str,
    forge: str,
    token: str,
    owner_kind: str | None,
    run_id: str,
    *,
    owner_says: int | None = None,
) -> None:
    facts_repo = _facts_repo(request)
    orgs_repo = _repo(request)
    try:
        client: Any
        if forge == "gitlab":
            raise RuntimeError("GitLab discovery is not in this release")
        from embry0.execution.github_client import GitHubClient

        client = GitHubClient(default_token=token, max_attempts=2)

        async def progress(seen: int, total: int) -> None:
            await facts_repo.progress(run_id, seen=seen, total=total)

        lookup = embry0_state_lookup(request)
        found = await org_discovery.discover(
            client,
            owner,
            owner_kind=owner_kind,
            hook_origins=_hook_origins(request),
            embry0_state=lookup,
            progress=progress,
        )
        for facts in found.repos:
            await facts_repo.upsert_fact(facts, run_id=run_id)
        vanished = await note_vanished(facts_repo, owner, run_id, found, lookup, client)
        summary = org_discovery.summarize(found.repos, owner_says=owner_says)
        summary.update(vanished, listing_complete=found.complete)
        await facts_repo.finish_run(run_id, summary=summary)
        await orgs_repo.mark_discovered(owner, repo_count=summary["repos"])
        logger.info(
            "org_discovery_done", owner=owner, run_id=run_id, **{k: v for k, v in summary.items() if k != "count_gap"}
        )
    except Exception as exc:  # noqa: BLE001 - the run row carries the failure
        logger.warning("org_discovery_failed", owner=owner, run_id=run_id, error=type(exc).__name__)
        await facts_repo.finish_run(run_id, error=f"{type(exc).__name__}: {str(exc)[:200]}")
        return
    # The needs are synced here, when the run finishes, and never on a plain
    # read. A failure is its own log line, never the run's: the run is left
    # unsynced, so the next GET /orgs/{owner}/needs catches up once.
    from embry0.api.v1.org_needs import needs_service

    service = needs_service(request, required=False)
    if service is not None:
        try:
            await service.sync(owner)
        except Exception as exc:  # noqa: BLE001 - logged, the run is already done
            logger.warning("org_needs_sync_failed", owner=owner, run_id=run_id, error=type(exc).__name__)


@router.post("/orgs/{owner}/discover", status_code=202, dependencies=[require_admin])
async def discover_organization(owner: str, request: Request) -> dict[str, Any]:
    """Walk every repository under the owner and store one fact row each.

    Runs in the orchestrator process and returns the run at once; poll
    ``GET /orgs/{owner}/runs`` for progress. One run per owner at a time.
    """
    org = await _repo(request).get(owner)
    if org is None:
        raise HTTPException(status_code=404, detail=f"no organization {owner}")
    if org.status != "active":
        raise HTTPException(status_code=409, detail=f"organization {owner} is {org.status}")
    running = await _facts_repo(request).running_run(org.owner)
    if running is not None:
        raise HTTPException(status_code=409, detail=f"discovery {running['id']} is already running for {owner}")
    return await start_discovery(request, org)


def app_request(app: Any) -> Request:
    """A request bound to ``app`` and to no caller, for a run nobody asked for
    over HTTP: the rediscovery schedule's (ADR 0014). The start path reads
    only ``request.app.state``; who started the run is ``actor``."""
    scope: Scope = {"type": "http", "app": app, "method": "POST", "path": "/", "headers": [], "query_string": b""}
    return Request(scope)


async def start_discovery(request: Request, org: Organization, *, actor: str | None = None) -> dict[str, Any]:
    """Start one discovery run for an active ``org`` in the background and return its run row.

    This endpoint, the organization actions (a rediscovery after a change)
    and the rediscovery schedule all start runs here. The caller has checked
    that none is running; a start that loses a race with another is refused
    with 409 by the repository's own check. ``actor`` names the starter in
    the audit row when it is not the request's caller.
    """
    facts_repo = _facts_repo(request)
    token, _source = await _token_for(request, org.owner, org.forge, org.credential_id)
    run = await facts_repo.start_run(org.owner)
    if run is None:
        raise HTTPException(status_code=409, detail=f"a discovery run is already running for {org.owner}")
    access = org.access if isinstance(org.access, dict) else {}
    owner_kind = access.get("owner_kind")
    owner_says = access.get("repo_count") if isinstance(access.get("repo_count"), int) else None
    task = asyncio.create_task(
        run_discovery(request, org.owner, org.forge, token, owner_kind, run["id"], owner_says=owner_says)
    )
    tasks = getattr(request.app.state, "org_discovery_tasks", None)
    if tasks is None:
        tasks = request.app.state.org_discovery_tasks = set()
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    await emit_audit(
        getattr(request.app.state, "db", None),
        "organization.discover",
        actor=actor or request_actor(request),
        details={"owner": org.owner, "run_id": run["id"]},
    )
    return run


# ---- the organization webhook (ADR 0012) ----


def webhook_origin(request: Request) -> str | None:
    """The public origin the forges deliver webhooks to, or ``None`` when it
    is unset or a loopback address no forge can reach.

    Only ``WEBHOOK_PUBLIC_URL``, read by :func:`_hook_origins` as discovery
    reads it: the dashboard origin is not a fallback, because its vhost
    answers 404 on the webhook routes on purpose.
    """
    origins = _hook_origins(request)
    return origins[0] if origins else None


def make_forge_client(request: Request, org: Organization, token: str) -> Any:
    """The client the webhook routes call the forge with. A module function so
    tests swap in a fake."""
    from embry0.execution.github_client import GitHubClient

    return GitHubClient(default_token=token, max_attempts=2)


async def _org_for_hooks(request: Request, owner: str) -> tuple[Organization, Any]:
    org = await _repo(request).get(owner)
    if org is None:
        raise HTTPException(status_code=404, detail=f"no organization {owner}")
    if org.forge != "github":
        raise HTTPException(status_code=422, detail="organization webhooks are a GitHub feature in this release")
    token, _source = await _token_for(request, org.owner, org.forge, org.credential_id)
    return org, make_forge_client(request, org, token)


@router.get("/orgs/{owner}/webhook")
async def get_organization_webhook(owner: str, request: Request) -> dict[str, Any]:
    """Whether a webhook on the organization points at this deployment, and
    every organization webhook the credential can see, by asking the forge.

    A hook that points elsewhere is listed by its origin only: such URLs often
    carry another service's token, and this route is not admin-only.
    """
    org, client = await _org_for_hooks(request, owner)
    origin = webhook_origin(request)
    target = org_webhooks.hook_target(origin) if origin else None
    hooks = await org_webhooks.list_org_hooks(client, org.owner)
    hook = org_webhooks.hook_pointing_at(hooks, target) if hooks and target else None
    ours = target.rstrip("/") if target else None
    return {
        "owner": org.owner,
        "installed": hook is not None,
        "hook": hook.to_dict() if hook else None,
        "target_url": target,
        "hooks_visible": hooks is not None,
        "hooks": [h.to_dict(origin_only=h.url.rstrip("/") != ours) for h in hooks or []],
    }


@router.post("/orgs/{owner}/webhook", dependencies=[require_admin])
async def install_organization_webhook(owner: str, request: Request) -> dict[str, Any]:
    """Install one webhook on the organization itself, for every repository
    under it, pointing at ``WEBHOOK_PUBLIC_URL`` and signed with the GitHub
    webhook secret. Refused unless the probe found the credential can manage
    hooks. Idempotent: a hook already pointing here is ``already``.
    """
    org, client = await _org_for_hooks(request, owner)
    if org.status != "active":
        raise HTTPException(status_code=409, detail=f"organization {owner} is {org.status}")
    if org.access.get("owner_kind") == "user":
        raise HTTPException(
            status_code=422, detail=f"{owner} is a user account: GitHub has no owner-level webhooks for one"
        )
    if org.access.get("hooks") is not True:
        raise HTTPException(
            status_code=422,
            detail=f"the probe did not find hook administration for {owner}; grant it and probe again",
        )
    origin = webhook_origin(request)
    if origin is None:
        raise HTTPException(
            status_code=422,
            detail="no public webhook origin: set WEBHOOK_PUBLIC_URL to an address the forge can reach, not loopback",
        )
    config = getattr(request.app.state, "config", None)
    secret = str(getattr(config, "github_webhook_secret", "") or "")
    if not secret:
        raise HTTPException(
            status_code=422, detail="GITHUB_WEBHOOK_SECRET is empty: an organization webhook must be signed"
        )
    target = org_webhooks.hook_target(origin)
    result = await org_webhooks.install_org_hook(client, org.owner, target=target, secret=secret)
    await emit_audit(
        getattr(request.app.state, "db", None),
        "organization.webhook_install",
        actor=request_actor(request),
        details={"owner": org.owner, "outcome": result.outcome, "hook_id": result.hook.id if result.hook else None},
    )
    if result.outcome == "failed":
        raise HTTPException(status_code=502, detail=result.detail)
    return {"owner": org.owner, "target_url": target, **result.to_dict()}


@router.get("/orgs/{owner}/repos")
async def organization_repositories(owner: str, request: Request, include_archived: bool = True) -> dict[str, Any]:
    if await _repo(request).get(owner) is None:
        raise HTTPException(status_code=404, detail=f"no organization {owner}")
    rows = await _facts_repo(request).list_for_owner(owner, include_archived=include_archived)
    return {"owner": owner.lower(), "repos": rows, "count": len(rows)}


@router.get("/orgs/{owner}/runs")
async def organization_runs(owner: str, request: Request, limit: int = 20) -> dict[str, Any]:
    if await _repo(request).get(owner) is None:
        raise HTTPException(status_code=404, detail=f"no organization {owner}")
    return {"owner": owner.lower(), "runs": await _facts_repo(request).list_runs(owner, limit=max(1, min(limit, 100)))}
