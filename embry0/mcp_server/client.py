"""Typed async HTTP client for the embry0 orchestrator API.

Configuration comes from ``EMBRY0_API_URL`` (default ``http://localhost:8200``)
and ``EMBRY0_API_KEY``. Every request carries the ``X-Requested-With`` header
required by the orchestrator's CSRF middleware on write methods, plus
``Authorization: Bearer <key>`` when a key is configured.
"""

from __future__ import annotations

import asyncio
import os
import re
from contextvars import ContextVar
from typing import Any

import httpx

# Hosted-endpoint auth (embry0.mcp_server.http): the middleware stashes the MCP
# caller's own bearer here per request; when set it OVERRIDES the env key so
# the orchestrator enforces the caller's key and scope, not the service's.
# Unset (stdio transport), the env-configured key applies as before.
caller_bearer: ContextVar[str | None] = ContextVar("embry0_caller_bearer", default=None)

DEFAULT_API_URL = "http://localhost:8200"

TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "cancelled", "partial", "expired", "skipped"})

_SECRET_MASK = "****"


class Embry0APIError(Exception):
    """An embry0 API call failed; carries the HTTP status and the API's ``detail``."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"embry0 API error {status_code}: {detail}")


def _as_dict(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise Embry0APIError(502, f"expected an object response, got {type(data).__name__}")
    return data


def _as_text(data: Any) -> str:
    if not isinstance(data, str):
        raise Embry0APIError(502, f"expected a text response, got {type(data).__name__}")
    return data


def _as_list(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, list):
        raise Embry0APIError(502, f"expected an array response, got {type(data).__name__}")
    return [_as_dict(item) for item in data]


def _drop_none(params: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if value is not None}


_REPO_SEGMENT = r"[A-Za-z0-9][A-Za-z0-9_.-]*"
_REPO_PATH_RE = re.compile(rf"^{_REPO_SEGMENT}(?:/{_REPO_SEGMENT})+\Z")
_REPO_PATH_ERROR = "repo must be 'group/name' or 'group/subgroup/name'"


def _repo_path(repo: str) -> str:
    r"""Validate a repo path and return it verbatim for use in a URL.

    embry0.mcp_server is a thin HTTP client that imports nothing else from
    embry0, so the rule of embry0/api/schemas/repo_path.py — the source of truth — is restated here
    verbatim, ``\Z`` anchor included, so a trailing newline cannot slip into a
    URL. Two or more segments: the orchestrator's /repos routes are
    ``{full_repo:path}`` catch-alls, so a GitLab subgroup path goes through
    whole instead of being split into owner+name and rejected.
    """
    if not _REPO_PATH_RE.match(repo):
        raise Embry0APIError(422, f"{_REPO_PATH_ERROR}, got {repo!r}")
    return repo


class Embry0Client:
    """Async client over the embry0 REST API (``/api/v1``)."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("EMBRY0_API_URL") or DEFAULT_API_URL).rstrip("/")
        key = api_key if api_key is not None else os.environ.get("EMBRY0_API_KEY", "")
        headers = {"X-Requested-With": "XMLHttpRequest"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self._client = httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        timeout: float | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        raw: bool = False,
    ) -> Any:
        try:
            extra: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
            extra_headers = dict(headers or {})
            bearer = caller_bearer.get()
            if bearer:
                extra_headers["Authorization"] = f"Bearer {bearer}"
            if extra_headers:
                extra["headers"] = extra_headers
            # json=None is the historical "no body". Raw bytes (qa.yaml) must not
            # also pass json, or httpx refuses the request.
            payload: dict[str, Any] = {"content": content} if content is not None else {"json": body}
            response = await self._client.request(method, path, params=params, **payload, **extra)
        except httpx.HTTPError as exc:
            raise Embry0APIError(0, f"cannot reach embry0 API at {self.base_url}: {exc}") from exc
        if response.status_code >= 400:
            raise Embry0APIError(response.status_code, _error_detail(response))
        if raw:
            return response.text
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    # ------------------------------------------------------------- health / stats

    async def health(self) -> dict[str, Any]:
        return _as_dict(await self._request("GET", "/api/v1/health/ready"))

    async def stats(self) -> dict[str, Any]:
        return _as_dict(await self._request("GET", "/api/v1/stats"))

    # ------------------------------------------------------------------- issues

    async def issue_list(
        self,
        *,
        status: str | None = None,
        priority: str | None = None,
        repo: str | None = None,
        labels: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        params = _drop_none(
            {
                "status": status,
                "priority": priority,
                "repo": repo,
                "labels": labels,
                "search": search,
                "limit": limit,
                "offset": offset,
            }
        )
        return _as_dict(await self._request("GET", "/api/v1/issues", params=params))

    async def issue_get(self, issue_id: str) -> dict[str, Any]:
        return _as_dict(await self._request("GET", f"/api/v1/issues/{issue_id}"))

    async def issue_create(
        self,
        title: str,
        *,
        body: str = "",
        labels: list[str] | None = None,
        priority: str = "medium",
        repo: str | None = None,
        auto_triage: bool = True,
    ) -> dict[str, Any]:
        payload = _drop_none(
            {
                "title": title,
                "body": body,
                "labels": labels or [],
                "priority": priority,
                "repo": repo,
                "auto_triage": auto_triage,
            }
        )
        return _as_dict(await self._request("POST", "/api/v1/issues", body=payload))

    # --------------------------------------------------------------------- jobs

    async def job_list(
        self,
        *,
        status: str | None = None,
        repo: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        params = _drop_none({"status": status, "repo": repo, "limit": limit, "offset": offset})
        return _as_dict(await self._request("GET", "/api/v1/jobs", params=params))

    async def job_get(self, job_id: str) -> dict[str, Any]:
        return _as_dict(await self._request("GET", f"/api/v1/jobs/{job_id}"))

    async def job_create(
        self,
        *,
        task: str | None = None,
        repo: str | None = None,
        pipeline: str | None = None,
        issue_number: int | None = None,
        branch: str | None = None,
        context: dict[str, Any] | None = None,
        pipeline_template: str | None = None,
        pipeline_config: dict[str, Any] | None = None,
        sandbox_profile: str | None = None,
        max_budget_usd: float | None = None,
        additional_context: str | None = None,
        agent_models: dict[str, str] | None = None,
        qa: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = _drop_none(
            {
                "task": task,
                "repo": repo,
                "pipeline": pipeline,
                "issue_number": issue_number,
                "branch": branch,
                "context": context,
                "pipeline_template": pipeline_template,
                "pipeline_config": pipeline_config,
                "sandbox_profile": sandbox_profile,
                "max_budget_usd": max_budget_usd,
                "additional_context": additional_context,
                "agent_models": agent_models,
                "qa": qa,
            }
        )
        return _as_dict(await self._request("POST", "/api/v1/jobs", body=payload))

    async def job_resume(self, job_id: str, choice: str, *, guidance: str = "") -> dict[str, Any]:
        body = {"choice": choice, "guidance": guidance}
        return _as_dict(await self._request("POST", f"/api/v1/jobs/{job_id}/resume", body=body))

    async def job_inputs(self, job_id: str) -> list[dict[str, Any]]:
        return _as_list(await self._request("GET", f"/api/v1/jobs/{job_id}/inputs"))

    async def job_input_answer(self, job_id: str, input_id: str, answer: str) -> dict[str, Any]:
        body = {"answer": answer}
        return _as_dict(await self._request("POST", f"/api/v1/jobs/{job_id}/inputs/{input_id}/answer", body=body))

    async def job_cancel(self, job_id: str) -> dict[str, Any]:
        return _as_dict(await self._request("POST", f"/api/v1/jobs/{job_id}/cancel"))

    async def job_logs(self, job_id: str) -> dict[str, Any]:
        """The whole pipeline event log, read page by page (the API pages at 500, #203)."""
        events: list[Any] = []
        since_seq = 0
        while True:
            page = _as_dict(
                await self._request(
                    "GET", f"/api/v1/jobs/{job_id}/logs/events", params={"since_seq": since_seq, "limit": 1000}
                )
            )
            events.extend(page.get("events") or [])
            next_seq = page.get("next_since_seq")
            if not page.get("has_more") or not isinstance(next_seq, int) or next_seq <= since_seq:
                return {"events": events}
            since_seq = next_seq

    async def job_deliverables(self, job_id: str) -> list[dict[str, Any]]:
        rows = _as_list(await self._request("GET", f"/api/v1/jobs/{job_id}/deliverables"))
        for row in rows:
            if row.get("type") == "artifact":
                deliverable_id = row.get("id") or row.get("deliverable_id")
                if deliverable_id:
                    row["download_url"] = f"{self.base_url}/api/v1/jobs/{job_id}/deliverables/{deliverable_id}/download"
        return rows

    async def job_wait(self, job_id: str, *, timeout: float = 600.0, poll_interval: float = 5.0) -> dict[str, Any]:
        """Poll a job until it reaches a terminal status or ``timeout`` elapses.

        On timeout the last-seen job is returned with ``wait_timed_out: true``
        instead of raising, so callers can inspect the in-flight state.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            job = await self.job_get(job_id)
            if job.get("status") in TERMINAL_JOB_STATUSES:
                return job
            remaining = deadline - loop.time()
            if remaining <= 0:
                job["wait_timed_out"] = True
                return job
            await asyncio.sleep(min(poll_interval, remaining))

    # ------------------------------------------------------------------- agents

    async def agent_list(self) -> list[dict[str, Any]]:
        return _as_list(await self._request("GET", "/api/v1/agents"))

    async def agent_get(self, agent_type: str) -> dict[str, Any]:
        return _as_dict(await self._request("GET", f"/api/v1/agents/{agent_type}"))

    async def agent_update(
        self,
        agent_type: str,
        *,
        description: str | None = None,
        model: str | None = None,
        tools: list[str] | None = None,
        skills: list[str] | None = None,
        system_prompt: str | None = None,
        execution_mode: str | None = None,
        auth_mode: str | None = None,
        mcp_servers: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = _drop_none(
            {
                "description": description,
                "model": model,
                "tools": tools,
                "skills": skills,
                "system_prompt": system_prompt,
                "execution_mode": execution_mode,
                "auth_mode": auth_mode,
                "mcp_servers": mcp_servers,
            }
        )
        return _as_dict(await self._request("PUT", f"/api/v1/agents/{agent_type}", body=body))

    async def agent_reset(self, agent_type: str) -> dict[str, Any]:
        return _as_dict(await self._request("POST", f"/api/v1/agents/{agent_type}/reset"))

    # ---------------------------------------------------------------- templates

    async def template_list(self) -> list[dict[str, Any]]:
        return _as_list(await self._request("GET", "/api/v1/pipelines/templates"))

    async def template_get(self, template_id: str) -> dict[str, Any]:
        return _as_dict(await self._request("GET", f"/api/v1/pipelines/templates/{template_id}"))

    async def template_validate(self, graph: dict[str, Any]) -> dict[str, Any]:
        return _as_dict(await self._request("POST", "/api/v1/pipelines/validate", body=graph))

    # --------------------------------------------------------------------- logs

    async def logs_query(
        self,
        *,
        container: str | None = None,
        search: str | None = None,
        since_minutes: int = 60,
        limit: int = 200,
        logql: str | None = None,
    ) -> dict[str, Any]:
        params = _drop_none(
            {
                "container": container,
                "search": search,
                "since_minutes": since_minutes,
                "limit": limit,
                "logql": logql,
            }
        )
        return _as_dict(await self._request("GET", "/api/v1/logs/query", params=params))

    # ------------------------------------------------------------------ watcher

    async def watcher_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return _as_list(await self._request("GET", "/api/v1/watcher/runs", params={"limit": limit}))

    async def watcher_trigger(self, *, timeout: float = 900.0) -> dict[str, Any]:
        # A tick runs its LLM analysis job synchronously inside the request —
        # real ticks take minutes, so the client default timeout would always
        # trip. The tick keeps running server-side either way; a long
        # per-request read timeout lets the caller actually see the summary.
        return _as_dict(await self._request("POST", "/api/v1/watcher/run", timeout=timeout))

    # ------------------------------------------------------------------- cases

    async def case_list(
        self,
        *,
        repo: str | None = None,
        stage: str | None = None,
        origin: str | None = None,
        resolution_path: str | None = None,
        healed_verdict: str | None = None,
        fix_deployed: bool | None = None,
        open_only: bool = False,
        awaiting_decision: bool = False,
        limit: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        params = _drop_none(
            {
                "repo": repo,
                "stage": stage,
                "origin": origin,
                "resolution_path": resolution_path,
                "healed_verdict": healed_verdict,
                "fix_deployed": None if fix_deployed is None else str(fix_deployed).lower(),
                "open_only": "true" if open_only else None,
                "awaiting_decision": "true" if awaiting_decision else None,
                "limit": limit,
                "cursor": cursor,
            }
        )
        return _as_dict(await self._request("GET", "/api/v1/cases", params=params))

    async def case_get(self, case_id: str) -> dict[str, Any]:
        return _as_dict(await self._request("GET", f"/api/v1/cases/{case_id}"))

    async def case_accept(self, case_id: str, *, accepted_by: str) -> dict[str, Any]:
        return _as_dict(
            await self._request("POST", f"/api/v1/cases/{case_id}/accept", body={"accepted_by": accepted_by})
        )

    async def case_close(
        self, case_id: str, *, resolution_path: str | None, note: str | None, closed_by: str
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"closed_by": closed_by}
        if resolution_path:
            body["resolution_path"] = resolution_path
        if note:
            body["note"] = note
        return _as_dict(await self._request("POST", f"/api/v1/cases/{case_id}/close", body=body))

    # --------------------------------------------------------- merge approvals

    async def merge_approval_list(self, *, status: str | None = None, limit: int = 20) -> dict[str, Any]:
        params = {k: v for k, v in {"status": status, "limit": limit}.items() if v is not None}
        return _as_dict(await self._request("GET", "/api/v1/merge-approvals", params=params))

    async def merge_approval_get(self, approval_id: str) -> dict[str, Any]:
        return _as_dict(await self._request("GET", f"/api/v1/merge-approvals/{approval_id}"))

    async def merge_decide(self, approval_id: str, *, decision: str, decided_by: str, chat_id: str) -> dict[str, Any]:
        body = {"decision": decision, "decided_by": decided_by, "chat_id": chat_id}
        return _as_dict(await self._request("POST", f"/api/v1/merge-approvals/{approval_id}/decide", body=body))

    # -------------------------------------------------------------- environment

    def _env_base(self, repo: str | None) -> str:
        if repo is None:
            return "/api/v1/environment/global"
        return f"/api/v1/repos/{_repo_path(repo)}/environment"

    async def env_list(self, *, repo: str | None = None) -> dict[str, Any]:
        return _as_dict(await self._request("GET", self._env_base(repo)))

    async def _env_reveal(self, key: str, repo: str | None) -> str:
        data = _as_dict(await self._request("GET", f"{self._env_base(repo)}/{key}/reveal"))
        value = data.get("value")
        return value if isinstance(value, str) else ""

    async def _env_rebuild(self, repo: str | None, *, drop_key: str | None = None) -> list[dict[str, Any]]:
        """Reconstruct the current variable set with real (unmasked) values.

        The API only exposes full-set PUT, and list responses mask secrets, so a
        single-key upsert/delete must reveal each existing secret before writing
        the set back — otherwise the literal mask would overwrite stored values.
        """
        current = await self.env_list(repo=repo)
        variables: list[dict[str, Any]] = []
        for var in _as_list(current.get("variables", [])):
            key = var.get("key")
            if not isinstance(key, str) or key == drop_key:
                continue
            value = var.get("value", "")
            if var.get("var_type") == "secret" or value == _SECRET_MASK:
                value = await self._env_reveal(key, repo)
            variables.append(
                {
                    "key": key,
                    "value": value,
                    "var_type": var.get("var_type", "config"),
                    "description": var.get("description", ""),
                    "required": var.get("required", False),
                    "scope": var.get("scope", "app"),
                }
            )
        return variables

    async def env_set(
        self,
        key: str,
        value: str,
        *,
        var_type: str = "config",
        description: str = "",
        required: bool = False,
        scope: str = "app",
        repo: str | None = None,
    ) -> dict[str, Any]:
        variables = await self._env_rebuild(repo, drop_key=key)
        variables.append(
            {
                "key": key,
                "value": value,
                "var_type": var_type,
                "description": description,
                "required": required,
                "scope": scope,
            }
        )
        return _as_dict(await self._request("PUT", self._env_base(repo), body={"variables": variables}))

    async def env_delete(self, key: str, *, repo: str | None = None) -> dict[str, Any]:
        if repo is not None:
            await self._request("DELETE", f"{self._env_base(repo)}/{key}")
            return {"key": key, "status": "deleted"}
        current = await self.env_list(repo=None)
        existing = {var.get("key") for var in _as_list(current.get("variables", []))}
        if key not in existing:
            raise Embry0APIError(404, f"Variable '{key}' not found")
        variables = await self._env_rebuild(None, drop_key=key)
        await self._request("PUT", self._env_base(None), body={"variables": variables})
        return {"key": key, "status": "deleted"}

    # ------------------------------------------------------------- repos / qa config (RAV-1251)

    async def org_list(self, *, include_removed: bool = False) -> dict[str, Any]:
        params = {"include_removed": "true"} if include_removed else None
        return _as_dict(await self._request("GET", "/api/v1/orgs", params=params))

    async def org_get(self, owner: str) -> dict[str, Any]:
        return _as_dict(await self._request("GET", f"/api/v1/orgs/{owner}"))

    async def org_register(
        self,
        owner: str,
        forge: str = "github",
        credential_id: str = "",
        display_name: str = "",
        probe: bool = True,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"owner": owner, "forge": forge, "probe": probe}
        if credential_id:
            body["credential_id"] = credential_id
        if display_name:
            body["display_name"] = display_name
        return _as_dict(await self._request("POST", "/api/v1/orgs", body=body))

    async def repo_list(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        params = {"include_inactive": "true"} if include_inactive else None
        return _as_list(await self._request("GET", "/api/v1/repos", params=params))

    async def repo_get(self, repo: str) -> dict[str, Any]:
        return _as_dict(await self._request("GET", f"/api/v1/repos/{_repo_path(repo)}"))

    async def repo_register(self, repo: str, notes: str = "", forge: str = "") -> dict[str, Any]:
        """Register a repo. ``forge`` ("github" | "gitlab") decides the clone
        origin and whether embry0 later opens a PR or an MR; the orchestrator
        validates it against SUPPORTED_FORGES.

        Empty means "unspecified": the key is left out of the body entirely so
        the orchestrator applies its own DEFAULT_FORGE — and refuses with 422
        when it has none, rather than the client guessing github on a tenant
        that deliberately stopped guessing (Spec 1A §3.8)."""
        body: dict[str, Any] = {"repo": _repo_path(repo), "notes": notes}
        if forge:
            body["forge"] = forge
        return _as_dict(await self._request("POST", "/api/v1/repos", body=body))

    async def qa_config_get(self, repo: str) -> str:
        """Raw stored qa.yaml text (the endpoint serves yaml, not JSON)."""
        # Through _request so the hosted MCP caller's bearer overrides the service key (#443).
        return _as_text(await self._request("GET", f"/api/v1/repos/{_repo_path(repo)}/qa-config", raw=True))

    async def qa_config_set(self, repo: str, yaml_text: str) -> dict[str, Any]:
        """Schema-validated upsert of the stored qa.yaml (raw-text PUT)."""
        return _as_dict(
            await self._request(
                "PUT",
                f"/api/v1/repos/{_repo_path(repo)}/qa-config",
                content=yaml_text.encode("utf-8"),
                headers={"Content-Type": "application/yaml"},
            )
        )

    # ----------------------- credentials (spec 2026-09-29 §3.9) — never creates or reveals a secret

    async def credential_list(self) -> list[dict[str, Any]]:
        return _as_list(await self._request("GET", "/api/v1/credentials"))

    async def credential_resolve(self, repo: str, role: str = "forge") -> dict[str, Any]:
        return _as_dict(await self._request("GET", "/api/v1/credentials/resolve", params={"repo": repo, "role": role}))

    async def repo_credentials_get(self, repo: str) -> dict[str, Any]:
        return _as_dict(await self._request("GET", f"/api/v1/repos/{_repo_path(repo)}/credentials"))

    async def credential_bind(
        self, repo: str, credential_id: str, role: str = "forge", pinned: bool = False
    ) -> dict[str, Any]:
        current = await self.repo_credentials_get(repo)
        keep = [
            {
                "credential_id": b["credential_id"],
                "role": b["role"],
                "pinned": bool(b.get("pinned", False)),
                "priority": int(b.get("priority", 0)),
            }
            for b in _as_list(current.get("bindings"))
            if b.get("role") != role
        ]
        body = {"bindings": [*keep, {"credential_id": credential_id, "role": role, "pinned": pinned, "priority": 0}]}
        return _as_dict(await self._request("PUT", f"/api/v1/repos/{_repo_path(repo)}/credentials", body=body))


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
    if isinstance(payload, dict) and "detail" in payload:
        detail = payload["detail"]
        return detail if isinstance(detail, str) else str(detail)
    return str(payload)
