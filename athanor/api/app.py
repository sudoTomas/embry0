"""FastAPI application factory with async lifespan management."""

import asyncio
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from athanor.api.middleware.csrf import CSRFMiddleware
from athanor.api.middleware.rate_limit import RateLimitMiddleware
from athanor.branding import API_TITLE
from athanor.config import AthanorConfig
from athanor.storage.database import DatabasePool
from athanor.storage.migrations.runner import run_migrations
from athanor.storage.repositories.agent_definitions import AgentDefinitionsRepository
from athanor.storage.repositories.budget_config import BudgetConfigRepository
from athanor.storage.repositories.context_config import ContextConfigRepository
from athanor.storage.repositories.environment import EnvironmentRepository
from athanor.storage.repositories.integration_config import IntegrationConfigRepository
from athanor.storage.repositories.jobs import JobsRepository
from athanor.storage.repositories.pipeline_templates import PipelineTemplatesRepository
from athanor.storage.repositories.provider_config import ProviderConfigRepository
from athanor.storage.repositories.repo_preferences import RepoPreferencesRepository
from athanor.storage.repositories.sandbox_profiles import SandboxProfilesRepository
from athanor.storage.repositories.traces import TracesRepository
from athanor.workflows.issue_to_pr.graph import IssueToprWorkflow
from athanor.workflows.registry import WorkflowRegistry

logger = structlog.get_logger(__name__)


def _resolve_proxy_admin_token(config: AthanorConfig) -> None:
    """Resolve PROXY_ADMIN_TOKEN. Generates in any dev-mode if missing; refuses in prod.

    Auto-generation is triggered by either auth_dev_mode or webhook_dev_mode so
    that the smee.io developer workflow (WEBHOOK_DEV_MODE=true AUTH_DEV_MODE=false)
    does not fail to boot when PROXY_ADMIN_TOKEN is unset.

    Args:
        config: Application configuration to resolve the token for.

    Raises:
        RuntimeError: If both dev-mode flags are False and proxy_admin_token is not set.
    """
    if config.proxy_admin_token:
        return
    if config.auth_dev_mode or config.webhook_dev_mode:
        config.proxy_admin_token = secrets.token_urlsafe(32)
        logger.warning(
            "proxy_admin_token_generated_for_dev",
            msg="dev mode active and PROXY_ADMIN_TOKEN unset — generated a random one for this process. Set PROXY_ADMIN_TOKEN in .env to make it stable across restarts.",
        )
        return
    raise RuntimeError(
        "PROXY_ADMIN_TOKEN must be set in production. Generate one with "
        "`python -c 'import secrets; print(secrets.token_urlsafe(32))'` "
        "and add it to .env."
    )


def _warn_and_audit_dev_mode(flag_name: str, audit_log_path: object) -> None:
    """Emit a CRITICAL log and an audit event for an active dev-mode bypass flag."""
    logger.critical(
        f"{flag_name.lower()}_enabled",
        msg=f"{flag_name}=true bypasses security checks. NEVER use in production.",
    )
    try:
        from athanor.audit.logger import emit_audit_event

        emit_audit_event(
            "dev_mode_enabled",
            actor="system",
            details={"flag": flag_name},
            audit_log_path=audit_log_path,
        )
    except Exception:
        logger.warning("dev_mode_audit_emit_failed", flag=flag_name, exc_info=True)


async def _init_app_state(
    app: FastAPI,
    db: DatabasePool,
    *,
    database_url: str,
    github_token: str | None = None,
    **executor_kwargs: object,
) -> None:
    """Initialize app.state.* with repositories and services.

    Shared by both the production lifespan and the integration-test lifespan.
    Does NOT set up Docker/proxy/sandbox infrastructure (production-only) or
    ``app.state.background_tasks`` (caller's responsibility).
    Any extra keyword arguments are forwarded to IssueExecutor (e.g.
    ``audit_log_path``, ``config``, ``sandbox_manager``, ``agent_runner``,
    ``proxy_manager``).
    """
    from athanor.api.events.bus import EventBus
    from athanor.services.github_sync import GitHubSyncService
    from athanor.services.issue_executor import IssueExecutor
    from athanor.storage.repositories.issue_inputs import IssueInputsRepository
    from athanor.storage.repositories.issues import IssuesRepository

    app.state.db = db

    app.state.jobs_repo = JobsRepository(db)
    app.state.traces_repo = TracesRepository(db)
    app.state.profiles_repo = SandboxProfilesRepository(db)
    app.state.context_repo = ContextConfigRepository(db)
    app.state.budget_repo = BudgetConfigRepository(db)
    app.state.agent_defs_repo = AgentDefinitionsRepository(db)
    app.state.templates_repo = PipelineTemplatesRepository(db)
    app.state.integration_repo = IntegrationConfigRepository(db)
    app.state.provider_repo = ProviderConfigRepository(db)
    app.state.env_repo = EnvironmentRepository(db)
    app.state.repo_preferences_repo = RepoPreferencesRepository(db)

    app.state.issues_repo = IssuesRepository(db)
    app.state.inputs_repo = IssueInputsRepository(db)
    app.state.github_sync = GitHubSyncService(github_token=github_token)

    registry = WorkflowRegistry()
    registry.register(IssueToprWorkflow())
    app.state.workflow_registry = registry

    app.state.event_bus = EventBus()

    app.state.issue_executor = IssueExecutor(
        issues_repo=app.state.issues_repo,
        jobs_repo=app.state.jobs_repo,
        traces_repo=app.state.traces_repo,
        workflow_registry=registry,
        database_url=database_url,
        db=db,
        inputs_repo=app.state.inputs_repo,
        event_bus=app.state.event_bus,
        env_repo=app.state.env_repo,
        repo_preferences_repo=app.state.repo_preferences_repo,
        **executor_kwargs,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config: AthanorConfig = app.state.config
    logger.info("athanor_starting")

    db = DatabasePool(config.database_url)
    await db.connect()
    await run_migrations(db)

    # Recover orphaned jobs from previous orchestrator lifecycle
    try:
        from athanor.safety.error_codes import ErrorCode

        orphaned = await db.fetch("SELECT job_id FROM jobs WHERE status IN ('running', 'pending')")
        if orphaned:
            orphaned_ids = [r["job_id"] for r in orphaned]
            logger.warning("recovering_orphaned_jobs", count=len(orphaned_ids), job_ids=orphaned_ids)
            await db.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    error_message = 'Orchestrator restarted — job was orphaned',
                    error_code = $1
                WHERE status IN ('running', 'pending')
                """,
                ErrorCode.ORPHANED.value,
            )
            # Append synthetic node_failed events to job_logs so the frontend
            # event timeline reflects the orphaned state
            import json as _json
            from datetime import UTC, datetime

            now_iso = datetime.now(UTC).isoformat()
            for jid in orphaned_ids:
                synthetic = {
                    "type": "node_completed",
                    "node": "developer",
                    "action": "orphaned",
                    "error": "Orchestrator restarted — job was orphaned",
                    "timestamp": now_iso,
                }
                try:
                    await db.execute(
                        "INSERT INTO job_logs (job_id, stream, text) VALUES ($1, $2, $3)",
                        jid,
                        "pipeline",
                        _json.dumps(synthetic, default=str),
                    )
                except Exception:
                    pass

            # Also reset any issues stuck in triaging/awaiting_input for these jobs
            await db.execute(
                """
                UPDATE issues SET status = 'open'
                WHERE status IN ('triaging', 'awaiting_input')
                AND id IN (
                    SELECT issue_id FROM jobs
                    WHERE job_id = ANY($1::text[]) AND issue_id IS NOT NULL
                )
                """,
                orphaned_ids,
            )
            logger.info("orphaned_jobs_recovered", count=len(orphaned_ids))
    except Exception:
        logger.exception("orphan_recovery_failed")

    # Orphan inputs: purge issue_inputs whose parent job is no longer in
    # awaiting_input or paused. Closes the window where a crash between
    # creating inputs and transitioning job status would leave stale rows.
    # Safe to run unguarded: no executor is live yet (app.state.issue_executor
    # is built below); no concurrent _handle_needs_info can race this DELETE.
    # If future refactors move executor construction earlier, revisit this.
    try:
        purged = await db.execute(
            """
            DELETE FROM issue_inputs
            WHERE job_id IN (
                SELECT job_id FROM jobs
                WHERE status NOT IN ('awaiting_input', 'paused')
            )
            """
        )
        # execute returns a status string like "DELETE 5"; log it for observability
        logger.info("orphan_inputs_purged", status=purged)
    except Exception:
        logger.exception("orphan_inputs_purge_failed")

    # Stale awaiting_input check — surface jobs waiting for user input longer
    # than 24h so ops can nudge the user or clean up dead sessions.
    try:
        stale = await db.fetch(
            """
            SELECT job_id, updated_at
            FROM jobs
            WHERE status = 'awaiting_input'
              AND updated_at < NOW() - INTERVAL '24 hours'
            ORDER BY updated_at ASC
            LIMIT 50
            """
        )
        if stale:
            logger.warning(
                "stale_awaiting_input_jobs",
                count=len(stale),
                oldest_job_id=stale[0]["job_id"] if stale else None,
                msg="Jobs have been awaiting user input for >24h. Consider cancelling or following up.",
            )
    except Exception:
        logger.warning("stale_awaiting_input_check_failed", exc_info=True)

    from athanor.execution.agent_runner import AgentRunner
    from athanor.execution.docker_client import DockerClient
    from athanor.execution.image_manager import ContainerReaper, SandboxImageManager
    from athanor.execution.proxy.manager import ProxyManager
    from athanor.execution.sandbox_manager import SandboxManager

    docker = DockerClient(
        docker_host=config.docker_host,
        tls_verify=config.docker_tls_verify,
        cert_path=config.docker_cert_path,
    )

    # Warn loudly and emit audit rows if any dev-mode bypass is active.
    if config.auth_dev_mode:
        _warn_and_audit_dev_mode("AUTH_DEV_MODE", config.audit_log_path)
    if config.webhook_dev_mode:
        _warn_and_audit_dev_mode("WEBHOOK_DEV_MODE", config.audit_log_path)

    # Resolve PROXY_ADMIN_TOKEN before constructing ProxyManager: the constructor
    # raises ValueError on an empty token, so in auth_dev_mode with PROXY_ADMIN_TOKEN
    # unset this call must run first to generate (and set) a valid token.
    _resolve_proxy_admin_token(config)

    proxy_mgr = ProxyManager(docker, proxy_admin_token=config.proxy_admin_token)
    sandbox_mgr = SandboxManager(docker, proxy_manager=proxy_mgr)
    agent_runner = AgentRunner(sandbox_mgr, docker)

    app.state.docker = docker
    app.state.sandbox_manager = sandbox_mgr
    app.state.agent_runner = agent_runner
    app.state.proxy_manager = proxy_mgr

    image_mgr = SandboxImageManager(docker)
    app.state.image_manager = image_mgr

    # Auto-build sandbox image on startup
    try:
        built = await image_mgr.ensure_image("athanor-sandbox:latest")
        if built:
            logger.info("sandbox_image_auto_built")
        else:
            logger.info("sandbox_image_already_current")
    except Exception as exc:
        logger.warning("sandbox_image_auto_build_failed", error=str(exc))

    # Start container reaper
    reaper = ContainerReaper(
        docker,
        max_age_hours=24,
        db=db,
        paused_ttl_hours=config.paused_job_ttl_hours,
        jobs_repo=app.state.jobs_repo,
    )
    reaper.start()
    app.state.container_reaper = reaper

    # Verify the proxy image is loaded in DinD before starting proxies.
    if not await image_mgr.check_proxy_image_present():
        logger.error(
            "proxy_image_missing_in_dind",
            image="athanor-proxy:latest",
            msg=(
                "athanor-proxy:latest is not loaded into DinD. The credential "
                "proxies cannot start. Build the image on the host and load it: "
                "`docker build -t athanor-proxy:latest -f infra/Dockerfile.proxy . "
                "&& cd infra && docker save athanor-proxy:latest | docker compose "
                "exec -T orchestrator docker --host tcp://dind:2376 --tlsverify "
                "--tlscacert=/certs/client/ca.pem --tlscert=/certs/client/cert.pem "
                "--tlskey=/certs/client/key.pem load`"
            ),
        )

    # Verify sandbox networks are present and restricted. We do NOT auto-create:
    # silently making `sandbox-restricted` without enable_ip_masquerade=false would
    # leave sandboxes with direct internet egress and bypass every credential proxy.
    # The init-sandbox-networks compose service runs setup-sandbox-networks.sh once
    # at stack start. If it didn't run (or its output was clobbered), refuse to boot.
    await docker.assert_sandbox_networks_or_die()

    from athanor.agents.executor import _assert_sdk_supports_hooks
    _assert_sdk_supports_hooks()

    # Start proxy services
    await proxy_mgr.start(
        anthropic_api_key=config.anthropic_api_key,
        github_token=config.github_token,
        enable_auth_proxy=config.auth_proxy_enabled,
    )

    # Clean up orphaned sandbox containers from previous lifecycle
    try:
        cmd = docker._build_base_cmd()
        cmd.extend(["ps", "-a", "--filter", "name=sandbox-", "--format", "{{.Names}}"])
        output = await docker.run_cmd(cmd, timeout=15)
        total = 0
        failed = 0
        if output.strip():
            for container_name in output.strip().split("\n"):
                container_name = container_name.strip()
                if not container_name:
                    continue
                total += 1
                logger.info("cleaning_orphaned_container", container=container_name)
                try:
                    await sandbox_mgr.destroy(container_name)
                except Exception:
                    failed += 1
                    logger.warning("orphaned_container_cleanup_failed", container=container_name)
        if total:
            logger.info("orphaned_container_cleanup_summary", total=total, failed=failed)
        if failed:
            logger.warning(
                "orphaned_container_cleanup_partial",
                total=total,
                failed=failed,
                msg="Some orphaned containers could not be cleaned up; manual docker rm may be needed.",
            )
    except Exception:
        logger.warning("orphaned_container_scan_failed", exc_info=True)

    app.state.background_tasks: set[asyncio.Task] = set()

    await _init_app_state(
        app,
        db,
        database_url=config.database_url,
        github_token=config.github_token if hasattr(config, "github_token") else None,
        audit_log_path=config.audit_log_path,
        config=config,
        sandbox_manager=sandbox_mgr,
        agent_runner=agent_runner,
        proxy_manager=proxy_mgr,
    )
    app.state.issue_executor._background_tasks = app.state.background_tasks

    if config.telegram_bot_token and getattr(config, "telegram_webhook_url", ""):
        import secrets

        from athanor.notifications.telegram import register_webhook

        webhook_url = f"{config.telegram_webhook_url.rstrip('/')}/api/v1/telegram/callback"
        app.state.telegram_webhook_secret = secrets.token_hex(32)
        await register_webhook(config.telegram_bot_token, webhook_url, secret_token=app.state.telegram_webhook_secret)
        if not app.state.telegram_webhook_secret:
            raise RuntimeError(
                "Telegram is configured (bot_token + webhook_url set) but the "
                "webhook secret could not be established. Refusing to start: "
                "the callback would otherwise be reachable without auth."
            )
    else:
        app.state.telegram_webhook_secret = ""

    logger.info("athanor_started")
    yield

    logger.info("athanor_stopping")
    # Snapshot the task set BEFORE cancelling so the done-callback that
    # removes tasks from the set can't mutate what we're iterating.
    tasks = list(app.state.background_tasks)
    if tasks:
        for task in tasks:
            task.cancel()
        # Bounded wait — don't hang shutdown forever if a task refuses to cancel.
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=30,
            )
        except TimeoutError:
            logger.warning(
                "background_tasks_shutdown_timeout",
                remaining=len([t for t in tasks if not t.done()]),
            )
    await reaper.stop()
    await proxy_mgr.stop()
    await db.close()
    logger.info("athanor_stopped")


def create_app(
    config: AthanorConfig | None = None,
    *,
    lifespan_override: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    if config is None:
        config = AthanorConfig()

    app = FastAPI(title=API_TITLE, version="0.1.0", lifespan=lifespan_override or lifespan)
    app.state.config = config

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.allowed_cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RateLimitMiddleware, requests_per_minute=config.api_rate_limit_per_minute)
    app.add_middleware(CSRFMiddleware)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "service": "athanor"}

    _register_routers(app)
    return app


def _register_routers(app: FastAPI) -> None:
    from athanor.api.deps import require_auth
    from athanor.api.v1 import (
        agents,
        config,
        environment,
        github,
        graphs,
        health,
        issues,
        jobs,
        pipeline_templates,
        queue,
        repo_preferences,
        sandbox_profiles,
        sandboxes,
        stats,
        telegram,
        traces,
        webhooks,
    )
    from athanor.api.ws import streaming

    auth_deps = [require_auth]
    app.include_router(health.router, prefix="/api/v1", tags=["health"])
    app.include_router(agents.router, prefix="/api/v1", tags=["agents"], dependencies=auth_deps)
    app.include_router(issues.router, prefix="/api/v1", tags=["issues"], dependencies=auth_deps)
    app.include_router(jobs.router, prefix="/api/v1", tags=["jobs"], dependencies=auth_deps)
    app.include_router(graphs.router, prefix="/api/v1", tags=["graphs"], dependencies=auth_deps)
    app.include_router(sandbox_profiles.router, prefix="/api/v1", tags=["sandbox-profiles"], dependencies=auth_deps)
    app.include_router(sandboxes.router, prefix="/api/v1", tags=["sandboxes"], dependencies=auth_deps)
    app.include_router(config.router, prefix="/api/v1", tags=["config"], dependencies=auth_deps)
    app.include_router(stats.router, prefix="/api/v1", tags=["stats"], dependencies=auth_deps)
    app.include_router(traces.router, prefix="/api/v1", tags=["traces"], dependencies=auth_deps)
    app.include_router(queue.router, prefix="/api/v1", tags=["queue"], dependencies=auth_deps)
    app.include_router(pipeline_templates.router, prefix="/api/v1", tags=["pipeline-templates"], dependencies=auth_deps)
    app.include_router(environment.router, prefix="/api/v1", tags=["environment"], dependencies=auth_deps)
    app.include_router(repo_preferences.router, prefix="/api/v1", tags=["repo-preferences"], dependencies=auth_deps)
    app.include_router(github.router, prefix="/api/v1", tags=["github"], dependencies=auth_deps)
    app.include_router(webhooks.router, prefix="/api/v1", tags=["webhooks"])
    app.include_router(telegram.router, prefix="/api/v1", tags=["telegram"])
    app.include_router(streaming.router)
