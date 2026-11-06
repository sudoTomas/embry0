"""The Slop-O-Meter's scoring policy is a settings section (#169), on the real app and Postgres.

Through the real lifespan, the real settings store and the meter's real
store: a weight changed in an organization's layer, as an operator writes it
(``PATCH /api/v1/config/settings/slop_meter?owner=``), rescores its
repository on the meter's next look, and the score row records the version
of the settings it was computed with; undoing the change rescores it back.
The write paths answer 422 naming every refused key, and an organization's
``slop_meter_bots`` (#184) moves into its layer. Docker is stubbed; nothing
runs git: the history comes from a fake mirror.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app, lifespan
from embry0.config import Embry0Config
from embry0.services.slop_meter import loop as loop_module
from embry0.services.slop_meter.loop import SlopMeterLoop
from embry0.storage.database import DatabasePool
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401 - a fixture
from tests.unit.services.slop_meter import test_loop as fakes

pytestmark = pytest.mark.requires_postgres

URL = "/api/v1/config/settings"
HEADERS = {"X-Requested-With": "XMLHttpRequest"}
REPO = "acme/widgets"
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


def _config(db: DatabasePool, tmp_path: Path) -> Embry0Config:
    with patch.dict(os.environ, {}, clear=True):
        return Embry0Config(  # type: ignore[call-arg]
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
            # The production meter's first look only removes, then it waits: the tests run their own.
            slop_meter_interval_seconds=0,
            slop_meter_data_path=str(tmp_path / "slop-meter"),
        )


async def _started(app: Any) -> None:
    meter: SlopMeterLoop = app.state.slop_meter
    if not hasattr(meter, "last_pass"):
        pytest.fail("the meter has no schedule that looks at the settings: a change waits for a restart")
    for _ in range(500):
        if meter.last_pass is not None:
            return
        await asyncio.sleep(0.01)
    pytest.fail("the production meter's first look never ran")


@pytest.fixture
async def app(
    db_with_migrations: DatabasePool,
    stub_container_runtime: Any,  # noqa: F811
    tmp_path: Path,
) -> AsyncIterator[Any]:
    """The real app through its real lifespan, with ``acme`` registered and ``acme/widgets`` enrolled."""
    app = create_app(_config(db_with_migrations, tmp_path))
    async with lifespan(app):
        await _started(app)
        await app.state.organizations_repo.register("acme", "github")
        await app.state.repos_repo.register(REPO, forge="github")
        await _put(app, "background:\n  slop_meter: true\n", owner="acme")
        await _put(app, "slop_meter:\n  enabled: true\n")
        yield app


async def _call(app: Any, method: str, url: str, **kwargs: Any) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=HEADERS) as client:
        return await client.request(method, url, **kwargs)


async def _put(app: Any, text: str, **params: str) -> None:
    resp = await _call(app, "PUT", URL, content=text, headers={"Content-Type": "application/yaml"}, params=params)
    assert resp.status_code == 200, f"the settings document took {text!r} at {params or 'the deployment'}: {resp.text}"


async def _patch(app: Any, keys: dict[str, Any], **params: str) -> Any:
    return await _call(
        app,
        "PATCH",
        f"{URL}/slop_meter",
        content=json.dumps(keys),
        headers={"Content-Type": "application/json"},
        params=params,
    )


class _Mirror(fakes.FakeMirror):
    """A fake mirror that also lists what it holds on disk, for the prune each tick runs first: nothing."""

    async def held(self) -> list[str]:
        return []


def _meter(app: Any, clock: list[datetime]) -> SlopMeterLoop:
    """The production meter's readers (the registry and the settings documents), its bots reader and its real
    store, with a fake mirror holding a history of SQL and Python, and no forge client: commits only."""
    production: SlopMeterLoop = app.state.slop_meter
    order = [f"q{i:03d}" for i in range(30)]
    records = {
        sha: fakes._record(
            sha, NOW - timedelta(days=60) + timedelta(hours=5 * i), [("db/q.sql", 400, 0), ("a.py", 9, 0)]
        )
        for i, sha in enumerate(order)
    }

    async def token_for(repo: str) -> tuple[str, str, str]:
        return ("github", "token-1", "")

    async def no_client(repo: str) -> None:
        return None

    pruner = production._pruner  # noqa: SLF001
    limits = getattr(loop_module, "SlopMeterLimits", None)
    if limits is None:
        pytest.fail("the meter's loop takes no process limits apart from the settings document's slop_meter section")
    return SlopMeterLoop(
        repos=production._repos,  # noqa: SLF001 - the production readers are what this test pins
        read=production._read,  # noqa: SLF001
        store=app.state.slop_meter_repo,
        mirror=_Mirror(records, order),
        exits=pruner.exits,
        audit=pruner.audit,
        token_for=token_for,
        client_for=no_client,
        bots=production._bots,  # noqa: SLF001
        calls=fakes.FakeCalls(),
        limits=limits(commits_per_tick=100),
        clock=lambda: clock[0],
    )


async def _score(db: DatabasePool) -> tuple[float, str]:
    row = await db.fetchrow("SELECT score, config_version FROM slop_meter_scores WHERE repo = $1 AND area = ''", REPO)
    assert row is not None, "the meter stored no score for the repository"
    return float(row["score"]), str(row["config_version"])


async def _effective_version(app: Any) -> str:
    from embry0.settings.sections.slop_meter import SLOP_METER, config_version

    snapshot = await app.state.settings.snapshot()
    return config_version(snapshot.scoped(SLOP_METER).repo(REPO))


async def test_a_weight_changed_in_the_document_rescores_on_the_next_run_and_records_the_version(
    app: Any, db_with_migrations: DatabasePool
) -> None:
    db = db_with_migrations
    clock = [NOW]
    loop = _meter(app, clock)
    assert await loop.scheduled() == "run", "the first look reads and scores the enrolled repository"
    fetched = len(loop._mirror.synced)  # noqa: SLF001 - the fake mirror records each fetch
    first_score, first_version = await _score(db)
    assert first_version == await _effective_version(app) != "", (
        "the score records the version of the settings acme/widgets reads"
    )

    resp = await _patch(app, {"weights": {"*.sql": 0.1}}, owner="acme")
    assert resp.status_code == 200, resp.text
    clock[0] += timedelta(minutes=5)
    assert await loop.scheduled() == "rescore", "a weight changed: the next look rescores, without waiting for the day"
    assert len(loop._mirror.synced) == fetched, "from what was read: nothing is fetched"  # noqa: SLF001
    second_score, second_version = await _score(db)
    assert second_version == await _effective_version(app) != first_version, (
        f"the rescored row records the new version: {first_version} → {second_version}"
    )
    assert second_score != first_score, f"SQL weighing a tenth changes the score: {first_score} → {second_score}"
    reading = await _call(app, "GET", f"/api/v1/repos/{REPO}/-/slop-meter")
    assert reading.status_code == 200 and reading.json()["reading"]["config_version"] == second_version

    revisions = await _call(app, "GET", f"{URL}/revisions", params={"owner": "acme"})
    newest = revisions.json()["revisions"][0]
    undo = await _call(app, "POST", f"{URL}/revisions/{newest['id']}/undo")
    assert undo.status_code == 200, f"the change has history with undo: {undo.text}"
    clock[0] += timedelta(minutes=5)
    assert await loop.scheduled() == "rescore", "undoing it is a change too"
    assert await _score(db) == (first_score, first_version), "and scores as before, recording the first version"


async def test_a_write_names_every_refused_key_and_a_repository_may_set_only_its_layout(app: Any) -> None:
    resp = await _patch(app, {"weights": {"*.PY": 1}, "level_bounds": [0.5, 0.4, 0.6, 0.8], "ai_agents": ["dev"]})
    assert resp.status_code == 422, resp.text
    named = {tuple(problem["loc"])[-1] for problem in resp.json()["detail"]}
    assert {"weights", "level_bounds", "ai_agents"} <= named, f"422 names every refused key: {resp.json()['detail']}"
    assert (await _patch(app, {"weights": {"generated/**": 0}}, repo=REPO)).status_code == 200
    refused = await _patch(app, {"lines_per_hour": 10}, repo=REPO)
    assert refused.status_code == 422 and "lines_per_hour" in refused.text, "a team's pace is its organization's"

    document = (await _call(app, "GET", URL, params={"repo": REPO})).json()
    assert "slop_meter" in document["sections"], "the Policy tab lists the section, as it lists every one"
    assert sorted(document["settable"]["slop_meter"]) == [
        "excluded_dirs",
        "test_dirs",
        "test_files",
        "test_marks",
        "weights",
    ], "and the keys a repository's layer may set"
    effective = (await _call(app, "GET", f"{URL}/effective", params={"repo": REPO})).json()
    [section] = [s for s in effective["sections"] if s["name"] == "slop_meter"]
    weights = next(k for k in section["keys"] if k["key"] == "weights")
    sources = {e["entry"]: e["source"] for e in weights["entries"]}
    assert sources["generated/**"] == "repository" and sources["*.py"] == "default", (
        f"the values in effect show each glob with the document it comes from: {weights}"
    )


async def test_an_organization_s_bots_move_into_its_layer(app: Any, db_with_migrations: DatabasePool) -> None:
    await _put(app, "slop_meter:\n  enabled: true\n  bots: [ci-runner]\n")
    bots = {"slop_meter_bots": [" release-bot ", "Release-Bot", "tab\tbot"]}
    resp = await _call(app, "PATCH", "/api/v1/orgs/acme", json={"settings": bots})
    assert resp.status_code == 200, f"a list the route took before is taken, each bot once: {resp.text}"
    assert [note["moved_to"] for note in resp.json()["deprecated"]] == ["slop_meter.bots"], resp.json()
    layer = await _call(app, "GET", URL, params={"owner": "acme"})
    assert layer.json()["data"]["slop_meter"]["bots"] == ["release-bot"], (
        "written into acme's layer once, the entry the section cannot hold left out"
    )
    assert "slop_meter_bots" not in resp.json()["settings"], "and no longer shown as the organization's own"
    from embry0.settings.sections.slop_meter import SLOP_METER

    snapshot = await app.state.settings.snapshot()
    assert snapshot.scoped(SLOP_METER).repo(REPO).bots == ("ci-runner", "release-bot"), (
        "acme's bots add to the deployment's: a bot the deployment lists counts for every organization"
    )
    effective = (await _call(app, "GET", f"{URL}/effective", params={"repo": REPO})).json()
    [section] = [s for s in effective["sections"] if s["name"] == "slop_meter"]
    listed = next(k for k in section["keys"] if k["key"] == "bots")
    assert listed["merged_by_entry"] and {e["entry"]: e["source"] for e in listed["entries"]} == {
        "ci-runner": "document",
        "release-bot": "organization",
    }, f"the values in effect name the document each bot comes from: {listed}"
    posted = await _call(app, "POST", "/api/v1/orgs", json={"owner": "initech", "probe": False, "settings": bots})
    assert posted.status_code == 201, f"registering an owner with them registers it and writes its layer: {posted.text}"

    # An owner whose row still lists bots its layer never imported (registered and written after the start's
    # copy) is read from its row, and copied into its layer by the budget policy's retry.
    await app.state.organizations_repo.register("globex", "github")
    await db_with_migrations.execute(
        "UPDATE organizations SET settings = '{\"slop_meter_bots\": [\"ci-bot\"]}'::jsonb WHERE owner = 'globex'"
    )
    read = await app.state.slop_meter._bots("globex/tools")  # noqa: SLF001 - the production bots reader
    from embry0.settings.sections.slop_meter import SLOP_METER

    snapshot = await app.state.settings.snapshot()
    listed = set(read) | set(snapshot.scoped(SLOP_METER).repo("globex/tools").bots)
    assert "ci-bot" in listed, f"the owner's own bots count, from its row or its layer: {read}, {listed}"


async def test_the_first_start_copies_each_organization_s_bots_into_its_layer(
    db_with_migrations: DatabasePool,
    stub_container_runtime: Any,  # noqa: F811
    tmp_path: Path,
) -> None:
    db = db_with_migrations
    await db.execute(
        "INSERT INTO organizations (owner, forge, settings) VALUES ('acme', 'github', $1::jsonb)",
        {"slop_meter_bots": ["release-bot", "Release-Bot", " ", "zero\u200bwidth", "ci@acme.example"]},
    )
    app = create_app(_config(db, tmp_path))
    async with lifespan(app):
        await _started(app)
        layer = await _call(app, "GET", URL, params={"owner": "acme"})
    assert layer.json()["data"].get("slop_meter", {}).get("bots") == ["release-bot", "ci@acme.example"], (
        "the start copies the owner's bots into its layer, each once, leaving out the entry the section cannot "
        f"hold rather than refusing the list: {layer.json()['data']}"
    )


async def test_a_moved_setting_the_section_refuses_is_refused_before_the_owner_is_registered(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``POST /orgs`` checks each moved setting as its layer would take it before registering anything, so a
    refusal never leaves an owner registered without the layer it asked for. Shown here with the cleaning of
    ``slop_meter_bots`` turned off, since a cleaned list is one the section always takes."""
    import dataclasses

    from embry0.services import organizations as org_service

    monkeypatch.setitem(
        org_service.MOVED, "slop_meter_bots", dataclasses.replace(org_service.MOVED["slop_meter_bots"], write=None)
    )
    posted = await _call(
        app,
        "POST",
        "/api/v1/orgs",
        json={"owner": "umbrella", "probe": False, "settings": {"slop_meter_bots": ["a\tb"]}},
    )
    assert posted.status_code == 422 and "slop_meter_bots" in posted.text, posted.text
    assert await app.state.organizations_repo.get("umbrella") is None, "nothing was registered"
