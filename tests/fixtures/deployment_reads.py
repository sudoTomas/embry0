"""The deployment's own records, absent and then present, on the real app over a real database.

The app and its keys are :mod:`tests.fixtures.repository_writes`': the limited
key covers :data:`~tests.fixtures.repository_writes.COVERED`, and both seeded
repositories hold their work from the start. :func:`deployment_reads_app`
starts it as a deployment that has none of its own records: no person, no
global variable, no model credential in the environment or the registry, no
connection test, no integration, nothing for the retention sweeps to remove.
The OpenAI-compatible providers OpenRouter and the custom one are enabled
(configuration, not a record), so their catalog entries and OpenRouter's
spend route answer.

:meth:`Deployment.add_deployment_wide` then gives the deployment records of
every kind that belongs to it rather than to a repository:

- its own records, each carrying one of :data:`OWN`'s values, which no answer
  to a key with an allowlist may name: a person (:data:`PERSON`), who makes
  every change below with a key of their own, so each change is recorded under
  their email; global variables, a secret and a value; a model credential for
  every repository, and the environment's Anthropic and custom provider
  credentials and the custom provider's address; a provider connection test;
  the integrations, their repository map naming
  :data:`~tests.fixtures.repository_writes.UNCOVERED`; and an old job of that
  repository, with a code event, an agent's session and a QA image for the
  retention sweeps to remove;
- what every repository's jobs run on, each carrying :data:`SHARED_MARK`: a
  change of the settings document's deployment layer, a version of the
  content pack, an agent, a pipeline template, a sandbox profile, a skill, the
  global agent context, the models, and a host.

It then forgets what the app caches, so a read after it reads the rows and
the environment as they are.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI

import embry0.services
from embry0.agents.providers import ALL_PROVIDERS
from embry0.api.deps import bust_api_key_cache
from embry0.api.middleware.rate_limit import RateLimitMiddleware
from embry0.audit.helpers import emit_audit
from embry0.cache.image_builder import QA_IMAGE_PREFIX
from embry0.cache.qa_image_retention import QAImageRetentionSweep
from embry0.execution.runtime.protocol import DiskUsage, ImageSummary
from embry0.services.agent_session_retention import AgentSessionRetentionSweep
from embry0.services.job_log_retention import JobLogRetentionSweep
from embry0.services.provider_readiness import PROBE_AUDIT_ACTION
from embry0.services.world import WorldCache
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeysRepository
from embry0.storage.repositories.job_log_retention import JobLogRetentionRepository
from embry0.storage.seeds.pipeline_templates_builtin import seed_builtin_pipeline_templates
from embry0.storage.seeds.sandbox_profiles_builtin import seed_builtin_sandbox_profiles
from tests.fixtures.repository_writes import NOW, UNCOVERED, Keys, call, repository_writes_app

#: What everything planted that every repository's jobs run on carries.
SHARED_MARK = "for-every-job"
#: The person who makes every planted change, with a key of their own.
PERSON = "ops@example.com"
#: The planted global variables: a secret and a value.
GLOBAL_SECRET, GLOBAL_VALUE = "API_URL", "PIP_INDEX_URL"
#: The model credential the planted person binds for every repository.
POOL_CREDENTIAL = "pool-account"
#: How long ago the uncovered repository's old job ran: past every sweep's retention.
LONG_AGO = timedelta(days=90)
OLD_JOB = "job-old-gizmo"
#: The QA image of the old job's repository the image sweep would remove.
OLD_IMAGE = f"{QA_IMAGE_PREFIX}{UNCOVERED.replace('/', '-')}:old-build"
#: Each of the deployment's own records a key without an allowlist reads, by what it is, with the value it carries.
OWN: dict[str, str] = {
    "a person's email": PERSON,
    "a person's GitHub login": "ops-login",
    "a global secret's value": "secret-value-000000000001",
    "a global variable's value": "https://packages.own-records.example.test/simple",
    "a global variable's description": "the package index of every sandbox",
    "the custom provider's address": "http://models.own-records.example.test:8000/v1",
    "the notifications chat": "-1000000000006",
    "where events are sent": "https://notify.own-records.example.test/events",
    "which tracker team files on which repository": f"TEAM={UNCOVERED}",
    "the old job's QA image": OLD_IMAGE,
}
#: The OpenAI-compatible providers enabled from the start: configuration, not one of the deployment's records.
ENABLED_PROVIDERS = "openrouter,custom"
#: The days each retention sweep keeps.
RETENTION_DAYS = 30
#: Environment variables that name a deployment's credentials or integrations: unset until planted.
_DEPLOYMENT_ENV = (
    *(p.api_key_env for p in ALL_PROVIDERS if p.api_key_env),
    *(p.base_url_env for p in ALL_PROVIDERS if p.base_url_env),
    "CLAUDE_CODE_OAUTH_TOKEN",
    "GITHUB_WEBHOOK_SECRET",
    "SLACK_WEBHOOK_URL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "WEBHOOK_NOTIFY_URL",
    "WEBHOOK_NOTIFY_SECRET",
    "LINEAR_API_KEY",
    "LINEAR_WEBHOOK_SECRET",
    "LINEAR_REPO_MAP",
)
#: The content pack embry0 ships.
DEFAULT_PACK = Path(embry0.services.__file__).parent / "progression_pack_default.yaml"


class _Daemon:
    """The container daemon the QA image sweep reads: the images it is given, no volume, and nothing in use."""

    def __init__(self) -> None:
        self.images: list[ImageSummary] = []

    async def list_images(self, *, timeout: int = 60) -> list[ImageSummary]:
        return list(self.images)

    async def disk_usage(self, *, timeout: int = 120) -> DiskUsage:
        return DiskUsage(image_unique_bytes={image.id: 4096 for image in self.images}, volume_bytes={})

    async def list_containers(self, **_kwargs: Any) -> list[Any]:
        return []

    async def list_volumes(self, **_kwargs: Any) -> list[str]:
        return []

    async def inspect_volumes(self, names: list[str], **_kwargs: Any) -> list[Any]:
        return []


def forget_caches(app: FastAPI) -> None:
    """Drop what the app keeps between reads, so the next read reads the rows and the environment as they are."""
    app.state.world_cache = WorldCache()
    app.state.provider_usage = None
    progression = getattr(app.state, "progression", None)
    if progression is not None:
        progression._cached = None  # noqa: SLF001 - the service's own cache, kept a minute


async def _ok(app: FastAPI, method: str, url: str, token: str, body: Any = None) -> Any:
    answer = await call(app, method, url, token, body)
    assert answer.status_code in (200, 201), f"{method} {url}: {answer.status_code} {answer.text[:300]}"
    return answer.json()


@dataclass
class Deployment:
    """The app, its keys and each seeded repository's ids; the deployment's own records join on
    :meth:`add_deployment_wide`."""

    app: FastAPI
    db: DatabasePool
    keys: Keys
    monkeypatch: pytest.MonkeyPatch
    daemon: _Daemon
    values: dict[str, dict[str, str]] = field(default_factory=dict)

    async def add_deployment_wide(self) -> None:
        app, db, env = self.app, self.db, self.monkeypatch
        # A person, who makes every change below with a key of their own.
        await db.execute(
            "INSERT INTO users (id, auth0_sub, email, name, tier, github_login) "
            "VALUES ('usr-ops', 'auth0|ops', $1, 'Ops', 'admin', $2)",
            PERSON,
            OWN["a person's GitHub login"],
        )
        _row, ops = await ApiKeysRepository(db).create(name="ops", scope="admin", user_id="usr-ops", created_by=PERSON)
        bust_api_key_cache()
        variables = [
            {"key": GLOBAL_SECRET, "value": OWN["a global secret's value"], "var_type": "secret"},
            {
                "key": GLOBAL_VALUE,
                "value": OWN["a global variable's value"],
                "description": OWN["a global variable's description"],
            },
        ]
        await _ok(app, "PUT", "/api/v1/environment/global", ops, {"variables": variables})
        # Model credentials: the environment's, and one in the registry for every repository.
        env.setenv("ANTHROPIC_API_KEY", "env-key-000000000002")
        env.setenv("CLAUDE_CODE_OAUTH_TOKEN", "env-token-000000000003")
        env.setenv("CUSTOM_LLM_API_KEY", "env-key-000000000004")
        env.setenv("CUSTOM_LLM_BASE_URL", OWN["the custom provider's address"])
        made = await _ok(
            app,
            "POST",
            "/api/v1/credentials",
            ops,
            {"kind": "llm_api_key", "provider": "openrouter", "label": POOL_CREDENTIAL, "secret": "y" * 24},
        )
        pool = {"scope_kind": "global", "scope_value": "", "bindings": [{"credential_id": made["id"], "role": "llm"}]}
        await _ok(app, "PUT", "/api/v1/credentials/bindings", ops, pool)
        # A connection test of a provider, as the test route records it.
        await emit_audit(
            db, PROBE_AUDIT_ACTION, actor=PERSON, details={"provider": "anthropic", "status": "ok"}, audit_log_path=None
        )
        integrations = {
            "slack_webhook_url": "https://hooks.own-records.example.test/000000000005",
            "telegram_chat_id": OWN["the notifications chat"],
            "webhook_notify_url": OWN["where events are sent"],
            "linear_repo_map": OWN["which tracker team files on which repository"],
        }
        await _ok(app, "PUT", "/api/v1/config/integrations", ops, integrations)
        await self._retention_candidates()
        await self._shared(ops)
        forget_caches(app)

    async def _shared(self, ops: str) -> None:
        """Changes to what every repository's jobs run on, by the person."""
        app = self.app
        await _ok(app, "PATCH", "/api/v1/config/settings/scout", ops, {"revisit_hours": 48})
        await _ok(app, "PUT", "/api/v1/progression/pack", ops, DEFAULT_PACK.read_text() + f"\n# {SHARED_MARK}\n")
        await _ok(app, "PUT", "/api/v1/agents/developer", ops, {"description": f"Forges the change, {SHARED_MARK}."})
        templates = await _ok(app, "GET", "/api/v1/pipelines/templates", ops)
        template = templates[0] if isinstance(templates, list) else templates["templates"][0]
        await _ok(app, "PUT", f"/api/v1/pipelines/templates/{template['id']}", ops, {"description": SHARED_MARK})
        await _ok(app, "POST", "/api/v1/sandbox-profiles", ops, {"name": f"{SHARED_MARK}-profile"})
        await _ok(app, "PUT", f"/api/v1/skills/inline/{SHARED_MARK}", ops, {"description": "A note.", "body": "Hi."})
        await _ok(app, "PUT", "/api/v1/config/context", ops, {"system_context": SHARED_MARK, "assistant_context": ""})
        await _ok(app, "PUT", "/api/v1/config/provider", ops, {"disabled_models": ["grok-4.7"]})
        host = {"name": f"{SHARED_MARK}-host", "transport": "http", "address": "h.test"}
        await _ok(app, "POST", "/api/v1/hosts", ops, host)

    async def _retention_candidates(self) -> None:
        """An old finished job of the uncovered repository, with a code event, an agent's session and a QA image."""
        db, then = self.db, NOW - LONG_AGO
        await db.execute(
            "INSERT INTO jobs (job_id, repo, task, status, pipeline_template, created_at, started_at, finished_at) "
            "VALUES ($1, $2, 'An old job', 'completed', 'full-magnum-opus', $3, $3, $3)",
            OLD_JOB,
            UNCOVERED,
            then,
        )
        await db.execute(
            "INSERT INTO job_logs (job_id, stream, event_type, text, created_at) "
            """VALUES ($1, 'pipeline', 'tool_result', '{"type": "tool_result", "content": "ok"}', $2)""",
            OLD_JOB,
            then,
        )
        await db.execute(
            "INSERT INTO agent_sessions (id, job_id, agent_type, mode, messages, last_turn_at, created_at) "
            """VALUES ('ses-old-gizmo', $1, 'developer', 'sdk', '[{"role": "user"}]'::jsonb, $2, $2)""",
            OLD_JOB,
            then,
        )
        image = ImageSummary(
            id="sha256:old-gizmo-image", repo_tags=(OLD_IMAGE,), repo_digests=(), created_at=then.isoformat(), size=4096
        )
        self.daemon.images.append(image)


def _retention_sweeps(app: FastAPI, daemon: _Daemon) -> None:
    """The three sweeps the production lifespan builds, each keeping :data:`RETENTION_DAYS`."""
    state = app.state
    state.job_log_retention = JobLogRetentionSweep(JobLogRetentionRepository(state.db), retention_days=RETENTION_DAYS)
    state.agent_session_retention = AgentSessionRetentionSweep(state.agent_sessions_repo, retention_days=RETENTION_DAYS)
    state.qa_image_retention = QAImageRetentionSweep(
        runtime=daemon,  # type: ignore[arg-type]
        image_repo=state.qa_image_tags_repo,
        volume_state_repo=state.qa_volume_state_repo,
        jobs_repo=state.jobs_repo,
        instance_id="tests-instance",
        retention_days=RETENTION_DAYS,
    )


@asynccontextmanager
async def deployment_reads_app(db: DatabasePool, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Deployment]:
    """The app as a deployment with none of its own records; :meth:`Deployment.add_deployment_wide` adds them."""
    # A walk of every route reads far more than one key's minute of requests; the limiter is not what is read here.
    monkeypatch.setattr(RateLimitMiddleware, "_over_limit", lambda _self, _key, _max_rpm: False)
    for name in _DEPLOYMENT_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", ENABLED_PROVIDERS)
    async with repository_writes_app(db, monkeypatch) as (app, keys, values):
        # What the production lifespan seeds and builds beside _init_app_state.
        await seed_builtin_pipeline_templates(app.state.templates_repo)
        await seed_builtin_sandbox_profiles(app.state.profiles_repo)
        daemon = _Daemon()
        _retention_sweeps(app, daemon)
        forget_caches(app)
        yield Deployment(app, db, keys, monkeypatch, daemon, {repo: dict(v) for repo, v in values.items()})
