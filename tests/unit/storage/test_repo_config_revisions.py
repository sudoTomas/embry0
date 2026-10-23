"""Every write to a repo's test setup or preferences leaves a revision (ADR 0019).

The revision is appended by the storage classes themselves, inside the
write's own transaction, so every writer is covered without a change at its
call site: the dashboard and API, MCP, bootstrap, the onboard pipeline, and
later the proposal apply and undo.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories.repo_config_revisions import KEEP_PER_SURFACE, RepoConfigRevisionsRepository
from embry0.storage.repositories.repo_preferences import RepoPreferencesRepository
from embry0.storage.repositories.repo_qa_configs import RepoQAConfigsRepository, SetupChanged

pytestmark = pytest.mark.requires_postgres

REPO = "acme/widgets"
V1 = "version: 2\nqa_required: never\n"
V2 = "version: 2\nqa_required: auto\n"


@pytest.fixture
def revisions(db_with_migrations: DatabasePool) -> RepoConfigRevisionsRepository:
    return RepoConfigRevisionsRepository(db_with_migrations)


@pytest.fixture
def configs(db_with_migrations: DatabasePool) -> RepoQAConfigsRepository:
    return RepoQAConfigsRepository(db_with_migrations)


@pytest.fixture
def prefs(db_with_migrations: DatabasePool) -> RepoPreferencesRepository:
    return RepoPreferencesRepository(db_with_migrations)


def _oldest_first(rows: list[dict[str, Any]]) -> list[tuple[Any, Any, str]]:
    return [(r["before"], r["after"], r["source"]) for r in reversed(rows)]


async def test_two_saves_and_a_removal_leave_three_revisions_in_order(configs, revisions) -> None:
    _, first = await configs.upsert_recorded(REPO, V1, updated_by="dev@example.com")
    _, second = await configs.upsert_recorded(REPO, V2, updated_by="dev@example.com")
    third = await configs.delete_recorded(REPO, actor="dev@example.com")

    rows = await revisions.list_for_repo(REPO)
    assert [r["id"] for r in rows] == [third, second, first], "list_for_repo must be newest first"
    assert _oldest_first(rows) == [
        (None, {"yaml_text": V1}, "person"),
        ({"yaml_text": V1}, {"yaml_text": V2}, "person"),
        ({"yaml_text": V2}, None, "person"),
    ]
    assert {r["surface"] for r in rows} == {"qa_config"}
    assert {r["actor"] for r in rows} == {"dev@example.com"}
    assert await configs.get(REPO) is None
    removed = await revisions.get(third)
    assert removed is not None and removed["before"] == {"yaml_text": V2}, "the removed text must stay readable"


async def test_an_unchanged_save_records_nothing(configs, revisions) -> None:
    await configs.upsert_recorded(REPO, V1, updated_by="dev@example.com")
    row, revision_id = await configs.upsert_recorded(REPO, V1, updated_by="dev@example.com")
    assert revision_id is None
    assert row["yaml_text"] == V1
    assert len(await revisions.list_for_repo(REPO)) == 1


async def test_removing_a_missing_setup_records_nothing(configs, revisions) -> None:
    assert await configs.delete_recorded(REPO, actor="dev@example.com") is None
    assert await revisions.list_for_repo(REPO) == []


@pytest.mark.parametrize(
    ("updated_by", "source"),
    [("bootstrap", "bootstrap"), ("onboard-pipeline", "onboard"), ("dev@example.com", "person"), ("", "person")],
)
async def test_the_source_is_read_from_the_writer(configs, revisions, updated_by: str, source: str) -> None:
    await configs.upsert_recorded(REPO, V1, updated_by=updated_by)
    latest = await revisions.latest(REPO, "qa_config")
    assert latest is not None
    assert (latest["source"], latest["actor"]) == (source, updated_by)


async def test_an_explicit_source_and_reason_win(configs, revisions) -> None:
    await configs.upsert_recorded(REPO, V1, updated_by="dev@example.com", source="proposal", reason="proposal p-1")
    latest = await revisions.latest(REPO, "qa_config")
    assert latest is not None
    assert (latest["source"], latest["reason"]) == ("proposal", "proposal p-1")


async def test_the_plain_upsert_and_delete_are_recorded_too(configs, revisions) -> None:
    """Bootstrap, the onboard pipeline and the org action still call upsert and delete."""
    row = await configs.upsert(REPO, V1, updated_by="bootstrap")
    assert row["yaml_text"] == V1 and row["updated_by"] == "bootstrap"
    await configs.delete(REPO)
    assert _oldest_first(await revisions.list_for_repo(REPO)) == [
        (None, {"yaml_text": V1}, "bootstrap"),
        ({"yaml_text": V1}, None, "person"),
    ]


async def test_a_failed_append_rolls_back_the_setup(configs, revisions) -> None:
    await configs.upsert_recorded(REPO, V1)
    configs._revisions.append = AsyncMock(side_effect=RuntimeError("revision store down"))
    with pytest.raises(RuntimeError, match="revision store down"):
        await configs.upsert_recorded(REPO, V2)
    with pytest.raises(RuntimeError, match="revision store down"):
        await configs.delete_recorded(REPO)
    stored = await configs.get(REPO)
    assert stored is not None and stored["yaml_text"] == V1, "the write must not outlive its revision"
    assert len(await revisions.list_for_repo(REPO)) == 1


async def _apply_over(db: DatabasePool, configs: RepoQAConfigsRepository, base: Any, text: str) -> int | None:
    """A compare-and-set write in its own transaction, as an apply makes it."""
    async with db.transaction() as conn:
        _, revision_id = await configs.upsert_recorded_if_unchanged(
            conn, REPO, text, base_updated_at=base, updated_by="dev@example.com", source="proposal", reason="proposal p"
        )
    return revision_id


async def test_a_compare_and_set_writes_only_over_the_setup_it_expects(
    db_with_migrations: DatabasePool, configs, revisions
) -> None:
    saved, _ = await configs.upsert_recorded(REPO, V1)
    with pytest.raises(SetupChanged):
        await _apply_over(db_with_migrations, configs, None, V2)
    assert (await configs.get(REPO))["yaml_text"] == V1
    assert len(await revisions.list_for_repo(REPO)) == 1

    revision_id = await _apply_over(db_with_migrations, configs, saved["updated_at"], V2)
    assert (await configs.get(REPO))["yaml_text"] == V2
    revision = await revisions.get(revision_id)
    assert revision is not None and (revision["before"], revision["source"]) == ({"yaml_text": V1}, "proposal")


async def _until_a_write_waits(db: DatabasePool) -> None:
    for _ in range(500):
        waiting = await db.fetchval(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock'"
        )
        if waiting:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the second write never waited for the first")


async def test_a_compare_and_set_over_no_setup_keeps_a_first_save_still_in_flight(
    db_with_migrations: DatabasePool, configs, revisions
) -> None:
    """With no setup stored there is no row to lock, so the first save of
    another writer can still be uncommitted when the apply writes."""
    db = db_with_migrations
    await db.execute("INSERT INTO repos (repo, forge) VALUES ($1, 'github')", REPO)
    async with db.transaction() as first_save:
        await first_save.execute(
            "INSERT INTO repo_qa_configs (repo, yaml_text, updated_by) VALUES ($1, $2, 'someone')", REPO, V1
        )
        apply = asyncio.create_task(_apply_over(db, configs, None, V2))
        await _until_a_write_waits(db)
    with pytest.raises(SetupChanged):
        await apply
    assert (await configs.get(REPO))["yaml_text"] == V1
    assert await revisions.list_for_repo(REPO) == []


async def test_a_deployment_without_a_default_forge_still_refuses_an_unregistered_repo(
    db_with_migrations: DatabasePool, revisions
) -> None:
    configs = RepoQAConfigsRepository(db_with_migrations, default_forge="")
    with pytest.raises(ValueError, match="DEFAULT_FORGE is empty"):
        await configs.upsert_recorded(REPO, V1)
    assert await revisions.list_for_repo(REPO) == []


async def test_a_preferences_save_records_the_row_without_updated_at(prefs, revisions) -> None:
    row, revision_id = await prefs.upsert_recorded(REPO, actor="dev@example.com", base_branch="develop")
    assert row["base_branch"] == "develop"
    revision = await revisions.get(revision_id)
    assert revision is not None
    assert (revision["surface"], revision["before"], revision["actor"]) == ("preferences", None, "dev@example.com")
    assert revision["after"]["base_branch"] == "develop"
    assert "updated_at" not in revision["after"]
    assert revision["after"] == {k: v for k, v in row.items() if k != "updated_at"}


async def test_an_unchanged_preferences_save_records_nothing(prefs, revisions) -> None:
    await prefs.upsert_recorded(REPO, base_branch="develop")
    _, revision_id = await prefs.upsert_recorded(REPO, base_branch="develop")
    assert revision_id is None
    assert len(await revisions.list_for_repo(REPO)) == 1


async def test_a_preferences_reset_keeps_pr_review_and_verification(prefs, revisions) -> None:
    pr_review = {"enabled": True, "depth": "targeted", "model": None, "ready_authors": "team"}
    verification = {"test_targets": ["unit"]}
    await prefs.upsert(REPO, sandbox_profile="qa-external", pr_review=pr_review, verification=verification)
    revision_id = await prefs.delete_recorded(REPO, actor="dev@example.com")
    assert await prefs.get(REPO) is None
    revision = await revisions.get(revision_id)
    assert revision is not None
    assert revision["after"] is None
    assert revision["before"]["pr_review"] == pr_review
    assert revision["before"]["verification"] == verification
    assert revision["before"]["sandbox_profile"] == "qa-external"


async def test_the_plain_preferences_delete_is_recorded(prefs, revisions) -> None:
    await prefs.upsert(REPO, notes="keep")
    await prefs.delete(REPO)
    rows = await revisions.list_for_repo(REPO)
    assert [(r["before"] is None, r["after"] is None) for r in rows] == [(False, True), (True, False)]


async def test_a_failed_append_rolls_back_the_preferences(prefs) -> None:
    await prefs.upsert(REPO, notes="before")
    prefs._revisions.append = AsyncMock(side_effect=RuntimeError("revision store down"))
    with pytest.raises(RuntimeError):
        await prefs.upsert_recorded(REPO, notes="after")
    with pytest.raises(RuntimeError):
        await prefs.delete_recorded(REPO)
    stored = await prefs.get(REPO)
    assert stored is not None and stored["notes"] == "before"


async def test_the_unknown_field_refusal_is_unchanged(prefs) -> None:
    with pytest.raises(TypeError, match="unknown repo preference field"):
        await prefs.upsert_recorded(REPO, colour="red")


async def test_latest_is_per_surface(configs, prefs, revisions) -> None:
    await configs.upsert_recorded(REPO, V1)
    await prefs.upsert_recorded(REPO, notes="n")
    await configs.upsert_recorded("acme/other", V2)
    latest_qa = await revisions.latest(REPO, "qa_config")
    latest_prefs = await revisions.latest(REPO, "preferences")
    assert latest_qa is not None and latest_qa["after"] == {"yaml_text": V1}
    assert latest_prefs is not None and latest_prefs["after"]["notes"] == "n"
    assert await revisions.latest("acme/ghost", "qa_config") is None
    assert await revisions.get(999_999) is None


async def test_list_for_repo_honours_the_limit(configs, revisions) -> None:
    for n in range(3):
        await configs.upsert_recorded(REPO, f"version: 2\n# {n}\n")
    rows = await revisions.list_for_repo(REPO, limit=2)
    assert [r["after"]["yaml_text"] for r in rows] == ["version: 2\n# 2\n", "version: 2\n# 1\n"]


async def test_append_keeps_the_newest_rows_of_one_repo_and_surface(db_with_migrations, revisions) -> None:
    async with db_with_migrations.transaction() as conn:
        for n in range(KEEP_PER_SURFACE + 3):
            await revisions.append(
                conn, repo=REPO, surface="qa_config", before=None, after={"n": n}, actor="", source="person", reason=""
            )
        await revisions.append(
            conn, repo=REPO, surface="preferences", before=None, after={}, actor="", source="person", reason=""
        )
        await revisions.append(
            conn, repo="acme/other", surface="qa_config", before=None, after={}, actor="", source="person", reason=""
        )
    kept = await revisions.list_for_repo(REPO, limit=KEEP_PER_SURFACE + 10)
    qa = [r["after"]["n"] for r in kept if r["surface"] == "qa_config"]
    assert qa == list(range(KEEP_PER_SURFACE + 2, 2, -1)), "the newest 100 stay, the oldest go"
    assert [r["surface"] for r in kept].count("preferences") == 1
    assert len(await revisions.list_for_repo("acme/other")) == 1
