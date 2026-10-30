"""The budget lives in the settings document's ``budget`` section (#171), end to end on a real database.

- The dispatch gate and every job's ceiling read the section at each check: a
  change through ``PATCH /config/settings/budget`` holds from the next one.
- The first boot imports the saved ``budget_config`` row once, since a saved
  row won over the variables, else the variables; a second boot imports
  nothing, and a cap nobody set stays unset.
- With the default and with migrated values, the spend checks decide as they
  did: each case's expected values are what the readers before the section
  read for the same row and variables.
- ``PUT /config/budget`` stays one release as a deprecated, admin-only wrapper
  over the section.
- An organization's context-audit caps move from ``organizations.settings``
  into its layer once; ``PATCH /orgs`` still takes them for one release.
- The real lifespan wires all of it.

The section is taken from the application's catalog by name, so each test
fails with its own message on a tree that has no budget section.
"""

from __future__ import annotations

import math
import os
import tomllib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from packaging.version import Version
from structlog.testing import capture_logs

from embry0.api.app import create_app, lifespan
from embry0.api.deps import bust_api_key_cache
from embry0.config import Embry0Config
from embry0.orchestration.budget_gate import BudgetGate
from embry0.orchestration.nodes.agent import load_budget_policy
from embry0.services import organizations as org_service
from embry0.services.issue_executor import IssueExecutor
from embry0.settings.bootstrap import BOOTSTRAP_ACTOR, import_env_once, log_ignored_env
from embry0.settings.registry import Section
from embry0.settings.scope import Scope
from embry0.settings.sections import CATALOG
from embry0.settings.store import SettingsStore, section_reader
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token
from embry0.storage.repositories.jobs import JobsRepository
from embry0.storage.repositories.organizations import OrganizationsRepository
from embry0.storage.repositories.repos import ReposRepository
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401 - a fixture

pytestmark = pytest.mark.requires_postgres

REPO_ROOT = Path(__file__).resolve().parents[3]
BUDGET_URL = "/api/v1/config/settings/budget"
ADMIN = "emb_" + "a" * 43
OPERATOR = "emb_" + "o" * 43
WIDGETS_ONLY = "emb_" + "w" * 43


def _budget() -> Section[Any]:
    section = CATALOG.get("budget")
    assert section is not None, "budget is a section of the settings document"
    return section


async def _import(store: SettingsStore, config: Any) -> Any:
    """The boot's import, with the sources kept before the document; fails with its own message without them."""
    try:
        from embry0.settings.earlier import EARLIER_SOURCES
    except ImportError:
        pytest.fail("the settings import reads the budget_config row, once (embry0.settings.earlier)")
    return await import_env_once(store, config, earlier=EARLIER_SOURCES)


def _moved() -> Any:
    """The organization settings that moved, and the release that stops taking them on PATCH /orgs."""
    moved = getattr(org_service, "MOVED_SETTINGS", None)
    assert moved, "the organization settings the budget section took over are declared (MOVED_SETTINGS)"
    return moved, org_service.MOVED_SETTINGS_REMOVED_IN


async def _import_orgs(store: SettingsStore, rows: list[tuple[str, Any]]) -> Any:
    try:
        from embry0.settings.org_import import import_org_settings_once
    except ImportError:
        pytest.fail("no copy of the organization settings that moved into a section (embry0.settings.org_import)")
    moved, _ = _moved()
    return await import_org_settings_once(store, rows, moved)


def _key(key_id: str, scope: str, user_id: str | None, allowed: tuple[str, ...] | None = None) -> ApiKeyRow:
    return ApiKeyRow(
        id=key_id,
        user_id=user_id,
        name=f"{scope}-key",
        prefix="emb_xxxxxxxx",
        scope=scope,
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
        allowed_repos=allowed,
    )


KEYS = {
    hash_token(ADMIN): _key("key-admin0000001", "admin", "usr-admin"),
    hash_token(OPERATOR): _key("key-operator0001", "operate", "usr-admin"),
    hash_token(WIDGETS_ONLY): _key("key-widgets00001", "admin", None, ("acme/widgets",)),
}


def _as(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"}


def _config(**env: str) -> Embry0Config:
    with patch.dict(os.environ, env, clear=True):
        return Embry0Config(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
async def app(db_with_migrations: DatabasePool) -> AsyncIterator[Any]:
    bust_api_key_cache()
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")  # type: ignore[call-arg]
    app = create_app(config)
    state = app.state
    state.db = db_with_migrations
    state.settings = SettingsStore(db_with_migrations)
    state.organizations_repo = OrganizationsRepository(db_with_migrations)
    state.repos_repo = ReposRepository(db_with_migrations, default_forge="github")
    keys = MagicMock()
    keys.get_by_hash = AsyncMock(side_effect=lambda h: KEYS.get(h))
    keys.touch_last_used = AsyncMock()
    state.api_keys_repo = keys
    user = MagicMock(disabled=False, email="alex@example.com")
    state.users_repo = MagicMock(get=AsyncMock(side_effect=lambda uid: user if uid == "usr-admin" else None))
    yield app
    bust_api_key_cache()


@pytest.fixture
async def client(app: Any) -> AsyncIterator[AsyncClient]:
    # A route that raises answers 500 here, so a test fails on its own assertion, naming what it got.
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as c:
        yield c


def _executor(db: DatabasePool, store: SettingsStore) -> IssueExecutor:
    """The executor as the app builds it, for its budget gate alone."""
    executor = IssueExecutor.__new__(IssueExecutor)
    executor._db = db
    executor._settings = store
    executor._budget_gate = None
    return executor


async def _spend_today(db: DatabasePool, usd: float) -> None:
    jobs = JobsRepository(db)
    job_id = await jobs.create(repo="acme/widgets", task="spent")
    await db.execute("UPDATE jobs SET total_cost_usd = $2, status = 'completed' WHERE job_id = $1", job_id, usd)


async def _budget_config_row(db: DatabasePool, **columns: Any) -> None:
    names = ", ".join(["id", *columns])
    marks = ", ".join(f"${i}" for i in range(1, len(columns) + 2))
    await db.execute(f"INSERT INTO budget_config ({names}) VALUES ({marks})", "default", *columns.values())


# ---- the caps are read from the section, at every check ----


async def test_a_cap_patched_through_the_api_holds_at_the_next_dispatch(
    app: Any, client: AsyncClient, db_with_migrations: DatabasePool
) -> None:
    executor = _executor(db_with_migrations, app.state.settings)
    await _spend_today(db_with_migrations, 50.0)
    assert await executor.dispatch_refusal() is None, "$50 of today's spend is under the default $100 daily cap"

    resp = await client.patch(BUDGET_URL, json={"daily_cap_usd": 40}, headers=_as(ADMIN))
    assert resp.status_code == 200, f"PATCH /config/settings/budget takes daily_cap_usd: {resp.text}"
    refusal = await executor.dispatch_refusal()
    assert refusal is not None and refusal.limit == "daily", "the lowered cap holds at the next check, no restart"

    resp = await client.patch(BUDGET_URL, json={"daily_cap_usd": 0}, headers=_as(ADMIN))
    assert resp.status_code == 200, resp.text
    assert await executor.dispatch_refusal() is None, "0 is no daily cap, as it always was"


async def test_every_jobs_ceiling_and_mode_are_the_sections_at_each_check(app: Any, client: AsyncClient) -> None:
    graph = {"configurable": {"settings_store": app.state.settings}}
    assert await load_budget_policy(graph) == (10.0, "soft")
    resp = await client.patch(
        BUDGET_URL, json={"max_budget_per_job_usd": 4.5, "overrun_mode": "hard"}, headers=_as(ADMIN)
    )
    assert resp.status_code == 200, resp.text
    assert await load_budget_policy(graph) == (4.5, "hard"), "a job's next check reads the new ceiling and mode"


async def test_writing_the_section_takes_an_unrestricted_admin(client: AsyncClient) -> None:
    resp = await client.patch(BUDGET_URL, json={"daily_cap_usd": 40}, headers=_as(ADMIN))
    assert resp.status_code == 200, f"an unrestricted admin changes the budget: {resp.text}"
    resp = await client.patch(BUDGET_URL, json={"daily_cap_usd": 30}, headers=_as(OPERATOR))
    assert resp.status_code == 403, f"an operate key may not change the budget: {resp.status_code}"
    resp = await client.patch(BUDGET_URL, json={"daily_cap_usd": 30}, headers=_as(WIDGETS_ONLY))
    assert resp.status_code == 403, f"a key with a repository allowlist may not either: {resp.status_code}"


# ---- the saved row, imported once ----


async def test_a_saved_row_is_imported_once_and_wins_over_the_variables(db_with_migrations: DatabasePool) -> None:
    await _budget_config_row(
        db_with_migrations,
        max_budget_per_job_usd=25.0,
        daily_cap_usd=40.0,
        monthly_cap_usd=500.0,
        rate_limit_per_author_per_hour=2,
        overrun_mode="hard",
    )
    store = SettingsStore(db_with_migrations)
    config = _config(MAX_BUDGET_USD="99", DAILY_BUDGET_CAP_USD="1", CONTEXT_AUDIT_JOB_BUDGET_USD="3.5")

    result = await _import(store, config)

    assert result.earlier == {
        "budget": {
            "max_budget_per_job_usd": 25.0,
            "daily_cap_usd": 40.0,
            "rate_limit_per_author_per_hour": 2,
            "overrun_mode": "hard",
        }
    }, "the row's values, not the variables'; its monthly cap is the default and is left unwritten"
    budget = (await store.document())["data"]["budget"]
    assert budget["max_budget_per_job_usd"] == 25.0 and budget["daily_cap_usd"] == 40.0
    assert budget["context_audit_job_usd"] == 3.5, "a key the row never held still comes from its variable"
    (revision,) = await store.revisions()
    assert (revision["actor"], revision["source"], revision["reason"]) == (
        BOOTSTRAP_ACTOR,
        "bootstrap",
        "imported from the budget_config table and the environment",
    )
    text = (await store.document())["yaml_text"]
    assert "# Imported from the budget_config table on " in text
    assert text.count("CONTEXT_AUDIT_JOB_BUDGET_USD") == 1, f"the variable all three came from is named once: {text}"
    assert all(budget[key] == 3.5 for key in ("context_audit_job_usd", "area_map_job_usd", "fix_job_usd"))

    await db_with_migrations.execute("UPDATE budget_config SET daily_cap_usd = 7")
    again = await _import(SettingsStore(db_with_migrations), config)
    assert (again.imported, again.revision_id) == ({}, None), "the row is imported once"
    assert (await store.snapshot()).get(_budget()).daily_cap_usd == 40.0

    with capture_logs() as logs:
        await log_ignored_env(store, config)
    ignored = sorted(e["variable"] for e in logs if e["event"] == "settings_env_ignored")
    assert "MAX_BUDGET_USD" in ignored and "DAILY_BUDGET_CAP_USD" in ignored, ignored


async def test_a_saved_row_of_defaults_writes_nothing_and_still_wins(db_with_migrations: DatabasePool) -> None:
    """A row PUT /config/budget created holds the table's defaults: those were the caps, not the variables."""
    await _budget_config_row(db_with_migrations, rate_limit_per_author_per_hour=5)
    store = SettingsStore(db_with_migrations)

    result = await _import(store, _config(MAX_BUDGET_USD="50"))

    assert result.imported == {}
    assert "budget" not in (await store.document())["data"]
    assert (await store.snapshot()).get(_budget()).max_budget_per_job_usd == 10.0, "the row's 10, not the variable's 50"


async def test_without_a_saved_row_the_variables_are_imported(db_with_migrations: DatabasePool) -> None:
    store = SettingsStore(db_with_migrations)
    config = _config(MAX_BUDGET_USD="0", DAILY_BUDGET_CAP_USD="250", BUDGET_OVERRUN_MODE="hard")

    result = await _import(store, config)

    assert result.earlier == {}
    assert result.imported == {
        "budget": {"max_budget_per_job_usd": 0.0, "daily_cap_usd": 250.0, "overrun_mode": "hard"}
    }
    graph = {"configurable": {"settings_store": store}}
    assert await load_budget_policy(graph) == (0.0, "hard"), "MAX_BUDGET_USD=0 was no ceiling, and still is"


async def test_a_cap_nobody_set_stays_unset(app: Any, client: AsyncClient, db_with_migrations: DatabasePool) -> None:
    """No row and no variable: nothing is written, so each key keeps reading its default, a later default too."""
    store = app.state.settings
    result = await _import(store, _config())

    assert result.imported == {}
    document = await store.document()
    assert "budget" not in document["data"], "no cap is written into the document"
    assert {path for path in document["bootstrapped_keys"] if path.startswith("budget.")} == {
        f"budget.{key}" for key in _budget().imports()
    }, "every key is marked, so a variable set later is not imported"
    resp = await client.get("/api/v1/config/settings/effective", headers=_as(ADMIN))
    (section,) = [s for s in resp.json()["sections"] if s["name"] == "budget"]
    assert {k["key"]: k["source"] for k in section["keys"]} == dict.fromkeys(_budget().fields, "default")


# ---- the spend checks decide as before ----


class _Spend:
    def __init__(self, daily: float = 0.0, monthly: float = 0.0, dispatched: int = 0) -> None:
        self.rollup = {"daily": daily, "monthly": monthly}
        self.dispatched = dispatched

    async def spend_rollup(self) -> dict[str, float]:
        return dict(self.rollup)

    async def dispatch_count_since(self, actor: str, since: Any, *, excluding: str | None = None) -> int:
        return self.dispatched


#: (the row, the variables) -> what the readers before the section read: (ceiling, mode, daily, monthly, rate),
#: None for no limit. The ceiling and mode were the agent node's reading, the rest the dispatch gate's.
CASES: list[
    tuple[str, dict[str, Any] | None, dict[str, str], tuple[float | None, str, float | None, float | None, int | None]]
] = [
    ("nothing set", None, {}, (10.0, "soft", 100.0, 500.0, 5)),
    (
        "variables, some set to no limit",
        None,
        {
            "MAX_BUDGET_USD": "0",
            "DAILY_BUDGET_CAP_USD": "0",
            "MONTHLY_BUDGET_CAP_USD": "-1",
            "RATE_LIMIT_PER_AUTHOR_PER_HOUR": "0",
            "BUDGET_OVERRUN_MODE": "hard",
        },
        (None, "hard", None, None, None),
    ),
    (
        "variables, all custom",
        None,
        {
            "MAX_BUDGET_USD": "3",
            "DAILY_BUDGET_CAP_USD": "30",
            "MONTHLY_BUDGET_CAP_USD": "300",
            "RATE_LIMIT_PER_AUTHOR_PER_HOUR": "2",
        },
        (3.0, "soft", 30.0, 300.0, 2),
    ),
    (
        "a saved row, the variables ignored",
        {
            "max_budget_per_job_usd": 25.0,
            "daily_cap_usd": 40.0,
            "monthly_cap_usd": 900.0,
            "rate_limit_per_author_per_hour": 2,
            "overrun_mode": "hard",
        },
        {"MAX_BUDGET_USD": "99", "DAILY_BUDGET_CAP_USD": "1", "BUDGET_OVERRUN_MODE": "soft"},
        (25.0, "hard", 40.0, 900.0, 2),
    ),
    (
        "a row saved with one field",
        {"daily_cap_usd": 40.0},
        {"MAX_BUDGET_USD": "99", "MONTHLY_BUDGET_CAP_USD": "1"},
        (10.0, "soft", 40.0, 500.0, 5),
    ),
    (
        "a row only SQL could write",
        {
            "max_budget_per_job_usd": 0.0,
            "daily_cap_usd": 0.0,
            "monthly_cap_usd": float("inf"),
            "rate_limit_per_author_per_hour": -1,
            "overrun_mode": "",
        },
        {"MAX_BUDGET_USD": "6", "BUDGET_OVERRUN_MODE": "hard"},
        (6.0, "hard", None, None, None),
    ),
]


@pytest.mark.parametrize(("name", "row", "env", "before"), CASES, ids=[case[0] for case in CASES])
async def test_the_spend_checks_decide_as_before_under_default_and_migrated_values(
    db_with_migrations: DatabasePool,
    name: str,
    row: dict[str, Any] | None,
    env: dict[str, str],
    before: tuple[float | None, str, float | None, float | None, int | None],
) -> None:
    if row is not None:
        await _budget_config_row(db_with_migrations, **row)
    store = SettingsStore(db_with_migrations)
    await _import(store, _config(**env))
    ceiling, mode, daily, monthly, rate = before

    graph = {"configurable": {"settings_store": store}}
    assert await load_budget_policy(graph) == (ceiling or 0.0, mode), f"{name}: the job's ceiling and mode"

    reader = section_reader(store, _budget())
    for limit, cap in (("daily", daily), ("monthly", monthly)):
        if cap is None:
            spend = _Spend(**{limit: 1e9})
            assert (await BudgetGate(reader, spend).check()).allowed, f"{name}: no {limit} cap"
            continue
        under = _Spend(**{limit: round(cap - 0.01, 2)})
        assert (await BudgetGate(reader, under).check()).allowed, f"{name}: under the {limit} cap of {cap}"
        at = await BudgetGate(reader, _Spend(**{limit: cap})).check()
        assert (at.allowed, at.limit) == (False, limit), f"{name}: at the {limit} cap of {cap}"
    if rate is None:
        assert (await BudgetGate(reader, _Spend(dispatched=10_000)).check(actor="alex")).allowed, f"{name}: no limit"
    else:
        assert (await BudgetGate(reader, _Spend(dispatched=rate - 1)).check(actor="alex")).allowed, name
        at = await BudgetGate(reader, _Spend(dispatched=rate)).check(actor="alex")
        assert (at.allowed, at.limit) == (False, "rate_limit"), f"{name}: at the rate limit of {rate}"


# ---- PUT /config/budget, for one release ----


async def test_the_old_route_is_admin_only_now(client: AsyncClient, app: Any) -> None:
    resp = await client.put("/api/v1/config/budget", json={"daily_cap_usd": 40}, headers=_as(OPERATOR))
    assert resp.status_code == 403, f"PUT /config/budget takes the admin tier now: {resp.status_code}"
    resp = await client.put("/api/v1/config/budget", json={"daily_cap_usd": 40}, headers=_as(WIDGETS_ONLY))
    assert resp.status_code == 403, f"and a key with no repository allowlist: {resp.status_code}"
    assert "budget" not in (await app.state.settings.document())["data"]


async def test_the_old_route_writes_the_section_and_records_it(
    client: AsyncClient, app: Any, db_with_migrations: DatabasePool
) -> None:
    resp = await client.put(
        "/api/v1/config/budget", json={"daily_cap_usd": 40, "overrun_mode": "hard"}, headers=_as(ADMIN)
    )
    assert resp.status_code == 200 and resp.json() == {"status": "updated"}, resp.text
    assert (await app.state.settings.document())["data"]["budget"] == {"daily_cap_usd": 40, "overrun_mode": "hard"}
    (revision,) = await app.state.settings.revisions()
    assert (revision["source"], revision["reason"]) == ("person", "PUT /config/budget")
    audit = await db_with_migrations.fetch("SELECT action, details FROM audit_log ORDER BY id")
    assert [(r["action"], r["details"]["keys"]) for r in audit if r["action"].startswith(("settings", "budget"))] == [
        ("settings.updated", ["budget.daily_cap_usd", "budget.overrun_mode"])
    ]

    resp = await client.get("/api/v1/config/budget", headers=_as(OPERATOR))
    assert resp.status_code == 200
    assert resp.json() == {
        "max_budget_per_job_usd": 10.0,
        "daily_cap_usd": 40.0,
        "monthly_cap_usd": 500.0,
        "rate_limit_per_author_per_hour": 5,
        "overrun_mode": "hard",
    }, "GET reads the section's values of its five keys"


async def test_the_old_route_still_cannot_lift_a_cap(client: AsyncClient, app: Any) -> None:
    for body in ({"daily_cap_usd": 0}, {"rate_limit_per_author_per_hour": 0}, {"max_budget_per_job_usd": -1}):
        resp = await client.put("/api/v1/config/budget", json=body, headers=_as(ADMIN))
        assert resp.status_code == 422, f"{body}: {resp.status_code}"
    assert "budget" not in (await app.state.settings.document())["data"]


@pytest.mark.parametrize(
    "raw", ['{"daily_cap_usd": NaN}', '{"daily_cap_usd": Infinity}', '{"max_budget_per_job_usd": NaN}']
)
async def test_a_number_json_cannot_write_is_a_422_not_a_500(client: AsyncClient, app: Any, raw: str) -> None:
    """A JSON body may hold NaN or Infinity; the 422 that names it must not fail to echo it."""
    headers = {**_as(ADMIN), "Content-Type": "application/json"}
    for url, method in ((BUDGET_URL, "PATCH"), ("/api/v1/config/budget", "PUT")):
        resp = await client.request(method, url, content=raw, headers=headers)
        assert resp.status_code == 422, f"{method} {url} with {raw}: {resp.status_code} {resp.text[:200]}"
        assert resp.json()["detail"], f"{method} {url}: the 422 names the field"
    assert "budget" not in (await app.state.settings.document())["data"]


def test_the_old_route_is_marked_deprecated_with_its_removal() -> None:
    app = create_app(Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True))
    operation = app.openapi()["paths"]["/api/v1/config/budget"]
    for method in ("get", "put"):
        assert operation[method].get("deprecated") is True, f"{method.upper()} /config/budget is not deprecated"
        assert "removed in 0." in operation[method]["description"], f"{method.upper()} does not say when it goes"
        assert "/config/settings" in operation[method]["description"], f"{method.upper()} names no successor"
    assert not app.openapi()["paths"]["/api/v1/config/settings/{section}"]["patch"].get("deprecated")


def _overdue(version: str) -> list[str]:
    """What this release still serves that is past its removal: the old budget route and the moved settings."""
    from embry0.api.v1 import config as config_api

    removed_in = getattr(config_api, "BUDGET_ROUTE_REMOVED_IN", None)
    assert removed_in, "the deprecated budget route names the release that removes it"
    moved, moved_removed_in = _moved()
    overdue = []
    if Version(version) >= Version(removed_in):
        overdue.append("GET and PUT /config/budget")
    if Version(version) >= Version(moved_removed_in):
        overdue.extend(f"{m.legacy} on PATCH /orgs" for m in moved)
    return overdue


def test_the_old_paths_are_gone_by_the_release_that_removes_them() -> None:
    version = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert _overdue(version) == [], (
        f"embry0 {version} still serves {_overdue(version)}: remove the /config/budget routes (api/v1/config.py), "
        "their docs/api.md note and the budget_config import, and refuse MOVED_SETTINGS on PATCH and POST /orgs "
        "with a hint naming each key's place in the settings document"
    )


def test_the_removal_check_fires_at_the_removal_release() -> None:
    """The check above is quiet before the removal release only: it is not quiet by construction."""
    assert len(_overdue("0.2.0")) == 1 + len(_moved()[0])
    assert _overdue("0.1.9") == []


# ---- an organization's own caps ----


async def test_an_organizations_context_audit_caps_move_into_its_layer_once(
    db_with_migrations: DatabasePool, client: AsyncClient, app: Any
) -> None:
    organizations = app.state.organizations_repo
    await organizations.register(
        "acme", "github", settings={"context_audit_budget_usd": 7.5, "context_audit_max_jobs_per_tick": 1}
    )
    await organizations.register("beta", "github", settings={"context_audit_days": 7})
    # What POST /orgs stored unchecked once; the old reader read it as the deployment's.
    await organizations.register("gamma", "github", settings={"context_audit_budget_usd": 80.0})
    store = app.state.settings
    rows = [(org.owner, org.settings) for org in await organizations.list_all(include_removed=True)]

    result = await _import_orgs(store, rows)

    assert result.imported == {"acme": {"budget": {"context_audit_job_usd": 7.5, "context_audit_max_jobs_per_tick": 1}}}
    snapshot = await store.snapshot()
    acme, gamma = snapshot.get(_budget(), owner="acme"), snapshot.get(_budget(), owner="gamma")
    assert (acme.context_audit_job_usd, acme.context_audit_max_jobs_per_tick) == (7.5, 1)
    assert (gamma.context_audit_job_usd, gamma.context_audit_max_jobs_per_tick) == (2.0, 3), "gamma reads as before"
    assert [layer["scope"] for layer in await store.layers()] == ["organization:acme"], (
        "a layer is written only where something moved"
    )
    again = await _import_orgs(store, rows)
    assert again.imported == {}, "once"

    resp = await client.get("/api/v1/orgs/acme", headers=_as(ADMIN))
    assert resp.status_code == 200
    assert "context_audit_budget_usd" not in resp.json()["settings"], "a moved setting is the layer's, not shown here"


async def test_patch_orgs_still_takes_a_moved_setting_and_writes_it_into_the_layer(
    client: AsyncClient, app: Any
) -> None:
    await app.state.organizations_repo.register("acme", "github")
    resp = await client.patch(
        "/api/v1/orgs/acme",
        json={"settings": {"context_audit_budget_usd": 3.5, "auto_register": True}},
        headers=_as(ADMIN),
    )
    assert resp.status_code == 200, resp.text
    assert "deprecated" in resp.json(), "the answer says the setting moved"
    (note,) = resp.json()["deprecated"]
    assert (note["setting"], note["moved_to"], note["removed_in"]) == (
        "context_audit_budget_usd",
        "budget.context_audit_job_usd",
        _moved()[1],
    )
    org = await app.state.organizations_repo.get("acme")
    assert org.settings == {"auto_register": True}, "the moved setting is not stored on the organization any more"
    store = app.state.settings
    assert (await store.snapshot()).get(_budget(), owner="acme").context_audit_job_usd == 3.5
    (revision,) = await store.revisions(scope=Scope.organization("acme"))
    assert revision["source"] == "person"

    resp = await client.patch(
        "/api/v1/orgs/acme", json={"settings": {"context_audit_budget_usd": None}}, headers=_as(ADMIN)
    )
    assert resp.status_code == 200, resp.text
    assert (await store.snapshot()).get(_budget(), owner="acme").context_audit_job_usd == 2.0, "null: the deployment's"

    resp = await client.patch(
        "/api/v1/orgs/acme", json={"settings": {"context_audit_budget_usd": 60}}, headers=_as(ADMIN)
    )
    assert resp.status_code == 422, "the old route keeps its own range for the release it stays"


async def test_post_orgs_writes_a_moved_setting_into_the_layer_of_the_organization_it_registers(
    client: AsyncClient, app: Any
) -> None:
    body = {"owner": "acme", "probe": False, "settings": {"context_audit_max_jobs_per_tick": 1, "auto_register": True}}
    resp = await client.post("/api/v1/orgs", json=body, headers=_as(ADMIN))
    assert resp.status_code == 201, resp.text
    assert [note["moved_to"] for note in resp.json().get("deprecated", [])] == [
        "budget.context_audit_max_jobs_per_tick"
    ], "the answer says the setting moved"
    org = await app.state.organizations_repo.get("acme")
    assert org.settings == {"auto_register": True}
    assert (await app.state.settings.snapshot()).get(_budget(), owner="acme").context_audit_max_jobs_per_tick == 1

    body = {"owner": "globex", "probe": False, "settings": {"context_audit_budget_usd": 3}}
    resp = await client.post("/api/v1/orgs", json=body, headers=_as(WIDGETS_ONLY))
    assert resp.status_code == 403, f"a key with an allowlist may not write an organization's layer: {resp.text}"
    assert await app.state.organizations_repo.get("globex") is None, "refused before anything was registered"


# ---- the real lifespan ----


@pytest.mark.usefixtures("stub_container_runtime")
async def test_the_lifespan_imports_the_row_and_the_organizations_and_reads_the_section_everywhere(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("embry0.observability.configure_structlog", lambda **kw: None)
    await _budget_config_row(db_with_migrations, daily_cap_usd=40.0, overrun_mode="hard")
    await OrganizationsRepository(db_with_migrations).register(
        "acme", "github", settings={"context_audit_budget_usd": 7.5}
    )
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
        max_budget_usd=99.0,
    )
    app = create_app(config)
    async with lifespan(app):
        store = app.state.settings
        document = await store.document()
        assert document["data"].get("budget") == {"daily_cap_usd": 40.0, "overrun_mode": "hard"}, (
            "the boot imported the saved row, not MAX_BUDGET_USD"
        )
        acme = (await store.snapshot()).get(_budget(), owner="acme")
        assert acme.context_audit_job_usd == 7.5, "the boot copied the organization's cap into its layer"

        executor = app.state.issue_executor
        graph = executor._build_graph_config("job-wiring")
        assert graph["configurable"].get("budget_policy") is app.state.budget_policy, (
            "a job's ceiling is read through the app's budget policy"
        )
        assert app.state.budget_policy.store is store
        await _spend_today(db_with_migrations, 30.0)
        assert await executor.dispatch_refusal() is None
        await store.set_keys("budget", {"daily_cap_usd": 25}, actor="alex@example.com")
        refusal = await executor.dispatch_refusal()
        assert refusal is not None and refusal.limit == "daily", "the executor's gate reads the app's store"

        from embry0.services.organizations import Organization

        audit = app.state.context_audit
        assert hasattr(audit, "caps"), "the context audit loop reads its caps from the budget section"
        assert await audit.caps(Organization(owner="acme", forge="github")) == (3, 7.5)
        await store.set_keys(
            "budget", {"context_audit_max_jobs_per_tick": 1}, actor="alex@example.com", scope=Scope.organization("acme")
        )
        assert await audit.caps(Organization(owner="acme", forge="github")) == (1, 7.5), (
            "the context audit loop reads the section as it is now"
        )
        kept = len(await store.revisions())

    second = create_app(config)
    async with lifespan(second):
        assert len(await second.state.settings.revisions()) == kept, "a second boot imports nothing again"
        assert math.isclose((await second.state.settings.snapshot()).get(_budget()).daily_cap_usd, 25.0)


# ---- while the import is pending, the views show the caps that are enforced (#171 review) ----


async def _a_failed_import(conn: Any, config: Any) -> Any:
    import asyncpg

    raise asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed in the middle of operation")


def _pending_policy(store: SettingsStore, config: Embry0Config) -> Any:
    """The application's budget policy, every attempt at the import failing as the first start's did."""
    from embry0.services.budget_policy import BudgetPolicy
    from embry0.settings.earlier import EARLIER_SOURCES

    earlier = tuple(type(s)(section=s.section, label=s.label, read=_a_failed_import) for s in EARLIER_SOURCES)
    return BudgetPolicy(store, config, earlier=earlier, moved=org_service.MOVED_SETTINGS)


def _budget_keys(view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {k["key"]: k for k in next(s for s in view["sections"] if s["name"] == "budget")["keys"]}


async def test_while_the_import_is_pending_the_effective_view_shows_the_caps_enforced(
    app: Any, client: AsyncClient, db_with_migrations: DatabasePool
) -> None:
    """The review's case: the import failed over a saved row of 2 / 20 / 50 / 1 / hard. The General tab
    read 10 / 100 / soft as defaults, so "lowering" the daily cap from 100 to 80 raised it from 20."""
    await _budget_config_row(
        db_with_migrations,
        max_budget_per_job_usd=2.0,
        daily_cap_usd=20.0,
        monthly_cap_usd=50.0,
        rate_limit_per_author_per_hour=1,
        overrun_mode="hard",
    )
    config = _config(CONTEXT_AUDIT_JOB_BUDGET_USD="0.75")
    app.state.budget_policy = _pending_policy(app.state.settings, config)
    enforced = await app.state.budget_policy.settings()

    resp = await client.get("/api/v1/config/settings/effective", headers=_as(OPERATOR))
    assert resp.status_code == 200, resp.text
    keys = _budget_keys(resp.json())
    fleet = (
        "max_budget_per_job_usd",
        "daily_cap_usd",
        "monthly_cap_usd",
        "rate_limit_per_author_per_hour",
        "overrun_mode",
    )
    assert (
        {key: keys[key]["value"] for key in fleet}
        == {key: getattr(enforced, key) for key in fleet}
        == {
            "max_budget_per_job_usd": 2.0,
            "daily_cap_usd": 20.0,
            "monthly_cap_usd": 50.0,
            "rate_limit_per_author_per_hour": 1,
            "overrun_mode": "hard",
        }
    ), "the view shows the caps the gate and every job enforce, not the section's defaults"
    assert {keys[key]["source"] for key in fleet} == {"not_imported"}
    assert keys["daily_cap_usd"]["not_imported"] == {"read_from": "row", "label": "the budget_config table"}
    assert (keys["context_audit_job_usd"]["value"], keys["context_audit_job_usd"]["not_imported"]) == (
        0.75,
        {"read_from": "variable", "label": "CONTEXT_AUDIT_JOB_BUDGET_USD"},
    ), "a key the row never held reads its variable"
    assert (keys["continue_extra_max_usd"]["source"], keys["continue_extra_max_usd"]["not_imported"]) == (
        "default",
        None,
    ), "a key no older store held reads its default, as it is"

    resp = await client.patch(BUDGET_URL, json={"daily_cap_usd": 15}, headers=_as(ADMIN))
    assert resp.status_code == 200, resp.text
    keys = _budget_keys((await client.get("/api/v1/config/settings/effective", headers=_as(OPERATOR))).json())
    assert (keys["daily_cap_usd"]["value"], keys["daily_cap_usd"]["source"]) == (15.0, "document")
    assert (await app.state.budget_policy.settings()).daily_cap_usd == 15.0, "a key written since wins, as enforced"
    assert keys["monthly_cap_usd"]["source"] == "not_imported", "the others are still read from the row"


async def test_an_organizations_view_shows_its_own_settings_until_its_layer_imported_them(
    app: Any, client: AsyncClient, db_with_migrations: DatabasePool
) -> None:
    await OrganizationsRepository(db_with_migrations).register(
        "acme", "github", settings={"context_audit_budget_usd": 0.5, "context_audit_max_jobs_per_tick": 1}
    )
    config = _config(CONTEXT_AUDIT_JOB_BUDGET_USD="0.75")
    app.state.budget_policy = _pending_policy(app.state.settings, config)

    resp = await client.get("/api/v1/config/settings/effective?owner=acme", headers=_as(OPERATOR))
    assert resp.status_code == 200, resp.text
    keys = _budget_keys(resp.json())
    audit = keys["context_audit_job_usd"]
    assert (audit["value"], audit["source"], audit["not_imported"]) == (
        0.5,
        "not_imported",
        {"read_from": "organization", "label": "acme's own settings"},
    ), "the audit reads acme's own 0.5 until its layer imported it"
    assert audit["inherited"] == 0.75, "what an override overrides: the deployment's cap as enforced"
    assert keys["context_audit_max_jobs_per_tick"]["value"] == 1
    assert (keys["area_map_job_usd"]["value"], keys["area_map_job_usd"]["not_imported"]["read_from"]) == (
        0.75,
        "variable",
    ), "a key acme never held reads the deployment's, as enforced"
    repo_view = _budget_keys(
        (await client.get("/api/v1/config/settings/effective?repo=acme/widgets", headers=_as(OPERATOR))).json()
    )
    assert repo_view["context_audit_job_usd"]["value"] == 0.5, "acme's repositories are audited under its cap"
