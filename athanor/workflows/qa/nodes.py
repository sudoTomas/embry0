"""QA pipeline nodes: init_qa, qa, report, retry.

Dependency injection: each node reads runtime dependencies from
``config["configurable"]`` (LangGraph standard ``RunnableConfig`` injection),
matching the pattern used by ``athanor/workflows/issue_to_pr/nodes.py``.

Phase 1.5 architecture: configurable carries TWO MinIO clients —
- ``qa_minio_sandbox``: endpoint=minio-proxy:9100, used to mint URLs the
  sandbox will hit through Docker DNS in DinD.
- ``qa_minio_internal``: endpoint=minio:9000, used by report_node for direct
  orchestrator-side uploads.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

import httpx
import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END
from langgraph.types import Command

from athanor.workflows.qa._subtask_env import build_qa_sandbox_env
from athanor.workflows.qa.boot import run_boot_phase
from athanor.workflows.qa.qa_yaml import parse_qa_yaml
from athanor.workflows.qa.screenshot import take_diagnostic_screenshot

logger = structlog.get_logger(__name__)


async def init_qa_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Initialize a QA attempt: resolve profile, start sandbox, clone repo,
    parse qa.yaml, register sandbox token, mint presigned URLs, write job.json.

    Dependencies are read from ``config["configurable"]``:
      - ``docker``, ``sandbox_manager``, ``profiles_repo``
      - ``qa_minio_sandbox``, ``qa_token_registry``
      - ``proxy_manager`` (optional but required for git auth into private repos)

    On any failure during steps 2-9, all partially-allocated resources
    (sandbox token registration, sandbox container, qa-net) are rolled back
    in reverse order before the exception is re-raised.
    """
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    sandbox_mgr = configurable.get("sandbox_manager")
    profiles_repo = configurable.get("profiles_repo")
    minio_sandbox = configurable.get("qa_minio_sandbox")
    token_registry = configurable.get("qa_token_registry")
    proxy_mgr = configurable.get("proxy_manager")

    if (
        docker is None
        or sandbox_mgr is None
        or profiles_repo is None
        or minio_sandbox is None
        or token_registry is None
    ):
        raise RuntimeError("init_qa_node missing required dependencies in config['configurable']")

    job_id = state["job_id"]
    repo = state["repo"]
    branch = state.get("branch_name") or "main"
    qa = state.setdefault("qa", {})

    attempt_n = len(qa.get("attempts", [])) + 1
    base = docker._build_base_cmd()  # noqa: SLF001
    qa_net = f"qa-net-{job_id}"

    # 1. Resolve sandbox profile.
    profile_name = qa.get("sandbox_profile_name") or qa.get("qa_yaml_parsed", {}).get("sandbox_profile") or "qa-jvm"
    if profile_name == "qa-jvm" and not qa.get("sandbox_profile_name") and not qa.get("qa_yaml_parsed"):
        logger.warning(
            "qa_profile_fallback_to_default",
            job_id=job_id,
            msg="Neither qa.sandbox_profile_name nor qa.qa_yaml_parsed.sandbox_profile set; "
            "defaulting to 'qa-jvm'. Set sandbox_profile in .athanor/qa.yaml.",
        )
    profile = await profiles_repo.get(profile_name)
    if profile is None:
        msg = f"qa: sandbox profile {profile_name!r} does not exist"
        raise RuntimeError(msg)

    is_dind = bool(profile.get("dind_enabled", False))

    # Track allocated resources for rollback on failure.
    container_id: str | None = None
    sandbox_token: str | None = None
    qa_net_created = False

    try:
        # 2. (Mode 2) create per-job network.
        if is_dind:
            await docker.run_cmd(
                base + ["network", "create", "--label", f"athanor.qa_job_id={job_id}", qa_net],
                timeout=15,
            )
            qa_net_created = True
            logger.info("qa_net_created", job_id=job_id, network=qa_net)

        # 3. Build sandbox env (filtered user env + infra vars) and start sandbox.
        git_proxy_url = getattr(proxy_mgr, "git_proxy_url", "") if proxy_mgr else ""
        env = build_qa_sandbox_env(
            user_env_vars=state.get("user_env_vars"),
            git_proxy_url=git_proxy_url,
            qa_job_id=job_id,
            attempt_n=attempt_n,
            qa_network_name=qa_net,
        )

        # PR-flow handoff: when this QA run was reached via the issue→PR
        # graph (review → init_qa), the developer sandbox from init_node
        # is still alive at sandbox-{job_id}. Sandbox name is derived from
        # job_id, so sandbox_mgr.create() would hit a "container name in
        # use" conflict. The dev sandbox has done its job by this point
        # (developer + review both ran inside it); destroy it before we
        # spin up the QA sandbox.
        existing = state.get("sandbox_container_id")
        if existing:
            try:
                await sandbox_mgr.destroy(existing)
                logger.info("qa_predecessor_sandbox_destroyed", job_id=job_id, container_id=existing)
            except Exception as exc:
                logger.warning(
                    "qa_predecessor_sandbox_destroy_failed",
                    job_id=job_id,
                    container_id=existing,
                    error=str(exc),
                )

        container_id, sandbox_token = await sandbox_mgr.create(
            job_id,
            profile=profile,
            env=env,
        )

        # 4–5. Network attach, git creds, clone, capture HEAD sha.
        from athanor.workflows.qa._subtask_prep import (
            prep_qa_sandbox_clone,
            prep_qa_sandbox_jobjson,
        )

        cloned = await prep_qa_sandbox_clone(
            docker=docker,
            proxy_mgr=proxy_mgr,
            container_id=container_id,
            sandbox_token=sandbox_token,
            job_id=job_id,
            repo=repo,
            branch=branch,
            is_dind=is_dind,
            qa_net=qa_net,
            base=base,
        )

        # 6. Read + parse .athanor/qa.yaml from inside the sandbox.
        # (Stays here — sub-task acquire_sandbox_node has its own qa.yaml dict
        # already and does not go through this path.)
        yaml_text = await docker.run_cmd(
            docker.build_exec_cmd(container_id, ["cat", "/workspace/.athanor/qa.yaml"]),
            timeout=10,
        )
        qa_yaml = parse_qa_yaml(yaml_text)

        # 7–9. Register token, mint presigned URLs, write job.json.
        artifact_prefix = f"{job_id}/{attempt_n}/"
        job_json = {
            "schema_version": 1,
            "job_id": job_id,
            "attempt_n": attempt_n,
            "mode": qa_yaml.mode,
            "qa_yaml": qa_yaml.model_dump(),
            "acceptance_criteria": qa.get("acceptance_criteria") or qa_yaml.acceptance_criteria_template,
            "changed_files": qa.get("changed_files", []),
            "frontend_url": qa_yaml.frontend_url,
            "presign_refresh_url": "http://presign-proxy:9104/api/v1/internal/qa/presign",
            "sandbox_token": sandbox_token,
        }
        await prep_qa_sandbox_jobjson(
            docker=docker,
            minio_sandbox=minio_sandbox,
            token_registry=token_registry,
            container_id=container_id,
            sandbox_token=sandbox_token,
            job_id=job_id,
            attempt_n=attempt_n,
            artifact_prefix=artifact_prefix,
            job_json=job_json,
        )

        # 10. Update state with the new attempt.
        qa.setdefault("attempts", []).append(
            {
                "attempt_n": attempt_n,
                "started_at": datetime.now(UTC).isoformat(),
                "ended_at": None,
                "sandbox_id": container_id,
                "qa_net_name": qa_net if is_dind else None,
                "artifact_prefix": artifact_prefix,
                "last_phase": None,
                "exit_reason": None,
                "result_summary": None,
                "log_artifact_url": None,
            }
        )
        qa["sandbox_token"] = sandbox_token
        qa["qa_yaml_raw"] = yaml_text
        qa["qa_yaml_parsed"] = qa_yaml.model_dump()
        qa["head_sha"] = cloned.head_sha
        state["qa"] = qa
        # run_agent_node (used by qa_node) reads state["sandbox_container_id"] to
        # know which container to docker exec into. Without this, it would pass
        # an empty string and the runner would exec on container "" → exit 1.
        state["sandbox_container_id"] = container_id

        logger.info("init_qa_done", job_id=job_id, attempt_n=attempt_n, sandbox=container_id)
        return state

    except Exception as exc:
        logger.error("init_qa_failed", job_id=job_id, error=str(exc), exc_info=True)
        # Roll back in reverse order of allocation.
        if sandbox_token:
            try:
                token_registry.unregister(sandbox_token)
            except Exception:
                logger.warning("init_qa_rollback_token_unregister_failed", exc_info=True)
        if container_id:
            try:
                await sandbox_mgr.destroy(container_id)
            except Exception:
                logger.warning("init_qa_rollback_destroy_failed", exc_info=True)
        if qa_net_created:
            from athanor.workflows.qa.cleanup import cleanup_qa_resources

            try:
                await cleanup_qa_resources(docker, job_id=job_id)
            except Exception:
                logger.warning("init_qa_rollback_cleanup_failed", exc_info=True)
        raise


async def boot_qa_node(state: dict[str, Any], config: RunnableConfig) -> Command[Literal["qa", "qa_report"]]:
    """Backend-owned QA boot phase.

    Runs qa.yaml startup.command in the sandbox, polls ready_checks until all
    pass, the budget elapses, or startup fails. Routes to qa (success) or
    qa_report (timeout / startup_failed). On non-success, captures a Playwright
    screenshot of frontend_url best-effort and uploads to MinIO for operator
    diagnostics.

    Reads:
      - state["qa"]["qa_yaml_parsed"]: parsed qa.yaml dict
      - state["qa"]["attempts"][-1]["sandbox_id"], ["artifact_prefix"]
      - state["sandbox_container_id"]: fallback container id
      - config["configurable"]["docker"]: required
      - config["configurable"]["qa_minio"]: required for screenshot upload
    """
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    minio = configurable.get("qa_minio")

    if docker is None:
        raise RuntimeError("boot_qa_node requires docker in config['configurable']")

    qa = state.get("qa") or {}
    qa_yaml = qa.get("qa_yaml_parsed") or {}
    attempts = qa.get("attempts") or []
    last_attempt = attempts[-1] if attempts else {}
    container_id = state.get("sandbox_container_id") or last_attempt.get("sandbox_id")
    if not container_id:
        raise RuntimeError("boot_qa_node: no sandbox container id available in state")
    artifact_prefix = last_attempt.get("artifact_prefix", f"{state['job_id']}/1/")

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=3.0, read=8.0, write=8.0, pool=8.0)) as http:
        result = await run_boot_phase(
            qa_yaml=qa_yaml,
            container_id=container_id,
            docker=docker,
            http=http,
        )

    qa["boot_outcome"] = result.outcome
    qa["boot_duration_ms"] = result.duration_ms
    qa["boot_attempts"] = result.attempts

    if result.outcome == "passed":
        return Command(goto="qa", update={"qa": qa})

    # Timeout or startup_failed — capture diagnostic screenshot best-effort.
    frontend_url = qa_yaml.get("frontend_url", "")
    screenshot_bytes: bytes | None = None
    if frontend_url:
        screenshot_bytes = await take_diagnostic_screenshot(
            docker=docker,
            container_id=container_id,
            frontend_url=frontend_url,
        )

    if screenshot_bytes and minio is not None:
        screenshot_key = f"{artifact_prefix}screenshots/boot-timeout.png"
        try:
            await minio.put_object(
                "qa-artifacts",
                screenshot_key,
                screenshot_bytes,
                content_type="image/png",
            )
            qa["boot_diagnostic_screenshot_path"] = screenshot_key
        except Exception as exc:
            logger.warning("boot_screenshot_upload_failed", error=str(exc))

    # Stamp the exit_reason on the attempt so report_node + retry_node route correctly.
    if attempts:
        attempts[-1]["exit_reason"] = "boot_timeout" if result.outcome == "timeout" else "startup_failed"
        attempts[-1]["last_phase"] = "boot"
        qa["attempts"] = attempts

    return Command(goto="qa_report", update={"qa": qa})


async def qa_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Invoke the QA agent inside the sandbox started by init_qa_node.

    The agent reads /workspace/.qa/job.json on its own; we just dispatch
    via the standard run_agent_node pipeline with agent_type='qa'.
    """
    from athanor.orchestration.nodes.agent import run_agent_node
    from athanor.storage.repositories.agent_definitions import AgentDefinitionsRepository

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
    last = qa["attempts"][-1]

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
        '      "category": "console_error" | "network_error" | "unexpected_state" | "crash",\n'
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
    )

    # Forward every event the sandbox runner emits to the LangGraph stream
    # writer. IssueExecutor._broadcast_event subscribes to the stream and
    # persists each event to job_logs (and pushes to the WS bus). Without
    # this hook, the agent's stdout is parsed by AgentRunner but never lands
    # anywhere we can read back — making post-mortem debugging impossible.
    # get_stream_writer() raises outside a LangGraph runnable context (unit
    # tests calling qa_node directly), so fall back to a no-op there.
    try:
        writer = get_stream_writer()
    except RuntimeError:
        writer = lambda _event: None  # noqa: E731

    # Plan C Task 7: load any prior AgentSession for this (job, agent) so the
    # in-sandbox executor can resume the same Claude conversation. Restore
    # failures must NEVER block the agent run — fall back to a fresh session.
    from athanor.agents.session import AgentSession

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
        prompt=prompt,
        agent_definition=agent_definition,
        # QA agents do a lot of infra investigation up front (Compose
        # introspection, log tailing, network debugging) before the
        # exploratory phase even begins. The default 40 turns gets eaten
        # by boot phase alone on non-trivial stacks; bump to 100.
        max_turns=100,
        timeout_seconds=qa.get("budget_seconds", 7200),
        on_event=writer,
        credentials=credentials,
        config=config,
        resume_session=resume_session,
    )

    # Plan C Task 7: capture post-run session state so the next invocation of
    # this node (after a retry, etc.) resumes the same conversation. Skip on
    # agent error and skip when both messages and session_id are absent.
    # Persist failures are best-effort; never block the workflow.
    if agent_sessions_repo is not None:
        last_output = (result.get("agent_outputs") or [None])[-1]
        if last_output and not last_output.get("is_error"):
            new_messages = last_output.get("messages")
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
    # of the agent's structured output happens in report_node by reading
    # /workspace/.qa/result.json.
    agent_outputs = result.get("agent_outputs", [])
    if agent_outputs:
        last_output = agent_outputs[-1]
        if last_output.get("is_error"):
            last["last_phase"] = None
            last["exit_reason"] = f"agent_error: {last_output.get('error_message', 'unknown')[:200]}"
        else:
            # Agent completed cleanly. report_node will read /workspace/.qa/result.json
            # to get the actual phase_reached + overall.
            last["last_phase"] = "report"  # tentative; report_node confirms

    qa["attempts"][-1] = last
    state["qa"] = qa
    # Merge any state updates run_agent_node returned (e.g. agent_outputs accumulator)
    if "agent_outputs" in result:
        state.setdefault("agent_outputs", []).extend(result["agent_outputs"])
    return state


async def report_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """End-of-attempt: capture logs, validate result.json, cleanup.

    Always best-effort: every cleanup step is wrapped in try/except so a
    failing rollback doesn't mask the result. Runs after qa_node regardless
    of pass/fail.
    """
    from athanor.workflows.qa.cleanup import cleanup_qa_resources
    from athanor.workflows.qa.result_schema import QAResult

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    docker = configurable.get("docker")
    sandbox_mgr = configurable.get("sandbox_manager")
    minio_internal = configurable.get("qa_minio")
    token_registry = configurable.get("qa_token_registry")

    if docker is None or sandbox_mgr is None or minio_internal is None or token_registry is None:
        raise RuntimeError("report_node missing required dependencies in config['configurable']")

    qa = state["qa"]
    job_id = state["job_id"]
    last = qa["attempts"][-1]
    container_id = last["sandbox_id"]
    artifact_prefix = last["artifact_prefix"]
    mode = (qa.get("qa_yaml_parsed") or {}).get("mode", "dind")

    # 1. Capture end-of-attempt logs from the sandbox.
    if mode == "dind":
        log_cmd = [
            "bash",
            "-c",
            "docker compose --project-name qa_${QA_JOB_ID} logs --no-color --timestamps 2>&1 || true",
        ]
    else:
        log_cmd = [
            "bash",
            "-c",
            "find /workspace/.qa/logs -type f -exec cat {} +; true",
        ]
    try:
        full_log = await docker.run_cmd(docker.build_exec_cmd(container_id, log_cmd), timeout=30)
    except Exception as exc:
        logger.warning("qa_log_capture_failed", error=str(exc), exc_info=True)
        full_log = f"[log capture failed: {exc}]"

    # 2. Upload logs directly via internal SDK (orchestrator holds creds; no presign).
    log_key = artifact_prefix + "logs/full.log"
    try:
        await minio_internal.put_object(
            "qa-artifacts",
            log_key,
            full_log.encode("utf-8"),
            content_type="text/plain",
        )
    except Exception as exc:
        logger.warning("qa_log_upload_failed", error=str(exc), exc_info=True)

    # 3. Read + validate result.json.
    result_summary: dict[str, Any] | None = None
    overall = "inconclusive"
    try:
        import json

        result_text = await docker.run_cmd(
            docker.build_exec_cmd(container_id, ["cat", "/workspace/.qa/result.json"]),
            timeout=10,
        )
        parsed_dict = json.loads(result_text)

        # Backend-owned boot phase: synthesize the result.json `boot` section from
        # state so the agent never has to fabricate it. Always wins over any value
        # the agent emitted.
        qa_state = state.get("qa") or {}
        if qa_state.get("boot_outcome"):
            parsed_dict["boot"] = {
                "command": (qa_state.get("qa_yaml_parsed") or {}).get("startup", {}).get("command", ""),
                "duration_ms": qa_state.get("boot_duration_ms", 0),
                "ready_checks": [
                    {
                        "url": rc.get("http", ""),
                        "status": 200 if qa_state["boot_outcome"] == "passed" else 0,
                        "duration_ms": qa_state.get("boot_duration_ms", 0),
                    }
                    for rc in (qa_state.get("qa_yaml_parsed") or {}).get("startup", {}).get("ready_checks", [])
                ]
                or [{"url": "<no-checks-configured>", "status": 200, "duration_ms": 0}],
            }

        result = QAResult.model_validate(parsed_dict)
        result_summary = result.model_dump(mode="json")
        overall = result.overall
    except Exception as exc:
        logger.warning("qa_result_invalid_or_missing", error=str(exc))
        if not last.get("exit_reason"):
            last["exit_reason"] = "result_json_missing"

    # 4. Update attempt + final_status.
    last["result_summary"] = result_summary
    last["ended_at"] = datetime.now(UTC).isoformat()
    last["log_artifact_url"] = f"/api/v1/jobs/{job_id}/artifacts/logs/full.log"

    qa["attempts"][-1] = last
    if overall == "passed":
        qa["final_status"] = "passed"
    elif overall == "failed":
        qa["final_status"] = "failed"
    else:
        qa["final_status"] = qa.get("final_status", "pending")

    # 5. Unregister token + destroy sandbox + cleanup. Each step independently
    # try/excepted so a single rollback failure doesn't mask the others.
    # Order matters: sandbox MUST be destroyed BEFORE cleanup_qa_resources runs
    # `network rm qa-net-<job_id>`. Otherwise the network removal fails because
    # the still-alive sandbox container is attached as an active endpoint.
    token = qa.get("sandbox_token")
    if token:
        try:
            token_registry.unregister(token)
        except Exception as exc:
            logger.warning("qa_token_unregister_failed", error=str(exc))
    try:
        await sandbox_mgr.destroy(container_id)
    except Exception as exc:
        logger.warning("qa_sandbox_destroy_failed", error=str(exc))
    try:
        await cleanup_qa_resources(docker, job_id=job_id)
    except Exception as exc:
        logger.warning("qa_cleanup_failed", error=str(exc))

    state["qa"] = qa
    return state


async def retry_node(state: dict[str, Any], config: RunnableConfig) -> Command[Any]:
    """Decide whether to retry the QA attempt or end (Section 6.2).

    - boot_timeout / seed_timeout / infra_error -> auto-retry (bounded)
    - idle_timeout                              -> hard-fail (ERR_QA_IDLE)
    - validation_failed                         -> standalone: END (Phase 5 routes to triage)
    - total_budget_exceeded                     -> END (Phase 5 routes to triage)
    - result_json_missing                       -> END (treat as inconclusive)
    - None (success)                            -> END
    """
    qa = state.get("qa") or {}
    attempts = qa.get("attempts", [])
    if not attempts:
        return Command(goto=END, update={"qa": {**qa, "final_status": "exhausted"}})

    last = attempts[-1]
    reason = last.get("exit_reason")
    n = len(attempts)
    max_retries = qa.get("max_qa_retries", 2)

    # Success path — no retry, just end.
    if reason is None:
        return Command(goto=END, update={"qa": qa})

    auto_retry_reasons = {"boot_timeout", "seed_timeout", "infra_error"}
    if reason in auto_retry_reasons and n <= max_retries:
        # Expand boot budget for boot timeouts: 1.5x then 2x.
        if reason == "boot_timeout":
            yaml_parsed = qa.get("qa_yaml_parsed") or {}
            startup = yaml_parsed.get("startup") or {}
            base_budget = startup.get("boot_timeout_seconds", 180)
            multiplier = 1.5 if n == 1 else 2.0
            qa["next_attempt_boot_timeout"] = int(base_budget * multiplier)
        return Command(goto="init_qa", update={"qa": qa})

    # Everything else terminates standalone runs.
    final = "failed" if reason == "validation_failed" else "exhausted"
    qa["final_status"] = final
    if n > max_retries and reason in auto_retry_reasons:
        qa["error_code"] = "ERR_QA_BUDGET_EXHAUSTED"
    elif reason == "idle_timeout":
        qa["error_code"] = "ERR_QA_IDLE"
    elif reason == "result_json_missing":
        qa["error_code"] = "ERR_QA_RESULT_INVALID"
    return Command(goto=END, update={"qa": qa})
