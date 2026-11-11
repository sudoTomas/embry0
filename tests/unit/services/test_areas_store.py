"""Each repository's areas as embry0 keeps them (spec 2026-11-10): the store, a map's life cycle and owners."""

from __future__ import annotations

from typing import Any

import pytest

from embry0.services.areas import Area
from embry0.services.areas_document import DocumentInvalid, load_yaml
from embry0.services.areas_proposals import finish_map
from embry0.services.areas_store import AreasStore
from embry0.services.owners import Owners, owners_for, repository_owners
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.repo_areas import AreasChanged
from embry0.storage.repositories.repo_areas_proposals import RepoAreasProposalsRepository
from embry0.storage.repositories.repo_config_revisions import RepoConfigRevisionsRepository

REPO = "acme/Widgets"
V1 = "# ours\nareas:\n  - name: Web\n    paths: [web/]\n    owners: ['@octo']\n"
V2 = "areas:\n  - name: Web\n    paths: [web/**]\n  - name: Api\n    paths: [api/**]\n"


async def _registered(db: DatabasePool, *repos: str) -> None:
    for repo in repos:
        await db.execute("INSERT INTO repos (repo, forge) VALUES ($1, 'github')", repo)


async def _save(store: AreasStore, text: str, **kwargs: Any) -> Any:
    kwargs.setdefault("expected", None)
    kwargs.setdefault("actor", "ada@example.com")
    kwargs.setdefault("source", "person")
    return await store.save(REPO, text, load_yaml(text), **kwargs)


@pytest.mark.requires_postgres
async def test_a_write_is_stored_as_written_and_read_as_its_values(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _registered(db, REPO)
    store = AreasStore(db)
    empty = await store.get(REPO)
    assert (empty.generation, empty.yaml_text, empty.areas, empty.stored) == (0, "", [], False)

    saved = await _save(store, V1)
    assert (saved.generation, saved.would_change) == (1, True) and saved.revision_id is not None
    stored = await store.get(REPO)
    assert stored.yaml_text == V1, "the text is kept byte for byte, its comment included"
    assert stored.areas == [Area("Web", ("web/**",), (), ("octo",))]
    again = await _save(store, V1)
    assert (again.generation, again.revision_id) == (1, None), "an unchanged write records nothing"

    second = await _save(store, V2, expected={1})
    assert second.generation == 2
    with pytest.raises(AreasChanged) as raised:
        await _save(store, V1, expected={1})
    assert raised.value.generation == 2, "a write against a generation that moved is refused"
    revisions = await RepoConfigRevisionsRepository(db).list_for_repo(REPO)
    assert [(r["surface"], r["source"], r["actor"]) for r in revisions] == [("areas", "person", "ada@example.com")] * 2
    assert revisions[-1]["before"] is None and revisions[-1]["after"]["yaml_text"] == V1


@pytest.mark.requires_postgres
async def test_a_dry_run_checks_and_compares_and_stores_nothing(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _registered(db, REPO)
    store = AreasStore(db)
    await _save(store, V1)
    preview = await _save(store, V2, dry_run=True)
    assert (preview.generation, preview.revision_id, preview.would_change) == (1, None, True)
    assert {(c["area"], c["change"]) for c in preview.changes} == {("Api", "added"), ("Web", "changed")}
    assert preview.text_diff.startswith("--- stored\n+++ proposed\n")
    assert (await store.get(REPO)).yaml_text == V1
    with pytest.raises(DocumentInvalid):
        await _save(store, "areas:\n  - name: Web\n  - name: web\n", dry_run=True)


@pytest.mark.requires_postgres
async def test_readers_read_by_any_spelling_in_one_query(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _registered(db, REPO, "acme/gadgets")
    store = AreasStore(db)
    await _save(store, V2)
    found = await store.areas_of(["ACME/widgets", "acme/gadgets", "acme/nobody"])
    assert found == {
        "acme/widgets": [Area("Web", ("web/**",)), Area("Api", ("api/**",))],
        "acme/gadgets": [],
        "acme/nobody": [],
    }
    assert await store.areas_for("acme/WIDGETS") == found["acme/widgets"]


@pytest.mark.requires_postgres
async def test_a_renamed_repository_reads_its_areas_under_either_name(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from embry0.storage import repo_renames

    db = db_with_migrations
    await _registered(db, REPO)
    store = AreasStore(db)
    await _save(store, V2)
    monkeypatch.setattr(repo_renames, "REPO_RENAMES", {REPO.lower(): "acme/widgets-next"})
    assert [a.name for a in await store.areas_for("acme/widgets-next")] == ["Web", "Api"]
    assert [a.name for a in await store.areas_for(REPO)] == ["Web", "Api"]


@pytest.mark.requires_postgres
async def test_a_restore_puts_back_what_a_change_replaced_while_it_is_still_stored(
    db_with_migrations: DatabasePool,
) -> None:
    db = db_with_migrations
    await _registered(db, REPO)
    store = AreasStore(db)
    await _save(store, V1)
    await _save(store, V2)
    revisions = await RepoConfigRevisionsRepository(db).list_for_repo(REPO)
    newest, first = revisions[0], revisions[1]
    with pytest.raises(AreasChanged):
        await store.restore(REPO, first["before"], after=first["after"], actor="ada", reason="undo")
    revision_id = await store.restore(REPO, newest["before"], after=newest["after"], actor="ada", reason="undo")
    assert revision_id is not None and (await store.get(REPO)).yaml_text == V1
    undo = (await RepoConfigRevisionsRepository(db).list_for_repo(REPO))[0]
    assert (undo["source"], undo["reason"]) == ("restore", "undo")


@pytest.mark.requires_postgres
async def test_undoing_the_write_that_created_the_areas_stores_none_at_a_new_generation(
    db_with_migrations: DatabasePool,
) -> None:
    """#458's review: the undo kept no row, so the next write was generation 1 again and a version read before
    the undo matched a document written after it."""
    db = db_with_migrations
    await _registered(db, REPO)
    store = AreasStore(db)
    created = await _save(store, V1)
    (revision,) = await RepoConfigRevisionsRepository(db).list_for_repo(REPO)
    assert await store.restore(REPO, None, after=revision["after"], actor="ada", reason="undo") is not None
    undone = await store.get(REPO)
    assert await store.areas_for(REPO) == [] and undone.yaml_text == ""
    assert undone.generation > created.generation, "an undo moves the generation on, as any write does"
    written = await _save(store, V2)
    assert written.generation > undone.generation
    with pytest.raises(AreasChanged):
        await _save(store, V1, expected={created.generation})


@pytest.mark.requires_postgres
async def test_a_repository_registered_again_never_reuses_a_generation(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _registered(db, REPO)
    store = AreasStore(db)
    await _save(store, V1)
    before = await _save(store, V2)
    await db.execute("DELETE FROM repos WHERE repo = $1", REPO)
    await _registered(db, REPO)
    assert (await store.get(REPO)).generation == 0, "the areas went with the repository"
    after = await _save(store, V2)
    assert after.generation > before.generation, (
        "a version read before the repository was removed matches nothing written after it is registered again"
    )


@pytest.mark.requires_postgres
async def test_an_empty_first_write_stores_nothing(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _registered(db, REPO)
    store = AreasStore(db)
    saved = await store.save(REPO, "", None, expected=None, actor="ada", source="person")
    assert (saved.generation, saved.revision_id, saved.would_change) == (0, None, False), (
        f"an empty text where no areas are stored was stored as a change: {saved}"
    )


JOB = "job-map-1"


async def _proposal(db: DatabasePool, store: AreasStore) -> tuple[RepoAreasProposalsRepository, str]:
    proposals = RepoAreasProposalsRepository(db)
    proposal_id = await proposals.create(REPO, base_generation=(await store.get(REPO)).generation, created_by="ada")
    await proposals.attach_job(proposal_id, JOB)
    return proposals, proposal_id


@pytest.mark.requires_postgres
@pytest.mark.parametrize(
    ("document", "failure", "status", "says"),
    [
        (V2, None, "pending", ""),
        (V1, None, "unchanged", ""),
        (None, None, "failed", "carries no areas document"),
        ("areas:\n  - name: Web\n    paths: [/web]\n", None, "failed", "areas.0.paths.0:"),
        (V2, "the budget ran out", "failed", "embry0 stopped before it finished: the budget ran out"),
    ],
    ids=["new areas wait", "the same areas", "no document", "a document refused", "the job failed"],
)
async def test_a_map_ends_as_its_report_says(
    db_with_migrations: DatabasePool, document: str | None, failure: str | None, status: str, says: str
) -> None:
    db = db_with_migrations
    await _registered(db, REPO)
    store = AreasStore(db)
    await _save(store, V1)
    proposals, proposal_id = await _proposal(db, store)
    finished = await finish_map(
        store, proposals, proposal_id, job_id=JOB, repo=REPO, document=document, notes="n", failure=failure
    )
    assert finished == status
    row = await proposals.get(proposal_id)
    assert row is not None and (row["status"], row["proposed_text"], row["notes_md"]) == (status, document, "n")
    assert says in row["failure"], row["failure"]
    assert "/web" not in row["failure"], "a refused glob is not echoed"
    again = await finish_map(store, proposals, proposal_id, job_id=JOB, repo=REPO, document=V2, notes="", failure=None)
    assert again is None, "a map finishes once"


class _Executor:
    """What :func:`finish_areas_map` reads of the executor: the proposals, the store and the jobs."""

    def __init__(self, db: DatabasePool, jobs: dict[str, dict[str, Any]]) -> None:
        self._areas_proposals = RepoAreasProposalsRepository(db)
        self._areas = AreasStore(db)
        self._rows = jobs

    @property
    def _jobs(self) -> Any:
        rows = self._rows

        class _Jobs:
            async def get(self, job_id: str) -> dict[str, Any] | None:
                return rows.get(job_id)

        return _Jobs()


@pytest.mark.requires_postgres
@pytest.mark.parametrize(
    ("job_id", "repo"),
    [("job-other", REPO), (JOB, "globex/gizmos")],
    ids=["another job", "another repository's job"],
)
async def test_a_job_finishes_only_its_own_repository_s_map(
    db_with_migrations: DatabasePool, job_id: str, repo: str
) -> None:
    """#458's review: a job whose pipeline_config named another repository's map stored its document there."""
    from embry0.services._executor_outcomes import finish_areas_map

    db = db_with_migrations
    await _registered(db, REPO)
    store = AreasStore(db)
    proposals, proposal_id = await _proposal(db, store)
    executor = _Executor(db, {job_id: {"job_id": job_id, "repo": repo}})
    await finish_areas_map(executor, job_id, proposal_id, {"areas_document": V2}, None)  # type: ignore[arg-type]
    row = await proposals.get(proposal_id)
    assert row is not None and (row["status"], row["proposed_text"]) == ("running", None), (
        f"a job that is not the map's own finished it: {row and row['status']}"
    )
    own = _Executor(db, {JOB: {"job_id": JOB, "repo": REPO.upper()}})
    await finish_areas_map(own, JOB, proposal_id, {"areas_document": V2}, None)  # type: ignore[arg-type]
    row = await proposals.get(proposal_id)
    assert row is not None and row["status"] == "pending", "the map's own job finishes it"


def test_an_area_s_owners_come_first_then_the_repository_s_then_codeowners() -> None:
    repository = repository_owners(
        ["acme/a", "acme/b", "acme/c"], {"acme/a": ["octo"]}, {"acme/a": ["x"], "acme/b": ["hubot"]}
    )
    assert repository == {
        "acme/a": Owners(("octo",), "repository"),
        "acme/b": Owners(("hubot",), "codeowners"),
        "acme/c": Owners(),
    }
    billing = Area("Billing", owners=("ada",))
    assert owners_for(billing, repository["acme/a"]) == Owners(("ada",), "area")
    assert owners_for(Area("Search"), repository["acme/b"]) == Owners(("hubot",), "codeowners")
    assert owners_for(None, repository["acme/c"]) == Owners()
