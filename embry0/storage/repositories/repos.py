"""Repo registry — the first-class list of target repos embry0 knows (RAV-1251).

A repo exists in embry0 when it has a row here. Rows are created explicitly
(POST /repos), by writing a QA config (the config repository auto-registers),
or by the migration-52 backfill from the pre-registry configured surfaces
(repo_preferences / repo_environment / and the since-retired
qa_workspace_provider_overrides table, dropped in migration 56).
``active=false`` soft-hides a repo from listings without dropping its
config/env rows.
"""

from __future__ import annotations

from typing import Any

import structlog

from embry0.storage import repo_renames
from embry0.storage.database import DatabasePool
from embry0.storage.repo_renames import repo_slug_variants

logger = structlog.get_logger(__name__)

_COLUMNS = "repo, forge, active, notes, docker_access, created_at, updated_at"

# Forges embry0 can route to. Kept here rather than in a DB CHECK so adding
# one is a code change reviewed alongside its client and proxy, not a silent
# data value that nothing knows how to clone (#162).
SUPPORTED_FORGES = ("github", "gitlab")
DEFAULT_FORGE = "github"


class UnregisteredRepoError(LookupError):
    """``forge_for`` was asked about a repo with no ``repos`` row, and the
    deployment set ``DEFAULT_FORGE=""`` to refuse guessing (Spec 1A §3.8).

    LookupError rather than a bare Exception: it IS a failed lookup, and the
    base class keeps callers that already catch LookupError working.
    """


class ReposRepository:
    """CRUD for the ``repos`` registry table."""

    def __init__(self, db: DatabasePool, *, default_forge: str = DEFAULT_FORGE) -> None:
        if default_forge and default_forge not in SUPPORTED_FORGES:
            raise ValueError(
                f"unsupported default forge {default_forge!r} — expected one of "
                f"{list(SUPPORTED_FORGES)} or '' to refuse unregistered repos"
            )
        self._db = db
        self._default_forge = default_forge

    @property
    def default_forge(self) -> str:
        """The forge assumed for unregistered repos; "" when the deployment
        refuses to assume one (Spec 1A §3.8).

        Read-only and public so a caller that must decide what to do when the
        lookup itself fails — ``services.forge_resolver`` — can ask, instead
        of falling back to the module-level ``DEFAULT_FORGE`` and cloning from
        github.com on precisely the tenants that set this empty.
        """
        return self._default_forge

    async def get(self, repo: str) -> dict[str, Any] | None:
        row = await self._db.fetchrow(f"SELECT {_COLUMNS} FROM repos WHERE repo = $1", repo)
        return dict(row) if row else None

    async def register(self, repo: str, notes: str = "", forge: str = "") -> dict[str, Any]:
        """Idempotent registration; an existing row keeps its fields.

        ``forge`` is validated here rather than by a DB CHECK: an unknown
        value would register a repo nothing can clone, and failing at the
        boundary gives a far better error than a later clone failure.

        An empty ``forge`` means "unspecified" and inherits the *configured*
        default, exactly as :meth:`forge_for` does — not the module constant.
        On a tenant that set ``DEFAULT_FORGE=""`` to stop embry0 guessing
        (Spec 1A §3.8), writing a guessed ``github`` row would be worse than
        writing none: ``forge_for`` would then succeed, ERR_REPO_UNREGISTERED
        would never fire, and every sandbox would clone github.com. So an
        unspecified forge on such a tenant raises instead.
        """
        forge = forge or self._default_forge
        if not forge:
            raise ValueError(
                f"repo {repo!r} needs an explicit forge — this deployment runs with "
                f"DEFAULT_FORGE empty and never guesses one (expected one of {list(SUPPORTED_FORGES)})"
            )
        if forge not in SUPPORTED_FORGES:
            raise ValueError(f"unsupported forge {forge!r} — expected one of {list(SUPPORTED_FORGES)}")
        await self._db.execute(
            """
            INSERT INTO repos (repo, notes, forge) VALUES ($1, $2, $3)
            ON CONFLICT (repo) DO NOTHING
            """,
            repo,
            notes,
            forge,
        )
        out = await self.get(repo)
        assert out is not None
        return out

    async def set_active(self, repo: str, active: bool) -> dict[str, Any] | None:
        await self._db.execute(
            "UPDATE repos SET active = $2, updated_at = NOW() WHERE repo = $1",
            repo,
            active,
        )
        return await self.get(repo)

    async def docker_access(self, repo: str) -> bool:
        """Has an admin granted ``repo`` the shared Docker daemon (#522)?

        False for an unregistered repo: the grant lives on the registry row,
        so a repo nobody registered has never been granted anything.
        """
        value = await self._db.fetchval("SELECT docker_access FROM repos WHERE repo = $1", repo)
        return bool(value)

    async def set_docker_access(self, repo: str, enabled: bool) -> dict[str, Any] | None:
        """Grant or revoke the shared Docker daemon for ``repo``. None when unregistered.

        Callers are admin-tier routes only; nothing else in embry0 writes it.
        """
        await self._db.execute(
            "UPDATE repos SET docker_access = $2, updated_at = NOW() WHERE repo = $1",
            repo,
            enabled,
        )
        return await self.get(repo)

    async def list_all(self, include_inactive: bool = False) -> list[dict[str, Any]]:
        where = "" if include_inactive else "WHERE active"
        rows = await self._db.fetch(f"SELECT {_COLUMNS} FROM repos {where} ORDER BY repo")
        return [dict(r) for r in rows]

    async def under_owner(self, owner: str) -> list[str]:
        """Every repository registered under ``owner``, active or not: its slug's first segment, any case, and each
        registered under an old slug ``REPO_RENAMES`` maps to a repository of ``owner``."""
        renamed_in = sorted(
            old.lower()
            for old, new in repo_renames.REPO_RENAMES.items()
            if new.split("/", 1)[0].lower() == owner.lower()
        )
        rows = await self._db.fetch(
            "SELECT repo FROM repos WHERE lower(split_part(repo, '/', 1)) = lower($1) OR lower(repo) = ANY($2::text[]) "
            "ORDER BY repo",
            owner,
            renamed_in,
        )
        return [str(r["repo"]) for r in rows]

    async def forge_for(self, repo: str) -> str:
        """Forge for ``repo``; the configured default for unregistered paths.

        Callers resolving a clone URL or API base need an answer even for a
        repo that was never registered (a job submitted straight through the
        API, say). ``DEFAULT_FORGE=github`` is what every pre-#162 deployment
        meant and stays the default. A tenant that must never clone from
        github.com sets it empty, and an unregistered repo then raises
        instead of being silently cloned from the wrong host (Spec 1A §3.8).
        """
        row = await self._db.fetchrow("SELECT forge FROM repos WHERE repo = $1", repo)
        if row:
            return str(row["forge"])
        if not self._default_forge:
            raise UnregisteredRepoError(
                f"repo {repo!r} is not registered and DEFAULT_FORGE is empty — "
                "register it with its forge first (POST /repos with "
                '{"repo": "<path>", "forge": "gitlab"} or the MCP repo_register tool).'
            )
        return self._default_forge

    async def registered_as(self, repo: str) -> str | None:
        """The ``repos`` row ``repo`` is registered under, or None (#360).

        Looked up under every spelling the repo may carry — the slug itself,
        its current name and each old name ``REPO_RENAMES`` maps to it — and
        case-insensitively, as the forges treat paths. A repo registered
        before a rename, or a tracker stamp still naming the old slug, is
        therefore still registered. An inactive row counts: ``active=false``
        hides a repo from listings, it does not unregister it.
        """
        spellings = {repo.lower(), *(slug.lower() for slug in repo_slug_variants(repo))}
        row = await self._db.fetchrow(
            "SELECT repo FROM repos WHERE lower(repo) = ANY($1::text[]) ORDER BY lower(repo) = $2 DESC, repo LIMIT 1",
            sorted(spellings),
            repo.lower(),
        )
        return str(row["repo"]) if row else None

    async def delete(self, repo: str) -> None:
        """Hard delete — cascades to repo_qa_configs. Env/preference rows are
        deliberately untouched (no FK; they predate the registry)."""
        await self._db.execute("DELETE FROM repos WHERE repo = $1", repo)
