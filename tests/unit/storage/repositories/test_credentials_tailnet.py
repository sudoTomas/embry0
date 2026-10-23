"""The tailnet_api_key credential kind (ADR 0013, migration 100): storage, binding rules and resolution."""

from __future__ import annotations

import asyncpg
import pytest
from structlog.testing import capture_logs

from embry0.services.credentials.resolver import (
    CredentialMissingError,
    CredentialResolver,
    CredentialUnavailableError,
)
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.credentials import BindingValidationError, CredentialsRepository

pytestmark = [pytest.mark.requires_postgres]

SECRET = "tskey-api-unit-test-secret-value"


class FakeSecrets:
    async def encrypt(self, plaintext: str) -> str:
        return "enc:" + plaintext

    async def decrypt(self, ciphertext: str) -> str:
        return ciphertext.removeprefix("enc:")


async def _tailnet_key(repo: CredentialsRepository, label: str = "tailnet", tailnet: str = "example.com") -> dict:
    return await repo.create(
        kind="tailnet_api_key",
        provider="tailscale",
        host="api.tailscale.com",
        label=label,
        secret_ciphertext="enc:" + SECRET,
        masked_preview="tskey-****alue",
        created_by="test",
        metadata={"tailnet": tailnet},
    )


async def test_the_kind_is_stored_with_its_metadata(db_with_migrations: DatabasePool) -> None:
    repo = CredentialsRepository(db_with_migrations)
    row = await _tailnet_key(repo)
    assert (row["kind"], row["provider"], row["metadata"]) == (
        "tailnet_api_key",
        "tailscale",
        {"tailnet": "example.com"},
    )
    assert "secret_ciphertext" not in row
    listed = await repo.list_all()
    assert listed[0]["metadata"] == {"tailnet": "example.com"}


async def test_other_kinds_keep_empty_metadata(db_with_migrations: DatabasePool) -> None:
    repo = CredentialsRepository(db_with_migrations)
    row = await repo.create(
        kind="forge_token",
        provider="github",
        host="github.com",
        label="alice",
        secret_ciphertext="enc:x",
        masked_preview="ghp_****abcd",
        created_by="test",
    )
    assert row["metadata"] == {}


async def test_the_schema_still_refuses_unknown_kinds_and_providers(db_with_migrations: DatabasePool) -> None:
    for kind, provider in (("vpn_key", "tailscale"), ("tailnet_api_key", "wireguard")):
        with pytest.raises(asyncpg.CheckViolationError):
            await db_with_migrations.execute(
                "INSERT INTO credentials (id, kind, provider, host, label, secret_ciphertext, masked_preview, created_by)"
                " VALUES ('cred-000000000001', $1, $2, 'h', 'l', 'c', 'm', 't')",
                kind,
                provider,
            )


async def test_binds_at_global_scope_with_role_tailnet_only(db_with_migrations: DatabasePool) -> None:
    repo = CredentialsRepository(db_with_migrations)
    key = await _tailnet_key(repo)
    bound = await repo.replace_bindings("global", "", [{"credential_id": key["id"], "role": "tailnet"}], created_by="t")
    assert [(b["role"], b["provider"]) for b in bound] == [("tailnet", "tailscale")]
    for scope_kind, scope_value in (("repo", "acme/widgets"), ("owner", "github:acme"), ("host", "github.com")):
        with pytest.raises(BindingValidationError, match="not valid for role 'tailnet'"):
            await repo.replace_bindings(
                scope_kind, scope_value, [{"credential_id": key["id"], "role": "tailnet"}], created_by="t"
            )
    with pytest.raises(BindingValidationError, match="role 'forge' needs"):
        await repo.replace_bindings("global", "", [{"credential_id": key["id"], "role": "forge"}], created_by="t")


async def test_a_forge_token_cannot_take_the_tailnet_role(db_with_migrations: DatabasePool) -> None:
    repo = CredentialsRepository(db_with_migrations)
    gh = await repo.create(
        kind="forge_token",
        provider="github",
        host="github.com",
        label="alice",
        secret_ciphertext="enc:x",
        masked_preview="ghp_****abcd",
        created_by="test",
    )
    with pytest.raises(BindingValidationError, match="role 'tailnet' needs"):
        await repo.replace_bindings("global", "", [{"credential_id": gh["id"], "role": "tailnet"}], created_by="t")


async def test_resolve_tailnet_from_the_global_binding(db_with_migrations: DatabasePool) -> None:
    repo = CredentialsRepository(db_with_migrations)
    resolver = CredentialResolver(repo, FakeSecrets(), config=None, environ={})
    with pytest.raises(CredentialMissingError, match="bind it at global scope with role tailnet"):
        await resolver.resolve_tailnet()
    key = await _tailnet_key(repo)
    await repo.replace_bindings("global", "", [{"credential_id": key["id"], "role": "tailnet"}], created_by="t")
    with capture_logs() as logs:
        resolved = await resolver.resolve_tailnet()
    assert (resolved.id, resolved.secret, resolved.metadata) == (key["id"], SECRET, {"tailnet": "example.com"})
    assert SECRET not in repr(resolved), "the secret leaked through repr"
    assert SECRET not in repr(logs)


async def test_resolve_tailnet_by_id_refuses_another_kind_or_a_disabled_key(db_with_migrations: DatabasePool) -> None:
    repo = CredentialsRepository(db_with_migrations)
    resolver = CredentialResolver(repo, FakeSecrets(), config=None, environ={})
    key = await _tailnet_key(repo)
    assert (await resolver.resolve_tailnet(credential_id=key["id"])).secret == SECRET
    gh = await repo.create(
        kind="forge_token",
        provider="github",
        host="github.com",
        label="alice",
        secret_ciphertext="enc:x",
        masked_preview="ghp_****abcd",
        created_by="test",
    )
    with pytest.raises(CredentialUnavailableError, match="not a tailnet_api_key"):
        await resolver.resolve_tailnet(credential_id=gh["id"])
    await repo.set_status(key["id"], "disabled")
    with pytest.raises(CredentialUnavailableError, match="disabled"):
        await resolver.resolve_tailnet(credential_id=key["id"])
    with pytest.raises(CredentialMissingError):
        await resolver.resolve_tailnet(credential_id="cred-000000000000")


async def test_a_disabled_global_key_is_skipped(db_with_migrations: DatabasePool) -> None:
    repo = CredentialsRepository(db_with_migrations)
    resolver = CredentialResolver(repo, FakeSecrets(), config=None, environ={})
    key = await _tailnet_key(repo)
    await repo.replace_bindings("global", "", [{"credential_id": key["id"], "role": "tailnet"}], created_by="t")
    await repo.set_status(key["id"], "disabled")
    with pytest.raises(CredentialMissingError, match="skipped"):
        await resolver.resolve_tailnet()
