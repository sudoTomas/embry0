"""init node — sandbox creation, clone, env injection (RAV-1235 split of nodes.py)."""

from __future__ import annotations

from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig

# Shared seams (run_agent_node, get_stream_writer, interrupt,
# _verify_branch_pushed) are resolved through the package namespace at CALL
# time so test patches on `embry0.workflows.issue_to_pr.nodes.<seam>` reach
# this module. The self-import is safe: the package __init__ binds the seams
# before importing the node modules.
import embry0.workflows.issue_to_pr.nodes as _nodes
from embry0.execution import events
from embry0.workflows.issue_to_pr.evidence import log_scope
from embry0.workflows.issue_to_pr.nodes._common import _filter_user_env_for_sandbox

logger = structlog.get_logger(__name__)


async def _resolve_base_branch_for_job(
    *,
    repo: str,
    forge: str,
    override: str | None,
    configurable: dict[str, Any],
    embry0_config: Any,
) -> tuple[str, str]:
    """``(state["base_branch"], state["base_branch_source"])`` for this job
    (Spec 1A §3.7).

    The forge client is built here rather than injected because it is only
    needed when neither an override nor a preference answers, and building
    it costs nothing when unused.

    The source travels with the branch because downstream nodes must be able
    to tell a resolved answer from the module default — see the
    ``base_branch_source`` note on ``JobState``.
    """
    from embry0.services.base_branch import resolve_base_branch_with_source
    from embry0.services.credentials.resolver import forge_token

    gitlab_client: Any = None
    github_client: Any = None
    resolver = configurable.get("credential_resolver")
    token_forge = "gitlab" if forge == "gitlab" else "github"
    default = str(getattr(embry0_config, f"{token_forge}_token", "") or "")
    try:
        # #424: a registry or per-owner/namespace env token counts, not only
        # the default — without one the lookup is skipped and the job
        # silently targets "main".
        has_token = bool(await forge_token(resolver, repo, token_forge, default))
    except Exception:
        logger.warning("base_branch_token_lookup_failed", repo=repo, exc_info=True)
        has_token = False
    if forge == "gitlab":
        if has_token:
            # Never a raw GitLabClient: the factory is what loads the private
            # CA bundle (spec 1A §3.2). Without it the default-branch lookup
            # fails the TLS handshake, _forge_default swallows it, and the job
            # targets "main" on a repo whose integration branch is not main.
            from embry0.execution.gitlab_client import make_gitlab_client

            gitlab_client = make_gitlab_client(embry0_config, credential_resolver=resolver)
    else:
        if has_token:
            from embry0.execution.github_client import GitHubClient

            github_client = GitHubClient(default_token=default, credential_resolver=resolver)

    return await resolve_base_branch_with_source(
        repo=repo,
        forge=forge,
        override=override,
        prefs_repo=configurable.get("repo_preferences_repo"),
        gitlab_client=gitlab_client,
        github_client=github_client,
    )


async def _job_case_evidence(
    state: dict[str, Any], configurable: dict[str, Any], embry0_config: Any, job_id: str
) -> dict[str, Any] | None:
    """``JobState.case_evidence`` for this job, or None (SH-P4.4).

    Optional context: any failure here degrades to "no evidence", never to a
    failed job. The ledger is read through HealingCaseService, built lazily
    from the pool like the other per-node repositories.
    """
    from embry0.workflows.issue_to_pr.evidence import resolve_case_evidence

    cases: Any = None
    db = configurable.get("db")
    if db is not None:
        try:
            from embry0.services.healing.service import HealingCaseService
            from embry0.storage.repositories.healing_cases import HealingCasesRepository

            cases = HealingCaseService(HealingCasesRepository(db))
        except Exception:
            # No ledger → the ticket is still a source.
            logger.warning("case_evidence_ledger_unavailable", job_id=job_id, exc_info=True)
    try:
        allowlist = str(getattr(embry0_config, "context_proxy_container_allowlist", "") or "")
        evidence = await resolve_case_evidence(
            job_id=job_id,
            issue_id=state.get("issue_id"),
            ticket_text=str(state.get("task") or ""),
            allowlist=[name.strip() for name in allowlist.split(",") if name.strip()],
            cases=cases,
        )
    except Exception:
        logger.warning("case_evidence_unavailable", job_id=job_id, exc_info=True)
        return None
    return evidence.as_state() if evidence is not None else None


def _job_push_branch(state: dict[str, Any]) -> str:
    """The branch this job's sandbox is enrolled to push (#559); see ``job_push_branch``."""
    from embry0.orchestration.branch_names import job_push_branch

    return job_push_branch(
        issue_id=state.get("issue_id"),
        task=state.get("task"),
        job_id=state.get("job_id"),
        context=state.get("context"),
        chosen=state.get("branch_name"),
    )


async def init_node(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    """Create the sandbox container and populate /workspace for the job context.

    Workspace population is delegated to the per-context-type initializer
    (embry0/workspace_init/); this node keeps the container lifecycle.
    """
    context = state.get("context") or {"type": "git", "repo": state.get("repo", "")}
    ctx_type = context.get("type", "git")

    # Strategy resolution + validation run BEFORE the stream writer and the
    # sandbox manager: an unknown context type or a denied context (path
    # outside the allowlist, malformed URL) must fail with no container
    # created — the invariant test_non_git_job_guard enforces.
    from embry0.workspace_init import InitContext, load_initializer

    initializer = load_initializer(ctx_type)
    embry0_config = config["configurable"].get("embry0_config")
    initializer.validate(context, embry0_config)

    writer = _nodes.get_stream_writer()
    writer(events.node_started(node="init"))

    job_id = state.get("job_id", "")
    repo = context.get("repo") or state.get("repo", "")

    sandbox_mgr = config["configurable"].get("sandbox_manager")
    docker = config["configurable"].get("docker")
    proxy_mgr = config["configurable"].get("proxy_manager")

    # #167 + Spec 1A §3.8: resolve the forge once — it decides the clone
    # origin, the git credential host enrolled with the proxies, and later
    # the MR vs PR call. Deliberately OUTSIDE the try below: an unregistered
    # repo must fail with no container created (same invariant as
    # initializer.validate above), and the try's except wraps everything into
    # RuntimeError("Sandbox initialization failed: …"), which would mask
    # UnregisteredRepoError as ERR_SANDBOX_INIT. resolve_forge still degrades
    # a registry OUTAGE to the registry's own default — what every pre-#162
    # deployment meant.
    from embry0.services.forge_resolver import resolve_forge

    forge = await resolve_forge(config["configurable"].get("repos_repo"), repo, job_id=job_id)
    gitlab_url = getattr(embry0_config, "gitlab_url", "") or ""

    # Spec 1A §3.7: one base branch for the whole job — the clone fetch, the
    # review diff and the MR/PR target all read state["base_branch"].
    base_branch, base_branch_source = await _resolve_base_branch_for_job(
        repo=repo,
        forge=forge,
        override=state.get("base_branch"),
        configurable=config["configurable"],
        embry0_config=embry0_config,
    )

    if not sandbox_mgr:
        raise RuntimeError("No sandbox manager available — cannot create sandbox")

    # Git proxy URL (empty when the orchestrator has no forge credential).
    # The sandbox's git uses the proxy as the remote for the job's repository,
    # and the proxy adds the forge token upstream (#521). No token ever enters
    # the sandbox.
    git_proxy_url = getattr(proxy_mgr, "git_proxy_url", "") if proxy_mgr else ""

    container_id = None
    try:
        # Merge user env FIRST, then infrastructure vars last so they win.
        # _filter_user_env_for_sandbox provides defense-in-depth vs the API-layer
        # validators: stored rows with reserved keys/prefixes (from pre-fix data
        # or a bypassed API) must not escape to the sandbox. It also filters
        # scope='qa' rows unless qa_active=True (Phase 2 QA pipeline sets this).
        env: dict[str, str] = _filter_user_env_for_sandbox(
            state.get("user_env_vars") or [],
            qa_active=bool(state.get("qa_active", False)),
        )
        if git_proxy_url and ctx_type == "git":
            env["EMBRY0_GIT_PROXY_URL"] = git_proxy_url

        # RAV-1140: mediated log drill-down for watcher analysis jobs. The
        # watcher_log_access flag can only ride the context dict via the
        # repository-layer create (JobContext is extra="forbid" at the API), so
        # its presence IS the authorization. Env is injected pre-create; the
        # enroll + token delivery happen post-create below.
        context_proxy_url = getattr(proxy_mgr, "context_proxy_url", "") if proxy_mgr else ""
        watcher_job = bool(context.get("watcher_log_access"))

        # SH-P4.4: the logged failure this job is about (linked healing case,
        # else the ticket's "## Evidence" section) — injected into the
        # developer and reviewer prompts. It also decides least-privilege log
        # access: the tool is scoped to the allowlisted services the evidence
        # names, and only when the evidence also describes a failure to look
        # for (log_scope). Watcher analysis jobs keep their own briefing and
        # their unscoped grant.
        case_evidence = (
            None if watcher_job else await _job_case_evidence(state, config["configurable"], embry0_config, job_id)
        )
        scope: list[str] | None = None
        if case_evidence:
            # Resolve — and report — the scope whether or not the grant is
            # enabled. The flag is off by default, so gating the diagnostic on
            # it would mean an operator deciding whether to turn it on has
            # nothing to read. A resolved scope with the flag off is the
            # shadow-mode line: "this job WOULD have been given log access".
            resolved = log_scope(case_evidence)
            if not resolved:
                # Otherwise an operator cannot tell "this job has no evidence"
                # from "its evidence names a container that no longer exists"
                # (every ticket filed before a repo rename, for instance).
                logger.info(
                    "developer_log_scope_empty",
                    job_id=job_id,
                    source=case_evidence["source"],
                    named_services=len(case_evidence["services"]),
                    has_signature=bool(case_evidence["signature"]),
                    sample_lines=len(case_evidence["samples"]),
                )
            elif getattr(embry0_config, "developer_log_tool_enabled", False):
                scope = resolved
            else:
                logger.info(
                    "developer_log_scope_available",
                    job_id=job_id,
                    containers=resolved,
                    enabled=False,
                )

        wants_log_tool = (watcher_job or scope is not None) and bool(context_proxy_url)
        if wants_log_tool:
            env["EMBRY0_CONTEXT_PROXY_URL"] = context_proxy_url

        # RAV-1058: honour a job-create sandbox_profile. Resolved by name via
        # profiles_repo (the QA acquire_sandbox pattern); an unknown name fails
        # the job loudly here rather than silently running on the default
        # profile. None → SandboxManager.create falls back to its default.
        profile: dict[str, Any] | None = None
        profile_name = state.get("sandbox_profile")
        if profile_name:
            profiles_repo = config["configurable"].get("profiles_repo")
            if profiles_repo is not None:
                profile = await profiles_repo.get(profile_name)
                if profile is None:
                    raise RuntimeError(f"sandbox profile {profile_name!r} not found")

        container_id, sandbox_token = await sandbox_mgr.create(
            job_id,
            profile=profile,
            env=env,
            repo=repo,
            forge=forge,
            llm_providers=tuple(config["configurable"].get("llm_providers") or ()),
            # The developer pushes its branch from this sandbox (#521). Every
            # other sandbox (QA, onboarding, warmers, image builds) only reads.
            git_push=True,
            # #559: and only that branch. Until the developer chooses, the
            # job's own name; developer_node swaps in the chosen one.
            git_push_branches=[_job_push_branch(state)],
        )
        logger.info("sandbox_created", job_id=job_id, container_id=container_id)
        writer(events.progress(message="Sandbox container created", node="init"))

        # EMB-45: when the xai-proxy is running, deliver the per-sandbox proxy bearer
        # to a 0600 file the DirectXaiExecutor reads to authenticate to the proxy. The
        # token never rides the sandbox env; git's proxy bearer is delivered the same way.
        xai_proxy_url = getattr(proxy_mgr, "xai_proxy_url", "") if proxy_mgr else ""
        if xai_proxy_url and docker:
            from embry0.sandbox.xai_token import build_xai_token_write_cmd

            await docker.run_cmd(
                docker.build_exec_cmd(container_id, build_xai_token_write_cmd(sandbox_token)),
                timeout=10,
            )

        # RAV-1140: enroll with the context-proxy (per-job opt-in — deliberately
        # not part of the enroll_sandbox broadcast) and deliver the bearer to a
        # 0600 home file, xai-token pattern. Fail-open: the watcher worked
        # briefing-only before this ticket, so a provisioning hiccup degrades
        # the job rather than failing it — the flag is only set on FULL success
        # and generic_agent grants the tool only when the flag is set.
        log_tool_ready = False
        granted_scope: list[str] | None = None
        if wants_log_tool and docker and proxy_mgr:
            try:
                from embry0.sandbox.context_token import build_context_token_write_cmd

                # sandbox_id == container_id everywhere (enroll_sandbox uses it,
                # and destroy() unenrolls by container_id — the context-proxy
                # entry must match or revocation misses it).
                if watcher_job:
                    await proxy_mgr.enroll_context(container_id, sandbox_token)
                else:
                    # Raises when the proxy does not confirm the scope (an
                    # image built before scoping grants everything).
                    granted_scope = await proxy_mgr.enroll_context(container_id, sandbox_token, containers=scope)
                await docker.run_cmd(
                    docker.build_exec_cmd(container_id, build_context_token_write_cmd(sandbox_token)),
                    timeout=10,
                )
                log_tool_ready = True
                logger.info("context_proxy_provisioned", job_id=job_id, containers=granted_scope)
            except Exception as exc:
                logger.warning("context_proxy_provisioning_failed", job_id=job_id, error=str(exc))

        init_update = await initializer.initialize(
            InitContext(
                job_id=job_id,
                context=context,
                container_id=container_id,
                sandbox_token=sandbox_token,
                docker=docker,
                git_proxy_url=git_proxy_url,
                forge=forge,
                gitlab_url=gitlab_url,
                base_branch=base_branch,
                repo_preferences_repo=config["configurable"].get("repo_preferences_repo"),
                config=embry0_config,
                forge_credential=(
                    sandbox_mgr.last_forge_credential(container_id)
                    if hasattr(sandbox_mgr, "last_forge_credential")
                    else None
                ),
                emit=writer,
            )
        )
    except Exception as exc:
        logger.error("sandbox_init_failed", job_id=job_id, error=str(exc))
        writer(events.error(message=f"Sandbox init failed: {exc}", node="init"))
        # Clean up partially-created container to avoid leaks
        if container_id:
            try:
                await sandbox_mgr.destroy(container_id)
            except Exception:
                logger.warning("sandbox_cleanup_failed", container_id=container_id)
        from embry0.workspace_init import WorkspaceInitError

        if isinstance(exc, WorkspaceInitError):
            # Re-raise unwrapped so error_code_for_exception maps the
            # specific code (local-denied / http-fetch) instead of SANDBOX_INIT.
            raise
        raise RuntimeError(f"Sandbox initialization failed: {exc}") from exc

    writer(events.node_completed(node="init"))
    update: dict[str, Any] = {
        "sandbox_container_id": container_id,
        "current_stage": "initialized",
        "retry_count": 0,
        "base_branch": base_branch,
        "base_branch_source": base_branch_source,
        **(init_update or {}),
    }
    if log_tool_ready and watcher_job:
        update["watcher_log_tool"] = True
    elif log_tool_ready and granted_scope:
        update["case_log_containers"] = list(granted_scope)
    if case_evidence:
        update["case_evidence"] = case_evidence
    return update
