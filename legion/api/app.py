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
    from legion.storage.repositories.issues import IssuesRepository
    app.state.issues_repo = IssuesRepository(db)
    app.state.github_sync = GitHubSyncService(github_token=config.github_token if hasattr(config, "github_token") else None)

    registry = WorkflowRegistry()
    registry.register(IssueToprWorkflow())
    app.state.workflow_registry = registry

    from legion.services.issue_executor import IssueExecutor
    app.state.issue_executor = IssueExecutor(
        issues_repo=app.state.issues_repo,
        jobs_repo=app.state.jobs_repo,
        traces_repo=app.state.traces_repo,
        workflow_registry=registry,
        database_url=config.database_url,
        audit_log_path=config.audit_log_path,
    )

    app.state.background_tasks: set[asyncio.Task] = set()
    app.state.event_subscribers: dict[str, list[asyncio.Queue]] = {}

    logger.info("legion_started")
    yield

    logger.info("legion_stopping")
    tasks = app.state.background_tasks
    if tasks:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
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
    from legion.api.v1 import agents, config, graphs, health, issues, jobs, pipeline_templates, queue, sandbox_profiles, stats, traces, webhooks
    from legion.api.ws import streaming

    auth_deps = [require_auth]
    app.include_router(health.router, prefix="/api/v1", tags=["health"])
    app.include_router(agents.router, prefix="/api/v1", tags=["agents"])
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
    app.include_router(streaming.router)
