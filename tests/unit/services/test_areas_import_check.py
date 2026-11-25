"""Repositories whose old areas file was never imported (#175): the import is gone, so a deployment that upgraded
past the release that carried it is told which repositories' areas embry0 never read, and how to fix it."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from structlog.testing import capture_logs

from embry0.storage.database import DatabasePool

pytestmark = pytest.mark.requires_postgres


def _check() -> Any:
    try:
        return importlib.import_module("embry0.services.areas_import_check")
    except ImportError:
        pytest.fail("startup must name the repositories whose old areas file was never imported")


async def _registered(db: DatabasePool, *repos: str) -> None:
    for repo in repos:
        await db.execute("INSERT INTO repos (repo) VALUES ($1) ON CONFLICT DO NOTHING", repo)


async def _ledger(db: DatabasePool, repo: str, outcome: str) -> None:
    await db.execute(
        "INSERT INTO repo_areas_imports (repo, outcome) VALUES ($1, $2) "
        "ON CONFLICT (repo) DO UPDATE SET outcome = EXCLUDED.outcome",
        repo,
        outcome,
    )


async def _flagged(db: DatabasePool) -> list[Any]:
    return list(await db.fetch("SELECT details FROM audit_log WHERE action = 'repo.areas_import_skipped'"))


async def test_a_deployment_whose_imports_all_ended_says_nothing(db_with_migrations: DatabasePool) -> None:
    await _registered(db_with_migrations, "acme/a", "acme/b")
    await _ledger(db_with_migrations, "acme/a", "imported")
    await _ledger(db_with_migrations, "acme/b", "none")
    with capture_logs() as logs:
        assert await _check().warn_if_areas_never_imported(db_with_migrations) == []
    assert not [e for e in logs if e["event"] == "areas_import_skipped"], logs
    assert await _flagged(db_with_migrations) == []


async def test_repositories_never_read_are_logged_at_error_level_and_audited_once(
    db_with_migrations: DatabasePool,
) -> None:
    await _registered(db_with_migrations, "acme/pending", "acme/failed", "acme/done")
    await _ledger(db_with_migrations, "acme/pending", "pending")
    await _ledger(db_with_migrations, "acme/failed", "failed")
    await _ledger(db_with_migrations, "acme/done", "imported")
    with capture_logs() as logs:
        found = await _check().warn_if_areas_never_imported(db_with_migrations)
    assert found == ["acme/failed", "acme/pending"], found
    said = [e for e in logs if e["event"] == "areas_import_skipped"]
    assert [(e["log_level"], e["count"], e["repos"]) for e in said] == [("error", 2, ["acme/failed", "acme/pending"])]
    assert "write or map these repositories' areas" in said[0]["remedy"], said
    flagged = await _flagged(db_with_migrations)
    assert len(flagged) == 1 and flagged[0]["details"]["count"] == 2, flagged


@pytest.mark.parametrize("why", ["its areas are written", "it is no longer registered"])
async def test_a_repository_is_off_the_list_once_it_has_areas_or_is_gone(
    db_with_migrations: DatabasePool, why: str
) -> None:
    await _registered(db_with_migrations, "acme/widgets")
    await _ledger(db_with_migrations, "acme/widgets", "pending")
    if why == "its areas are written":
        await db_with_migrations.execute(
            "INSERT INTO repo_areas (repo, yaml_text, data) VALUES ('acme/widgets', '', '{}'::jsonb)"
        )
    else:
        await db_with_migrations.execute("DELETE FROM repos WHERE repo = 'acme/widgets'")
    assert await _check().unread(db_with_migrations) == [], why


async def test_a_check_that_fails_never_stops_the_start() -> None:
    class Broken:
        async def fetch(self, *args: Any) -> Any:
            raise OSError("the database went away")

    with capture_logs() as logs:
        assert await _check().warn_if_areas_never_imported(Broken()) == []
    assert [e["event"] for e in logs] == ["areas_import_check_failed"], logs
