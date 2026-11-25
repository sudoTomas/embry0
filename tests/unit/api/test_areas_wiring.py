"""A repository's areas are read from embry0, never from the repository (spec 2026-11-10, #175).

- No code names the old areas file but the migrations, which are history,
  in any form: pieced together, in a constant, URL-encoded as GitLab's files
  API wants it. No code but the owners fallback takes anything from the
  forge files helper, however it imports it, and the modules that read a
  repository's files through a forge are a known few, each reading files the
  repository keeps for itself.
- Production gives every reader the store (the guardrail on arguments
  production cannot work without, #369): the leads, the scout, the
  Slop-O-Meter and the executor, through the real lifespan.
- The owner proposals feed and the decisions name a lead's area and ask the
  area's owners from the stored document, with every forge call refused.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from tests.fixtures.repository_writes import COVERED, MASTER, call, repository_writes_app
from tests.unit.metrics.test_lifespan_wiring import booted, stub_container_runtime  # noqa: F401 - fixtures

ROOT = pathlib.Path(__file__).resolve().parents[3]
#: The migrations, which are history, may name the old file. The import is gone (#175).
MIGRATIONS = "embry0/storage/migrations/runner.py"
#: A module that may spell an areas file's name, with why: never the repository's.
MAY_SPELL_AN_AREAS_FILE = {
    MIGRATIONS: "history",
    "embry0/cli/areas.py": "the operator's own file on disk, which the CLI checks, diffs and applies",
}
#: The forge APIs that read a repository's files: a file, a tree, a blob, a raw file or the whole archive.
READS_FILES = (
    "/contents",
    "/repository/files",
    "/repository/tree",
    "/repository/blobs",
    "/repository/archive",
    "/git/trees",
    "/git/blobs",
    "raw.githubusercontent",
    "/-/raw/",
    "/zipball",
    "/tarball",
    # GraphQL: GitHub's `object(expression: "HEAD:path") { ... on Blob { text } }`, GitLab's
    # `repository { blobs(paths: [...]) { nodes { rawTextBlob } } }`.
    "object(expression",
    "... on blob",
    "blobs(paths",
    "rawblob",
    "rawtextblob",
)
#: Every module that reads a repository's files through a forge, and what it reads: files the repository keeps for
#: itself, never embry0's configuration (#175). A new one is added here, with its reason, in review.
KNOWN_READERS = {
    "embry0/services/forge_files.py": "the helper: one file of the default branch (the owners fallback's CODEOWNERS)",
    "embry0/api/v1/environment.py": "the repository's env templates, to detect the variables it needs",
    "embry0/services/org_discovery.py": "what the repository holds (its listing, package.json, an env file)",
    "embry0/services/context_audit.py": "the repository's tree of blob shas, for the context audit's fingerprint",
}


#: The forge files helper's public names: one taken from a module that passes it on is still the helper's.
_HELPER_NAMES = tuple(
    sorted(
        node.name
        for node in ast.parse((ROOT / "embry0/services/forge_files.py").read_text()).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not node.name.startswith("_")
    )
)


def _modules() -> list[tuple[str, pathlib.Path]]:
    return sorted((str(path.relative_to(ROOT)), path) for path in (ROOT / "embry0").rglob("*.py"))


def test_no_code_names_the_repository_s_old_areas_file() -> None:
    """In any form: spelled, pieced together, held in a constant, joined as a path or URL-encoded (GitLab's files
    API reads ``.embry0%2Fareas.yml``)."""
    from tests.fixtures.source_strings import Tree, spells

    tree = Tree(ROOT)
    named = [
        name
        for name, path in _modules()
        if name != MIGRATIONS and spells(path.read_text(), ".embry0/areas", tree=tree, path=path)
    ]
    assert named == [], f"code that names the repository's old areas file: {named}"
    spelled = [
        name
        for name, path in _modules()
        if name not in MAY_SPELL_AN_AREAS_FILE and spells(path.read_text(), "areas.y", tree=tree, path=path)
    ]
    assert spelled == [], f"code that spells an areas file's name: {spelled}"


def test_nothing_but_the_owners_fallback_takes_from_the_forge_files_helper() -> None:
    """However it is reached: ``from … import``, the module itself, a relative import, by name, as an attribute of
    its package, or one of its names taken from a module that passes it on."""
    from tests.fixtures.source_strings import imports_of

    helper = "embry0/services/forge_files.py"
    readers = sorted(
        (name, imported)
        for name, path in _modules()
        if name != helper
        for imported in imports_of(path, ROOT, "embry0.services.forge_files", _HELPER_NAMES)
    )
    assert readers == [("embry0/api/v1/proposals.py", "read_contents")], f"what uses the forge files helper: {readers}"


def test_a_repository_s_files_are_read_only_by_the_known_readers() -> None:
    """embry0 reads no configuration of its own from a target repository (#175). What reads a repository's files
    through a forge reads files the repository keeps for itself: :data:`KNOWN_READERS`, and nothing else."""
    from tests.fixtures.source_strings import Tree, spells

    tree = Tree(ROOT)
    readers = [name for name, path in _modules() if spells(path.read_text(), *READS_FILES, tree=tree, path=path)]
    assert readers == sorted(KNOWN_READERS), f"what reads a repository's files through a forge: {readers}"


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
    """The lifespan runs the check (embry0.services.areas_import_check), in the background: a repository migration
    145 listed and no import ever read is logged at error level and audited."""
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
            check = getattr(app.state, "areas_import_check", None)
            if check is not None:
                await asyncio.wait_for(check, timeout=30)
    said = [(e["log_level"], e.get("due_repos")) for e in logs if e["event"] == "areas_import_skipped"]
    assert said == [("error", ["acme/widgets"])], f"the start must name the repository never read: {said}"


@pytest.mark.requires_postgres
@pytest.mark.usefixtures("stub_container_runtime")
async def test_the_start_never_waits_on_the_check(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A check held up (a lock on the ledger, a slow plan) holds up nothing else: the start goes on, and the check
    is cancelled at shutdown."""
    from embry0.api.app import create_app, lifespan
    from tests.unit.api.test_settings_lifespan import _config

    reached = asyncio.Event()

    async def held(db: Any, **kwargs: Any) -> Any:
        reached.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("embry0.services.areas_import_check.unread", held)
    app = create_app(_config(db_with_migrations))
    running = lifespan(app)
    try:
        await asyncio.wait_for(running.__aenter__(), timeout=60)
    except TimeoutError:
        pytest.fail("the start waited on the areas import check")
    try:
        await asyncio.wait_for(reached.wait(), timeout=10)
        task = app.state.areas_import_check
        assert not task.done(), "the check ended though its query never answered"
    finally:
        await running.__aexit__(None, None, None)
    assert task.cancelled(), "the check outlived the shutdown"


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
