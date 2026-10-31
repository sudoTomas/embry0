"""The scout's policy read per repository, through its organization's layer (#278), on a real database.

An organization's layer may set three keys of the ``scout`` section for its
own repositories: ``templates``, ``revisit_hours`` and ``max_leads_per_job``.
``GET /config/settings/effective?owner=`` reports them in effect, so the scout
must run them. Each loop here is the production scout with its settings
reader kept and the rest faked, so these tests also pin that production
reads each repository through its organization's layer (a reader of the
deployment's values alone would look exactly like organizations that set
nothing), and that one tick reads the ``scout`` section and the scout's
model (``background_models.scout``, #164) from one snapshot. The documents
are written through ``PUT /config/settings``.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app, lifespan
from embry0.config import Embry0Config
from embry0.services.leads import Lead
from embry0.services.scout import ScoutLoop
from embry0.settings.sections import CATALOG
from embry0.settings.store import Snapshot, build_snapshot
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.scout import ScoutRunsRepository
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401 - a fixture

pytestmark = pytest.mark.requires_postgres

URL = "/api/v1/config/settings"
HEADERS = {"X-Requested-With": "XMLHttpRequest"}
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
LOCAL = "custom/local-model"
BOTH = ["garden", "kintsugi-trace"]
#: The deployment document's model for the scout: without one, no repository is scouted (#164).
MODEL = f"background_models:\n  scout: {LOCAL}\n"
#: A kintsugi ledger of five graded cracks, more than any cap below keeps.
LEDGER = """## Ledger

| ID | Crack | Evidence | Grade | Seal |
|---|---|---|---|---|
| K-01 | Two handlers write the same row | `app/orders.py:40` | fracture | Lock the row (medium) |
| K-02 | A retry loop never gives up | `app/sync.py:12` | break | Cap the retries (small) |
| K-03 | The README names a removed flag | `README.md:9` | hairline | Drop the line (small) |
| K-04 | A log line repeats its own prefix | `app/log.py:7` | hairline | Drop the prefix (small) |
| K-05 | Two callers parse one date two ways | `app/dates.py:3` | fracture | Share one parser (medium) |
"""


@pytest.fixture
async def app(
    db_with_migrations: DatabasePool,
    stub_container_runtime: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Any]:
    """The real app through its real lifespan, with ``acme``, ``globex`` and ``quiet`` registered, and the
    operator's own server offered, so a write may name ``custom/<model>``."""
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
        )
    app = create_app(config)
    async with lifespan(app):
        for owner in ("acme", "globex", "quiet"):
            await app.state.organizations_repo.register(owner, "github")
        yield app


async def _put(app: Any, text: str, **params: str) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=HEADERS) as client:
        resp = await client.put(URL, content=text, headers={"Content-Type": "application/yaml"}, params=params)
    assert resp.status_code == 200, f"the settings document took {text!r} at {params or 'the deployment'}: {resp.text}"


class FakeJobs:
    """The jobs a loop creates; each stays pending, so it stays in flight."""

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

    def started(self, job_ids: Sequence[str]) -> dict[str, list[str]]:
        """Each repository to the templates started on it, sorted."""
        return {repo: [template for template, _ in jobs] for repo, jobs in self.pinned(job_ids).items()}

    def pinned(self, job_ids: Sequence[str]) -> dict[str, list[tuple[str, str]]]:
        """Each repository to the ``(template, model)`` of each job started on it, sorted."""
        out: dict[str, list[tuple[str, str]]] = {}
        for job_id in job_ids:
            created = self.rows[job_id]["created_with"]
            model = str(created["pipeline_config"]["agent_models_override"]["*"])
            out.setdefault(str(created["repo"]), []).append((str(created["pipeline_template"]), model))
        return {repo: sorted(jobs) for repo, jobs in out.items()}


class FakeLeads:
    def __init__(self) -> None:
        self.stored: list[Lead] = []

    async def upsert(self, leads: Sequence[Lead], *, now: datetime, areas: Any = None) -> None:
        self.stored += list(leads)

    def by_repo(self) -> dict[str, list[str]]:
        """Each repository to the severities of the leads stored for it, in order."""
        out: dict[str, list[str]] = {}
        for lead in self.stored:
            out.setdefault(lead.repo, []).append(lead.severity)
        return out


def _loop(
    app: Any, db: DatabasePool, repos: Sequence[str], *, jobs: FakeJobs | None = None, leads: FakeLeads | None = None
) -> ScoutLoop:
    """The production scout with every reader of the settings documents it was built with kept, and its
    repositories, areas, gate, job store, leads and clock faked."""

    async def listed() -> list[str]:
        return list(repos)

    async def areas_for(repo: str) -> list[Any]:
        return []

    async def gate() -> Any:
        return None

    async def submit(job_id: str, unit: Any, text: str) -> None:
        return None

    production: ScoutLoop = app.state.scout
    return dataclasses.replace(
        production,
        runs=ScoutRunsRepository(db),
        jobs=jobs or FakeJobs(),
        leads=leads or FakeLeads(),
        repos=listed,
        areas_for=areas_for,
        ask_gate=gate,
        submit=submit,
        clock=lambda: NOW,
    )


def _count_snapshots(app: Any, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """How many snapshots the settings store has handed out, in a list the test reads after a tick."""
    store = app.state.settings
    read = store.snapshot
    count = [0]

    async def counted() -> Any:
        count[0] += 1
        return await read()

    monkeypatch.setattr(store, "snapshot", counted)
    return count


def _snapshot(documents: Mapping[str, Mapping[str, Any]]) -> Snapshot:
    """A snapshot of ``documents``, each stored scope to its values, as the store builds one."""
    rows = [
        {"scope": scope, "data": dict(data), "generation": 1, "updated_at": None} for scope, data in documents.items()
    ]
    return build_snapshot(CATALOG, rows, log=False)


async def test_an_organization_s_templates_start_on_its_repositories_only(
    app: Any, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _put(app, "scout:\n  max_in_flight: 10\n" + MODEL)
    await _put(app, "scout:\n  templates: [garden]\n", owner="acme")
    await _put(app, "scout:\n  revisit_hours: 48\n", owner="globex")
    await _put(app, "scout:\n  templates: [garden]\nbackground_models:\n  scout: false\n", owner="quiet")
    jobs = FakeJobs()
    repos = ["acme/widgets", "acme/gadgets", "quiet/tools", "globex/gizmos", "other/tools"]
    loop = _loop(app, db_with_migrations, repos, jobs=jobs)
    snapshots = _count_snapshots(app, monkeypatch)

    started = jobs.started((await loop.tick())["started"])
    taken = snapshots[0]
    assert (started.get("acme/widgets"), started.get("acme/gadgets")) == (["garden"], ["garden"]), (
        f"acme's layer names garden only, so only garden starts on acme's repositories: {started}"
    )
    assert started.get("globex/gizmos") == BOTH, (
        f"globex's layer sets no templates, so its repository runs the deployment's: {started}"
    )
    assert started.get("other/tools") == BOTH, (
        f"a repository of an organization with no layer runs the deployment's templates: {started}"
    )
    assert "quiet/tools" not in started, (
        f"quiet's layer runs the scout on no model, so its repository is not scouted, whatever its templates: {started}"
    )
    assert taken == 1, (
        "one tick takes one snapshot and reads both the scout section and the scout's model from it, for every "
        f"repository, never one read per section or per repository: {taken}"
    )


async def test_an_organization_s_revisit_hours_hold_on_its_repositories_only(
    app: Any, db_with_migrations: DatabasePool
) -> None:
    await _put(app, "scout:\n  templates: [garden]\n  max_in_flight: 10\n" + MODEL)
    await _put(app, "scout:\n  revisit_hours: 2\n", owner="acme")
    await _put(app, "scout:\n  revisit_hours: 48\n", owner="globex")
    runs = ScoutRunsRepository(db_with_migrations)
    last = {"acme/widgets": 5, "other/tools": 5, "globex/gizmos": 30, "other/kit": 30}
    for n, (repo, hours) in enumerate(last.items()):
        await runs.record(job_id=f"past-{n}", repo=repo, area="", template="garden", now=NOW - timedelta(hours=hours))
        await runs.harvested(f"past-{n}", leads=0, now=NOW)
    jobs = FakeJobs()
    loop = _loop(app, db_with_migrations, list(last), jobs=jobs)

    started = jobs.started((await loop.tick())["started"])
    assert "acme/widgets" in started, (
        f"acme's layer scouts its repositories again after 2 hours, so acme/widgets, 5 hours on, is due: {started}"
    )
    assert "globex/gizmos" not in started, (
        f"globex's layer waits 48 hours, so globex/gizmos, 30 hours on, is not due yet: {started}"
    )
    assert sorted(started) == ["acme/widgets", "other/kit"], (
        "a repository of an organization with no layer waits the deployment's 24 hours: other/kit, 30 hours on, "
        f"is due and other/tools, 5 hours on, is not: {sorted(started)}"
    )


async def test_an_organization_s_max_leads_per_job_holds_on_its_repositories_only(
    app: Any, db_with_migrations: DatabasePool
) -> None:
    await _put(app, "scout:\n  max_in_flight: 0\n  max_leads_per_job: 3\n" + MODEL)
    await _put(app, "scout:\n  max_leads_per_job: 1\n", owner="acme")
    await _put(app, "scout:\n  max_leads_per_job: 2\n", owner="globex")
    runs = ScoutRunsRepository(db_with_migrations)
    jobs, leads = FakeJobs(), FakeLeads()
    for n, repo in enumerate(("acme/widgets", "globex/gizmos", "other/tools")):
        await runs.record(job_id=f"done-{n}", repo=repo, area="", template="kintsugi-trace", now=NOW)
        jobs.rows[f"done-{n}"] = {"status": "completed", "result_summary": LEDGER}
    loop = _loop(app, db_with_migrations, [], jobs=jobs, leads=leads)

    harvested = (await loop.tick())["harvested_leads"]
    stored = leads.by_repo()
    assert stored.get("acme/widgets") == ["high"], (
        f"acme's layer keeps one lead a job, the worst, of the five it found on acme's repositories: {stored}"
    )
    assert stored.get("globex/gizmos") == ["high", "medium"], f"globex's layer keeps two of the five: {stored}"
    assert stored.get("other/tools") == ["high", "medium", "medium"], (
        f"a repository of an organization with no layer keeps the deployment's three of the five: {stored}"
    )
    assert harvested == 6, f"the tick reports the 1 + 2 + 3 leads it stored: {harvested}"


async def test_production_reads_each_repository_s_policy_and_model_from_one_snapshot(
    app: Any, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real lifespan's scout, on its own readers: each repository's templates and model through its
    organization's layer, both from one snapshot a tick.

    ``ScoutLoop`` constructs happily with a reader of the deployment's values,
    and then every organization's layer is shown in effect and never run.
    """
    await _put(app, "scout:\n  max_in_flight: 10\n" + MODEL)
    await _put(app, "scout:\n  templates: [garden]\nbackground_models:\n  scout: custom/acme-model\n", owner="acme")
    jobs = FakeJobs()
    loop = _loop(app, db_with_migrations, ["acme/widgets", "globex/gizmos"], jobs=jobs)
    snapshots = _count_snapshots(app, monkeypatch)

    started = jobs.pinned((await loop.tick())["started"])
    assert started.get("acme/widgets") == [("garden", "custom/acme-model")], (
        "production reads acme/widgets through acme's layer: garden alone, on acme's model; a reader of the "
        f"deployment's values alone never reads an organization's layer: {started}"
    )
    assert started.get("globex/gizmos") == [("garden", LOCAL), ("kintsugi-trace", LOCAL)], (
        f"globex has no layer: the deployment's templates, on the deployment's model: {started}"
    )
    assert snapshots[0] == 1, f"production's tick takes one snapshot of the settings documents: {snapshots[0]}"


#: Before a write: no model for the scout, so nothing starts.
BEFORE: dict[str, dict[str, Any]] = {
    "deployment": {"scout": {"max_in_flight": 10}, "background_models": {"scout": False}}
}
#: One write, each way: the documents after it start garden alone, on the model it names.
AFTER: dict[str, tuple[dict[str, dict[str, Any]], str]] = {
    "an organization's layer": (
        {
            **BEFORE,
            "organization:acme": {"scout": {"templates": ["garden"]}, "background_models": {"scout": "custom/acme-m"}},
        },
        "custom/acme-m",
    ),
    "the deployment's document": (
        {
            "deployment": {
                "scout": {"max_in_flight": 10, "templates": ["garden"]},
                "background_models": {"scout": "custom/m"},
            }
        },
        "custom/m",
    ),
}


@pytest.mark.parametrize("written", sorted(AFTER))
async def test_a_write_landing_during_a_tick_never_mixes_two_versions_of_the_documents(
    app: Any, db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch, written: str
) -> None:
    """A write that commits after the tick's first read of the store, as one can at any moment."""
    after, model = AFTER[written]
    reads = [0]

    async def snapshot() -> Snapshot:
        reads[0] += 1
        return _snapshot(BEFORE if reads[0] == 1 else after)

    monkeypatch.setattr(app.state.settings, "snapshot", snapshot)
    jobs = FakeJobs()
    loop = _loop(app, db_with_migrations, ["acme/widgets"], jobs=jobs)

    started = jobs.pinned((await loop.tick())["started"])
    assert started in ({}, {"acme/widgets": [("garden", model)]}), (
        f"a tick acts on one version of the documents: the one before the write to {written} starts nothing, the one "
        f"after it garden alone on {model}; a tick that read them twice mixed the two: {started}"
    )
