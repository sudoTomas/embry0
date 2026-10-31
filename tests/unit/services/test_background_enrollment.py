"""What the scout and the Slop-O-Meter read before enrollment became explicit is enrolled once (#165), on Postgres.

Before, each read the registered, active repositories the world map showed (a
job created in the last 90 days, owners, or an enabled watched stack), plus
``SCOUT_REPOS`` or ``SLOP_METER_REPOS`` and minus ``SCOUT_EXCLUDE`` or
``SLOP_METER_EXCLUDE``. The first start of this release writes exactly that
set into the repositories' layers, as it stood at a moment recorded once, so
an existing deployment keeps reading what it read and a new one reads nothing
until an operator enrolls a repository.
"""

from __future__ import annotations

import importlib
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from embry0.config import Embry0Config
from embry0.settings.document import load_yaml
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool

pytestmark = pytest.mark.requires_postgres

DAY = date(2026, 10, 31)
#: The moment the import is first asked about: the first start of the release.
MOMENT = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def _enrollment_import() -> Any:
    try:
        return importlib.import_module("embry0.services.background_enrollment")
    except ModuleNotFoundError:
        pytest.fail(
            "embry0.services.background_enrollment imports, once, the repositories the scout and the meter read "
            "before enrollment became explicit; it does not exist, so nothing carries them over"
        )


def _scope() -> Any:
    return importlib.import_module("embry0.services.background_scope")


def _config(**env: str) -> Embry0Config:
    with patch.dict(os.environ, env, clear=True):
        return Embry0Config(_env_file=None)  # type: ignore[call-arg]


async def _register(db: DatabasePool, repo: str, *, at: datetime, active: bool = True) -> None:
    await db.execute(
        "INSERT INTO repos (repo, forge, active, created_at) VALUES ($1, 'github', $2, $3)", repo, active, at
    )


async def _job(db: DatabasePool, repo: str, *, at: datetime) -> None:
    job_id = f"job-{repo.replace('/', '-')}-{int(at.timestamp())}"
    await db.execute("INSERT INTO jobs (job_id, task, repo, created_at) VALUES ($1, 't', $2, $3)", job_id, repo, at)


async def _existing_deployment(db: DatabasePool) -> None:
    """A deployment that ran before this release, with every way the old rule read a repository and every
    way it did not."""
    long_ago = MOMENT - timedelta(days=400)
    for repo in (
        "acme/jobbed",
        "acme/old-job",
        "acme/owned",
        "acme/watched",
        "acme/quiet",
        "acme/excluded",
        "Acme/Listed",
        "acme/late",
    ):
        await _register(db, repo, at=long_ago)
    await _register(db, "acme/inactive", at=long_ago, active=False)
    await _job(db, "ACME/JOBBED", at=MOMENT - timedelta(days=10))  # a job in the window, spelled otherwise
    await _job(db, "acme/old-job", at=MOMENT - timedelta(days=100))  # out of the window
    await _job(db, "acme/inactive", at=MOMENT - timedelta(days=1))  # inactive: the old rule skipped it
    await _job(db, "acme/excluded", at=MOMENT - timedelta(days=1))
    await _job(db, "ghost/unregistered", at=MOMENT - timedelta(days=1))  # a job on a repository nobody registered
    await _job(db, "acme/late", at=MOMENT + timedelta(days=1))  # a job after the upgrade enrolls nothing
    await db.execute("INSERT INTO repo_preferences (repo, owners) VALUES ('acme/owned', '[\"octo\"]'::jsonb)")
    await db.execute("INSERT INTO watcher_stacks (repo, enabled, logql) VALUES ('acme/watched', TRUE, 'q')")


async def _read(store: SettingsStore, feature: str, registered: list[str]) -> list[str]:
    return _scope().enrollment(await store.snapshot()).scope(feature, registered)


async def _registered(db: DatabasePool) -> list[str]:
    return [str(r["repo"]) for r in await db.fetch("SELECT repo FROM repos WHERE active ORDER BY repo")]


async def test_an_existing_deployment_keeps_reading_what_it_read_and_its_lists_hold(
    db_with_migrations: DatabasePool,
) -> None:
    module = _enrollment_import()
    db = db_with_migrations
    await _existing_deployment(db)
    store = SettingsStore(db)
    config = _config(
        SLOP_METER_REPOS="acme/listed, ghost/missing, acme/excluded, acme/inactive",
        SLOP_METER_EXCLUDE="acme/excluded, ghost/other",
    )

    with capture_logs() as logs:
        result = await module.import_enrollment_once(store, config, today=DAY, now=MOMENT)

    old_rule = ["acme/excluded", "acme/jobbed", "acme/owned", "acme/watched"]
    assert await _read(store, "scout", await _registered(db)) == old_rule, (
        "the scout keeps the registered, active repositories the world map showed at the upgrade: a job in the "
        "last 90 days (any spelling), owners, or a watched stack; not a job 100 days old, an inactive "
        "repository, or a job on one nobody registered"
    )
    assert await _read(store, "slop_meter", await _registered(db)) == [
        "acme/jobbed",
        "Acme/Listed",
        "acme/owned",
        "acme/watched",
    ], "the meter keeps the same, plus what SLOP_METER_REPOS added and minus what SLOP_METER_EXCLUDE took out"
    assert "acme/inactive" not in result.written and "acme/late" not in result.written, (
        "no layer is written for an inactive repository, nor for one whose only job came after the upgrade: "
        f"{sorted(result.written)}"
    )
    assert result.marked == ("scout", "slop_meter"), "both features' imports are done"
    assert result.skipped == (
        ("SLOP_METER_REPOS", "ghost/missing", "not registered"),
        ("SLOP_METER_REPOS", "acme/inactive", "inactive"),
    ), "an include name nobody registered, or one naming an inactive repository, is not enrolled"
    skipped = {e["repo"]: e for e in logs if e["event"] == "background_enrollment_name_skipped"}
    assert {name: (e["log_level"], e["variable"]) for name, e in skipped.items()} == {
        "ghost/missing": ("error", "SLOP_METER_REPOS"),
        "acme/inactive": ("error", "SLOP_METER_REPOS"),
    }, "each is logged at error level, with the variable that names it: the meter stopped reading it"
    assert (
        "register it" in skipped["ghost/missing"]["hint"].lower()
        and "remove it from" in skipped["ghost/missing"]["hint"]
    ), "the hint says to register and enroll it, or take it out of the list"
    assert all("ghost/other" != e.get("repo") for e in logs if e["log_level"] == "error"), (
        "an exclude name nobody registered excludes nothing, and is no error"
    )
    assert any(e["event"] == "background_enrollment_name_skipped" and e["repo"] == "ghost/missing" for e in logs), (
        "and named in a warning"
    )

    excluded = await store.document(_scope_of("acme/excluded"))
    assert load_yaml(excluded["yaml_text"]) == {"background": {"scout": True, "slop_meter": False}}, (
        "an excluded repository is written false, even when the include list names it too, as the exclude list "
        "won before; so enrolling its organization later does not bring it back"
    )
    assert "background.slop_meter" in excluded["bootstrapped_keys"], "each key is marked in the layer's own ledger"
    jobbed = await store.document(_scope_of("acme/jobbed"))
    assert jobbed["yaml_text"].startswith("# Imported on 2026-10-31: the scout and the Slop-O-Meter, read before"), (
        f"the layer says where its values came from: {jobbed['yaml_text']!r}"
    )
    (revision,) = await store.revisions(scope=_scope_of("acme/jobbed"))
    assert (revision["actor"], revision["source"]) == ("embry0:bootstrap", "upgrade"), (
        "the old rule's enrollment is the release's own upgrade, recorded with history and undo"
    )
    (listed,) = await store.revisions(scope=_scope_of("Acme/Listed"))
    assert listed["source"] == "bootstrap", "a value a variable contributed is recorded as imported from it"
    deployment = await store.document()
    assert {"background.scout", "background.slop_meter"} <= set(deployment["bootstrapped_keys"])
    assert load_yaml(deployment["yaml_text"] or "{}") in ({}, None), "the deployment's own document is not written"


def _scope_of(repo: str) -> Any:
    from embry0.settings.scope import Scope

    return Scope.repository(repo)


async def test_the_lists_import_exactly_once(db_with_migrations: DatabasePool) -> None:
    module = _enrollment_import()
    db = db_with_migrations
    await _register(db, "acme/widgets", at=MOMENT - timedelta(days=1))
    await _register(db, "acme/gadgets", at=MOMENT - timedelta(days=1))
    store = SettingsStore(db)

    first = await module.import_enrollment_once(store, _config(SCOUT_REPOS="acme/widgets"), today=DAY, now=MOMENT)
    assert first.written == {"acme/widgets": {"scout": True}}
    # The operator then takes acme/widgets out, and the next start has other lists.
    async with store.edit(actor="ops@example.com", source="person", scope=_scope_of("acme/widgets")) as draft:
        draft.replace("background:\n  scout: false\n", {"background": {"scout": False}}, draft.schema_version)
    again = await module.import_enrollment_once(
        store, _config(SCOUT_REPOS="acme/gadgets", SCOUT_EXCLUDE="acme/widgets"), today=DAY, now=MOMENT
    )
    assert again.written == {} and again.marked == (), (
        "the lists are read at the first start of the release and never again, whatever they say later"
    )
    assert await _read(store, "scout", await _registered(db)) == [], (
        "acme/gadgets, listed only after the import ran, is not enrolled, and the operator's false for "
        "acme/widgets stands"
    )
    with capture_logs() as logs:
        found = module.log_ignored_lists(await store.snapshot(), _config(SCOUT_REPOS="acme/gadgets"))
    assert found == [("SCOUT_REPOS", "background.scout")]
    assert [e["variable"] for e in logs if e["event"] == "settings_env_ignored"] == ["SCOUT_REPOS"], (
        "every start says the variable is set and no longer read"
    )


async def test_a_new_deployment_starts_with_the_scout_and_the_meter_off(db_with_migrations: DatabasePool) -> None:
    module = _enrollment_import()
    db = db_with_migrations
    store = SettingsStore(db)

    result = await module.import_enrollment_once(store, _config(), today=DAY, now=MOMENT)
    assert result.written == {} and result.marked == ("scout", "slop_meter"), (
        "a new deployment registered nothing before its first start: nothing to carry over, and done"
    )
    await _register(db, "acme/widgets", at=MOMENT + timedelta(hours=1))
    await _job(db, "acme/widgets", at=MOMENT + timedelta(hours=2))
    await db.execute("INSERT INTO repo_preferences (repo, owners) VALUES ('acme/widgets', '[\"octo\"]'::jsonb)")
    assert await module.import_enrollment_once(store, _config(), today=DAY) == module.EnrollmentImport()
    registered = await _registered(db)
    assert await _read(store, "scout", registered) == [] and await _read(store, "slop_meter", registered) == [], (
        "a job, owners or a watched stack enroll nothing on a new deployment: only an operator does"
    )
    assert await _read(store, "leads", registered) == ["acme/widgets"], "leads are not imported, and read it"


async def test_a_retried_import_counts_only_what_stood_at_its_first_start_and_what_was_written_since_stands(
    db_with_migrations: DatabasePool,
) -> None:
    module = _enrollment_import()
    db = db_with_migrations
    store = SettingsStore(db)
    await _register(db, "acme/before", at=MOMENT - timedelta(days=30))
    await _job(db, "acme/before", at=MOMENT - timedelta(days=1))
    await _register(db, "acme/second", at=MOMENT - timedelta(days=30))
    await db.execute("INSERT INTO repo_preferences (repo, owners) VALUES ('acme/second', '[\"octo\"]'::jsonb)")

    # The first start records its moment, writes acme/before's layer, and fails on acme/second's.
    real = module._write

    async def flaky(store: SettingsStore, repo: str, planned: Any, day: str) -> Any:
        if repo == "acme/second":
            raise ConnectionError("the database went away")
        return await real(store, repo, planned, day)

    with patch.object(module, "_write", flaky):
        failed = await module.import_enrollment_once(store, _config(), today=DAY, now=MOMENT)
    assert failed.failed == ("acme/second",) and failed.marked == (), "a failed write leaves its features unmarked"
    assert _scope().enrollment(await store.snapshot()).settled("slop_meter") is False, (
        "until the import has run, what the meter does not read is not settled, so it removes nothing"
    )

    # Meanwhile an operator clears acme/before's layer and keeps acme/second out of the scout, and after the
    # moment a repository with owners is registered and a job runs on it.
    async with store.edit(actor="ops@example.com", source="person", scope=_scope_of("acme/before")) as draft:
        draft.replace("", {}, draft.schema_version)
    async with store.edit(actor="ops@example.com", source="person", scope=_scope_of("acme/second")) as draft:
        draft.replace("background:\n  scout: false\n", {"background": {"scout": False}}, draft.schema_version)
    await _register(db, "acme/after", at=MOMENT + timedelta(hours=1))
    await _job(db, "acme/after", at=MOMENT + timedelta(hours=2))
    await db.execute("INSERT INTO repo_preferences (repo, owners) VALUES ('acme/after', '[\"octo\"]'::jsonb)")

    retried = await module.import_enrollment_once(store, _config(), today=DAY, now=MOMENT + timedelta(days=1))
    assert retried.marked == ("scout", "slop_meter")
    registered = await _registered(db)
    assert await _read(store, "slop_meter", registered) == ["acme/second"], (
        "the retry writes only what is still missing, by the old rule as it stood at the first start: "
        "acme/before, written once and cleared since, is not written again, and acme/after, registered later "
        "with owners and a job, is not enrolled"
    )
    assert await _read(store, "scout", registered) == [], "and the operator's false for acme/second stands"


async def test_a_registered_name_no_layer_can_be_stored_under_holds_the_import(
    db_with_migrations: DatabasePool,
) -> None:
    module = _enrollment_import()
    db = db_with_migrations
    store = SettingsStore(db)
    await _register(db, "acme/fine", at=MOMENT - timedelta(days=30))
    await _register(db, ".dot/repo", at=MOMENT - timedelta(days=30))
    for repo in ("acme/fine", ".dot/repo"):
        await _job(db, repo, at=MOMENT - timedelta(days=1))
    with capture_logs() as logs:
        result = await module.import_enrollment_once(store, _config(), today=DAY, now=MOMENT)
    assert result.failed == (".dot/repo",) and result.marked == (), (
        "a registered name no settings layer can be stored under is a failed write: neither feature is marked, so "
        "the meter removes nothing until it is renamed or unregistered"
    )
    assert any(
        e["event"] == "background_enrollment_name_unaddressable"
        and e["log_level"] == "error"
        and e["repo"] == ".dot/repo"
        for e in logs
    ), "and it is logged at error level, naming it"
    assert "acme/fine" in result.written, "every other repository is still written"
    await db.execute("DELETE FROM repos WHERE repo = '.dot/repo'")
    retried = await module.import_enrollment_once(store, _config(), today=DAY, now=MOMENT)
    assert retried.marked == ("scout", "slop_meter"), "once it is unregistered, the next start finishes the import"


def _preview_sql() -> str:
    """The query docs/ops/background-data.md gives an operator to run before the upgrade, with its two lists
    as parameters."""
    from pathlib import Path

    doc = (Path(__file__).parents[3] / "docs" / "ops" / "background-data.md").read_text()
    start, end = "<!-- prune-preview:start -->", "<!-- prune-preview:end -->"
    assert start in doc and end in doc, "docs/ops/background-data.md gives the query that lists what will be removed"
    block = doc.split(start, 1)[1].split(end, 1)[0]
    sql = block.split("```sql", 1)[1].split("```", 1)[0]
    sql = sql.replace("ARRAY[]::text[] AS include", "$1::text[] AS include")
    return sql.replace("ARRAY[]::text[] AS exclude", "$2::text[] AS exclude")


async def test_the_preview_query_in_the_docs_lists_exactly_what_the_first_tick_finds_out_of_scope(
    db_with_migrations: DatabasePool,
) -> None:
    module = _enrollment_import()
    db = db_with_migrations
    days_ago = datetime.now(UTC) - timedelta(days=400)
    for repo, active in (
        ("acme/jobbed", True),
        ("acme/aged", True),
        ("acme/owned", True),
        ("acme/watched", True),
        ("acme/inactive", False),
        ("acme/excluded", True),
        ("acme/listed", True),
        ("acme/listed-inactive", False),
        ("acme/quiet", True),
    ):
        await _register(db, repo, at=days_ago, active=active)
    now = datetime.now(UTC)
    for n, (repo, days) in enumerate(
        (("Acme/Jobbed", 10), ("acme/aged", 100), ("acme/inactive", 1), ("acme/excluded", 1), ("ghost/unreg", 1))
    ):
        await db.execute(
            "INSERT INTO jobs (job_id, task, repo, created_at) VALUES ($1, 't', $2, $3)",
            f"job-{n}",
            repo,
            now - timedelta(days=days),
        )
    await db.execute("INSERT INTO repo_preferences (repo, owners) VALUES ('acme/owned', '[\"octo\"]'::jsonb)")
    await db.execute("INSERT INTO watcher_stacks (repo, enabled, logql) VALUES ('Acme/Watched', TRUE, 'q')")
    held = [
        "Acme/Jobbed",
        "acme/aged",
        "acme/inactive",
        "acme/excluded",
        "acme/listed",
        "acme/listed-inactive",
        "ghost/unreg",
        "acme/owned",
        "ACME/WATCHED",
        "acme/quiet",
    ]
    for repo in held:
        await db.execute("INSERT INTO slop_meter_repos (repo) VALUES ($1)", repo)
    include, exclude = ["acme/listed", "acme/listed-inactive", "ghost/missing"], ["acme/excluded"]

    previewed = [str(r["repo"]) for r in await db.fetch(_preview_sql(), include, exclude)]

    store = SettingsStore(db)
    config = _config(SLOP_METER_REPOS=", ".join(include), SLOP_METER_EXCLUDE=", ".join(exclude))
    result = await module.import_enrollment_once(store, config, today=DAY)
    assert result.marked == ("scout", "slop_meter")
    every = [str(r["repo"]) for r in await db.fetch("SELECT repo FROM repos ORDER BY repo")]
    keep = {name.lower() for name in _scope().enrollment(await store.snapshot()).scope("slop_meter", every)}
    out_of_scope = sorted({name.lower() for name in held} - keep)
    assert previewed == out_of_scope, (
        "the query the docs give lists exactly the repositories the meter's first tick finds out of scope, and so "
        f"removes prune_after_days later: {previewed} != {out_of_scope}"
    )
    assert out_of_scope == ["acme/aged", "acme/excluded", "acme/quiet", "ghost/unreg"], (
        "the inactive repositories the meter keeps rows for are kept; what dropped out of the old set, what "
        f"SLOP_METER_EXCLUDE names and what is not registered are not: {out_of_scope}"
    )


async def test_a_fresh_install_counts_nothing_registered_after_its_process_began_however_late_it_asks(
    db_with_migrations: DatabasePool,
) -> None:
    """The moment is when the process began (``SettingsStore.started``, read before the migrations), not when
    the import first manages to ask: a first ask that fails, then a repository registered and run on, then the
    retry, still enroll nothing on a new deployment."""
    module = _enrollment_import()
    db = db_with_migrations
    started = await db.fetchval("SELECT now()")
    store = SettingsStore(db, started=started)
    real = module._first_asked
    calls = [0]

    async def first_ask_fails(*args: Any, **kwargs: Any) -> Any:
        calls[0] += 1
        if calls[0] == 1:
            raise ConnectionError("the database went away")
        return await real(*args, **kwargs)

    with patch.object(module, "_first_asked", first_ask_fails):
        with pytest.raises(ConnectionError):
            await module.import_enrollment_once(store, _config(), today=DAY)
        await db.execute("INSERT INTO repos (repo, forge) VALUES ('acme/early-bird', 'github')")
        await db.execute("INSERT INTO jobs (job_id, task, repo) VALUES ('j-early', 't', 'acme/early-bird')")
        retried = await module.import_enrollment_once(store, _config(), today=DAY)
    assert retried.marked == ("scout", "slop_meter") and retried.written == {}, (
        "the retry counts only what was registered before the process began: a repository registered, and run "
        f"on, after it is never enrolled by the old rule: {retried.written}"
    )
    ledger = (await store.document())["bootstrapped_keys"]
    assert f"background.slop_meter@{started.isoformat()}" in ledger, (
        f"the moment recorded is the process's start, the one the environment import uses too: {ledger}"
    )


async def test_an_inactive_repository_the_meter_keeps_rows_for_is_enrolled_so_it_loses_nothing(
    db_with_migrations: DatabasePool,
) -> None:
    module = _enrollment_import()
    db = db_with_migrations
    long_ago = MOMENT - timedelta(days=400)
    await _register(db, "acme/hidden", at=long_ago, active=False)
    await _register(db, "acme/hidden-empty", at=long_ago, active=False)
    await _register(db, "acme/hidden-out", at=long_ago, active=False)
    await _register(db, "acme/live", at=long_ago)
    await _job(db, "acme/live", at=MOMENT - timedelta(days=1))
    for repo in ("ACME/Hidden", "acme/hidden-out", "acme/live"):
        await db.execute("INSERT INTO slop_meter_repos (repo) VALUES ($1)", repo)
    store = SettingsStore(db)
    result = await module.import_enrollment_once(
        store, _config(SLOP_METER_EXCLUDE="acme/hidden-out"), today=DAY, now=MOMENT
    )
    assert result.written.get("acme/hidden") == {"slop_meter": True}, (
        "an inactive repository the meter keeps rows for is enrolled in the meter alone: it is not read while "
        f"inactive, and keeps what was read, as it did before: {result.written}"
    )
    assert "acme/hidden-empty" not in result.written, "one the meter keeps nothing of has nothing to keep"
    assert result.written.get("acme/hidden-out") == {"slop_meter": False}, "SLOP_METER_EXCLUDE still wins"
    hidden = await store.document(_scope_of("acme/hidden"))
    assert "inactive, so not read, and keeping what was read" in hidden["yaml_text"], hidden["yaml_text"]
    every = [str(r["repo"]) for r in await db.fetch("SELECT repo FROM repos ORDER BY repo")]
    assert "acme/hidden" in _scope().enrollment(await store.snapshot()).scope("slop_meter", every), (
        "so it is in the meter's keep set, and nothing of it is pending removal"
    )


async def test_an_import_whose_first_ask_failed_is_retried_in_the_same_run_with_the_process_start(
    db_with_migrations: DatabasePool,
) -> None:
    """The review's `acme/early-bird` probe: the first start of a new deployment fails before it records its
    moment; while it serves, a repository is registered and run on. The same run's retry must not enroll it."""
    module = _enrollment_import()
    settle = getattr(module, "settle_enrollment", None)
    assert settle is not None, "an enrollment import that failed is tried again in the same run"
    db = db_with_migrations
    started = await db.fetchval("SELECT now()")
    store = SettingsStore(db, started=started)

    async def fails(*args: Any, **kwargs: Any) -> Any:
        raise ConnectionError("the database went away")

    with patch.object(module, "_first_asked", fails):
        with pytest.raises(ConnectionError):
            await module.import_enrollment_once(store, _config(), today=DAY)
    await db.execute("INSERT INTO repos (repo, forge) VALUES ('acme/early-bird', 'github')")
    await db.execute("INSERT INTO jobs (job_id, task, repo) VALUES ('j-early', 't', 'acme/early-bird')")
    waited: list[float] = []

    async def no_wait(seconds: float) -> None:
        waited.append(seconds)

    result = await settle(store, _config(), None, sleep=no_wait)
    assert waited == [60.0], f"it waits before it tries again, as the environment import's retry does: {waited}"
    assert result.marked == ("scout", "slop_meter") and result.written == {}, (
        "the retry records the moment this process began, so a repository registered while it waited is not one "
        f"the old rule read: {result.written}"
    )


async def test_a_name_that_holds_the_import_is_reported_once_and_keeps_the_gauge_up_until_it_goes(
    db_with_migrations: DatabasePool,
) -> None:
    """The review's settle probe: the retries go on while an unaddressable name holds the import, quietly. One
    error log and one audit row a process, debug after that, and `embry0_background_enrollment_blocked` at 1
    until the name is unregistered."""
    from prometheus_client import CollectorRegistry

    from embry0.metrics.loop import LoopMetrics, sample

    module = _enrollment_import()
    settle = getattr(module, "settle_enrollment", None)
    assert settle is not None, "an enrollment import that failed is tried again in the same run"
    db = db_with_migrations
    store = SettingsStore(db, started=await db.fetchval("SELECT now()"))
    for repo in ("acme/a", ".dot/repo"):
        await _register(db, repo, at=MOMENT - timedelta(days=30))
        await _job(db, repo, at=MOMENT - timedelta(days=1))
    metrics = LoopMetrics(CollectorRegistry())
    assert sample(metrics.registry, "embry0_background_enrollment_blocked") == 0.0, (
        "the gauge is seeded 0 when the process starts, so the series exists before the first block"
    )
    reported: set[str] = set()

    class Stop(Exception):
        pass

    tries = [0]

    async def sleep(seconds: float) -> None:
        tries[0] += 1
        if tries[0] > 6:
            raise Stop

    with capture_logs() as logs:
        first = await module.import_enrollment_once(store, _config(), now=MOMENT, reported=reported, metrics=metrics)
        assert first.unaddressable == (".dot/repo",)
        assert sample(metrics.registry, "embry0_background_enrollment_blocked") == 1.0, "1 while the name holds it"
        with pytest.raises(Stop):
            await settle(store, _config(), first, sleep=sleep, reported=reported, metrics=metrics)
    levels = [e["log_level"] for e in logs if e["event"] == "background_enrollment_name_unaddressable"]
    assert levels == ["error"] + ["debug"] * 6, (
        f"the first import logs it at error level, and each of the six retries at debug level: {levels}"
    )
    audited = await db.fetch("SELECT details FROM audit_log WHERE action = 'background_enrollment.blocked'")
    assert [r["details"]["repo"] for r in audited] == [".dot/repo"], "one audit row, not one a retry"
    assert sample(metrics.registry, "embry0_background_enrollment_blocked") == 1.0, "still 1 while it holds"
    await db.execute("DELETE FROM repos WHERE repo = '.dot/repo'")
    elsewhere = await module.import_enrollment_once(store, _config())
    assert elsewhere.marked == ("scout", "slop_meter"), "another orchestrator's try finishes the import"
    done = await settle(store, _config(), first, sleep=_no_wait, reported=reported, metrics=metrics)
    assert done.marked == () and done.unaddressable == (), "so this process's next try finds nothing to do"
    assert sample(metrics.registry, "embry0_background_enrollment_blocked") == 0.0, (
        "and the gauge goes back to 0 then too, not only when this process finishes the import"
    )


async def _no_wait(seconds: float) -> None:
    return None
