"""The progression content pack API (#176): read the pack, edit it, preview what it re-prices, activate, undo, reset.

One pack per deployment. ``GET /progression/pack`` is the one in effect: a
stored customised version, or the shipped default when nothing is stored or
after a reset. A write takes YAML (stored as written, comments kept) or a
JSON object, and is checked in full first: a refusal is a 422 in FastAPI's
own shape, ``{"detail": [{"loc", "type", "msg"}]}``, one entry per problem,
never echoing a value. ``If-Match`` names the version a write expects to
replace (``"0"`` when nothing is stored); a stale one is a 409.

Two ways to change it, following the settings document's conventions (#160):

- the draft: ``PUT /progression/pack/draft``, ``GET /progression/preview``
  (the history re-priced under it, before going live) and
  ``POST /progression/pack/draft/activate``;
- the one-shot ``PUT /progression/pack`` for the CLI and MCP, with
  ``?dry_run=1`` to see the problems, warnings and diff first.

Reads take any key; who made a version is shown as ``GET /audit`` shows
actors (an admin sees everyone, anyone else only themselves). Writes and
the preview take the admin tier and a key without a repository allowlist:
the pack decides what every page shows and re-prices the whole history.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from functools import cache
from typing import Any, Literal, cast

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response

from embry0.api.actor_visibility import actor_shown
from embry0.api.deps import request_actor, require_admin
from embry0.api.document_requests import if_match_versions, read_capped_body
from embry0.api.repo_allowlist import enforce_repo_allowlist
from embry0.audit.actions import AuditAction
from embry0.audit.helpers import emit_audit
from embry0.services import progression_vocabulary as vocab
from embry0.services.progression import ProgressionService, pack_changes
from embry0.services.progression_pack import (
    MAX_TEXT_BYTES,
    SCHEMA_VERSION,
    Pack,
    PackInvalid,
    load_text,
    pack_hash,
    rules_for,
)
from embry0.services.progression_pack_store import (
    Activation,
    DraftStale,
    NoDraft,
    PackStore,
    PackTooLarge,
    Submitted,
    VersionMismatch,
    VersionNotFound,
    changed_sections,
    pack_warnings,
    read_body,
)
from embry0.storage.repositories.progression_packs import StoredPack

router = APIRouter()

_WRITE_ACTION = "change the progression content pack"
_REASON = Query(default="", max_length=500, description="why, for the version history")


def _store(request: Request) -> PackStore:
    store = getattr(request.app.state, "progression_packs", None)
    if store is None:
        raise HTTPException(status_code=503, detail="the progression content pack is not initialized")
    return cast(PackStore, store)


def _service(request: Request) -> ProgressionService:
    service = getattr(request.app.state, "progression", None)
    if service is None:
        raise HTTPException(status_code=503, detail="progression is not initialized")
    return cast(ProgressionService, service)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _unprocessable(exc: PackInvalid) -> HTTPException:
    return HTTPException(status_code=422, detail=[problem.to_dict() for problem in exc.problems])


def _conflict(exc: VersionMismatch) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=f"The {exc.what} changed since you read it (now version {exc.current}). "
        "Read it again and apply your change to the new version.",
    )


async def _body(request: Request) -> Submitted:
    """The request's pack, refused (413) before it is read in full when it is too large."""
    too_large = f"the pack is larger than {MAX_TEXT_BYTES // 1024} KiB"
    body = await read_capped_body(request, MAX_TEXT_BYTES, too_large)
    try:
        return read_body(body, request.headers.get("content-type", ""))
    except PackTooLarge:
        raise HTTPException(status_code=413, detail=too_large) from None
    except PackInvalid as exc:
        raise _unprocessable(exc) from None


async def _agent_types(request: Request) -> list[str] | None:
    repo = getattr(request.app.state, "agent_defs_repo", None)
    if repo is None:
        return None
    try:
        return [str(row["type"]) for row in await repo.list_all()]
    except Exception:  # noqa: BLE001 - a warning is not worth failing a write over
        return None


async def _audit(request: Request, action: AuditAction, details: dict[str, Any]) -> None:
    config = getattr(request.app.state, "config", None)
    await emit_audit(
        getattr(request.app.state, "db", None),
        action,
        actor=request_actor(request),
        details=details,
        audit_log_path=getattr(config, "audit_log_path", None),
    )


async def _activated(request: Request, result: Activation, *, reason: str) -> dict[str, Any]:
    """The answer of an activating write, audited when it changed something."""
    if result.changed:
        await _audit(
            request,
            "progression.pack_reset" if result.source == "reset" else "progression.pack_activated",
            {
                "version": result.version,
                "previous_version": result.previous,
                "source": result.source,
                "reason": reason,
            },
        )
    return result.to_dict()


@cache
def _document_of(text: str) -> dict[str, Any]:
    loaded = load_text(text)
    return loaded if isinstance(loaded, dict) else {}


def _version_entry(row: StoredPack, shown: Callable[[str | None], str | None]) -> dict[str, Any]:
    """One version as the API lists it. ``created_by`` as ``GET /audit`` shows actors: the caller's own, or null."""
    return {
        "version": row.version,
        "status": row.status,
        "follows_default": row.follows_default,
        "schema_version": row.schema_version,
        "based_on": row.based_on,
        "source": row.source,
        "reason": row.reason,
        "created_by": shown(row.created_by),
        "created_at": _iso(row.created_at),
        "activated_at": _iso(row.activated_at),
        "parent_version": row.parent_version,
    }


# ---- reading ----


@router.get("/progression/pack", response_model=None)
async def get_pack(
    request: Request, response: Response, format: Literal["json", "yaml"] = "json"
) -> dict[str, Any] | Response:
    """The pack in effect: its text, its values, its version and whether the shipped default moved on since.

    ``version`` is 0 when nothing is stored. ``fallback`` lists why a stored
    version is not in effect (this release refuses it): the shipped default
    is then, and ``text`` is still the stored one, to be fixed. The ``ETag``
    is the version, for ``If-Match`` on a write. ``?format=yaml`` answers the
    text in effect alone: with a refused stored version, the shipped
    default's, and ``X-Embry0-Pack-Refused`` names the refused version.
    ``created_by`` is shown as ``GET /audit`` shows actors.
    """
    store = _store(request)
    row = await store.repository.active()
    effective = store.resolve(row)
    text = store.text_of(row)
    etag = f'"{effective.version}"'
    if format == "yaml":
        headers = {"ETag": etag}
        if effective.fallback is not None:
            text = store.shipped().text
            headers["X-Embry0-Pack-Refused"] = str(effective.version)
        return Response(content=text, media_type="application/yaml", headers=headers)
    response.headers["ETag"] = etag
    shown = await actor_shown(request)
    shipped = store.shipped()
    moved = await store.default_moved(row)
    draft = await store.draft()
    customised = row is not None and not row.follows_default
    return {
        "version": effective.version,
        "follows_default": effective.follows_default,
        "schema_version": row.schema_version if row is not None else SCHEMA_VERSION,
        "hash": pack_hash(effective.pack),
        "text": text,
        "document": row.document if customised and row is not None else _document_of(shipped.text),
        "based_on": moved["based_on"],
        "default_hash": shipped.hash,
        "default_moved": moved["moved"],
        "source": row.source if row is not None else None,
        "reason": row.reason if row is not None else "",
        "created_by": shown(row.created_by) if row is not None else None,
        "activated_at": _iso(row.activated_at) if row is not None else None,
        "fallback": [p.to_dict() for p in effective.fallback] if effective.fallback is not None else None,
        "draft_version": draft.version if draft is not None else None,
    }


@router.get("/progression/pack/default", response_model=None)
async def get_default_pack(request: Request, format: Literal["json", "yaml"] = "json") -> dict[str, Any] | Response:
    """The shipped default this release carries: its text, comments included, and its hash."""
    shipped = _store(request).shipped()
    if format == "yaml":
        return Response(content=shipped.text, media_type="application/yaml")
    return {"text": shipped.text, "hash": shipped.hash, "schema_version": SCHEMA_VERSION}


@router.get("/progression/pack/default/diff")
async def get_default_diff(request: Request) -> dict[str, Any]:
    """Whether the shipped default moved on since the active customised pack was taken from it, and the diff.

    ``text_diff`` runs from the default the pack was based on to this
    release's; null when nothing moved, or when that default's text was not
    recorded.
    """
    return await _store(request).default_moved()


@router.get("/progression/pack/vocabulary")
async def get_pack_vocabulary(request: Request) -> dict[str, Any]:
    """What a pack may name, for editors: the JSON Schema, and every metric, kind, category and art id with its parameters."""
    return {
        "schema_version": SCHEMA_VERSION,
        "schema": Pack.model_json_schema(),
        "metrics": {
            name: {"params": metric.params.model_json_schema(), "counted_from": "work" if metric.fragment else "levels"}
            for name, metric in vocab.METRICS.items()
        },
        "quest_kinds": {
            name: {"steps": kind.steps, "params": kind.params.model_json_schema(), "capstone": kind.capstone}
            for name, kind in vocab.QUEST_KINDS.items()
        },
        "task_kinds": {name: {"params": params.model_json_schema()} for name, params in vocab.TASK_KINDS.items()},
        "action_kinds": list(vocab.ACTION_KINDS),
        "action_keys": sorted(vocab.ACTION_KEYS),
        "outcome_sources": list(vocab.OUTCOME_SOURCES),
        "art": {
            "gear_steps": list(vocab.GEAR_STEPS),
            "rare_art": list(vocab.RARE_ART),
            "achievement_icons": list(vocab.ACHIEVEMENT_ICONS),
        },
        "messages": {name: sorted(names) for name, names in vocab.MESSAGE_PLACEHOLDERS.items()},
        "labels": {name: sorted(names) for name, names in vocab.LABEL_PLACEHOLDERS.items()},
    }


@router.get("/progression/pack/versions")
async def list_pack_versions(request: Request, limit: int = Query(50, ge=1, le=100)) -> dict[str, Any]:
    """The active and superseded versions, newest first, without their text."""
    rows = await _store(request).repository.versions(limit)
    shown = await actor_shown(request)
    return {"versions": [_version_entry(row, shown) for row in rows]}


@router.get("/progression/pack/versions/{version}")
async def get_pack_version(version: int, request: Request) -> dict[str, Any]:
    """One version with its text; ``text`` is null for one that followed the shipped default of its day."""
    row = await _store(request).repository.get(version)
    if row is None or row.status == "draft":
        raise HTTPException(status_code=404, detail="There is no such version of the pack.")
    return {**_version_entry(row, await actor_shown(request)), "text": row.text}


@router.get("/progression/pack/draft", response_model=None)
async def get_pack_draft(request: Request, response: Response) -> dict[str, Any]:
    """The one draft: its text and what it changes against the pack in effect; 404 when there is none.

    ``problems`` lists why this release refuses it (it was stored by
    another one); it cannot be previewed or activated until it is saved again.
    ``stale`` says the pack in effect changed since the draft was saved over
    ``parent_version``: it is previewed and saved again before it can be
    activated.
    """
    store = _store(request)
    row = await store.draft()
    if row is None:
        raise HTTPException(status_code=404, detail="There is no draft.")
    response.headers["ETag"] = f'"{row.version}"'
    effective = store.resolve(row)
    active = await store.effective()
    return {
        **_version_entry(row, await actor_shown(request)),
        "stale": row.parent_version != active.version,
        "text": row.text,
        "hash": pack_hash(effective.pack) if effective.fallback is None else None,
        "sections": changed_sections(active.pack, effective.pack) if effective.fallback is None else [],
        "problems": [p.to_dict() for p in effective.fallback] if effective.fallback is not None else None,
    }


# ---- writing ----


@router.put("/progression/pack/draft", dependencies=[require_admin])
async def put_pack_draft(
    request: Request,
    response: Response,
    reason: str = _REASON,
    rebase: bool = Query(False, description="the text now takes in this release's shipped default"),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> dict[str, Any]:
    """Store the one draft, replacing any other. Nothing anyone sees changes until it is activated.

    ``If-Match`` names the draft this replaces (``"0"`` for none). Answers
    the new draft's version, the sections it changes against the pack in
    effect, and the warnings.
    """
    await enforce_repo_allowlist(request, None, action=_WRITE_ACTION)
    store = _store(request)
    expected = if_match_versions(if_match)
    submitted = await _body(request)
    try:
        row, replaced = await store.save_draft(
            submitted, actor=request_actor(request), if_match=expected, rebase=rebase, reason=reason
        )
    except VersionMismatch as exc:
        raise _conflict(exc) from None
    except PackInvalid as exc:
        raise _unprocessable(exc) from None
    await _audit(request, "progression.pack_draft_saved", {"version": row.version, "replaced_version": replaced})
    active = await store.effective()
    response.headers["ETag"] = f'"{row.version}"'
    return {
        "version": row.version,
        "replaced_version": replaced,
        "hash": submitted.hash,
        "based_on": row.based_on,
        "sections": changed_sections(active.pack, submitted.pack),
        "warnings": pack_warnings(submitted.pack, await _agent_types(request)),
    }


@router.delete("/progression/pack/draft", dependencies=[require_admin])
async def delete_pack_draft(
    request: Request, if_match: str | None = Header(default=None, alias="If-Match")
) -> dict[str, Any]:
    """Discard the draft. 404 when there is none."""
    await enforce_repo_allowlist(request, None, action=_WRITE_ACTION)
    try:
        version = await _store(request).delete_draft(if_match=if_match_versions(if_match))
    except NoDraft:
        raise HTTPException(status_code=404, detail="There is no draft.") from None
    except VersionMismatch as exc:
        raise _conflict(exc) from None
    await _audit(request, "progression.pack_draft_deleted", {"version": version})
    return {"deleted_version": version}


@router.get("/progression/preview", dependencies=[require_admin])
async def preview_pack_draft(request: Request) -> dict[str, Any]:
    """``GET /progression``'s answer under the draft, computed now from the history, with ``changes``.

    ``changes`` holds each character's level, total level and experience,
    the account's totals and the achievements unlocked, before (the pack in
    effect) and after (the draft). One preview runs at a time: it reads the
    whole history, so another is refused with 429 while it does.
    """
    store = _store(request)
    service = _service(request)
    lock = getattr(request.app.state, "progression_preview_lock", None)
    if lock is None:
        lock = request.app.state.progression_preview_lock = asyncio.Lock()
    if lock.locked():
        raise HTTPException(
            status_code=429,
            detail="Another preview is being computed. Try again when it is done.",
            headers={"Retry-After": "5"},
        )
    async with lock:
        try:
            row, draft = await store.draft_pack()
        except NoDraft:
            raise HTTPException(status_code=404, detail="There is no draft to preview.") from None
        except PackInvalid as exc:
            raise _unprocessable(exc) from None
        rules = rules_for(draft)
        active = await store.effective()
        before = await service.read()
        after = await service.compute(rules)
    return {
        **after.to_dict(),
        "draft": {
            "version": row.version,
            "hash": rules.hash,
            "parent_version": row.parent_version,
            "stale": row.parent_version != active.version,
        },
        "active": {"version": active.version, "hash": before.rules.hash},
        "changes": pack_changes(before, after),
    }


@router.post("/progression/pack/draft/activate", dependencies=[require_admin])
async def activate_pack_draft(
    request: Request, reason: str = _REASON, if_match: str | None = Header(default=None, alias="If-Match")
) -> dict[str, Any]:
    """The draft goes live: it becomes the active version and the one before it is superseded.

    ``If-Match`` names the draft (the version a preview showed), so a draft
    changed since is not activated unseen. A draft saved over an earlier
    pack than the one in effect is a 409 too: activating it would undo the
    newer activation unseen, so it is previewed and saved again first. The
    draft goes live as a new version, the newest, and is used up. Every page
    re-prices the history under it at its next read.
    """
    await enforce_repo_allowlist(request, None, action=_WRITE_ACTION)
    try:
        result = await _store(request).activate_draft(
            actor=request_actor(request), if_match=if_match_versions(if_match), reason=reason
        )
    except NoDraft:
        raise HTTPException(status_code=404, detail="There is no draft to activate.") from None
    except VersionMismatch as exc:
        raise _conflict(exc) from None
    except DraftStale as exc:
        raise HTTPException(
            status_code=409,
            detail=f"The pack in effect changed since this draft was saved: it was saved over version "
            f"{exc.parent}, and version {exc.current} is in effect now. Preview the draft again, save it again "
            "over the pack in effect, then activate it.",
        ) from None
    except PackInvalid as exc:
        raise _unprocessable(exc) from None
    return await _activated(request, result, reason=reason)


@router.put("/progression/pack", dependencies=[require_admin])
async def put_pack(
    request: Request,
    dry_run: bool = False,
    reason: str = _REASON,
    source: Literal["person", "file"] = "person",
    rebase: bool = Query(False, description="the text now takes in this release's shipped default"),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> dict[str, Any]:
    """Validate a pack and activate it in one call (the CLI and MCP).

    The body is YAML (stored as written, comments included) or a JSON object
    (``Content-Type: application/json``, stored as the YAML it dumps to). A
    text equal to the one in effect records nothing. 422 lists every
    problem; 409 when ``If-Match`` names another version; 413 past 256 KiB.
    With ``dry_run`` nothing is stored, and the answer is whether it would
    change, the sections it changes, the text diff and the warnings.
    """
    await enforce_repo_allowlist(request, None, action=_WRITE_ACTION)
    store = _store(request)
    expected = if_match_versions(if_match)
    submitted = await _body(request)
    warnings = pack_warnings(submitted.pack, await _agent_types(request))
    try:
        if dry_run:
            return {**await store.preview_put(submitted, if_match=expected), "warnings": warnings}
        result = await store.put(
            submitted, actor=request_actor(request), source=source, reason=reason, if_match=expected, rebase=rebase
        )
    except VersionMismatch as exc:
        raise _conflict(exc) from None
    except PackInvalid as exc:
        raise _unprocessable(exc) from None
    answer = await _activated(request, result, reason=reason)
    return {**answer, "warnings": warnings}


@router.post("/progression/pack/versions/{version}/restore", dependencies=[require_admin])
async def restore_pack_version(
    version: int, request: Request, reason: str = _REASON, if_match: str | None = Header(default=None, alias="If-Match")
) -> dict[str, Any]:
    """Undo: an earlier version becomes the active one again, recorded as a new version with source ``restore``.

    ``If-Match`` names the active version this replaces. 422 when this
    release refuses that version's pack.
    """
    await enforce_repo_allowlist(request, None, action=_WRITE_ACTION)
    try:
        result = await _store(request).restore(
            version, actor=request_actor(request), if_match=if_match_versions(if_match), reason=reason
        )
    except VersionNotFound:
        raise HTTPException(status_code=404, detail="There is no such version of the pack.") from None
    except VersionMismatch as exc:
        raise _conflict(exc) from None
    except PackInvalid as exc:
        raise _unprocessable(exc) from None
    return await _activated(request, result, reason=reason)


@router.post("/progression/pack/reset", dependencies=[require_admin])
async def reset_pack(
    request: Request, reason: str = _REASON, if_match: str | None = Header(default=None, alias="If-Match")
) -> dict[str, Any]:
    """Follow the shipped default from now on, as a new version (source ``reset``); a release that changes it then applies.

    Nothing is recorded when the deployment already follows it.
    """
    await enforce_repo_allowlist(request, None, action=_WRITE_ACTION)
    try:
        result = await _store(request).reset(
            actor=request_actor(request), if_match=if_match_versions(if_match), reason=reason
        )
    except VersionMismatch as exc:
        raise _conflict(exc) from None
    return await _activated(request, result, reason=reason)
