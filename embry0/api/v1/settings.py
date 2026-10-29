"""The settings document API (spec 2026-10-28): operator policy, one section per feature.

GET reads the stored document, the effective value of every setting with
where it came from, the JSON Schema, and the revisions. PUT replaces the
whole document, YAML or a JSON object, and answers 422 listing every bad key
in FastAPI's own error shape (``loc``, ``type``, ``msg``, never the rejected
value). ``?dry_run=1`` answers what would change and stores nothing. Undo
puts back what the newest change replaced.

**Layers** (spec 2026-10-29). ``?owner=acme`` or ``?repo=acme/widgets`` on
the document, its revisions and the effective view names an organization's
or a repository's layer instead of the deployment's document: the same
shape, holding only the keys its sections let that layer set, each a 422
``not_overridable`` otherwise. Revision ids are global, so a revision and
its undo need no layer named.

Reads take any key: no document holds secrets. Who changed it is shown as
``GET /audit`` shows actors (:mod:`embry0.api.actor_visibility`): an admin
sees everyone, anyone else only themselves. Writes take the admin tier. The
deployment's document and an organization's layer govern many repositories,
so they need a key without a repository allowlist; a repository's layer
takes a key whose allowlist covers it. A body past 256 KiB is refused before
it is read in full, with or without a ``Content-Length``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal, cast

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response

from embry0.api.actor_visibility import actor_shown
from embry0.api.deps import request_actor, require_admin
from embry0.api.document_requests import if_match_versions, read_capped_body
from embry0.api.repo_allowlist import enforce_repo_allowlist
from embry0.audit.actions import AuditAction
from embry0.audit.helpers import emit_audit
from embry0.settings.document import (
    MAX_TEXT_BYTES,
    DocumentInvalid,
    DocumentTooLarge,
    changes,
    parse_body,
    unknown_parts,
)
from embry0.settings.effective import effective_view, iso
from embry0.settings.env import env_name
from embry0.settings.registry import MANAGED_BY_KEY, Catalog
from embry0.settings.scope import Scope, ScopeError, current, owner_of
from embry0.settings.store import (
    WHY_NOT,
    GenerationMismatch,
    NotUndoable,
    RevisionInvalid,
    RevisionNotFound,
    SettingsStore,
)

router = APIRouter()

_WRITE_ACTION = "change the settings document"
_BOTH = "Name one layer: owner or repo, not both."
_OWNER = Query(default=None, max_length=200, description="an organization's layer, by its owner")
_REPO = Query(default=None, max_length=400, description="a repository's layer, as owner/name")


def _store(request: Request) -> SettingsStore:
    store = getattr(request.app.state, "settings", None)
    if store is None:
        raise HTTPException(status_code=503, detail="settings storage not initialized")
    return cast(SettingsStore, store)


def _unprocessable(exc: DocumentInvalid) -> HTTPException:
    return HTTPException(status_code=422, detail=[problem.to_dict() for problem in exc.problems])


def _scope(owner: str | None, repo: str | None) -> Scope:
    """The document a request names: the deployment's, or one organization's or repository's layer."""
    owner, repo = (owner or "").strip() or None, (repo or "").strip() or None
    if owner and repo:
        raise HTTPException(status_code=422, detail=_BOTH)
    try:
        if owner:
            return Scope.organization(owner)
        if repo:
            return Scope.repository(repo)
    except ScopeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return Scope.deployment()


async def _registered(request: Request, scope: Scope) -> bool:
    """Whether the organization or repository a layer belongs to is registered here."""
    state = request.app.state
    if scope.layer == "organization":
        organizations = getattr(state, "organizations_repo", None)
        if organizations is None:
            raise HTTPException(status_code=503, detail="organization storage not initialized")
        return await organizations.get(scope.name) is not None
    if scope.layer == "repository":
        repos = getattr(state, "repos_repo", None)
        if repos is None:
            raise HTTPException(status_code=503, detail="repository storage not initialized")
        for row in await repos.list_all(include_inactive=True):
            try:
                if Scope.repository(str(row["repo"])).name == scope.name:
                    return True
            except ScopeError:
                continue
        return False
    return True


def _not_registered(scope: Scope) -> HTTPException:
    if scope.layer == "organization":
        return HTTPException(status_code=404, detail=f"No organization {scope.name} is registered; register it first.")
    return HTTPException(status_code=404, detail=f"No repository {scope.name} is registered; register it first.")


async def _may_write(request: Request, scope: Scope) -> None:
    """A repository's layer takes a key whose allowlist covers it; every other document an unrestricted key."""
    await enforce_repo_allowlist(request, scope.name if scope.layer == "repository" else None, action=_WRITE_ACTION)


def _settable(catalog: Catalog, scope: Scope) -> dict[str, list[str]]:
    """Per section, the keys the document at ``scope`` may set."""
    return {section.name: sorted(section.settable(scope.layer)) for section in catalog}


def _unknown(catalog: Catalog, scope: Scope, data: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """The stored sections this release does not know, and the stored keys it does not let this document set."""
    sections, keys = unknown_parts(catalog, data)
    extra = list(keys)
    if scope.layer != "deployment":
        for section in catalog:
            raw = data.get(section.name)
            if isinstance(raw, dict):
                allowed = section.settable(scope.layer)
                extra.extend(
                    f"{section.name}.{key}" for key in raw if key in section.model.model_fields and key not in allowed
                )
    return list(sections), sorted(set(extra))


async def _audit(request: Request, action: AuditAction, details: dict[str, Any]) -> None:
    config = getattr(request.app.state, "config", None)
    await emit_audit(
        getattr(request.app.state, "db", None),
        action,
        actor=request_actor(request),
        details=details,
        audit_log_path=getattr(config, "audit_log_path", None),
    )


@router.get("/config/settings", response_model=None)
async def get_settings(
    request: Request,
    response: Response,
    format: Literal["json", "yaml"] = "json",
    owner: str | None = _OWNER,
    repo: str | None = _REPO,
) -> dict[str, Any] | Response:
    """A stored settings document: its text, its values and its generation.

    The deployment's, or with ``owner`` or ``repo`` that layer's (404 when
    no layer is stored and the organization or repository is not
    registered). ``?format=yaml`` answers the text alone. The ``ETag`` is the
    generation, for ``If-Match`` on a later PUT; a layer nobody wrote is
    generation 0. ``managed_by`` is ``"file"`` when the document says it is
    kept in a file, else null: each layer says so for itself.
    """
    store = _store(request)
    scope = _scope(owner, repo)
    row = await store.document(scope)
    if scope.layer != "deployment" and int(row["generation"]) == 0 and not await _registered(request, scope):
        raise _not_registered(scope)
    etag = f'"{row["generation"]}"'
    if format == "yaml":
        return Response(content=row["yaml_text"], media_type="application/yaml", headers={"ETag": etag})
    response.headers["ETag"] = etag
    shown = await actor_shown(request)
    unknown_sections, unknown_keys = _unknown(store.catalog, scope, row["data"])
    return {
        "scope": scope.view(),
        "yaml_text": row["yaml_text"],
        "data": row["data"],
        "schema_version": row["schema_version"],
        "generation": row["generation"],
        # Who wrote it, as /audit shows actors: an admin sees anyone, anyone else only themselves.
        "updated_by": shown(row["updated_by"]) if row["updated_by"] else row["updated_by"],
        "updated_at": iso(row["updated_at"]),
        "sections": list(store.catalog.names),
        "settable": _settable(store.catalog, scope),
        "unknown_sections": unknown_sections,
        "unknown_keys": unknown_keys,
        # "file" when the document is kept in a file (`embry0 settings apply`): the dashboard only shows it.
        "managed_by": row["data"].get(MANAGED_BY_KEY),
    }


@router.put("/config/settings", dependencies=[require_admin])
async def put_settings(
    request: Request,
    dry_run: bool = False,
    reason: str = Query(default="", max_length=500),
    source: Literal["person", "file"] = "person",
    if_match: str | None = Header(default=None, alias="If-Match"),
    owner: str | None = _OWNER,
    repo: str | None = _REPO,
) -> dict[str, Any]:
    """Replace a settings document: the deployment's, or with ``owner`` or ``repo`` that layer.

    The body is YAML (stored as written, comments included) or a JSON object
    (``Content-Type: application/json``, stored as the YAML it dumps to). An
    empty body puts every setting back to its default, or for a layer to what
    it inherits. 422 lists every problem, a key the layer may not set
    included; 404 for a layer of an organization or repository that is not
    registered; 409 when ``If-Match`` names no generation that is the stored
    one (``*`` matches any); 413 past 256 KiB. With ``dry_run`` nothing is
    stored, ``If-Match`` is checked the same way, and the answer says what
    would change. ``layers_refused`` lists the values of the layers under the
    document that the change makes read as inherited.
    """
    scope = _scope(owner, repo)
    await _may_write(request, scope)
    if scope.layer != "deployment" and not await _registered(request, scope):
        raise _not_registered(scope)
    store = _store(request)
    expected = if_match_versions(if_match)
    body = await read_capped_body(
        request, MAX_TEXT_BYTES, f"the settings document is larger than {MAX_TEXT_BYTES} bytes"
    )
    try:
        text, values = parse_body(body, request.headers.get("content-type", ""))
        if dry_run:
            preview = await store.preview(text, values, if_generation=expected, scope=scope)
            return {
                "scope": scope.view(),
                "would_change": preview.would_change,
                "text_diff": preview.text_diff,
                "changes": preview.changes,
                "unknown_sections": list(preview.unknown_sections),
                "unknown_keys": list(preview.unknown_keys),
                "layers_refused": preview.layers_refused,
                "layers_refused_total": preview.layers_refused_total,
            }
        result = await store.save(
            text,
            values,
            actor=request_actor(request),
            source=source,
            reason=reason,
            if_generation=expected,
            scope=scope,
        )
    except DocumentTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from None
    except DocumentInvalid as exc:
        raise _unprocessable(exc) from None
    except GenerationMismatch as exc:
        raise HTTPException(
            status_code=409,
            detail=f"The settings changed since you read them (now generation {exc.current}). "
            "Read them again and apply your change to the new version.",
        ) from None
    if result.revision_id is not None:
        await _audit(
            request,
            "settings.updated",
            {
                "scope": str(scope),
                "revision_id": result.revision_id,
                "generation": result.generation,
                "keys": [change["key"] for change in result.changes],
                "source": source,
                "reason": reason,
            },
        )
    return {
        "scope": scope.view(),
        "generation": result.generation,
        "revision_id": result.revision_id,
        "changes": result.changes,
        "unknown_sections": list(result.unknown_sections),
        "unknown_keys": list(result.unknown_keys),
        "layers_refused": result.layers_refused,
        "layers_refused_total": result.layers_refused_total,
    }


@router.get("/config/settings/effective")
async def get_effective_settings(
    request: Request, owner: str | None = _OWNER, repo: str | None = _REPO
) -> dict[str, Any]:
    """Every setting of every section: the value loops read, its default, and where it came from.

    With ``owner``, as that organization reads it; with ``repo``, as that
    repository does, its organization's layer included (an ``owner`` given
    beside it must be the repository's). A layer's view adds, per key, the
    value it inherits and what each document in the view sets.
    """
    store = _store(request)
    owner, repo = (owner or "").strip() or None, (repo or "").strip() or None
    org_scope: Scope | None = None
    repo_scope: Scope | None = None
    try:
        if repo:
            repo_scope = Scope.repository(repo)
            org_scope = Scope.organization(owner_of(repo_scope.name))
            if owner and Scope.organization(owner).name != org_scope.name:
                raise HTTPException(
                    status_code=422, detail=f"{repo_scope.name} is not a repository of {owner.lower()}."
                )
        elif owner:
            org_scope = Scope.organization(owner)
    except ScopeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    snapshot = await store.snapshot()
    revisions: dict[str, list[dict[str, Any]]] = {"deployment": await store.revisions(limit=100)}
    for scope in (org_scope, repo_scope):
        if scope is not None:
            revisions[str(scope)] = await store.revisions(limit=100, scope=scope)
    view = effective_view(
        store.catalog,
        snapshot,
        revisions,
        request.app.state.config,
        owner=org_scope.name if org_scope is not None and repo_scope is None else None,
        repo=repo_scope.name if repo_scope is not None else None,
    )
    shown = await actor_shown(request)
    for section in view["sections"]:
        for key in section["keys"]:
            key["set_by"] = shown(key["set_by"])
            for layer in key.get("layers") or ():
                layer["set_by"] = shown(layer["set_by"])
    return view


@router.get("/config/settings/schema")
async def get_settings_schema(request: Request) -> dict[str, Any]:
    """The JSON Schema of the whole document, with each section's summary and overridable keys.

    ``x-embry0-sections.<name>.env`` names the environment variable each key
    was imported from.
    """
    store = _store(request)
    schema = store.catalog.json_schema()
    extras = schema.get("x-embry0-sections", {})
    for section in store.catalog:
        extras.setdefault(section.name, {})["env"] = {
            key: env_name(imported.attr) for key, imported in section.imports().items()
        }
    return schema


@router.get("/config/settings/layers")
async def list_settings_layers(request: Request) -> dict[str, Any]:
    """Every stored organization and repository layer: which, its generation, who last wrote it and the keys it sets.

    A layer cleared with an empty document is listed with no keys.
    """
    store = _store(request)
    shown = await actor_shown(request)
    out: list[dict[str, Any]] = []
    for row in await store.layers():
        try:
            stored = Scope.parse(str(row["scope"]))
        except ScopeError:
            continue
        scope = current(stored)
        data = row["data"]
        keys = sorted(f"{name}.{key}" for name, values in data.items() if isinstance(values, dict) for key in values)
        out.append(
            {
                **scope.view(),
                # A repository renamed since its layer was written: the old slug the layer is kept under.
                "stored_as": str(stored) if str(stored) != str(scope) else None,
                "generation": row["generation"],
                "updated_by": shown(row["updated_by"]) if row["updated_by"] else row["updated_by"],
                "updated_at": iso(row["updated_at"]),
                "keys": keys,
            }
        )
    return {"layers": out}


def _what(revision: dict[str, Any]) -> str:
    before = (revision.get("before") or {}).get("data") or {}
    after = (revision.get("after") or {}).get("data") or {}
    keys = [change.path for change in changes(before, after)]
    if not keys:
        return "Comments or layout changed; no setting changed"
    verb = "Imported from the environment" if revision.get("source") == "bootstrap" else "Changed"
    return f"{verb}: {', '.join(keys)}"


def _revision_scope(revision: Mapping[str, Any]) -> Scope:
    """The document a revision changed, a repository under its current slug."""
    try:
        return current(Scope.parse(str(revision.get("scope") or "deployment")))
    except ScopeError:
        raise HTTPException(status_code=404, detail="That change was not found.") from None


def _revision_entry(
    revision: dict[str, Any], newest_id: int | None, current_text: str, shown: Callable[[str | None], str | None]
) -> dict[str, Any]:
    undoable = revision["id"] == newest_id and (revision.get("after") or {}).get("yaml_text") == current_text
    return {
        "id": revision["id"],
        "scope": _revision_scope(revision).view(),
        "what": _what(revision),
        "actor": shown(revision["actor"]),
        "source": revision["source"],
        "reason": revision["reason"],
        "created_at": iso(revision["created_at"]),
        "undoable": undoable,
        "why_not": None if undoable else WHY_NOT,
    }


@router.get("/config/settings/revisions")
async def list_settings_revisions(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    owner: str | None = _OWNER,
    repo: str | None = _REPO,
) -> dict[str, Any]:
    """A document's changes, newest first; only the newest can be undone, while it is still what is stored."""
    store = _store(request)
    scope = _scope(owner, repo)
    rows = await store.revisions(limit=limit, scope=scope)
    current = (await store.document(scope))["yaml_text"]
    newest = rows[0]["id"] if rows else None
    shown = await actor_shown(request)
    return {"revisions": [_revision_entry(row, newest, current, shown) for row in rows]}


@router.get("/config/settings/revisions/{revision_id}")
async def get_settings_revision(revision_id: int, request: Request) -> dict[str, Any]:
    """One change, of whichever document it changed (``scope``): its text before and after, and each key it changed."""
    store = _store(request)
    revision = await store.revision(revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="That change was not found.")
    scope = _revision_scope(revision)
    newest = await store.revisions(limit=1, scope=scope)
    current = (await store.document(scope))["yaml_text"]
    entry = _revision_entry(revision, newest[0]["id"] if newest else None, current, await actor_shown(request))
    before = revision.get("before") or {}
    after = revision.get("after") or {}
    return {
        **entry,
        "before_text": before.get("yaml_text"),
        "after_text": after.get("yaml_text"),
        "changes": [
            {"key": c.path, "before": c.before, "after": c.after, "set_before": c.set_before, "set_after": c.set_after}
            for c in changes(before.get("data") or {}, after.get("data") or {})
        ],
    }


@router.post("/config/settings/revisions/{revision_id}/undo", dependencies=[require_admin])
async def undo_settings_revision(revision_id: int, request: Request) -> dict[str, Any]:
    """Put back what the newest change of a document replaced. 409 for any other change; recorded with source ``restore``.

    Authorized for the document the change belongs to, as a write to it is.
    """
    store = _store(request)
    revision = await store.revision(revision_id)
    # Authorized before a missing change is reported, so a restricted key learns nothing about which exist.
    scope = _revision_scope(revision) if revision is not None else Scope.deployment()
    await _may_write(request, scope)
    if revision is None:
        raise HTTPException(status_code=404, detail="That change was not found.")
    try:
        result = await store.undo(revision_id, actor=request_actor(request))
    except RevisionNotFound:
        raise HTTPException(status_code=404, detail="That change was not found.") from None
    except NotUndoable:
        raise HTTPException(status_code=409, detail=WHY_NOT) from None
    except RevisionInvalid as exc:
        raise _unprocessable(exc) from None
    await _audit(
        request,
        "settings.change_undone",
        {
            "scope": str(scope),
            "revision_id": revision_id,
            "new_revision_id": result.revision_id,
            "generation": result.generation,
        },
    )
    return {
        "scope": scope.view(),
        "generation": result.generation,
        "revision_id": result.revision_id,
        "changes": result.changes,
        "unknown_sections": list(result.unknown_sections),
        "unknown_keys": list(result.unknown_keys),
        "layers_refused": result.layers_refused,
        "layers_refused_total": result.layers_refused_total,
    }
