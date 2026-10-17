"""org_needs on Postgres (migration 99): one row per owner, kind and repository set,
and the case each open need files shows up as awaiting a decision."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from embry0.services.healing.service import HealingCaseService
from embry0.services.org_discovery import RepoFacts
from embry0.services.org_needs import Need, OrgNeedsService
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.healing_cases import HealingCasesRepository
from embry0.storage.repositories.org_needs import OrgNeedsRepository
from embry0.storage.repositories.org_repos import OrgReposRepository
from embry0.storage.repositories.organizations import OrganizationsRepository

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

REGISTER = Need(
    kind="not_registered",
    title="Register 2 repositories",
    detail="embry0 has no row for these repositories.",
    repos=("acme/api", "acme/web"),
    action={"type": "register", "params": {}},
)


async def test_upsert_keys_on_owner_kind_and_repositories_and_keeps_the_answer(
    db_with_migrations: DatabasePool,
) -> None:
    repo = OrgNeedsRepository(db_with_migrations)
    first = await repo.upsert("Acme", REGISTER, run_id="odr-1")
    assert first["owner"] == "acme" and first["status"] == "open" and first["case_id"] is None
    assert first["repos"] == ["acme/api", "acme/web"] and first["action"] == {"type": "register", "params": {}}
    dismissed = await repo.set_status(first["id"], "dismissed")
    assert dismissed is not None and dismissed["status"] == "dismissed"
    case = await HealingCaseService(HealingCasesRepository(db_with_migrations)).open_case(
        repo="acme", origin="org", title=REGISTER.title
    )
    assert (await repo.set_case(first["id"], case["id"])) is not None
    reworded = Need(
        kind=REGISTER.kind, title="Register two", detail="again", repos=REGISTER.repos, action=REGISTER.action
    )
    second = await repo.upsert("acme", reworded, run_id="odr-2")
    assert second["id"] == first["id"], "the same kind and repositories are the same need"
    assert second["title"] == "Register two" and second["run_id"] == "odr-2"
    assert second["status"] == "dismissed" and second["case_id"] == case["id"], "the person's answer is kept"
    smaller = Need(kind=REGISTER.kind, title="Register 1", detail="d", repos=("acme/api",), action=REGISTER.action)
    third = await repo.upsert("acme", smaller, run_id="odr-2")
    assert third["id"] != first["id"] and third["status"] == "open"
    assert [r["id"] for r in await repo.list_for_owner("acme")] == [third["id"], first["id"]], "open first"
    with pytest.raises(ValueError):
        await repo.set_status(first["id"], "sleeping")
    assert await repo.get("need-missing") is None


async def test_settle_missing_marks_only_the_open_needs_not_kept(db_with_migrations: DatabasePool) -> None:
    repo = OrgNeedsRepository(db_with_migrations)
    kept = await repo.upsert("acme", REGISTER, run_id="odr-1")
    gone = await repo.upsert(
        "acme",
        Need(kind="no_webhook", title="Hook", detail="d", repos=("acme/api",), action={"type": "hook", "params": {}}),
        run_id="odr-1",
    )
    dismissed = await repo.upsert(
        "acme",
        Need(
            kind="not_watched", title="Watch", detail="d", repos=("acme/web",), action={"type": "watch", "params": {}}
        ),
        run_id="odr-1",
    )
    await repo.set_status(dismissed["id"], "dismissed")
    other = await repo.upsert("other", REGISTER, run_id="odr-9")
    settled = await repo.settle_missing("acme", [kept["id"]])
    assert [r["id"] for r in settled] == [gone["id"]]
    rows = {r["id"]: r["status"] for r in await repo.list_for_owner("acme")}
    assert rows == {kept["id"]: "open", gone["id"]: "done", dismissed["id"]: "dismissed"}
    assert (await repo.get(other["id"]))["status"] == "open"  # type: ignore[index]


async def test_open_needs_are_cases_awaiting_a_decision_until_dismissed(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await OrganizationsRepository(db).register("acme", "github")
    facts = OrgReposRepository(db)
    run = await facts.start_run("acme")
    await facts.upsert_fact(
        RepoFacts(
            owner="acme",
            repo="acme/api",
            facts={"root_visible": True, "hooks_visible": True, "hook_present": False, "registered": False},
        ),
        run_id=run["id"],
    )
    await facts.finish_run(run["id"], summary={"repos": 1})
    cases_repo = HealingCasesRepository(db)
    service = OrgNeedsService(OrgNeedsRepository(db), facts, HealingCaseService(cases_repo))

    listing = await service.sync("acme")
    assert listing["run_id"] == run["id"] and listing["counts"] == {"open": 2, "done": 0, "dismissed": 0}
    waiting, total = await cases_repo.list_page(awaiting_decision=True)
    assert total == 2 and {c["origin"] for c in waiting} == {"org"}
    assert {c["id"] for c in waiting} == {n["case_id"] for n in listing["needs"]}
    assert {c["repo"] for c in waiting} == {"acme/api"}

    again = await service.sync("acme")
    assert again == listing, "a second sync of the same run changes nothing"
    assert (await cases_repo.list_page(awaiting_decision=True))[1] == 2

    need = next(n for n in listing["needs"] if n["kind"] == "no_webhook")
    dismissed = await service.dismiss("acme", need["id"])
    assert dismissed is not None and dismissed["status"] == "dismissed"
    closed = await cases_repo.get(need["case_id"])
    assert closed is not None and closed["stage"] == "closed" and closed["resolution_path"] == "cancelled"
    assert (await cases_repo.list_page(awaiting_decision=True))[1] == 1

    reopened = await service.reopen("acme", need["id"])
    assert reopened is not None and reopened["case_id"] != need["case_id"]
    successor = await cases_repo.get(reopened["case_id"])
    assert successor is not None and successor["parent_case_id"] == need["case_id"]
    assert (await cases_repo.list_page(awaiting_decision=True))[1] == 2


async def test_answers_survive_a_second_discovery_run(db_with_migrations: DatabasePool) -> None:
    """Dismissed on the needs list, or rejected as a case on the Proposals
    list: a rediscovery that finds the same facts keeps both answers and files
    nothing. A different set of repositories is a new question."""
    db = db_with_migrations
    await OrganizationsRepository(db).register("acme", "github")
    facts = OrgReposRepository(db)
    cases_repo = HealingCasesRepository(db)
    cases = HealingCaseService(cases_repo)
    service = OrgNeedsService(OrgNeedsRepository(db), facts, cases)
    seen = {"root_visible": True, "hooks_visible": True, "hook_present": False, "registered": False}

    async def discover(*registered: str) -> str:
        run = await facts.start_run("acme")
        for repo in ("acme/api", "acme/web"):
            await facts.upsert_fact(
                RepoFacts(owner="acme", repo=repo, facts={**seen, "registered": repo in registered}), run_id=run["id"]
            )
        await facts.finish_run(run["id"], summary={"repos": 2})
        return str(run["id"])

    await discover()
    first = {n["kind"]: n for n in (await service.sync("acme"))["needs"]}
    assert set(first) == {"not_registered", "no_webhook"}
    assert first["not_registered"]["repos"] == ["acme/api", "acme/web"]
    await service.dismiss("acme", first["not_registered"]["id"])
    await cases.close(first["no_webhook"]["case_id"], resolution_path="cancelled", note="Rejected from the dashboard")

    second_run = await discover()
    second = await service.sync("acme")
    assert second["run_id"] == second_run
    by_id = {n["id"]: n for n in second["needs"]}
    assert by_id[first["not_registered"]["id"]]["status"] == "dismissed"
    assert by_id[first["no_webhook"]["id"]]["status"] == "dismissed", "rejecting its case answered it"
    assert by_id[first["no_webhook"]["id"]]["case_id"] == first["no_webhook"]["case_id"]
    assert second["counts"] == {"open": 0, "done": 0, "dismissed": 2}
    assert (await cases_repo.list_page(awaiting_decision=True))[1] == 0
    assert (await cases_repo.list_page(origin="org"))[1] == 2, "no case filed again"

    await discover("acme/web")
    third = {n["kind"]: n for n in (await service.sync("acme"))["needs"] if n["status"] == "open"}
    assert third["not_registered"]["repos"] == ["acme/api"], "one repository left is a new question"
    assert third["not_registered"]["id"] != first["not_registered"]["id"]
    # Registered now, acme/web asks the next questions: QA and logs.
    assert set(third) == {"not_registered", "no_qa_config", "no_log_source"}
    assert (await cases_repo.list_page(awaiting_decision=True))[1] == len(third)


# ---- reads write nothing, and a catch-up is filed once across processes ----


async def _finished_unsynced_run(db: DatabasePool) -> OrgReposRepository:
    """One finished run whose needs were never synced, as a run that finished
    before the needs list existed leaves them."""
    await OrganizationsRepository(db).register("acme", "github")
    facts = OrgReposRepository(db)
    run = await facts.start_run("acme")
    await facts.upsert_fact(
        RepoFacts(
            owner="acme",
            repo="acme/api",
            facts={"root_visible": True, "hooks_visible": True, "hook_present": False, "registered": False},
        ),
        run_id=run["id"],
    )
    await facts.finish_run(run["id"], summary={"repos": 1})
    return facts


def _service(db: DatabasePool) -> OrgNeedsService:
    return OrgNeedsService(
        OrgNeedsRepository(db), OrgReposRepository(db), HealingCaseService(HealingCasesRepository(db))
    )


async def _row_versions(db: DatabasePool) -> dict[str, Any]:
    """Every row a sync could write, with its xmin: any UPDATE, even one that
    changes no value, gives a row a new xmin."""
    out: dict[str, Any] = {}
    for table, key in (("org_needs", "id"), ("org_needs_synced", "owner"), ("healing_cases", "id")):
        rows = await db.fetch(f"SELECT {key} AS k, xmin::text AS v FROM {table}")
        out[table] = {r["k"]: r["v"] for r in rows}
    out["healing_case_events"] = await db.fetchval("SELECT COUNT(*) FROM healing_case_events")
    return out


async def test_a_second_read_of_an_unchanged_run_writes_nothing(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await _finished_unsynced_run(db)
    service = _service(db)
    first = await service.read("acme")
    assert first["run_id"] is not None and first["counts"] == {"open": 2, "done": 0, "dismissed": 0}
    before = await _row_versions(db)
    assert len(before["org_needs"]) == 2 and len(before["healing_cases"]) == 2
    assert await service.read("acme") == first
    assert await _row_versions(db) == before, "the second read rewrote a row"


async def test_a_concurrent_catch_up_in_two_processes_files_each_case_once(db_with_migrations: DatabasePool) -> None:
    """Two orchestrator processes, each with its own pool, read the same
    unsynced run at once: the advisory lock lets one catch up, and the other
    finds nothing left to do."""
    db = db_with_migrations
    await _finished_unsynced_run(db)
    other = DatabasePool(
        os.environ.get("TEST_DATABASE_URL", "postgresql://embry0:embry0@localhost:5432/embry0_test"),
        min_size=1,
        max_size=5,
    )
    await other.connect()
    try:
        first, second = await asyncio.gather(_service(db).read("acme"), _service(other).read("acme"))
    finally:
        await other.close()
    assert first == second and first["counts"]["open"] == 2
    cases, total = await HealingCasesRepository(db).list_page(origin="org")
    assert total == 2, "each need filed its case once"
    assert sorted(c["id"] for c in cases) == sorted(n["case_id"] for n in first["needs"])


async def test_the_owner_lock_serializes_sessions_and_bounds_the_wait(db_with_migrations: DatabasePool) -> None:
    holder, waiter = OrgNeedsRepository(db_with_migrations), OrgNeedsRepository(db_with_migrations)
    async with holder.owner_lock("Acme") as held:
        assert held is True
        async with waiter.owner_lock("acme", timeout=0.2) as waited:
            assert waited is False, "another session holds acme"
            assert await db_with_migrations.fetchval("SELECT 1") == 1
        async with waiter.owner_lock("other", timeout=0.2) as free:
            assert free is True, "owners do not share a lock"
    async with waiter.owner_lock("acme", timeout=0.2) as after:
        assert after is True, "the lock ends with its transaction"
