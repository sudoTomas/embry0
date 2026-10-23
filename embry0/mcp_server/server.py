"""MCP server exposing the embry0 orchestrator API as MCP tools.

Run via the ``embry0-mcp`` console script (stdio transport). Configure with
``EMBRY0_API_URL`` (default ``http://localhost:8200``) and ``EMBRY0_API_KEY``.

Destructive or mutating-sensitive tools (``job_cancel``, ``job_resume``,
``job_answer``, ``agent_create``, ``agent_update``, ``agent_delete``,
``agent_reset``, ``env_set``, ``env_delete``, ``merge_decide``, ``case_accept``,
``case_close``, ``repo_register``, ``org_register``, ``org_update``,
``org_webhook_install``, ``org_act``, ``context_audit_run``, ``qa_config_set``,
``credential_bind``, ``host_import_tailnet``,
``skill_source_add``, ``skill_source_update``, ``skill_source_sync``,
``skill_source_remove``, ``inline_skill_put``, ``inline_skill_remove``,
``agent_skill_add``, ``agent_skill_remove``, ``standard_set``,
``standard_audit``) require ``confirm=true``; without it they return an
instructive refusal instead of acting.
"""

from __future__ import annotations

import functools
import importlib.metadata
import re
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from embry0.mcp_server.client import Embry0APIError, Embry0Client

mcp = MCPServer(
    "embry0",
    # mcp 1.x reported its own package version as the server's; mcp 2 reports ""
    # unless told. Keep the 1.x value rather than send an empty version.
    version=importlib.metadata.version("mcp"),
    instructions=(
        "Tools for driving the embry0 issue-to-PR / QA / research automation platform: "
        "issues, jobs (create/wait/logs/deliverables), agent definitions, pipeline "
        "templates, the log watcher, healing cases, merge approvals, per-repo environment "
        "variables, forge credentials (list, resolve, bind), the hosts that run the "
        "code (list, get, probe, import from a tailnet), "
        "organizations (list, get, needs, register, update settings, webhook, actions), "
        "the context audit that finds stale or wrong lines in the files that steer a coding agent "
        "(CLAUDE.md, AGENTS.md, skills, rules) in an organization's repositories: its schedule and "
        "caps are organization settings set with org_update, context_audit_status reads it, and "
        "context_audit_run starts the due audits now, "
        "skills (catalog, sources, inline skills, per-agent add/remove), the tool catalog, "
        "and an owner's standards (get, set, audit a repository against one). "
        "Destructive tools require confirm=true after explicit user approval. "
        'Two lists answer "what is open?", and they are different things: '
        'issue_list(status="open") is the work on the Issues page of the dashboard; '
        "case_list(awaiting_decision=true) is what waits for a person: the watcher's draft "
        "tickets to accept or reject, and organization needs to answer or dismiss. "
        "case_list(open_only=true) is neither: it is every finding not yet closed, "
        "including ones already merged, deployed and verified."
    ),
)

_client: Embry0Client | None = None


def _get_client() -> Embry0Client:
    global _client
    if _client is None:
        _client = Embry0Client()
    return _client


def _confirmation_refusal(tool: str, action: str) -> dict[str, Any]:
    return {
        "error": "confirmation_required",
        "message": (
            f"Refused to {action}: {tool} is a destructive or mutating operation and was "
            f"called without confirm=true. Ask the user to explicitly approve this exact "
            f"action, then call {tool} again with confirm=true. Do not set confirm=true "
            f"on the user's behalf without their approval."
        ),
    }


def _tool[ToolFn: Callable[..., Awaitable[Any]]](fn: ToolFn) -> ToolFn:
    """Register ``fn`` as an MCP tool whose embry0 API errors reach the caller.

    mcp 2 answers any exception other than ``ToolError`` with a bare "Error
    executing tool <name>" and keeps its text in the server log. An
    ``Embry0APIError`` is the API's own answer (401 for a bad key, 403 for a
    missing scope, 404, 409, a validation detail), which the caller needs in
    order to act, so it is re-raised as ``ToolError`` and reaches the client as
    "Error executing tool <name>: embry0 API error <status>: <detail>", exactly
    as under mcp 1. Any other exception stays a crash: logged, not sent.

    The registered wrapper keeps ``fn``'s name, docstring and signature, so the
    tool's schema and description are unchanged; ``fn`` itself is returned, so a
    direct call still raises ``Embry0APIError``.
    """

    @functools.wraps(fn)
    async def call(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except Embry0APIError as exc:
            raise ToolError(str(exc)) from exc

    mcp.add_tool(call)
    return fn


# ------------------------------------------------------------- health / stats


# ------------------------------------------------------- readable job output
#
# The API echoes a job's full `task` prompt back on every read. For a
# multi-paragraph brief that is thousands of tokens of noise per call, and it
# buries the five fields a caller actually reads: status, stage, spend, PR,
# error. These helpers keep the structured payload intact (callers still get
# every field) while adding a one-line `summary`, and trade the echoed prompt
# for a bounded `task_preview`.

_TASK_PREVIEW_CHARS = 220


def _short_pr(url: str | None) -> str:
    """`.../pull/211` -> `#211`; anything else passes through."""
    if not url:
        return "-"
    match = re.search(r"/pull/(\d+)/?$", url)
    return f"#{match.group(1)}" if match else url


def _job_summary(job: dict[str, Any]) -> str:
    """One glanceable line: id, status, stage, spend vs budget, repo, PR, error."""
    budget = (job.get("pipeline_config") or {}).get("max_budget_usd")
    spend = job.get("total_cost_usd") or 0.0
    cost = f"${spend:.2f}" + (f"/${budget:g}" if budget else "")
    stage = job.get("current_stage") or "-"
    parts = [str(job.get("job_id", "?")), str(job.get("status", "?")), f"stage={stage}", cost]
    if job.get("repo"):
        parts.append(str(job["repo"]))
    pr = _short_pr(job.get("pr_url"))
    if pr != "-":
        parts.append(f"PR {pr}")
    error = job.get("error_message")
    if error:
        parts.append(f"ERROR: {str(error)[:120]}")
    return " \u00b7 ".join(parts)


def _compact_job(job: dict[str, Any]) -> dict[str, Any]:
    """Add `summary`; swap the echoed `task` for a bounded preview. A non-dict is returned unchanged."""
    if not isinstance(job, dict):
        return job
    out = dict(job)
    task = out.get("task")
    if isinstance(task, str) and len(task) > _TASK_PREVIEW_CHARS:
        out["task_preview"] = task[:_TASK_PREVIEW_CHARS].rstrip() + "\u2026"
        out["task_chars"] = len(task)
        del out["task"]
    out["summary"] = _job_summary(out)
    return out


def _compact_jobs(envelope: dict[str, Any]) -> dict[str, Any]:
    """Compact each job in a list envelope and add an aligned `board` table. Anything else is returned unchanged."""
    if not isinstance(envelope, dict) or not isinstance(envelope.get("jobs"), list):
        return envelope
    out = dict(envelope)
    out["jobs"] = [_compact_job(j) for j in envelope["jobs"]]
    rows = [
        (
            str(j.get("job_id", "?")),
            str(j.get("status", "?")),
            str(j.get("current_stage") or "-"),
            f"{j.get('total_cost_usd') or 0:.2f}",
            _short_pr(j.get("pr_url")),
        )
        for j in out["jobs"]
        if isinstance(j, dict)
    ]
    if rows:
        head = ("JOB", "STATUS", "STAGE", "COST", "PR")
        widths = [max(len(r[i]) for r in (*rows, head)) for i in range(len(head))]
        fmt = "  ".join(f"{{:<{w}}}" for w in widths)
        out["board"] = "\n".join([fmt.format(*head), *(fmt.format(*r) for r in rows)])
    return out


@_tool
async def health() -> dict[str, Any]:
    """Check embry0 orchestrator health (API reachability + database status)."""
    return await _get_client().health()


@_tool
async def stats() -> dict[str, Any]:
    """Platform statistics: issue/job counts, success rate, costs, queue depth, token usage."""
    return await _get_client().stats()


# ------------------------------------------------------------------- issues


@_tool
async def issue_list(
    status: str | None = None,
    priority: str | None = None,
    repo: str | None = None,
    labels: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List embry0 issues — the dashboard's Issues page (paginated envelope: issues,
    total, offset, limit). With no status every issue is returned, closed ones too.

    For "what is open?" pass status="open": issues that are neither running nor done —
    new ones, and ones whose last job failed (a failed job returns its issue to open so
    it can be retried). Watcher drafts nobody has accepted yet are not issues; they are
    case_list(awaiting_decision=true).

    Filters: status (open|triaging|in_progress|awaiting_input|closed|cancelled),
    priority (critical|high|medium|low), repo ("group/name", or a GitLab subgroup path
    like "group/subgroup/project"), labels, free-text search.
    """
    return await _get_client().issue_list(
        status=status, priority=priority, repo=repo, labels=labels, search=search, limit=limit, offset=offset
    )


@_tool
async def issue_get(issue_id: str) -> dict[str, Any]:
    """Get one issue with its children and associated jobs."""
    return await _get_client().issue_get(issue_id)


@_tool
async def issue_create(
    title: str,
    body: str = "",
    labels: list[str] | None = None,
    priority: str = "medium",
    repo: str | None = None,
    auto_triage: bool = True,
) -> dict[str, Any]:
    """Create an embry0 issue. Labels drive auto-dispatch; with auto_triage=true (default)
    the issue is immediately triaged and a pipeline job may be dispatched from it.
    The result carries job_id (the started job), dispatch_error_code and
    dispatch_error: a non-null dispatch_error_code (ERR_DISPATCH_FAILED) means the
    issue was created but NO job started — report it rather than assuming a run.

    priority: critical|high|medium|low. repo: the path of an onboarded repository —
    "group/name", or a GitLab subgroup path like "group/subgroup/project".
    """
    return await _get_client().issue_create(
        title, body=body, labels=labels, priority=priority, repo=repo, auto_triage=auto_triage
    )


# --------------------------------------------------------------------- jobs


@_tool
async def job_list(
    status: str | None = None,
    repo: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List jobs (paginated envelope: jobs, total, offset, limit). Filter by status
    (pending|running|awaiting_input|paused|completed|failed|cancelled|partial|expired|skipped|pr_merged|pr_closed)
    and/or repo ("group/name", or a GitLab subgroup path like "group/subgroup/project")."""
    return _compact_jobs(await _get_client().job_list(status=status, repo=repo, limit=limit, offset=offset))


@_tool
async def job_get(job_id: str) -> dict[str, Any]:
    """Get one job: status, current stage, costs (with per-agent breakdown), PR URL,
    error details, result summary."""
    return _compact_job(await _get_client().job_get(job_id))


@_tool
async def job_create(
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
    """Create a pipeline job. Returns the created job (poll with job_wait).

    - Code job: task + repo — "group/name", or a GitLab subgroup path like
      "group/subgroup/project" (any depth).
      Pass the project's full path exactly as the forge spells it; never shorten it
      to two segments. The repo must already be registered (repo_register).
    - Research job over a URL: task + context={"type": "http", "url": "https://..."}.
    - Repo-less job: task alone (context defaults to {"type": "none"}).
    - QA pipeline: pipeline="qa" + repo + branch (optional qa overrides object).
    - Onboard: pipeline="onboard" + repo.
    task is required for every pipeline except qa/onboard.
    """
    return await _get_client().job_create(
        task=task,
        repo=repo,
        pipeline=pipeline,
        issue_number=issue_number,
        branch=branch,
        context=context,
        pipeline_template=pipeline_template,
        pipeline_config=pipeline_config,
        sandbox_profile=sandbox_profile,
        max_budget_usd=max_budget_usd,
        additional_context=additional_context,
        agent_models=agent_models,
        qa=qa,
    )


@_tool
async def job_wait(job_id: str, timeout: float = 600.0, poll_interval: float = 5.0) -> dict[str, Any]:
    """Wait server-side until a job reaches a terminal status (completed|failed|cancelled|
    partial|expired|skipped) or timeout (seconds) elapses. On timeout the current job state is
    returned with wait_timed_out=true. Use this instead of polling job_get in a loop."""
    return await _get_client().job_wait(job_id, timeout=timeout, poll_interval=poll_interval)


@_tool
async def job_resume(job_id: str, choice: str, guidance: str = "", confirm: bool = False) -> dict[str, Any]:
    """Resume a paused job. choice: continue_retrying | merge_as_is | abandon; optional
    guidance text for the agents. Requires confirm=true (mutates job state)."""
    if not confirm:
        return _confirmation_refusal("job_resume", f"resume job {job_id} with choice={choice!r}")
    return await _get_client().job_resume(job_id, choice, guidance=guidance)


@_tool
async def job_inputs(job_id: str) -> list[dict[str, Any]]:
    """List agent questions (inputs) for a job: question text, asking agent, importance,
    status (pending|answered|auto_answered|skipped), and any recorded answer. Use on an
    awaiting_input job to find the pending question ids before calling job_answer."""
    return await _get_client().job_inputs(job_id)


@_tool
async def job_answer(job_id: str, input_id: str, answer: str, confirm: bool = False) -> dict[str, Any]:
    """Answer a pending agent question on a job. When no blocking questions remain the
    workflow resumes from its checkpoint with the accumulated Q&A as context. The Q&A pair
    is also recorded as a comment on the linked Linear issue. Requires confirm=true
    (mutates job state) — echo the exact answer to the user for approval first."""
    if not confirm:
        return _confirmation_refusal("job_answer", f"answer input {input_id} on job {job_id}")
    return await _get_client().job_input_answer(job_id, input_id, answer)


@_tool
async def job_cancel(job_id: str, confirm: bool = False) -> dict[str, Any]:
    """Cancel a pending/running/awaiting_input/paused job. Requires confirm=true
    (destructive: the job's work is abandoned)."""
    if not confirm:
        return _confirmation_refusal("job_cancel", f"cancel job {job_id}")
    return await _get_client().job_cancel(job_id)


@_tool
async def job_logs(job_id: str, tail: int = 50) -> dict[str, Any]:
    """Pipeline event log for a job ({"events": [...]}). tail limits to the most recent
    N events (0 = all — can be large)."""
    data = await _get_client().job_logs(job_id)
    events = data.get("events")
    if tail > 0 and isinstance(events, list) and len(events) > tail:
        return {"events": events[-tail:], "truncated": True, "total_events": len(events)}
    return data


@_tool
async def job_deliverables(job_id: str) -> list[dict[str, Any]]:
    """List a job's deliverables. Report/text deliverables carry their content inline;
    artifact deliverables get a download_url (binary passthrough endpoint)."""
    return await _get_client().job_deliverables(job_id)


# ------------------------------------------------------------------- agents


@_tool
async def agent_list() -> list[dict[str, Any]]:
    """List all agent definitions (type, description, model, tools, skills, execution mode).

    ``model`` is the stored one. ``effective_model`` and ``effective_provider``
    are what a job that names no model runs it on: a deployment default model
    replaces the stored one (``effective_model_source`` says which applied).
    """
    return await _get_client().agent_list()


@_tool
async def agent_get(agent_type: str) -> dict[str, Any]:
    """Get one agent definition, including its system prompt and MCP server config."""
    return await _get_client().agent_get(agent_type)


@_tool
async def agent_update(
    agent_type: str,
    description: str | None = None,
    model: str | None = None,
    tools: list[str] | None = None,
    skills: list[str] | None = None,
    system_prompt: str | None = None,
    execution_mode: str | None = None,
    auth_mode: str | None = None,
    mcp_servers: dict[str, Any] | None = None,
    display_name: str | None = None,
    avatar: str | None = None,
    color: str | None = None,
    tagline: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Update an agent definition (only provided fields change). execution_mode: sdk|cli;
    auth_mode: api_key|oauth; avatar and color are roster and palette ids. skills: only names the agent
    does not have yet are checked, and a bare name (no colon) is the target repository's own skill.
    Requires confirm=true (changes live pipeline behavior)."""
    if not confirm:
        return _confirmation_refusal("agent_update", f"update agent definition {agent_type!r}")
    return await _get_client().agent_update(
        agent_type,
        description=description,
        model=model,
        tools=tools,
        skills=skills,
        system_prompt=system_prompt,
        execution_mode=execution_mode,
        auth_mode=auth_mode,
        mcp_servers=mcp_servers,
        display_name=display_name,
        avatar=avatar,
        color=color,
        tagline=tagline,
    )


@_tool
async def agent_create(
    agent_type: str,
    description: str,
    model: str,
    tools: list[str] | None = None,
    skills: list[str] | None = None,
    system_prompt: str = "",
    execution_mode: str | None = None,
    auth_mode: str | None = None,
    mcp_servers: dict[str, Any] | None = None,
    display_name: str = "",
    avatar: str = "golem",
    color: str = "slate",
    tagline: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    """Create a custom agent definition. avatar is one of the roster ids and color one of the palette ids
    (see GET /agents for the builtins' values). Requires confirm=true (adds a runnable agent)."""
    if not confirm:
        return _confirmation_refusal("agent_create", f"create agent definition {agent_type!r}")
    return await _get_client().agent_create(
        agent_type,
        description=description,
        model=model,
        tools=tools,
        skills=skills,
        system_prompt=system_prompt,
        execution_mode=execution_mode,
        auth_mode=auth_mode,
        mcp_servers=mcp_servers,
        display_name=display_name,
        avatar=avatar,
        color=color,
        tagline=tagline,
    )


@_tool
async def agent_delete(agent_type: str, confirm: bool = False) -> dict[str, Any]:
    """Delete a custom agent definition (builtins answer 403). Requires confirm=true (destructive)."""
    if not confirm:
        return _confirmation_refusal("agent_delete", f"delete agent definition {agent_type!r}")
    return await _get_client().agent_delete(agent_type)


@_tool
async def agent_reset(agent_type: str, confirm: bool = False) -> dict[str, Any]:
    """Reset an agent definition to its built-in defaults, discarding customizations.
    Requires confirm=true (destructive)."""
    if not confirm:
        return _confirmation_refusal("agent_reset", f"reset agent definition {agent_type!r} to defaults")
    return await _get_client().agent_reset(agent_type)


@_tool
async def agent_skill_add(agent_type: str, skill_name: str, confirm: bool = False) -> dict[str, Any]:
    """Add one skill (a `plugin:skill` name from skill_list, or a bare name for the target repository's own
    skill, which is not checked) to an agent definition; adding one it already has is a no-op. An unknown
    name answers 422 with the closest known names; one from a source not ready yet is added and listed in
    `warnings`. Returns the agent. Requires confirm=true (changes live pipeline behavior)."""
    if not confirm:
        return _confirmation_refusal("agent_skill_add", f"add skill {skill_name!r} to agent {agent_type!r}")
    return await _get_client().agent_skill_add(agent_type, skill_name)


@_tool
async def agent_skill_remove(agent_type: str, skill_name: str, confirm: bool = False) -> dict[str, Any]:
    """Remove one skill from an agent definition; removing one it does not have is a no-op.
    Returns the agent. Requires confirm=true (changes live pipeline behavior)."""
    if not confirm:
        return _confirmation_refusal("agent_skill_remove", f"remove skill {skill_name!r} from agent {agent_type!r}")
    return await _get_client().agent_skill_remove(agent_type, skill_name)


# ------------------------------------------------------------------- skills


@_tool
async def skill_list() -> list[dict[str, Any]]:
    """List the skill catalog: every `plugin:skill` name the skill sources provide, with its source,
    description, and whether it is available (its source is ready and enabled)."""
    return await _get_client().skill_list()


@_tool
async def skill_source_list() -> list[dict[str, Any]]:
    """List the skill sources (git, path, inline) with status, resolved sha, plugin name, synced_at and
    the last sync error."""
    return await _get_client().skill_source_list()


@_tool
async def skill_source_add(
    source_id: str,
    kind: str,
    url: str = "",
    ref: str = "",
    enabled: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Add a skill source and start its first sync; it answers in status `pending`. kind: git|path.
    git: url is a public https repository and ref a tag, branch or commit sha (the pin is the trust
    decision); path: url is a directory inside the orchestrator container. Inline skills go through
    inline_skill_put. Requires confirm=true (code the agents will load)."""
    if not confirm:
        return _confirmation_refusal("skill_source_add", f"add {kind} skill source {source_id!r}")
    return await _get_client().skill_source_add(source_id, kind=kind, url=url, ref=ref, enabled=enabled)


@_tool
async def skill_source_get(source_id: str) -> dict[str, Any]:
    """One skill source: kind, url, ref, resolved sha, status and error, and the skills it lists."""
    return await _get_client().skill_source_get(source_id)


@_tool
async def skill_source_update(
    source_id: str, ref: str | None = None, enabled: bool | None = None, confirm: bool = False
) -> dict[str, Any]:
    """Change a skill source's ref (git sources; starts a sync) or enable or disable it. Only the given
    fields change; built-in sources accept enabled only. Requires confirm=true (changes what agents load)."""
    if not confirm:
        return _confirmation_refusal("skill_source_update", f"update skill source {source_id!r}")
    return await _get_client().skill_source_update(source_id, ref=ref, enabled=enabled)


@_tool
async def skill_source_sync(source_id: str, confirm: bool = False) -> dict[str, Any]:
    """Re-sync a skill source in the background (409 while a sync of it runs); watch skill_source_list
    for the result. Requires confirm=true (replaces the tree new sandboxes mount)."""
    if not confirm:
        return _confirmation_refusal("skill_source_sync", f"re-sync skill source {source_id!r}")
    return await _get_client().skill_source_sync(source_id)


@_tool
async def skill_source_remove(source_id: str, confirm: bool = False) -> dict[str, Any]:
    """Delete a skill source and its stored tree. Builtins answer 403; a source whose skills an agent or
    template still names answers 409 listing them. Requires confirm=true (destructive)."""
    if not confirm:
        return _confirmation_refusal("skill_source_remove", f"delete skill source {source_id!r}")
    return await _get_client().skill_source_remove(source_id)


@_tool
async def inline_skill_list() -> list[dict[str, Any]]:
    """The inline skills (name, description, body, timestamps); agents name each one `local:<name>`."""
    return await _get_client().inline_skill_list()


@_tool
async def inline_skill_put(name: str, description: str, body: str, confirm: bool = False) -> dict[str, Any]:
    """Create or replace an inline skill, published as `local:<name>`. body is the SKILL.md content
    below the frontmatter. Re-syncs the `local` source. Requires confirm=true (changes what agents load)."""
    if not confirm:
        return _confirmation_refusal("inline_skill_put", f"write inline skill {name!r}")
    return await _get_client().inline_skill_put(name, description=description, body=body)


@_tool
async def inline_skill_remove(name: str, confirm: bool = False) -> dict[str, Any]:
    """Delete an inline skill; 409 while an agent or template names `local:<name>`.
    Requires confirm=true (destructive)."""
    if not confirm:
        return _confirmation_refusal("inline_skill_remove", f"delete inline skill {name!r}")
    return await _get_client().inline_skill_remove(name)


# -------------------------------------------------------------------- tools


@_tool
async def tool_list() -> list[dict[str, Any]]:
    """List the tool names an agent definition can name: Claude Code builtins, each browser backend's
    MCP tools (source `mcp:<backend>`) and the log tool (source `mcp:context`)."""
    return await _get_client().tool_list()


# ---------------------------------------------------------------- templates


@_tool
async def template_list() -> list[dict[str, Any]]:
    """List pipeline templates (custom agent graphs)."""
    return await _get_client().template_list()


@_tool
async def template_get(template_id: str) -> dict[str, Any]:
    """Get one pipeline template, including its graph definition (each step names its model in config.model)."""
    return await _get_client().template_get(template_id)


@_tool
async def template_validate(graph: dict[str, Any]) -> dict[str, Any]:
    """Validate a pipeline graph definition without saving it.
    Returns {"valid": bool, "errors": [...]}."""
    return await _get_client().template_validate(graph)


# ------------------------------------------------------------------ watcher


@_tool
async def logs_query(
    container: str | None = None,
    search: str | None = None,
    since_minutes: int = 60,
    limit: int = 200,
    logql: str | None = None,
) -> dict[str, Any]:
    """Read production container logs (Loki), newest first — for on-demand bug
    investigation. container: Docker container name substring (e.g. "api");
    search: regex line filter (e.g. "(?i)error|exception"); since_minutes: lookback
    window (max 1440); limit: max lines (default 200, max 1000); logql: raw LogQL
    override for advanced queries. Keep limit modest — lines land in your context.
    Needs an operate-scope key or above. Lines come back redacted (secrets, e-mail
    addresses and the operator's personal-data fields replaced) and cut at 2000
    characters."""
    return await _get_client().logs_query(
        container=container, search=search, since_minutes=since_minutes, limit=limit, logql=logql
    )


@_tool
async def watcher_runs(limit: int = 50, stack: str | None = None) -> list[dict[str, Any]]:
    """List recent log-watcher runs (anomaly scans that file draft tickets), newest
    first. stack (the watched repo, "group/name" or a GitLab subgroup path) narrows
    to one watched stack; omitted, every stack's runs are interleaved and each
    row's repo names its stack."""
    return await _get_client().watcher_runs(limit=limit, stack=stack)


@_tool
async def watcher_trigger(timeout: float = 900.0, stack: str | None = None) -> dict[str, Any]:
    """Trigger a log-watcher tick now and return its summary. Safe: proposal caps,
    dedup, and the human gate still apply. stack (a watched repo, as watcher_runs
    names it) ticks that stack; omitted while several are watched, every stack
    ticks and the summary lists each one's result under "stacks" (each may run
    one paid analysis). A tick runs an LLM analysis job synchronously and takes
    minutes — timeout (seconds) bounds the wait; the tick keeps running
    server-side even if it elapses."""
    return await _get_client().watcher_trigger(timeout=timeout, stack=stack)


# -------------------------------------------------------------------- cases


@_tool
async def case_list(
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
    """List healing cases — one lifecycle record per finding, newest first (envelope:
    cases, total, limit, next_cursor — pass next_cursor back as cursor for the next page). A case links the watcher run, tracker
    ticket, embry0 issue, PRs, merge, deploy marker and healed verdict for one finding.
    fix_deployed says whether a merged fix is running; do NOT infer that from stage — a
    case can be verified or closed with no fix at all (self_resolved, non_code).

    Filters: repo ("group/name"), stage (detected|tracked|accepted|pr_open|merged|
    deployed|verified|closed), origin (watcher|human|webhook|alert|ci|org), resolution_path
    (embry0|out_of_band|cancelled|non_code|self_resolved|abandoned), healed_verdict
    (pending|healed|not_healed|inconclusive|regressed), fix_deployed (true/false — use
    THIS, not stage=verified, to find findings a shipped fix healed), awaiting_decision
    (use THIS for "what needs a decision?": watcher drafts with a ticket that nobody has
    accepted or rejected, findings whose shipped fix did not heal them, sent back for a
    retry nobody has accepted yet, and organization needs waiting for a person, origin
    org: org_needs reads them, closing the case dismisses one, none is ever accepted),
    open_only (every case whose stage is not closed — that
    includes merged, deployed and verified cases, alerts and findings with no ticket, so
    it answers neither "what is open on the dashboard?", which is
    issue_list(status="open"), nor "what needs a decision?"). limit max 200."""
    return await _get_client().case_list(
        repo=repo,
        stage=stage,
        origin=origin,
        resolution_path=resolution_path,
        healed_verdict=healed_verdict,
        fix_deployed=fix_deployed,
        open_only=open_only,
        awaiting_decision=awaiting_decision,
        limit=limit,
        cursor=cursor,
    )


@_tool
async def case_get(case_id: str) -> dict[str, Any]:
    """Get one healing case ("hc-...") with its linked jobs, every PR it had, and full stage history."""
    return await _get_client().case_get(case_id)


# ---------------------------------------------------------- merge approvals


@_tool
async def case_accept(case_id: str, accepted_by: str, confirm: bool = False) -> dict[str, Any]:
    """Accept a watcher finding ("hc-...") and dispatch the fix pipeline on its draft
    ticket — what adding the trigger label in the tracker does. accepted_by: the name of
    the person who said yes.

    Works for a GitHub draft and for a Linear draft. For a Linear draft embry0 puts the
    trigger label on the ticket itself and then dispatches it, so the ticket shows the
    acceptance; nobody needs to label it by hand. A GitLab draft is still accepted by
    adding the label in GitLab (409 here). A finding whose fix did not heal it and that
    was sent back for a retry is accepted the same way, whatever its tracker: the retry
    runs on the issue of the failed attempt, and the ticket is not touched.

    embry0 refuses (409/403) anything that is not a watcher finding at stage "tracked"
    on a stack this orchestrator watches, and a Linear ticket that cannot take a job
    (finished, already dispatched, or belonging to another repo than the finding's). An
    error says what was done to the ticket; read it out — in the rare case where the
    label went on and could not be taken off again it asks for the label to be removed
    in Linear. Returns the job_id it started; there is no success without one. Requires
    confirm=true."""
    if not confirm:
        return _confirmation_refusal("case_accept", f"accept finding {case_id} and dispatch its fix")
    return await _get_client().case_accept(case_id, accepted_by=accepted_by)


@_tool
async def case_close(
    case_id: str,
    closed_by: str,
    resolution_path: str | None = None,
    note: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Close a healing case ("hc-...") a human has decided is settled — the mirror of
    case_accept, for a finding nobody will ship a fix for. closed_by: the name of the
    person who decided. note: why, for the ledger.

    resolution_path says what really happened and is required unless the case already
    carries one: "out_of_band" (fixed outside embry0), "abandoned" (accepted, looked at,
    given up), "non_code" (nothing to change in code), "cancelled" (the finding was
    withdrawn), "self_resolved" (it stopped by itself), "embry0" (embry0's own merge).
    embry0 refuses (409) a case that is already closed. Closing is terminal but keeps
    history: a later recurrence opens a new case pointing at this one. A case with
    origin "org" is an organization need: closing it dismisses the need and needs an
    admin-scope key. Requires confirm=true."""
    if not confirm:
        return _confirmation_refusal("case_close", f"close case {case_id} as {resolution_path or 'its existing path'}")
    return await _get_client().case_close(case_id, resolution_path=resolution_path, note=note, closed_by=closed_by)


@_tool
async def merge_approval_list(status: str | None = None, limit: int = 20) -> dict[str, Any]:
    """List "merge this PR?" asks, newest first. status: pending|approved|rejected|merged|
    failed|stale|closed|expired (closed = the PR was merged or closed on GitHub; expired =
    nobody answered in time). `mode` in the envelope says whether a decision would really merge (on)
    or only record "would merge" (dry_run)."""
    return await _get_client().merge_approval_list(status=status, limit=limit)


@_tool
async def merge_approval_get(approval_id: str) -> dict[str, Any]:
    """Get one merge ask ("mrg-..."): the PR, the head commit it was asked about, the
    denylisted paths and verification verdict shown with it, and its status."""
    return await _get_client().merge_approval_get(approval_id)


@_tool
async def merge_decide(
    approval_id: str, decision: str, decided_by: str, chat_id: str, confirm: bool = False
) -> dict[str, Any]:
    """Relay a human's answer to a "merge this PR?" ask. decision: "approve" or "reject".
    decided_by: the name of the person who answered. chat_id: the id of the chat the
    answer came from, exactly as you received it — embry0 refuses answers from any chat
    but the approval chat.

    embry0 then re-checks everything itself: the PR head must still be the commit that
    was asked about (otherwise the ask goes stale and a fresh one is posted), every CI
    check must have passed, the verification gate must not have found new failures, and
    GitHub must say the PR merges cleanly. A refused approval comes back with
    outcome="refused" and the reason; the same ask can be approved again later. outcome
    "would_merge" means dry-run mode: nothing was merged. Requires confirm=true."""
    if not confirm:
        return _confirmation_refusal("merge_decide", f"{decision} merge approval {approval_id}")
    return await _get_client().merge_decide(approval_id, decision=decision, decided_by=decided_by, chat_id=chat_id)


# -------------------------------------------------------------- environment


@_tool
async def env_list(repo: str | None = None) -> dict[str, Any]:
    """List environment variables — global set by default, or a repo's set when repo
    ("group/name", or a GitLab subgroup path like "group/subgroup/project") is given.
    Secret values are always masked as "****"; there is no tool to read secret values.
    A secret's masked_preview shows at most its last 2 characters, and only for a
    secret of 12 characters or more."""
    return await _get_client().env_list(repo=repo)


@_tool
async def env_set(
    key: str,
    value: str,
    var_type: str = "config",
    description: str = "",
    required: bool = False,
    scope: str = "app",
    repo: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Create or update one environment variable (write-only: values are never readable
    back through MCP). var_type: config|secret; scope: app|qa (qa keys must start with
    QA_); repo=None targets the global set. Requires confirm=true."""
    if not confirm:
        scope_desc = f"repo {repo!r}" if repo else "the global scope"
        return _confirmation_refusal("env_set", f"set environment variable {key!r} in {scope_desc}")
    return await _get_client().env_set(
        key, value, var_type=var_type, description=description, required=required, scope=scope, repo=repo
    )


@_tool
async def env_delete(key: str, repo: str | None = None, confirm: bool = False) -> dict[str, Any]:
    """Delete one environment variable from the global set (repo=None) or a repo's set.
    Requires confirm=true (destructive: secret values are not recoverable)."""
    if not confirm:
        scope_desc = f"repo {repo!r}" if repo else "the global scope"
        return _confirmation_refusal("env_delete", f"delete environment variable {key!r} from {scope_desc}")
    return await _get_client().env_delete(key, repo=repo)


@_tool
async def org_list(include_removed: bool = False) -> dict[str, Any]:
    """List the organizations embry0 maintains: forge owners with the credential
    embry0 acts with and what the probe found it can do there (ADR 0010)."""
    return await _get_client().org_list(include_removed=include_removed)


@_tool
async def org_get(owner: str) -> dict[str, Any]:
    """One organization by owner: forge, credential, probe result, repo count, status."""
    return await _get_client().org_get(owner)


@_tool
async def org_needs(owner: str) -> dict[str, Any]:
    """What embry0 still needs to maintain one organization, from its newest
    discovery run: each need's kind (not_registered, no_webhook,
    onboarding_candidate, not_watched, no_qa_config, access_limited,
    env_values_needed, no_log_source, no_deploy_marker, count_gap,
    hook_origin_unknown), the repositories it covers, the action that answers
    it, its status (open, done, dismissed) and its healing case. Read-only."""
    return await _get_client().org_needs(owner)


@_tool
async def org_register(
    owner: str,
    forge: str = "github",
    credential_id: str = "",
    display_name: str = "",
    probe: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Register a forge owner (a GitHub organization or user, or a GitLab group)
    so embry0 can discover and maintain every repository under it. With
    ``probe`` on (default) the forge is asked what the credential can do there
    first; an owner the credential cannot list is refused. ``credential_id``
    names a forge_token in the registry; empty uses the environment token for
    that owner. Idempotent. Requires confirm=true (creates platform state)."""
    if not confirm:
        return _confirmation_refusal("org_register", f"register organization {owner!r} on {forge}")
    return await _get_client().org_register(
        owner, forge=forge, credential_id=credential_id, display_name=display_name, probe=probe
    )


@_tool
async def org_update(
    owner: str,
    display_name: str | None = None,
    settings: dict[str, Any] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Change an organization's display name or settings. Settings merge: the keys given are set and
    the rest stay; a key set to null turns it off or hands it back to the deployment's default. The
    keys an organization accepts, and no others:
    auto_register (boolean): a webhook delivery for an unregistered repository under the owner
    registers it first.
    rediscover_hours (integer 1 to 168, or null for off): rediscover the owner on a schedule.
    context_audit_days (integer 1 to 365, or null for off): audit the files that steer a coding agent
    (CLAUDE.md, AGENTS.md, skills, rules) in each registered repository on a schedule, when they
    change and this many days after the newest audit once the branch moved.
    context_audit_budget_usd (number more than 0 and at most 50, or null for the deployment's
    default): the hard cap in USD of each context-audit job started for the owner.
    context_audit_max_jobs_per_tick (integer 0 to 50, or null for the deployment's default): the most
    context-audit jobs one tick of the schedule (every 15 minutes) starts for the owner; 0 starts none.
    An unknown key, a wrong type or a value out of range is refused. Returns the organization. Needs
    an admin-scope key. Requires confirm=true (changes what embry0 does, and may spend, for every
    repository under the owner)."""
    if not confirm:
        changed = sorted(settings or {}) + (["display_name"] if display_name is not None else [])
        return _confirmation_refusal("org_update", f"update organization {owner!r}: {', '.join(changed) or 'nothing'}")
    return await _get_client().org_update(owner, display_name=display_name, settings=settings)


@_tool
async def context_audit_status(owner: str) -> dict[str, Any]:
    """What the context audit reads for one GitHub organization: its context_audit_days (null is
    off) and, for each active registered repository with files that steer a coding agent (CLAUDE.md,
    AGENTS.md, skills, rules), how many there are, their fingerprint, the head read, the newest
    context-audit job (job_id, status, created_at), whether the schedule would start one now (due)
    and why (reason: first, changed, aged, live, current or off). unread lists the repositories the
    forge did not answer for, with why. Reads the forge and starts nothing, whatever the setting
    says. Read-only."""
    return await _get_client().context_audit_status(owner)


@_tool
async def context_audit_run(
    owner: str,
    repo: str | None = None,
    force: bool = False,
    limit: int | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Start an organization's due context audits now, whether or not its schedule is on, through the
    schedule's own start, so the schedule counts them and does not repeat them. Without repo: every
    due repository, at most limit (1 to 50; default the organization's
    context_audit_max_jobs_per_tick). With repo ("group/name", one of the owner's active registered
    repositories): that one when it is due, or whatever the due rule says with force=true; force
    needs repo. With the schedule off no interval applies: due is no audit yet, or changed files.
    Never while a context-audit job of the repository is live, force or not. Each job's hard cap is
    the organization's context_audit_budget_usd, else the deployment's default. Returns started (repo, job_id, reason) and
    skipped (repo, reason: live, current, limit, refused, gated, unread or no_instruction_files, and
    detail; gated means the budget gate refused, no job was written and the repository stays due);
    follow a job with job_wait and job_deliverables. Use this rather than
    job_create(pipeline_template="context-audit"): that job records no fingerprint, so the schedule
    cannot compare a later change with it. Needs an operate-scope key, and a key limited to some
    repositories must name one with repo. Requires confirm=true (starts paid jobs)."""
    if not confirm:
        action = f"start the context audit of {repo!r}" if repo else f"start the due context audits under {owner!r}"
        return _confirmation_refusal("context_audit_run", action)
    return await _get_client().context_audit_run(owner, repo=repo, force=force, limit=limit)


@_tool
async def host_list(source: str | None = None, include_removed: bool = False) -> dict[str, Any]:
    """List the hosts: the machines that run the code embry0 maintains (ADR 0013). Each has a
    name, an address, a transport (tailnet, ssh or http), roles, a log source, tags, a status
    (active, unreachable or removed), when it was last seen, and the repositories it serves
    with the role it plays for each. source: "manual" or "tailnet" keeps only hosts added
    that way. include_removed=true also shows removed hosts."""
    return await _get_client().host_list(source=source, include_removed=include_removed)


@_tool
async def host_get(host_id: str) -> dict[str, Any]:
    """One host ("host-..."): address, transport, roles, log source, status, last seen, and
    the repositories it serves."""
    return await _get_client().host_get(host_id)


@_tool
async def host_probe(host_id: str, credential_id: str = "") -> dict[str, Any]:
    """Check whether a host ("host-...") is reachable over its transport, and record the answer
    as its status and last-seen time. A tailnet host is looked up in the tailnet's device list
    by address or name, and is reachable when the tailnet reports it connected; an ssh or http
    host gets a TCP connect to its port, with nothing sent. A tailnet is read with the
    tailnet_api_key ``credential_id``, or the one bound at global scope with role tailnet when
    empty. Returns the probe (reachable, detail) and the updated host. Changes nothing a person
    configured. Never returns the key."""
    return await _get_client().host_probe(host_id, credential_id=credential_id)


@_tool
async def host_import_tailnet(credential_id: str = "", confirm: bool = False) -> dict[str, Any]:
    """Create or update one host per device of the tailnet, read with a tailnet_api_key
    credential: ``credential_id``, or the one bound at global scope with role tailnet when
    empty. A new device becomes a tailnet host; a host an earlier import made gets its address,
    status and last-seen time refreshed; a manual host with the same name, or a host a person
    removed, is skipped. Idempotent: a second import reports every host unchanged. Never
    returns the key. Requires confirm=true (creates platform state)."""
    if not confirm:
        return _confirmation_refusal("host_import_tailnet", "import hosts from the tailnet's devices")
    return await _get_client().host_import_tailnet(credential_id=credential_id)


@_tool
async def standard_get(owner: str, kind: str) -> dict[str, Any]:
    """The active version of a forge owner's standard of one ``kind`` ("design") (ADR 0014): its
    schema (the token roles, type scale and component rules every standard of the kind shares), its
    values (this owner's fill of the roles), prose, references and version number. 404s when the
    owner holds none of that kind. Read-only."""
    return await _get_client().standard_get(owner, kind)


@_tool
async def standard_set(
    owner: str,
    kind: str,
    schema: dict[str, Any],
    values: dict[str, Any],
    prose: str = "",
    references: list[str] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Write a new version of a forge owner's standard of one ``kind`` ("design") and supersede the
    active one (ADR 0014). The call is the whole version, so read it with standard_get first and
    send back what you keep. ``schema`` holds ``roles`` (role name to an object describing the
    role), ``type_scale`` (step name to an object) and ``rules`` (objects with an ``id`` and a
    ``text``); every key of ``values`` must be a role the schema declares. ``references`` are
    repositories or URLs where the standard is held best. Needs an admin-scope key. Requires
    confirm=true (changes what every repository under the owner is audited against)."""
    if not confirm:
        return _confirmation_refusal("standard_set", f"write a new version of the {kind} standard of {owner!r}")
    return await _get_client().standard_set(
        owner, kind, schema=schema, values=values, prose=prose, references=list(references or [])
    )


@_tool
async def standard_audit(owner: str, kind: str, repo: str, confirm: bool = False) -> dict[str, Any]:
    """Start a read-only audit job of one repository under ``owner`` ("group/name", or a GitLab
    subgroup path like "group/subgroup/project") against the owner's active standard of ``kind``
    ("design"). Refused unless an admin put the repository in mode advise or enforce, the owner holds
    an active standard of the kind, its pipeline template (design-audit) exists, and no audit of the
    repository is pending or running: a retry while one runs is refused with that job's id, which
    is the one to follow. When the job
    completes, its report's score (0 to 100) and finding count are stored on the repository's
    standards state, which repo_get shows. Returns the job id: follow it with job_wait and
    job_deliverables. Requires confirm=true (starts a paid job)."""
    if not confirm:
        return _confirmation_refusal("standard_audit", f"start a {kind} audit of {repo!r} against {owner!r}")
    return await _get_client().standard_audit(owner, kind, repo)


@_tool
async def org_webhook_install(owner: str, confirm: bool = False) -> dict[str, Any]:
    """Install one GitHub webhook on the organization itself, firing for every
    repository under ``owner`` (present and future) and pointing at this
    deployment's public webhook route, signed with its webhook secret. Refused
    unless the stored credential was probed able to manage hooks. Idempotent:
    a hook already pointing here answers outcome=already. Requires confirm=true
    (writes to the forge)."""
    if not confirm:
        return _confirmation_refusal("org_webhook_install", f"install an organization webhook on {owner!r}")
    return await _get_client().org_webhook_install(owner)


@_tool
async def org_act(
    owner: str, action: str, repos: list[str], dry_run: bool = False, force: bool = False, confirm: bool = False
) -> dict[str, Any]:
    """Run one organization action over repositories discovery saw under
    ``owner``: register (the repo registry), hook (a forge webhook pointing at
    WEBHOOK_PUBLIC_URL), onboard (the onboard job, for registered repositories
    with a boot hint), watch (a disabled watcher stack) or deactivate (the
    registry row goes inactive: the answer to a repository that left the
    listing). One outcome per repository: done, already (the state exists),
    skipped (detail says why) or failed. Idempotent: the same call twice
    reports already for every repository the first one changed, and
    changes nothing. dry_run=true reports what would happen without touching
    anything. force=true lets onboard start another job where the last one
    completed without storing a QA config. A call that changed anything
    starts a rediscovery (``rediscovery`` in the result). Requires
    confirm=true for every call, dry runs included, like every other gated
    tool: a real call creates registrations, webhooks, jobs or watcher
    stacks, or deactivates registrations."""
    if not confirm:
        verb = "preview" if dry_run else "run"
        return _confirmation_refusal("org_act", f"{verb} {action} on {len(repos)} repositories under {owner!r}")
    return await _get_client().org_act(owner, action, repos, dry_run=dry_run, force=force)


@_tool
async def repo_list(include_inactive: bool = False) -> list[dict[str, Any]]:
    """List registered target repos (the embry0 repo registry) with a
    has_qa_config flag per repo. include_inactive=true also shows deactivated repos."""
    return await _get_client().repo_list(include_inactive=include_inactive)


@_tool
async def repo_get(repo: str) -> dict[str, Any]:
    """Unified view of one registered repo — "group/name", or a GitLab
    subgroup path like "group/subgroup/project": registry row, has_qa_config
    + last config update, preferences (sandbox profile etc.), env
    var/secret counts, and the hosts its code runs on. 404s when the repo is
    not registered."""
    return await _get_client().repo_get(repo)


@_tool
async def repo_register(repo: str, notes: str = "", forge: str = "", confirm: bool = False) -> dict[str, Any]:
    """Register a repo in the embry0 registry — "group/name", or a GitLab
    subgroup path like "group/subgroup/project". ``forge`` is "github" or
    "gitlab"; it decides the clone origin for every sandbox and whether
    embry0 opens a PR or an MR. Leave it empty to inherit the server's
    DEFAULT_FORGE — a server configured with no default refuses the
    registration rather than guessing, so pass it explicitly there.
    Idempotent. Requires confirm=true (creates platform state)."""
    if not confirm:
        return _confirmation_refusal(
            "repo_register", f"register repo {repo!r} on {forge or 'the server default forge'}"
        )
    return await _get_client().repo_register(repo, notes=notes, forge=forge)


@_tool
async def qa_config_get(repo: str) -> dict[str, Any]:
    """The stored qa.yaml v2 document for a repo ("group/name", or a GitLab subgroup
    path like "group/subgroup/project") — the DB is the source of truth;
    target repos carry no config file."""
    text = await _get_client().qa_config_get(repo)
    return {"repo": repo, "yaml_text": text}


@_tool
async def qa_config_set(repo: str, yaml_text: str, confirm: bool = False) -> dict[str, Any]:
    """Create or replace a repo's stored qa.yaml v2 document (schema-validated
    server-side; auto-registers the repo). Requires confirm=true — this changes
    how every future QA run judges the repo."""
    if not confirm:
        return _confirmation_refusal("qa_config_set", f"replace the stored QA config for {repo!r}")
    return await _get_client().qa_config_set(repo, yaml_text)


@_tool
async def credential_list() -> list[dict[str, Any]]:
    """Registered forge accounts (id, label, provider, host, masked preview, status, binding count).
    Never returns a secret."""
    return await _get_client().credential_list()


@_tool
async def credential_resolve(repo: str, role: str = "forge") -> dict[str, Any]:
    """Dry run: which account a job on this repo would use for `role`, with the precedence
    trace (repo > owner > host > global > env). Never returns a secret.

    repo is "group/name", or a GitLab subgroup path like "group/subgroup/project".
    """
    return await _get_client().credential_resolve(repo, role)


@_tool
async def credential_bind(
    repo: str, credential_id: str, role: str = "forge", pinned: bool = False, confirm: bool = False
) -> dict[str, Any]:
    """Bind a registered credential to a repo for `role` (replacing that role's binding, keeping
    others). Requires confirm=true and an admin-scope key — it changes which account every future
    job on the repo pushes and opens PRs as. Never returns a secret.

    repo is "group/name", or a GitLab subgroup path like "group/subgroup/project".
    """
    if not confirm:
        return _confirmation_refusal("credential_bind", f"bind {credential_id!r} to {repo!r} for role {role!r}")
    return await _get_client().credential_bind(repo, credential_id, role, pinned)


def main() -> None:
    """Console entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
