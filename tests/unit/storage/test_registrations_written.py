"""The writes of a repository's or an organization's registration are noted, so a cache read through one (a
repository's CODEOWNERS) drops what it kept at once (#181)."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from embry0.storage.database import DatabasePool
from embry0.storage.repositories import registrations
from embry0.storage.repositories.organizations import OrganizationsRepository
from embry0.storage.repositories.repos import ReposRepository

pytestmark = pytest.mark.requires_postgres


async def test_registering_and_removing_a_repository_is_noted(db_with_migrations: DatabasePool) -> None:
    repos = ReposRepository(db_with_migrations, default_forge="github")
    before = registrations.count()
    await repos.register("Acme/Docs", forge="github")
    assert registrations.changed_since("repo", "acme/docs", before), "a registration is noted, in any case"
    assert not registrations.changed_since("repo", "acme/web", before), "another repository's is not"
    registered = registrations.count()
    await repos.register("acme/docs", forge="github")
    assert registrations.changed_since("repo", "acme/docs", registered), "registering again is noted too"
    again = registrations.count()
    await repos.delete("acme/docs")
    assert registrations.changed_since("repo", "acme/docs", again), "a removal is noted"


async def test_registering_an_organization_and_changing_its_status_is_noted(db_with_migrations: DatabasePool) -> None:
    organizations = OrganizationsRepository(db_with_migrations)
    before = registrations.count()
    await organizations.register("Acme", "github", created_by="tests")
    assert registrations.changed_since("organization", "acme", before)
    assert not registrations.changed_since("organization", "other", before)
    registered = registrations.count()
    await organizations.set_status("acme", "removed")
    assert registrations.changed_since("organization", "acme", registered), "a change of status is noted"


async def test_a_qa_config_write_that_registers_a_repository_notes_it_only_when_it_commits(
    db_with_migrations: DatabasePool,
) -> None:
    """The write registers a repository nobody registered (its row is the config's key): noted once committed, and
    not at all when the write rolls back. A repository already registered is no new registration."""
    from embry0.storage.repositories.repo_qa_configs import RepoQAConfigsRepository

    configs = RepoQAConfigsRepository(db_with_migrations, default_forge="github")

    def refuse(_stored: str | None) -> None:
        raise ValueError("refused")

    before = registrations.count()
    with pytest.raises(ValueError):
        await configs.upsert_recorded("acme/rolled", "version: 2\n", "dev@example.com", check=refuse)
    assert not registrations.changed_since("repo", "acme/rolled", before), "a rolled-back write registered nothing"

    await configs.upsert_recorded("acme/fresh", "version: 2\n", "dev@example.com")
    assert registrations.changed_since("repo", "acme/fresh", before), "a committed registration is noted"
    registered = registrations.count()
    await configs.upsert_recorded("acme/fresh", "version: 2\nx: 1\n", "dev@example.com")
    assert not registrations.changed_since("repo", "acme/fresh", registered), "already registered: nothing new"


def watch_commits(monkeypatch: pytest.MonkeyPatch, db: Any, seen: Callable[[], bool]) -> list[bool]:
    """Patch ``db.transaction`` so each transaction, at the end of its block and before its commit, records
    ``seen()``: what was visible while the transaction was still open."""
    real = db.transaction
    before_commit: list[bool] = []

    @contextlib.asynccontextmanager
    async def transaction(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        async with real(*args, **kwargs) as conn:
            yield conn
            before_commit.append(seen())

    monkeypatch.setattr(db, "transaction", transaction)
    return before_commit


async def test_a_qa_config_write_notes_its_registration_after_the_commit_not_before(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Noted inside the transaction, a cache could read the registration before it commits and keep the old answer."""
    from embry0.storage.repositories.repo_qa_configs import RepoQAConfigsRepository

    before = registrations.count()
    open_ = watch_commits(
        monkeypatch, db_with_migrations, lambda: registrations.changed_since("repo", "acme/late", before)
    )
    await RepoQAConfigsRepository(db_with_migrations, default_forge="github").upsert_recorded(
        "acme/late", "version: 2\n", "dev@example.com"
    )
    assert open_ == [False], "noted while the transaction was open"
    assert registrations.changed_since("repo", "acme/late", before)
