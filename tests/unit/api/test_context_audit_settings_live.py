"""The production context audit reads the ``context_audit`` section the API writes (#170), on a real database.

The app runs its real lifespan, so the loop under test is the one production
builds, reading the settings documents through the budget policy it was
given: these tests pin that wiring as much as the section. The documents
are written through ``PUT /config/settings``, the deployment's and each
layer, and read back through ``GET /orgs/{owner}/context-audit``, which
surveys through the same loop; only the forge is faked.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from embry0.api.app import create_app, lifespan
from embry0.config import Embry0Config
from embry0.storage.database import DatabasePool
from tests.unit.metrics.test_lifespan_wiring import stub_container_runtime  # noqa: F401 - a fixture
from tests.unit.services.test_context_audit import HEAD, FakeForge

pytestmark = pytest.mark.requires_postgres

URL = "/api/v1/config/settings"
HEADERS = {"X-Requested-With": "XMLHttpRequest"}
WIDGETS, GIZMOS, GADGETS = "acme/widgets", "acme/gizmos", "globex/gadgets"
FILES = ("CLAUDE.md", "CONVENTIONS.md", "docs/CONVENTIONS.md", "HANDOFF.md", "vendor/lib/CLAUDE.md", "src/app.py")


@pytest.fixture
async def app(
    db_with_migrations: DatabasePool,
    stub_container_runtime: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Any]:
    """The real app through its real lifespan, its organizations' forge faked: ``acme`` with two repositories
    and ``globex`` with one, each holding the same files."""
    forge = FakeForge()
    for repo in (WIDGETS, GIZMOS, GADGETS):
        forge.put(repo, HEAD, {path: hashlib.sha1(path.encode()).hexdigest() for path in FILES})

    async def forge_client(_request: Any, _org: Any) -> FakeForge:
        return forge

    # Before the lifespan, which binds the client the loop reads the forge with.
    monkeypatch.setattr("embry0.api.v1.organizations.org_forge_client", forge_client)
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
        for owner in ("acme", "globex"):
            await app.state.organizations_repo.register(owner, "github")
        for repo in (WIDGETS, GIZMOS, GADGETS):
            await app.state.repos_repo.register(repo, forge="github")
        yield app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=HEADERS)


async def _put(app: Any, text: str, **params: str) -> Any:
    async with _client(app) as client:
        return await client.put(URL, content=text, headers={"Content-Type": "application/yaml"}, params=params)


async def _files(app: Any, owner: str) -> dict[str, int]:
    """Each repository of ``owner`` to how many instruction files the production schedule selects in it."""
    async with _client(app) as client:
        resp = await client.get(f"/api/v1/orgs/{owner}/context-audit", params={"refresh": "true"})
    assert resp.status_code == 200, resp.text
    return {row["repo"]: row["files"] for row in resp.json()["repos"]}


async def test_each_repository_is_read_with_the_files_its_layers_name_and_the_cadence_the_deployment_s(
    app: Any,
) -> None:
    assert await _files(app, "acme") == {WIDGETS: 2, GIZMOS: 2}, "the built-in list: CLAUDE.md, HANDOFF.md, not vendor"
    org_layer = await _put(app, 'context_audit:\n  files: ["**/CLAUDE.md", "**/CONVENTIONS.md"]\n', owner="acme")
    assert org_layer.status_code == 200, f"the organization's layer refused its own files: {org_layer.text}"
    repo_layer = await _put(app, "context_audit:\n  exclude_dirs: []\n", repo=WIDGETS)
    assert repo_layer.status_code == 200, f"the repository's layer refused its own directories: {repo_layer.text}"

    assert await _files(app, "acme") == {WIDGETS: 4, GIZMOS: 3}, (
        "the production schedule does not read each repository's files through its layers: acme/gizmos reads "
        "the organization's three, acme/widgets them and vendor/lib/CLAUDE.md, with no directory skipped"
    )
    assert await _files(app, "globex") == {GADGETS: 2}, "another organization still reads the built-in list"

    loop = app.state.context_audit
    assert getattr(loop, "interval_seconds", None) is not None, "the production loop reads no interval"
    assert await loop.interval_seconds() == 900, "nothing written: a wake every 15 minutes, as before"
    deployment = await _put(app, "context_audit:\n  check_minutes: 60\n  read_concurrency: 3\n")
    assert deployment.status_code == 200, deployment.text
    assert await loop.interval_seconds() == 3600, "the running loop does not read the deployment's check_minutes"
    assert (await loop.settings()).read_concurrency == 3, "nor its read_concurrency"


async def test_a_value_the_section_refuses_is_a_422_naming_the_key_and_nothing_is_stored(app: Any) -> None:
    bad_glob = await _put(app, 'context_audit:\n  files: ["**/CLAUDE.md", "/AGENTS.md"]\n', owner="acme")
    assert bad_glob.status_code == 422, bad_glob.text
    errors = bad_glob.json()["detail"]
    assert [e["loc"] for e in errors] == [["context_audit", "files"]] and "glob 2" in errors[0]["msg"], (
        f"the 422 must name the key and which glob: {errors}"
    )
    paced = await _put(app, "context_audit:\n  check_minutes: 30\n", owner="acme")
    assert paced.status_code == 422, paced.text
    assert ["context_audit", "check_minutes"] in [e["loc"] for e in paced.json()["detail"]], (
        f"an organization may not pace the deployment's schedule: {paced.text}"
    )
    assert await _files(app, "acme") == {WIDGETS: 2, GIZMOS: 2}, "a refused write changed what the schedule reads"
