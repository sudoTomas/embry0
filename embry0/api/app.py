"""FastAPI application factory with async lifespan management."""

import asyncio
import os
import secrets
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from embry0.api.middleware.body_size import BodySizeMiddleware
from embry0.api.middleware.csrf import CSRFMiddleware
from embry0.api.middleware.rate_limit import RateLimitMiddleware
from embry0.branding import API_TITLE
from embry0.cache.prebaked_staleness import BaseImageDigestResolver
from embry0.cache.volume_manager import SharedVolumeManager
from embry0.config import Embry0Config
from embry0.config_redaction import mask_url_credentials
from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import run_migrations
from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository
from embry0.storage.repositories.api_keys import ApiKeysRepository
from embry0.storage.repositories.audit import AuditRepository
from embry0.storage.repositories.budget_config import BudgetConfigRepository
from embry0.storage.repositories.context_config import ContextConfigRepository
from embry0.storage.repositories.deliverables import DeliverablesRepository
from embry0.storage.repositories.environment import EnvironmentRepository
from embry0.storage.repositories.integration_config import IntegrationConfigRepository
from embry0.storage.repositories.jobs import JobsRepository
from embry0.storage.repositories.pipeline_templates import PipelineTemplatesRepository
from embry0.storage.repositories.provider_config import ProviderConfigRepository
from embry0.storage.repositories.qa_app_results import QAAppResultsRepository
from embry0.storage.repositories.qa_image_tags import QAImageTagsRepository
from embry0.storage.repositories.qa_run_metadata import QARunMetadataRepository
from embry0.storage.repositories.qa_volume_state import QAVolumeStateRepository
from embry0.storage.repositories.repo_preferences import RepoPreferencesRepository
from embry0.storage.repositories.repo_qa_configs import RepoQAConfigsRepository
from embry0.storage.repositories.repos import ReposRepository
from embry0.storage.repositories.sandbox_profiles import SandboxProfilesRepository
from embry0.storage.repositories.traces import TracesRepository
from embry0.storage.repositories.users import UsersRepository
from embry0.workflows.issue_to_pr.graph import IssueToprWorkflow
from embry0.workflows.registry import WorkflowRegistry

if TYPE_CHECKING:
    from embry0.execution.qa.minio_client import QAMinioClient
    from embry0.execution.runtime import AgentRuntime
    from embry0.storage.encryption import FernetSecretsProvider

logger = structlog.get_logger(__name__)


def _check_postgres_password(config: Embry0Config) -> None:
    """Refuse to start in production with the well-known default Postgres password."""
    from urllib.parse import urlparse

    url = urlparse(config.database_url)
    if url.password == "embry0" and not config.auth_dev_mode:
        # #324: no psql command here. The role and database are
        # deployment-specific (POSTGRES_USER / POSTGRES_DB), and a second copy
        # of the STEPS is how the first one went stale. The one procedure is
        # in docs/ops/backup-restore.md (#529 moved it out of CLAUDE.md), and
        # it reads both names from the container env. The
        # password constraint is repeated rather than referenced: an operator
        # who reads only this message must not pick a password that breaks the
        # URL, and unlike the steps it cannot drift - compose either
        # interpolates the value or it does not.
        raise RuntimeError(
            "Refusing to start: POSTGRES_PASSWORD is the insecure default 'embry0'.\n"
            "Rotate it with the procedure in the repo's docs/ops/backup-restore.md, section "
            "'Upgrading POSTGRES_PASSWORD on a live deploy' - it changes the role's password "
            "inside Postgres (role and database come from POSTGRES_USER / POSTGRES_DB, never typed), "
            "then the env files.\n"
            "The value the compose stack reads is POSTGRES_PASSWORD in infra/.env: compose splices it "
            "into the orchestrator's DATABASE_URL and hands it to the backup container. Keep "
            "POSTGRES_PASSWORD in the root .env equal to it.\n"
            "The password must be URL-safe and compose-safe: any of @ / : # % ? breaks the URL and "
            "$ is interpolated by compose. Use only A-Z a-z 0-9 _ - ; generate one with:\n"
            "  python -c 'import secrets; print(secrets.token_urlsafe(32))'"
        )


def _check_api_key(config: Embry0Config) -> None:
    """Refuse to start with an empty API_KEY unless AUTH_DEV_MODE is on (#518).

    AUTH_DEV_MODE is the one switch that turns API authentication off; with
    it an empty key is allowed, and warned about. Without it an empty key is
    refused whatever else is set: WEBHOOK_DEV_MODE skips webhook signature
    checks and nothing more, so the smee.io recipe needs an API_KEY like any
    other deployment. The auth dependencies fail closed on an empty key as
    well; this check makes the misconfiguration a clear startup error.

    Args:
        config: Application configuration to check.

    Raises:
        RuntimeError: If api_key is empty and auth_dev_mode is False.
    """
    if config.api_key:
        return
    if config.auth_dev_mode:
        logger.warning(
            "api_key_empty_in_dev_mode",
            msg="AUTH_DEV_MODE active and API_KEY unset — API authentication is off for this process. Never expose it beyond localhost.",
        )
        return
    webhook_note = (
        " WEBHOOK_DEV_MODE does not change that: it skips webhook signature checks only."
        if config.webhook_dev_mode
        else ""
    )
    raise RuntimeError(
        "API_KEY must be set: an empty API_KEY is accepted only with AUTH_DEV_MODE=true, "
        "which turns API authentication off for a local-only instance."
        + webhook_note
        + " Generate a key with `python -c 'import secrets; print(secrets.token_urlsafe(32))'` "
        "and add it to .env."
    )


#: Env names that carry a public-ingress tunnel token. docs/webhooks.md puts it
#: in the repo-root .env, the orchestrator's env_file. Read off the environment
#: rather than declared as config: the orchestrator never uses the token, it
#: only needs to know that one is configured. A token kept only in infra/.env,
#: for compose interpolation, is not visible to this process.
_TUNNEL_TOKEN_ENV: tuple[str, ...] = ("CLOUDFLARED_TUNNEL_TOKEN", "TUNNEL_TOKEN")


def _check_dev_mode_exposure(config: Embry0Config, environ: Mapping[str, str]) -> None:
    """Refuse a dev-mode flag on a deployment with a public origin (#518).

    SSO (AUTH0_* or SSO_PUBLIC_ORIGIN) or a tunnel token means the instance is
    reachable from the internet. There AUTH_DEV_MODE opens the whole API to
    anyone, and WEBHOOK_DEV_MODE accepts unsigned deliveries on every webhook
    route that has no secret, each of which can dispatch a job. The refusal
    names the settings involved, never their values.

    Args:
        config: Application configuration to check.
        environ: The process environment, where a tunnel token would be set.

    Raises:
        RuntimeError: If a dev-mode flag is set while a public origin is configured.
    """
    flags = [
        name
        for name, enabled in (("AUTH_DEV_MODE", config.auth_dev_mode), ("WEBHOOK_DEV_MODE", config.webhook_dev_mode))
        if enabled
    ]
    if not flags:
        return
    public = [
        name
        for name, value in (
            ("AUTH0_ISSUER", config.auth0_issuer),
            ("AUTH0_CLIENT_ID", config.auth0_client_id),
            ("AUTH0_CLIENT_SECRET", config.auth0_client_secret),
            ("SSO_PUBLIC_ORIGIN", config.sso_public_origin),
        )
        if value
    ]
    public += [name for name in _TUNNEL_TOKEN_ENV if environ.get(name, "").strip()]
    if not public:
        return
    raise RuntimeError(
        f"Refusing to start: {' and '.join(flags)} is set on a deployment with a public origin "
        f"({', '.join(public)} set). The dev-mode flags are for a local instance only: unset them, "
        "or, for local development, unset the SSO and tunnel settings. See docs/webhooks.md."
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

    from embry0.storage.encryption import INSECURE_EXAMPLE_SECRET_KEY as _INSECURE_DEFAULT
    from embry0.storage.encryption import FernetSecretsProvider

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

    return FernetSecretsProvider(secret_key=key, old_secret_keys=config.environment_secret_keys_old_list)


def _check_sso_config(config: Embry0Config) -> None:
    """Validate the Auth0 SSO block (RAV-1069): all-or-nothing.

    A partially-set SSO config is always a mistake (e.g. a forgotten
    SESSION_SECRET would silently disable SSO while the operator believes
    it's live), so any partial set refuses startup. Fully unset = SSO off,
    valid deployment (LAN API-key only).
    """
    fields = {
        "AUTH0_ISSUER": config.auth0_issuer,
        "AUTH0_CLIENT_ID": config.auth0_client_id,
        "AUTH0_CLIENT_SECRET": config.auth0_client_secret,
        "SESSION_SECRET": config.session_secret,
        "SSO_PUBLIC_ORIGIN": config.sso_public_origin,
        # #393: no default any more — it named one deployment's domain. It has
        # to be here rather than merely documented: a missing claim is not an
        # outage, it is every user authenticating successfully with zero roles
        # and being refused by default-deny, with nothing in the logs to say
        # the claim was the reason.
        "SSO_ROLES_CLAIM": config.sso_roles_claim,
    }
    set_fields = [k for k, v in fields.items() if v]
    if not set_fields:
        logger.info("sso_configured", enabled=False)
        return
    missing = [k for k, v in fields.items() if not v]
    if missing:
        raise RuntimeError(
            f"Partial SSO configuration: {sorted(set_fields)} set but {sorted(missing)} missing — set all or none."
        )
    if len(config.session_secret) < 32:
        raise RuntimeError("SESSION_SECRET must be at least 32 characters (it signs the SSO session cookie).")
    if not config.auth0_issuer.startswith("https://"):
        raise RuntimeError("AUTH0_ISSUER must be an https:// URL.")
    logger.info(
        "sso_configured",
        enabled=True,
        issuer=mask_url_credentials(config.auth0_issuer),
        origin=mask_url_credentials(config.sso_public_origin),
        roles_claim=config.sso_roles_claim,
    )


def _load_gitlab_ca_pem(config: Embry0Config) -> str:
    """PEM text for ``GITLAB_CA_BUNDLE``, or "" when none is configured.

    Read once at startup (spec 1A §3.2) and fanned out to the three clients
    that need it. Raises :class:`~embry0.execution.tls.CABundleError` — an
    unreadable bundle refuses the boot, because the alternative is an
    orchestrator that looks healthy and fails every GitLab clone with a TLS
    handshake error naming nothing.
    """
    from embry0.execution.tls import load_ca_pem

    pem = load_ca_pem(config.gitlab_ca_bundle)
    if pem:
        logger.info("gitlab_ca_bundle_loaded", path=config.gitlab_ca_bundle, bytes=len(pem))
    return pem


def _required_image_pins() -> set[str]:
    """The images that must run by digest: the proxy, which holds the forge and
    admin tokens, and every builtin sandbox image, which runs an agent's job.
    init-push-images pushes exactly these (tests/unit/infra pins the two lists)."""
    from embry0.execution.proxy.manager import _BASE_IMAGE as proxy_image
    from embry0.storage.seeds.sandbox_profiles_builtin import BUILTIN_SANDBOX_PROFILES

    return {proxy_image} | {fields["base_image"] for fields in BUILTIN_SANDBOX_PROFILES.values()}


def _configure_image_registry(config: Embry0Config, environ: Mapping[str, str]) -> None:
    """Log in to the image registry and install the image pins, or refuse to start (#523).

    Runs before anything resolves an image name: ProxyManager qualifies its
    image in its constructor. Each half is off when its setting is empty; the
    compose stack sets both.
    """
    from embry0.execution.image_registry import ImagePinError, install_image_pins, load_image_pins
    from embry0.execution.registry_auth import (
        RegistryAuthError,
        docker_config_dir,
        read_registry_credential,
        registry_host,
        write_docker_login,
    )

    if (config.registry_auth_dir or config.image_pins_file) and not config.image_registry:
        raise RuntimeError(
            "Refusing to start: REGISTRY_AUTH_DIR or IMAGE_PINS_FILE is set but IMAGE_REGISTRY is empty, "
            "so there is no registry to log in to or pin images in. Set IMAGE_REGISTRY in the root .env "
            "(registry:5000 for the compose stack)."
        )

    if config.registry_auth_dir:
        host = registry_host(config.image_registry)
        try:
            username, password = read_registry_credential(config.registry_auth_dir)
            path = write_docker_login(docker_config_dir(environ), host, username, password)
        except RegistryAuthError as exc:
            raise RuntimeError(
                f"Refusing to start: {exc}. The registry refuses anonymous pulls, so every sandbox and "
                "proxy launch would fail. The registry-auth-init compose service writes this login: "
                "check `docker compose logs registry-auth-init`."
            ) from exc
        logger.info("registry_login_configured", registry=host, docker_config=str(path))

    if config.image_pins_file:
        try:
            pins = load_image_pins(config.image_pins_file)
        except ImagePinError as exc:
            raise RuntimeError(
                f"Refusing to start: {exc}. init-push-images records the pins as it pushes the images: "
                "check `docker compose logs init-push-images`, then recreate it and the orchestrator."
            ) from exc
        missing = sorted(_required_image_pins() - pins.keys())
        if missing:
            raise RuntimeError(
                f"Refusing to start: {config.image_pins_file} has no pin for {', '.join(missing)}, "
                "which would then run by tag. init-push-images pins every image it pushes: build the "
                "missing images (`docker compose --profile images build`), then recreate "
                "init-push-images and the orchestrator."
            )
        install_image_pins(config.image_registry, pins)
        logger.info("image_pins_installed", registry=config.image_registry, images=sorted(pins))
    elif config.image_registry:
        logger.warning(
            "image_pins_not_configured",
            registry=config.image_registry,
            msg=(
                "IMAGE_PINS_FILE is unset: embry0 images run by tag, so anything that can push to "
                "the registry can replace the image a proxy or sandbox runs."
            ),
        )


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


def _warn_unknown_env_vars(environ: Mapping[str, str]) -> None:
    """Warn about env vars that look like a misspelled config field (#248)
    and about anything under an embry0 prefix that is not a field at all
    (Spec 1A §3.8).

    Embry0Config ignores unknown env vars by design, so a typo is otherwise
    invisible until it surfaces as a downstream symptom. Advisory only —
    never fails startup.
    """
    try:
        from embry0.config import unknown_prefixed_env_keys
        from embry0.config_lint import declared_env_names, find_unknown_env_vars

        for finding in find_unknown_env_vars(environ, declared_env_names(Embry0Config)):
            logger.warning("unknown_env_var", name=finding.name, did_you_mean=finding.did_you_mean)

        unknown = unknown_prefixed_env_keys(environ)
        if unknown:
            logger.warning(
                "unknown_env_keys",
                keys=unknown,
                msg="set under an embry0 prefix but not a config field — ignored, check for a typo",
            )
    except Exception:
        logger.warning("unknown_env_var_scan_failed", exc_info=True)


# EMB-45: how often the orchestrator refreshes + re-pushes the SuperGrok access
# token to the xai-proxy. Must be shorter than the refresher's expiry margin
# (REFRESH_MARGIN_SECONDS = 30m) so the proxy always holds a comfortably-valid
# token across rotations; ensure_fresh() is cheap when the cached token is current.
_XAI_TOKEN_PUSH_INTERVAL_SECONDS = 600
# How often the loop reads the xai-proxy's per-account upstream counts. A stale-token
# 403 is repaired by a forced refresh, so it should not wait a whole push interval.
_XAI_HEALTH_POLL_SECONDS = 60


async def _push_initial_xai_token(
    registry: object,
    proxy_mgr: object,
    *,
    attempts: int = 5,
    delay_seconds: float = 2.0,
) -> None:
    """Push each registered account's access token, with retries.

    The proxy container's listener can lag the docker-run return by a moment, so a
    single immediate push races it (observed: connection-refused ~200ms after
    start on 2 of 3 boots). A failed initial push isn't fatal — the 10-minute
    refresh loop self-heals — but it leaves grok 503ing until that first tick,
    so retry briefly instead. The file-backed refresher is not called here: it
    would rotate a lineage the registry also holds.
    """
    for attempt in range(1, attempts + 1):
        try:
            refreshed = await registry.refresh_all()  # type: ignore[attr-defined]
            for credential_id, (token, expires_at) in refreshed.items():
                await proxy_mgr.push_xai_token(credential_id, token, expires_at)  # type: ignore[attr-defined]
            return
        except Exception as exc:
            if attempt == attempts:
                logger.error(
                    "xai_proxy_initial_token_push_failed",
                    error_type=type(exc).__name__,
                    attempts=attempts,
                )
                return
            logger.warning(
                "xai_proxy_initial_token_push_retry",
                attempt=attempt,
                error_type=type(exc).__name__,
            )
            await asyncio.sleep(delay_seconds)


async def _clean_orphaned_sandboxes(runtime: "AgentRuntime", sandbox_mgr: object, jobs_repo: object) -> None:
    """Destroy the sandbox containers no live job owns (#484).

    A container that outlived the previous process belongs to a job that
    restart killed (orphan recovery has already failed those), unless its job
    is paused or awaiting input: that checkpoint names the container and the
    resume reuses it. Those are spared, using the same active-job set as the
    ContainerReaper. Fails closed like the reaper: without that set nothing is
    destroyed, and the reaper removes true orphans later. Never raises.
    """
    if jobs_repo is None:
        logger.warning("orphaned_container_scan_skipped", reason="no jobs repository")
        return
    try:
        active: set[str] = set(await jobs_repo.fetch_active_sandbox_containers())  # type: ignore[attr-defined]
    except Exception:
        logger.warning("orphaned_container_scan_skipped", reason="active jobs unavailable", exc_info=True)
        return
    try:
        from embry0.execution.sandbox_listing import list_sandbox_containers

        containers = await list_sandbox_containers(runtime, timeout=15)
        total = 0
        failed = 0
        for container_name in (c.name for c in containers):
            if container_name in active:
                logger.info("orphaned_container_kept_for_live_job", container=container_name)
                continue
            total += 1
            logger.info("cleaning_orphaned_container", container=container_name)
            try:
                await sandbox_mgr.destroy(container_name)  # type: ignore[attr-defined]
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


async def _xai_token_refresh_loop(registry: object, proxy_mgr: object, health: object) -> None:
    """Refresh every registered xAI account and push it; act on the proxy's upstream counts.

    The counts are read every ``_XAI_HEALTH_POLL_SECONDS``, the scheduled refresh runs every
    ``_XAI_TOKEN_PUSH_INTERVAL_SECONDS``. Runs until cancelled at shutdown. A per-row failure
    stays inside ``refresh_all``. Anything else is logged and retried on the next tick. The
    file-backed refresher is not called: one lineage must have one owner.
    """
    ticks_per_refresh = max(1, _XAI_TOKEN_PUSH_INTERVAL_SECONDS // _XAI_HEALTH_POLL_SECONDS)
    tick = 0
    while True:
        await asyncio.sleep(_XAI_HEALTH_POLL_SECONDS)
        tick += 1
        try:
            if tick % ticks_per_refresh == 0:
                refreshed = await registry.refresh_all()  # type: ignore[attr-defined]
                for credential_id, (token, expires_at) in refreshed.items():
                    await proxy_mgr.push_xai_token(credential_id, token, expires_at)  # type: ignore[attr-defined]
            report = await proxy_mgr.xai_proxy_health()  # type: ignore[attr-defined]
            await _apply_xai_proxy_health(report, registry, proxy_mgr, health)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("xai_token_refresh_loop_error", error_type=type(exc).__name__)


async def _apply_xai_proxy_health(report: object, registry: object, proxy_mgr: object, health: object) -> None:
    """Act on one read of the xai-proxy's per-account upstream counts.

    ``stale``: xAI could not validate the access token (embry0/safety/xai_denials.py). A
    refresh fixes that, so the account is force-refreshed and pushed, never quarantined;
    one account's failed refresh does not stop the others. ``429`` quarantines as rate
    limited and ``401`` as auth rejected, 401 last so a window that saw both keeps auth_rejected.
    """
    from embry0.safety.error_codes import ErrorCode

    credentials = report.get("credentials") if isinstance(report, dict) else None
    if not isinstance(credentials, dict):
        return
    for credential_id, bucket in credentials.items():
        if not isinstance(bucket, dict):
            continue
        if int(bucket.get("stale") or 0) > 0:
            try:
                token, expires_at = await registry.refresh_credential(credential_id, force=True)  # type: ignore[attr-defined]
                await proxy_mgr.push_xai_token(credential_id, token, expires_at)  # type: ignore[attr-defined]
                logger.info(
                    "xai_credential_force_refreshed", credential_id=credential_id, stale_hits=bucket.get("stale")
                )
            except Exception as exc:
                logger.warning(
                    "xai_credential_force_refresh_failed", credential_id=credential_id, error_type=type(exc).__name__
                )
        if int(bucket.get("429") or 0) > 0:
            await health.report(credential_id, ErrorCode.PROVIDER_RATE_LIMITED)  # type: ignore[attr-defined]
        if int(bucket.get("401") or 0) > 0:
            await health.report(credential_id, ErrorCode.AUTH_REJECTED)  # type: ignore[attr-defined]


# #278: the backfill reads ~30 proposals and is a no-op after its first run;
# a minute is generous. It must never be what keeps the API from coming up.
HEALING_BACKFILL_TIMEOUT_SECONDS = 60.0


async def _backfill_healing_cases(db: DatabasePool, config: Embry0Config) -> None:
    """Run the #278 watcher-proposal backfill; log and carry on if it fails
    or overruns. Called after run_migrations has released its advisory lock."""
    from embry0.services.healing import backfill
    from embry0.storage.repositories.healing_cases import HealingCasesRepository

    try:
        await asyncio.wait_for(
            backfill.backfill_watcher_proposals(HealingCasesRepository(db), default_repo=config.watcher_repo),
            timeout=HEALING_BACKFILL_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning("healing_backfill_timed_out", timeout_seconds=HEALING_BACKFILL_TIMEOUT_SECONDS)
    except Exception:
        logger.warning("healing_backfill_failed", exc_info=True)


async def _bring_up_qa_minio(
    app: FastAPI,
    internal: "QAMinioClient",
    sandbox: "QAMinioClient",
    *,
    expire_days: int,
    retry_delays: tuple[float, ...] = (1, 2, 5, 10, 30),
) -> None:
    """Prepare the QA bucket, retrying until MinIO answers, then publish both clients.

    Runs as a background task so the orchestrator boots without MinIO, and
    compose need not start MinIO first (#573). Until it returns, ``qa_minio``
    stays None: the QA routes answer 503 and a QA run fails fast, as
    before. The executor may already exist by then, so it is updated too.
    """
    await internal.prepare_bucket("qa-artifacts", expire_days=expire_days, retry_delays=retry_delays)
    app.state.qa_minio = internal
    app.state.qa_minio_sandbox = sandbox
    executor = getattr(app.state, "issue_executor", None)
    if executor is not None:
        executor._qa_minio = internal
        executor._qa_minio_sandbox = sandbox
    logger.info("qa_minio_ready", retention_days=expire_days)


def _build_credential_registry(app: FastAPI, db: DatabasePool) -> None:
    """Put the credential repository and resolver on app.state. Stores references only: no DB call."""
    from embry0.services.credentials.resolver import CredentialResolver
    from embry0.storage.repositories.credentials import CredentialsRepository

    app.state.credentials_repo = CredentialsRepository(db)
    app.state.credential_resolver = CredentialResolver(
        app.state.credentials_repo, getattr(app.state, "secrets_provider", None), app.state.config
    )


async def _init_app_state(
    app: FastAPI,
    db: DatabasePool,
    *,
    database_url: str,
    github_token: str | None = None,
    github_comment_channel: object = None,
    linear_sync: object = None,
    webhook_notifier: object = None,
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
    from embry0.notifications.tracker_comment import TrackerCommentChannel
    from embry0.services.github_sync import GitHubSyncService
    from embry0.services.issue_executor import IssueExecutor
    from embry0.storage.repo_renames import configure_repo_renames
    from embry0.storage.repositories.agent_sessions import AgentSessionsRepository
    from embry0.storage.repositories.dispatch_ledger import DispatchLedgerRepository
    from embry0.storage.repositories.issue_inputs import IssueInputsRepository
    from embry0.storage.repositories.issues import IssuesRepository
    from embry0.trackers import IssueTracker, TrackerRegistry

    app.state.db = db

    # #285: created by create_app; absent only when a test drives this
    # function against a bare app object.
    loop_metrics = getattr(app.state, "loop_metrics", None)
    app.state.jobs_repo = JobsRepository(db, metrics=loop_metrics)
    app.state.dispatch_ledger = DispatchLedgerRepository(db)
    app.state.traces_repo = TracesRepository(db, metrics=loop_metrics)
    app.state.users_repo = UsersRepository(db)
    app.state.api_keys_repo = ApiKeysRepository(db)
    app.state.audit_repo = AuditRepository(db)
    app.state.deliverables_repo = DeliverablesRepository(db)
    app.state.profiles_repo = SandboxProfilesRepository(db)
    app.state.context_repo = ContextConfigRepository(db)
    app.state.budget_repo = BudgetConfigRepository(db)
    app.state.agent_defs_repo = AgentDefinitionsRepository(db)
    app.state.templates_repo = PipelineTemplatesRepository(db)
    app.state.integration_repo = IntegrationConfigRepository(db)
    app.state.provider_repo = ProviderConfigRepository(db)
    app.state.env_repo = EnvironmentRepository(db)
    app.state.repo_preferences_repo = RepoPreferencesRepository(db)
    # The lifespan builds these before SandboxManager (which needs the
    # resolver); bare-bones callers that skip the lifespan get them here.
    if not hasattr(app.state, "credentials_repo"):
        _build_credential_registry(app, db)
    # The lifespan builds this next to the xAI refresher, before _init_app_state.
    # A caller that skips the lifespan still gets one, so a provider failure
    # can quarantine. Reuse the lifespan instance when it is already there.
    if not hasattr(app.state, "credential_health"):
        from embry0.services.credentials.health import CredentialHealth

        app.state.credential_health = CredentialHealth(app.state.credentials_repo, app.state.config)

    # RAV-1251: repo registry + DB-backed QA config. Spec 1A §3.8: the
    # registry's fallback forge is config-driven — DEFAULT_FORGE="" makes
    # forge_for raise instead of assuming github for unknown repos. Read
    # straight off app.state.config (create_app always sets it) so a missing
    # config fails loudly here rather than silently restoring the github
    # fallback the empty setting exists to remove.
    app_config: Embry0Config = app.state.config
    # #527: the operator's repo renames, installed process-wide before merge
    # sync, the PR webhook or the healing ledger normalise a URL through them.
    configure_repo_renames(app_config.repo_renames)
    app.state.repos_repo = ReposRepository(db, default_forge=app_config.default_forge)
    from embry0.storage.repositories.organizations import OrganizationsRepository

    app.state.organizations_repo = OrganizationsRepository(db)
    from embry0.storage.repositories.org_repos import OrgReposRepository

    app.state.org_repos_repo = OrgReposRepository(db)
    from embry0.storage.repositories.org_needs import OrgNeedsRepository

    app.state.org_needs_repo = OrgNeedsRepository(db)
    from embry0.storage.repositories.hosts import HostsRepository

    app.state.hosts_repo = HostsRepository(db)
    app.state.qa_configs_repo = RepoQAConfigsRepository(db, default_forge=app_config.default_forge)
    app.state.qa_app_results_repo = QAAppResultsRepository(db)
    app.state.qa_image_tags_repo = QAImageTagsRepository(db)
    app.state.qa_volume_state_repo = QAVolumeStateRepository(db)
    app.state.qa_run_metadata_repo = QARunMetadataRepository(db)
    # SharedVolumeManager requires app.state.docker, which is set in the lifespan
    # after docker is initialized. Set to None here; lifespan will construct
    # and set the real manager after docker is ready.
    app.state.qa_shared_volume_manager = None

    app.state.issues_repo = IssuesRepository(db)
    # #278: the healing-case lifecycle ledger. The repo serves the read API;
    # the service is the one write path every later writer goes through.
    from embry0.execution.github_client import GitHubClient
    from embry0.services.healing.forge import GitHubPrTimes
    from embry0.services.healing.recorder import CaseRecorder
    from embry0.services.healing.service import HealingCaseService
    from embry0.storage.repositories.healing_cases import HealingCasesRepository

    app.state.healing_cases_repo = HealingCasesRepository(db)
    app.state.healing_cases = HealingCaseService(app.state.healing_cases_repo, metrics=loop_metrics)
    # #278 part 2: the fail-open face of the ledger that the watcher, the
    # dispatch path and the job lifecycle report to.
    app.state.case_recorder = CaseRecorder(
        app.state.healing_cases,
        # A PR's opened_at must be the forge's, not the moment the job that
        # opened it happened to finish.
        pr_times=GitHubPrTimes(
            GitHubClient(default_token=github_token or "", credential_resolver=app.state.credential_resolver)
        ),
    )
    # #279/#280: the one recorder of what became of a job's PR — shared by the
    # pull_request webhook, the merge-sync sweep and the backfill CLI.
    from embry0.services.merge_sync.recorder import DeliveryRecorder
    from embry0.storage.repositories.job_delivery import JobDeliveryRepository

    app.state.job_delivery_repo = JobDeliveryRepository(db)
    app.state.delivery_recorder = DeliveryRecorder(
        app.state.job_delivery_repo, app.state.issues_repo, app.state.healing_cases
    )
    app.state.inputs_repo = IssueInputsRepository(db)
    app.state.agent_sessions_repo = AgentSessionsRepository(db)
    app.state.github_sync = GitHubSyncService(
        github_token=github_token, credential_resolver=app.state.credential_resolver
    )
    # EMB-47: Linear trigger + write-back — None when the integration is off;
    # the /webhook/linear route answers "not configured" in that case.
    app.state.linear_sync = linear_sync

    # #160: one registry over every configured tracker. The per-service
    # app.state slots above stay — outbound write-back still reads them
    # directly until the #163 channel work; the registry is the uniform
    # surface. The Linear route re-registers its per-delivery resolved
    # instance so the registry never holds stale credentials.
    trackers = TrackerRegistry()
    trackers.register(app.state.github_sync)
    if isinstance(linear_sync, IssueTracker):
        trackers.register(linear_sync)
    gitlab_sync = getattr(app.state, "gitlab_sync", None)
    if isinstance(gitlab_sync, IssueTracker):
        trackers.register(gitlab_sync)
    app.state.trackers = trackers

    # #163: comment channels for the tracker trail (questions / answers /
    # outcomes on the issue's originating tracker). Linear and GitLab only —
    # GitHub's surface is the opt-in `github` notification channel plus the
    # PR itself; an unconditional outcome comment there would be noise.
    # Channels resolve their tracker through the registry at call time, so
    # the Linear route's per-delivery credential refresh reaches them too.
    app.state.tracker_channels = {
        name: TrackerCommentChannel(trackers, name) for name in ("linear", "gitlab") if trackers.get(name) is not None
    }

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
        repos_repo=app.state.repos_repo,
        secrets_provider=getattr(app.state, "secrets_provider", None),
        # QA deps may be absent in tests / when MinIO is unconfigured; the
        # executor tolerates None and only a QA run will fail at runtime.
        qa_minio=getattr(app.state, "qa_minio", None),
        qa_minio_sandbox=getattr(app.state, "qa_minio_sandbox", None),
        qa_token_registry=getattr(app.state, "qa_token_registry", None),
        profiles_repo=app.state.profiles_repo,
        agent_sessions_repo=app.state.agent_sessions_repo,
        metrics=loop_metrics,
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
        github_token=github_token,
        credential_resolver=app.state.credential_resolver,
        credentials_repo=app.state.credentials_repo,
        credential_health=app.state.credential_health,
        github_comment_channel=github_comment_channel,
        tracker_channels=app.state.tracker_channels,
        webhook_notifier=webhook_notifier,
        case_recorder=app.state.case_recorder,
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

    # Refuse an empty API_KEY outside AUTH_DEV_MODE, and either dev-mode flag
    # on a deployment with a public origin (#518).
    _check_api_key(config)
    _check_dev_mode_exposure(config, os.environ)

    # Refuse to start with a partially-configured SSO stack (RAV-1069).
    _check_sso_config(config)

    # Refuse to start with an unreadable GITLAB_CA_BUNDLE (spec 1A §3.2).
    gitlab_ca_pem = _load_gitlab_ca_pem(config)
    app.state.gitlab_ca_pem = gitlab_ca_pem

    # Refuse to start without the registry login or the image pins the compose
    # stack configures (#523).
    _configure_image_registry(config, os.environ)

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

    # Seed the pull request reviewer (#30), same idempotent pattern.
    from embry0.workflows.pr_review.agent_seed import seed_pr_reviewer_agent

    await seed_pr_reviewer_agent(AgentDefinitionsRepository(db))

    # Insert any BUILTIN_SEED agents missing from the DB (RAV-604: agents
    # added after a deployment's first migration run — research/analysis/ops).
    # Insert-if-missing only; POST /agents/{type}/reset is the restore path.
    from embry0.storage.seeds.agents_builtin import seed_missing_builtin_agents

    await seed_missing_builtin_agents(AgentDefinitionsRepository(db))

    # #278: give every historical watcher proposal a healing case. Idempotent,
    # and fail-open — a ledger gap must never keep the orchestrator down.
    await _backfill_healing_cases(db, config)

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
    #
    # The bucket is prepared in the background, retrying until MinIO answers,
    # so neither a slow nor a missing MinIO holds the boot back and a late one
    # needs no restart (#573). The clients are published once it is ready.
    app.state.qa_minio = None
    app.state.qa_minio_sandbox = None
    qa_minio_task: asyncio.Task[None] | None = None
    if config.minio_root_user and config.minio_root_password:
        from embry0.execution.qa.minio_client import QAMinioClient

        qa_minio_task = asyncio.create_task(
            _bring_up_qa_minio(
                app,
                QAMinioClient(
                    endpoint=config.minio_endpoint,
                    access_key=config.minio_root_user,
                    secret_key=config.minio_root_password,
                    secure=False,
                ),
                # Sandbox-facing client: same creds, different endpoint. It
                # only mints URLs; the bucket work belongs to the internal one.
                QAMinioClient(
                    endpoint=config.minio_sandbox_endpoint,
                    access_key=config.minio_root_user,
                    secret_key=config.minio_root_password,
                    secure=False,
                ),
                expire_days=config.qa_artifact_retention_days,
            )
        )
    else:
        logger.warning(
            "qa_minio_unconfigured",
            msg="MINIO_ROOT_USER / MINIO_ROOT_PASSWORD not set — QA pipelines will fail.",
        )

    from embry0.execution.qa.token_registry import SandboxTokenRegistry

    app.state.qa_token_registry = SandboxTokenRegistry()

    # Phase 5C: in-process pub/sub for QA run-state changes. The SSE route
    # at GET /api/v1/qa/runs/{run_id}/events subscribes; the orchestrator's
    # subtask_nodes.emit_result_node and qa_orchestrator_node publish.
    # Single-process by design; the dashboard's 15s polling fallback covers
    # any client that connects late or reconnects after a drop.
    from embry0.qa.event_bus import QAEventBus

    app.state.qa_event_bus = QAEventBus()

    # Recover the jobs the previous orchestrator lifecycle left in flight.
    # Lifecycle knowledge lives in the repositories (RAV-1063): fail_orphaned /
    # reopen_stuck_for_jobs are the set-based boot-reconciliation writes.
    # RAV-1221: _init_app_state runs later in this lifespan, so the app.state
    # repos don't exist yet — build repos on the pool directly (same pattern
    # as the Linear-sync wiring below), or every restart skips reconciliation.
    # #307: jobs that can go on are taken back here and started at the end of
    # startup, once the executor and the proxies exist (_recover_jobs below);
    # the rest fail as before.
    recovering: list[dict[str, Any]] = []
    try:
        from embry0.orchestration.restart import take_back_jobs
        from embry0.safety.error_codes import ErrorCode
        from embry0.storage.repositories.issues import IssuesRepository

        boot_jobs_repo = JobsRepository(db, metrics=getattr(app.state, "loop_metrics", None))
        exhausted: list[str] = []
        try:
            recovering, exhausted = await take_back_jobs(boot_jobs_repo, db, max_attempts=config.max_resume_attempts)
        except Exception:
            # Recovery is the extra; failing the orphans is the floor.
            logger.exception("job_recovery_planning_failed")
            recovering = []
        orphan_message = "Orchestrator restarted — job was orphaned"
        # #583: a job queued for a run slot never ran. Since #307 one is
        # started again at boot only when its row records how (launch).
        not_started = "Orchestrator restarted before the job started; nothing ran. Dispatch it again."
        drained = (
            "Orchestrator shut down during the job and could not resume it "
            "(QA and onboard jobs, and jobs without a checkpoint, start over). Dispatch it again."
        )
        orphaned_ids = await boot_jobs_repo.fail_orphaned(
            ErrorCode.ORPHANED.value,
            orphan_message,
            never_started_message=not_started,
            keep=[job["job_id"] for job in recovering],
            interrupted_code=ErrorCode.INTERRUPTED_BY_SHUTDOWN.value,
            interrupted_message=drained,
        )
        orphaned_ids += exhausted
        if orphaned_ids:
            logger.warning("recovering_orphaned_jobs", count=len(orphaned_ids), job_ids=orphaned_ids)
            # The executor's credential-use stamp never runs for these jobs;
            # close their open ledger rows here. The app.state repo does not
            # exist yet (built after secrets_provider), so build one on the pool.
            from embry0.storage.repositories.credentials import CredentialsRepository as _BootCredentialsRepo

            boot_credentials_repo = _BootCredentialsRepo(db)
            for jid in orphaned_ids:
                outcome = ErrorCode.RESUME_LIMIT if jid in exhausted else ErrorCode.ORPHANED
                try:
                    await boot_credentials_repo.finish_uses(jid, outcome.value)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("credential_uses_finish_failed", job_id=jid, error=type(exc).__name__)
            # spec 3.5: the orphan path is a terminal transition. Nothing can be
            # subscribed before the server listens, so persist only (bus=None);
            # the WebSocket replay and the REST log serve it.
            from embry0.services.job_events import emit_job_finished

            boot_metrics = getattr(app.state, "loop_metrics", None)
            for jid in orphaned_ids:
                await emit_job_finished(boot_jobs_repo, None, boot_metrics, jid)

            # Also reset any issues stuck in triaging/awaiting_input for these jobs
            reopened = await IssuesRepository(db).reopen_stuck_for_jobs(orphaned_ids)
            logger.info("orphaned_jobs_recovered", count=len(orphaned_ids), issues_reopened=reopened)
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
    from embry0.execution.image_manager import ContainerReaper, SandboxImageManager
    from embry0.execution.image_registry import qualify_image
    from embry0.execution.proxy.manager import ProxyManager
    from embry0.execution.runtime import build_runtime
    from embry0.execution.sandbox_manager import SandboxManager

    # #268: the one place the container runtime is chosen. ``docker`` is its
    # CLI client, for the call sites that are not behind the seam yet.
    runtime = build_runtime(config)
    docker = runtime.client
    # Before anything is built, run or pulled on it: a rootless runtime
    # pointed at the wrong daemon refuses the start here.
    await runtime.verify_daemon()

    # Warn loudly and emit audit rows if any dev-mode bypass is active.
    if config.auth_dev_mode:
        _warn_and_audit_dev_mode("AUTH_DEV_MODE", config.audit_log_path)
    if config.webhook_dev_mode:
        _warn_and_audit_dev_mode("WEBHOOK_DEV_MODE", config.audit_log_path)

    # #248: env vars that look like a misspelled config field.
    _warn_unknown_env_vars(os.environ)

    # Resolve PROXY_ADMIN_TOKEN before constructing ProxyManager: the constructor
    # raises ValueError on an empty token, so in auth_dev_mode with PROXY_ADMIN_TOKEN
    # unset this call must run first to generate (and set) a valid token.
    _resolve_proxy_admin_token(config)

    # Validate and cache the Fernet secrets provider — single PBKDF2 derivation
    # per process (390k iterations). Raises RuntimeError in production if the key
    # is missing or still set to the insecure example default.
    app.state.secrets_provider = _resolve_secrets_provider(config)
    # Built here, not in _init_app_state: SandboxManager below needs the resolver.
    _build_credential_registry(app, db)
    from embry0.execution.xai_credential import XaiRefresherRegistry
    from embry0.services.credentials.health import CredentialHealth

    app.state.credential_health = CredentialHealth(app.state.credentials_repo, config)
    app.state.xai_refresher_registry = None
    if config.xai_proxy_enabled:
        app.state.xai_refresher_registry = XaiRefresherRegistry(
            app.state.credentials_repo,
            app.state.secrets_provider,
            health=app.state.credential_health,
        )

    proxy_mgr = ProxyManager(
        docker,
        proxy_admin_token=config.proxy_admin_token,
        image_registry=config.image_registry,
        enroll_ttl_seconds=config.proxy_enroll_ttl_seconds,
    )
    sandbox_mgr = SandboxManager(
        docker,
        proxy_manager=proxy_mgr,
        image_registry=config.image_registry,
        github_token=config.github_token,
        # #166 wiring gap (third of three — see #210/#211): without these the
        # GitLab enrollment payload resolved to an empty token and every
        # GitLab clone got "HTTP Basic: Access denied".
        gitlab_token=config.gitlab_token,
        gitlab_url=config.gitlab_url,
        # Spec 1A §3.2: third client — in-sandbox git.
        gitlab_ca_pem=gitlab_ca_pem,
        credential_resolver=app.state.credential_resolver,
        # #522: the repos registry holds the admin's Docker grant. Built here,
        # not taken from app.state: _init_app_state creates repos_repo later.
        # Without it no repo gets the daemon (closed, not open).
        docker_access_registry=ReposRepository(db, default_forge=config.default_forge),
        runtime=runtime,
    )
    loop_metrics = getattr(app.state, "loop_metrics", None)
    agent_runner = AgentRunner(
        sandbox_mgr,
        docker,
        on_stale_image=loop_metrics.sandbox_runner_stale_image if loop_metrics is not None else None,
    )
    if loop_metrics is not None:
        from embry0.orchestration.channels import set_fold_observer

        set_fold_observer(loop_metrics.state_channel_fold)

    app.state.runtime = runtime
    app.state.docker = docker
    # #334: shared so GET /stats reuses one short-lived digest cache instead
    # of a docker inspect per dashboard poll.
    app.state.qa_base_digest_resolver = BaseImageDigestResolver(runtime)
    app.state.sandbox_manager = sandbox_mgr
    app.state.agent_runner = agent_runner
    app.state.proxy_manager = proxy_mgr

    image_mgr = SandboxImageManager(runtime, image_registry=config.image_registry)
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
    from embry0.storage.repositories.issues import IssuesRepository

    reaper = ContainerReaper(
        runtime,
        max_age_hours=24,
        db=db,
        paused_ttl_hours=config.paused_job_ttl_hours,
        jobs_repo=JobsRepository(db, metrics=loop_metrics),
        issues_repo=IssuesRepository(db),
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
                "--force-recreate init-push-images orchestrator`. With AGENT_RUNTIME=rootless "
                "there is no registry: build the images on the sandbox daemon itself "
                "(DOCKER_HOST=unix:///run/embry0-sandbox/docker.sock docker compose --profile images build)."
            ),
        )

    # Verify sandbox networks are present and restricted. We do NOT auto-create:
    # silently making `sandbox-restricted` without enable_ip_masquerade=false would
    # leave sandboxes with direct internet egress and bypass every credential proxy.
    # The init-sandbox-networks compose service runs setup-sandbox-networks.sh once
    # at stack start. If it didn't run (or its output was clobbered), refuse to boot.
    await runtime.verify_networks()

    # EMB-29: probe DOCKER-USER egress enforcement (helper image + iptables
    # in DinD's netns). Non-fatal by design — enforcement itself fails
    # CLOSED per-sandbox at create time, so an unavailable helper at boot
    # only means an early operator warning instead of a bricked deploy.
    await runtime.verify_policy_ready()

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
                old_secret_keys=config.environment_secret_keys_old_list,
                grok_cli_store=grok_store,
            )
            logger.info("xai_refresher_ready")
        except Exception as exc:
            # The file store is a second lineage. Its absence must not stop the
            # proxy: registered accounts push their own tokens. The event name
            # stays so existing log queries still match.
            logger.error("xai_proxy_disabled_credential_error", error_type=type(exc).__name__)
            app.state.xai_refresher = None

    # RAV-1140: context-proxy needs a container allowlist to serve anything;
    # enabled-but-empty is a misconfiguration worth a loud line at startup
    # (the manager then skips the launch and watcher jobs run briefing-only).
    if config.context_proxy_enabled and not config.context_proxy_container_allowlist.strip():
        logger.warning("context_proxy_enabled_without_allowlist")

    # RAV-1056 Phase B: the context-proxy authenticates its upstream
    # logs-query calls with a boot-rotated read-scope service key instead of
    # the master API_KEY — the master no longer leaves the orchestrator.
    # Rotation-per-boot is correct: proxy_mgr.start() recreates the proxy
    # containers, so the previous token has no live consumer. Fail open to
    # the master with a loud line if the table is somehow unavailable.
    # NOTE: _init_app_state (which wires app.state.api_keys_repo) runs later
    # in this lifespan — construct the repository directly on the connected
    # pool here.
    context_proxy_key = config.api_key
    try:
        context_proxy_key = await ApiKeysRepository(db).rotate_service_key("svc-context-proxy", scope="read")
        logger.info("context_proxy_service_key_rotated")
    except Exception as exc:  # noqa: BLE001
        logger.warning("context_proxy_service_key_rotation_failed", error=str(exc))

    # Start proxy services. A forge counts as present when the registry or the
    # environment holds any credential for it, so a registry-only deployment
    # still gets its proxies.
    has_github = await app.state.credential_resolver.has_forge_credential("github")
    has_gitlab = await app.state.credential_resolver.has_forge_credential("gitlab")
    await proxy_mgr.start(
        anthropic_api_key=config.anthropic_api_key,
        github_token=config.github_token,
        # #166 wiring gap: without these the gitlab-proxy never launched on
        # any deployment, and (with the manager gate above) a GitLab-only
        # deployment had no git-proxy at all.
        gitlab_token=config.gitlab_token,
        gitlab_api_base=f"{config.gitlab_url.rstrip('/')}/api/v4" if has_gitlab else "",
        has_github_credential=has_github,
        has_gitlab_credential=has_gitlab,
        # Spec 1A §3.2: second client — the gitlab-proxy's httpx calls.
        gitlab_ca_pem=gitlab_ca_pem,
        enable_auth_proxy=config.auth_proxy_enabled,
        enable_xai_proxy=xai_enabled,
        enable_context_proxy=config.context_proxy_enabled,
        orchestrator_api_key=context_proxy_key,
        context_container_allowlist=config.context_proxy_container_allowlist,
        context_query_quota=config.context_proxy_query_quota,
        log_redaction_fields=config.log_redaction_fields,
    )

    # #554, #568: a sandbox may reach the proxies just launched and no other
    # sandbox. Written now, not at the first create: the proxies' addresses
    # change with every start, and the paused sandboxes kept from the
    # previous process need the new ones (on sandbox-internet as well as
    # sandbox-restricted). Not fatal, like the egress probe above: create()
    # writes both again and refuses every sandbox while it cannot, and a
    # resume refreshes them before it re-enrolls.
    try:
        await sandbox_mgr.refresh_proxy_policy()
    except Exception as exc:  # noqa: BLE001
        logger.error("sandbox_isolation_unavailable", error=str(exc))
    # #626: a proxy found stopped is recreated, maybe at new addresses, and
    # the rules naming its addresses must follow it.
    proxy_mgr.on_proxy_recreated(sandbox_mgr.refresh_proxy_policy)

    # Provision the xai-proxy with each registered account so the first grok
    # request doesn't race the background refresh loop.
    if app.state.xai_refresher_registry is not None:
        await _push_initial_xai_token(app.state.xai_refresher_registry, proxy_mgr)

    # Clean up sandbox containers the previous process left behind. Runs after
    # orphan recovery has failed the jobs that restart killed, so the active set
    # is exactly the paused and awaiting-input jobs, whose sandboxes a resume reuses.
    # Its own repository, like orphan recovery: app.state.jobs_repo is set later,
    # by _init_app_state.
    sweep_jobs_repo = JobsRepository(db, metrics=getattr(app.state, "loop_metrics", None))
    await _clean_orphaned_sandboxes(runtime, sandbox_mgr, sweep_jobs_repo)

    # DinD volume reaper (#274). Its first sweep runs immediately, so it is
    # started only now: orphan recovery has already failed the previous
    # lifecycle's jobs (their volumes read as terminal-owned) and the orphaned
    # sandboxes that mounted those volumes were just removed above.
    from embry0.cache.volume_reaper import VolumeReaper
    from embry0.storage.instance_id import fetch_instance_id

    # Stamped on every volume this process creates and required by the reaper
    # before it will consider one. Unresolved (None) is safe in both places:
    # new volumes get no instance label and the reaper matches nothing.
    volume_instance_id: str | None = None
    try:
        volume_instance_id = await fetch_instance_id(db)
        logger.info("volume_instance_id_resolved", instance_id=volume_instance_id)
    except Exception:
        logger.warning("volume_instance_id_unresolved", exc_info=True)
    app.state.volume_instance_id = volume_instance_id

    volume_reaper: VolumeReaper | None = None
    if config.volume_reaper_enabled:
        volume_reaper = VolumeReaper(
            runtime,
            JobsRepository(db),
            # Its own repository: _init_app_state, which sets
            # app.state.qa_volume_state_repo, runs later in this lifespan.
            state_repo=QAVolumeStateRepository(db),
            instance_id=volume_instance_id,
            interval_seconds=config.volume_reaper_interval_seconds,
            dry_run=config.volume_reaper_dry_run,
            adopt_legacy_names=config.volume_reaper_adopt_legacy_names,
            unknown_owner_grace_minutes=config.volume_reaper_unknown_owner_grace_minutes,
            metrics=loop_metrics,
        )
        volume_reaper.start()
    app.state.volume_reaper = volume_reaper

    app.state.background_tasks = set()
    if qa_minio_task is not None:
        app.state.background_tasks.add(qa_minio_task)
        qa_minio_task.add_done_callback(app.state.background_tasks.discard)

    # Keep every registered xAI access token fresh across its lifetime.
    if app.state.xai_refresher_registry is not None:
        _xai_task = asyncio.create_task(
            _xai_token_refresh_loop(
                app.state.xai_refresher_registry,
                proxy_mgr,
                app.state.credential_health,
            )
        )
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

    from embry0.execution.github_client import GITHUB_API
    from embry0.notifications.github_comment import GitHubCommentChannel

    github_http_headers = {"Accept": "application/vnd.github+json"}
    if config.github_token:
        github_http_headers["Authorization"] = f"Bearer {config.github_token}"
    github_http_client = httpx.AsyncClient(
        base_url=GITHUB_API,
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

    from embry0.services.credentials.resolver import forge_token
    from embry0.storage.repositories.issue_inputs import IssueInputsRepository as _CommentInputsRepo

    github_comment_channel = GitHubCommentChannel(
        http_client=github_http_client,
        dashboard_base_url=dashboard_url,
        token_resolver=lambda repo: forge_token(
            app.state.credential_resolver, repo, "github", config.github_token or ""
        ),
        # #253: the channel pins each question to its position in the comment
        # it posts, so inbound "/answer N:" resolves against that render.
        # Built directly over db: app.state.inputs_repo is only assigned by
        # _init_app_state, which runs later in this lifespan.
        inputs_repo=_CommentInputsRepo(db),
    )
    app.state.github_comment_channel = github_comment_channel

    # Native Telegram question channel (reply-to-answer): sends each blocking
    # agent question via the bot and persists the message_id so replies to
    # that message answer the input through /api/v1/telegram/callback. Only
    # built when the bot token + chat id are configured.
    telegram_channel = None
    if config.telegram_bot_token and config.telegram_chat_id:
        from embry0.notifications.channels import TelegramChannel
        from embry0.storage.repositories.issue_inputs import IssueInputsRepository as _InputsRepo

        telegram_channel = TelegramChannel(
            bot_token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
            inputs_repo=_InputsRepo(db),
        )
        logger.info("telegram_question_channel_enabled")
    app.state.telegram_channel = telegram_channel

    # RAV-1052: outbound event webhooks (signed POSTs — job.completed/
    # job.failed, watcher.proposed, agent.question). Always constructed;
    # emissions are logged skips until a URL is configured (integration_config
    # row or WEBHOOK_NOTIFY_* env). Built BEFORE _init_app_state so the
    # executor gets it via its constructor, like the github-comment channel.
    from embry0.notifications.webhook import WebhookNotifier
    from embry0.storage.repositories.integration_config import IntegrationConfigRepository as _IntegrationRepo

    webhook_notifier = WebhookNotifier(_IntegrationRepo(db))
    app.state.webhook_notifier = webhook_notifier

    # Slack question channel. Always constructed: the incoming-webhook URL is
    # read per send from integration_config, so a settings save needs no
    # restart. An empty URL is a logged skip, not an error. This is not the
    # signed event webhook above.
    from embry0.notifications.channels import SlackChannel

    slack_channel = SlackChannel(_IntegrationRepo(db))
    app.state.slack_channel = slack_channel

    # EMB-47: Linear trigger + write-back — constructed only when configured.
    # #157: credentials also live in integration_config, so the webhook route
    # re-resolves them per delivery through app.state.linear_provider. This
    # startup instance seeds the outbound write-back paths, which read
    # app.state.linear_sync directly; the route refreshes it when the resolved
    # credentials differ.
    # GitLab trigger (#165) — built only when a GitLab credential exists in
    # the registry or the environment (has_gitlab, from the proxy start
    # above); the route reports "not configured" otherwise.
    app.state.gitlab_sync = None
    if has_gitlab:
        from embry0.execution.gitlab_client import make_gitlab_client
        from embry0.execution.gitlab_client import parse_repo_map as _parse_gitlab_map
        from embry0.services.gitlab_sync import GitLabSyncService

        app.state.gitlab_sync = GitLabSyncService(
            make_gitlab_client(config, credential_resolver=app.state.credential_resolver),
            _parse_gitlab_map(config.gitlab_repo_map),
        )
        logger.info(
            "gitlab_sync_enabled", gitlab_url=mask_url_credentials(config.gitlab_url), private_ca=bool(gitlab_ca_pem)
        )

    from embry0.services.linear_provider import LinearSyncProvider

    app.state.linear_provider = LinearSyncProvider(_IntegrationRepo(db))

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
        telegram_channel=telegram_channel,
        slack_channel=slack_channel,
        linear_sync=linear_sync,
        webhook_notifier=webhook_notifier,
        audit_log_path=config.audit_log_path,
        config=config,
        sandbox_manager=sandbox_mgr,
        agent_runner=agent_runner,
        proxy_manager=proxy_mgr,
    )
    # spec 3.5: the reaper was built before the executor existed; it announces
    # each expired job's job_finished through the executor's event path.
    app.state.container_reaper.on_job_finished = app.state.issue_executor._announce_finished

    # Seed the log scrubbers with every registry secret before any job runs.
    from embry0.safety.redaction import register_known_secrets, register_redacted_fields

    register_known_secrets(await app.state.credential_resolver.known_secret_values())
    register_redacted_fields(config.log_redaction_fields)

    # Now that _init_app_state has populated the repos and docker is ready,
    # construct SharedVolumeManager and update both the placeholder on app.state
    # and the reference stored in the executor (which was initialized with None
    # earlier when docker was not yet ready).
    real_manager = SharedVolumeManager(
        runtime=app.state.runtime,
        state_repo=app.state.qa_volume_state_repo,
        instance_id=volume_instance_id,
    )
    app.state.qa_shared_volume_manager = real_manager
    app.state.issue_executor._qa_shared_volume_manager = real_manager
    # Same reason (#334): built with docker, threaded into the graph config so
    # QA selection and GET /stats share one digest cache.
    app.state.issue_executor._qa_base_digest_resolver = app.state.qa_base_digest_resolver

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

    # RAV-1056 Phase C: daily audit retention — prunes audit_log rows and
    # JSONL lines older than AUDIT_RETENTION_DAYS from both sinks.
    from embry0.audit.retention import sweep_audit_retention as _sweep_audit

    async def _daily_audit_retention_sweep() -> None:
        while True:
            await asyncio.sleep(86400)  # 24 h
            try:
                db_n, file_n = await _sweep_audit(
                    app.state.audit_repo, config.audit_log_path, config.audit_retention_days
                )
                logger.info("audit_retention_swept", db_rows_deleted=db_n, jsonl_lines_pruned=file_n)
            except Exception:
                logger.warning("audit_retention_sweep_failed", exc_info=True)

    audit_sweep_task = asyncio.create_task(_daily_audit_retention_sweep())
    app.state.background_tasks.add(audit_sweep_task)
    audit_sweep_task.add_done_callback(app.state.background_tasks.discard)

    # #270: which trigger label dispatch matches on, and whether the DB row,
    # .env or the code default supplied it (DB wins; a shadowed .env warns).
    await app.state.integration_repo.log_effective_trigger_labels()

    # #284: Linear dispatch reconciler — the second look for labeled tickets a
    # lost webhook left without a job. Always constructed; the loop starts
    # unless the mode is `off` or the interval is 0, and each sweep is a no-op
    # while Linear has no API key (credentials are dashboard-editable, so that
    # is asked per sweep). Default mode is `shadow`: decide and log, change
    # nothing.
    from embry0.services.linear_reconciler import ACTOR as _RECONCILER_ACTOR
    from embry0.services.linear_reconciler import LinearDispatchReconciler, ReconcilerSettings
    from embry0.trackers import TriggerContext

    async def _reconciler_sync() -> object | None:
        resolved: object | None
        resolved, _secret = await app.state.linear_provider.resolve()
        return resolved

    async def _reconciler_labels() -> set[str]:
        return set(await _IntegrationRepo(db).get_trigger_labels())

    def _reconciler_context(labels: set[str]) -> TriggerContext:
        return TriggerContext(
            issues_repo=app.state.issues_repo,
            issue_executor=app.state.issue_executor,
            jobs_repo=app.state.jobs_repo,
            dispatch_ledger=app.state.dispatch_ledger,
            trigger_labels=labels,
            # No localhost fallback: a loopback URL is a dead link on a ticket.
            dashboard_base_url=getattr(config, "dashboard_public_url", ""),
            auto_retry=app.state.linear_reconciler.acts,
            # A fixed actor: jobs.dispatched_by is populated, the per-author
            # rate limit applies, and the reconciler's own caps count from it.
            actor=_RECONCILER_ACTOR,
        )

    app.state.linear_reconciler = LinearDispatchReconciler(
        resolve_sync=_reconciler_sync,
        resolve_trigger_labels=_reconciler_labels,
        make_context=_reconciler_context,
        settings=ReconcilerSettings(
            mode=config.linear_reconcile_mode,
            interval_seconds=config.linear_reconcile_interval_seconds,
            startup_delay_seconds=config.linear_reconcile_startup_delay_seconds,
            max_dispatches_per_sweep=config.linear_reconcile_max_dispatches,
            max_dispatches_per_day=config.linear_reconcile_max_dispatches_per_day,
            max_attempts_per_ticket=config.linear_reconcile_max_attempts_per_ticket,
            failure_max_attempts=config.linear_reconcile_failure_max_attempts,
            stale_triaging_minutes=config.linear_reconcile_stale_triaging_minutes,
        ),
        metrics=loop_metrics,
    )
    if app.state.linear_reconciler.enabled:
        reconciler_task = asyncio.create_task(app.state.linear_reconciler.start())
        app.state.background_tasks.add(reconciler_task)
        reconciler_task.add_done_callback(app.state.background_tasks.discard)
    else:
        logger.info(
            "linear_reconciler_disabled",
            mode=config.linear_reconcile_mode,
            interval_seconds=config.linear_reconcile_interval_seconds,
        )

    # #279: merge sync — the read-only GitHub sweep behind the PR webhook. An
    # optional subsystem: whatever goes wrong building it is logged and the
    # orchestrator starts without it (the webhook path still records).
    try:
        from embry0.execution.github_client import GitHubClient
        from embry0.services.merge_sync.sweep import MergeSyncSweep, settings_from_config

        async def _repo_is_on_github(repo: str) -> bool:
            try:
                return str(await app.state.repos_repo.forge_for(repo)) == "github"
            except Exception:
                return False  # unregistered or unreadable: not ours to ask GitHub about

        app.state.merge_sync = MergeSyncSweep(
            # max_attempts=1: ForgeClient's retry policy is built for a
            # request whose caller is waiting. Here the SWEEP is the retry —
            # a 429 retried twice first would sleep up to a minute and burn
            # quota before the budget could see it and stand down.
            client=GitHubClient(
                default_token=config.github_token,
                max_attempts=1,
                credential_resolver=app.state.credential_resolver,
            ),
            recorder=app.state.delivery_recorder,
            delivery_repo=app.state.job_delivery_repo,
            settings=settings_from_config(config),
            is_github=_repo_is_on_github,
        )
        if app.state.merge_sync.enabled:
            merge_sync_task = asyncio.create_task(app.state.merge_sync.start())
            app.state.background_tasks.add(merge_sync_task)
            merge_sync_task.add_done_callback(app.state.background_tasks.discard)
        else:
            logger.info("merge_sync_disabled", mode=config.merge_sync_mode)
    except Exception:
        app.state.merge_sync = None
        logger.critical("merge_sync_unavailable", exc_info=True)

    # RAV-657 (W1d): log-watcher/proposer. The service is always constructed
    # (POST /watcher/run works for deliberate operator runs); the scheduled
    # loop only starts when WATCHER_ENABLED=true.
    from embry0.execution.github_client import GitHubClient
    from embry0.services.healing.deploys import DeployLedgerSync, GitHubAncestry
    from embry0.services.watcher import WatcherService
    from embry0.storage.repositories.log_signatures import LogSignaturesRepository
    from embry0.storage.repositories.watcher_patterns import WatcherPatternsRepository
    from embry0.storage.repositories.watcher_runs import WatcherRunsRepository

    app.state.watcher_runs_repo = WatcherRunsRepository(db)
    # #378: rows written before stacks existed carry repo ''. Claim them for
    # the configured stack once, at startup — no migration can know which
    # stack a deployment was watching. Idempotent and cheap on a fresh DB.
    if getattr(config, "watcher_repo", ""):
        try:
            await app.state.watcher_runs_repo.adopt_unscoped_rows(config.watcher_repo)
        except Exception:
            logger.warning("watcher_row_adoption_failed", exc_info=True)

    app.state.watcher_patterns_repo = WatcherPatternsRepository(db)
    # #378: what embry0 watches, one row per stack. A deployment upgrading
    # from the env-var watcher seeds its stack here once, so nothing changes
    # for it; from then on stacks are managed through the API.
    from embry0.services.watcher_stacks import seed_stacks
    from embry0.storage.repositories.watcher_stacks import WatcherStacksRepository

    app.state.watcher_stacks_repo = WatcherStacksRepository(db)
    try:
        await seed_stacks(app.state.watcher_stacks_repo, config)
    except Exception:
        logger.warning("watcher_stack_seed_failed", exc_info=True)
    # #369: the ctor param is optional and nothing ever passed it, so #289's
    # clustering logged a shadow comparison on every tick while log_signatures
    # stayed empty — "is this new?" was unanswerable and #290/#291/#312 had
    # nothing to read. Exactly the check-that-measures-nothing shape CLAUDE.md
    # warns about, which is why a test now pins this argument.
    app.state.log_signatures_repo = LogSignaturesRepository(db)
    # #378: one watcher per enabled stack. Each gets its stack's view of the
    # config (StackConfig: overrides from the row, everything else global) and
    # its own filer. A stack whose tracker cannot be built gets None and
    # records the error on every tick rather than filing somewhere else.
    from embry0.services.draft_filers import build_filer
    from embry0.services.watcher_stacks import StackConfig, WatcherStack

    def _watcher_for(stack: WatcherStack) -> WatcherService:
        return WatcherService(
            cases_repo=app.state.healing_cases_repo,
            case_recorder=app.state.case_recorder,
            # #278: a deploy marker is reported on a case only after the forge
            # confirms the deployed commit contains the case's merge.
            deploy_sync=DeployLedgerSync(
                app.state.healing_cases,
                app.state.healing_cases_repo,
                GitHubAncestry(
                    GitHubClient(
                        default_token=getattr(config, "github_token", "") or "",
                        credential_resolver=app.state.credential_resolver,
                    )
                ),
            ),
            config=StackConfig(config, stack),
            jobs_repo=app.state.jobs_repo,
            watcher_runs_repo=app.state.watcher_runs_repo,
            integration_repo=app.state.integration_repo,
            executor=app.state.issue_executor,
            filer=build_filer(
                stack,
                linear_sync=linear_sync,
                github_token=getattr(config, "github_token", "") or "",
                credential_resolver=app.state.credential_resolver,
            ),
            webhook_notifier=webhook_notifier,
            patterns_repo=app.state.watcher_patterns_repo,
            signatures_repo=app.state.log_signatures_repo,
            credential_resolver=app.state.credential_resolver,
            metrics=loop_metrics,
        )

    try:
        watched = await app.state.watcher_stacks_repo.list_all(enabled_only=True)
    except Exception:
        watched = []
        logger.critical("watcher_stacks_unreadable", exc_info=True)
    app.state.watcher_services = {stack.repo: _watcher_for(stack) for stack in watched}
    # Back-compat for the single-stack callers (POST /watcher/run with no
    # stack, the health check's default): the first stack, or None.
    app.state.watcher_service = next(iter(app.state.watcher_services.values()), None)
    logger.info("watcher_stacks_loaded", stacks=sorted(app.state.watcher_services))

    if loop_metrics is not None:
        # #285: the DB-backed gauges, and the tick timestamps restored from
        # watcher_runs — a restart must not read as "never ticked".
        from embry0.metrics.wiring import build_snapshot, seed_watcher_ticks

        # #448: the cap gauges are per stack, read through the same view the
        # stack's watcher reads its cap and window from.
        stack_views = {stack.repo: StackConfig(config, stack) for stack in watched}
        loop_metrics.watcher_configured(
            enabled=config.watcher_enabled,
            max_open_proposals={repo: view.watcher_max_open_proposals for repo, view in stack_views.items()},
        )
        for watched_repo in app.state.watcher_services:
            await seed_watcher_ticks(loop_metrics, app.state.watcher_runs_repo, repo=watched_repo)
        app.state.loop_snapshot = build_snapshot(
            loop_metrics,
            healing_cases=app.state.healing_cases,
            healing_cases_repo=app.state.healing_cases_repo,
            jobs_repo=app.state.jobs_repo,
            stacks=stack_views,
            reconciler_actor=_RECONCILER_ACTOR,
        )
    if config.watcher_enabled:
        # One loop per stack. WATCHER_ENABLED still gates them all: it is the
        # deployment's "is the watcher running at all" switch, while a single
        # stack is silenced by disabling its row.
        for watched_repo, service in app.state.watcher_services.items():
            watcher_task = asyncio.create_task(service.start())
            app.state.background_tasks.add(watcher_task)
            watcher_task.add_done_callback(app.state.background_tasks.discard)
            logger.info("watcher_loop_started", stack=watched_repo)

    # #312 (SH-P5.4): confirm-healed. Read-only apart from ticket comments, so
    # it ships on rather than in shadow. Optional like merge sync — whatever
    # goes wrong building it is logged and the orchestrator starts without it.
    try:
        from embry0.services.healing.confirm_sweep import ConfirmHealedSweep, linear_commenter

        team_id = getattr(config, "watcher_linear_team_id", "") or ""
        app.state.confirm_healed = ConfirmHealedSweep(
            cases_repo=app.state.healing_cases_repo,
            cases_service=app.state.healing_cases,
            signatures_repo=app.state.log_signatures_repo,
            config=config,
            commenter=linear_commenter(linear_sync, team_id) if linear_sync is not None and team_id else None,
            metrics=loop_metrics,
        )
        if app.state.confirm_healed.enabled:
            confirm_task = asyncio.create_task(app.state.confirm_healed.start())
            app.state.background_tasks.add(confirm_task)
            confirm_task.add_done_callback(app.state.background_tasks.discard)
        else:
            logger.info("confirm_healed_disabled")
    except Exception:
        app.state.confirm_healed = None
        logger.critical("confirm_healed_unavailable", exc_info=True)

    # Daily digest: one summary a day of what embry0 did, in place of a
    # notification per watcher analysis job. Optional like the sweeps above.
    try:
        from embry0.services.digest import DailyDigestService
        from embry0.storage.repositories.digest import DigestRepository

        app.state.daily_digest = DailyDigestService(repo=DigestRepository(db), notifier=webhook_notifier, config=config)
        if app.state.daily_digest.enabled:
            digest_task = asyncio.create_task(app.state.daily_digest.start())
            app.state.background_tasks.add(digest_task)
            digest_task.add_done_callback(app.state.background_tasks.discard)
        else:
            logger.info("daily_digest_disabled")
    except Exception:
        app.state.daily_digest = None
        logger.critical("daily_digest_unavailable", exc_info=True)

    # Merge approvals (#319 phase 2): a job's PR on an enabled repo is asked
    # about in the operator's approval chat, and a relayed "merge it" is
    # checked and merged here. The executor holds it as an optional
    # attribute, so without this assignment jobs finish normally and nobody
    # is ever asked (tests/unit/api/test_merge_approvals_wiring.py pins it).
    try:
        from embry0.execution.github_client import GitHubClient as _MergeGitHubClient
        from embry0.services.merge_approvals import MergeApprovalService
        from embry0.storage.repositories.job_verifications import JobVerificationsRepository
        from embry0.storage.repositories.merge_approvals import MergeApprovalsRepository

        app.state.merge_approvals = MergeApprovalService(
            repo=MergeApprovalsRepository(db),
            github=_MergeGitHubClient(
                default_token=getattr(config, "github_token", "") or "",
                credential_resolver=app.state.credential_resolver,
            ),
            notifier=webhook_notifier,
            config=config,
            verifications=JobVerificationsRepository(db),
            cases=app.state.healing_cases_repo,
            jobs=app.state.jobs_repo,
        )
        app.state.issue_executor._merge_approvals = app.state.merge_approvals
        # The sweep posts recorded asks once CI settles and retires the ones
        # nobody answered. Without it no ask ever reaches the chat.
        merge_sweep_task = asyncio.create_task(app.state.merge_approvals.start())
        app.state.background_tasks.add(merge_sweep_task)
        merge_sweep_task.add_done_callback(app.state.background_tasks.discard)
        logger.info(
            "merge_approvals_ready",
            mode=app.state.merge_approvals.mode,
            repos=sorted(app.state.merge_approvals.repos),
            chat_configured=bool(app.state.merge_approvals.chat_id),
        )
        # An empty repo list is a valid configuration (nothing is asked
        # about) and also exactly what a deployment that relied on the old
        # code default looks like after that default was emptied. Say so.
        if not app.state.merge_approvals.repos:
            logger.warning(
                "merge_approvals_no_repos",
                hint="MERGE_APPROVAL_REPOS is empty: no PR will be asked about",
            )
    except Exception:
        app.state.merge_approvals = None
        logger.critical("merge_approvals_unavailable", exc_info=True)

    # RAV-1092: comment-driven dispatch. Always constructed (the service's
    # own gate handles COMMENT_DISPATCH_ENABLED); the webhook handler picks
    # it up from app.state.
    from embry0.services.comment_dispatch import CommentDispatchService

    app.state.comment_dispatch = CommentDispatchService(
        config=config,
        jobs_repo=app.state.jobs_repo,
        agent_sessions_repo=app.state.agent_sessions_repo,
        executor=app.state.issue_executor,
        credential_resolver=app.state.credential_resolver,
        case_recorder=app.state.case_recorder,
    )
    if config.comment_dispatch_enabled:
        logger.info(
            "comment_dispatch_enabled",
            tag=config.comment_dispatch_tag,
            author_roles=config.comment_dispatch_author_roles,
        )

    # #30: pull request review. Always constructed: each repo's own
    # pr_review.enabled is the gate, and the webhook handlers find it here.
    # GitLab gets a client only when a GitLab credential exists (gitlab_sync
    # is built under the same condition); a GitLab comment command is then
    # refused as gitlab_not_configured, and so is a GitLab ready event under
    # pr_review.ready_authors "team" (reason author_not_on_team, detail
    # gitlab_not_configured), since the author's access cannot be checked.
    from embry0.execution.github_client import GitHubClient as _ReviewGitHubClient
    from embry0.services.pr_review.dispatcher import PrReviewDispatcher
    from embry0.storage.repositories.pr_reviews import PrReviewsRepository

    pr_review_gitlab = None
    if getattr(app.state, "gitlab_sync", None) is not None:
        from embry0.execution.gitlab_client import make_gitlab_client as _review_gitlab_client

        pr_review_gitlab = _review_gitlab_client(config, credential_resolver=app.state.credential_resolver)
    app.state.pr_review_dispatcher = PrReviewDispatcher(
        config=config,
        repos_repo=app.state.repos_repo,
        prefs_repo=app.state.repo_preferences_repo,
        jobs_repo=app.state.jobs_repo,
        reviews_repo=PrReviewsRepository(db),
        executor=app.state.issue_executor,
        github=_ReviewGitHubClient(
            default_token=getattr(config, "github_token", "") or "",
            credential_resolver=app.state.credential_resolver,
        ),
        gitlab=pr_review_gitlab,
    )

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

    # #307: the jobs boot took back above, now that the executor, the proxies
    # and the volume manager exist. Each runs as an ordinary job task.
    _start_recovered_jobs(app.state.issue_executor, recovering)

    logger.info("embry0_started")
    yield

    logger.info("embry0_stopping")
    # #307: drain first, while every intake loop is still running: from here
    # on a dispatch stays pending for the next boot instead of starting, and
    # the running jobs stop at a checkpoint (or the deadline) and are stamped
    # as interrupted. Only then are the loops and leftover tasks cancelled.
    try:
        await app.state.issue_executor.drain(config.shutdown_drain_seconds)
    except Exception:
        logger.exception("executor_drain_failed")
    from embry0.orchestration.channels import set_fold_observer as _set_fold_observer

    _set_fold_observer(None)
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
    if volume_reaper is not None:
        await volume_reaper.stop()
    # EMB-45: the refresh loop was cancelled with the other background tasks above;
    # close the refresher's HTTP client now.
    xai_refresher = getattr(app.state, "xai_refresher", None)
    if xai_refresher is not None:
        try:
            await xai_refresher.aclose()
        except Exception:
            logger.warning("xai_refresher_close_failed", exc_info=True)
    xai_registry = getattr(app.state, "xai_refresher_registry", None)
    if xai_registry is not None:
        try:
            await xai_registry.aclose()
        except Exception:
            logger.warning("xai_refresher_registry_close_failed", exc_info=True)
    await proxy_mgr.stop()
    # Close the long-lived GitHub HTTP client used by the github-comment channel.
    gh_client = getattr(app.state, "github_http_client", None)
    if gh_client is not None:
        try:
            await gh_client.aclose()
        except Exception:
            logger.warning("github_http_client_close_failed", exc_info=True)
    # Asks still in flight hold no state worth waiting for; the next job asks again.
    merge_approvals = getattr(app.state, "merge_approvals", None)
    if merge_approvals is not None:
        try:
            await merge_approvals.aclose()
        except Exception:
            logger.warning("merge_approvals_close_failed", exc_info=True)
    # RAV-1052: cancel in-flight webhook deliveries + close the notifier's client.
    notifier = getattr(app.state, "webhook_notifier", None)
    if notifier is not None:
        try:
            await notifier.aclose()
        except Exception:
            logger.warning("webhook_notifier_close_failed", exc_info=True)
    # The other unguarded raise in this shutdown. Starlette forwards
    # traceback.format_exc() on lifespan shutdown failure to uvicorn's stdlib
    # logger, so one chained exception becomes two unstructured traceback
    # lines. The proxy rm failures are already caught inside stop() and are
    # not that traceback. Keep a database-close failure structured too.
    try:
        await db.close()
    except Exception:
        logger.warning("database_close_failed", exc_info=True)
    logger.info("embry0_stopped")


def _start_recovered_jobs(executor: Any, jobs: list[dict[str, Any]]) -> None:
    """Start the jobs boot recovery took back (#307): resume a checkpointed
    one, launch the others again. One that cannot be started stays in flight
    and the next boot decides again."""
    for job in jobs:
        try:
            if job["action"] == "resume":
                executor.resume_after_restart(job)
            else:
                executor.relaunch(job)
        except Exception:
            logger.exception("job_recovery_start_failed", job_id=job.get("job_id"))
    if jobs:
        logger.info(
            "jobs_recovered_after_restart",
            resumed=[j["job_id"] for j in jobs if j["action"] == "resume"],
            relaunched=[j["job_id"] for j in jobs if j["action"] != "resume"],
        )


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
    app.add_middleware(
        RateLimitMiddleware,
        requests_per_minute=config.api_rate_limit_per_minute,
        webhook_requests_per_minute=config.webhook_rate_limit_per_minute,
    )
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

    from fastapi.exceptions import RequestValidationError

    from embry0.api.v1.credentials import validation_error_handler

    # A credential body's 422 must not echo the secret back (see the handler).
    # The decorator calls add_exception_handler, whose own signature wants a handler
    # that accepts any Exception; this one takes RequestValidationError.
    app.exception_handler(RequestValidationError)(validation_error_handler)

    _register_routers(app)

    # Prometheus metrics endpoint — LAN-only by network topology (not via
    # cloudflared, not proxied by nginx). #285 adds the self-healing-loop
    # metrics to the same endpoint; see embry0/metrics/.
    from embry0.metrics.wiring import install_metrics

    # Never raises; app.state.loop_metrics is None if it could not install.
    install_metrics(app, config)

    return app


def _register_routers(app: FastAPI) -> None:
    from embry0.api.deps import require_admin, require_auth
    from embry0.api.v1 import (
        agents,
        api_keys,
        audit,
        auth_session,
        bootstrap,
        cases,
        config,
        credentials,
        deliverables,
        environment,
        github,
        gitlab,
        health,
        hosts,
        internal_qa,
        issues,
        jobs,
        logs,
        merge_approvals,
        org_needs,
        organizations,
        pipeline_templates,
        qa_artifacts,
        qa_config,
        qa_dashboard,
        qa_events,
        qa_images,
        queue,
        repo_preferences,
        repos,
        sandbox_profiles,
        sandboxes,
        stats,
        telegram,
        traces,
        users,
        watcher,
        watcher_stacks,
        webhooks,
    )
    from embry0.api.ws import streaming

    auth_deps = [require_auth]
    app.include_router(health.router, prefix="/api/v1", tags=["health"])
    # RAV-1069: Auth0 SSO endpoints. Deliberately WITHOUT auth_deps — /login,
    # /callback and /check are the unauthenticated entry; /me and /logout act
    # on the session cookie themselves. All handlers are GETs except /logout
    # (POST — covered by the CSRF header guard like every other mutation).
    app.include_router(auth_session.router, prefix="/api/v1", tags=["auth"])
    app.include_router(agents.router, prefix="/api/v1", tags=["agents"], dependencies=auth_deps)
    # RAV-1056 Phase A: user management — admin tier only (require_admin
    # accepts the master API key or an embry0-admin session).
    app.include_router(users.router, prefix="/api/v1", tags=["users"], dependencies=[require_admin])
    # RAV-1056 Phase B: self-service key management — operator+ (per-route
    # logic enforces ownership and scope caps).
    app.include_router(api_keys.router, prefix="/api/v1", tags=["api-keys"], dependencies=auth_deps)
    # RAV-1056 Phase C: global audit read API — operator+ mount (NOT the
    # users-router require_admin pattern above); tier logic is in-handler
    # (admins: all rows; operator/read callers: own rows only).
    app.include_router(audit.router, prefix="/api/v1", tags=["audit"], dependencies=auth_deps)
    app.include_router(issues.router, prefix="/api/v1", tags=["issues"], dependencies=auth_deps)
    app.include_router(jobs.router, prefix="/api/v1", tags=["jobs"], dependencies=auth_deps)
    # RAV-603: typed job outputs (pr/report/artifact/message) + artifact download.
    app.include_router(deliverables.router, prefix="/api/v1", tags=["deliverables"], dependencies=auth_deps)
    # RAV-1057: on-demand container-log queries (Loki proxy).
    app.include_router(logs.router, prefix="/api/v1", tags=["logs"], dependencies=auth_deps)
    # #634: the live log tail is an EventSource, so its route takes a
    # job-scoped token as well as the header. Included first: the artifacts
    # router's `{path:path}` catch-all would otherwise shadow it.
    app.include_router(qa_artifacts.log_stream_router, prefix="/api/v1", tags=["qa-artifacts"])
    app.include_router(qa_artifacts.router, prefix="/api/v1", tags=["qa-artifacts"], dependencies=auth_deps)
    app.include_router(qa_dashboard.router, prefix="/api/v1", tags=["qa-dashboard"], dependencies=auth_deps)
    # EMB-42: per-repo pre-baked QA cache-image builds. Synchronous and
    # minutes-long on a fresh build — callers use a long HTTP timeout.
    app.include_router(qa_images.router, prefix="/api/v1", tags=["qa-images"], dependencies=auth_deps)
    # Phase 5C: SSE liveness for /qa/runs/{run_id}/events. The EventSource
    # browser API cannot send Authorization headers, so this router carries
    # its own auth dependency accepting Bearer header OR a `token` query
    # parameter (RAV-1058 A1) instead of the shared header-only auth_deps.
    app.include_router(qa_events.router, prefix="/api/v1", tags=["qa-events"])
    app.include_router(sandbox_profiles.router, prefix="/api/v1", tags=["sandbox-profiles"], dependencies=auth_deps)
    app.include_router(sandboxes.router, prefix="/api/v1", tags=["sandboxes"], dependencies=auth_deps)
    app.include_router(config.router, prefix="/api/v1", tags=["config"], dependencies=auth_deps)
    app.include_router(stats.router, prefix="/api/v1", tags=["stats"], dependencies=auth_deps)
    app.include_router(traces.router, prefix="/api/v1", tags=["traces"], dependencies=auth_deps)
    # #278: healing-case lifecycle ledger (read-only).
    app.include_router(cases.router, prefix="/api/v1", tags=["cases"], dependencies=auth_deps)
    # #319 phase 2: the chat merge-approval loop.
    app.include_router(merge_approvals.router, prefix="/api/v1", tags=["merge-approvals"], dependencies=auth_deps)
    # RAV-657: watcher audit trail + manual tick trigger.
    app.include_router(watcher.router, prefix="/api/v1", tags=["watcher"], dependencies=auth_deps)
    # #378: the watched stacks (reads operator-tier; writes admin-tier in-route).
    app.include_router(watcher_stacks.router, prefix="/api/v1", tags=["watcher"], dependencies=auth_deps)
    app.include_router(queue.router, prefix="/api/v1", tags=["queue"], dependencies=auth_deps)
    app.include_router(pipeline_templates.router, prefix="/api/v1", tags=["pipeline-templates"], dependencies=auth_deps)
    app.include_router(environment.router, prefix="/api/v1", tags=["environment"], dependencies=auth_deps)
    # Spec 2026-09-29 §3.8: the credential registry (reads operator-tier; writes admin-tier in-route).
    app.include_router(credentials.router, prefix="/api/v1", tags=["credentials"], dependencies=auth_deps)
    app.include_router(repo_preferences.router, prefix="/api/v1", tags=["repo-preferences"], dependencies=auth_deps)
    # RAV-1251: repo registry + unified per-repo detail.
    app.include_router(qa_config.router, prefix="/api/v1", tags=["qa-config"], dependencies=auth_deps)
    app.include_router(bootstrap.router, prefix="/api/v1", tags=["bootstrap"], dependencies=auth_deps)
    # MUST come after every router owning a /repos/{repo}/<suffix> route:
    # repos exposes a `{full_repo:path}` catch-all (#162, for GitLab nested
    # subgroups) which would otherwise shadow them. Pinned by
    # tests/unit/api/test_repos_route_order.py.
    app.include_router(repos.router, prefix="/api/v1", tags=["repos"], dependencies=auth_deps)
    app.include_router(organizations.router, prefix="/api/v1", tags=["organizations"], dependencies=auth_deps)
    app.include_router(org_needs.router, prefix="/api/v1", tags=["organizations"], dependencies=auth_deps)
    # ADR 0013: the hosts that run the code (reads and probes operator-tier; writes admin-tier in-route).
    app.include_router(hosts.router, prefix="/api/v1", tags=["hosts"], dependencies=auth_deps)
    app.include_router(github.router, prefix="/api/v1", tags=["github"], dependencies=auth_deps)
    app.include_router(gitlab.router, prefix="/api/v1", tags=["gitlab"], dependencies=auth_deps)
    app.include_router(webhooks.router, prefix="/api/v1", tags=["webhooks"])
    app.include_router(telegram.router, prefix="/api/v1", tags=["telegram"])
    # Internal sandbox-only endpoints — auth is via per-sandbox bearer
    # token in the request body, not via session cookies, so these paths
    # are also exempted from CSRF middleware.
    app.include_router(internal_qa.router, prefix="/api/v1", tags=["internal-qa"])
    app.include_router(streaming.router)
