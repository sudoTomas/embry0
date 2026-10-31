"""The real lifespan recovers in-flight jobs at boot and drains at shutdown (#307).

Runs ``lifespan`` against the test database with the container runtime
stubbed (as ``tests/unit/metrics/test_lifespan_wiring.py`` does). The
executor's recovery and drain entry points are replaced by recorders: this
pins that production calls them, with what; their behaviour has tests of its own.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from embry0.api.app import create_app, lifespan
from embry0.config import Embry0Config
from embry0.orchestration import channels
from embry0.orchestration.checkpoint import checkpointer_context
from embry0.services.issue_executor import IssueExecutor
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.jobs import JobsRepository
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401 — fixture

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


@pytest.mark.changes_schema  # the boot creates the checkpointer's tables
async def test_boot_recovers_jobs_and_shutdown_drains(
    db_with_migrations: DatabasePool,
    stub_container_runtime: dict[str, MagicMock],  # noqa: F811 — the imported fixture
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = JobsRepository(db_with_migrations)
    checkpointed = await jobs.create(repo="o/r", task="t")
    await jobs.update(checkpointed, status="running")
    async with checkpointer_context(db_with_migrations._url):  # noqa: SLF001
        pass
    await db_with_migrations.execute(
        "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata)"
        " VALUES ($1, '', 'c1', '{}'::jsonb, '{}'::jsonb)",
        checkpointed,
    )
    deferred = await jobs.create(repo="o/r", task="t")
    await jobs.record_launch(deferred, {"runner": "workflow", "issue_id": None, "issue": {"title": "t"}})
    stateless = await jobs.create(repo="o/r", task="t")
    await jobs.update(stateless, status="running")

    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        IssueExecutor, "resume_after_restart", lambda self, job: calls.append(("resume", job["job_id"]))
    )
    monkeypatch.setattr(IssueExecutor, "relaunch", lambda self, job: calls.append(("relaunch", job["job_id"])))

    async def drain(self: IssueExecutor, timeout: float) -> list[str]:
        calls.append(("drain", timeout))
        return []

    monkeypatch.setattr(IssueExecutor, "drain", drain)

    config = Embry0Config(
        _env_file=None,
        database_url=db_with_migrations._url,  # noqa: SLF001
        auth_dev_mode=True,
        webhook_dev_mode=True,
        api_key="",
        watcher_enabled=False,
        volume_reaper_enabled=False,
        linear_reconcile_mode="off",
        telegram_bot_token="",
        xai_proxy_enabled=False,
        auth_proxy_enabled=False,
        context_proxy_enabled=False,
        shutdown_drain_seconds=42,
    )
    channels.set_fold_observer(None)
    app = create_app(config)
    async with lifespan(app):
        assert sorted(c for c in calls if c[0] != "drain") == sorted(
            [("resume", checkpointed), ("relaunch", deferred)]
        ), f"boot must resume the checkpointed job and launch the deferred one again; calls {calls}"
        kept = {job_id: (await jobs.get(job_id) or {}).get("status") for job_id in (checkpointed, deferred)}
        assert kept == {checkpointed: "running", deferred: "pending"}, (
            f"the boot sweep must leave the jobs boot recovers in flight; they read {kept}"
        )
        row = await jobs.get(stateless)
        assert row is not None and (row["status"], row["error_code"]) == ("failed", "ERR_ORPHANED"), (
            f"a running job with nothing to resume from must fail at boot as before; it reads {row}"
        )
        assert ("drain", 42) not in calls
    channels.set_fold_observer(None)

    assert calls[-1] == ("drain", 42), f"shutdown must drain the executor for SHUTDOWN_DRAIN_SECONDS; calls {calls}"
