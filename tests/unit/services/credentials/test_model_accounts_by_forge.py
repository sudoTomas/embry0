"""A model account bound to an owner serves that owner's repositories on its own forge, on a real database.

``github:acme`` is a GitHub organization and ``gitlab:acme`` a GitLab group: two owners. The resolver
reads an owner's model account on the forge the repository's work is on, its registration's (matched
in any case), else the deployment's default forge; a repository with neither reads no owner's account.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from embry0.services.credentials.resolver import CredentialResolver
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.credentials import CredentialsRepository
from embry0.storage.repositories.repos import ReposRepository
from tests.fixtures.repository_writes import repository_writes_app

pytestmark = [pytest.mark.requires_postgres]


class _Plain:
    async def encrypt(self, plaintext: str) -> str:
        return plaintext

    async def decrypt(self, ciphertext: str) -> str:
        return ciphertext


async def _account(
    repo: CredentialsRepository, label: str, scope_kind: str, scope_value: str, secrets: Any = None
) -> str:
    row = await repo.create(
        kind="llm_api_key",
        provider="anthropic",
        host="api.anthropic.com",
        label=label,
        secret_ciphertext=await (secrets or _Plain()).encrypt("secret-" + label),
        masked_preview="****",
        created_by="test",
    )
    await repo.replace_bindings(
        scope_kind, scope_value, [{"credential_id": str(row["id"]), "role": "llm"}], created_by="t"
    )
    return str(row["id"])


def _resolver(db: DatabasePool, default_forge: str) -> CredentialResolver:
    return CredentialResolver(
        CredentialsRepository(db),
        _Plain(),
        SimpleNamespace(github_token="", gitlab_token="", gitlab_url="https://gitlab.example.com"),
        environ={},
        cache_ttl_seconds=0,
        repo_forge=ReposRepository(db, default_forge=default_forge).forge_or_none,
    )


async def _labels(resolver: CredentialResolver, repos: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for full_repo in repos:
        try:
            out[full_repo] = (await resolver.resolve_llm(full_repo, "anthropic")).label
        except LookupError:
            out[full_repo] = None
    return out


async def test_an_owners_model_account_serves_its_repositories_on_its_forge_alone(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the resolver the app builds, as production builds it (DEFAULT_FORGE github)."""
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)  # the environment answers no repository here
    db = db_with_migrations
    async with repository_writes_app(db, monkeypatch) as (app, _keys, _values):
        await db.execute(
            "INSERT INTO repos (repo, forge) VALUES ('Acme/Web2', 'github'), ('acme/app', 'gitlab'),"
            " ('beta/app', 'gitlab')"
        )
        repo = CredentialsRepository(db)
        secrets = app.state.secrets_provider
        await _account(repo, "acme-github", "owner", "github:acme", secrets)
        await _account(repo, "beta-github", "owner", "github:beta", secrets)
        await _account(repo, "pool", "global", "", secrets)  # what a repository no owner's account serves reads
        resolver = app.state.credential_resolver
        resolver.invalidate()
        got = await _labels(resolver, ["acme/web2", "Acme/Web2", "acme/app", "beta/app", "acme/unregistered"])
        assert got == {
            "acme/web2": "acme-github",
            "Acme/Web2": "acme-github",
            "acme/app": "pool",
            "beta/app": "pool",
            "acme/unregistered": "acme-github",
        }, f"github:acme's account serves acme's GitHub repositories, and the default forge's unregistered: {got}"

        disabled = await _account(repo, "acme-gitlab", "owner", "gitlab:acme", secrets)
        await repo.set_status(disabled, "disabled", reason="spent")
        pool = (await repo.bindings_for_scope("global", ""))[0]["credential_id"]
        await repo.set_status(pool, "disabled", reason="spent")
        paused = {r: await resolver.llm_pause(r, "anthropic") for r in ("acme/web2", "acme/app")}
        assert paused["acme/web2"] is None and paused["acme/app"] is not None, (
            f"github:acme's usable account does not keep acme's GitLab repository on the provider: {paused}"
        )


async def test_a_repository_whose_forge_is_unknown_reads_no_owners_account(db_with_migrations: DatabasePool) -> None:
    """No registration and no default forge: the repository is under no owner the resolver can name."""
    db = db_with_migrations
    repo = CredentialsRepository(db)
    await _account(repo, "acme-github", "owner", "github:acme")
    await _account(repo, "acme-gitlab", "owner", "gitlab:acme")
    await _account(repo, "pool", "global", "")
    resolved = await _resolver(db, "").resolve_llm("acme/unregistered", "anthropic")
    assert resolved.label == "pool", f"an unplaced repository reads the global pool, not an owner's: {resolved}"
    assert "owner:acme -> skipped (the repository's forge is unknown)" in resolved.trace, resolved.trace
