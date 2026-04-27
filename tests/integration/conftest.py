"""Integration test fixtures — requires running PostgreSQL."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from athanor.api.app import create_app
from athanor.config import AthanorConfig
from athanor.services.issue_executor import IssueExecutor
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


@pytest.fixture(scope="session")
def database_url() -> str:
    return os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql://athanor:athanor@localhost:5432/athanor_integration_test",
    )


@pytest.fixture(scope="session")
async def setup_database(database_url: str) -> AsyncIterator[str]:
    """Create and migrate the test database."""
    try:
        sys_url = database_url.rsplit("/", 1)[0] + "/postgres"
        sys_conn = await asyncpg.connect(sys_url)
        db_name = database_url.rsplit("/", 1)[1]
        try:
            await sys_conn.execute(f"CREATE DATABASE {db_name}")
        except asyncpg.DuplicateDatabaseError:
            pass
        await sys_conn.close()

        db = DatabasePool(database_url)
        await db.connect()
        await run_migrations(db)
        await db.close()

        yield database_url

        sys_conn = await asyncpg.connect(sys_url)
        await sys_conn.execute(f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)")
        await sys_conn.close()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")


def _make_test_lifespan(database_url: str):
    """Return an ASGI lifespan that initialises only DB repos (no Docker/proxy)."""

    @asynccontextmanager
    async def test_lifespan(app):
        db = DatabasePool(database_url)
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
        app.state.env_repo = EnvironmentRepository(db)
        app.state.repo_preferences_repo = RepoPreferencesRepository(db)

        from athanor.api.events.bus import EventBus
        from athanor.services.github_sync import GitHubSyncService
        from athanor.storage.repositories.issue_inputs import IssueInputsRepository
        from athanor.storage.repositories.issues import IssuesRepository

        app.state.issues_repo = IssuesRepository(db)
        app.state.inputs_repo = IssueInputsRepository(db)
        app.state.github_sync = GitHubSyncService(github_token=None)

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
        )

        yield

        await db.close()

    return test_lifespan


@pytest.fixture
async def app(setup_database: str) -> AsyncIterator[AsyncClient]:
    """Create a test FastAPI app with real database (no Docker/proxy)."""
    config = AthanorConfig(
        _env_file=None,
        database_url=setup_database,
        dev_mode=True,
        api_key="",
        github_webhook_secret="test-secret",
    )
    test_lifespan = _make_test_lifespan(setup_database)
    fastapi_app = create_app(config, lifespan_override=test_lifespan)

    # Manually drive the lifespan context so DB repos are available before
    # any HTTP request is made via ASGITransport (which does not send lifespan
    # events automatically).
    async with test_lifespan(fastapi_app):
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
