"""Organizations API (ADR 0010): the forge owners embry0 maintains as a whole.

Reads are operator-tier like the rest of the registry; writes are admin-tier,
because registering an owner points embry0's credentials and, later, its
spend at every repository under it.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from embry0.api.deps import request_actor, require_admin
from embry0.audit.helpers import emit_audit
from embry0.services import organizations as org_service
from embry0.services.organizations import FORGES, STATUSES, AccessProbe
from embry0.storage.repositories.organizations import OrganizationsRepository

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


class StatusBody(BaseModel):
    status: str = Field(pattern="^(active|paused|removed)$")


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
    owner = body.owner.strip().lower()
    if "/" in owner:
        raise HTTPException(status_code=422, detail="an organization is a forge owner, with no slash")
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
