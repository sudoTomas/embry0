"""The settings document API (spec 2026-10-28): operator policy, one section per feature.

GET reads the stored document, the effective value of every setting with
where it came from, the JSON Schema, and the revisions. PUT replaces the
whole document, YAML or a JSON object, and answers 422 listing every bad key
in FastAPI's own error shape (``loc``, ``type``, ``msg``, never the rejected
value). ``?dry_run=1`` answers what would change and stores nothing. Undo
puts back what the newest change replaced.

Reads take any key: the document holds no secrets. Who changed it is shown
as ``GET /audit`` shows actors (:mod:`embry0.api.actor_visibility`): an
admin sees everyone, anyone else only themselves. Writes take the admin
tier and a key without a repository allowlist, since the document governs
every repository. A body past 256 KiB is refused before it is read in
full, with or without a ``Content-Length``.
"""

from __future__ import annotations

from collections.abc import Callable
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


def _store(request: Request) -> SettingsStore:
    store = getattr(request.app.state, "settings", None)
    if store is None:
        raise HTTPException(status_code=503, detail="settings storage not initialized")
    return cast(SettingsStore, store)


def _unprocessable(exc: DocumentInvalid) -> HTTPException:
    return HTTPException(status_code=422, detail=[problem.to_dict() for problem in exc.problems])


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
    request: Request, response: Response, format: Literal["json", "yaml"] = "json"
) -> dict[str, Any] | Response:
    """The stored settings document: its text, its values and its generation.

    ``?format=yaml`` answers the text alone. The ``ETag`` is the generation,
    for ``If-Match`` on a later PUT.
    """
    store = _store(request)
    row = await store.document()
    etag = f'"{row["generation"]}"'
    if format == "yaml":
        return Response(content=row["yaml_text"], media_type="application/yaml", headers={"ETag": etag})
    response.headers["ETag"] = etag
    shown = await actor_shown(request)
    unknown_sections, unknown_keys = unknown_parts(store.catalog, row["data"])
    return {
        "yaml_text": row["yaml_text"],
        "data": row["data"],
        "schema_version": row["schema_version"],
        "generation": row["generation"],
        # Who wrote it, as /audit shows actors: an admin sees anyone, anyone else only themselves.
        "updated_by": shown(row["updated_by"]) if row["updated_by"] else row["updated_by"],
        "updated_at": iso(row["updated_at"]),
        "sections": list(store.catalog.names),
        "unknown_sections": list(unknown_sections),
        "unknown_keys": list(unknown_keys),
    }


@router.put("/config/settings", dependencies=[require_admin])
async def put_settings(
    request: Request,
    dry_run: bool = False,
    reason: str = Query(default="", max_length=500),
    source: Literal["person", "file"] = "person",
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> dict[str, Any]:
    """Replace the settings document.

    The body is YAML (stored as written, comments included) or a JSON object
    (``Content-Type: application/json``, stored as the YAML it dumps to). An
    empty body puts every setting back to its default. 422 lists every
    problem; 409 when ``If-Match`` names no generation that is the stored
    one (``*`` matches any); 413 past 256 KiB. With ``dry_run`` nothing is
    stored, ``If-Match`` is checked the same way, and the answer says what
    would change.
    """
    await enforce_repo_allowlist(request, None, action=_WRITE_ACTION)
    store = _store(request)
    expected = if_match_versions(if_match)
    body = await read_capped_body(
        request, MAX_TEXT_BYTES, f"the settings document is larger than {MAX_TEXT_BYTES} bytes"
    )
    try:
        text, values = parse_body(body, request.headers.get("content-type", ""))
        if dry_run:
            preview = await store.preview(text, values, if_generation=expected)
            return {
                "would_change": preview.would_change,
                "text_diff": preview.text_diff,
                "changes": preview.changes,
                "unknown_sections": list(preview.unknown_sections),
                "unknown_keys": list(preview.unknown_keys),
            }
        result = await store.save(
            text, values, actor=request_actor(request), source=source, reason=reason, if_generation=expected
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
                "revision_id": result.revision_id,
                "generation": result.generation,
                "keys": [change["key"] for change in result.changes],
                "source": source,
                "reason": reason,
            },
        )
    return {
        "generation": result.generation,
        "revision_id": result.revision_id,
        "changes": result.changes,
        "unknown_sections": list(result.unknown_sections),
        "unknown_keys": list(result.unknown_keys),
    }


@router.get("/config/settings/effective")
async def get_effective_settings(request: Request) -> dict[str, Any]:
    """Every setting of every section: the value loops read, its default, and where it came from."""
    store = _store(request)
    snapshot = await store.snapshot()
    revisions = await store.revisions(limit=100)
    view = effective_view(store.catalog, snapshot, revisions, request.app.state.config)
    shown = await actor_shown(request)
    for section in view["sections"]:
        for key in section["keys"]:
            key["set_by"] = shown(key["set_by"])
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


def _what(revision: dict[str, Any]) -> str:
    before = (revision.get("before") or {}).get("data") or {}
    after = (revision.get("after") or {}).get("data") or {}
    keys = [change.path for change in changes(before, after)]
    if not keys:
        return "Comments or layout changed; no setting changed"
    verb = "Imported from the environment" if revision.get("source") == "bootstrap" else "Changed"
    return f"{verb}: {', '.join(keys)}"


def _revision_entry(
    revision: dict[str, Any], newest_id: int | None, current_text: str, shown: Callable[[str | None], str | None]
) -> dict[str, Any]:
    undoable = revision["id"] == newest_id and (revision.get("after") or {}).get("yaml_text") == current_text
    return {
        "id": revision["id"],
        "what": _what(revision),
        "actor": shown(revision["actor"]),
        "source": revision["source"],
        "reason": revision["reason"],
        "created_at": iso(revision["created_at"]),
        "undoable": undoable,
        "why_not": None if undoable else WHY_NOT,
    }


@router.get("/config/settings/revisions")
async def list_settings_revisions(request: Request, limit: int = Query(50, ge=1, le=100)) -> dict[str, Any]:
    """The document's changes, newest first; only the newest can be undone, while it is still what is stored."""
    store = _store(request)
    rows = await store.revisions(limit=limit)
    current = (await store.document())["yaml_text"]
    newest = rows[0]["id"] if rows else None
    shown = await actor_shown(request)
    return {"revisions": [_revision_entry(row, newest, current, shown) for row in rows]}


@router.get("/config/settings/revisions/{revision_id}")
async def get_settings_revision(revision_id: int, request: Request) -> dict[str, Any]:
    """One change: its text before and after, and each key it changed."""
    store = _store(request)
    revision = await store.revision(revision_id)
    if revision is None:
        raise HTTPException(status_code=404, detail="That change was not found.")
    newest = await store.revisions(limit=1)
    current = (await store.document())["yaml_text"]
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
    """Put back what the newest change replaced. 409 for any other change; recorded with source ``restore``."""
    await enforce_repo_allowlist(request, None, action=_WRITE_ACTION)
    store = _store(request)
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
        {"revision_id": revision_id, "new_revision_id": result.revision_id, "generation": result.generation},
    )
    return {
        "generation": result.generation,
        "revision_id": result.revision_id,
        "changes": result.changes,
        "unknown_sections": list(result.unknown_sections),
        "unknown_keys": list(result.unknown_keys),
    }
