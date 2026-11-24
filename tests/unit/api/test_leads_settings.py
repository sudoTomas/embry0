"""The leads policy lives in the settings document (#168), end to end on a real database.

- A PUT of the document reaches the next look of a running leads loop, with no restart: turning
  leads on and off, the finders, and an organization's own layer.
- The readiness thresholds come from the section: ``ask_min`` holds a ticket out of chat in the
  proposals feed and marks it on its decision card, per repository.
- A bad value is a 422 naming its key, never repeating it, and nothing is stored.
- Leads are off by default, and no boot writes the document: a registered repository turns nothing on.
- The real lifespan builds the leads loop and the brief writer with readers of the document.

The section is taken from the application's catalog by name, and each test
first writes or reads it, so each fails with its own message on a tree that
has no leads section.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app, lifespan
from embry0.api.deps import bust_api_key_cache
from embry0.config import Embry0Config
from embry0.services.leads.loop import LeadsLoop
from embry0.settings.document import load_yaml
from embry0.settings.scope import Scope
from embry0.settings.store import SettingsStore, scoped_reader
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.api_keys import ApiKeyRow, hash_token
from embry0.storage.repositories.leads import LeadsRepository
from embry0.storage.repositories.repos import ReposRepository
from tests.fixtures.areas import no_areas_for
from tests.fixtures.leads import leads_section
from tests.unit.api.v1.test_proposals_api import FakeLeads, FakeOrgs, FakeRepo
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401 - a fixture
from tests.unit.services.leads.fakes import FakeForge

pytestmark = pytest.mark.requires_postgres

URL = "/api/v1/config/settings"
ADMIN = "emb_" + "a" * 43
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

    async def targets(section: Any = None) -> list[str]:
        return list(repos)

    async def client_for(repo: str) -> Any:
        return forge

    async def nothing() -> Any:
        return None

    async def tracked(tracker: str, repo: str, key: str) -> bool:
        return False

    async def triggers() -> list[str]:
        return []

    return LeadsLoop(
        settings=scoped_reader(store, leads_section()),
        leads=LeadsRepository(db),
        targets=targets,
        client_for=client_for,
        linear=nothing,
        tracked=tracked,
        trigger_labels=triggers,
        areas_for=no_areas_for,
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


async def test_each_bad_readiness_word_is_a_422_naming_every_problem_and_nothing_is_stored(
    app: Any, client: AsyncClient
) -> None:
    """The words a ticket's readiness is read in (#168): every problem of every key at once, none repeated."""
    assert "readiness_labels" in leads_section().model.model_fields, "the leads section holds the readiness words"
    text = (
        "leads:\n"
        "  readiness_labels: {zz-omen-role: [zz-a], ready: [zz-dup, ZZ-DUP]}\n"
        "  readiness_weights: {zz-omen-signal: 5, done: 4242}\n"
        "  done_phrases: [zz-phrase, ZZ-Phrase]\n"
        "  epic_title_words: [zz-long-word-zz-long-word-zz-long-word-zz-long]\n"
        "  small_estimate_max: -7\n"
    )
    resp = await _put(client, text)
    assert resp.status_code == 422, f"a bad readiness word is refused when it is written: {resp.text}"
    detail = resp.json()["detail"]
    assert sorted(tuple(p["loc"][:2]) for p in detail) == [
        ("leads", "done_phrases"),
        ("leads", "epic_title_words"),
        ("leads", "readiness_labels"),
        ("leads", "readiness_weights"),
        ("leads", "small_estimate_max"),
    ], f"each bad key is refused by its own name: {resp.text}"
    labels = next(p["msg"] for p in detail if p["loc"][1] == "readiness_labels")
    assert "name only the roles" in labels and "ready lists a label twice" in labels, (
        f"one key's every problem is named: {labels}"
    )
    assert "zz-" not in resp.text.casefold() and "4242" not in resp.text, "the 422 repeats nothing written"
    stored = (await client.get(URL)).json()
    assert (stored["yaml_text"], stored["data"]) == ("", {}), "a refused document stores nothing"


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
async def test_the_lifespan_builds_the_leads_readers_of_the_document(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lifespan's own catalog and readers. A registered repository turns nothing on: the boot writes nothing."""
    monkeypatch.setattr("embry0.observability.configure_structlog", lambda **kw: None)
    section = leads_section()
    await ReposRepository(db_with_migrations).register("acme/web")
    config = _lifespan_config(db_with_migrations)
    first = create_app(config)
    async with lifespan(first):
        loop = first.state.leads_loop
        assert loop is not None and callable(loop.settings), (
            "the leads loop is built with a reader of the settings document, not a value read once at startup"
        )
        assert (await loop.settings()).deployment() == section.defaults(), (
            "nothing stored: the running loop's reader reads the section's defaults, leads off"
        )
        assert await first.state.settings.revisions() == [], "the boot writes no revision"
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
        assert len(await second.state.settings.revisions()) == kept, "and writes no revision"


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
