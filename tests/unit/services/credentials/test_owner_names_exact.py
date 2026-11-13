"""Owner bindings compare owner names exactly, case aside, on a real database.

``my-group``, ``my.group`` and ``my_group`` are three owners on a forge that allows all three
characters (GitLab does). A credential bound to one serves that owner's repositories and no
other's: the binding is stored as the owner is spelled, lowercased, and every lookup
(``resolve_forge``, ``resolve_llm``, ``llm_pause``, ``bindings_for_scope``) asks for that
spelling. A key with no allowlist, a session and the master key resolve through these same
calls, so what holds here holds for them.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from embry0.services.credentials.resolver import CredentialResolver
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.credentials import CredentialsRepository
from embry0.storage.repositories.repos import ReposRepository

pytestmark = [pytest.mark.requires_postgres]

#: Three GitLab groups whose names differ only in the character between the words.
DASH, DOT, UNDERSCORE = "other-grp", "other.grp", "other_grp"


class _Plain:
    """Secrets stored as given, so a test reads which credential resolved by its secret."""

    async def encrypt(self, plaintext: str) -> str:
        return plaintext

    async def decrypt(self, ciphertext: str) -> str:
        return ciphertext


def _config() -> Any:
    return SimpleNamespace(github_token="", gitlab_token="", gitlab_url="https://gitlab.example.com")


async def _credential(repo: CredentialsRepository, label: str, *, provider: str, kind: str) -> str:
    host = {"gitlab": "gitlab.example.com", "github": "github.com"}.get(provider, "api.anthropic.com")
    row = await repo.create(
        kind=kind,
        provider=provider,
        host=host,
        label=label,
        secret_ciphertext="secret-" + label,
        masked_preview="****",
        created_by="test",
    )
    return str(row["id"])


async def _bind(repo: CredentialsRepository, scope_value: str, cid: str, role: str = "forge") -> list[dict]:
    return await repo.replace_bindings("owner", scope_value, [{"credential_id": cid, "role": role}], created_by="t")


def _resolver(repo: CredentialsRepository, db: DatabasePool) -> CredentialResolver:
    # No environment: a repository no binding serves resolves nothing, rather than an env token. A repository
    # nobody registered is on GitLab, as the deployment's default forge.
    return CredentialResolver(
        repo,
        _Plain(),
        _config(),
        environ={},
        cache_ttl_seconds=0,
        repo_forge=ReposRepository(db, default_forge="gitlab").forge_or_none,
    )


async def _forge_label(resolver: CredentialResolver, full_repo: str) -> str | None:
    try:
        return (await resolver.resolve_forge(full_repo, "gitlab")).label
    except LookupError:
        return None


async def test_an_owner_binding_serves_its_owner_and_no_owner_spelled_otherwise(
    db_with_migrations: DatabasePool,
) -> None:
    repo = CredentialsRepository(db_with_migrations)
    dash = await _credential(repo, "dash-bot", provider="gitlab", kind="forge_token")
    stored = await _bind(repo, "gitlab:Other-Grp", dash)
    assert [b["scope_value"] for b in stored] == ["gitlab:other-grp"], (
        f"the binding is stored as the owner is spelled, lowercased: {[b['scope_value'] for b in stored]}"
    )
    resolver = _resolver(repo, db_with_migrations)
    got = {owner: await _forge_label(resolver, f"{owner}/app") for owner in (DASH, DOT, UNDERSCORE, "Other-Grp")}
    assert got == {DASH: "dash-bot", DOT: None, UNDERSCORE: None, "Other-Grp": "dash-bot"}, (
        f"gitlab:other-grp's credential serves other-grp's repositories alone: {got}"
    )
    assert await repo.bindings_for_scope("owner", f"gitlab:{UNDERSCORE}") == [], (
        "other_grp's owner scope lists other-grp's binding"
    )
    assert await repo.bindings_for_scope("owner", f"gitlab:{DOT}") == [], "other.grp's owner scope lists other-grp's"


async def test_an_owner_spelled_with_an_underscore_has_its_own_binding(db_with_migrations: DatabasePool) -> None:
    """The reverse: a binding of other_grp serves neither other-grp nor other.grp, and each keeps its own."""
    repo = CredentialsRepository(db_with_migrations)
    dash = await _credential(repo, "dash-bot", provider="gitlab", kind="forge_token")
    under = await _credential(repo, "under-bot", provider="gitlab", kind="forge_token")
    await _bind(repo, f"gitlab:{UNDERSCORE}", under)
    resolver = _resolver(repo, db_with_migrations)
    got = {owner: await _forge_label(resolver, f"{owner}/app") for owner in (DASH, DOT, UNDERSCORE)}
    assert got == {DASH: None, DOT: None, UNDERSCORE: "under-bot"}, (
        f"gitlab:other_grp's credential serves other_grp's repositories alone: {got}"
    )
    await _bind(repo, f"gitlab:{DASH}", dash)
    resolver.invalidate()
    got = {owner: await _forge_label(resolver, f"{owner}/app") for owner in (DASH, DOT, UNDERSCORE)}
    assert got == {DASH: "dash-bot", DOT: None, UNDERSCORE: "under-bot"}, (
        f"two owners whose names differ in one character each keep their own binding: {got}"
    )
    by_scope = {
        owner: [b["label"] for b in await repo.bindings_for_scope("owner", f"gitlab:{owner}")]
        for owner in (DASH, DOT, UNDERSCORE)
    }
    assert by_scope == {DASH: ["dash-bot"], DOT: [], UNDERSCORE: ["under-bot"]}, by_scope


async def test_a_model_account_bound_to_an_owner_serves_its_owner_alone(db_with_migrations: DatabasePool) -> None:
    """An owner's model account is looked up under either forge's prefix, by the owner's exact name."""
    repo = CredentialsRepository(db_with_migrations)
    account = await _credential(repo, "dash-llm", provider="anthropic", kind="llm_api_key")
    await _bind(repo, f"gitlab:{DASH}", account, role="llm")
    resolver = _resolver(repo, db_with_migrations)
    got = {}
    for owner in (DASH, DOT, UNDERSCORE):
        try:
            got[owner] = (await resolver.resolve_llm(f"{owner}/app", "anthropic")).label
        except LookupError:
            got[owner] = None
    assert got == {DASH: "dash-llm", DOT: None, UNDERSCORE: None}, (
        f"other-grp's model account serves other-grp's repositories alone: {got}"
    )
    await repo.set_status(account, "disabled", reason="spent")
    paused = {owner: await resolver.llm_pause(f"{owner}/app", "anthropic") for owner in (DASH, UNDERSCORE)}
    assert paused[DASH] is not None and paused[UNDERSCORE] is not None, (
        f"with no account in the environment, both pause: {paused}"
    )
    other = await _credential(repo, "under-llm", provider="anthropic", kind="llm_api_key")
    await _bind(repo, f"gitlab:{UNDERSCORE}", other, role="llm")
    paused = {owner: await resolver.llm_pause(f"{owner}/app", "anthropic") for owner in (DASH, UNDERSCORE)}
    assert paused[UNDERSCORE] is None and paused[DASH] is not None, (
        f"other_grp's usable account does not keep other-grp's jobs on the provider: {paused}"
    )


async def test_known_owners_lists_each_registered_owner_as_spelled(db_with_migrations: DatabasePool) -> None:
    db = db_with_migrations
    await db.execute("INSERT INTO repos (repo, forge) VALUES ('Other-Grp/app', 'gitlab'), ('acme/web', 'github')")
    await db.execute("INSERT INTO organizations (owner, forge) VALUES ('other_grp', 'gitlab')")
    known = await CredentialsRepository(db).known_owners()
    assert known == {"gitlab": {DASH, UNDERSCORE}, "github": {"acme"}}, known
