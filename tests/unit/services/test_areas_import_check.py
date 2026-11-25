"""Repositories whose old areas file was never imported (#175): the import is gone, so a deployment that upgraded
past the builds that carried it is told which repositories' areas embry0 never read, and how to fix each."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import time
from contextlib import asynccontextmanager
from typing import Any

import pytest
from structlog.testing import capture_logs

from embry0.storage import repo_renames
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


async def _ledger(db: DatabasePool, repo: str, outcome: str, attempts: int = 0) -> None:
    await db.execute(
        "INSERT INTO repo_areas_imports (repo, outcome, attempts) VALUES ($1, $2, $3) "
        "ON CONFLICT (repo) DO UPDATE SET outcome = EXCLUDED.outcome, attempts = EXCLUDED.attempts",
        repo,
        outcome,
        attempts,
    )


async def _areas(db: DatabasePool, repo: str) -> None:
    await db.execute("INSERT INTO repo_areas (repo, yaml_text, data) VALUES ($1, '', '{}'::jsonb)", repo)


async def _flagged(db: DatabasePool) -> list[Any]:
    return list(await db.fetch("SELECT details FROM audit_log WHERE action = 'repo.areas_import_skipped'"))


def _groups(found: Any) -> tuple[list[str], list[str]]:
    if not hasattr(found, "due") or not hasattr(found, "exhausted"):
        pytest.fail(f"the check must tell the repositories still due a read from those given up on: {found!r}")
    return list(found.due), list(found.exhausted)


def _given_up_after(check: Any) -> int:
    attempts = getattr(check, "MAX_ATTEMPTS", None)
    if attempts is None:
        pytest.fail("the check must know after how many failed reads the import gave up on a repository")
    return int(attempts)


async def test_a_deployment_whose_imports_all_ended_says_nothing(db_with_migrations: DatabasePool) -> None:
    await _registered(db_with_migrations, "acme/a", "acme/b")
    await _ledger(db_with_migrations, "acme/a", "imported")
    await _ledger(db_with_migrations, "acme/b", "none")
    with capture_logs() as logs:
        assert _groups(await _check().warn_if_areas_never_imported(db_with_migrations)) == ([], [])
    assert not [e for e in logs if e["event"] == "areas_import_skipped"], logs
    assert await _flagged(db_with_migrations) == []


async def test_repositories_never_read_are_logged_at_error_level_by_group_and_audited_once(
    db_with_migrations: DatabasePool,
) -> None:
    """``pending`` and a ``failed`` read the import would have tried again are due: one start of a build that
    carries the import reads them. A read that failed 24 times was given up on: only writing its areas helps."""
    check = _check()
    given_up = _given_up_after(check)
    await _registered(db_with_migrations, "acme/pending", "acme/failed", "acme/given-up", "acme/done")
    await _ledger(db_with_migrations, "acme/pending", "pending")
    await _ledger(db_with_migrations, "acme/failed", "failed", attempts=given_up - 1)
    await _ledger(db_with_migrations, "acme/given-up", "failed", attempts=given_up)
    await _ledger(db_with_migrations, "acme/done", "imported")
    with capture_logs() as logs:
        found = await check.warn_if_areas_never_imported(db_with_migrations)
    assert _groups(found) == (["acme/failed", "acme/pending"], ["acme/given-up"]), found
    said = [e for e in logs if e["event"] == "areas_import_skipped"]
    assert [
        (e["log_level"], e["count"], e["due"], e["exhausted"], e["due_repos"], e["exhausted_repos"]) for e in said
    ] == [("error", 3, 2, 1, ["acme/failed", "acme/pending"], ["acme/given-up"])], said
    remedy = said[0]["remedy"]
    assert "for the 2 never read or still due a read: start a build of main from commit 45d07a6f" in remedy, remedy
    assert "before pull request #706" in remedy, remedy
    assert "for the 1 whose reads were given up on: write or map their areas" in remedy, remedy
    flagged = await _flagged(db_with_migrations)
    assert len(flagged) == 1, flagged
    details = flagged[0]["details"]
    assert (details["count"], details["due"], details["exhausted"]) == (3, 2, 1), details


async def test_only_given_up_reads_are_told_only_to_write_their_areas(db_with_migrations: DatabasePool) -> None:
    check = _check()
    await _registered(db_with_migrations, "acme/given-up")
    await _ledger(db_with_migrations, "acme/given-up", "failed", attempts=_given_up_after(check) + 3)
    with capture_logs() as logs:
        await check.warn_if_areas_never_imported(db_with_migrations)
    (said,) = [e for e in logs if e["event"] == "areas_import_skipped"]
    assert "45d07a6f" not in said["remedy"] and said["remedy"].startswith("for the 1 whose reads"), said


@pytest.mark.parametrize("why", ["its areas are written", "it is no longer registered"])
async def test_a_repository_is_off_the_list_once_it_has_areas_or_is_gone(
    db_with_migrations: DatabasePool, why: str
) -> None:
    await _registered(db_with_migrations, "acme/widgets")
    await _ledger(db_with_migrations, "acme/widgets", "pending")
    if why == "its areas are written":
        await _areas(db_with_migrations, "acme/widgets")
    else:
        await db_with_migrations.execute("DELETE FROM repos WHERE repo = 'acme/widgets'")
    assert _groups(await _check().unread(db_with_migrations)) == ([], []), why


@pytest.mark.parametrize(
    ("ledger", "registered", "with_areas", "listed"),
    [
        ("Acme/Widgets", "acme/widgets", False, True),  # registered under another case: still never read
        ("Acme/Widgets", "acme/widgets", True, False),  # its areas, stored under the registry's spelling
        ("acme/old-name", "acme/new-name", False, True),  # renamed since: still never read
        ("acme/old-name", "acme/new-name", True, False),  # its areas, under its new name
        ("acme/new-name", "acme/old-name", True, False),  # or under its old one, still registered so
    ],
)
async def test_a_repository_is_matched_as_the_areas_store_matches_it(
    db_with_migrations: DatabasePool,
    monkeypatch: pytest.MonkeyPatch,
    ledger: str,
    registered: str,
    with_areas: bool,
    listed: bool,
) -> None:
    """Whatever the case, and under an old or a new name (``REPO_RENAMES``), as ``AreasStore.areas_of`` reads."""
    monkeypatch.setattr(repo_renames, "REPO_RENAMES", {"acme/old-name": "acme/new-name"})
    await _registered(db_with_migrations, registered)
    await _ledger(db_with_migrations, ledger, "pending")
    if with_areas:
        await _areas(db_with_migrations, registered)
    found = await _check().unread(db_with_migrations)
    assert _groups(found) == (([ledger] if listed else []), []), (ledger, registered, with_areas)


async def test_a_locked_ledger_stops_the_check_not_the_start(db_with_migrations: DatabasePool) -> None:
    """A lock held on the ledger: each query stops at its statement timeout, and the check says it failed."""
    await _registered(db_with_migrations, "acme/widgets")
    await _ledger(db_with_migrations, "acme/widgets", "pending")
    warn = _check().warn_if_areas_never_imported
    if "timeout" not in inspect.signature(warn).parameters:
        pytest.fail("the check's queries must stop at a statement timeout")
    async with db_with_migrations.transaction() as holder:
        await holder.execute("LOCK TABLE repo_areas_imports IN ACCESS EXCLUSIVE MODE")
        started = time.monotonic()
        with capture_logs() as logs:
            found = await asyncio.wait_for(warn(db_with_migrations, timeout=0.5), timeout=20)
        took = time.monotonic() - started
    assert _groups(found) == ([], []), found
    assert [e["event"] for e in logs] == ["areas_import_check_failed"], logs
    assert took < 5, f"the check waited {took:.1f}s on the lock"


async def test_a_check_that_fails_never_stops_the_start() -> None:
    class Broken:
        @asynccontextmanager
        async def transaction(self) -> Any:
            raise OSError("the database went away")
            yield

    with capture_logs() as logs:
        assert _groups(await _check().warn_if_areas_never_imported(Broken())) == ([], [])
    assert [e["event"] for e in logs] == ["areas_import_check_failed"], logs
