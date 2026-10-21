"""A repository's change history and undo (ADR 0019).

GET lists the revisions the storage layer records for a repo's test setup and
preferences (``repo_config_revisions``), each described in plain words. POST
undo puts back what one change replaced: only the newest change of its
surface, and only while the stored document is still what that change wrote.
An undo is itself recorded, with source ``restore``, so it can be undone too.

Operator tier, the same as the writes it reverses: the router is mounted with
``auth_deps``, which already refuses a read-scope key on POST. Undo also
checks the calling key's repository allowlist first.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError

from embry0.api.deps import request_actor
from embry0.api.repo_allowlist import enforce_repo_allowlist
from embry0.api.schemas.repo_preferences import RepoPreferencesUpdateRequest
from embry0.audit.helpers import emit_audit
from embry0.storage.repositories.repo_config_revisions import RevisionSurface
from embry0.storage.repositories.repo_preferences import preferences_snapshot

router = APIRouter()

WHY_NOT: Final = "This changed again later. Undo the newer change first."

# Plain names for the preference fields, in the order a change lists them.
_FIELD_NAMES: Final[dict[str, str]] = {
    "sandbox_profile": "Work machine",
    "base_branch": "Branch embry0 starts from",
    "git_author_name": "Commit author name",
    "git_author_email": "Commit author email",
    "dev_checks": "Checks before a pull request",
    "language_hint": "Language label",
    "notes": "Notes",
    "pr_review": "Pull request review",
    "verification": "Test gate",
    "execution_mode": "How agents run",
    "auth_mode": "How agents sign in",
}
# The two fields a change names with their values, and what an empty value means.
_UNSET_VALUE: Final[dict[str, str]] = {
    "sandbox_profile": "(embry0's default)",
    "base_branch": "(repository default)",
}


def _review_on(doc: Mapping[str, Any]) -> bool:
    pr_review = doc.get("pr_review")
    return isinstance(pr_review, dict) and pr_review.get("enabled") is True


def _is_set(value: Any) -> bool:
    return value not in (None, "", [], {})


def _preferences_what(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> str:
    if before is None or after is None:
        doc = (after if before is None else before) or {}
        verb = "created" if before is None else "cleared"
        names = [name for field, name in _FIELD_NAMES.items() if _is_set(doc.get(field))]
        text = f"Settings {verb}: {', '.join(names)}" if names else f"Settings {verb}"
        if after is None and _review_on(doc):
            text += " (pull request review turned off)"
        return text
    sentences: list[str] = []
    plain: list[str] = []
    for field, name in _FIELD_NAMES.items():
        old, new = before.get(field), after.get(field)
        if old == new:
            continue
        if field in _UNSET_VALUE:
            unset = _UNSET_VALUE[field]
            sentences.append(f"{name} changed from {old or unset} to {new or unset}")
        elif field == "pr_review" and _review_on(before) != _review_on(after):
            sentences.append("Pull request review turned on" if _review_on(after) else "Pull request review turned off")
        else:
            plain.append(name)
    if plain:
        sentences.append(f"Settings changed: {', '.join(plain)}")
    return ". ".join(sentences) or "Settings changed"


def change_what(surface: str, before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> str:
    """One change in plain words, as the History panel shows it."""
    if surface == "qa_config":
        if before is None:
            return "Test setup created"
        return "Test setup removed" if after is None else "Test setup changed"
    return _preferences_what(before, after)


async def _current(request: Request, repo: str, surface: RevisionSurface) -> dict[str, Any] | None:
    """The stored document in the shape a revision's ``after`` takes."""
    if surface == "qa_config":
        row = await request.app.state.qa_configs_repo.get(repo)
        return None if row is None else {"yaml_text": row["yaml_text"]}
    prefs = await request.app.state.repo_preferences_repo.get(repo)
    return None if prefs is None else preferences_snapshot(prefs)


@router.get("/repos/{full_repo:path}/history")
async def get_history(full_repo: str, request: Request, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    revisions = request.app.state.repo_config_revisions_repo
    rows = await revisions.list_for_repo(full_repo, limit=limit)
    current = {
        "qa_config": await _current(request, full_repo, "qa_config"),
        "preferences": await _current(request, full_repo, "preferences"),
    }
    # Newest first, so the first row of a surface is that surface's newest change.
    newest: dict[str, int] = {}
    for row in rows:
        newest.setdefault(row["surface"], row["id"])
    entries = []
    for row in rows:
        undoable = newest[row["surface"]] == row["id"] and row["after"] == current[row["surface"]]
        entries.append(
            {
                "id": row["id"],
                "surface": row["surface"],
                "what": change_what(row["surface"], row["before"], row["after"]),
                "actor": row["actor"],
                "source": row["source"],
                "created_at": row["created_at"].isoformat(),
                "undoable": undoable,
                "why_not": None if undoable else WHY_NOT,
            }
        )
    before_history = current["qa_config"] is not None and await revisions.latest(full_repo, "qa_config") is None
    return {"entries": entries, "before_history": before_history}


def _no_longer_valid(exc: Exception) -> HTTPException:
    return HTTPException(status_code=422, detail=f"That earlier version is no longer valid: {exc}")


@router.post("/repos/{full_repo:path}/history/{revision_id}/undo")
async def undo_change(full_repo: str, revision_id: int, request: Request) -> dict[str, int | None]:
    from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2

    await enforce_repo_allowlist(request, full_repo, action="undo a change")
    revision = await request.app.state.repo_config_revisions_repo.get(revision_id)
    if revision is None or revision["repo"] != full_repo:
        raise HTTPException(status_code=404, detail="That change was not found for this repository.")
    surface: RevisionSurface = revision["surface"]
    latest = await request.app.state.repo_config_revisions_repo.latest(full_repo, surface)
    if (
        latest is None
        or latest["id"] != revision_id
        or revision["after"] != await _current(request, full_repo, surface)
    ):
        raise HTTPException(status_code=409, detail=WHY_NOT)

    actor = request_actor(request)
    reason = f"undo of change {revision_id}"
    before = revision["before"]
    new_revision_id: int | None
    if surface == "qa_config":
        configs = request.app.state.qa_configs_repo
        if before is None:
            new_revision_id = await configs.delete_recorded(full_repo, actor=actor, source="restore", reason=reason)
        else:
            try:
                parse_qa_yaml_v2(before["yaml_text"])
            except (ValueError, yaml.YAMLError) as exc:
                raise _no_longer_valid(exc) from exc
            try:
                _, new_revision_id = await configs.upsert_recorded(
                    full_repo, before["yaml_text"], updated_by=actor, source="restore", reason=reason
                )
            except ValueError as exc:
                # As the PUT: a deployment with no default forge will not
                # re-register a repository that was removed since.
                raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        prefs = request.app.state.repo_preferences_repo
        if before is None:
            new_revision_id = await prefs.delete_recorded(full_repo, actor=actor, source="restore", reason=reason)
        else:
            fields = {k: before[k] for k in RepoPreferencesUpdateRequest.model_fields if k in before}
            try:
                RepoPreferencesUpdateRequest.model_validate(fields)
            except ValidationError as exc:
                raise _no_longer_valid(exc) from exc
            # The stored values go back as they were; the model only checks
            # they would still be accepted today.
            _, new_revision_id = await prefs.upsert_recorded(
                full_repo, actor=actor, source="restore", reason=reason, **fields
            )

    config = getattr(request.app.state, "config", None)
    await emit_audit(
        getattr(request.app.state, "db", None),
        "repo.change_undone",
        actor=actor,
        details={"repo": full_repo, "revision_id": revision_id, "new_revision_id": new_revision_id},
        audit_log_path=getattr(config, "audit_log_path", None),
    )
    return {"revision_id": new_revision_id}
