"""FastAPI application factory with async lifespan management."""

import asyncio
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from embry0.api.middleware.body_size import BodySizeMiddleware
from embry0.api.middleware.csrf import CSRFMiddleware
from embry0.api.middleware.rate_limit import RateLimitMiddleware
from embry0.branding import API_TITLE
from embry0.cache.volume_manager import SharedVolumeManager
from embry0.config import Embry0Config
from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import run_migrations
from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository
from embry0.storage.repositories.budget_config import BudgetConfigRepository
from embry0.storage.repositories.context_config import ContextConfigRepository
from embry0.storage.repositories.environment import EnvironmentRepository
from embry0.storage.repositories.integration_config import IntegrationConfigRepository
from embry0.storage.repositories.jobs import JobsRepository
from embry0.storage.repositories.pipeline_templates import PipelineTemplatesRepository
from embry0.storage.repositories.provider_config import ProviderConfigRepository
from embry0.storage.repositories.qa_app_results import QAAppResultsRepository
from embry0.storage.repositories.qa_image_tags import QAImageTagsRepository
from embry0.storage.repositories.qa_run_metadata import QARunMetadataRepository
from embry0.storage.repositories.qa_volume_state import QAVolumeStateRepository
from embry0.storage.repositories.qa_workspace_provider_overrides import (
    QAWorkspaceProviderOverridesRepository,
)
from embry0.storage.repositories.repo_preferences import RepoPreferencesRepository
from embry0.storage.repositories.sandbox_profiles import SandboxProfilesRepository
from embry0.storage.repositories.traces import TracesRepository
from embry0.workflows.issue_to_pr.graph import IssueToprWorkflow
from embry0.workflows.registry import WorkflowRegistry

if TYPE_CHECKING:
    from embry0.storage.encryption import FernetSecretsProvider

logger = structlog.get_logger(__name__)


def _check_postgres_password(config: Embry0Config) -> None:
    """Refuse to start in production with the well-known default Postgres password."""
    from urllib.parse import urlparse

    url = urlparse(config.database_url)
    if url.password == "embry0" and not config.auth_dev_mode:
        raise RuntimeError(
            "Refusing to start: POSTGRES_PASSWORD is the insecure default 'embry0'. "
            "Generate a strong password with: openssl rand -hex 20\n"
            "Then update DATABASE_URL in .env and run:\n"
            "  docker exec embry0-postgres psql -U embry0 "
            "-c \"ALTER USER embry0 PASSWORD '<new-password>';\"\n"
            "  docker compose up -d --force-recreate orchestrator"
        )


def _check_api_key(config: Embry0Config) -> None:
    """Refuse to start in production when API_KEY is empty.

    ``verify_api_key`` skips all Bearer verification when the configured key is
    empty, so an empty API_KEY silently disables authentication for the whole
    API. Booting with an empty key is allowed only when a dev-mode flag is
    active (auth_dev_mode bypasses API auth anyway; webhook_dev_mode keeps the
    documented smee.io developer workflow bootable) — and then warns loudly.

    Args:
        config: Application configuration to check.

    Raises:
        RuntimeError: If both dev-mode flags are False and api_key is not set.
    """
    if config.api_key:
        return
    if config.auth_dev_mode or config.webhook_dev_mode:
        logger.warning(
            "api_key_empty_in_dev_mode",
            msg="dev mode active and API_KEY unset — API authentication is DISABLED for this process. Set API_KEY in .env before exposing the API beyond localhost.",
        )
        return
    raise RuntimeError(
        "API_KEY must be set in production — an empty API_KEY disables all API "
        "authentication. Generate one with "
        "`python -c 'import secrets; print(secrets.token_urlsafe(32))'` "
        "and add it to .env."
    )


def _resolve_proxy_admin_token(config: Embry0Config) -> None:
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


def _resolve_secrets_provider(config: Embry0Config) -> "FernetSecretsProvider":
    """Resolve and validate ENVIRONMENT_SECRET_KEY, returning a FernetSecretsProvider.

    In production (auth_dev_mode=False):
      - Raises RuntimeError if the key is empty.
      - Raises RuntimeError if the key equals the hardcoded example default.

    In dev mode (auth_dev_mode=True):
      - If the key is missing or default-valued, generates a process-local
        ephemeral key and logs CRITICAL (secrets encrypted this run cannot be
        decrypted after restart).
      - Otherwise uses the configured key.

    This mirrors the _resolve_proxy_admin_token pattern.
    """
    from cryptography.fernet import Fernet

    from embry0.storage.encryption import FernetSecretsProvider

    _INSECURE_DEFAULT = "embry0-dev-secret-key"
    key = config.environment_secret_key

    if not key or key == _INSECURE_DEFAULT:
        if config.auth_dev_mode:
            ephemeral = Fernet.generate_key().decode()
            logger.critical(
                "fernet_ephemeral_key_generated",
                msg=(
                    "auth_dev_mode active and ENVIRONMENT_SECRET_KEY is missing or default — "
                    "generated a random ephemeral key for this process. "
                    "Env-var secrets will NOT survive a restart. "
                    "Set ENVIRONMENT_SECRET_KEY in .env for persistent encryption."
                ),
            )
            return FernetSecretsProvider(secret_key=ephemeral)
        raise RuntimeError(
            "ENVIRONMENT_SECRET_KEY must be set to a strong random value in production. "
            'Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" '
            "and add it to .env. "
            + (
                "The current value is the insecure example default — rotate it immediately."
                if key == _INSECURE_DEFAULT
                else "The key is currently empty."
            )
        )

    return FernetSecretsProvider(secret_key=key)


def _warn_and_audit_dev_mode(flag_name: str, audit_log_path: object) -> None:
    """Emit a CRITICAL log and an audit event for an active dev-mode bypass flag."""
    from pathlib import Path

    logger.critical(
        f"{flag_name.lower()}_enabled",
        msg=f"{flag_name}=true bypasses security checks. NEVER use in production.",
    )
    try:
        from embry0.audit.logger import emit_audit_event

        path = audit_log_path if isinstance(audit_log_path, Path) else None
        emit_audit_event(
            "dev_mode_enabled",
            actor="system",
            details={"flag": flag_name},
            audit_log_path=path,
        )
    except Exception:
        logger.warning("dev_mode_audit_emit_failed", flag=flag_name, exc_info=True)


# EMB-45: how often the orchestrator refreshes + re-pushes the SuperGrok access
# token to the xai-proxy. Must be shorter than the refresher's expiry margin
# (REFRESH_MARGIN_SECONDS = 30m) so the proxy always holds a comfortably-valid
# token across rotations; ensure_fresh() is cheap when the cached token is current.
_XAI_TOKEN_PUSH_INTERVAL_SECONDS = 600


async def _push_initial_xai_token(
    refresher: object,
    proxy_mgr: object,
    *,
    attempts: int = 5,
    delay_seconds: float = 2.0,
) -> None:
    """Push the first access token to the just-(re)created xai-proxy, with retries.

    The proxy container's listener can lag the docker-run return by a moment, so a
    single immediate push races it (observed: connection-refused ~200ms after
    start on 2 of 3 boots). A failed initial push isn't fatal — the 10-minute
    refresh loop self-heals — but it leaves grok 503ing until that first tick,
    so retry briefly instead.
    """
    for attempt in range(1, attempts + 1):
        try:
            token = await refresher.ensure_fresh()  # type: ignore[attr-defined]
            await proxy_mgr.push_xai_token(token)  # type: ignore[attr-defined]
            return
        except Exception as exc:
            if attempt == attempts:
                logger.error("xai_proxy_initial_token_push_failed", error=str(exc), attempts=attempts)
                return
            logger.warning("xai_proxy_initial_token_push_retry", attempt=attempt, error=str(exc))
            await asyncio.sleep(delay_seconds)


async def _xai_token_refresh_loop(refresher: object, proxy_mgr: object) -> None:
    """Periodically refresh the SuperGrok access token and push it to the xai-proxy.

    Runs until cancelled at shutdown. Failures are logged and retried on the next
    tick — a transient refresh/push error must not kill the loop.
    """
    while True:
        await asyncio.sleep(_XAI_TOKEN_PUSH_INTERVAL_SECONDS)
        try:
            token = await refresher.ensure_fresh()  # type: ignore[attr-defined]
            await proxy_mgr.push_xai_token(token)  # type: ignore[attr-defined]
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("xai_token_refresh_loop_error", error=str(exc))


async def _init_app_state(
    app: FastAPI,
    db: DatabasePool,
    *,
    database_url: str,
    github_token: str | None = None,
    github_comment_channel: object = None,
    linear_sync: object = None,
    **executor_kwargs: object,
) -> None:
    """Initialize app.state.* with repositories and services.

    Shared by both the production lifespan and the integration-test lifespan.
    Does NOT set up Docker/proxy/sandbox infrastructure (production-only) or
    ``app.state.background_tasks`` (caller's responsibility).
    Any extra keyword arguments are forwarded to IssueExecutor (e.g.
    ``audit_log_path``, ``config``, ``sandbox_manager``, ``agent_runner``,
    ``proxy_manager``).

    ``github_comment_channel`` is forwarded to IssueExecutor's constructor
    when provided. It must be built by the caller (lifespan) before this
    function is called so the executor has it available from construction
    onward.
    """
    from embry0.api.events.bus import EventBus
    from embry0.services.github_sync import GitHubSyncService
    from embry0.services.issue_executor import IssueExecutor
    from embry0.storage.repositories.agent_sessions import AgentSessionsRepository
    from embry0.storage.repositories.issue_inputs import IssueInputsRepository
    from embry0.storage.repositories.issues import IssuesRepository

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
    app.state.qa_app_results_repo = QAAppResultsRepository(db)
    app.state.qa_image_tags_repo = QAImageTagsRepository(db)
    app.state.qa_volume_state_repo = QAVolumeStateRepository(db)
    app.state.qa_run_metadata_repo = QARunMetadataRepository(db)
    # Phase 5G: dashboard-set per-repo workspace_provider overrides. Wired
    # through into qa_orchestrator_node via IssueExecutor; also exposed via
    # the /qa/admin/providers admin routes.
    app.state.qa_workspace_provider_overrides_repo = QAWorkspaceProviderOverridesRepository(db)
    # SharedVolumeManager requires app.state.docker, which is set in the lifespan
    # after docker is initialized. Set to None here; lifespan will construct
    # and set the real manager after docker is ready.
    app.state.qa_shared_volume_manager = None

    app.state.issues_repo = IssuesRepository(db)
    app.state.inputs_repo = IssueInputsRepository(db)
    app.state.agent_sessions_repo = AgentSessionsRepository(db)
    app.state.github_sync = GitHubSyncService(github_token=github_token)
    # EMB-47: Linear trigger + write-back — None when the integration is off;
    # the /webhook/linear route answers "not configured" in that case.
    app.state.linear_sync = linear_sync

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
        secrets_provider=getattr(app.state, "secrets_provider", None),
        # QA deps may be absent in tests / when MinIO is unconfigured; the
        # executor tolerates None and only init_qa_node will fail at runtime.
        qa_minio=getattr(app.state, "qa_minio", None),
        qa_minio_sandbox=getattr(app.state, "qa_minio_sandbox", None),
        qa_token_registry=getattr(app.state, "qa_token_registry", None),
        profiles_repo=app.state.profiles_repo,
        agent_sessions_repo=app.state.agent_sessions_repo,
        # Phase-A wiring: cache layers + dashboard reporting
        qa_app_results_repo=app.state.qa_app_results_repo,
        qa_image_tags_repo=app.state.qa_image_tags_repo,
        qa_volume_state_repo=app.state.qa_volume_state_repo,
        qa_shared_volume_manager=app.state.qa_shared_volume_manager,
        # Phase 5C: forward the SSE pub/sub to the executor when the lifespan
        # already constructed it on app.state. In _init_app_state's bare-bones
        # callers (older integration tests) the attribute may not be set; use
        # getattr to avoid breaking those.
        qa_event_bus=getattr(app.state, "qa_event_bus", None),
        # Phase 5D: forward the qa_run_metadata repo so qa_orchestrator_node
        # can persist the affected-set decision per run.
        qa_run_metadata_repo=app.state.qa_run_metadata_repo,
        # Phase 5G: dashboard-set workspace_provider overrides — read by
        # qa_orchestrator_node to layer over the qa.yaml file-based config.
        qa_workspace_provider_overrides_repo=(app.state.qa_workspace_provider_overrides_repo),
        github_token=github_token,
        github_comment_channel=github_comment_channel,
        linear_sync=linear_sync,
        **executor_kwargs,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config: Embry0Config = app.state.config
    from embry0.observability import configure_structlog

    # json=True in production (auth_dev_mode=False); human-readable in dev
    configure_structlog(json=not config.auth_dev_mode)
    logger.info("embry0_starting")

    # Refuse to start in production with the well-known default Postgres password.
    _check_postgres_password(config)

    # Refuse to start in production with an empty API_KEY (auth would be disabled).
    _check_api_key(config)

    db = DatabasePool(config.database_url)
    await db.connect()
    await run_migrations(db)

    # Seed canonical builtin sandbox profiles. Runs after migrations so the
    # sandbox_profiles schema (incl. QA columns from migration 18) is present.
    # The seed always overwrites builtin rows — local edits to a builtin
    # profile are intentionally discarded on every boot.
    from embry0.storage.repositories.sandbox_profiles import SandboxProfilesRepository
    from embry0.storage.seeds.sandbox_profiles_builtin import seed_builtin_sandbox_profiles

    await seed_builtin_sandbox_profiles(SandboxProfilesRepository(db))

    # Seed canonical builtin pipeline templates. Runs after migration 24
    # (is_builtin column on pipeline_templates). Idempotent — see
    # pipeline_templates_builtin.py.
    from embry0.storage.seeds.pipeline_templates_builtin import seed_builtin_pipeline_templates

    await seed_builtin_pipeline_templates(PipelineTemplatesRepository(db))

    # Seed the qa agent definition. Idempotent — upserts on every boot so the
    # canonical system prompt and MCP server config stay in sync with the code.
    from embry0.workflows.qa.agent_seed import seed_qa_agent

    await seed_qa_agent(AgentDefinitionsRepository(db))

    # Seed the onboarding agent definition (EMB-50), same idempotent pattern.
    from embry0.workflows.onboard.agent_seed import seed_onboarding_agent

    await seed_onboarding_agent(AgentDefinitionsRepository(db))

    # Insert any BUILTIN_SEED agents missing from the DB (RAV-604: agents
    # added after a deployment's first migration run — research/analysis/ops).
    # Insert-if-missing only; POST /agents/{type}/reset is the restore path.
    from embry0.storage.seeds.agents_builtin import seed_missing_builtin_agents

    await seed_missing_builtin_agents(AgentDefinitionsRepository(db))

    # MinIO — QA artifact storage.
    #
    # Two clients because the orchestrator and the sandbox see MinIO via
    # different hostnames:
    #
    # * ``qa_minio`` (internal endpoint, e.g. ``minio:9000``) — used for
    #   bucket admin (ensure bucket / set lifecycle) and also for any
    #   orchestrator-side reads (e.g. dashboard download URLs).
    #
    # * ``qa_minio_sandbox`` (sandbox-facing endpoint, e.g.
    #   ``minio-proxy:9100``) — used by the presign endpoint to mint URLs the
    #   sandbox can actually reach. The hostname is signed into the URL, so
    #   the sandbox's Host header on the resulting PUT must match what the
    #   orchestrator signed. Phase 1.5.
    if config.minio_root_user and config.minio_root_password:
        from embry0.execution.qa.minio_client import QAMinioClient

        qa_minio = QAMinioClient(
            endpoint=config.minio_endpoint,
            access_key=config.minio_root_user,
            secret_key=config.minio_root_password,
            secure=False,
        )
        try:
            await qa_minio.ensure_bucket("qa-artifacts")
            await qa_minio.set_lifecycle_policy("qa-artifacts", expire_days=config.qa_artifact_retention_days)
            app.state.qa_minio = qa_minio
            # Sandbox-facing client: same creds, different endpoint. We do
            # NOT call ensure_bucket / set_lifecycle here — that work
            # belongs to the internal client; this one only mints URLs.
            app.state.qa_minio_sandbox = QAMinioClient(
                endpoint=config.minio_sandbox_endpoint,
                access_key=config.minio_root_user,
                secret_key=config.minio_root_password,
                secure=False,
            )
            logger.info(
                "qa_minio_ready",
                retention_days=config.qa_artifact_retention_days,
                internal_endpoint=config.minio_endpoint,
                sandbox_endpoint=config.minio_sandbox_endpoint,
            )
        except Exception as exc:
            logger.warning(
                "qa_minio_unavailable",
                error=str(exc),
                msg="QA pipelines will fail; orchestrator continues so other features work.",
                exc_info=True,
            )
            app.state.qa_minio = None
            app.state.qa_minio_sandbox = None
    else:
        logger.warning(
            "qa_minio_unconfigured",
            msg="MINIO_ROOT_USER / MINIO_ROOT_PASSWORD not set — QA pipelines will fail.",
        )
        app.state.qa_minio = None
        app.state.qa_minio_sandbox = None

    from embry0.execution.qa.token_registry import SandboxTokenRegistry

    app.state.qa_token_registry = SandboxTokenRegistry()

    # Phase 5C: in-process pub/sub for QA run-state changes. The SSE route
    # at GET /api/v1/qa/runs/{run_id}/events subscribes; the orchestrator's
    # subtask_nodes.emit_result_node and qa_orchestrator_node publish.
    # Single-process by design; the dashboard's 15s polling fallback covers
    # any client that connects late or reconnects after a drop.
    from embry0.qa.event_bus import QAEventBus

    app.state.qa_event_bus = QAEventBus()

    # Recover orphaned jobs from previous orchestrator lifecycle
    try:
        from embry0.safety.error_codes import ErrorCode

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

    from embry0.execution.agent_runner import AgentRunner
    from embry0.execution.docker_client import DockerClient
    from embry0.execution.image_manager import ContainerReaper, SandboxImageManager
    from embry0.execution.image_registry import qualify_image
    from embry0.execution.proxy.manager import ProxyManager
    from embry0.execution.sandbox_manager import SandboxManager

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

    # Validate and cache the Fernet secrets provider — single PBKDF2 derivation
    # per process (390k iterations). Raises RuntimeError in production if the key
    # is missing or still set to the insecure example default.
    app.state.secrets_provider = _resolve_secrets_provider(config)

    proxy_mgr = ProxyManager(
        docker,
        proxy_admin_token=config.proxy_admin_token,
        image_registry=config.image_registry,
    )
    sandbox_mgr = SandboxManager(
        docker,
        proxy_manager=proxy_mgr,
        image_registry=config.image_registry,
        github_token=config.github_token,
    )
    agent_runner = AgentRunner(sandbox_mgr, docker)

    app.state.docker = docker
    app.state.sandbox_manager = sandbox_mgr
    app.state.agent_runner = agent_runner
    app.state.proxy_manager = proxy_mgr

    image_mgr = SandboxImageManager(docker, image_registry=config.image_registry)
    app.state.image_manager = image_mgr

    # Auto-build sandbox image on startup
    try:
        built = await image_mgr.ensure_image("embry0-sandbox:latest")
        if built:
            logger.info("sandbox_image_auto_built")
        else:
            logger.info("sandbox_image_already_current")
    except Exception as exc:
        logger.warning("sandbox_image_auto_build_failed", error=str(exc))

    # Start container reaper
    # _init_app_state runs later in this lifespan, so app.state.jobs_repo
    # isn't yet set. Construct a fresh JobsRepository here — they're cheap
    # (just hold the DB pool reference). Plan F / Task 9 follow-up.
    reaper = ContainerReaper(
        docker,
        max_age_hours=24,
        db=db,
        paused_ttl_hours=config.paused_job_ttl_hours,
        jobs_repo=JobsRepository(db),
        database_url=config.database_url,
    )
    reaper.start()
    app.state.container_reaper = reaper

    # Verify the proxy image is reachable from DinD before starting proxies.
    # The compose stack's init-push-images service tags + pushes the host-built
    # embry0-proxy image to the sidecar registry; DinD pulls on first reference.
    if not await image_mgr.check_proxy_image_present():
        logger.error(
            "proxy_image_missing_in_dind",
            image=qualify_image("embry0-proxy:latest", config.image_registry),
            msg=(
                "embry0-proxy image not present in DinD. Either init-push-images "
                "did not run (check `docker compose logs init-push-images`) or the "
                "host image is missing — run "
                "`docker compose --profile images build && docker compose up -d "
                "--force-recreate init-push-images orchestrator`."
            ),
        )

    # Verify sandbox networks are present and restricted. We do NOT auto-create:
    # silently making `sandbox-restricted` without enable_ip_masquerade=false would
    # leave sandboxes with direct internet egress and bypass every credential proxy.
    # The init-sandbox-networks compose service runs setup-sandbox-networks.sh once
    # at stack start. If it didn't run (or its output was clobbered), refuse to boot.
    await docker.assert_sandbox_networks_or_die()

    # EMB-29: probe DOCKER-USER egress enforcement (helper image + iptables
    # in DinD's netns). Non-fatal by design — enforcement itself fails
    # CLOSED per-sandbox at create time, so an unavailable helper at boot
    # only means an early operator warning instead of a bricked deploy.
    from embry0.execution.egress import EgressEnforcer

    await EgressEnforcer(docker).verify_ready()

    from embry0.agents.executor import _assert_sdk_supports_hooks

    _assert_sdk_supports_hooks()

    # EMB-45: build the SuperGrok credential refresher BEFORE starting proxies so a
    # misconfigured credential disables the xai-proxy (grok falls back to the CLI
    # path) rather than launching an un-provisioned proxy that 503s every request.
    app.state.xai_refresher = None
    xai_enabled = config.xai_proxy_enabled
    if xai_enabled:
        try:
            from embry0.execution.xai_credential import DEFAULT_GROK_CLI_STORE, build_refresher

            grok_store = config.xai_grok_cli_store or DEFAULT_GROK_CLI_STORE
            app.state.xai_refresher = await build_refresher(
                store_path=config.xai_credential_path,
                secret_key=config.environment_secret_key,
                grok_cli_store=grok_store,
            )
            logger.info("xai_refresher_ready")
        except Exception as exc:
            logger.error("xai_proxy_disabled_credential_error", error=str(exc))
            xai_enabled = False
            app.state.xai_refresher = None

    # Start proxy services
    await proxy_mgr.start(
        anthropic_api_key=config.anthropic_api_key,
        github_token=config.github_token,
        enable_auth_proxy=config.auth_proxy_enabled,
        enable_xai_proxy=xai_enabled,
    )

    # Provision the xai-proxy with an initial access token so the first grok request
    # doesn't race the background refresh loop.
    if app.state.xai_refresher is not None:
        await _push_initial_xai_token(app.state.xai_refresher, proxy_mgr)

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

    app.state.background_tasks = set()

    # EMB-45: keep the xai-proxy's access token fresh across 6h rotations.
    if app.state.xai_refresher is not None:
        _xai_task = asyncio.create_task(_xai_token_refresh_loop(app.state.xai_refresher, proxy_mgr))
        app.state.background_tasks.add(_xai_task)
        _xai_task.add_done_callback(app.state.background_tasks.discard)

    # GitHub-comment outbound channel — used by ask-user fan-out when an issue
    # is GitHub-synced and "github" is in its notification_channels. The HTTP
    # client is constructed once with base_url + Authorization pre-set so the
    # channel itself is auth-free; closed at shutdown below.
    #
    # Built BEFORE _init_app_state so the channel can be passed into
    # IssueExecutor's constructor via the proper kwarg path (rather than
    # mutating a private attribute on an already-constructed executor).
    import httpx

    from embry0.notifications.github_comment import GitHubCommentChannel

    github_http_headers = {"Accept": "application/vnd.github+json"}
    if config.github_token:
        github_http_headers["Authorization"] = f"Bearer {config.github_token}"
    github_http_client = httpx.AsyncClient(
        base_url="https://api.github.com",
        headers=github_http_headers,
        timeout=httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
        transport=httpx.AsyncHTTPTransport(retries=2),
    )
    app.state.github_http_client = github_http_client

    # Item 2: warn when dashboard_public_url is localhost in production. The
    # link in the GitHub comment is unreachable from outside the host
    # otherwise.
    dashboard_url = config.dashboard_public_url or "http://localhost:8200"
    if dashboard_url.startswith("http://localhost") and not (config.auth_dev_mode or config.webhook_dev_mode):
        logger.warning(
            "dashboard_public_url_localhost_in_prod",
            url=dashboard_url,
            msg="GitHub-comment deep-links will be unreachable from outside the host. "
            "Set DASHBOARD_PUBLIC_URL to your dashboard's public URL.",
        )

    from embry0.execution.github_tokens import resolve_for_repo

    github_comment_channel = GitHubCommentChannel(
        http_client=github_http_client,
        dashboard_base_url=dashboard_url,
        token_resolver=lambda repo: resolve_for_repo(repo, config.github_token or ""),
    )
    app.state.github_comment_channel = github_comment_channel

    # EMB-47: Linear trigger + write-back — constructed only when configured.
    linear_sync = None
    if config.linear_api_key:
        from embry0.services.linear_sync import LinearSyncService, parse_repo_map

        linear_sync = LinearSyncService(config.linear_api_key, parse_repo_map(config.linear_repo_map))
        logger.info("linear_sync_enabled", repo_map=parse_repo_map(config.linear_repo_map))

    await _init_app_state(
        app,
        db,
        database_url=config.database_url,
        github_token=config.github_token if hasattr(config, "github_token") else None,
        github_comment_channel=github_comment_channel,
        linear_sync=linear_sync,
        audit_log_path=config.audit_log_path,
        config=config,
        sandbox_manager=sandbox_mgr,
        agent_runner=agent_runner,
        proxy_manager=proxy_mgr,
    )

    # Now that _init_app_state has populated the repos and docker is ready,
    # construct SharedVolumeManager and update both the placeholder on app.state
    # and the reference stored in the executor (which was initialized with None
    # earlier when docker was not yet ready).
    real_manager = SharedVolumeManager(
        docker=app.state.docker,
        state_repo=app.state.qa_volume_state_repo,
    )
    app.state.qa_shared_volume_manager = real_manager
    app.state.issue_executor._qa_shared_volume_manager = real_manager

    app.state.issue_executor._background_tasks = app.state.background_tasks

    # Daily orphan checkpoint sweep — purges checkpoint rows for job_ids
    # that no longer exist in the jobs table (covers cases where per-job
    # purge was missed due to an orchestrator crash).
    from embry0.orchestration.checkpoint import sweep_orphan_checkpoints as _sweep_checkpoints

    async def _daily_checkpoint_sweep() -> None:
        while True:
            await asyncio.sleep(86400)  # 24 h
            try:
                n = await _sweep_checkpoints(config.database_url)
                logger.info("orphan_checkpoints_swept", rows_deleted=n)
            except Exception:
                logger.warning("orphan_checkpoint_sweep_failed", exc_info=True)

    sweep_task = asyncio.create_task(_daily_checkpoint_sweep())
    app.state.background_tasks.add(sweep_task)
    sweep_task.add_done_callback(app.state.background_tasks.discard)

    if config.telegram_bot_token and getattr(config, "telegram_webhook_url", ""):
        import secrets

        from embry0.notifications.telegram import register_webhook

        webhook_url = f"{config.telegram_webhook_url.rstrip('/')}/api/v1/telegram/callback"
        app.state.telegram_webhook_secret = secrets.token_hex(32)
        await register_webhook(config.telegram_bot_token, webhook_url, secret_token=app.state.telegram_webhook_secret)
        # register_webhook raises on failure (which propagates out of lifespan and
        # crashes startup). The runtime handler additionally fail-closes with 503
        # if app.state.telegram_webhook_secret is somehow empty at request time.
    else:
        app.state.telegram_webhook_secret = ""

    logger.info("embry0_started")
    yield

    logger.info("embry0_stopping")
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
    # EMB-45: the refresh loop was cancelled with the other background tasks above;
    # close the refresher's HTTP client now.
    xai_refresher = getattr(app.state, "xai_refresher", None)
    if xai_refresher is not None:
        try:
            await xai_refresher.aclose()
        except Exception:
            logger.warning("xai_refresher_close_failed", exc_info=True)
    await proxy_mgr.stop()
    # Close the long-lived GitHub HTTP client used by the github-comment channel.
    gh_client = getattr(app.state, "github_http_client", None)
    if gh_client is not None:
        try:
            await gh_client.aclose()
        except Exception:
            logger.warning("github_http_client_close_failed", exc_info=True)
    await db.close()
    logger.info("embry0_stopped")


def create_app(
    config: Embry0Config | None = None,
    *,
    lifespan_override: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    if config is None:
        config = Embry0Config()

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
    app.add_middleware(BodySizeMiddleware, max_bytes=config.max_webhook_body_bytes)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "embry0"}

    from fastapi import Request
    from fastapi.responses import JSONResponse

    from embry0.storage.repositories.jobs import StatusTransitionConflict

    @app.exception_handler(StatusTransitionConflict)
    async def status_conflict_handler(request: Request, exc: StatusTransitionConflict) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": "Job or issue status has already changed; retry if needed."},
        )

    _register_routers(app)

    # Prometheus metrics endpoint — LAN-only by network topology (not via cloudflared)
    from prometheus_fastapi_instrumentator import Instrumentator

    Instrumentator().instrument(app).expose(app, endpoint="/metrics")

    return app


def _register_routers(app: FastAPI) -> None:
    from embry0.api.deps import require_auth
    from embry0.api.v1 import (
        agents,
        bootstrap,
        config,
        environment,
        github,
        health,
        internal_qa,
        issues,
        jobs,
        pipeline_templates,
        qa_admin,
        qa_artifacts,
        qa_config,
        qa_dashboard,
        qa_events,
        qa_images,
        queue,
        repo_preferences,
        sandbox_profiles,
        sandboxes,
        stats,
        telegram,
        traces,
        webhooks,
    )
    from embry0.api.ws import streaming

    auth_deps = [require_auth]
    app.include_router(health.router, prefix="/api/v1", tags=["health"])
    app.include_router(agents.router, prefix="/api/v1", tags=["agents"], dependencies=auth_deps)
    app.include_router(issues.router, prefix="/api/v1", tags=["issues"], dependencies=auth_deps)
    app.include_router(jobs.router, prefix="/api/v1", tags=["jobs"], dependencies=auth_deps)
    app.include_router(qa_artifacts.router, prefix="/api/v1", tags=["qa-artifacts"], dependencies=auth_deps)
    app.include_router(qa_dashboard.router, prefix="/api/v1", tags=["qa-dashboard"], dependencies=auth_deps)
    # Phase 5G: dashboard admin surface for per-repo workspace_provider
    # overrides. Same Bearer auth as the rest of /qa/...
    app.include_router(qa_admin.router, prefix="/api/v1", tags=["qa-admin"], dependencies=auth_deps)
    # EMB-42: per-repo pre-baked QA cache-image builds. Synchronous and
    # minutes-long on a fresh build — callers use a long HTTP timeout.
    app.include_router(qa_images.router, prefix="/api/v1", tags=["qa-images"], dependencies=auth_deps)
    # Phase 5C: SSE liveness for /qa/runs/{run_id}/events. Same auth posture
    # as qa_dashboard — the EventSource browser API can carry cookies but not
    # custom Authorization headers, so production-Bearer deploys 401 here and
    # rely on the dashboard's 15s polling fallback.
    app.include_router(qa_events.router, prefix="/api/v1", tags=["qa-events"], dependencies=auth_deps)
    app.include_router(sandbox_profiles.router, prefix="/api/v1", tags=["sandbox-profiles"], dependencies=auth_deps)
    app.include_router(sandboxes.router, prefix="/api/v1", tags=["sandboxes"], dependencies=auth_deps)
    app.include_router(config.router, prefix="/api/v1", tags=["config"], dependencies=auth_deps)
    app.include_router(stats.router, prefix="/api/v1", tags=["stats"], dependencies=auth_deps)
    app.include_router(traces.router, prefix="/api/v1", tags=["traces"], dependencies=auth_deps)
    app.include_router(queue.router, prefix="/api/v1", tags=["queue"], dependencies=auth_deps)
    app.include_router(pipeline_templates.router, prefix="/api/v1", tags=["pipeline-templates"], dependencies=auth_deps)
    app.include_router(environment.router, prefix="/api/v1", tags=["environment"], dependencies=auth_deps)
    app.include_router(repo_preferences.router, prefix="/api/v1", tags=["repo-preferences"], dependencies=auth_deps)
    app.include_router(qa_config.router, prefix="/api/v1", tags=["qa-config"], dependencies=auth_deps)
    app.include_router(bootstrap.router, prefix="/api/v1", tags=["bootstrap"], dependencies=auth_deps)
    app.include_router(github.router, prefix="/api/v1", tags=["github"], dependencies=auth_deps)
    app.include_router(webhooks.router, prefix="/api/v1", tags=["webhooks"])
    app.include_router(telegram.router, prefix="/api/v1", tags=["telegram"])
    # Internal sandbox-only endpoints — auth is via per-sandbox bearer
    # token in the request body, not via session cookies, so these paths
    # are also exempted from CSRF middleware.
    app.include_router(internal_qa.router, prefix="/api/v1", tags=["internal-qa"])
    app.include_router(streaming.router)
