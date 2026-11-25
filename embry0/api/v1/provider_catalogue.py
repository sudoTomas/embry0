"""The provider catalogue (#173, ADR 0031): ``/api/v1/config/providers/catalog``.

The deployment's overlay of the catalogue embry0 ships, its revisions and
undo. Any key reads it: it holds no address an operator gives and no key's
source, only what every job runs on. Only an admin key without a repository
allowlist writes it (``deployment_writes``). A write is refused naming every
problem (422, never a value), past 256 KiB (413), or when ``If-Match`` names
another generation (409).
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, nullcontext
from typing import Any, Literal, cast

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response

from embry0.agents.catalogue import CatalogueInvalid, seed
from embry0.api.actor_visibility import deployment_actor_shown
from embry0.api.deps import request_actor, require_admin
from embry0.api.document_requests import if_match_versions, read_capped_body
from embry0.api.repo_allowlist import deployment_writes
from embry0.api.served import ServedRoute
from embry0.audit.actions import AuditAction
from embry0.audit.helpers import emit_audit
from embry0.services.provider_catalogue_store import (
    MAX_TEXT_BYTES,
    WHY_NOT,
    CatalogueStore,
    CatalogueTooLarge,
    GenerationMismatch,
    NotUndoable,
    RevisionNotFound,
    SaveResult,
)

router = APIRouter(route_class=ServedRoute)

_WRITE = "change the provider catalogue"
_TOO_LARGE = f"the provider catalogue is larger than {MAX_TEXT_BYTES} bytes"


def _store(request: Request) -> CatalogueStore:
    store = getattr(request.app.state, "provider_catalogue", None)
    if store is None:
        raise HTTPException(status_code=503, detail="provider catalogue storage not initialized")
    return cast(CatalogueStore, store)


def catalogue_held(request: Request) -> AbstractAsyncContextManager[Any]:
    """For a write that names a model (a tier, the fallback order, an agent definition): check and store it inside
    this block, against the stored catalogue, with its row held (``CatalogueStore.held``). Without a store (an app
    built without one), the installed catalogue, as before."""
    store = getattr(request.app.state, "provider_catalogue", None)
    return cast(CatalogueStore, store).held() if store is not None else nullcontext()


def _text(body: bytes) -> str:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=422, detail=[{"loc": [], "type": "encoding", "msg": "the catalogue must be UTF-8 text"}]
        ) from None


async def _audit(request: Request, action: AuditAction, details: dict[str, Any]) -> None:
    config = getattr(request.app.state, "config", None)
    await emit_audit(
        getattr(request.app.state, "db", None),
        action,
        actor=request_actor(request),
        details=details,
        audit_log_path=getattr(config, "audit_log_path", None),
    )


def _refusals(exc: Exception) -> HTTPException:
    if isinstance(exc, CatalogueTooLarge):
        return HTTPException(status_code=413, detail=str(exc))
    if isinstance(exc, CatalogueInvalid):
        return HTTPException(status_code=422, detail=[problem.to_dict() for problem in exc.problems])
    if isinstance(exc, GenerationMismatch):
        return HTTPException(
            status_code=409,
            detail=f"The catalogue changed since you read it (now generation {exc.current}). "
            "Read it again and apply your change to the new version.",
        )
    raise exc


def _saved(result: SaveResult) -> dict[str, Any]:
    return {
        "generation": result.generation,
        "revision_id": result.revision_id,
        "changed": result.changed,
        "diff": result.diff,
    }


@router.get("/config/providers/catalog", response_model=None)
async def get_provider_catalogue(
    request: Request, response: Response, format: Literal["json", "yaml"] = "json"
) -> dict[str, Any] | Response:
    """The deployment's catalogue overlay and the catalogue in force.

    ``yaml_text`` is the overlay as written (empty: the shipped catalogue as it
    is), ``data`` its values, ``generation`` the version to name in a write's
    ``If-Match`` (also the ``ETag``). ``catalogue`` is what this process runs
    on: the shipped catalogue with the overlay merged in. ``in_force`` says how
    much of the overlay that is: ``whole``; ``in_part``, every part this
    release takes, the parts it refuses named in ``refused`` (a release that
    took out a model or a provider the overlay names, at the start); or
    ``earlier``, the overlay of ``in_force_generation``, when a later one this
    release refuses part of was written while the process ran. ``shipped``
    is the catalogue this release ships, so a client can tell what the
    overlay changes and put a field back. A price left out
    leaves what it priced unpriced (``withheld`` in ``refused``). ``notes``
    names each model a release dropped that the overlay keeps as the
    deployment's own, at its own prices. ``?format=yaml`` answers the
    overlay's text alone. Any key."""
    store = _store(request)
    installed = await store.refresh()
    stored = await store.document()
    etag = f'"{stored.generation}"'
    if format == "yaml":
        return Response(content=stored.yaml_text, media_type="application/yaml", headers={"ETag": etag})
    response.headers["ETag"] = etag
    shown = await deployment_actor_shown(request)
    return {
        "yaml_text": stored.yaml_text,
        "data": stored.data,
        "generation": stored.generation,
        "updated_by": shown(stored.updated_by) if stored.updated_by else stored.updated_by,
        "updated_at": stored.updated_at.isoformat() if stored.updated_at is not None else None,
        "catalogue": installed.document.model_dump(mode="json", by_alias=True),
        "shipped": seed().document.model_dump(mode="json", by_alias=True),
        "in_force": store.in_force,
        "in_force_generation": store.in_force_generation,
        "refused": [problem.to_dict() for problem in store.refused],
        "notes": [problem.to_dict() for problem in store.notes],
    }


@router.get("/config/providers/catalog/shipped", response_model=None)
async def get_shipped_provider_catalogue() -> Response:
    """The catalogue embry0 ships with this release (``embry0/agents/providers.yaml``), as YAML. Any key."""
    from embry0.agents.catalogue import SEED_FILE

    return Response(content=SEED_FILE.read_text(encoding="utf-8"), media_type="application/yaml")


@router.put("/config/providers/catalog", dependencies=[require_admin, deployment_writes(_WRITE)])
async def put_provider_catalogue(
    request: Request,
    dry_run: bool = False,
    reason: str = Query(default="", max_length=500),
    source: Literal["person", "file"] = "person",
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> dict[str, Any]:
    """Replace the catalogue overlay with the body, YAML (stored as written, comments included).

    An empty body returns to the shipped catalogue. A provider embry0 ships
    may be turned on or off, its factors, default price and models changed;
    a model's ``request_extras`` take only the fields that change neither
    which model answers nor what it costs. Refused, with nothing stored: 422
    naming every problem (the text's, the overlay's, the catalogue it makes,
    and each tier, fallback, agent definition or ``background_models`` value
    whose model the change would take out, leave unpriced or turn off), 413
    past 256 KiB, 409 when ``If-Match`` names another generation. With
    ``dry_run`` nothing is stored and the answer says what would change. A
    write is a revision, which ``POST .../revisions/{id}/undo`` reverses, and
    is in force at once. Admin, no repository allowlist."""
    store = _store(request)
    expected = if_match_versions(if_match)
    body = await read_capped_body(request, MAX_TEXT_BYTES, _TOO_LARGE)
    text = _text(body)
    try:
        if dry_run:
            preview = await store.preview(text, if_generation=expected)
            return {"dry_run": True, "generation": preview.generation, "changed": preview.changed, "diff": preview.diff}
        result = await store.save(
            text, actor=request_actor(request), source=source, reason=reason, if_generation=expected
        )
    except (CatalogueTooLarge, CatalogueInvalid, GenerationMismatch) as exc:
        raise _refusals(exc) from None
    if result.revision_id is not None:
        await _audit(
            request,
            "provider_catalogue.updated",
            {"generation": result.generation, "revision_id": result.revision_id, "source": source, "reason": reason},
        )
    return _saved(result)


def _revision_entry(row: dict[str, Any], newest: int | None, current_text: str, shown: Any) -> dict[str, Any]:
    after = row.get("after") or {}
    undoable = row["id"] == newest and (after.get("yaml_text") if isinstance(after, dict) else None) == current_text
    return {
        "id": row["id"],
        "actor": shown(row["actor"]) if row["actor"] else row["actor"],
        "source": row["source"],
        "reason": row["reason"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") is not None else None,
        "undoable": undoable,
        "why_not": None if undoable else WHY_NOT,
    }


@router.get("/config/providers/catalog/revisions")
async def list_provider_catalogue_revisions(request: Request, limit: int = Query(50, ge=1, le=100)) -> dict[str, Any]:
    """The catalogue's changes, newest first; only the newest can be undone, while it is still what is stored."""
    store = _store(request)
    rows = await store.revisions(limit=limit)
    current_text = (await store.document()).yaml_text if rows else ""
    newest = rows[0]["id"] if rows else None
    shown = await deployment_actor_shown(request)
    return {"revisions": [_revision_entry(row, newest, current_text, shown) for row in rows]}


@router.get("/config/providers/catalog/revisions/{revision_id}")
async def get_provider_catalogue_revision(revision_id: int, request: Request) -> dict[str, Any]:
    """One change of the catalogue: its text before and after."""
    store = _store(request)
    revision = await store.revision(revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="That change was not found.")
    newest = await store.revisions(limit=1)
    current_text = (await store.document()).yaml_text
    entry = _revision_entry(
        revision, newest[0]["id"] if newest else None, current_text, await deployment_actor_shown(request)
    )
    before = revision.get("before") or {}
    after = revision.get("after") or {}
    return {**entry, "before_text": before.get("yaml_text"), "after_text": after.get("yaml_text")}


@router.post(
    "/config/providers/catalog/revisions/{revision_id}/undo", dependencies=[require_admin, deployment_writes(_WRITE)]
)
async def undo_provider_catalogue_revision(revision_id: int, request: Request) -> dict[str, Any]:
    """Put back what the newest change of the catalogue replaced: 409 for any other change, 422 when that earlier
    text no longer makes a catalogue. Recorded with source ``restore``. Admin, no repository allowlist."""
    store = _store(request)
    try:
        result = await store.undo(revision_id, actor=request_actor(request))
    except RevisionNotFound:
        raise HTTPException(status_code=404, detail="That change was not found.") from None
    except NotUndoable:
        raise HTTPException(status_code=409, detail=WHY_NOT) from None
    except (CatalogueTooLarge, CatalogueInvalid, GenerationMismatch) as exc:
        raise _refusals(exc) from None
    await _audit(
        request,
        "provider_catalogue.change_undone",
        {"revision_id": revision_id, "new_revision_id": result.revision_id, "generation": result.generation},
    )
    return _saved(result)
