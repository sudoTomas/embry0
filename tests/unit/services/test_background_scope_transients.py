"""A state that flips a repository out of the meter's scope for a while costs nothing if it is put right in time.

The review's probes (#165), on Postgres with the real registry, settings
store, record of departures and meter store, and a real mirror root under
``tmp_path``: a ``REPO_RENAMES`` mistake that makes a renamed repository's
layer unreadable, an organization switched off by mistake, the deployment's
switch flipped, and a repository made inactive. Each used to remove the
repository's mirror and rows on the next tick; now each only records that it
left, the removal waits ``background.prune_after_days``, and putting the state
right cancels it.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from embry0.config import Embry0Config
from embry0.services.slop_meter.history import Commit
from embry0.services.slop_meter.loop import SlopMeterLoop, SlopMeterSettings
from embry0.services.slop_meter.mirror import GitMirror
from embry0.settings.scope import Scope
from embry0.settings.store import SettingsStore
from embry0.storage import repo_renames
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.repos import ReposRepository
from embry0.storage.repositories.slop_meter import SlopMeterRepository

pytestmark = pytest.mark.requires_postgres

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
RENAMES = {"acme/old-name": "acme/new-name"}


def _modules() -> tuple[Any, Any, Any]:
    try:
        return (
            importlib.import_module("embry0.services.background_enrollment"),
            importlib.import_module("embry0.services.background_scope"),
            importlib.import_module("embry0.storage.repositories.background_scope"),
        )
    except ModuleNotFoundError as exc:
        pytest.fail(f"{exc.name} does not exist: a repository out of scope is removed on the next tick")


class Audit:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, *, action: str, details: dict[str, Any]) -> None:
        self.rows.append((action, details))


@pytest.fixture
def renames() -> Iterator[dict[str, str]]:
    """``REPO_RENAMES`` for the test; a test changes it in place, as a start with another value would."""
    installed: dict[str, str] = dict(RENAMES)
    with patch.object(repo_renames, "REPO_RENAMES", installed):
        yield installed


async def _deployment(db: DatabasePool, tmp_path: Path) -> tuple[Any, SettingsStore, list[datetime], Audit]:
    """An existing deployment after the upgrade's import, with what the meter kept of four repositories."""
    enrollment_import, scope, exits = _modules()
    for repo in ("acme/old-name", "acme/a", "acme/via-org", "acme/hidden"):
        await db.execute(
            "INSERT INTO repos (repo, forge, created_at) VALUES ($1, 'github', now() - interval '400 days')", repo
        )
    for repo in ("acme/old-name", "acme/a", "acme/hidden"):
        await db.execute("INSERT INTO jobs (job_id, task, repo) VALUES ($1, 't', $2)", f"j-{repo}", repo)
    store = SettingsStore(db)
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    result = await enrollment_import.import_enrollment_once(store, config)
    assert result.marked == ("scout", "slop_meter")
    meter = SlopMeterRepository(db)
    mirror = GitMirror(tmp_path / "slop-meter")
    for repo in ("acme/old-name", "acme/a", "acme/via-org", "acme/hidden"):
        await meter.set_state(repo, mirrored_at=NOW)
        await meter.add_commits(repo, 0, [Commit("a1", NOW, "Ana", "ana@example.com", "w", (), ()).to_row()])
        (mirror.path(repo) / "objects").mkdir(parents=True)
    clock = [NOW]
    audit = Audit()

    async def nothing(repo: str) -> None:
        return None

    async def no_bots(repo: str) -> tuple[str, ...]:
        return ()

    loop = SlopMeterLoop(  # type: ignore[call-arg]
        repos=scope.registry_reader(ReposRepository(db)),
        read=importlib.import_module("embry0.services.slop_meter.loop").meter_reader(store),
        store=meter,
        mirror=mirror,
        exits=exits.ScopeExitsRepository(db),
        audit=audit,
        token_for=nothing,
        client_for=nothing,
        bots=no_bots,
        calls=None,  # type: ignore[arg-type]
        settings=SlopMeterSettings(),
        clock=lambda: clock[0],
    )
    return loop, store, clock, audit


async def _left(db: DatabasePool) -> list[str]:
    return [str(r["repo"]) for r in await db.fetch("SELECT repo FROM background_scope_exits ORDER BY repo")]


async def _held(db: DatabasePool) -> list[str]:
    return [str(r["repo"]) for r in await db.fetch("SELECT repo FROM slop_meter_repos ORDER BY repo")]


async def _set(store: SettingsStore, text: str, data: dict[str, Any], scope: Scope | None = None) -> None:
    async with store.edit(actor="ops@example.com", source="person", reason="probe", scope=scope) as draft:
        draft.replace(text, data, draft.schema_version)


async def test_a_repo_renames_mistake_waits_and_putting_it_right_cancels_the_removal(
    db_with_migrations: DatabasePool, tmp_path: Path, renames: dict[str, str]
) -> None:
    db = db_with_migrations
    loop, _store, clock, _audit = await _deployment(db, tmp_path)
    await loop.tick()
    assert "acme/old-name" not in await _left(db), "after the upgrade everything the meter read is in scope"
    renames.clear()  # a mistyped REPO_RENAMES at the next start installs nothing
    clock[0] = NOW + timedelta(days=1)
    await loop.tick()
    assert "acme/old-name" in await _left(db), (
        "its layer is stored under the slug the rename maps it to, so without the rename it reads false: it left"
    )
    assert "acme/old-name" in await _held(db), "and nothing of it is removed"
    renames.update(RENAMES)
    clock[0] = NOW + timedelta(days=3)
    await loop.tick()
    assert "acme/old-name" not in await _left(db) and "acme/old-name" in await _held(db), (
        "the rename put right within the week: the removal is cancelled and everything is kept"
    )


async def test_an_organization_switched_off_by_mistake_waits_and_switching_it_back_cancels(
    db_with_migrations: DatabasePool, tmp_path: Path, renames: dict[str, str]
) -> None:
    db = db_with_migrations
    loop, store, clock, _audit = await _deployment(db, tmp_path)
    await _set(
        store, "background:\n  slop_meter: true\n", {"background": {"slop_meter": True}}, Scope.organization("acme")
    )
    await loop.tick()
    assert await _left(db) == []
    await _set(
        store, "background:\n  slop_meter: false\n", {"background": {"slop_meter": False}}, Scope.organization("acme")
    )
    clock[0] = NOW + timedelta(days=6)
    await loop.tick()
    assert await _left(db) == ["acme/via-org"], (
        "acme/via-org was enrolled through its organization only, so the switch takes it out; the repositories the "
        "upgrade enrolled in their own layers stay, since a repository's layer outranks its organization's"
    )
    await _set(
        store, "background:\n  slop_meter: true\n", {"background": {"slop_meter": True}}, Scope.organization("acme")
    )
    clock[0] = NOW + timedelta(days=12)
    await loop.tick()
    assert await _left(db) == [] and "acme/via-org" in await _held(db), "switched back in time: nothing is lost"


async def test_the_deployment_switched_off_does_not_reach_what_the_upgrade_enrolled_and_inactive_loses_nothing(
    db_with_migrations: DatabasePool, tmp_path: Path, renames: dict[str, str]
) -> None:
    db = db_with_migrations
    loop, store, clock, audit = await _deployment(db, tmp_path)
    await _set(store, "background:\n  slop_meter: false\n", {"background": {"slop_meter": False}})
    await db.execute("UPDATE repos SET active = false WHERE repo = 'acme/hidden'")
    await loop.tick()
    clock[0] = NOW + timedelta(days=8)
    await loop.tick()
    assert await _held(db) == ["acme/a", "acme/hidden", "acme/old-name"], (
        "the deployment's false reaches only what no layer of its own enrolls: acme/via-org, enrolled by nothing "
        "since the upgrade, goes a week after it was first found out; acme/hidden, made inactive, is not read and "
        "loses nothing, since its own layer still enrolls it"
    )
    assert await _left(db) == [], "and nothing else is waiting"
    assert [action for action, _ in audit.rows] == ["slop_meter.pruned"]


async def test_two_registry_spellings_of_one_repository_are_read_once_and_never_again(
    db_with_migrations: DatabasePool, tmp_path: Path, renames: dict[str, str]
) -> None:
    """The review's three-tick probe: with `Acme/Widgets` and `acme/widgets` both registered, the meter read one
    spelling and moved its rows to the other at the next tick, so `commits_read` reset every tick and the
    history (and its outliers' judgments) was read again."""
    _enrollment_import, scope, exits = _modules()
    db = db_with_migrations
    for repo in ("Acme/Widgets", "acme/widgets", "acme/other"):
        await db.execute(
            "INSERT INTO repos (repo, forge, created_at) VALUES ($1, 'github', now() - interval '400 days')", repo
        )
    store = SettingsStore(db)
    await _set(
        store, "background:\n  slop_meter: true\n", {"background": {"slop_meter": True}}, Scope.organization("acme")
    )
    async with store.edit(actor="ops@example.com", source="person", reason="settled") as draft:
        draft.mark_bootstrapped(["background.scout", "background.slop_meter"])
    meter = SlopMeterRepository(db)
    loop_mod = importlib.import_module("embry0.services.slop_meter.loop")

    async def nothing(repo: str) -> None:
        return None

    loop = SlopMeterLoop(  # type: ignore[call-arg]
        repos=scope.registry_reader(ReposRepository(db)),
        read=loop_mod.meter_reader(store),
        store=meter,
        mirror=GitMirror(tmp_path / "slop-meter"),
        exits=exits.ScopeExitsRepository(db),
        audit=Audit(),
        token_for=nothing,
        client_for=nothing,
        bots=nothing,
        calls=None,  # type: ignore[arg-type]
        settings=SlopMeterSettings(),
    )
    read_counts = []
    for _tick in range(3):
        read = await loop_mod.meter_reader(store)()
        reads = read.enrollment.scope("slop_meter", (await scope.registry_reader(ReposRepository(db))()).active)
        spelled = next(name for name in reads if name.lower() == "acme/widgets")
        state = await meter.state(spelled)
        await meter.set_state(spelled, commits_read=int(state.get("commits_read") or 0) + 10)
        await loop.prune_only()  # the next tick starts with the respelling
        read_counts.append(int((await meter.state(spelled)).get("commits_read") or 0))
    assert read_counts == [10, 20, 30], (
        "each tick goes on from where the last stopped: the spelling the meter reads is the one its rows stay "
        f"under, so nothing is read again: {read_counts}"
    )
    assert [r["repo"] for r in await db.fetch("SELECT repo FROM slop_meter_repos")] == ["Acme/Widgets"], (
        "one row, under the one spelling every reader takes"
    )


async def test_a_deactivated_duplicate_spelling_never_takes_the_active_repository_s_place(
    db_with_migrations: DatabasePool, tmp_path: Path, renames: dict[str, str]
) -> None:
    """The review's probe: `acme/widgets` is the active registration, with its preferences (owners, the commit
    author the meter counts as a bot); a later `Acme/Widgets` row was deactivated. Every feature must read the
    active spelling, or it reads none of what is kept under it."""
    _enrollment_import, scope, _exits = _modules()
    db = db_with_migrations
    await db.execute(
        "INSERT INTO repos (repo, forge, active, created_at) VALUES "
        "('acme/widgets', 'github', TRUE, now() - interval '400 days'), "
        "('Acme/Widgets', 'github', FALSE, now() - interval '10 days')"
    )
    await db.execute(
        "INSERT INTO repo_preferences (repo, owners, git_author_email) "
        "VALUES ('acme/widgets', '[\"octo\"]'::jsonb, 'bot@example.com')"
    )
    registry = ReposRepository(db)
    meter_sees = await scope.registry_reader(registry)()
    others_see = await scope.registered_reader(registry)()
    assert meter_sees.active == ("acme/widgets",) and meter_sees.inactive == () and others_see == ["acme/widgets"], (
        f"the meter, the scout and the leads all read the active registration: {meter_sees}, {others_see}"
    )
    from embry0.services.slop_meter.loop import repo_bots
    from embry0.storage.repositories.organizations import OrganizationsRepository
    from embry0.storage.repositories.repo_preferences import RepoPreferencesRepository

    prefs = RepoPreferencesRepository(db)
    assert await prefs.get(meter_sees.active[0]) is not None, "its preferences are found under that spelling"
    bots = await repo_bots(meter_sees.active[0], prefs=prefs, orgs=OrganizationsRepository(db), credentials=None)
    assert bots == ["bot@example.com"], f"so the meter counts the repository's commit author as a bot: {bots}"
