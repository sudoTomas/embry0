"""A repository's areas are read from embry0, never from the repository (spec 2026-11-10, #175).

- No code names the old areas file but the one-time import (and the
  migrations, which are history).
- Production gives every reader the store (the guardrail on arguments
  production cannot work without, #369): the leads, the scout, the
  Slop-O-Meter and the executor, through the real lifespan.
- The owner proposals feed and the decisions name a lead's area and ask the
  area's owners from the stored document, with every forge call refused.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from tests.fixtures.repository_writes import COVERED, MASTER, call, repository_writes_app
from tests.unit.metrics.test_lifespan_wiring import booted, stub_container_runtime  # noqa: F401 - fixtures

ROOT = pathlib.Path(__file__).resolve().parents[3]
#: The one module that may still name the old file: the migrations, which are history. The import is gone (#175).
MAY_NAME_THE_FILE = {"embry0/storage/migrations/runner.py"}


def test_no_code_names_the_repository_s_old_areas_file() -> None:
    named = sorted(
        str(path.relative_to(ROOT))
        for path in (ROOT / "embry0").rglob("*.py")
        if ".embry0/areas" in path.read_text() and str(path.relative_to(ROOT)) not in MAY_NAME_THE_FILE
    )
    assert named == [], f"code that names the repository's old areas file: {named}"


def test_a_repository_s_files_are_read_only_for_its_codeowners() -> None:
    """embry0 reads no configuration of its own from a target repository (#175): the one reader of a repository's
    files through the forge is the owners fallback, which reads CODEOWNERS with ``read_contents``."""
    readers = sorted(
        (str(path.relative_to(ROOT)), alias.name)
        for path in (ROOT / "embry0").rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.ImportFrom) and node.module == "embry0.services.forge_files"
        for alias in node.names
    )
    assert readers == [("embry0/api/v1/proposals.py", "read_contents")], f"what reads a repository's files: {readers}"


@pytest.mark.requires_postgres
async def test_production_gives_every_reader_the_areas_embry0_keeps(booted: Any) -> None:  # noqa: F811
    state = booted.state
    store = state.areas
    assert state.leads_loop.areas_for == store.areas_for, "the leads read the store"
    assert state.scout.areas_for == store.areas_for, "the scout reads the store"
    assert state.slop_meter._areas_of == store.areas_of, "the Slop-O-Meter reads the store"  # noqa: SLF001
    assert state.issue_executor._areas is store, "the pull requests embry0 opens read the store"  # noqa: SLF001
    assert state.issue_executor._areas_proposals is state.repo_areas_proposals_repo, (  # noqa: SLF001
        "a map's document waits on its proposal"
    )
    assert getattr(state, "areas_import", None) is None, "no import of a repository's old areas file runs (#175)"


@pytest.mark.requires_postgres
@pytest.mark.usefixtures("stub_container_runtime")
async def test_the_start_names_the_repositories_whose_old_areas_file_was_never_read(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lifespan runs the check (embry0.services.areas_import_check): a repository migration 145 listed and no
    import ever read is logged at error level and audited."""
    from structlog.testing import capture_logs

    from embry0.api.app import create_app, lifespan
    from tests.unit.api.test_settings_lifespan import _config

    await db_with_migrations.execute("INSERT INTO repos (repo) VALUES ('acme/widgets') ON CONFLICT DO NOTHING")
    await db_with_migrations.execute(
        "INSERT INTO repo_areas_imports (repo, outcome) VALUES ('acme/widgets', 'pending') ON CONFLICT DO NOTHING"
    )
    monkeypatch.setattr("embry0.observability.configure_structlog", lambda **kw: None)  # keep capture_logs
    app = create_app(_config(db_with_migrations))
    with capture_logs() as logs:
        async with lifespan(app):
            pass
    said = [(e["log_level"], e.get("repos")) for e in logs if e["event"] == "areas_import_skipped"]
    assert said == [("error", ["acme/widgets"])], f"the start must name the repository never read: {said}"


@pytest.mark.requires_postgres
async def test_the_feed_and_the_decisions_name_the_stored_area_and_ask_its_owners(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with repository_writes_app(db_with_migrations, monkeypatch) as (app, _keys, _values):
        put = await call(
            app,
            "PUT",
            f"/api/v1/repos/{COVERED}/-/areas",
            MASTER,
            "areas:\n  - name: Storefront\n    paths: [src/**]\n    owners: [octo]\n",
        )
        assert put.status_code == 200, put.text
        feed = await call(app, "GET", "/api/v1/proposals", MASTER)
        decisions = await call(app, "GET", "/api/v1/decisions?stage=offered", MASTER)
    assert feed.status_code == 200 and decisions.status_code == 200, (feed.text, decisions.text)
    (lead,) = [p for p in feed.json()["proposals"] if p["repo"] == COVERED and p["kind"] == "ci"]
    assert (lead["area"], lead["owners"]) == ("Storefront", ["octo"]), lead
    (offered,) = [d for d in decisions.json()["decisions"] if d["repo"] == COVERED and d["id"].startswith("offer:ci")]
    assert (offered["area"], offered["owners"]) == ("Storefront", ["octo"]), offered
