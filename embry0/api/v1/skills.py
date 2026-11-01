"""Skills: catalog, sources, inline skills (agent identity and skills spec 3.4). Writes are admin."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, cast

import structlog
from asyncpg.exceptions import UniqueViolationError
from fastapi import APIRouter, HTTPException, Request

from embry0.agents.skills import normalize_delta
from embry0.api.deps import require_admin
from embry0.api.repo_allowlist import deployment_writes
from embry0.api.schemas import InlineSkillRequest, SkillSourceCreateRequest, SkillSourcePatchRequest
from embry0.api.v1._audit import audit
from embry0.skills.sync import SkillSourceSync, SkillSyncBusy
from embry0.storage.repositories.skills import InlineSkillsRepository, SkillSourceRefused, SkillSourcesRepository
from embry0.workflows.issue_to_pr.route_plan import node_config

logger = structlog.get_logger(__name__)

# Skills reach every repository's jobs: not a key with a repository allowlist's to change.
router = APIRouter(dependencies=[deployment_writes("change the deployment's skills")])

# The source the inline skills are published under.
_INLINE_SOURCE = "local"


def _sources(request: Request) -> SkillSourcesRepository:
    repo = getattr(request.app.state, "skill_sources_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="skill storage not initialized")
    return cast("SkillSourcesRepository", repo)


def _inline(request: Request) -> InlineSkillsRepository:
    repo = getattr(request.app.state, "inline_skills_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="skill storage not initialized")
    return cast("InlineSkillsRepository", repo)


async def _run_sync(sync: SkillSourceSync, source_id: str) -> None:
    """A sync records its own failures on the row; a concurrent one is simply already doing the work."""
    try:
        await sync.sync(source_id)
    except SkillSyncBusy:  # defensive: sync() coalesces a second request and does not raise this today
        logger.warning("skill_source_sync_busy", source_id=source_id)
    except Exception:
        logger.exception("skill_sync_crashed", source_id=source_id)


def _schedule_sync(request: Request, source_id: str) -> None:
    sync = getattr(request.app.state, "skill_sync", None)
    if sync is None:
        return
    task = asyncio.create_task(_run_sync(sync, source_id))
    tasks = getattr(request.app.state, "background_tasks", None)
    if tasks is not None:
        tasks.add(task)
        task.add_done_callback(tasks.discard)


def _plain(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}


def _serialize(row: dict[str, Any], request: Request) -> dict[str, Any]:
    sync = getattr(request.app.state, "skill_sync", None)
    out = _plain(row)
    out["syncing"] = bool(sync is not None and sync.is_syncing(row["id"]))
    return out


async def _references(request: Request, predicate: Callable[[str], bool]) -> dict[str, list[str]]:
    """The agents whose skills, and the templates whose nodes' added skills, match ``predicate``."""
    found: dict[str, list[str]] = {}
    agents_repo = getattr(request.app.state, "agent_defs_repo", None)
    if agents_repo is not None:
        agents = sorted(
            a["type"] for a in await agents_repo.list_all() if any(predicate(s) for s in a.get("skills") or [])
        )
        if agents:
            found["agents"] = agents
    templates_repo = getattr(request.app.state, "templates_repo", None)
    if templates_repo is not None:
        templates: list[str] = []
        for summary in await templates_repo.list_all():
            template = await templates_repo.get(summary["id"])
            nodes = ((template or {}).get("graph_definition") or {}).get("nodes") or []
            for node in nodes:
                try:
                    added = normalize_delta(node_config(node).get("skills"))["add"]
                except ValueError:
                    continue
                if any(predicate(s) for s in added):
                    templates.append(str(summary.get("name") or summary["id"]))
                    break
        if templates:
            found["templates"] = sorted(templates)
    return found


@router.get("/skills")
async def list_skills(request: Request) -> list[dict[str, Any]]:
    return await _sources(request).catalog()


@router.get("/skills/sources")
async def list_sources(request: Request) -> list[dict[str, Any]]:
    return [_serialize(r, request) for r in await _sources(request).list_all()]


@router.get("/skills/sources/{source_id}")
async def get_source(source_id: str, request: Request) -> dict[str, Any]:
    row = await _sources(request).get(source_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no skill source {source_id!r}")
    return _serialize(row, request)


@router.post("/skills/sources", status_code=201, dependencies=[require_admin])
async def create_source(body: SkillSourceCreateRequest, request: Request) -> dict[str, Any]:
    try:
        row = await _sources(request).create(
            source_id=body.id, kind=body.kind, url=body.url, ref=body.ref, enabled=body.enabled
        )
    except SkillSourceRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail=f"skill source {body.id!r} already exists") from exc
    await audit(request, "skill_source.created", source_id=body.id, kind=body.kind, url=body.url, ref=body.ref)
    _schedule_sync(request, body.id)
    return _serialize(row, request)


@router.patch("/skills/sources/{source_id}", dependencies=[require_admin])
async def patch_source(source_id: str, body: SkillSourcePatchRequest, request: Request) -> dict[str, Any]:
    repo = _sources(request)
    row = await repo.get(source_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no skill source {source_id!r}")
    # exclude_none: neither column is nullable, so a sent null means "leave it".
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if "ref" in fields:
        if row["is_builtin"]:
            raise HTTPException(
                status_code=403, detail="built-in sources accept enabled only; their ref is pinned in code"
            )
        if row["kind"] != "git":
            raise HTTPException(status_code=422, detail=f"a {row['kind']} source has no ref")
    if not fields:
        return _serialize(row, request)
    try:
        updated = await repo.update(source_id, **fields)
    except SkillSourceRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await audit(request, "skill_source.updated", source_id=source_id, fields=sorted(fields))
    if "ref" in fields:
        _schedule_sync(request, source_id)
    return _serialize(updated, request)


@router.post("/skills/sources/{source_id}/sync", status_code=202, dependencies=[require_admin])
async def sync_source(source_id: str, request: Request) -> dict[str, Any]:
    sync = getattr(request.app.state, "skill_sync", None)
    if sync is None:
        raise HTTPException(status_code=503, detail="skill sync not initialized")
    if await _sources(request).get(source_id) is None:
        raise HTTPException(status_code=404, detail=f"no skill source {source_id!r}")
    if sync.is_syncing(source_id):
        raise HTTPException(status_code=409, detail=f"a sync of {source_id!r} is already running")
    await audit(request, "skill_source.synced", source_id=source_id)
    _schedule_sync(request, source_id)
    return {"id": source_id, "status": "syncing"}


@router.delete("/skills/sources/{source_id}", dependencies=[require_admin])
async def delete_source(source_id: str, request: Request) -> dict[str, Any]:
    repo = _sources(request)
    row = await repo.get(source_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no skill source {source_id!r}")
    # Before the reference scan: a 409 would tell the caller to remove skills, and that still ends in 403.
    if row["is_builtin"]:
        raise HTTPException(status_code=403, detail=f"cannot delete built-in skill source {source_id!r}")
    sync = getattr(request.app.state, "skill_sync", None)
    if sync is not None and sync.is_syncing(source_id):
        raise HTTPException(
            status_code=409, detail=f"a sync of {source_id!r} is running; delete it once it has finished"
        )
    prefix = (row["plugin_name"] or row["id"]) + ":"
    refs = await _references(request, lambda s: s.startswith(prefix))
    if refs:
        raise HTTPException(
            status_code=409,
            detail={"message": f"skill source {source_id!r} is referenced; remove its skills first", **refs},
        )
    try:
        await repo.delete(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=403 if "built-in" in str(exc) else 404, detail=str(exc)) from None
    store = getattr(request.app.state, "skill_store", None)
    if store is not None:
        store.remove_source(source_id)
    await audit(request, "skill_source.deleted", source_id=source_id)
    return {"id": source_id, "status": "deleted"}


@router.get("/skills/inline")
async def list_inline(request: Request) -> list[dict[str, Any]]:
    return [_plain(r) for r in await _inline(request).list_all()]


@router.put("/skills/inline/{name}", dependencies=[require_admin])
async def put_inline(name: str, body: InlineSkillRequest, request: Request) -> dict[str, Any]:
    try:
        row = await _inline(request).upsert(name=name, description=body.description, body=body.body)
    except SkillSourceRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await audit(request, "inline_skill.written", name=name)
    _schedule_sync(request, _INLINE_SOURCE)
    return _plain(row)


@router.delete("/skills/inline/{name}", dependencies=[require_admin])
async def delete_inline(name: str, request: Request) -> dict[str, Any]:
    refs = await _references(request, lambda s: s == f"{_INLINE_SOURCE}:{name}")
    if refs:
        raise HTTPException(status_code=409, detail={"message": f"inline skill {name!r} is referenced", **refs})
    try:
        await _inline(request).delete(name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await audit(request, "inline_skill.deleted", name=name)
    _schedule_sync(request, _INLINE_SOURCE)
    return {"name": name, "status": "deleted"}
