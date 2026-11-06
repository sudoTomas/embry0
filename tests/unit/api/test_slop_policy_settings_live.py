"""Slop routing is a settings section (#169), on the real app and Postgres.

Through the real lifespan, the real settings store and the real pipeline
templates, seeded at the start:

- the acceptance criterion: with ``slop_policy.enabled: false`` in acme's
  layer, a job of acme's judged slop takes its normal route, while globex's
  still runs the cleanup; undoing the change routes acme's again;
- every write path checks the section: 422 naming each refused key, a route
  template that does not take a code job included;
- a template a document routes to cannot be deleted, renamed or changed to
  stop taking a code job (409 naming the documents), and the check cannot
  race a settings write either way.

Docker is stubbed.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app, lifespan
from embry0.config import Embry0Config
from embry0.services.job_settings import STATE_KEY, read_for_job
from embry0.settings.document import DocumentInvalid
from embry0.settings.scope import Scope
from embry0.settings.sections import CATALOG
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.pipeline_templates import PipelineTemplatesRepository
from embry0.storage.repositories.settings_documents import SettingsDocumentsRepository
from embry0.storage.seeds.pipeline_templates_builtin import seed_builtin_pipeline_templates
from embry0.workflows.issue_to_pr.plan_route import plan_route_node
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401 - a fixture

pytestmark = pytest.mark.requires_postgres

URL = "/api/v1/config/settings"
TEMPLATES = "/api/v1/pipelines/templates"
HEADERS = {"X-Requested-With": "XMLHttpRequest"}
SLOP = {"nature": "slop", "slop_score": 72, "kinds": ["symptom_patch"], "evidence": []}


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
            slop_meter_interval_seconds=0,
            slop_meter_data_path=str(tmp_path / "slop-meter"),
        )


@pytest.fixture
async def app(
    db_with_migrations: DatabasePool,
    stub_container_runtime: Any,  # noqa: F811
    tmp_path: Path,
) -> AsyncIterator[Any]:
    """The real app through its real lifespan, with acme and globex registered."""
    app = create_app(_config(db_with_migrations, tmp_path))
    async with lifespan(app):
        for owner in ("acme", "globex"):
            await app.state.organizations_repo.register(owner, "github")
        for repo in ("acme/widgets", "globex/api"):
            await app.state.repos_repo.register(repo, forge="github")
        yield app


async def _call(app: Any, method: str, url: str, **kwargs: Any) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=HEADERS) as client:
        return await client.request(method, url, **kwargs)


async def _patch(app: Any, keys: dict[str, Any], **params: str) -> Any:
    return await _call(
        app,
        "PATCH",
        f"{URL}/slop_policy",
        content=json.dumps(keys),
        headers={"Content-Type": "application/json"},
        params=params,
    )


async def _route(app: Any, repo: str) -> tuple[str | None, str]:
    """The route plan_route gives a job of ``repo`` judged slop, with the settings it reads when it starts."""
    state = {
        "job_id": f"job-{repo}",
        "repo": repo,
        "triage_decision": {"job_kind": "code", "work_nature": SLOP},
        STATE_KEY: await read_for_job(app.state.settings, repo),
    }
    configurable = {"pipeline_templates_repo": app.state.templates_repo, "db": app.state.db}
    with patch("embry0.workflows.issue_to_pr.plan_route.get_stream_writer", return_value=lambda _e: None):
        out = await plan_route_node(state, {"configurable": configurable})
    return out["route_template_name"], out["coding_agent"]


async def _template_id(app: Any, name: str) -> str:
    rows = (await _call(app, "GET", TEMPLATES)).json()
    found = [row["id"] for row in rows if row["name"] == name]
    assert found, f"no template named {name!r}"
    return str(found[0])


# ---- the acceptance criterion ----


async def test_with_the_policy_off_a_job_judged_slop_takes_its_normal_route(app: Any) -> None:
    assert await _route(app, "acme/widgets") == ("slop-cleanup", "slopmaster"), "routed by default, as before"

    resp = await _patch(app, {"enabled": False}, owner="acme")
    assert resp.status_code == 200, f"acme's layer cannot turn slop routing off: {resp.text}"
    assert await _route(app, "acme/widgets") == ("full-magnum-opus", "developer"), (
        "with slop_policy.enabled: false, acme's job judged slop takes its normal route"
    )
    assert await _route(app, "globex/api") == ("slop-cleanup", "slopmaster"), "acme's layer never routes globex's"

    revisions = (await _call(app, "GET", f"{URL}/revisions", params={"owner": "acme"})).json()["revisions"]
    undo = await _call(app, "POST", f"{URL}/revisions/{revisions[0]['id']}/undo")
    assert undo.status_code == 200, f"the change has history with undo: {undo.text}"
    assert await _route(app, "acme/widgets") == ("slop-cleanup", "slopmaster"), "undone: routed again"


async def test_triage_leans_by_the_level_names_its_repository_reads(app: Any) -> None:
    from embry0.services.slop_meter import context

    names = ["Clean", "Fine", "Mixed", "Sloppy", "Slop"]
    resp = await _call(
        app,
        "PATCH",
        f"{URL}/slop_meter",
        content=json.dumps({"level_names": names}),
        headers={"Content-Type": "application/json"},
        params={"owner": "acme"},
    )
    assert resp.status_code == 200, resp.text
    reader = getattr(context, "level_names", None)
    assert reader is not None, "triage is told no level names but the defaults"
    config = {"configurable": {"db": app.state.db}}
    assert list(await reader(config, "acme/widgets")) == names, "acme's own names"
    assert (await reader(config, "globex/api"))[3] == "Sloppy Joe", "globex reads the defaults"


# ---- the write paths ----


async def test_a_write_names_each_refused_key(app: Any) -> None:
    text = (
        "slop_policy:\n  kinds: []\n  route_template: research-default\n  ticket_heading: '## Slop'\n"
        "  dungeon_min_level: 9\n"
    )
    resp = await _call(app, "PUT", URL, content=text, headers={"Content-Type": "application/yaml"})
    assert resp.status_code == 422, resp.text
    named = {".".join(map(str, error["loc"][-2:])): error["msg"] for error in resp.json()["detail"]}
    assert set(named) == {
        "slop_policy.kinds",
        "slop_policy.route_template",
        "slop_policy.ticket_heading",
        "slop_policy.dungeon_min_level",
    }, f"the 422 names each refused key: {named}"
    assert "records its jobs as research jobs" in named["slop_policy.route_template"]

    unknown = await _patch(app, {"route_template": "nowhere"}, owner="acme")
    assert unknown.status_code == 422 and "no pipeline template named 'nowhere'" in unknown.text, unknown.text
    words = await _patch(app, {"classifier_guide": "Our own words."}, repo="acme/widgets")
    assert words.status_code == 422 and "not_overridable" in words.text, words.text
    fine = await _patch(app, {"route_template": "quick-fix", "min_score_to_route": 50}, repo="acme/widgets")
    assert fine.status_code == 200, fine.text


async def test_the_policy_tab_shows_the_section(app: Any) -> None:
    effective = (await _call(app, "GET", f"{URL}/effective")).json()
    text = json.dumps(effective)
    assert "slop_policy" in text and "route_template" in text and "classifier_guide" in text, (
        "the Policy tab lists the sections the API answers"
    )
    schema = (await _call(app, "GET", f"{URL}/schema")).json()
    assert schema["x-embry0-sections"]["slop_policy"]["repo_keys"] == [
        "enabled",
        "min_score_to_route",
        "route_template",
    ]


# ---- a template a policy routes to ----


async def test_a_template_a_policy_routes_to_is_not_deleted_renamed_or_unfitted(app: Any) -> None:
    cleanup = await _template_id(app, "slop-cleanup")
    gone = await _call(app, "DELETE", f"{TEMPLATES}/{cleanup}")
    assert gone.status_code == 409, f"the template the default policy routes to was deleted: {gone.text}"
    assert "the deployment's document (its default)" in gone.json()["detail"]
    renamed = await _call(app, "PUT", f"{TEMPLATES}/{cleanup}", json={"name": "tidy"})
    assert renamed.status_code == 409 and "rename it" in renamed.json()["detail"], (
        f"a template a document routes to was renamed: {renamed.text}"
    )
    described = await _call(app, "PUT", f"{TEMPLATES}/{cleanup}", json={"description": "Our cleanup."})
    assert described.status_code == 200, "a change that keeps it a code route is taken"

    copy = await _call(app, "POST", f"{TEMPLATES}/{cleanup}/duplicate", json={"name": "tidy-up"})
    assert copy.status_code == 201, copy.text
    tidy = str(copy.json()["id"])
    assert (await _patch(app, {"route_template": "tidy-up"}, owner="acme")).status_code == 200
    held = await _call(app, "DELETE", f"{TEMPLATES}/{tidy}")
    assert held.status_code == 409 and "organization acme" in held.json()["detail"], held.text
    research = await _call(app, "PUT", f"{TEMPLATES}/{tidy}", json={"job_kind": "research"})
    assert research.status_code == 409 and "no longer takes a code job" in research.json()["detail"], research.text
    assert await _route(app, "acme/widgets") == ("tidy-up", "slopmaster")

    assert (await _patch(app, {"route_template": "tidy-up"})).status_code == 200
    released = await _call(app, "DELETE", f"{TEMPLATES}/{cleanup}")
    assert released.status_code == 200, f"nothing routes to slop-cleanup now: {released.text}"


async def _tidy_up(app: Any, **fields: Any) -> str:
    """A copy of slop-cleanup named tidy-up, with ``fields`` changed; its id."""
    copy = await _call(
        app, "POST", f"{TEMPLATES}/{await _template_id(app, 'slop-cleanup')}/duplicate", json={"name": "tidy-up"}
    )
    assert copy.status_code == 201, copy.text
    tidy = str(copy.json()["id"])
    if fields:
        changed = await _call(app, "PUT", f"{TEMPLATES}/{tidy}", json=fields)
        assert changed.status_code == 200, changed.text
    return tidy


async def test_a_template_whose_row_records_another_kind_is_refused(app: Any) -> None:
    """The write reads each template's row as it is, its own job_kind included."""
    await _tidy_up(app, job_kind="research")
    refused = await _patch(app, {"route_template": "tidy-up"}, owner="acme")
    assert refused.status_code == 422 and "records its jobs as research jobs" in refused.text, (
        f"a template whose row records research jobs was taken as a slop route: {refused.text}"
    )


async def test_a_dry_run_checks_the_template_as_a_write_does(app: Any) -> None:
    resp = await _call(
        app,
        "PATCH",
        f"{URL}/slop_policy",
        content=json.dumps({"route_template": "nowhere"}),
        headers={"Content-Type": "application/json"},
        params={"owner": "acme", "dry_run": "true"},
    )
    assert resp.status_code == 422 and "no pipeline template named 'nowhere'" in resp.text, (
        f"a dry run passed a template the write would refuse: {resp.status_code} {resp.text}"
    )


async def test_taking_route_template_out_never_leaves_a_template_that_is_gone(app: Any) -> None:
    """The deployment routes to tidy-up and slop-cleanup is deleted: a write that falls back to the default,
    by leaving the key out or the whole section, is refused, and tidy-up stays held."""
    tidy = await _tidy_up(app)
    assert (await _patch(app, {"route_template": "tidy-up"})).status_code == 200
    assert (await _call(app, "DELETE", f"{TEMPLATES}/{await _template_id(app, 'slop-cleanup')}")).status_code == 200
    for text in ("slop_policy:\n  enabled: true\n", "ui:\n  game: true\n"):
        resp = await _call(app, "PUT", URL, content=text, headers={"Content-Type": "application/yaml"})
        assert resp.status_code == 422, (
            f"{text!r} left the deployment routing to the default slop-cleanup, which is gone: {resp.text}"
        )
        locs = [".".join(map(str, error["loc"][-2:])) for error in resp.json()["detail"]]
        assert locs == ["slop_policy.route_template"], locs
    assert await _route(app, "acme/widgets") == ("tidy-up", "slopmaster")
    held = await _call(app, "DELETE", f"{TEMPLATES}/{tidy}")
    assert held.status_code == 409, "tidy-up is still what the deployment routes to"


async def test_an_undo_that_would_route_to_a_template_that_is_gone_is_refused(app: Any) -> None:
    tidy = await _tidy_up(app)
    assert (await _patch(app, {"route_template": "tidy-up"}, owner="acme")).status_code == 200
    assert (await _patch(app, {"route_template": "slop-cleanup"}, owner="acme")).status_code == 200
    assert (await _call(app, "DELETE", f"{TEMPLATES}/{tidy}")).status_code == 200, "nothing routes to tidy-up now"
    revisions = (await _call(app, "GET", f"{URL}/revisions", params={"owner": "acme"})).json()["revisions"]
    undo = await _call(app, "POST", f"{URL}/revisions/{revisions[0]['id']}/undo")
    assert undo.status_code == 422 and "no pipeline template named 'tidy-up'" in undo.text, (
        f"an undo put back a route to a deleted template: {undo.status_code} {undo.text}"
    )
    assert await _route(app, "acme/widgets") == ("slop-cleanup", "slopmaster")


async def test_a_repository_layer_holds_the_template_it_routes_to(app: Any) -> None:
    tidy = await _tidy_up(app)
    assert (await _patch(app, {"route_template": "tidy-up"}, repo="acme/widgets")).status_code == 200
    held = await _call(app, "DELETE", f"{TEMPLATES}/{tidy}")
    assert held.status_code == 409 and "repository acme/widgets" in held.json()["detail"], (
        f"a template a repository's layer routes to was deleted: {held.text}"
    )
    assert (await _patch(app, {"route_template": None}, repo="acme/widgets")).status_code == 200
    assert (await _call(app, "DELETE", f"{TEMPLATES}/{tidy}")).status_code == 200, "released by editing that layer"


# ---- no race between a settings write and a template change ----


def _guarding() -> Any:
    """``embry0.services.slop_routing``: the guard of a template a settings document routes to."""
    try:
        from embry0.services import slop_routing
    except ImportError:
        pytest.fail("nothing guards a template a settings document routes slop jobs to")
    return slop_routing


async def _seeded(db: DatabasePool) -> tuple[PipelineTemplatesRepository, str]:
    templates = PipelineTemplatesRepository(db)
    await seed_builtin_pipeline_templates(templates)
    rows = {row["name"]: row["id"] for row in await templates.list_all()}
    copy = await templates.duplicate(str(rows["slop-cleanup"]), new_name="tidy-up")
    return templates, str(copy["id"])


async def _settle(go: asyncio.Event, *tasks: asyncio.Task[Any]) -> None:
    """Let a held transaction go and wait for every task, whatever the test asserted: a task left waiting holds
    its transaction open, and the database fixture's cleanup would wait for it for good."""
    go.set()
    await asyncio.wait(tasks, timeout=30)
    for task in tasks:
        if not task.done():
            task.cancel()


async def test_a_delete_waits_for_a_write_that_read_its_template_and_then_sees_it(
    db_with_migrations: DatabasePool,
) -> None:
    db = db_with_migrations
    routing = _guarding()
    templates, tidy = await _seeded(db)
    locked, go = asyncio.Event(), asyncio.Event()
    from embry0.settings.write_context import deployment_reads

    async def slow_reads(conn: Any, sections: frozenset[str]) -> Any:
        context = await deployment_reads(conn, sections)
        locked.set()
        await go.wait()
        return context

    store = SettingsStore(db, write_reads=slow_reads)
    write = asyncio.create_task(
        store.set_keys("slop_policy", {"route_template": "tidy-up"}, actor="t", scope=Scope.organization("acme"))
    )
    guard = routing.slop_route_guard(SettingsDocumentsRepository(db), CATALOG)
    delete: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(locked.wait(), 10)
        delete = asyncio.create_task(templates.delete(tidy, guard=guard))
        await asyncio.sleep(0.3)
        blocked = not delete.done()
    finally:
        await _settle(go, write, *([delete] if delete is not None else []))
    assert blocked, "the delete waits for the settings write that read its template"
    write.result()
    with pytest.raises(routing.TemplateInUse, match="organization acme"):
        delete.result()
    assert await templates.get(tidy) is not None


async def test_a_write_waits_for_a_delete_of_its_template_and_then_refuses_it(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    routing = _guarding()
    templates, tidy = await _seeded(db)
    locked, go = asyncio.Event(), asyncio.Event()
    guard = routing.slop_route_guard(SettingsDocumentsRepository(db), CATALOG)

    async def slow_guard(conn: Any, before: Any, after: Any) -> None:
        await guard(conn, before, after)
        locked.set()
        await go.wait()

    delete = asyncio.create_task(templates.delete(tidy, guard=slow_guard))
    store = SettingsStore(db)
    write: asyncio.Task[Any] | None = None
    try:
        await asyncio.wait_for(locked.wait(), 10)
        write = asyncio.create_task(
            store.set_keys("slop_policy", {"route_template": "tidy-up"}, actor="t", scope=Scope.organization("acme"))
        )
        await asyncio.sleep(0.3)
        blocked = not write.done()
    finally:
        await _settle(go, delete, *([write] if write is not None else []))
    assert blocked, "the settings write waits for the delete of the template it names"
    delete.result()
    with pytest.raises(DocumentInvalid) as refused:
        write.result()
    assert "no pipeline template named 'tidy-up'" in str([p.msg for p in refused.value.problems])


async def test_only_a_write_that_may_change_slop_policy_waits_on_a_template(db_with_migrations: DatabasePool) -> None:
    """A template row held by a change waits a slop_policy write, and no write of another section."""
    db = db_with_migrations
    templates, tidy = await _seeded(db)
    store = SettingsStore(db)
    async with db.transaction() as conn:
        await conn.execute("SELECT 1 FROM pipeline_templates WHERE id = $1 FOR UPDATE", tidy)
        other = asyncio.create_task(store.set_keys("done_when", {"retries": 2}, actor="t"))
        dry = asyncio.create_task(store.preview_keys("done_when", {"retries": 3}))
        slop = asyncio.create_task(store.set_keys("slop_policy", {"min_score_to_route": 10}, actor="t"))
        done, _pending = await asyncio.wait({other, dry}, timeout=5)
        waiting = not slop.done()
    await asyncio.wait({other, dry, slop}, timeout=30)
    assert done == {other, dry}, "a write of another section, and its dry run, take no lock on the templates"
    assert waiting, "a slop_policy write reads the templates FOR SHARE, so it waits for the held row"
    slop.result()
