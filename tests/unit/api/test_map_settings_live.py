"""The ``maps`` section and ``leads.labels`` (#180), on the real app and Postgres.

Through the real lifespan, the real settings store, the real pipeline
templates and the real integration config:

- a write of ``maps.labels`` or ``leads.labels`` naming one of the
  deployment's trigger labels is refused, at the label's place in the list
  and without its value;
- the trigger labels changing later is not refused: the labels stay, a write
  of the same document still goes through, and an issue opened is not given
  the label that is a trigger label now;
- ``maps.analysis_template`` must name a template that records analysis
  jobs, and a template a document names cannot be deleted, renamed or
  changed to stop recording them (409 naming the documents); a maps write
  reads the templates FOR SHARE, so it waits for a change of one.

Docker is stubbed.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from structlog.testing import capture_logs

from embry0.api.app import create_app, lifespan
from embry0.config import Embry0Config
from embry0.settings.store import SettingsStore
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.pipeline_templates import PipelineTemplatesRepository
from embry0.storage.seeds.pipeline_templates_builtin import seed_builtin_pipeline_templates
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401 - a fixture

pytestmark = pytest.mark.requires_postgres

URL = "/api/v1/config/settings"
TEMPLATES = "/api/v1/pipelines/templates"
HEADERS = {"X-Requested-With": "XMLHttpRequest"}


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
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Any]:
    """The real app through its real lifespan, with acme registered; the trigger label is the default one."""
    monkeypatch.delenv("TRIGGER_LABELS", raising=False)
    app = create_app(_config(db_with_migrations, tmp_path))
    async with lifespan(app):
        await app.state.organizations_repo.register("acme", "github")
        await app.state.repos_repo.register("acme/shop", forge="github")
        yield app


async def _call(app: Any, method: str, url: str, **kwargs: Any) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=HEADERS) as client:
        return await client.request(method, url, **kwargs)


async def _patch(app: Any, section: str, keys: dict[str, Any], **params: str) -> Any:
    return await _call(
        app,
        "PATCH",
        f"{URL}/{section}",
        content=json.dumps(keys),
        headers={"Content-Type": "application/json"},
        params=params,
    )


async def _triggers(app: Any, labels: list[str]) -> None:
    changed = await _call(app, "PUT", "/api/v1/config/integrations", json={"trigger_labels": labels})
    assert changed.status_code == 200, changed.text


async def _template_id(app: Any, name: str) -> str:
    rows = (await _call(app, "GET", TEMPLATES)).json()
    found = [row["id"] for row in rows if row["name"] == name]
    assert found, f"no template named {name!r}"
    return str(found[0])


async def _deep_look(app: Any, *, kind: str | None = "analysis") -> str:
    """A copy of analysis-default named deep-look, whose job_kind is ``kind``; its id."""
    copy = await _call(
        app, "POST", f"{TEMPLATES}/{await _template_id(app, 'analysis-default')}/duplicate", json={"name": "deep-look"}
    )
    assert copy.status_code == 201, copy.text
    deep = str(copy.json()["id"])
    if kind is not None:
        changed = await _call(app, "PUT", f"{TEMPLATES}/{deep}", json={"job_kind": kind})
        assert changed.status_code == 200, changed.text
    return deep


# ---- no label starts a job ----


@pytest.mark.parametrize(("section", "params"), [("maps", {"repo": "acme/shop"}), ("leads", {"owner": "acme"})])
async def test_a_trigger_label_is_refused_at_its_place_without_its_value(
    app: Any, section: str, params: dict[str, str]
) -> None:
    refused = await _patch(app, section, {"labels": ["ours", "Embry0"]}, **params)
    assert refused.status_code == 422, f"{section}.labels took the deployment's trigger label: {refused.text}"
    assert [error["loc"] for error in refused.json()["detail"]] == [[section, "labels", 1]], refused.text
    assert "Embry0" not in refused.text, f"the 422 repeats the label: {refused.text}"

    await _triggers(app, ["run-it"])
    assert (await _patch(app, section, {"labels": ["ours", "embry0"]}, **params)).status_code == 200, (
        "embry0 is no trigger label now"
    )
    again = await _patch(app, section, {"labels": ["ours", "embry0", "RUN-IT"]}, **params)
    assert again.status_code == 422 and [error["loc"] for error in again.json()["detail"]] == [
        [section, "labels", 2]
    ], f"the trigger labels are read as they are when the write comes in: {again.text}"


async def test_a_trigger_change_is_not_refused_and_the_label_is_left_off_the_next_issue(app: Any) -> None:
    from embry0.api.v1 import knowledge_maps

    _map_issue_labels = getattr(knowledge_maps, "_map_issue_labels", None)
    assert _map_issue_labels is not None, "a map's issue is labelled in code, not by the settings document"
    assert (await _patch(app, "maps", {"labels": ["ours", "later"]}, repo="acme/shop")).status_code == 200
    await _triggers(app, ["embry0", "Later"])
    kept = await _patch(app, "maps", {"analysis_template": "analysis-default"}, repo="acme/shop")
    assert kept.status_code == 200, f"a write of another key judged the labels again: {kept.text}"
    put = await _call(app, "GET", URL, params={"repo": "acme/shop", "format": "yaml"})
    assert put.status_code == 200, put.text
    same = await _call(
        app, "PUT", URL, params={"repo": "acme/shop"}, content=put.text, headers={"Content-Type": "application/yaml"}
    )
    assert same.status_code == 200, f"the document written back as it is was refused: {same.text}"

    request = SimpleNamespace(app=app)
    with capture_logs() as logs:
        labels = await _map_issue_labels(request, "acme/shop", "shop", remove=False)  # type: ignore[arg-type]
    assert labels == ["ours", "map:shop"], f"an issue opened now is not given the trigger label: {labels}"
    assert [log.get("label") for log in logs if log["event"] == "issue_label_skipped"] == ["later"], logs


# ---- the analysis template ----


async def test_the_analysis_template_must_record_analysis_jobs(app: Any) -> None:
    unknown = await _patch(app, "maps", {"analysis_template": "nowhere"}, owner="acme")
    assert unknown.status_code == 422 and "no pipeline template named 'nowhere'" in unknown.text, unknown.text
    research = await _patch(app, "maps", {"analysis_template": "research-default"}, owner="acme")
    assert research.status_code == 422 and "records its jobs as research jobs" in research.text, research.text
    assert [error["loc"] for error in research.json()["detail"]] == [["maps", "analysis_template"]]
    deep = await _deep_look(app, kind=None)
    bare = await _patch(app, "maps", {"analysis_template": "deep-look"}, owner="acme")
    assert bare.status_code == 422 and "set its job_kind to analysis" in bare.text, (
        f"a copy that records no kind of job was taken: {bare.text}"
    )
    assert (await _call(app, "PUT", f"{TEMPLATES}/{deep}", json={"job_kind": "analysis"})).status_code == 200
    fine = await _patch(app, "maps", {"analysis_template": "deep-look"}, owner="acme")
    assert fine.status_code == 200, fine.text


async def test_a_template_a_document_analyses_with_is_not_deleted_renamed_or_unfitted(app: Any) -> None:
    deep = await _deep_look(app)
    assert (await _patch(app, "maps", {"analysis_template": "deep-look"}, repo="acme/shop")).status_code == 200
    gone = await _call(app, "DELETE", f"{TEMPLATES}/{deep}")
    assert gone.status_code == 409, f"a template a document analyses with was deleted: {gone.text}"
    assert "maps.analysis_template" in gone.json()["detail"] and "repository acme/shop" in gone.json()["detail"]
    renamed = await _call(app, "PUT", f"{TEMPLATES}/{deep}", json={"name": "shallow-look"})
    assert renamed.status_code == 409 and "rename it" in renamed.json()["detail"], renamed.text
    research = await _call(app, "PUT", f"{TEMPLATES}/{deep}", json={"job_kind": "research"})
    assert research.status_code == 409 and "no longer records analysis jobs" in research.json()["detail"], research.text
    described = await _call(app, "PUT", f"{TEMPLATES}/{deep}", json={"description": "A closer look."})
    assert described.status_code == 200, "a change that keeps it an analysis template is taken"

    default = await _call(app, "DELETE", f"{TEMPLATES}/{await _template_id(app, 'analysis-default')}")
    assert default.status_code == 409 and "the deployment's document (its default)" in default.json()["detail"], (
        f"the template every map analyses with by default was deleted: {default.text}"
    )
    assert (await _patch(app, "maps", {"analysis_template": None}, repo="acme/shop")).status_code == 200
    assert (await _call(app, "DELETE", f"{TEMPLATES}/{deep}")).status_code == 200, "released by editing that layer"


async def test_a_maps_write_waits_for_a_held_template_and_another_section_does_not(
    db_with_migrations: DatabasePool,
) -> None:
    """The templates are read FOR SHARE in a maps write's transaction: a template changed at the same time is
    either seen by the write or sees the write's document. What is asserted is what Postgres says."""
    db = db_with_migrations
    templates = PipelineTemplatesRepository(db)
    await seed_builtin_pipeline_templates(templates)
    held = {row["name"]: row["id"] for row in await templates.list_all()}["analysis-default"]
    store = SettingsStore(db)
    tasks: list[asyncio.Task[Any]] = []
    other_done = maps_waited = False
    async with db.transaction() as conn:
        holder = await conn.fetchval("SELECT pg_backend_pid()")
        await conn.execute("SELECT 1 FROM pipeline_templates WHERE id = $1 FOR UPDATE", held)

        async def blocked() -> bool:
            sql = "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE $1 = ANY(pg_blocking_pids(pid)))"
            return bool(await db.fetchval(sql, holder))

        other = asyncio.create_task(store.set_keys("done_when", {"retries": 2}, actor="t"))
        tasks.append(other)
        await asyncio.wait({other}, timeout=30)
        other_done = other.done()
        if other_done:
            write = asyncio.create_task(store.set_keys("maps", {"labels": ["ours"]}, actor="t"))
            tasks.append(write)
            for _ in range(1500):
                if write.done() or await blocked():
                    break
                await asyncio.sleep(0.02)
            maps_waited = not write.done() and await blocked()
    await asyncio.wait(tasks, timeout=30)
    assert other_done, "a write of another section takes no lock on the templates"
    assert maps_waited, "a maps write reads the templates FOR SHARE, so it waits for the held row"
    for task in tasks:
        task.result()
