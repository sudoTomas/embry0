"""A cap is never loosened by an import that has not happened (#171 review).

When the first start's import of the budget fails (a database that drops the
connection mid-transaction), the readers must not fall back to the code
defaults: until its keys are in the import ledger, the dispatch gate and every
job's cap read them as the release before did, the saved ``budget_config`` row
and then the variables; an organization's audit caps read its own settings
until its layer imported them. The import is tried again while the process
runs, and the failure is logged at error level until it succeeds.

Every name this needs that the change adds is looked up when used, so each
test fails on its own assertion on a tree without it.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Mapping
from typing import Any
from unittest.mock import patch

import asyncpg
import pytest
from structlog.testing import capture_logs

from embry0.config import Embry0Config
from embry0.orchestration.budget_gate import BudgetGate
from embry0.orchestration.nodes.agent import load_budget_policy
from embry0.settings.bootstrap import import_env_once
from embry0.settings.scope import Scope
from embry0.settings.sections import CATALOG
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool

pytestmark = pytest.mark.requires_postgres

ROW = {
    "max_budget_per_job_usd": 2.0,
    "daily_cap_usd": 20.0,
    "monthly_cap_usd": 50.0,
    "rate_limit_per_author_per_hour": 1,
    "overrun_mode": "hard",
}


def _config(**env: str) -> Embry0Config:
    with patch.dict(os.environ, env, clear=True):
        return Embry0Config(_env_file=None)  # type: ignore[call-arg]


def _policy_module() -> Any:
    try:
        return importlib.import_module("embry0.services.budget_policy")
    except ImportError:
        pytest.fail("no budget policy reads a key the import has not imported the old way (budget_policy)")


def _sources(read: Any) -> tuple[Any, ...]:
    """The application's earlier sources, with ``read`` in place of each one's own."""
    try:
        from embry0.settings.earlier import EARLIER_SOURCES
    except ImportError:
        pytest.fail("the settings import reads the budget_config row (embry0.settings.earlier)")
    return tuple(type(s)(section=s.section, label=s.label, read=read) for s in EARLIER_SOURCES)


async def _dropped(conn: Any, config: Any) -> Any:
    raise asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed in the middle of operation")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Spend:
    def __init__(self, daily: float = 0.0) -> None:
        self.daily = daily

    async def spend_rollup(self) -> dict[str, float]:
        return {"daily": self.daily, "monthly": 0.0}

    async def dispatch_count_since(self, actor: str, since: Any, *, excluding: str | None = None) -> int:
        return 0


async def _row(db: DatabasePool, **columns: Any) -> None:
    names = ", ".join(["id", *columns])
    marks = ", ".join(f"${i}" for i in range(1, len(columns) + 2))
    await db.execute(f"INSERT INTO budget_config ({names}) VALUES ({marks})", "default", *columns.values())


async def _failed_boot(db: DatabasePool, config: Any) -> SettingsStore:
    """The first start: its import drops the connection mid-transaction, and the boot goes on."""
    store = SettingsStore(db)
    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await import_env_once(store, config, earlier=_sources(_dropped))
    document = await store.document()
    assert not [k for k in document["bootstrapped_keys"] if k.startswith("budget.")], "nothing was marked"
    return store


async def test_after_a_failed_import_the_gate_refuses_past_the_saved_daily_cap(
    db_with_migrations: DatabasePool,
) -> None:
    """The reviewer's case: a saved row of 2/20/50/1/hard, an import that drops mid-transaction."""
    await _row(db_with_migrations, **ROW)
    config = _config(MAX_BUDGET_USD="2")
    store = await _failed_boot(db_with_migrations, config)
    module = _policy_module()
    # The retry fails too: the readers must still read the row.
    policy = module.BudgetPolicy(store, config, earlier=_sources(_dropped), clock=Clock())

    with capture_logs() as logs:
        decision = await BudgetGate(policy.settings, Spend(daily=25.0)).check()
    assert (decision.allowed, decision.limit) == (False, "daily"), (
        f"spend past the saved $20 daily cap was let through after a failed import: {decision}"
    )
    assert decision.details["cap_usd"] == 20.0
    assert await load_budget_policy({"configurable": {"budget_policy": policy}}) == (2.0, "hard"), (
        "a job's ceiling and mode are the saved row's, not the defaults"
    )
    errors = [e["event"] for e in logs if e["log_level"] == "error"]
    assert "settings_budget_not_imported" in errors and "settings_budget_import_retry_failed" in errors, errors


async def test_the_import_is_tried_again_while_the_process_runs(db_with_migrations: DatabasePool) -> None:
    await _row(db_with_migrations, **ROW)
    config = _config()
    store = await _failed_boot(db_with_migrations, config)
    module = _policy_module()
    calls = {"n": 0}
    healthy = _sources(None)[0]

    async def flaky(conn: Any, cfg: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            return await _dropped(conn, cfg)
        from embry0.storage.repositories.budget_config import read_row

        return await read_row(conn, cfg)

    clock = Clock()
    policy = module.BudgetPolicy(
        store, config, earlier=(type(healthy)(healthy.section, healthy.label, flaky),), clock=clock
    )
    assert (await policy.settings()).daily_cap_usd == 20.0, "the first check tries again, fails, reads the row"
    assert calls["n"] == 1
    clock.now += 5
    await policy.settings()
    assert calls["n"] == 1, "not tried again within the retry interval"
    clock.now += module.RETRY_SECONDS
    with capture_logs() as logs:
        settings = await policy.settings()
    assert calls["n"] == 2 and settings.daily_cap_usd == 20.0
    assert "settings_budget_import_retried" in [e["event"] for e in logs]
    document = await store.document()
    assert document["data"]["budget"]["daily_cap_usd"] == 20.0, "the retry imported the row into the document"
    assert "budget.daily_cap_usd" in document["bootstrapped_keys"]
    with capture_logs() as logs:
        await policy.settings()
    assert not [e for e in logs if e["log_level"] == "error"], "nothing is logged once it is imported"


async def test_without_a_saved_row_a_key_not_imported_reads_its_variable(db_with_migrations: DatabasePool) -> None:
    config = _config(DAILY_BUDGET_CAP_USD="15", CONTEXT_AUDIT_JOB_BUDGET_USD="0.75")
    store = await _failed_boot(db_with_migrations, config)
    policy = _policy_module().BudgetPolicy(store, config, earlier=_sources(_dropped), clock=Clock())
    settings = await policy.settings()
    assert (settings.daily_cap_usd, settings.context_audit_job_usd, settings.monthly_cap_usd) == (15.0, 0.75, 500.0)


async def test_a_key_written_since_wins_over_the_old_reading(db_with_migrations: DatabasePool) -> None:
    await _row(db_with_migrations, **ROW)
    config = _config()
    store = await _failed_boot(db_with_migrations, config)
    await store.set_keys("budget", {"daily_cap_usd": 30}, actor="alex@example.com")
    policy = _policy_module().BudgetPolicy(store, config, earlier=_sources(_dropped), clock=Clock())
    settings = await policy.settings()
    assert (settings.daily_cap_usd, settings.monthly_cap_usd) == (30.0, 50.0), "the newer change, then the row"


async def test_when_no_snapshot_can_be_read_every_key_reads_the_old_way(db_with_migrations: DatabasePool) -> None:
    await _row(db_with_migrations, **ROW)
    config = _config()
    store = SettingsStore(db_with_migrations)

    async def broken() -> Any:
        raise RuntimeError("the documents cannot be read")

    store.snapshot = broken  # type: ignore[method-assign]
    policy = _policy_module().BudgetPolicy(store, config, clock=Clock())
    decision = await BudgetGate(policy.settings, Spend(daily=25.0)).check()
    assert (decision.allowed, decision.limit) == (False, "daily")


async def test_a_job_whose_policy_cannot_be_read_falls_back_to_the_variables(db_with_migrations: DatabasePool) -> None:
    class Down:
        async def settings(self) -> Any:
            raise ConnectionResetError("db down")

    graph = {
        "configurable": {
            "budget_policy": Down(),
            "embry0_config": _config(MAX_BUDGET_USD="3", BUDGET_OVERRUN_MODE="hard"),
        }
    }
    assert await load_budget_policy(graph) == (3.0, "hard"), "the variables the release before fell back to"


async def test_one_organizations_failed_copy_stops_no_other_and_is_read_from_its_settings(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's three organizations: the second one's copy drops its connection."""
    from embry0.services.organizations import Organization

    org_import = importlib.import_module("embry0.settings.org_import")
    moved = importlib.import_module("embry0.services.organizations").MOVED_SETTINGS
    legacy = {"context_audit_budget_usd": 0.5, "context_audit_max_jobs_per_tick": 0}
    orgs = [Organization(owner=o, forge="github", settings=dict(legacy)) for o in ("aaa", "bbb", "ccc")]
    store = SettingsStore(db_with_migrations)
    real = org_import._import_one
    bbb_attempts = {"n": 0}

    async def flaky(*a: Any, **k: Any) -> Any:
        if a[1] == Scope.organization("bbb"):
            bbb_attempts["n"] += 1
            if bbb_attempts["n"] <= 2:
                raise asyncpg.exceptions.ConnectionDoesNotExistError("connection lost")
        return await real(*a, **k)

    monkeypatch.setattr(org_import, "_import_one", flaky)
    with capture_logs() as logs:
        result = await org_import.import_org_settings_once(store, [(o.owner, o.settings) for o in orgs], moved)
    assert sorted(result.imported) == ["aaa", "ccc"], "the others went on"
    assert getattr(result, "failed", None) == ("bbb",)
    assert [e["owner"] for e in logs if e["event"] == "settings_org_import_failed" and e["log_level"] == "error"] == [
        "bbb"
    ]

    module = _policy_module()
    clock = Clock()
    policy = module.BudgetPolicy(store, _config(), moved=moved, clock=clock)
    view = await policy.view(orgs)  # tries bbb again: the copy drops a second time
    assert bbb_attempts["n"] == 2, "the policy tried the organization's copy again"
    caps = {
        o.owner: (
            view.organization(o.owner, o.settings).context_audit_max_jobs_per_tick,
            view.organization(o.owner, o.settings).context_audit_job_usd,
        )
        for o in orgs
    }
    assert caps == {"aaa": (0, 0.5), "bbb": (0, 0.5), "ccc": (0, 0.5)}, (
        f"an organization not imported yet reads its own settings, not the deployment's 3 and 2.0: {caps}"
    )
    clock.now += module.RETRY_SECONDS
    await policy.view(orgs)
    assert bbb_attempts["n"] == 3
    layer = await store.document(Scope.organization("bbb"))
    assert layer["data"].get("budget") == {"context_audit_job_usd": 0.5, "context_audit_max_jobs_per_tick": 0}, (
        "the retry copied bbb's settings into its layer"
    )


def test_a_policy_view_reads_an_organizations_own_settings_only_until_its_layer_imported_them() -> None:
    from embry0.services.organizations import MOVED_SETTINGS
    from embry0.settings.store import build_snapshot

    module = _policy_module()
    settings: Mapping[str, Any] = {"context_audit_budget_usd": 0.5}
    rows = [
        {"scope": "deployment", "data": {}, "generation": 1, "updated_at": None, "bootstrapped_keys": []},
        {
            "scope": "organization:done",
            "data": {"budget": {"context_audit_job_usd": 4.0}},
            "generation": 1,
            "updated_at": None,
            "bootstrapped_keys": ["budget.context_audit_job_usd"],
        },
    ]
    view = module.BudgetView(build_snapshot(CATALOG, rows, token=(), log=False), moved=MOVED_SETTINGS)
    assert view.organization("waiting", settings).context_audit_job_usd == 0.5
    assert view.organization("done", settings).context_audit_job_usd == 4.0, "imported: its layer, not the old row"
    assert view.organization("waiting", None).context_audit_job_usd == 2.0
