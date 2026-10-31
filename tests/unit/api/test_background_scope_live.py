"""A repository taken out of a background feature is not read by it on the next tick, with no restart (#165).

On the real app, through its real lifespan, on Postgres: each production loop
(the scout, the Slop-O-Meter, the leads finders) keeps the readers it was
built with (the settings documents and the registry), so these tests also pin
that production reads its repositories through ``background.<feature>`` of
the settings document, from one snapshot a tick. Enrollment is written
through ``PUT /api/v1/config/settings`` with ``?owner=`` or ``?repo=``, as an
operator writes it, and repositories are unregistered through
``DELETE /api/v1/repos/...``. Docker is stubbed; nothing runs git.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app, lifespan
from embry0.config import Embry0Config
from embry0.services.leads import Lead
from embry0.services.scout import ScoutLoop
from embry0.services.slop_meter.history import Commit
from embry0.services.slop_meter.loop import SlopMeterLoop, SlopMeterSettings
from embry0.services.slop_meter.mirror import GitMirror
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.scout import ScoutRunsRepository
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401 - a fixture

pytestmark = pytest.mark.requires_postgres

URL = "/api/v1/config/settings"
HEADERS = {"X-Requested-With": "XMLHttpRequest"}
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
LOCAL = "custom/local-model"
LEDGER = """## Ledger

| ID | Crack | Evidence | Grade | Seal |
|---|---|---|---|---|
| K-01 | A retry loop never gives up | `app/sync.py:12` | break | Cap the retries (small) |
"""


@pytest.fixture
async def app(
    db_with_migrations: DatabasePool,
    stub_container_runtime: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncIterator[Any]:
    """The real app through its real lifespan, with ``acme`` and ``globex`` registered and four of their
    repositories. The meter's and the leads' schedules are off, so only the ticks a test runs read anything."""
    monkeypatch.setenv("OPENAI_COMPATIBLE_PROVIDERS", "custom")
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(  # type: ignore[call-arg]
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
            leads_enabled=False,
            slop_meter_interval_seconds=0,
            slop_meter_data_path=str(tmp_path / "slop-meter"),
        )
    app = create_app(config)
    async with lifespan(app):
        # With its schedule off, the meter only removes, once, what left its scope: let that finish first.
        for task in list(app.state.background_tasks):
            if task.get_coro().__qualname__ == "SlopMeterLoop.start":
                await task
        for owner in ("acme", "globex"):
            await app.state.organizations_repo.register(owner, "github")
        for repo in ("acme/widgets", "acme/gadgets", "globex/gizmos", "globex/tools"):
            await app.state.repos_repo.register(repo, forge="github")
        yield app


async def _call(app: Any, method: str, url: str, text: str | None = None, **params: str) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=HEADERS) as client:
        if text is None:
            return await client.request(method, url, params=params)
        return await client.request(
            method, url, content=text, headers={"Content-Type": "application/yaml"}, params=params
        )


async def _put(app: Any, text: str, **params: str) -> None:
    resp = await _call(app, "PUT", URL, text, **params)
    assert resp.status_code == 200, f"the settings document took {text!r} at {params or 'the deployment'}: {resp.text}"


def _count_snapshots(app: Any, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    store = app.state.settings
    read = store.snapshot
    count = [0]

    async def counted() -> Any:
        count[0] += 1
        return await read()

    monkeypatch.setattr(store, "snapshot", counted)
    return count


# ---- the scout ----


class FakeJobs:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def create(self, **kwargs: Any) -> str:
        job_id = f"job-{len(self.rows) + 1:03d}"
        self.rows[job_id] = {"created_with": kwargs, "status": "pending"}
        return job_id

    async def update(self, job_id: str, **kwargs: Any) -> None:
        self.rows[job_id].update(kwargs)

    async def get(self, job_id: str) -> dict[str, Any] | None:
        return self.rows.get(job_id)

    def started(self, job_ids: Sequence[str]) -> list[str]:
        return sorted(str(self.rows[j]["created_with"]["repo"]) for j in job_ids)


class FakeLeads:
    def __init__(self) -> None:
        self.stored: list[Lead] = []

    async def upsert(self, leads: Sequence[Lead], *, now: datetime, areas: Any = None) -> None:
        self.stored += list(leads)


async def test_a_repository_opted_out_of_the_scout_is_not_scouted_on_the_next_tick(
    app: Any, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _put(
        app, f"scout:\n  templates: [kintsugi-trace]\n  max_in_flight: 10\nbackground_models:\n  scout: {LOCAL}\n"
    )
    await _put(app, "background:\n  scout: true\n", owner="acme")
    jobs, leads, clock = FakeJobs(), FakeLeads(), [NOW]

    async def no_areas(repo: str) -> list[Any]:
        return []

    async def go() -> None:
        return None

    async def submit(job_id: str, unit: Any, text: str) -> None:
        return None

    production: ScoutLoop = app.state.scout
    loop = dataclasses.replace(
        production,
        runs=ScoutRunsRepository(db_with_migrations),
        jobs=jobs,
        leads=leads,
        areas_for=no_areas,
        ask_gate=go,
        submit=submit,
        clock=lambda: clock[0],
    )
    snapshots = _count_snapshots(app, monkeypatch)

    first = await loop.tick()
    assert jobs.started(first["started"]) == ["acme/gadgets", "acme/widgets"], (
        "acme's layer enrolls its registered repositories in the scout, and nothing enrolls globex's: "
        f"{jobs.started(first['started'])}"
    )
    assert snapshots[0] == 1, f"one tick reads its enrollment from the one snapshot it takes: {snapshots[0]}"

    await _put(app, "background:\n  scout: false\n", repo="acme/gadgets")
    for row in jobs.rows.values():
        row.update(status="completed", result_summary=LEDGER)
    clock[0] = NOW + timedelta(hours=25)
    second = await loop.tick()
    assert jobs.started(second["started"]) == ["acme/widgets"], (
        "the next tick, with no restart, scouts acme/widgets again and not acme/gadgets, which its own layer "
        f"took out: {jobs.started(second['started'])}"
    )
    assert sorted({lead.repo for lead in leads.stored}) == ["acme/widgets"], (
        "and the finished job of acme/gadgets adds no leads: the scout no longer reads anything of it"
    )


# ---- the Slop-O-Meter ----


class NoCalls:
    async def endpoint(self, feature: str, model: str, repo: str | None) -> None:
        return None


def _meter(app: Any, clock: list[datetime]) -> SlopMeterLoop:
    """The production meter's readers (the registry and the settings documents), its record of departures, its
    audit writer and its real store, with a mirror under the test's own root, a clock the test moves, and no
    credential: reading a repository fails at once and is recorded."""
    production: SlopMeterLoop = app.state.slop_meter
    assert hasattr(production, "_read") and hasattr(production, "_pruner"), (
        "the production meter reads its enrollment (background.slop_meter) and its judge's model from one "
        "snapshot a tick, and removes what it kept only after background.prune_after_days"
    )

    async def no_credential(repo: str) -> None:
        return None

    async def no_bots(repo: str) -> tuple[str, ...]:
        return ()

    pruner = production._pruner  # noqa: SLF001 - the production record and audit writer are what this test pins
    return SlopMeterLoop(
        repos=production._repos,  # noqa: SLF001 - the production reader is what this test pins
        read=production._read,  # noqa: SLF001
        store=app.state.slop_meter_repo,
        mirror=production._mirror,  # noqa: SLF001
        exits=pruner.exits,
        audit=pruner.audit,
        token_for=no_credential,
        client_for=no_credential,
        bots=no_bots,
        calls=NoCalls(),  # type: ignore[arg-type]
        settings=SlopMeterSettings(),
        clock=lambda: clock[0],
    )


async def _kept(app: Any, repo: str, login: str) -> Path:
    """What the meter keeps of ``repo``: its rows, a contributor's name, email and login, and its mirror."""
    store = app.state.slop_meter_repo
    commit = Commit("a1", NOW, "Ana Example", f"{login}@example.com", "Work", (("src/a.py", 10, 2),), ())
    await store.set_state(repo, mirrored_at=NOW)
    await store.add_commits(repo, 0, [commit.to_row()])
    await store.add_events(repo, [("ic:1", "comment", login, NOW)])
    await store.set_login(login, f"{login}@example.com")
    mirror: GitMirror = app.state.slop_meter._mirror  # noqa: SLF001
    path = mirror.path(repo)
    (path / "objects").mkdir(parents=True)
    (path / "HEAD").write_text("ref: refs/heads/main\n")
    return path


async def _rows(db: DatabasePool, repo: str) -> int:
    return int(
        await db.fetchval(
            "SELECT (SELECT count(*) FROM slop_meter_repos WHERE repo = $1)"
            " + (SELECT count(*) FROM slop_meter_commits WHERE repo = $1)"
            " + (SELECT count(*) FROM slop_meter_events WHERE repo = $1)",
            repo,
        )
    )


async def _audited(db: DatabasePool) -> list[tuple[str, str, str]]:
    rows = await db.fetch("SELECT action, actor, details FROM audit_log WHERE action LIKE 'slop_meter.%' ORDER BY id")
    return [(str(r["action"]), str(r["actor"]), str((r["details"] or {}).get("repo"))) for r in rows]


async def test_opting_out_or_unregistering_removes_the_meter_s_mirror_and_contributor_rows_a_week_later(
    app: Any, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = db_with_migrations
    await _put(app, "background:\n  slop_meter: true\n", owner="acme")
    gadgets = await _kept(app, "acme/gadgets", "gadgeteer")
    widgets = await _kept(app, "acme/widgets", "widgeteer")
    clock = [NOW]
    loop = _meter(app, clock)
    snapshots = _count_snapshots(app, monkeypatch)

    await loop.tick()
    assert gadgets.exists() and widgets.exists() and await _rows(db, "acme/gadgets") == 3, (
        "both repositories are in the meter's scope through acme's layer: nothing of them is removed"
    )
    assert snapshots[0] == 1, f"one tick reads its enrollment and its judge's model from one snapshot: {snapshots[0]}"

    await _put(app, "background:\n  slop_meter: false\n", repo="acme/gadgets")
    await loop.tick()
    assert gadgets.exists() and await _rows(db, "acme/gadgets") == 3, (
        "the next tick, with no restart, finds acme/gadgets out of scope and removes nothing yet"
    )
    pending = await db.fetch("SELECT feature, repo FROM background_scope_exits")
    assert [(r["feature"], r["repo"]) for r in pending] == [("slop_meter", "acme/gadgets")], "it records it left"

    await _put(app, "background:\n  slop_meter: true\n", repo="acme/gadgets")
    clock[0] = NOW + timedelta(days=3)
    await loop.tick()
    assert await db.fetchval("SELECT count(*) FROM background_scope_exits") == 0, (
        "enrolled again within the week: the removal is cancelled"
    )
    await _put(app, "background:\n  slop_meter: false\n", repo="acme/gadgets")
    clock[0] = NOW + timedelta(days=4)
    await loop.tick()
    clock[0] = NOW + timedelta(days=11)
    await loop.tick()
    assert not gadgets.exists(), "a week after it left again, the mirror of the repository opted out goes"
    assert await _rows(db, "acme/gadgets") == 0, "and every row the meter kept of it"
    assert await app.state.slop_meter_repo.logins() == {"widgeteer": "widgeteer@example.com"}, (
        "with the login of the contributor only it knew"
    )
    assert widgets.exists() and await _rows(db, "acme/widgets") > 0, "acme/widgets, still enrolled, keeps everything"
    assert await _audited(db) == [("slop_meter.pruned", "embry0:slop-meter", "acme/gadgets")], (
        "the removal leaves an audit row, by the meter"
    )

    # Something stays in scope, so the floor (nothing in scope, nothing removed) does not hold the removal.
    await _put(app, "background:\n  slop_meter: true\n", owner="globex")
    resp = await _call(app, "DELETE", "/api/v1/repos/acme/widgets")
    assert resp.status_code in (200, 204), resp.text
    await loop.tick()
    assert widgets.exists(), "a repository unregistered waits its week too"
    clock[0] = NOW + timedelta(days=18)
    await loop.tick()
    assert not widgets.exists() and await _rows(db, "acme/widgets") == 0, "then it loses its mirror and its rows"
    assert await app.state.slop_meter_repo.logins() == {}, "and its contributors' logins"


async def test_an_inactive_repository_is_no_longer_read_and_loses_nothing(
    app: Any, db_with_migrations: DatabasePool
) -> None:
    db = db_with_migrations
    await _put(app, "background:\n  slop_meter: true\n", owner="acme")
    gadgets = await _kept(app, "acme/gadgets", "gadgeteer")
    await _kept(app, "acme/widgets", "widgeteer")
    clock = [NOW]
    loop = _meter(app, clock)
    await app.state.repos_repo.set_active("acme/gadgets", False)
    await loop.tick()
    clock[0] = NOW + timedelta(days=60)
    await loop.tick()
    assert gadgets.exists() and await _rows(db, "acme/gadgets") == 3, (
        "making a repository inactive hides it and stops the reading; it removes nothing"
    )
    assert await db.fetchval("SELECT count(*) FROM background_scope_exits") == 0
    errors = await db.fetchval("SELECT error FROM slop_meter_repos WHERE repo = 'acme/gadgets'")
    assert errors is None, "and it is not read: no attempt to fetch it was recorded"


# ---- the leads finders ----


def _leads(app: Any) -> Any:
    """The production leads loop, its readers of the settings documents and its targets kept, with no forge,
    no Linear and no trigger labels: each finder skips, and the pass says which repositories it read."""
    from embry0.services.leads.loop import LeadsLoop

    production: LeadsLoop = app.state.leads_loop

    async def nothing(*args: Any) -> Any:
        return None

    async def no_labels() -> list[str]:
        return []

    async def untracked(tracker: str, repo: str, key: str) -> bool:
        return False

    return dataclasses.replace(
        production, client_for=nothing, linear=nothing, trigger_labels=no_labels, tracked=untracked, area_of=nothing
    )


async def test_a_repository_or_an_organization_opted_out_of_leads_is_not_read_on_the_next_pass(
    app: Any, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    for repo in ("acme/widgets", "acme/gadgets", "globex/gizmos"):
        await app.state.repo_preferences_repo.upsert(repo, owners=["octo"])
    loop = _leads(app)
    assert sorted(await loop.tick()) == ["acme/gadgets", "acme/widgets", "globex/gizmos"], (
        "leads read every registered repository someone answers for, as before; globex/tools has no owners"
    )
    await _put(app, "background:\n  leads: false\n", owner="acme")
    await _put(app, "background:\n  leads: true\n", repo="acme/gadgets")
    assert sorted(await loop.tick()) == ["acme/gadgets", "globex/gizmos"], (
        "on the next pass, with no restart, acme's layer takes its repositories out of leads and acme/gadgets's "
        "own layer brings it back"
    )


async def test_one_leads_pass_reads_its_policy_and_its_repositories_from_one_snapshot(
    app: Any, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    await app.state.repo_preferences_repo.upsert("acme/widgets", owners=["octo"])
    await _put(app, "background:\n  leads: false\n", owner="globex")
    loop = _leads(app)
    snapshots = _count_snapshots(app, monkeypatch)
    assert sorted(await loop.tick()) == ["acme/widgets"]
    assert snapshots[0] == 1, (
        "one leads pass reads the leads section and background.leads from one snapshot of the settings documents, "
        f"so a write during the pass cannot hand it two versions: {snapshots[0]} snapshots"
    )


# ---- an upgrade, through the real lifespan ----


async def test_an_existing_deployment_keeps_its_scope_on_upgrade_and_a_new_one_starts_off(
    db_with_migrations: DatabasePool,
    stub_container_runtime: Any,  # noqa: F811
    tmp_path: Path,
) -> None:
    db = db_with_migrations
    long_ago = datetime.now(UTC) - timedelta(days=400)
    for repo in ("acme/busy", "acme/idle", "acme/owned"):
        await db.execute("INSERT INTO repos (repo, forge, created_at) VALUES ($1, 'github', $2)", repo, long_ago)
    await db.execute("INSERT INTO jobs (job_id, task, repo) VALUES ('job-1', 't', 'acme/busy')")
    await db.execute("INSERT INTO repo_preferences (repo, owners) VALUES ('acme/owned', '[\"octo\"]'::jsonb)")
    with patch.dict(os.environ, {"SCOUT_EXCLUDE": "acme/owned"}, clear=True):
        config = Embry0Config(  # type: ignore[call-arg]
            _env_file=None,
            database_url=db._url,  # noqa: SLF001
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
            leads_enabled=False,
            slop_meter_interval_seconds=0,
            slop_meter_data_path=str(tmp_path / "slop-meter"),
        )
    app = create_app(config)
    async with lifespan(app):
        scout: ScoutLoop = app.state.scout
        assert hasattr(scout, "scope") and hasattr(app.state.slop_meter, "_read"), (
            "the scout and the meter read their repositories through background.<feature> of the settings document"
        )
        scouted = await scout.scope(await scout.settings())
        meter = await app.state.slop_meter._read()  # noqa: SLF001
        metered = meter.enrollment.scope("slop_meter", (await app.state.slop_meter._repos()).active)  # noqa: SLF001
        await app.state.repos_repo.register("acme/fresh", forge="github")
        await db.execute("INSERT INTO jobs (job_id, task, repo) VALUES ('job-2', 't', 'acme/fresh')")
        later = await scout.scope(await scout.settings())
    assert scouted == ["acme/busy"], (
        "the first start of the release keeps what the scout read before: a job in the last 90 days, owners, a "
        f"watched stack, minus SCOUT_EXCLUDE; acme/idle had none of them: {scouted}"
    )
    assert metered == ["acme/busy", "acme/owned"], f"and what the meter read: {metered}"
    assert later == ["acme/busy"], (
        "after the upgrade a job no longer enrolls a repository: acme/fresh, registered and run on since, is "
        f"not scouted until an operator enrolls it: {later}"
    )
