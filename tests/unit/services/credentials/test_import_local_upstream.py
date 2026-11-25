"""`credentials import-env` and the retired ``LOCAL_LLM_UPSTREAM`` (ADR 0032): the variable that sent every custom run
to one server becomes the environment's custom credential, and only that one."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from embry0.services.credentials import import_env
from embry0.services.credentials.import_env import import_from_env
from embry0.storage.database import DatabasePool
from embry0.storage.repositories.credentials import CredentialsRepository
from tests.fixtures.provider_catalogue import enable_providers

pytestmark = [pytest.mark.requires_postgres]

#: The key the import stores when no variable gives one.
PLACEHOLDER_KEY = getattr(import_env, "PLACEHOLDER_KEY", "no-key")


class FakeSecrets:
    async def encrypt(self, plaintext: str) -> str:
        return "enc:" + plaintext

    async def decrypt(self, ciphertext: str) -> str:
        return ciphertext.removeprefix("enc:")


@pytest.fixture(autouse=True)
def _custom_on(monkeypatch: pytest.MonkeyPatch) -> None:
    enable_providers(monkeypatch, "custom")


def _config(upstream: str = "http://10.0.0.5:8000/v1/", key: str = "server-key") -> SimpleNamespace:
    return SimpleNamespace(gitlab_url="", local_llm_upstream=upstream, local_llm_api_key=key)


async def _import(repo: CredentialsRepository, config: Any, env: dict[str, str] | None = None, **kw: Any) -> Any:
    return await import_from_env(
        repo,
        FakeSecrets(),
        config,
        environ=env or {},
        claude_credentials_file="/no/such/file",
        xai_store_path="/no/such/store",
        grok_auth_file="/no/such/auth.json",
        **kw,
    )


async def _custom(repo: CredentialsRepository, label: str = "api-key") -> dict[str, Any]:
    row = await repo.find_by_label("custom", label)
    assert row is not None, f"no custom/{label}"
    return row


async def test_the_upstream_becomes_the_environment_s_custom_credential(db_with_migrations: DatabasePool) -> None:
    repo = CredentialsRepository(db_with_migrations)
    # CUSTOM_LLM_BASE_URL named the server background calls reached; LOCAL_LLM_UPSTREAM the one every run did.
    env = {"CUSTOM_LLM_API_KEY": "custom-env-key", "CUSTOM_LLM_BASE_URL": "http://elsewhere.test/v1"}
    report = await _import(repo, _config(), env)
    assert "custom/api-key" in report.created, f"LOCAL_LLM_UPSTREAM made no credential: {report.as_dict()}"
    row = await _custom(repo)
    assert (row["host"], row["metadata"]) == ("10.0.0.5", {"base_url": "http://10.0.0.5:8000/v1"}), (
        f"the environment's custom credential must name the server LOCAL_LLM_UPSTREAM named: {row}"
    )
    assert row["secret_ciphertext"] == "enc:server-key", "LOCAL_LLM_API_KEY is the key that server checks"
    assert "global:llm -> custom/api-key" in " ".join(report.bound), f"not bound for every job: {report.bound}"


@pytest.mark.parametrize(
    ("local_key", "env_key", "expected"),
    [
        ("server-key", "custom-env-key", "server-key"),
        ("", "custom-env-key", "custom-env-key"),
        ("", "", PLACEHOLDER_KEY),
    ],
    ids=["LOCAL_LLM_API_KEY", "CUSTOM_LLM_API_KEY", "placeholder"],
)
async def test_the_key_is_the_server_s_else_the_custom_one_else_a_placeholder(
    db_with_migrations: DatabasePool, local_key: str, env_key: str, expected: str
) -> None:
    repo = CredentialsRepository(db_with_migrations)
    env = {"CUSTOM_LLM_API_KEY": env_key} if env_key else {}
    await _import(repo, _config(key=local_key), env)
    assert (await _custom(repo))["secret_ciphertext"] == f"enc:{expected}", f"the key imported was not {expected!r}"


async def test_a_stored_credential_with_another_address_moves_only_with_replace(
    db_with_migrations: DatabasePool,
) -> None:
    repo = CredentialsRepository(db_with_migrations)
    await _import(repo, _config(upstream="http://old-box.test:8000/v1"))
    before = await _custom(repo)

    kept = await _import(repo, _config())
    assert "custom/api-key: exists with a different address (pass replace=true)" in kept.skipped, (
        f"another address must be named, as another secret is: {kept.as_dict()}"
    )
    unchanged = await _custom(repo)
    assert (unchanged["host"], unchanged["metadata"]) == (before["host"], before["metadata"]), (
        "the address moved without replace"
    )

    await _import(repo, _config(), replace=True)
    moved = await _custom(repo)
    assert (moved["id"], moved["host"], moved["metadata"]) == (
        before["id"],
        "10.0.0.5",
        {"base_url": "http://10.0.0.5:8000/v1"},
    ), f"with replace, the same credential names the new server: {moved}"
    assert moved["secret_ciphertext"] == "enc:server-key"


async def test_a_different_secret_and_address_are_both_named(db_with_migrations: DatabasePool) -> None:
    repo = CredentialsRepository(db_with_migrations)
    await _import(repo, _config(upstream="http://old-box.test:8000/v1", key="old-key"))
    report = await _import(repo, _config())
    assert "custom/api-key: exists with a different secret and address (pass replace=true)" in report.skipped, (
        f"the report must name what it did not import, and why: {report.as_dict()}"
    )


async def test_a_stored_custom_credential_of_its_own_keeps_its_address_and_key(
    db_with_migrations: DatabasePool,
) -> None:
    repo = CredentialsRepository(db_with_migrations)
    own = await repo.create(
        kind="llm_api_key",
        provider="custom",
        host="gpu-box.test",
        label="gpu-box",
        secret_ciphertext="enc:its-own-key",
        masked_preview="its-****-key",
        created_by="test",
        metadata={"base_url": "http://gpu-box.test:8000/v1"},
    )
    await _import(repo, _config(), replace=True)
    kept = await _custom(repo, "gpu-box")
    assert (kept["id"], kept["host"], kept["metadata"], kept["secret_ciphertext"]) == (
        own["id"],
        "gpu-box.test",
        {"base_url": "http://gpu-box.test:8000/v1"},
        "enc:its-own-key",
    ), "the import wrote a stored custom credential other than the environment's"


@pytest.mark.parametrize("upstream", ["models.test:8000/v1", "ftp://models.test/v1", "http:///v1"])
async def test_an_upstream_that_is_no_http_url_is_not_imported(db_with_migrations: DatabasePool, upstream: str) -> None:
    repo = CredentialsRepository(db_with_migrations)
    report = await _import(repo, _config(upstream=upstream))
    assert await repo.find_by_label("custom", "api-key") is None, f"{upstream!r} was imported"
    assert "custom/api-key: LOCAL_LLM_UPSTREAM is not an http(s) URL with a host" in report.skipped, (
        f"the report must name what it did not import, and why: {report.as_dict()}"
    )


@pytest.mark.parametrize("upstream", ["http://127.0.0.1:8000/v1", "http://169.254.169.254/v1", "http://localhost/v1"])
async def test_an_upstream_on_this_machine_or_link_local_is_not_imported(
    db_with_migrations: DatabasePool, upstream: str
) -> None:
    repo = CredentialsRepository(db_with_migrations)
    report = await _import(repo, _config(upstream=upstream))
    assert await repo.find_by_label("custom", "api-key") is None, f"{upstream!r} was imported as a model server"
    assert any(
        line.startswith("custom/api-key: LOCAL_LLM_UPSTREAM may not be a model server") for line in report.skipped
    ), f"the report must say why nothing was imported: {report.as_dict()}"


async def test_a_custom_base_url_on_link_local_is_not_imported(db_with_migrations: DatabasePool) -> None:
    repo = CredentialsRepository(db_with_migrations)
    env = {"CUSTOM_LLM_API_KEY": "k", "CUSTOM_LLM_BASE_URL": "http://169.254.169.254/v1"}
    report = await _import(repo, SimpleNamespace(gitlab_url=""), env)
    assert await repo.find_by_label("custom", "api-key") is None, "a link-local CUSTOM_LLM_BASE_URL was imported"
    assert any(line.startswith("custom/api-key: its base URL may not be a model server") for line in report.skipped), (
        f"the report must say why nothing was imported: {report.as_dict()}"
    )


async def test_with_the_custom_provider_off_nothing_is_imported_and_the_report_says_why(
    db_with_migrations: DatabasePool, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable_providers(monkeypatch)
    repo = CredentialsRepository(db_with_migrations)
    report = await _import(repo, _config())
    assert await repo.find_by_label("custom", "api-key") is None
    assert (
        "custom/api-key: LOCAL_LLM_UPSTREAM is set but the catalogue has the custom provider off" in report.skipped
    ), f"the report must name what it did not import, and why: {report.as_dict()}"
