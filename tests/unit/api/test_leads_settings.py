"""The leads policy lives in the settings document (#168), end to end on a real database.

- A PUT of the document reaches the next look of a running leads loop, with no restart: turning
  leads on and off, the finders, and an organization's own layer.
- The readiness thresholds come from the section: ``ask_min`` holds a ticket out of chat in the
  proposals feed and marks it on its decision card, per repository.
- A bad value is a 422 naming its key, never repeating it, and nothing is stored.
- The first boot imports a non-default ``LEADS_*`` value once; a second boot does not.
- Leads are off by default now. A deployment whose finders had stored leads, with ``LEADS_ENABLED``
  unset, keeps them on: the first boot writes ``enabled: true``, once. A new one stays off.
- The real lifespan builds the leads loop and the brief writer with readers of the document.

The section is taken from the application's catalog by name, and each test
first writes or reads it, so each fails with its own message on a tree that
has no leads section.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

from embry0.api.app import create_app, lifespan
from embry0.api.deps import bust_api_key_cache
from embry0.config import Embry0Config
from embry0.services.leads import Lead
from embry0.services.leads.loop import LeadsLoop
from embry0.settings.bootstrap import import_env_once, log_ignored_env
from embry0.settings.document import load_yaml
from embry0.settings.scope import Scope
from embry0.settings.store import SettingsStore, scoped_reader
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token
from embry0.storage.repositories.jobs import JobsRepository
from embry0.storage.repositories.leads import LeadsRepository
from embry0.storage.repositories.repos import ReposRepository
from tests.fixtures.leads import leads_section
from tests.unit.api.v1.test_proposals_api import FakeLeads, FakeOrgs, FakeRepo
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401 - a fixture
from tests.unit.services.leads.fakes import FakeForge

pytestmark = pytest.mark.requires_postgres

URL = "/api/v1/config/settings"
ADMIN = "emb_" + "a" * 43
DAY = date(2026, 10, 30)
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
HEAD = "e" * 40


@pytest.fixture
async def app(db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    bust_api_key_cache()
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, api_key="master-key")  # type: ignore[call-arg]
    app = create_app(config)
    app.state.db = db_with_migrations
    app.state.settings = SettingsStore(db_with_migrations)
    keys = MagicMock()
    row = ApiKeyRow(
        id="key-admin0000001",
        user_id="usr-admin",
        name="admin-key",
        prefix="emb_xxxxxxxx",
        scope="admin",
        created_by="tester",
        created_at=None,
        expires_at=None,
        last_used_at=None,
        revoked_at=None,
        allowed_repos=None,
    )
    keys.get_by_hash = AsyncMock(side_effect=lambda h: row if h == hash_token(ADMIN) else None)
    keys.touch_last_used = AsyncMock()
    app.state.api_keys_repo = keys
    user = MagicMock(disabled=False, email="alex@example.com")
    app.state.users_repo = MagicMock(get=AsyncMock(side_effect=lambda uid: user if uid == "usr-admin" else None))
    # The proposals feed: two backlog tickets of acme/api, ready 70 and 40 (tests.unit.api.v1.test_proposals_api).
    app.state.proposals_repo = FakeRepo()
    app.state.leads_repo = FakeLeads()
    app.state.organizations_repo = FakeOrgs()
    app.state.proposal_briefs_repo = None

    async def no_owners(request: Any, repos: Any) -> dict[str, Any]:
        return {}

    monkeypatch.setattr("embry0.api.v1.proposals._codeowners", no_owners)
    monkeypatch.setattr("embry0.api.v1.proposals._areas", no_owners)
    yield app
    bust_api_key_cache()


@pytest.fixture
async def client(app: Any) -> AsyncIterator[AsyncClient]:
    headers = {"Authorization": f"Bearer {ADMIN}", "X-Requested-With": "XMLHttpRequest"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=headers) as c:
        yield c


async def _put(client: AsyncClient, text: str) -> Any:
    return await client.put(URL, content=text, headers={"Content-Type": "application/yaml"})


def _forge(*repos: str) -> FakeForge:
    """Each repository's default branch has one check failing."""
    answers: dict[str, Any] = {}
    for repo in repos:
        answers |= {
            f"/repos/{repo}": {"default_branch": "main"},
            f"/repos/{repo}/commits/main": {"sha": HEAD},
            f"/repos/{repo}/commits/{HEAD}/check-runs": {
                "total_count": 1,
                "check_runs": [{"name": "build", "status": "completed", "conclusion": "failure"}],
            },
            f"/repos/{repo}/commits/{HEAD}/status": {"statuses": []},
        }
    return FakeForge(answers)


def _loop(store: SettingsStore, db: DatabasePool, clock: list[datetime], *repos: str) -> LeadsLoop:
    forge = _forge(*repos)

    async def targets() -> list[str]:
        return list(repos)

    async def client_for(repo: str) -> Any:
        return forge

    async def nothing() -> Any:
        return None

    async def tracked(tracker: str, repo: str, key: str) -> bool:
        return False

    async def triggers() -> list[str]:
        return []

    async def no_area(repo: str, client: Any, lead: Lead) -> str | None:
        return None

    return LeadsLoop(
        settings=scoped_reader(store, leads_section()),
        leads=LeadsRepository(db),
        targets=targets,
        client_for=client_for,
        linear=nothing,
        tracked=tracked,
        trigger_labels=triggers,
        area_of=no_area,
        clock=lambda: clock[0],
    )


async def test_a_put_reaches_the_next_look_of_the_running_loop(
    app: Any, client: AsyncClient, db_with_migrations: DatabasePool
) -> None:
    leads_section()
    clock = [NOW]
    loop = _loop(app.state.settings, db_with_migrations, clock, "acme/web", "globex/api")
    assert await loop.scheduled() is None, "nothing written: leads are off by default, and the look runs no pass"

    on = await _put(client, "leads:\n  enabled: true\n  finders: [ci]\n")
    assert on.status_code == 200, f"the settings document takes leads.enabled and leads.finders: {on.text}"
    first = await loop.scheduled()
    assert first == {r: {"ci": {"found": 1, "new": 1}} for r in ("acme/web", "globex/api")}, (
        f"the same loop's next look runs a pass with the finders the PUT named: {first}"
    )

    text = "leads:\n  finders: []\n"
    await app.state.settings.save(text, load_yaml(text), actor="alex@example.com", scope=Scope.organization("acme"))
    clock[0] += timedelta(hours=6)
    second = await loop.scheduled()
    assert second == {"acme/web": {}, "globex/api": {"ci": {"found": 1}}}, (
        f"acme's layer runs no finder on its repository; globex's still runs the deployment's: {second}"
    )

    off = await _put(client, "leads:\n  enabled: false\n  finders: [ci]\n")
    assert off.status_code == 200, off.text
    clock[0] += timedelta(days=1)
    assert await loop.scheduled() is None, "turned off by a PUT: the next look runs no pass"


async def test_ask_min_holds_a_ticket_out_of_chat_and_marks_its_card_per_repository(
    app: Any, client: AsyncClient
) -> None:
    """acme/api's tickets are ready 70 and 40; the deployment's ask_min is 60 until a PUT changes it."""
    leads_section()

    async def asked() -> list[str]:
        resp = await client.get("/api/v1/proposals", params={"kind": "ticket"})
        assert resp.status_code == 200, resp.text
        return sorted(p["id"] for p in resp.json()["proposals"])

    async def tones() -> dict[str, str]:
        resp = await client.get("/api/v1/decisions", params={"stage": "offered"})
        assert resp.status_code == 200, resp.text
        return {
            d["id"]: next(f["tone"] for f in d["facts"] if f["label"] == "Readiness")
            for d in resp.json()["decisions"]
            if d["kind"] == "ticket"
        }

    assert await asked() == ["ticket:ready"], "by default (ask_min 60) the ticket ready 40 is held out of chat"
    resp = await _put(client, "leads:\n  ask_min: 80\n")
    assert resp.status_code == 200, f"the settings document takes leads.ask_min: {resp.text}"
    assert await asked() == [], "ask_min 80: both tickets are held, read on the next request with no restart"
    assert await tones() == {"offer:ticket:ready": "warn", "offer:ticket:thin": "warn"}, (
        "each decision card marks a readiness below ask_min, not below a fixed 60"
    )

    text = "leads:\n  ask_min: 30\n"
    await app.state.settings.save(text, load_yaml(text), actor="alex@example.com", scope=Scope.organization("acme"))
    assert await asked() == ["ticket:ready", "ticket:thin"], "acme's layer asks from 30: both are asked"
    assert await tones() == {"offer:ticket:ready": "", "offer:ticket:thin": ""}, "and neither card is marked"


async def test_each_bad_leads_value_is_a_422_naming_its_key_and_nothing_is_stored(
    app: Any, client: AsyncClient
) -> None:
    leads_section()
    text = "leads:\n  finders: [ci, omens]\n  interval_hours: 0.01\n  ask_min: 4242\n"
    resp = await _put(client, text)
    assert resp.status_code == 422, f"a bad leads value is refused when it is written: {resp.text}"
    assert sorted(tuple(p["loc"][:2]) for p in resp.json()["detail"]) == [
        ("leads", "ask_min"),
        ("leads", "finders"),
        ("leads", "interval_hours"),
    ], f"each bad value is refused by its own key: {resp.text}"
    for value in ("omens", "0.01", "4242"):
        assert value not in resp.text, f"the 422 does not repeat {value!r}"
    stored = (await client.get(URL)).json()
    assert (stored["yaml_text"], stored["data"]) == ("", {}), "a refused document stores nothing"
    assert (await app.state.settings.snapshot()).get(leads_section()).ask_min == 60, "so no reader ever reads it"


def _config(**env: str) -> Embry0Config:
    with patch.dict(os.environ, env, clear=True):
        return Embry0Config(_env_file=None)  # type: ignore[call-arg]


def _lead(kind: str, key: str) -> Lead:
    return Lead(
        kind=kind,
        repo="acme/web",
        source_key=key,
        title="A lead",
        summary="A lead.",
        details="",
        action="Fix it",
    )


async def test_the_first_boot_imports_non_default_leads_values_once_and_a_second_does_not(
    db_with_migrations: DatabasePool,
) -> None:
    section = leads_section()
    store = SettingsStore(db_with_migrations)
    config = _config(
        LEADS_ENABLED="true",
        LEADS_FINDERS="ci, nonsense",
        LEADS_INTERVAL_HOURS="0.1",
        LEADS_BACKLOG_INTERVAL_HOURS="24",
        LEADS_MAX_NEW_PER_REPO_PER_DAY="5000",
        LEADS_TICKET_MIN_SCORE="50",
        PROPOSALS_TICKET_MIN_READINESS="70",
    )

    with capture_logs() as logs:
        first = await import_env_once(store, config, today=DAY)
    assert first.imported.get("leads") == {
        "enabled": True,
        "finders": ["ci"],
        "interval_hours": 0.25,
        "offer_min": 50,
        "ask_min": 70,
    }, (
        "the values that differ from the defaults are imported as the loop read them: an unknown finder dropped, "
        f"an interval below a quarter hour ran every quarter hour; 24 is the default: {first.imported}"
    )
    refused = [(e["variable"], e["key"]) for e in logs if e["event"] == "settings_bootstrap_value_refused"]
    assert refused == [("LEADS_MAX_NEW_PER_REPO_PER_DAY", "leads.max_new_per_repo_per_day")], (
        "a value past the section's bound is reported by name and not imported"
    )
    dropped = [
        (e["variable"], e["dropped"], e["log_level"]) for e in logs if e["event"] == "settings_env_value_dropped"
    ]
    assert dropped == [("LEADS_FINDERS", ["nonsense"], "warning")], (
        f"a name in LEADS_FINDERS that is no finder is named in a warning, not dropped silently: {dropped}"
    )
    imported = (await store.snapshot()).get(section)
    assert (imported.offer_min, imported.ask_min, imported.max_new_per_repo_per_day) == (50, 70, 5)

    text = "leads:\n  enabled: false\n"
    await store.save(text, load_yaml(text), actor="alex@example.com")
    again_store = SettingsStore(db_with_migrations)
    with capture_logs() as logs:
        again = await import_env_once(again_store, config, today=DAY)
        await log_ignored_env(again_store, config)
    assert (again.imported, again.revision_id) == ({}, None), "the second boot imports nothing"
    assert (await again_store.document())["data"] == {"leads": {"enabled": False}}, "the person's value stays"
    ignored = {e["variable"] for e in logs if e["event"] == "settings_env_ignored"}
    assert {"LEADS_ENABLED", "LEADS_FINDERS", "PROPOSALS_TICKET_MIN_READINESS"} <= ignored, (
        f"each policy variable still set is logged as ignored: {ignored}"
    )


async def test_a_new_deployment_keeps_leads_off_even_once_it_is_used_later(
    db_with_migrations: DatabasePool,
) -> None:
    """A database just migrated holds no repository, job or lead: what a new deployment's first start sees."""
    section = leads_section()
    store = SettingsStore(db_with_migrations)

    first = await import_env_once(store, _config(), today=DAY)
    assert first.imported == {}, f"a new deployment with LEADS_ENABLED unset keeps nothing: {first.imported}"
    assert (await store.snapshot()).get(section).enabled is False, "a new deployment's leads are off"
    assert "leads.enabled" in (await store.document())["bootstrapped_keys"], "the key is marked: asked once"

    await ReposRepository(db_with_migrations).register("acme/web")
    await LeadsRepository(db_with_migrations).upsert([_lead("ci", "check:build")], now=NOW)
    again = await import_env_once(SettingsStore(db_with_migrations), _config(), today=DAY)
    assert again.imported == {}, "a repository and a lead added after the first boot turn nothing on"
    assert (await store.snapshot()).get(section).enabled is False


async def _a_repository(db: DatabasePool) -> None:
    await ReposRepository(db).register("acme/web")


async def _a_job(db: DatabasePool) -> None:
    await JobsRepository(db).create(repo="acme/web", task="Fix the export")


async def _a_gone_finder_lead(db: DatabasePool) -> None:
    leads = LeadsRepository(db)
    await leads.upsert([_lead("review", "pr:7")], now=NOW - timedelta(days=30))
    await leads.mark_gone("acme/web", "review", set(), now=NOW - timedelta(days=20))


@pytest.mark.parametrize(
    "record",
    [_a_repository, _a_job, _a_gone_finder_lead],
    ids=["a registered repository, no lead", "a job, no repository", "a finder's lead, gone, no repository"],
)
async def test_a_deployment_that_existed_before_keeps_leads_on_once(
    db_with_migrations: DatabasePool, record: Callable[[DatabasePool], Awaitable[None]]
) -> None:
    """Before the section leads ran on every deployment that left LEADS_ENABLED unset, stored or not."""
    section = leads_section()
    await record(db_with_migrations)
    store = SettingsStore(db_with_migrations)

    with capture_logs() as logs:
        first = await import_env_once(store, _config(), today=DAY)
    assert first.imported == {"leads": {"enabled": True}}, (
        f"the deployment existed before and LEADS_ENABLED is unset: it keeps leads on: {first.imported}"
    )
    assert getattr(first, "kept", None) == ("leads.enabled",)
    assert (await store.document())["yaml_text"] == (
        "# Kept on 2026-10-30: LEADS_ENABLED is not set, and the deployment existed before leads became opt-in "
        "(it held a registered repository, a job or a lead a finder stored before this release first started).\n"
        "leads:\n  enabled: true\n"
    )
    assert [e["key"] for e in logs if e["event"] == "settings_default_kept"] == ["leads.enabled"]
    assert (await store.snapshot()).get(section).enabled is True, "leads stay on"

    text = "leads:\n  enabled: false\n"
    await store.save(text, load_yaml(text), actor="alex@example.com")
    again = await import_env_once(SettingsStore(db_with_migrations), _config(), today=DAY)
    assert (again.imported, getattr(again, "kept", None)) == ({}, ()), "the second boot does not turn them on again"
    assert (await store.snapshot()).get(section).enabled is False, "the person's false stays"


@pytest.mark.parametrize(("kind", "key"), [("crack", "ledger:1"), ("weed", "map:1")])
async def test_only_a_finder_s_lead_counts_among_the_leads(
    db_with_migrations: DatabasePool, kind: str, key: str
) -> None:
    """The evidence is a repository, a job or a lead of a finder's kind; a scout's or a map's lead alone is not."""
    section = leads_section()
    await LeadsRepository(db_with_migrations).upsert([_lead(kind, key)], now=NOW)
    store = SettingsStore(db_with_migrations)
    result = await import_env_once(store, _config(), today=DAY)
    assert result.imported == {}, f"a {kind} lead alone is not counted: {result.imported}"
    assert (await store.snapshot()).get(section).enabled is False


async def test_leads_enabled_false_wins_over_the_evidence(db_with_migrations: DatabasePool) -> None:
    section = leads_section()
    await ReposRepository(db_with_migrations).register("acme/web")
    await LeadsRepository(db_with_migrations).upsert([_lead("ci", "check:build")], now=NOW)
    store = SettingsStore(db_with_migrations)
    result = await import_env_once(store, _config(LEADS_ENABLED="false"), today=DAY)
    assert result.imported == {}, "LEADS_ENABLED=false is set: the normal import, and false is the default now"
    assert (await store.snapshot()).get(section).enabled is False, "the operator's off stays off"


async def _import_at(store: SettingsStore, config: Embry0Config, started: Any, **kwargs: Any) -> Any:
    """``import_env_once`` for a process that started at ``started`` (the database's clock)."""
    assert "started" in inspect.signature(import_env_once).parameters, (
        "the import is told when this process started, so it counts only what came before the release"
    )
    return await import_env_once(store, config, started=started, **kwargs)


def _fail_first_check(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Make the first evidence check raise, as a database error at the start would; later ones answer."""
    found = importlib.util.find_spec("embry0.storage.repositories.deployment_history")
    assert found is not None, "the evidence that a deployment existed before is read in the storage layer"
    history = importlib.import_module("embry0.storage.repositories.deployment_history").DeploymentHistory
    real = history.existed_before
    calls: list[int] = []

    async def first_fails(self: Any, *, lead_kinds: Any = ()) -> bool:
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("the database dropped the connection")
        return await real(self, lead_kinds=lead_kinds)

    monkeypatch.setattr(history, "existed_before", first_fails)
    return calls


def _settle() -> Any:
    from embry0.settings import bootstrap

    settle = getattr(bootstrap, "settle_unchecked", None)
    assert settle is not None, "a key whose check failed is asked again in the same run"
    return settle


async def _no_wait(seconds: float) -> None:
    return None


async def test_an_operator_s_value_written_while_a_check_waits_for_its_retry_stands(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deployment existed before, so the retry's check would keep leads on, but the operator turned
    them off while it waited: their false stands, and the key is settled."""
    section = leads_section()
    await ReposRepository(db_with_migrations).register("acme/web")
    _fail_first_check(monkeypatch)
    store = SettingsStore(db_with_migrations)
    first = await import_env_once(store, _config(), today=DAY)
    assert getattr(first, "unchecked", ()), "the start's check failed"
    text = "leads:\n  enabled: false\n"
    await store.save(text, load_yaml(text), actor="alex@example.com")

    settled = await _settle()(store, _config(), first, sleep=_no_wait)

    assert (settled.unchecked, settled.imported) == ((), {}), f"the retry imports nothing over it: {settled}"
    assert (await store.snapshot()).get(section).enabled is False, "the operator's false stands"
    assert "leads.enabled" in (await store.document())["bootstrapped_keys"], "and the key is marked"


async def test_a_repository_registered_while_a_new_deployment_s_check_waits_is_no_evidence(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new deployment: its first check fails, then acme/web is registered, then the retry runs."""
    section = leads_section()
    started = await db_with_migrations.fetchval("SELECT now()")
    _fail_first_check(monkeypatch)
    store = SettingsStore(db_with_migrations)
    first = await _import_at(store, _config(), started, today=DAY)
    assert getattr(first, "unchecked", ()), "the start's check failed"
    await ReposRepository(db_with_migrations).register("acme/web")

    settle = _settle()
    assert "started" in inspect.signature(settle).parameters, "the retry asks about the same start as the import"
    settled = await settle(store, _config(), first, started=started, sleep=_no_wait)

    assert (settled.unchecked, settled.imported) == ((), {}), (
        f"a repository registered after this start is not the deployment's past: {settled.imported}"
    )
    assert (await store.snapshot()).get(section).enabled is False, "a new deployment's leads stay off"


async def test_a_repository_registered_between_two_starts_after_a_failed_import_is_no_evidence(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new deployment's first import fails outright; a repository is registered; the next start imports."""
    section = leads_section()
    first_boot = await db_with_migrations.fetchval("SELECT now()")

    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the first import fails after it began")

    with monkeypatch.context() as patched:
        patched.setattr("embry0.settings.bootstrap._relied_on", broken)
        with pytest.raises(RuntimeError):
            await _import_at(SettingsStore(db_with_migrations), _config(), first_boot, today=DAY)
    await ReposRepository(db_with_migrations).register("acme/web")

    second_boot = await db_with_migrations.fetchval("SELECT now()")
    store = SettingsStore(db_with_migrations)
    again = await _import_at(store, _config(), second_boot, today=DAY)

    assert again.imported == {}, (
        f"the first start was recorded before its import failed, so the repository is not evidence: {again.imported}"
    )
    assert (await store.snapshot()).get(section).enabled is False, "a new deployment's leads stay off"


async def test_a_retry_after_an_import_that_failed_before_recording_its_start_counts_from_the_process_start(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The start's import fails before it can record when the release first asked; a repository is registered;
    the retry in the same run is given the process's start, so the repository still does not count."""
    from embry0.settings import bootstrap

    section = leads_section()
    started = await db_with_migrations.fetchval("SELECT now()")
    real = getattr(bootstrap, "_first_asked", None)
    assert real is not None, "the import records when the release first asked about a relied-on key"
    calls: list[int] = []

    async def fail_once(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("the database dropped the connection")
        return await real(*args, **kwargs)

    monkeypatch.setattr(bootstrap, "_first_asked", fail_once)
    store = SettingsStore(db_with_migrations)
    with pytest.raises(ConnectionError):
        await _import_at(store, _config(), started, today=DAY)
    await ReposRepository(db_with_migrations).register("acme/web")

    settled = await _settle()(store, _config(), None, started=started, sleep=_no_wait)

    assert (settled.unchecked, settled.imported) == ((), {}), f"nothing is kept: {settled.imported}"
    assert (await store.snapshot()).get(section).enabled is False, "a new deployment's leads stay off"


def _lifespan_config(db: DatabasePool, **extra: Any) -> Embry0Config:
    return Embry0Config(
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
        **extra,
    )


@pytest.mark.usefixtures("stub_container_runtime")
async def test_the_lifespan_keeps_leads_on_and_builds_its_readers_of_the_document(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lifespan's own import, catalog and readers: a deployment that existed before keeps leads on."""
    monkeypatch.setattr("embry0.observability.configure_structlog", lambda **kw: None)
    leads_section()
    await ReposRepository(db_with_migrations).register("acme/web")
    config = _lifespan_config(db_with_migrations, leads_review_stale_days=9)
    first = create_app(config)
    async with lifespan(first):
        loop = first.state.leads_loop
        assert loop is not None and callable(loop.settings), (
            "the leads loop is built with a reader of the settings document, not a value read once at startup"
        )
        policy = (await loop.settings()).deployment()
        assert (policy.enabled, policy.review_stale_days) == (True, 9), (
            "the boot kept leads on (a repository was registered) and imported LEADS_REVIEW_STALE_DAYS"
        )
        text = "leads:\n  enabled: true\n  ask_min: 85\n"
        await first.state.settings.save(text, load_yaml(text), actor="alex@example.com")
        assert (await loop.settings()).deployment().ask_min == 85, "a write reaches the running loop's reader"
        writer = first.state.proposal_brief_writer
        assert (await writer._settings()).leads.repo("acme/web").ask_min == 85, (  # noqa: SLF001
            "the brief writer orders held tickets by the section's ask_min, read every tick"
        )
        kept = len(await first.state.settings.revisions())

    second = create_app(config)
    async with lifespan(second):
        assert (await second.state.leads_loop.settings()).deployment().ask_min == 85, "a second boot keeps it"
        assert len(await second.state.settings.revisions()) == kept, "and imports nothing again"


@pytest.mark.usefixtures("stub_container_runtime")
async def test_the_lifespan_asks_a_failed_check_again_in_the_same_run(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The evidence check fails at the start's import: leads read off until the lifespan's retry answers."""
    monkeypatch.setattr("embry0.observability.configure_structlog", lambda **kw: None)
    monkeypatch.setattr("embry0.settings.bootstrap.SETTLE_DELAYS", (0.05,))
    leads_section()
    await ReposRepository(db_with_migrations).register("acme/web")
    calls = _fail_first_check(monkeypatch)
    app = create_app(_lifespan_config(db_with_migrations))
    with capture_logs() as logs:
        async with lifespan(app):
            reader = app.state.leads_loop.settings
            for _ in range(200):
                if (await reader()).deployment().enabled:
                    break
                await asyncio.sleep(0.05)
            enabled = (await reader()).deployment().enabled
    failed = [e["log_level"] for e in logs if e["event"] == "settings_bootstrap_check_failed"]
    assert failed == ["error"], f"the start's failed check is an error: {failed}"
    assert enabled is True, (
        "the lifespan asks the failed check again in the same run, so leads are on without waiting for a restart"
    )
    assert len(calls) == 2, f"asked twice: at the start's import and once more: {calls}"


async def test_a_held_flag_follows_each_repository_s_ask_min_on_every_path_that_shows_it(
    app: Any, client: AsyncClient
) -> None:
    """acme's layer asks from 60 while the deployment asks from 80: acme's ticket ready 70 is asked, globex's held.

    The flag decides who the ask loop asks (``asks.recipients``), so it must be each repository's.
    """
    from embry0.services.asks import recipients

    leads_section()
    app.state.leads_repo = TwoOrgLeads()
    app.state.proposals_repo = TwoOrgProposals()
    resp = await _put(client, "leads:\n  ask_min: 80\n")
    assert resp.status_code == 200, f"the settings document takes leads.ask_min: {resp.text}"
    text = "leads:\n  ask_min: 60\n"
    await app.state.settings.save(text, load_yaml(text), actor="alex@example.com", scope=Scope.organization("acme"))

    listed = await client.get("/api/v1/proposals", params={"kind": "ticket", "include_held": "true"})
    assert listed.status_code == 200, listed.text
    held = {p["id"]: p["held"] for p in listed.json()["proposals"]}
    assert held == {"ticket:acme": False, "ticket:globex": True}, (
        f"each proposal's held flag is its repository's ask_min, through its organization's layer: {held}"
    )
    decided = await client.get("/api/v1/decisions", params={"stage": "offered"})
    assert decided.status_code == 200, decided.text
    decisions = {d["id"]: d for d in decided.json()["decisions"] if d["kind"] == "ticket"}
    assert {i: d["held"] for i, d in decisions.items()} == {"offer:ticket:acme": False, "offer:ticket:globex": True}
    people = {"octo": "octo@example.com", "pat": "pat@example.com"}
    assert recipients(decisions["offer:ticket:acme"], people) == ["octo@example.com"], "acme's ticket is asked"
    assert recipients(decisions["offer:ticket:globex"], people) == [], "globex's is asked of nobody"


async def test_one_decision_read_by_id_marks_readiness_by_its_repository_s_ask_min(
    app: Any, client: AsyncClient
) -> None:
    leads_section()
    app.state.leads_repo = TwoOrgLeads()
    app.state.proposals_repo = TwoOrgProposals()
    resp = await _put(client, "leads:\n  ask_min: 80\n")
    assert resp.status_code == 200, f"the settings document takes leads.ask_min: {resp.text}"
    text = "leads:\n  ask_min: 60\n"
    await app.state.settings.save(text, load_yaml(text), actor="alex@example.com", scope=Scope.organization("acme"))

    def tone(body: dict[str, Any]) -> str:
        return next(f["tone"] for f in body["facts"] if f["label"] == "Readiness")

    acme = await client.get("/api/v1/decisions/offer:ticket:acme")
    globex = await client.get("/api/v1/decisions/offer:ticket:globex")
    assert acme.status_code == globex.status_code == 200, (acme.text, globex.text)
    assert (tone(acme.json()), acme.json()["held"]) == ("", False), (
        f"acme's layer asks from 60, so its ticket ready 70 is not marked: {acme.json()['facts']}"
    )
    assert (tone(globex.json()), globex.json()["held"]) == ("warn", True), "globex reads the deployment's 80"


class TwoOrgLeads:
    """One backlog ticket ready 70 in each of two organizations."""

    async def open_leads(self) -> list[dict[str, Any]]:
        def ticket(owner: str) -> dict[str, Any]:
            return {
                "id": f"ticket:{owner}",
                "kind": "ticket",
                "repo": f"{owner}/api",
                "title": f"Ticket of {owner}",
                "summary": "A backlog ticket embry0 could take (70/100 ready)",
                "details": "",
                "action": "Put the trigger label on the ticket and start its pipeline",
                "first_seen": NOW,
                "data": {"tracker": "linear", "key": f"{owner.upper()}-1", "readiness": 70},
            }

        return [ticket("acme"), ticket("globex")]


class TwoOrgProposals(FakeRepo):
    async def owners(self) -> dict[str, list[str]]:
        return {"acme/api": ["octo"], "globex/api": ["pat"]}


async def _until(condition: Callable[[], Awaitable[bool]], seconds: float = 10.0) -> None:
    for _ in range(int(seconds / 0.05)):
        if await condition():
            return
        await asyncio.sleep(0.05)


@pytest.mark.usefixtures("stub_container_runtime")
async def test_the_lifespan_retry_counts_only_what_a_new_deployment_held_before_it_started(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's probe, through the real lifespan: a new deployment's check fails at the start, acme/web
    is registered, and only then does the retry ask. Leads stay off."""
    monkeypatch.setattr("embry0.observability.configure_structlog", lambda **kw: None)
    monkeypatch.setattr("embry0.settings.bootstrap.SETTLE_DELAYS", (0.01,))
    leads_section()
    history = importlib.import_module("embry0.storage.repositories.deployment_history").DeploymentHistory
    real = history.existed_before
    registered = asyncio.Event()
    calls: list[int] = []

    async def fail_then_wait(self: Any, *, lead_kinds: Any = ()) -> bool:
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("the database dropped the connection")
        await registered.wait()
        return await real(self, lead_kinds=lead_kinds)

    monkeypatch.setattr(history, "existed_before", fail_then_wait)
    app = create_app(_lifespan_config(db_with_migrations))
    async with lifespan(app):
        await ReposRepository(db_with_migrations).register("acme/web")
        registered.set()

        async def settled() -> bool:
            return "leads.enabled" in (await app.state.settings.document())["bootstrapped_keys"]

        await _until(settled)
        enabled = (await app.state.leads_loop.settings()).deployment().enabled
        marked = await settled()
    assert len(calls) == 2 and marked, f"the retry asked once more and settled the key: {calls}, {marked}"
    assert enabled is False, "acme/web came after this start, so it is not the new deployment's past: leads stay off"


@pytest.mark.usefixtures("stub_container_runtime")
async def test_the_lifespan_tries_a_failed_import_again_in_the_same_run(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The start's import fails outright; the retry imports, counting only what was there at the start."""
    from embry0.settings import bootstrap

    monkeypatch.setattr("embry0.observability.configure_structlog", lambda **kw: None)
    monkeypatch.setattr("embry0.settings.bootstrap.SETTLE_DELAYS", (0.01,))
    section = leads_section()
    await ReposRepository(db_with_migrations).register("acme/web")
    real = bootstrap._relied_on  # noqa: SLF001
    calls: list[int] = []

    async def fail_once(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("the start's import fails")
        return await real(*args, **kwargs)

    monkeypatch.setattr(bootstrap, "_relied_on", fail_once)
    app = create_app(_lifespan_config(db_with_migrations))
    with capture_logs() as logs:
        async with lifespan(app):

            async def on() -> bool:
                return bool((await app.state.leads_loop.settings()).deployment().enabled)

            await _until(on)
            enabled = (await app.state.settings.snapshot()).get(section).enabled
    assert [e["event"] for e in logs if e["event"] == "settings_bootstrap_failed"] == ["settings_bootstrap_failed"]
    assert enabled is True, "the failed import is tried again in this run, and acme/web predates the start: on"


@pytest.mark.usefixtures("stub_container_runtime")
async def test_the_lifespan_s_retry_counts_from_the_process_start_when_the_first_import_recorded_nothing(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The start's import fails before it can record when the release first asked; acme/web is registered;
    then a retry records it. Every retry in the process, whoever runs it, counts from the process start."""
    from embry0.settings import bootstrap

    monkeypatch.setattr("embry0.observability.configure_structlog", lambda **kw: None)
    monkeypatch.setattr("embry0.settings.bootstrap.SETTLE_DELAYS", (0.01,))
    section = leads_section()
    real = getattr(bootstrap, "_first_asked", None)
    assert real is not None, "the import records when the release first asked about a relied-on key"
    registered = asyncio.Event()
    calls: list[int] = []

    async def fail_then_wait(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("the database dropped the connection")
        await registered.wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(bootstrap, "_first_asked", fail_then_wait)
    app = create_app(_lifespan_config(db_with_migrations))
    async with lifespan(app):
        await ReposRepository(db_with_migrations).register("acme/web")
        registered.set()

        async def settled() -> bool:
            return "leads.enabled" in (await app.state.settings.document())["bootstrapped_keys"]

        await _until(settled)
        marked = await settled()
        enabled = (await app.state.settings.snapshot()).get(section).enabled
    assert marked, f"a retry settled the key: {calls}"
    assert enabled is False, (
        "acme/web came after this process started, so it is not the new deployment's past, though the start "
        "was only recorded after it"
    )


@pytest.mark.usefixtures("stub_container_runtime")
async def test_each_brief_writer_takes_one_settings_snapshot_a_tick(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store's rule: a loop takes one snapshot a tick, so it never mixes two versions of the documents. The
    writers as the lifespan builds them; only this test's own reads are counted, not the background loops'."""
    monkeypatch.setattr("embry0.observability.configure_structlog", lambda **kw: None)
    leads_section()
    app = create_app(_lifespan_config(db_with_migrations))
    async with lifespan(app):
        store = app.state.settings
        real = store.snapshot
        mine = asyncio.current_task()
        reads: list[int] = []

        async def counted() -> Any:
            if asyncio.current_task() is mine:
                reads.append(1)
            return await real()

        monkeypatch.setattr(store, "snapshot", counted)
        await app.state.proposal_brief_writer.tick()
        proposal_reads = len(reads)
        reads.clear()
        await app.state.job_brief_writer.tick()
        job_reads = len(reads)
    assert proposal_reads == 1, (
        f"a proposal brief tick reads its model and the leads section from one snapshot, not {proposal_reads}"
    )
    assert job_reads == 1, f"a job brief tick reads its model from one snapshot, not {job_reads}"
