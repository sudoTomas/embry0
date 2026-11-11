"""The one-time import of each repository's old areas file (spec 2026-11-10): once, then never read again."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from embry0.services.areas import Area
from embry0.services.areas_document import check, load_yaml
from embry0.services.areas_import import ACTOR, AREAS_PATH, import_once, import_text, parse_areas
from embry0.services.areas_store import AreasStore
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.repo_areas_imports import RepoAreasImportsRepository
from embry0.storage.repositories.repo_config_revisions import RepoConfigRevisionsRepository
from embry0.storage.repositories.repos import ReposRepository

OLD = """# Our areas
areas:
  - name: Billing
    paths: ["services/billing/**", "web/apps/billing/"]
    logs: billing-api
    owners: ["@octo", "not a login!", "hubot"]
  - name: Search
    paths: ["services/search/**"]
  - paths: ["nameless/**"]
  - name: "  "
  - Plain string
"""
VALID = 'areas:\n  - name: Billing\n    paths: ["services/billing/**"]  # the service\n'
TODAY = datetime(2026, 11, 11, tzinfo=UTC)


def test_the_old_reader_reads_the_file_leniently() -> None:
    billing, search = parse_areas(OLD)
    assert billing == Area("Billing", ("services/billing/**", "web/apps/billing/"), ("billing-api",), ("octo", "hubot"))
    assert search == Area("Search", ("services/search/**",))
    for broken in (None, "", "areas: [", "- a list", "areas: nope"):
        assert parse_areas(broken) == []


def test_a_file_the_document_accepts_is_stored_as_written() -> None:
    imported = import_text(VALID, blob_sha="b1", today="2026-11-11")
    assert imported is not None and imported.text == VALID and imported.left_out == []
    assert import_text("areas: []\n", blob_sha=None, today="2026-11-11") is None, "a file naming no area"
    assert import_text("areas: [\n", blob_sha=None, today="2026-11-11") is None, "a file that is not YAML"


def test_a_file_the_document_refuses_is_stored_as_the_old_reader_read_it() -> None:
    text = OLD.replace('"services/search/**"', '"services/search**", "/services/search/**"')
    imported = import_text(text, blob_sha="b2", today="2026-11-11")
    assert imported is not None
    assert check(load_yaml(imported.text)) == {
        "areas": [
            {
                "name": "Billing",
                "paths": ["services/billing/**", "web/apps/billing/**"],
                "logs": ["billing-api"],
                "owners": ["octo", "hubot"],
            },
            {"name": "Search", "paths": ["services/search/**"], "logs": [], "owners": []},
        ]
    }, "what the old reader acted on: owners not logins dropped, a leading / dropped"
    header = imported.text.split("areas:", 1)[0]
    assert "Imported by embry0 on 2026-11-11" in header and AREAS_PATH in header and "blob b2" in header
    assert "Search: the path 'services/search**'" in header, "what was left out is named, and why"
    assert imported.left_out == ["Search: the path 'services/search**', which is not a glob embry0 reads"]


async def _registered(db: DatabasePool, repos: dict[str, str]) -> None:
    for repo, forge in repos.items():
        await db.execute("INSERT INTO repos (repo, forge) VALUES ($1, $2)", repo, forge)
        await db.execute("INSERT INTO repo_areas_imports (repo, outcome) VALUES ($1, 'pending')", repo)


@pytest.mark.requires_postgres
async def test_each_listed_repository_is_read_once_and_never_again(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _registered(
        db,
        {
            "acme/file": "github",
            "acme/none": "github",
            "acme/down": "github",
            "acme/written": "github",
            "acme/lab": "gitlab",
        },
    )
    await db.execute("INSERT INTO repos (repo, forge) VALUES ('acme/later', 'github')")
    store = AreasStore(db)
    await store.save("acme/written", VALID, load_yaml(VALID), expected=None, actor="ada", source="person")
    files = {"acme/file": (OLD, "sha-1"), "acme/written": (VALID, "sha-2"), "acme/later": (VALID, "sha-3")}
    asked: list[str] = []
    down = [True]
    recorded: list[dict[str, Any]] = []

    async def read(repo: str) -> tuple[str, str | None] | None:
        asked.append(repo)
        if repo == "acme/down" and down[0]:
            raise RuntimeError("the forge is down")
        return files.get(repo)

    async def record(repo: str, details: dict[str, Any]) -> None:
        recorded.append(details)

    ledger = RepoAreasImportsRepository(db)
    run = {"ledger": ledger, "store": store, "repos": ReposRepository(db), "read": read, "record": record}
    outcomes = await import_once(**run, clock=lambda: TODAY)
    assert outcomes == {
        "acme/file": "imported",
        "acme/none": "none",
        "acme/down": "failed",
        "acme/written": "none",
        "acme/lab": "none",
    }
    assert sorted(asked) == ["acme/down", "acme/file", "acme/none"], (
        "only the GitHub repositories with no areas yet are read, and never one registered later"
    )
    assert [a.name for a in await store.areas_for("acme/file")] == ["Billing", "Search"]
    assert (await store.get("acme/written")).yaml_text == VALID, "a person's areas are never overwritten"
    (revision,) = await RepoConfigRevisionsRepository(db).list_for_repo("acme/file")
    assert (revision["source"], revision["actor"]) == ("import", ACTOR)
    assert revision["reason"] == f"imported from {AREAS_PATH} (blob sha-1)"
    assert recorded == [{"repo": "acme/file", "revision_id": revision["id"], "left_out": 0}]
    down_row = await ledger.get("acme/down")
    assert down_row is not None and "the forge is down" in down_row["detail"] and down_row["attempts"] == 1

    asked.clear()
    down[0] = False
    assert await import_once(**run, clock=lambda: TODAY) == {"acme/down": "none"}, "a failed read is tried again"
    assert asked == ["acme/down"]
    asked.clear()
    assert await import_once(**run, clock=lambda: TODAY) == {}
    assert asked == [], "once every listed repository is read, nothing is read again"


def test_a_bracket_the_old_reader_matched_as_itself_still_names_that_directory() -> None:
    """#458's review: the old reader matched '[' as itself; the document's grammar opens a class with it."""
    from embry0.services.areas import area_of_path
    from embry0.services.areas_document import areas_of

    text = 'areas:\n  - name: Item\n    paths: ["web/app/[id]/", "web/app/[slug]/**"]\n'
    imported = import_text(text, blob_sha="b3", today="2026-11-11")
    assert imported is not None
    areas = areas_of(check(load_yaml(imported.text)))
    assert [a.paths for a in areas] == [("web/app/[[]id]/**", "web/app/[[]slug]/**")], (
        f"the import stores globs that name other paths than the old reader matched:\n{imported.text}"
    )
    assert area_of_path(areas, "web/app/[id]/page.tsx") is not None, "the route directory is still the area's"
    assert area_of_path(areas, "web/app/i/page.tsx") is None, "and nothing the class would have matched"
    assert "written [[]" in imported.text.split("areas:", 1)[0], "the header says why the text was rewritten"


@pytest.mark.requires_postgres
async def test_two_imports_at_once_record_the_one_that_stored_the_areas(db_with_migrations: DatabasePool) -> None:
    """Two orchestrators read a repository at once: one stores its areas, and the ledger says so, once."""
    db = db_with_migrations
    await _registered(db, {"acme/file": "github"})
    store = AreasStore(db)
    arrived: list[str] = []
    both = asyncio.Event()
    one_done = asyncio.Event()

    async def read(repo: str) -> tuple[str, str | None] | None:
        arrived.append(repo)
        if len(arrived) == 2:
            both.set()
        await asyncio.wait_for(both.wait(), timeout=10)
        return OLD, "sha-1"

    async def record(repo: str, details: dict[str, Any]) -> None:
        # Only the import that stored the areas gets here; it waits until the other has finished, so the other
        # records its outcome first, the order that left the ledger saying "none".
        await asyncio.wait_for(one_done.wait(), timeout=10)

    run = {"store": store, "repos": ReposRepository(db), "read": read, "record": record}

    async def once() -> dict[str, str]:
        outcomes = await import_once(ledger=RepoAreasImportsRepository(db), **run, clock=lambda: TODAY)
        one_done.set()
        return outcomes

    first, second = await asyncio.gather(once(), once())
    assert sorted([first["acme/file"], second["acme/file"]]) == ["imported", "none"]
    row = await RepoAreasImportsRepository(db).get("acme/file")
    assert row is not None and (row["outcome"], row["attempts"], row["blob_sha"]) == ("imported", 1, "sha-1"), (
        f"the ledger does not name the import that stored the areas: {row}"
    )


class _Response:
    def __init__(self, status: int, body: Any = None) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        return self._body


async def test_a_repository_the_forge_hides_is_read_again_not_taken_as_having_no_file() -> None:
    """GitHub answers 404 for a private repository the credential cannot see, as for a file that is not there."""
    from embry0.services.forge_files import ForgeReadFailed, read_file

    async def hidden(url: str) -> _Response:
        return _Response(404)

    async def shown(url: str) -> _Response:
        return _Response(404) if "/contents/" in url else _Response(200, {"full_name": "acme/file"})

    with pytest.raises(ForgeReadFailed):
        await read_file(hidden, "acme/file", AREAS_PATH)
    assert await read_file(shown, "acme/file", AREAS_PATH) is None, "a repository that reads has no such file"


@pytest.mark.requires_postgres
async def test_a_read_that_keeps_failing_is_given_up_after_24_reads(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _registered(db, {"acme/down": "github"})
    reads: list[str] = []

    async def read(repo: str) -> tuple[str, str | None] | None:
        reads.append(repo)
        raise RuntimeError("the forge does not show it")

    async def record(repo: str, details: dict[str, Any]) -> None:
        return None

    ledger = RepoAreasImportsRepository(db)
    run = {"store": AreasStore(db), "repos": ReposRepository(db), "read": read, "record": record}
    for _ in range(30):
        if not await ledger.due():
            break
        await import_once(ledger=ledger, **run, clock=lambda: TODAY)
    assert len(reads) == 24, f"a read that fails every time was tried {len(reads)} times, not 24"


@pytest.mark.requires_postgres
async def test_a_failed_read_is_retried_at_later_passes_a_bounded_number_of_times(
    db_with_migrations: DatabasePool,
) -> None:
    from embry0.services.areas_import import import_passes
    from embry0.storage.repositories.repo_areas_imports import MAX_ATTEMPTS

    db = db_with_migrations
    await _registered(db, {"acme/down": "github"})
    reads: list[str] = []
    slept: list[float] = []

    async def read(repo: str) -> tuple[str, str | None] | None:
        reads.append(repo)
        raise RuntimeError("the forge does not show it")

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    async def record(repo: str, details: dict[str, Any]) -> None:
        return None

    ledger = RepoAreasImportsRepository(db)
    await import_passes(
        ledger=ledger,
        every=60.0,
        sleep=sleep,
        store=AreasStore(db),
        repos=ReposRepository(db),
        read=read,
        record=record,
        clock=lambda: TODAY,
    )
    assert len(reads) == MAX_ATTEMPTS and slept == [60.0] * (MAX_ATTEMPTS - 1), (len(reads), len(slept))
    row = await ledger.get("acme/down")
    assert row is not None and (row["outcome"], row["attempts"]) == ("failed", MAX_ATTEMPTS)
    assert await ledger.due() == [], "a read that failed every time is not read again"
