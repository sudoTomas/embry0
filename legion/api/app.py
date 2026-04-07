"""FastAPI application factory with async lifespan management."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from legion.api.middleware.csrf import CSRFMiddleware
from legion.api.middleware.rate_limit import RateLimitMiddleware
from legion.branding import API_TITLE
from legion.config import LegionConfig
from legion.storage.database import DatabasePool
from legion.storage.migrations.runner import run_migrations
from legion.storage.repositories.agent_definitions import AgentDefinitionsRepository
from legion.storage.repositories.budget_config import BudgetConfigRepository
from legion.storage.repositories.context_config import ContextConfigRepository
from legion.storage.repositories.integration_config import IntegrationConfigRepository
from legion.storage.repositories.jobs import JobsRepository
from legion.storage.repositories.pipeline_templates import PipelineTemplatesRepository
from legion.storage.repositories.provider_config import ProviderConfigRepository
from legion.storage.repositories.sandbox_profiles import SandboxProfilesRepository
from legion.storage.repositories.traces import TracesRepository
from legion.workflows.issue_to_pr.graph import IssueToprWorkflow
from legion.workflows.registry import WorkflowRegistry

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config: LegionConfig = app.state.config
    logger.info("legion_starting")

    db = DatabasePool(config.database_url)
    await db.connect()
    await run_migrations(db)

    # Recover orphaned jobs from previous orchestrator lifecycle
    try:
        orphaned = await db.fetch("SELECT job_id FROM jobs WHERE status IN ('running', 'pending')")
        if orphaned:
            orphaned_ids = [r["job_id"] for r in orphaned]
            logger.warning("recovering_orphaned_jobs", count=len(orphaned_ids), job_ids=orphaned_ids)
            await db.execute(
                """
                UPDATE jobs
                SET status = 'failed',
                    error_message = 'Orchestrator restarted — job was orphaned'
                WHERE status IN ('running', 'pending')
                """
            )
            # Append synthetic node_failed events to job_logs so the frontend
            # event timeline reflects the orphaned state
            from datetime import UTC, datetime
            import json as _json

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

    from legion.services.github_sync import GitHubSyncService
    from legion.storage.repositories.issue_inputs import IssueInputsRepository
    from legion.storage.repositories.issues import IssuesRepository

    app.state.issues_repo = IssuesRepository(db)
    app.state.inputs_repo = IssueInputsRepository(db)
    app.state.github_sync = GitHubSyncService(
        github_token=config.github_token if hasattr(config, "github_token") else None
    )

    registry = WorkflowRegistry()
    registry.register(IssueToprWorkflow())
    app.state.workflow_registry = registry

    from legion.execution.agent_runner import AgentRunner
    from legion.execution.docker_client import DockerClient
    from legion.execution.image_manager import ContainerReaper, SandboxImageManager
    from legion.execution.proxy.manager import ProxyManager
    from legion.execution.sandbox_manager import SandboxManager
    from legion.services.issue_executor import IssueExecutor

    docker = DockerClient(
        docker_host=config.docker_host,
        tls_verify=config.docker_tls_verify,
        cert_path=config.docker_cert_path,
    )
    sandbox_mgr = SandboxManager(docker)
    agent_runner = AgentRunner(sandbox_mgr, docker)
    proxy_mgr = ProxyManager()

    app.state.docker = docker
    app.state.sandbox_manager = sandbox_mgr
    app.state.agent_runner = agent_runner
    app.state.proxy_manager = proxy_mgr

    image_mgr = SandboxImageManager(docker)
    app.state.image_manager = image_mgr

    # Auto-build sandbox image on startup
    try:
        built = await image_mgr.ensure_image("legion-sandbox:latest")
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
    )
    reaper.start()
    app.state.container_reaper = reaper

    # Start proxy services
    await proxy_mgr.start(
        anthropic_api_key=config.anthropic_api_key,
        github_token=config.github_token,
    )

    # Auto-create sandbox network (idempotent)
    try:
        base_cmd = docker._build_base_cmd()
        await docker.run_cmd(base_cmd + ["network", "create", "--driver", "bridge", "sandbox-restricted"])
    except RuntimeError:
        pass  # Network already exists

    # Clean up orphaned sandbox containers from previous lifecycle
    try:
        cmd = docker._build_base_cmd()
        cmd.extend(["ps", "-a", "--filter", "name=sandbox-", "--format", "{{.Names}}"])
        output = await docker.run_cmd(cmd, timeout=15)
        if output.strip():
            for container_name in output.strip().split("\n"):
                container_name = container_name.strip()
                if not container_name:
                    continue
                logger.info("cleaning_orphaned_container", container=container_name)
                try:
                    await sandbox_mgr.destroy(container_name)
                except Exception:
                    logger.warning("orphaned_container_cleanup_failed", container=container_name)
    except Exception:
        logger.warning("orphaned_container_scan_failed", exc_info=True)

    app.state.background_tasks: set[asyncio.Task] = set()
    app.state.event_subscribers: dict[str, list[asyncio.Queue]] = {}

    app.state.issue_executor = IssueExecutor(
        issues_repo=app.state.issues_repo,
        jobs_repo=app.state.jobs_repo,
        traces_repo=app.state.traces_repo,
        workflow_registry=registry,
        database_url=config.database_url,
        audit_log_path=config.audit_log_path,
        db=db,
        inputs_repo=app.state.inputs_repo,
        config=config,
        sandbox_manager=sandbox_mgr,
        agent_runner=agent_runner,
        proxy_manager=proxy_mgr,
        event_subscribers=app.state.event_subscribers,
    )
    app.state.issue_executor._background_tasks = app.state.background_tasks

    if config.telegram_bot_token and getattr(config, "telegram_webhook_url", ""):
        import secrets

        from legion.notifications.telegram import register_webhook

        webhook_url = f"{config.telegram_webhook_url.rstrip('/')}/api/v1/telegram/callback"
        app.state.telegram_webhook_secret = secrets.token_hex(32)
        await register_webhook(config.telegram_bot_token, webhook_url, secret_token=app.state.telegram_webhook_secret)
    else:
        app.state.telegram_webhook_secret = ""

    logger.info("legion_started")
    yield

    logger.info("legion_stopping")
    tasks = app.state.background_tasks
    if tasks:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    await reaper.stop()
    await proxy_mgr.stop()
    await db.close()
    logger.info("legion_stopped")


def create_app(config: LegionConfig | None = None) -> FastAPI:
    if config is None:
        config = LegionConfig()

    app = FastAPI(title=API_TITLE, version="0.1.0", lifespan=lifespan)
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
        return {"status": "ok", "service": "legion"}

    _register_routers(app)
    return app


def _register_routers(app: FastAPI) -> None:
    from legion.api.deps import require_auth
    from legion.api.v1 import (
        agents,
        config,
        graphs,
        health,
        issues,
        jobs,
        pipeline_templates,
        queue,
        sandbox_profiles,
        stats,
        telegram,
        traces,
        webhooks,
    )
    from legion.api.ws import streaming

    auth_deps = [require_auth]
    app.include_router(health.router, prefix="/api/v1", tags=["health"])
    app.include_router(agents.router, prefix="/api/v1", tags=["agents"], dependencies=auth_deps)
    app.include_router(issues.router, prefix="/api/v1", tags=["issues"], dependencies=auth_deps)
    app.include_router(jobs.router, prefix="/api/v1", tags=["jobs"], dependencies=auth_deps)
    app.include_router(graphs.router, prefix="/api/v1", tags=["graphs"], dependencies=auth_deps)
    app.include_router(sandbox_profiles.router, prefix="/api/v1", tags=["sandbox-profiles"], dependencies=auth_deps)
    app.include_router(config.router, prefix="/api/v1", tags=["config"], dependencies=auth_deps)
    app.include_router(stats.router, prefix="/api/v1", tags=["stats"], dependencies=auth_deps)
    app.include_router(traces.router, prefix="/api/v1", tags=["traces"], dependencies=auth_deps)
    app.include_router(queue.router, prefix="/api/v1", tags=["queue"], dependencies=auth_deps)
    app.include_router(pipeline_templates.router, prefix="/api/v1", tags=["pipeline-templates"], dependencies=auth_deps)
    app.include_router(webhooks.router, prefix="/api/v1", tags=["webhooks"])
    app.include_router(telegram.router, prefix="/api/v1", tags=["telegram"])
    app.include_router(streaming.router)
