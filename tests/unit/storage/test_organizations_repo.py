import os

import pytest

from embry0.services.org_discovery import RepoFacts
from embry0.storage.database import DatabasePool
from embry0.storage.migrations.runner import run_migrations
from embry0.storage.repositories.org_repos import WEBHOOK_DELIVERY_IDS_KEPT, OrgReposRepository
from embry0.storage.repositories.organizations import OrganizationsRepository

pytestmark = pytest.mark.requires_postgres


@pytest.fixture
async def orgs_repo() -> OrganizationsRepository:
    url = os.environ.get("TEST_DATABASE_URL", "postgresql://embry0:embry0@localhost:5432/embry0_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    await db.execute("DELETE FROM org_repo_facts")
    await db.execute("DELETE FROM org_discovery_runs")
    await db.execute("DELETE FROM organizations")
    yield OrganizationsRepository(db)
    await db.close()


@pytest.fixture
async def facts_repo() -> OrgReposRepository:
    url = os.environ.get("TEST_DATABASE_URL", "postgresql://embry0:embry0@localhost:5432/embry0_test")
    db = DatabasePool(url)
    await db.connect()
    await run_migrations(db)
    await db.execute("DELETE FROM org_repo_facts")
    await db.execute("DELETE FROM org_discovery_runs")
    yield OrgReposRepository(db)
    await db.close()


@pytest.mark.asyncio
async def test_register_is_idempotent_on_owner_and_lowercases(orgs_repo: OrganizationsRepository) -> None:
    first = await orgs_repo.register("Acme", "github", display_name="Acme Inc", created_by="tests")
    second = await orgs_repo.register("acme", "github", settings={"tracker": "github"})
    assert first.owner == "acme"
    assert second.display_name == "Acme Inc"
    assert second.settings == {"tracker": "github"}
    assert len(await orgs_repo.list_all()) == 1


@pytest.mark.asyncio
async def test_access_repo_count_status_and_removed_filter(orgs_repo: OrganizationsRepository) -> None:
    await orgs_repo.register("acme", "github")
    updated = await orgs_repo.update_access("acme", {"read": True, "hooks": False}, repo_count=12)
    assert updated is not None and updated.access == {"read": True, "hooks": False} and updated.repo_count == 12
    marked = await orgs_repo.mark_discovered("acme", repo_count=13)
    assert marked is not None and marked.repo_count == 13 and marked.discovered_at is not None
    removed = await orgs_repo.set_status("acme", "removed")
    assert removed is not None and removed.status == "removed"
    assert await orgs_repo.list_all() == []
    assert len(await orgs_repo.list_all(include_removed=True)) == 1
    revived = await orgs_repo.register("acme", "github")
    assert revived.status == "active"


@pytest.mark.asyncio
async def test_register_refuses_a_repository_path_and_an_unknown_forge(orgs_repo: OrganizationsRepository) -> None:
    with pytest.raises(ValueError):
        await orgs_repo.register("acme/backend", "github")
    with pytest.raises(ValueError):
        await orgs_repo.register("acme", "bitbucket")
    with pytest.raises(ValueError):
        await orgs_repo.set_status("acme", "sleeping")


@pytest.mark.asyncio
async def test_facts_replace_per_repo_and_runs_keep_history(facts_repo: OrgReposRepository) -> None:
    run1 = await facts_repo.start_run("Acme")
    assert run1["status"] == "running" and run1["owner"] == "acme"
    assert (await facts_repo.running_run("acme")) is not None
    await facts_repo.progress(run1["id"], seen=1, total=2)
    await facts_repo.upsert_fact(RepoFacts(owner="acme", repo="acme/web", facts={"frontend": True}), run_id=run1["id"])
    await facts_repo.upsert_fact(RepoFacts(owner="acme", repo="acme/old", archived=True), run_id=run1["id"])
    done = await facts_repo.finish_run(run1["id"], summary={"repos": 2})
    assert done is not None and done["status"] == "done" and done["summary"] == {"repos": 2} and done["repos_seen"] == 1
    assert (await facts_repo.running_run("acme")) is None
    run2 = await facts_repo.start_run("acme")
    await facts_repo.upsert_fact(RepoFacts(owner="acme", repo="acme/web", facts={"frontend": False}), run_id=run2["id"])
    failed = await facts_repo.finish_run(run2["id"], error="RuntimeError: boom")
    assert failed is not None and failed["status"] == "error"
    rows = await facts_repo.list_for_owner("acme")
    assert {r["repo"]: r["facts"] for r in rows} == {"acme/web": {"frontend": False}, "acme/old": {}}
    assert [r["repo"] for r in await facts_repo.list_for_owner("acme", include_archived=False)] == ["acme/web"]
    runs = await facts_repo.list_runs("acme")
    assert [r["status"] for r in runs] == ["error", "done"]


@pytest.mark.asyncio
async def test_patch_merges_settings_and_keeps_the_rest(orgs_repo: OrganizationsRepository) -> None:
    await orgs_repo.register("acme", "github", settings={"tracker": "github"}, display_name="Acme")
    patched = await orgs_repo.patch("Acme", settings={"auto_register": True})
    assert patched is not None
    assert patched.settings == {"tracker": "github", "auto_register": True}
    assert patched.display_name == "Acme"
    renamed = await orgs_repo.patch("acme", display_name="Acme Inc")
    assert renamed is not None and renamed.display_name == "Acme Inc"
    assert renamed.settings == {"tracker": "github", "auto_register": True}
    assert await orgs_repo.patch("nobody", settings={"auto_register": True}) is None


@pytest.mark.asyncio
async def test_note_webhook_event_creates_a_bare_fact_row_and_counts_deliveries(
    facts_repo: OrgReposRepository,
) -> None:
    created = await facts_repo.note_webhook_event(
        "Acme", "acme/widgets", event="issues", action="opened", delivery_id="d-1", registered=False
    )
    again = await facts_repo.note_webhook_event(
        "acme", "acme/widgets", event="push", action="", delivery_id="d-2", registered=False
    )
    assert (created, again) == (True, False), "only the first delivery creates the row"
    rows = await facts_repo.list_for_owner("acme")
    assert [r["repo"] for r in rows] == ["acme/widgets"]
    assert rows[0]["run_id"] is None
    assert rows[0]["facts"] == {
        "registered": False,
        "webhook_events": 2,
        "last_webhook_event": {"event": "push", "action": "", "delivery_id": "d-2"},
        "webhook_delivery_ids": ["d-1", "d-2"],
    }


@pytest.mark.asyncio
async def test_a_redelivery_is_counted_once(facts_repo: OrgReposRepository) -> None:
    for delivery in ("d-1", "d-1", "d-2", "d-1", "d-2"):
        await facts_repo.note_webhook_event(
            "acme", "acme/widgets", event="issues", action="opened", delivery_id=delivery, registered=False
        )
    facts = (await facts_repo.list_for_owner("acme"))[0]["facts"]
    assert facts["webhook_events"] == 2
    assert facts["webhook_delivery_ids"] == ["d-1", "d-2"]
    assert facts["last_webhook_event"]["delivery_id"] == "d-2"


@pytest.mark.asyncio
async def test_a_delivery_without_an_id_is_counted_every_time(facts_repo: OrgReposRepository) -> None:
    for delivery in (None, "d-1", None):
        await facts_repo.note_webhook_event(
            "acme", "acme/widgets", event="issues", action="opened", delivery_id=delivery, registered=False
        )
    facts = (await facts_repo.list_for_owner("acme"))[0]["facts"]
    assert facts["webhook_events"] == 3
    assert facts["webhook_delivery_ids"] == ["d-1"]


@pytest.mark.asyncio
async def test_the_remembered_delivery_ids_are_bounded_to_the_latest(facts_repo: OrgReposRepository) -> None:
    total = WEBHOOK_DELIVERY_IDS_KEPT + 3
    for n in range(total):
        await facts_repo.note_webhook_event(
            "acme", "acme/widgets", event="issues", action="opened", delivery_id=f"d-{n}", registered=False
        )
    facts = (await facts_repo.list_for_owner("acme"))[0]["facts"]
    assert facts["webhook_events"] == total
    assert facts["webhook_delivery_ids"] == [f"d-{n}" for n in range(3, total)]
    await facts_repo.note_webhook_event(
        "acme", "acme/widgets", event="issues", action="opened", delivery_id=f"d-{total - 1}", registered=False
    )
    assert (await facts_repo.list_for_owner("acme"))[0]["facts"]["webhook_events"] == total


@pytest.mark.asyncio
async def test_note_webhook_event_keeps_discovery_facts_and_marks_a_registration(
    facts_repo: OrgReposRepository,
) -> None:
    run = await facts_repo.start_run("acme")
    await facts_repo.upsert_fact(
        RepoFacts(owner="acme", repo="acme/widgets", default_branch="main", facts={"registered": False, "ci": True}),
        run_id=run["id"],
    )
    before = (await facts_repo.list_for_owner("acme"))[0]
    await facts_repo.note_webhook_event(
        "acme", "acme/widgets", event="issues", action="opened", delivery_id=None, registered=True
    )
    after = (await facts_repo.list_for_owner("acme"))[0]
    assert after["run_id"] == run["id"]
    assert after["default_branch"] == "main"
    assert after["seen_at"] == before["seen_at"]
    assert after["facts"] == {
        "ci": True,
        "registered": True,
        "active": True,
        "webhook_events": 1,
        "last_webhook_event": {"event": "issues", "action": "opened", "delivery_id": None},
    }


@pytest.mark.asyncio
async def test_a_rescan_replaces_discovery_facts_but_keeps_the_delivery_record(
    facts_repo: OrgReposRepository,
) -> None:
    await facts_repo.note_webhook_event(
        "acme", "acme/widgets", event="issues", action="opened", delivery_id="d-1", registered=False
    )
    run = await facts_repo.start_run("acme")
    await facts_repo.upsert_fact(
        RepoFacts(owner="acme", repo="acme/widgets", default_branch="main", facts={"registered": True, "ci": False}),
        run_id=run["id"],
    )
    row = (await facts_repo.list_for_owner("acme"))[0]
    assert row["run_id"] == run["id"]
    assert row["facts"] == {
        "registered": True,
        "ci": False,
        "webhook_events": 1,
        "last_webhook_event": {"event": "issues", "action": "opened", "delivery_id": "d-1"},
        "webhook_delivery_ids": ["d-1"],
    }
    # The kept ids still dedupe after the rescan.
    await facts_repo.note_webhook_event(
        "acme", "acme/widgets", event="issues", action="opened", delivery_id="d-1", registered=False
    )
    assert (await facts_repo.list_for_owner("acme"))[0]["facts"]["webhook_events"] == 1


# ---- rediscovery (ADR 0014) ----


@pytest.mark.asyncio
async def test_start_run_opens_one_run_per_owner_even_when_starts_race(facts_repo: OrgReposRepository) -> None:
    import asyncio

    started = await asyncio.gather(*(facts_repo.start_run("Acme") for _ in range(6)), facts_repo.start_run("other"))
    acme = [run for run in started[:6] if run is not None]
    assert len(acme) == 1, f"six concurrent starts opened {len(acme)} runs"
    assert started[6] is not None and started[6]["owner"] == "other", "the lock is per owner"
    assert [r["id"] for r in await facts_repo.list_runs("acme")] == [acme[0]["id"]]
    await facts_repo.finish_run(acme[0]["id"], summary={})
    again = await facts_repo.start_run("acme")
    assert again is not None and again["status"] == "running", "a finished run frees the owner"


@pytest.mark.asyncio
async def test_fail_interrupted_runs_closes_only_the_running_ones(facts_repo: OrgReposRepository) -> None:
    from embry0.storage.repositories.org_repos import INTERRUPTED

    done = await facts_repo.start_run("acme")
    assert done is not None
    await facts_repo.finish_run(done["id"], summary={"repos": 1})
    cut = await facts_repo.start_run("acme")
    other = await facts_repo.start_run("other")
    assert cut is not None and other is not None
    assert sorted(await facts_repo.fail_interrupted_runs()) == sorted([cut["id"], other["id"]])
    runs = {r["id"]: r for r in await facts_repo.list_runs("acme")}
    assert runs[cut["id"]]["status"] == "error" and runs[cut["id"]]["error"] == INTERRUPTED
    assert runs[cut["id"]]["finished_at"] is not None
    assert runs[done["id"]]["status"] == "done" and runs[done["id"]]["error"] is None
    assert await facts_repo.running_run("acme") is None
    assert await facts_repo.fail_interrupted_runs() == [], "a second sweep finds nothing"


@pytest.mark.asyncio
async def test_refresh_state_merges_embry0_state_and_keeps_the_listing_run(facts_repo: OrgReposRepository) -> None:
    run = await facts_repo.start_run("acme")
    assert run is not None
    await facts_repo.upsert_fact(
        RepoFacts(
            owner="acme", repo="acme/gone", facts={"boot_hint": "npm run dev", "registered": True, "active": True}
        ),
        run_id=run["id"],
    )
    await facts_repo.refresh_state("acme/gone", {"registered": True, "active": False})
    (row,) = await facts_repo.list_for_owner("acme")
    assert row["run_id"] == run["id"]
    assert row["facts"] == {"boot_hint": "npm run dev", "registered": True, "active": False}
