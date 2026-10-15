"""The QA agent node: ``qa_node`` runs the QA agent inside a prepared sandbox.

Its only caller is the sub-task graph's ``exploratory_qa_node``
(``subtask_nodes.py``), which hands it a pseudo-state for one app. Runtime
dependencies come from ``config["configurable"]`` (LangGraph standard
``RunnableConfig`` injection).
"""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer

from embry0.workflows.qa.browser_backends import (
    DEFAULT_BROWSER_BACKEND,
    backend_for,
    with_browser_backend,
)

logger = structlog.get_logger(__name__)

# EMB-34 fix #2: error-message signatures that mark an agent-phase failure
# as environmental rather than behavioral. Conservative by design — only
# unambiguous transport/infrastructure phrases; anything else stays a
# terminal agent_error.
_TRANSIENT_AGENT_ERROR_SIGNATURES = (
    "connection reset",
    "connection refused",
    "connection aborted",
    "connection error",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "mcp disconnect",
    "transport closed",
    "websocket closed",
    "network is unreachable",
    "name resolution",
    "econnreset",
    "epipe",
    "socket hang up",
    "overloaded_error",
    "server disconnected",
    "502",
    "503",
    "529",
)


def _is_transient_agent_error(error_message: str) -> bool:
    """True when an agent failure looks environmental (network/MCP/API blip).

    The agent's own wall-clock limit ("Agent timed out after Ns") matches the
    "timed out" signature but is not a blip: retrying it would spend the
    whole budget again (#643).
    """
    msg = (error_message or "").lower()
    if msg.startswith("agent timed out after"):
        return False
    return any(sig in msg for sig in _TRANSIENT_AGENT_ERROR_SIGNATURES)


async def qa_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Invoke the QA agent inside the sandbox started by acquire_sandbox_node.

    The agent reads /workspace/.qa/job.json on its own; we just dispatch
    via the standard run_agent_node pipeline with agent_type='qa'.
    """
    from embry0.orchestration.nodes.agent import run_agent_node
    from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    agent_runner = configurable.get("agent_runner")
    credentials = configurable.get("credentials") or {}
    db = configurable.get("db")
    agent_sessions_repo = configurable.get("agent_sessions_repo")

    if agent_runner is None:
        raise RuntimeError(
            "qa_node requires agent_runner in config['configurable'] — "
            "qa pipeline cannot run without a sandbox executor"
        )
    if db is None:
        raise RuntimeError(
            "qa_node requires db in config['configurable'] to load the qa "
            "agent_definition (system prompt, tools, mcp_servers)"
        )

    # Load the qa agent_definition row (seeded by Phase 2 Task 5). The resolver
    # inside run_agent_node only consumes the dict it's handed; it does not look
    # up DB rows by agent_type, so we fetch here and pass agent_definition= so
    # the system_prompt/tools/mcp_servers actually reach the sandbox runner.
    repo = AgentDefinitionsRepository(db)
    agent_definition = await repo.get("qa")
    if agent_definition is None:
        raise RuntimeError(
            "qa agent_definition row missing — run AgentDefinitionsRepository.reset('qa') "
            "or restart the orchestrator to re-seed builtin agents"
        )

    qa = state["qa"]

    # #233: wire the definition to the resolved browser backend. The name
    # rides qa_yaml_parsed (sub-task path: _synth_v1_qa_yaml carries the
    # ResolvedAppConfig value; legacy v1 documents never have the key →
    # default backend, a byte-identical no-op merge). The DB row stays
    # playwright-shaped — the swap is invocation-scoped, same pattern as
    # generic_agent's _with_watcher_log_tool.
    backend_name = (qa.get("qa_yaml_parsed") or {}).get("browser_backend") or DEFAULT_BROWSER_BACKEND
    agent_definition = with_browser_backend(agent_definition, backend_for(backend_name))
    last = qa["attempts"][-1]

    # Scale the agent's turn budget with the acceptance-criteria count. The
    # old flat cap of 100 was sized for a managed app's boot investigation
    # (Compose introspection, log tailing) followed by a handful of criteria.
    # A deployed-target run skips boot but adds a multi-step login gate and
    # one-or-more browser turns per criterion, so a 7-criterion run with an
    # Auth0 login blew straight through 100 and never wrote result.json.
    # Budget ~30 turns/criterion on top of a 100 floor, capped at 400. The
    # budget_seconds wall-clock (1800s deployed / 7200s managed since EMB-37)
    # plus AgentRunner's idle watchdog are the runaway backstops.
    n_criteria = len(
        qa.get("acceptance_criteria") or (qa.get("qa_yaml_parsed") or {}).get("acceptance_criteria_template") or []
    )
    max_turns = min(400, max(100, 40 + 30 * n_criteria))

    # Includes the EXACT result.json schema. Without this the agent invents
    # field names (overall_status vs overall, phases.* vs flat) and status
    # values (warning, partial) that fail Pydantic validation, making every
    # successful agent run look like a failed attempt downstream.
    prompt = (
        "The boot phase has already completed (boot_outcome=passed) — the app "
        "is running and ready for you. Read /workspace/.qa/job.json and run the "
        "4 phases described in your system prompt: seed, e2e, exploratory, "
        "report. Then write /workspace/.qa/result.json EXACTLY matching this "
        "schema (extra keys, renamed keys, or invented status values are "
        "rejected by Pydantic):\n"
        "\n"
        "{\n"
        '  "schema_version": 1,\n'
        '  "job_id": "<from job.json>",\n'
        '  "attempt_n": <from job.json>,\n'
        '  "phase_reached": "boot" | "seed" | "e2e" | "exploratory" | "report",\n'
        '  "overall": "passed" | "failed" | "inconclusive",\n'
        '  "boot": null,  // omit or set null — orchestrator fills this in from state.qa\n'
        '  "seed": {"ran": false, "note": "no seed config"} | null,\n'
        '  "e2e":  {"ran": false} | null,\n'
        '  "acceptance_results": [\n'
        "    {\n"
        '      "criterion": "<verbatim from job.json acceptance_criteria>",\n'
        '      "status": "passed" | "failed" | "inconclusive",  // NOT warning/partial/etc.\n'
        '      "evidence": ["<artifact path uploaded to MinIO>", ...],\n'
        '      "notes": "<optional: short reason, esp. for failed/inconclusive>",\n'
        '      "console_errors": ["<verbatim browser console message>", ...],\n'
        '      "network_failures": [{"url": "<...>", "status": <int>}, ...],  // dicts, NOT strings\n'
        '      "log_excerpts": [{"service": "<container_name>", "lines": "<excerpt>"}, ...]  // dicts, NOT strings\n'
        "    }, ...\n"
        "  ],\n"
        '  "anomalies": [\n'
        "    {\n"
        '      "category": "console_error" | "network_error" | "unexpected_state" | "crash" | "guardrail_violation",\n'
        '      "detail": "<short>",\n'
        '      "evidence_paths": ["..."]\n'
        "    }, ...\n"
        "  ]\n"
        "}\n"
        "\n"
        "Mapping rules:\n"
        "- A criterion with even ONE truly failing observation → status=failed.\n"
        "  A 404 for a static asset (e.g. vite.svg) IS a console error — if the\n"
        "  criterion forbids console errors, status MUST be failed (notes can\n"
        "  call out it's benign). Don't paper over with 'warning' — there's no\n"
        "  such status.\n"
        "- overall=passed iff every acceptance_results[].status is passed.\n"
        "  overall=failed iff any is failed. Else inconclusive.\n"
        "- GUARDRAILS: if job.json has a non-empty guardrails list, obey it as\n"
        "  absolute DO-NOT rules (see your system prompt). If you realize you\n"
        "  violated one, report it as an anomaly with\n"
        '  category="guardrail_violation" — never hide it.\n'
    )

    # Forward every event the sandbox runner emits to the LangGraph stream
    # writer. IssueExecutor._broadcast_event subscribes to the stream and
    # persists each event to job_logs (and pushes to the WS bus). Without
    # this hook, the agent's stdout is parsed by AgentRunner but never lands
    # anywhere we can read back — making post-mortem debugging impossible.
    #
    # Writer resolution, in priority order:
    # 1. configurable["qa_parent_writer"] — threaded down by the QA
    #    orchestrator when this node runs inside a sub-task graph (invoked
    #    via ainvoke(), where get_stream_writer() returns a DEAD writer:
    #    custom events only flow under the parent's astream — the reason
    #    standalone QA runs used to log nothing). Sub-task events are tagged
    #    with the app name parsed from the synthetic "<parent>__<app>" id.
    # 2. get_stream_writer() — this node running as a real node of the
    #    streamed parent graph (issue-to-pr QA gate).
    # 3. no-op — outside any runnable context (unit tests).
    node_configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    parent_writer = node_configurable.get("qa_parent_writer")
    writer: Any
    if parent_writer is not None:
        _qa_job_id = str(state.get("job_id") or "")
        _app_label = _qa_job_id.split("__", 1)[1] if "__" in _qa_job_id else ""
        if _app_label:

            def _tagged_writer(event: dict[str, Any]) -> None:
                parent_writer({**event, "app": _app_label})

            writer = _tagged_writer
        else:
            writer = parent_writer
    else:
        try:
            writer = get_stream_writer()
        except RuntimeError:
            writer = lambda _event: None  # noqa: E731

    # Plan C Task 7: load any prior AgentSession for this (job, agent) so the
    # in-sandbox executor can resume the same Claude conversation. Restore
    # failures must NEVER block the agent run — fall back to a fresh session.
    from embry0.agents.session import AgentSession, merged_resume_messages

    # EMB-41: deployed-target sub-tasks run under a synthetic job_id
    # ("<parent>__<app>") that has no row in `jobs` — persisting a session
    # for it violates the agent_sessions FK (a logged traceback on every
    # deployed run), and nothing ever resumes a sub-task: retry rounds
    # launch fresh sub-task jobs. Skip session load AND persist for
    # synthetic ids. (Keying on the parent instead would collide across
    # the per-app QA agents, which all share agent_type="qa".)
    if "__" in (state.get("job_id") or ""):
        agent_sessions_repo = None

    resume_session: AgentSession | None = None
    if agent_sessions_repo is not None:
        try:
            prior = await agent_sessions_repo.get(
                job_id=state.get("job_id", ""),
                agent_type="qa",
            )
            if prior is not None:
                resume_session = AgentSession(
                    job_id=prior["job_id"],
                    agent_type=prior["agent_type"],
                    mode=prior["mode"],
                    messages=prior.get("messages"),
                    session_id=prior.get("session_id"),
                    session_blob=prior.get("session_blob"),
                )
        except Exception:
            logger.warning(
                "qa_session_restore_failed",
                job_id=state.get("job_id"),
                exc_info=True,
            )
            resume_session = None

    result = await run_agent_node(
        state=state,
        agent_runner=agent_runner,
        agent_type="qa",
        node="qa",
        prompt=prompt,
        agent_definition=agent_definition,
        # Scaled with the acceptance-criteria count (see above): 100 floor
        # for managed/boot-heavy runs, +30/criterion for criteria-rich
        # deployed runs with a login gate, capped at 400.
        max_turns=max_turns,
        timeout_seconds=qa.get("budget_seconds", 7200),
        on_event=writer,
        credentials=credentials,
        config=config,
        resume_session=resume_session,
        # #468: the template's QA step config, set by the issue-to-PR graph's
        # orchestrate_qa node. Absent for standalone QA (no template).
        template_config=configurable.get("qa_template_config"),
    )

    # Plan C Task 7: capture post-run session state so the next invocation of
    # this node (after a retry, etc.) resumes the same conversation. Skip on
    # agent error and skip when both messages and session_id are absent.
    # Persist failures are best-effort; never block the workflow.
    if agent_sessions_repo is not None:
        last_output = (result.get("agent_outputs") or [None])[-1]
        if last_output and not last_output.get("is_error"):
            new_messages = merged_resume_messages(resume_session, last_output.get("messages"))
            new_session_id = last_output.get("session_id")
            new_session_blob = last_output.get("session_blob")
            if new_messages or new_session_id:
                mode = last_output.get("mode") or ("claude_max" if credentials.get("oauth_token") else "anthropic_api")
                try:
                    await agent_sessions_repo.upsert(
                        job_id=state.get("job_id", ""),
                        agent_type="qa",
                        mode=mode,
                        messages=new_messages,
                        session_id=new_session_id,
                        session_blob=new_session_blob,
                    )
                except Exception:
                    logger.warning(
                        "qa_session_persist_failed",
                        job_id=state.get("job_id"),
                        exc_info=True,
                    )

    # run_agent_node returns a state-update dict; the agent's outcome is in
    # result["agent_outputs"][-1]. For now we record the basics; richer parsing
    # of the agent's structured output happens in the sub-task's
    # collect_artifacts_node by reading /workspace/.qa/result.json.
    agent_outputs = result.get("agent_outputs", [])
    if agent_outputs:
        last_output = agent_outputs[-1]
        if last_output.get("is_error"):
            last["last_phase"] = None
            err_msg = last_output.get("error_message", "unknown")
            # EMB-34 fix #2: classify obviously-environmental agent failures
            # as transient; the sub-task's exploratory_qa_node then runs the
            # agent once more (#643).
            reason_prefix = "agent_transient" if _is_transient_agent_error(err_msg) else "agent_error"
            last["exit_reason"] = f"{reason_prefix}: {err_msg[:200]}"
        else:
            # Agent completed cleanly. collect_artifacts_node reads
            # /workspace/.qa/result.json for the actual phase_reached + overall.
            last["last_phase"] = "report"  # tentative

    qa["attempts"][-1] = last
    state["qa"] = qa
    # Merge any state updates run_agent_node returned (e.g. agent_outputs accumulator)
    if "agent_outputs" in result:
        state.setdefault("agent_outputs", []).extend(result["agent_outputs"])
    # A soft per-job cap crossing: exploratory_qa_node must not retry past it.
    if result.get("budget_overrun_usd"):
        state["budget_overrun_usd"] = result["budget_overrun_usd"]
    return state
