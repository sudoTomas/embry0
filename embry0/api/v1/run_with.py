"""Run with (spec 2026-10-25): the keys a job may be run with, and the check every action that starts agent work makes.

``GET /run-with/keys?repo=`` lists the stored LLM keys a job on ``repo``
may use: those bound to the repository, its owner, or the global pool, by
label and provider, never a secret. :func:`checked_run_with` turns an
action's ``run_with`` into a :class:`~embry0.services.run_with.RunWith`, or
refuses it with a 422 that says why.

To a key with a repository allowlist, a key that is neither its own
(:func:`~embry0.api.v1.credentials.key_owns_credential`) nor one a job on a
repository its list covers resolves answers as a key that does not exist
(:func:`_shown_to`). A key the repository resolves, the global pool's
included, stays offered and choosable, as ``GET /repos/{repo}/-/credentials``
shows it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from embry0.api.repo_allowlist import answer_as_missing, enforce_repo_allowlist, key_covers, restricted_key_name
from embry0.api.schemas import RunWithRequest
from embry0.api.v1.credentials import key_owns_credential
from embry0.services.run_with import ALL_AGENTS, LLM_KINDS, RunWith, key_scopes, model_provider

router = APIRouter()

#: What naming a credential a key with an allowlist may not see is refused, in the log and the audit row.
_RUN_WITH = "run work with a credential"


def _usable(row: dict[str, Any]) -> bool:
    from embry0.services.credentials.resolver import CredentialResolver

    return CredentialResolver._availability(row) is None  # noqa: SLF001 - the one availability rule


async def usable_keys(request: Request, repo: str | None) -> list[dict[str, Any]]:
    """The LLM keys a job on ``repo`` may be run with, each once, the most specific scope first."""
    creds = getattr(request.app.state, "credentials_repo", None)
    if creds is None:
        return []
    keys: dict[str, dict[str, Any]] = {}
    for scope_kind, scope_value in key_scopes(repo):
        for binding in await creds.bindings_for_scope(scope_kind, scope_value):
            if binding.get("role") != "llm" or binding["credential_id"] in keys:
                continue
            keys[binding["credential_id"]] = {
                "id": binding["credential_id"],
                "label": binding.get("label") or binding["credential_id"],
                "provider": binding.get("provider"),
                "scope": scope_kind,
                "status": binding.get("status"),
            }
    return list(keys.values())


async def _shown_to(request: Request, row: dict[str, Any], repo: str | None) -> bool:
    """Whether the key ``row`` a caller names for a job on ``repo`` may be answered about, by its label and status.
    Always to a caller without an allowlist. To a key with one: its own key, and a key a job on ``repo`` resolves
    (bound to the repository, its owner or the global pool) when its list covers ``repo``; any other is not there
    for it."""
    if restricted_key_name(request) is None or await key_owns_credential(request, row):
        return True
    return key_covers(request, repo) and str(row["id"]) in {k["id"] for k in await usable_keys(request, repo)}


async def checked_run_with(request: Request, body: RunWithRequest | None, repo: str | None) -> RunWith | None:
    """``body`` checked for a job on ``repo``; None for the usual run.

    The model must be priced and enabled (the checks ``POST /jobs`` makes of
    an agent model). The key must be a usable LLM key bound to the
    repository, its owner, or the global pool, and of the chosen model's
    provider when both are chosen. To a key with an allowlist, a key
    :func:`_shown_to` does not show it answers as one that does not exist,
    the refusal logged and audited.
    """
    if body is None or (not body.model and not body.credential_id):
        return None
    model = (body.model or "").strip() or None
    if model:
        from embry0.api.v1.jobs import check_agent_models

        await check_agent_models(request, {ALL_AGENTS: model})
    provider = label = None
    credential_id = (body.credential_id or "").strip() or None
    if credential_id:
        creds = getattr(request.app.state, "credentials_repo", None)
        row = await creds.get(credential_id) if creds is not None else None
        missing = HTTPException(status_code=422, detail=f"run_with: {credential_id} is not a stored LLM key")
        if row and not await _shown_to(request, row, repo):
            # Before its kind, status or bindings are read into an answer: it is not there for the caller.
            await answer_as_missing(request, f"credential {credential_id}", missing, action=_RUN_WITH)
        if not row or row.get("kind") not in LLM_KINDS:
            raise missing
        provider, label = str(row.get("provider") or ""), str(row.get("label") or credential_id)
        if not _usable(row):
            raise HTTPException(status_code=422, detail=f"run_with: the key '{label}' is {row.get('status')}")
        if credential_id not in {k["id"] for k in await usable_keys(request, repo)}:
            raise HTTPException(
                status_code=422,
                detail=f"run_with: the key '{label}' is not bound for {repo or 'jobs without a repository'}; "
                "bind it to the repository, its owner or the global pool on the Credentials page",
            )
        if model and model_provider(model) != provider:
            raise HTTPException(
                status_code=422,
                detail=f"run_with: {model} runs on {model_provider(model)}, and '{label}' is a {provider} key",
            )
    return RunWith(model=model, credential_id=credential_id, provider=provider, credential_label=label)


@router.get("/run-with/keys")
async def run_with_keys(request: Request, repo: str | None = Query(default=None)) -> dict[str, Any]:
    """The stored LLM keys a job on ``repo`` may be run with (labels and providers only)."""
    if repo:
        await enforce_repo_allowlist(request, repo, action="read the keys a job may use")
    return {"repo": repo, "keys": await usable_keys(request, repo)}
