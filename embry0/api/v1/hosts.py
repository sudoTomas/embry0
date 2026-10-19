"""Hosts API (ADR 0013): the machines that run the code embry0 maintains.

Reads are operator-tier like the rest of the registry. Creating, changing,
removing and importing hosts are admin-tier: they decide where embry0 looks
for logs and deploys. A probe is operator-tier: it changes only what embry0
observed (status and last-seen time), never what a person configured.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, cast

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from embry0.api.deps import request_actor, require_admin
from embry0.api.schemas.repo_path import validate_repo_path
from embry0.audit.actions import AuditAction
from embry0.audit.helpers import emit_audit
from embry0.services import hosts as hosts_service
from embry0.services.credentials.resolver import CredentialMissingError, CredentialUnavailableError
from embry0.services.hosts import LOG_SOURCE_KINDS, SOURCES, STATUSES, TRANSPORTS, Host
from embry0.storage.repositories.hosts import HostNameTakenError, HostsRepository

logger = structlog.get_logger(__name__)
router = APIRouter()

_NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$"
Role = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.-]{0,31}$")]
Tag = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")]
_CREDENTIAL_ID = r"^cred-[0-9a-f]{12}$"


class LogSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["journald", "docker", "loki"]
    selector: str = Field(
        min_length=1,
        max_length=500,
        description="what to read: a systemd unit for journald, a container name for docker, a LogQL selector for loki",
    )


class RepoLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str = Field(description='"group/name", or a GitLab subgroup path like "group/subgroup/project"')
    role: Role = "app"

    @field_validator("repo")
    @classmethod
    def _repo_shape(cls, v: str) -> str:
        return validate_repo_path(v)


def _check_address(transport: str, address: str) -> None:
    if transport == "tailnet":
        if any(c.isspace() for c in address):
            raise ValueError("address must have no spaces")
        return
    try:
        hosts_service.parse_endpoint(address, transport)
    except hosts_service.ProbeError as exc:
        raise ValueError(f"{transport} host: {exc}") from None


class HostCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=_NAME)
    address: str = Field(
        default="",
        max_length=255,
        description="tailnet: an address or MagicDNS name (empty matches by name); ssh: host or host:port; "
        "http: host, host:port or a URL",
    )
    transport: Literal["tailnet", "ssh", "http"]
    roles: list[Role] = Field(default_factory=list, max_length=20)
    log_source: LogSource | None = None
    tags: list[Tag] = Field(default_factory=list, max_length=50)
    repos: list[RepoLink] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def _address_fits_transport(self) -> HostCreate:
        self.address = self.address.strip()
        _check_address(self.transport, self.address)
        return self


class HostPatch(BaseModel):
    """Every field optional; an omitted one is kept. ``log_source: null`` clears it; ``repos`` replaces the links."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, pattern=_NAME)
    address: str | None = Field(default=None, max_length=255)
    transport: Literal["tailnet", "ssh", "http"] | None = None
    roles: list[Role] | None = Field(default=None, max_length=20)
    log_source: LogSource | None = None
    tags: list[Tag] | None = Field(default=None, max_length=50)
    status: Literal["active", "removed"] | None = None
    repos: list[RepoLink] | None = Field(default=None, max_length=200)


class ProbeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_id: str | None = Field(
        default=None, pattern=_CREDENTIAL_ID, description="a tailnet_api_key; absent means the global tailnet binding"
    )


class TailnetImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool = Field(default=False, description="must be true: an import creates and updates host rows")
    credential_id: str | None = Field(
        default=None, pattern=_CREDENTIAL_ID, description="a tailnet_api_key; absent means the global tailnet binding"
    )


def _repo(request: Request) -> HostsRepository:
    repository = getattr(request.app.state, "hosts_repo", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="host storage not initialized")
    return cast("HostsRepository", repository)


async def _host_or_404(request: Request, host_id: str) -> Host:
    host = await _repo(request).get(host_id)
    if host is None:
        raise HTTPException(status_code=404, detail=f"no host {host_id}")
    return host


async def _audit(request: Request, action: AuditAction, details: dict[str, Any]) -> None:
    config = getattr(request.app.state, "config", None)
    await emit_audit(
        getattr(request.app.state, "db", None),
        action,
        actor=request_actor(request),
        details=details,
        audit_log_path=getattr(config, "audit_log_path", None),
    )


async def _tailnet_access(request: Request, credential_id: str | None) -> hosts_service.TailnetAccess:
    resolver = getattr(request.app.state, "credential_resolver", None)
    if resolver is None:
        raise HTTPException(status_code=503, detail="credential registry not initialized")
    try:
        resolved = await resolver.resolve_tailnet(credential_id=credential_id)
    except CredentialMissingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except CredentialUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    tailnet = str((resolved.metadata or {}).get("tailnet") or hosts_service.DEFAULT_TAILNET)
    return hosts_service.TailnetAccess(
        api_key=resolved.secret, tailnet=tailnet, credential_id=resolved.id, label=resolved.label
    )


@router.get("/hosts")
async def list_hosts(
    request: Request,
    source: Literal["manual", "tailnet"] | None = None,
    include_removed: bool = False,
) -> dict[str, Any]:
    hosts = await _repo(request).list_all(source=source, include_removed=include_removed)
    return {
        "hosts": [h.to_dict() for h in hosts],
        "transports": list(TRANSPORTS),
        "sources": list(SOURCES),
        "statuses": list(STATUSES),
        "log_source_kinds": list(LOG_SOURCE_KINDS),
    }


@router.post("/hosts", status_code=201, dependencies=[require_admin])
async def create_host(body: HostCreate, request: Request) -> dict[str, Any]:
    """Add a host by hand. A removed host with the same name comes back with these values; any other is a 409."""
    repository = _repo(request)
    try:
        host = await repository.create(
            name=body.name,
            address=body.address,
            transport=body.transport,
            roles=list(body.roles),
            log_source=body.log_source.model_dump() if body.log_source else None,
            tags=list(body.tags),
        )
    except HostNameTakenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if body.repos:
        await repository.replace_repos(host.id, [(link.repo, link.role) for link in body.repos])
        host = await repository.get(host.id) or host
    await _audit(request, "host.created", {"host_id": host.id, "name": host.name, "transport": host.transport})
    return host.to_dict()


# Literal paths first: /hosts/import/tailnet must never be read as a host id.


@router.post("/hosts/import/tailnet", dependencies=[require_admin])
async def import_tailnet_hosts(body: TailnetImportBody, request: Request) -> dict[str, Any]:
    """Create or update one host per device of the tailnet. Idempotent; needs ``confirm: true``."""
    if not body.confirm:
        raise HTTPException(
            status_code=422,
            detail="set confirm=true to import: an import creates and updates host rows from the tailnet's devices",
        )
    access = await _tailnet_access(request, body.credential_id)
    try:
        devices = await hosts_service.list_tailnet_devices(access)
    except hosts_service.TailnetAPIError as exc:
        raise HTTPException(status_code=502, detail=f"tailnet API: {exc.reason}") from None
    results = await hosts_service.import_tailnet(_repo(request), devices)
    counts = {outcome: 0 for outcome in ("created", "updated", "unchanged", "skipped")}
    for result in results:
        counts[result["outcome"]] = counts.get(result["outcome"], 0) + 1
    await _audit(
        request,
        "host.imported",
        {"tailnet": access.tailnet, "credential_id": access.credential_id, "counts": counts},
    )
    return {"tailnet": access.tailnet, "credential_id": access.credential_id, "results": results, "counts": counts}


@router.get("/hosts/{host_id}")
async def get_host(host_id: str, request: Request) -> dict[str, Any]:
    return (await _host_or_404(request, host_id)).to_dict()


@router.patch("/hosts/{host_id}", dependencies=[require_admin])
async def update_host(host_id: str, body: HostPatch, request: Request) -> dict[str, Any]:
    repository = _repo(request)
    host = await _host_or_404(request, host_id)
    sent = body.model_dump(exclude_unset=True)
    repos = sent.pop("repos", None)
    if "address" in sent and sent["address"] is not None:
        sent["address"] = sent["address"].strip()
    for key in ("name", "address", "transport", "roles", "tags", "status"):
        if key in sent and sent[key] is None:
            raise HTTPException(status_code=422, detail=f"{key} cannot be null")
    transport = sent.get("transport", host.transport)
    address = sent.get("address", host.address)
    if "transport" in sent or "address" in sent:
        try:
            _check_address(transport, address)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    if repos is not None and sent.get("status", host.status) == "removed":
        raise HTTPException(
            status_code=409, detail=f"host {host_id} is removed; set status active to link repositories"
        )
    try:
        updated = await repository.update(host_id, sent)
    except HostNameTakenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if updated is None:
        raise HTTPException(status_code=404, detail=f"no host {host_id}")
    if repos is not None:
        await repository.replace_repos(host_id, [(link["repo"], link["role"]) for link in repos])
        updated = await repository.get(host_id) or updated
    fields = sorted([*sent, *(["repos"] if repos is not None else [])])
    await _audit(request, "host.updated", {"host_id": host_id, "fields": fields})
    return updated.to_dict()


@router.delete("/hosts/{host_id}", status_code=204, response_model=None, dependencies=[require_admin])
async def remove_host(host_id: str, request: Request) -> None:
    """Mark the host removed and drop its repository links. The row stays, so a tailnet import skips it."""
    if await _repo(request).remove(host_id) is None:
        raise HTTPException(status_code=404, detail=f"no host {host_id}")
    await _audit(request, "host.removed", {"host_id": host_id})


@router.post("/hosts/{host_id}/probe")
async def probe_host(host_id: str, request: Request, body: ProbeBody | None = None) -> dict[str, Any]:
    """Is the host reachable over its transport? Writes the host's status and last-seen time."""
    host = await _host_or_404(request, host_id)
    if host.status == "removed":
        raise HTTPException(status_code=409, detail=f"host {host_id} is removed")
    access = None
    if host.transport == "tailnet":
        access = await _tailnet_access(request, body.credential_id if body else None)
    try:
        result = await hosts_service.probe_host(host, tailnet=access)
    except hosts_service.ProbeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except hosts_service.TailnetAPIError as exc:
        raise HTTPException(status_code=502, detail=f"tailnet API: {exc.reason}") from None
    updated = await _repo(request).record_probe(host_id, reachable=result.reachable, last_seen_at=result.last_seen_at)
    return {"probe": result.to_dict(), "host": (updated or host).to_dict()}
