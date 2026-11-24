"""A cap is never loosened by a copy that has not happened, nor by a read that failed (#171 review).

An organization's audit caps read its own settings until its layer has
copied them; the copy is tried again while the process runs, and the
failure is logged at error level until it succeeds. A budget whose
documents cannot be read raises: no reader falls back to a default in
place of what the operator set.

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
from embry0.settings.scope import Scope
from embry0.settings.sections import CATALOG
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool

pytestmark = pytest.mark.requires_postgres


def _config(**env: str) -> Embry0Config:
    with patch.dict(os.environ, env, clear=True):
        return Embry0Config(_env_file=None)  # type: ignore[call-arg]


def _policy_module() -> Any:
    try:
        return importlib.import_module("embry0.services.budget_policy")
    except ImportError:
        pytest.fail("no budget policy every reader reads the budget through (budget_policy)")


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


async def test_when_no_snapshot_can_be_read_every_reader_raises_rather_than_reading_a_default(
    db_with_migrations: DatabasePool,
) -> None:
    """No budget is read in place of the documents: the view, the gate and a job's cap each raise the read's error."""
    store = SettingsStore(db_with_migrations)

    async def broken() -> Any:
        raise RuntimeError("the documents cannot be read")

    store.snapshot = broken  # type: ignore[method-assign]
    policy = _policy_module().BudgetPolicy(store, _config(), clock=Clock())
    with pytest.raises(RuntimeError, match="the documents cannot be read"):
        await policy.view()
    with pytest.raises(RuntimeError, match="the documents cannot be read"):
        await BudgetGate(policy.settings, Spend(daily=25.0)).check()
    with pytest.raises(RuntimeError, match="the documents cannot be read"):
        await load_budget_policy({"configurable": {"budget_policy": policy}})


async def test_a_job_whose_policy_cannot_be_read_fails_rather_than_reading_a_default(
    db_with_migrations: DatabasePool,
) -> None:
    """#551: the job's budget is what the operator set, not a default in its place."""

    class Down:
        async def settings(self) -> Any:
            raise ConnectionResetError("db down")

    graph = {
        "configurable": {
            "budget_policy": Down(),
            "embry0_config": _config(),
        }
    }
    with pytest.raises(ConnectionResetError, match="db down"):
        await load_budget_policy(graph)


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
