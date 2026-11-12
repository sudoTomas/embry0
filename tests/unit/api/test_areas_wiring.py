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
#: The modules that may still name the old file: the import (until the release that removes it) and the
#: migrations, which are history.
MAY_NAME_THE_FILE = {"embry0/services/areas_import.py", "embry0/storage/migrations/runner.py"}


def test_no_code_names_the_repository_s_old_areas_file_but_the_import() -> None:
    named = sorted(
        str(path.relative_to(ROOT))
        for path in (ROOT / "embry0").rglob("*.py")
        if ".embry0/areas" in path.read_text() and str(path.relative_to(ROOT)) not in MAY_NAME_THE_FILE
    )
    assert named == [], f"code that names the repository's old areas file: {named}"


def test_only_the_import_reads_a_file_of_a_repository_for_its_areas() -> None:
    """The one module that reads a repository's files through the forge for areas is the import; the rest use
    ``read_contents`` for CODEOWNERS only."""
    readers = sorted(
        str(path.relative_to(ROOT))
        for path in (ROOT / "embry0").rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.ImportFrom)
        and node.module == "embry0.services.forge_files"
        and any(alias.name == "read_file" for alias in node.names)
    )
    assert readers == ["embry0/api/app.py"], f"what reads a repository's file with its blob: {readers}"


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
    assert state.areas_import is not None, "the one-time import runs in the background"


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
