"""The repository and owner lists narrowed in their query to the repositories a key covers, on a real database.

Each list a key with an allowlist reads of repositories, or of what each
belongs to one, takes the slugs the key covers (``covered_slugs``, lower-cased,
each with its old slugs from ``REPO_RENAMES``) and keeps only their rows in
SQL: the registry, the waiting test setup proposals, the watched stacks, the
agent contexts, the facts discovery keeps and the standards' states. A row
stored under a repository's old slug, or in another case, is selected through
the key's entry; another repository's row is not; ``None`` keeps every row and
an empty list none.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from embry0.services.watcher_stacks import WatcherStack
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.context_config import ContextConfigRepository
from embry0.storage.repositories.org_repos import OrgReposRepository
from embry0.storage.repositories.repo_setup_proposals import RepoSetupProposalsRepository
from embry0.storage.repositories.repos import ReposRepository
from embry0.storage.repositories.standards import RepoStandardStateRepository
from embry0.storage.repositories.watcher_stacks import WatcherStacksRepository

pytestmark = pytest.mark.requires_postgres

#: What covered_slugs gives a key covering acme/web, which was Acme/Old-Web.
COVERED = ["acme/old-web", "acme/web"]
#: The rows each list holds: the key's repository, the same under its old slug in another case, another repository.
STORED = ("acme/web", "Acme/Old-Web", "acme/api")


async def _seed(db: DatabasePool) -> None:
    for repo in STORED:
        await db.execute("INSERT INTO repos (repo, active) VALUES ($1, true)", repo)
        await db.execute(
            "INSERT INTO repo_setup_proposals (id, repo, status) VALUES ($1, $2, 'pending')", f"setup-{repo}", repo
        )
        await WatcherStacksRepository(db).upsert(WatcherStack(repo=repo, logql='{job="x"}'))
        await ContextConfigRepository(db).set_repo(repo, system_context="s", assistant_context="a")
        await db.execute("INSERT INTO org_repo_facts (owner, repo, facts) VALUES ('acme', $1, '{}')", repo)
        await db.execute(
            "INSERT INTO repo_standard_state (repo, owner, kind, mode) VALUES ($1, 'acme', 'design', 'advise')", repo
        )


def _slugs(rows: list[Any]) -> list[str]:
    return sorted(str(row["repo"] if isinstance(row, dict) else row.repo) for row in rows)


#: Each list, called with ``repos``.
LISTS: dict[str, Callable[[DatabasePool, list[str] | None], Awaitable[list[Any]]]] = {
    "the registry": lambda db, repos: ReposRepository(db).list_all(include_inactive=True, repos=repos),
    "the waiting setup proposals": lambda db, repos: RepoSetupProposalsRepository(db).list_pending(repos=repos),
    "the watched stacks": lambda db, repos: WatcherStacksRepository(db).list_all(repos=repos),
    "the agent contexts": lambda db, repos: ContextConfigRepository(db).list_repos(repos=repos),
    "the discovery facts": lambda db, repos: OrgReposRepository(db).list_for_owner("acme", repos=repos),
    "the standards' states": lambda db, repos: RepoStandardStateRepository(db).list_for_owner("acme", repos=repos),
}


@pytest.mark.parametrize("name", list(LISTS))
async def test_a_list_holds_only_the_covered_repositories_rows(name: str, db_with_migrations: DatabasePool) -> None:
    await _seed(db_with_migrations)
    narrowed = _slugs(await LISTS[name](db_with_migrations, COVERED))
    every = _slugs(await LISTS[name](db_with_migrations, None))
    none = await LISTS[name](db_with_migrations, [])
    assert narrowed == ["Acme/Old-Web", "acme/web"], (
        f"{name}, narrowed to acme/web, holds its row and the one stored under its old slug, and no other: {narrowed}"
    )
    assert every == sorted(STORED), f"{name}, unnarrowed, holds every row as before: {every}"
    assert none == [], f"{name}: an empty list selects nothing: {none}"
