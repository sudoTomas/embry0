"""Unit tests for environment parse helper + reserved-key validation."""

import pytest

from embry0.api.v1.environment import _parse_env_file


def test_parse_env_file_basic():
    parsed = _parse_env_file("# API key for X\nAPI_KEY=abc123\nMODE=prod\n")
    assert parsed == [
        {
            "key": "API_KEY",
            "default_value": "abc123",
            "description": "API key for X",
            "suggested_type": "secret",
        },
        {
            "key": "MODE",
            "default_value": "prod",
            "description": "",
            "suggested_type": "config",
        },
    ]


def test_parse_env_file_classifies_secrets():
    parsed = _parse_env_file("GITHUB_TOKEN=\nDATABASE_PASSWORD=\nPORT=8080\n")
    types = {v["key"]: v["suggested_type"] for v in parsed}
    assert types == {
        "GITHUB_TOKEN": "secret",
        "DATABASE_PASSWORD": "secret",
        "PORT": "config",
    }


def test_parse_env_file_ignores_blank_separated_comments():
    content = "# orphan comment\n\nMODE=value\n"
    parsed = _parse_env_file(content)
    assert parsed == [{"key": "MODE", "default_value": "value", "description": "", "suggested_type": "config"}]


def test_parse_env_file_strips_quotes():
    parsed = _parse_env_file("NAME=\"hello world\"\nOTHER='quoted'\n")
    values = {v["key"]: v["default_value"] for v in parsed}
    assert values == {"NAME": "hello world", "OTHER": "quoted"}


def test_parse_env_file_empty_value_is_none():
    parsed = _parse_env_file("EMPTY=\n")
    assert parsed[0]["default_value"] is None


def test_env_var_input_rejects_reserved_keys():
    """Reserved infrastructure keys (EMBRY0_GIT_PROXY_URL etc.) must be blocked
    at the API boundary — allowing them would let a user repoint the credential
    proxy URL to an attacker endpoint, exfiltrating GitHub credentials from
    every subsequent job on that repo."""
    from pydantic import ValidationError

    from embry0.api.schemas.environment import RESERVED_ENV_KEYS, EnvVarInput

    assert "EMBRY0_GIT_PROXY_URL" in RESERVED_ENV_KEYS
    assert "GITHUB_TOKEN" in RESERVED_ENV_KEYS

    with pytest.raises(ValidationError) as exc:
        EnvVarInput(key="EMBRY0_GIT_PROXY_URL", value="http://evil.example", var_type="config")
    assert "reserved" in str(exc.value).lower()

    # Non-reserved keys still work
    v = EnvVarInput(key="DATABASE_URL", value="postgres://...", var_type="config")
    assert v.key == "DATABASE_URL"


def test_env_var_input_rejects_reserved_keys_case_insensitively():
    """Lower/mixed-case spellings of a reserved key or prefix must be blocked
    too — an exact-match check would let `github_token` or `Embry0_Xai_Proxy_Url`
    smuggle the same infrastructure secret past the API boundary."""
    from pydantic import ValidationError

    from embry0.api.schemas.environment import EnvVarInput

    with pytest.raises(ValidationError) as exc:
        EnvVarInput(key="github_token", value="ghp_smuggled", var_type="secret")
    assert "reserved" in str(exc.value).lower()

    with pytest.raises(ValidationError) as exc:
        EnvVarInput(key="Docker_Host", value="tcp://evil:2375", var_type="config")
    assert "reserved" in str(exc.value).lower()


def test_init_node_drops_reserved_user_env_keys(monkeypatch):
    """Defense-in-depth: even if a reserved key somehow made it into
    user_env_vars, init_node must filter it before passing to the sandbox."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from embry0.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value=("container-x", "a" * 43))
    sandbox_mgr.destroy = AsyncMock()
    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])
    docker.run_cmd = AsyncMock(return_value="")
    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = "http://git-proxy:9101"

    import embry0.workflows.issue_to_pr.nodes as nodes_module

    def _noop_writer():
        return lambda _e: None

    orig = getattr(nodes_module, "get_stream_writer", None)
    nodes_module.get_stream_writer = _noop_writer
    try:
        asyncio.run(
            init_node(
                state={
                    "job_id": "job-x",
                    "repo": "o/r",
                    "user_env_vars": {
                        "EMBRY0_GIT_PROXY_URL": "http://attacker.example",
                        "SAFE_VAR": "value",
                    },
                },
                config={
                    "configurable": {
                        "sandbox_manager": sandbox_mgr,
                        "docker": docker,
                        "proxy_manager": proxy_mgr,
                    }
                },
            )
        )
    finally:
        if orig is not None:
            nodes_module.get_stream_writer = orig

    _, kwargs = sandbox_mgr.create.call_args
    env_passed = kwargs.get("env", {}) or {}
    # Attacker key was dropped; infrastructure key stayed; safe key preserved.
    assert env_passed.get("EMBRY0_GIT_PROXY_URL") == "http://git-proxy:9101"
    assert env_passed.get("SAFE_VAR") == "value"
    assert "http://attacker" not in str(env_passed)


def test_init_node_drops_reserved_user_env_keys_case_insensitively(monkeypatch):
    """Defense-in-depth must catch case-variant spellings too — a plain
    dict of user_env_vars bypasses the EnvVarInput pattern/case validation,
    so `github_token` reaching init_node directly must still be dropped."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from embry0.workflows.issue_to_pr.nodes import init_node

    sandbox_mgr = MagicMock()
    sandbox_mgr.create = AsyncMock(return_value=("container-x", "a" * 43))
    sandbox_mgr.destroy = AsyncMock()
    docker = MagicMock()
    docker.build_exec_cmd = MagicMock(side_effect=lambda cid, cmd: ["docker", "exec", cid, *cmd])
    docker.run_cmd = AsyncMock(return_value="")
    proxy_mgr = MagicMock()
    proxy_mgr.git_proxy_url = "http://git-proxy:9101"

    import embry0.workflows.issue_to_pr.nodes as nodes_module

    def _noop_writer():
        return lambda _e: None

    orig = getattr(nodes_module, "get_stream_writer", None)
    nodes_module.get_stream_writer = _noop_writer
    try:
        asyncio.run(
            init_node(
                state={
                    "job_id": "job-x",
                    "repo": "o/r",
                    "user_env_vars": {
                        "github_token": "ghp_smuggled",
                        "SAFE_VAR": "value",
                    },
                },
                config={
                    "configurable": {
                        "sandbox_manager": sandbox_mgr,
                        "docker": docker,
                        "proxy_manager": proxy_mgr,
                    }
                },
            )
        )
    finally:
        if orig is not None:
            nodes_module.get_stream_writer = orig

    _, kwargs = sandbox_mgr.create.call_args
    env_passed = kwargs.get("env", {}) or {}
    assert "ghp_smuggled" not in str(env_passed)
    assert env_passed.get("SAFE_VAR") == "value"


# --- RAV-1294: partial-mask previews + repo env-var counts ---


def test_partial_mask_rules():
    from embry0.api.v1.environment import partial_mask

    glyphs = "•" * 8
    # Under 12 chars: fully masked, fixed length (no length leak).
    assert partial_mask("short") == glyphs
    assert partial_mask("Password") == glyphs
    assert partial_mask("abcdefghijk") == glyphs
    # 12+ chars: the last two only, enough to tell two secrets apart.
    assert partial_mask("s3cretPassw0") == f"{glyphs}w0"
    assert partial_mask("AZURxxxxxxxx7Q2F") == f"{glyphs}2F"


def test_no_secret_preview_shows_more_than_its_last_two_characters():
    """Every caller that can list env vars sees the preview, read keys included."""
    from embry0.api.v1.environment import partial_mask

    for length in range(65):
        secret = "".join(chr(ord("a") + i % 26) for i in range(length))
        preview = partial_mask(secret)
        shown = len(preview) - preview.count("•")
        allowed = 2 if length >= 12 else 0
        assert shown <= allowed, f"a {length}-character secret previews {shown} real characters: {preview!r}"
        assert preview.endswith(secret[-shown:] if shown else "•"), f"the preview is not the secret's tail: {preview!r}"


def _env_app():
    from unittest.mock import AsyncMock, MagicMock

    from embry0.api.app import create_app
    from embry0.config import Embry0Config

    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config)
    env_repo = MagicMock()
    env_repo.get_global = AsyncMock(return_value=[])
    env_repo.get_repo = AsyncMock(
        return_value=[
            {
                "repo": "o/r",
                "key": "AZURE_KEY",
                "value": "ciphertext-1",
                "var_type": "secret",
                "description": "",
                "required": False,
                "scope": "app",
            },
            {
                "repo": "o/r",
                "key": "MODE",
                "value": "prod",
                "var_type": "config",
                "description": "",
                "required": False,
                "scope": "app",
            },
        ]
    )
    app.state.env_repo = env_repo

    provider = MagicMock()
    provider.decrypt = AsyncMock(return_value="AZURsecretvalue7Q2F")
    app.state.secrets_provider = provider
    return app, env_repo, provider


@pytest.mark.asyncio
async def test_repo_environment_lists_partial_previews():
    from httpx import ASGITransport, AsyncClient

    app, _env_repo, _provider = _env_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/repos/o/r/environment")
    assert resp.status_code == 200
    by_key = {v["key"]: v for v in resp.json()["variables"]}
    secret = by_key["AZURE_KEY"]
    # The full value stays masked; the preview is recognisable-but-safe.
    assert secret["value"] == "****"
    assert secret["masked_preview"] == "•" * 8 + "2F"
    # Config vars carry their plain value and no preview.
    assert by_key["MODE"]["value"] == "prod"
    assert by_key["MODE"]["masked_preview"] is None


def _put_app():
    """App whose env repo remembers what a PUT stored."""
    from unittest.mock import AsyncMock

    app, env_repo, provider = _env_app()
    provider.encrypt = AsyncMock(side_effect=lambda plaintext: f"enc:{plaintext}")
    env_repo.set_repo = AsyncMock()
    env_repo.set_global = AsyncMock()
    env_repo.get_global = AsyncMock(
        return_value=[
            {
                "key": "AZURE_KEY",
                "value": "ciphertext-1",
                "var_type": "secret",
                "description": "",
                "required": False,
                "scope": "app",
            }
        ]
    )
    return app, env_repo, provider


def _secret_roundtrip_payload() -> dict[str, object]:
    return {
        "variables": [
            {
                "key": "AZURE_KEY",
                "value": "****",
                "var_type": "secret",
                "description": "kept",
                "required": False,
                "scope": "app",
            },
            {
                "key": "MODE",
                "value": "staging",
                "var_type": "config",
                "description": "",
                "required": False,
                "scope": "app",
            },
        ]
    }


@pytest.mark.asyncio
async def test_put_repo_environment_keeps_a_secret_when_the_client_sends_the_mask():
    from httpx import ASGITransport, AsyncClient

    app, env_repo, provider = _put_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    ) as client:
        resp = await client.put("/api/v1/repos/o/r/environment", json=_secret_roundtrip_payload())
    assert resp.status_code == 200
    saved = {row["key"]: row for row in env_repo.set_repo.await_args.args[1]}
    assert saved["AZURE_KEY"]["value"] == "ciphertext-1", "PUT stored the mask over the existing secret"
    assert "_ciphertext" not in saved["AZURE_KEY"]
    assert saved["AZURE_KEY"]["description"] == "kept"
    assert saved["MODE"]["value"] == "staging"
    assert all(call.args[0] != "****" for call in provider.encrypt.await_args_list)


@pytest.mark.asyncio
async def test_put_global_environment_keeps_a_secret_when_the_client_sends_the_mask():
    from httpx import ASGITransport, AsyncClient

    app, env_repo, provider = _put_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    ) as client:
        resp = await client.put("/api/v1/environment/global", json=_secret_roundtrip_payload())
    assert resp.status_code == 200
    saved = {row["key"]: row for row in env_repo.set_global.await_args.args[0]}
    assert saved["AZURE_KEY"]["value"] == "ciphertext-1", "PUT stored the mask over the existing secret"
    assert "_ciphertext" not in saved["AZURE_KEY"]
    assert all(call.args[0] != "****" for call in provider.encrypt.await_args_list)


@pytest.mark.asyncio
async def test_put_rejects_a_new_secret_whose_value_is_the_mask():
    from httpx import ASGITransport, AsyncClient

    app, env_repo, _provider = _put_app()
    payload = {
        "variables": [
            {
                "key": "BRAND_NEW",
                "value": "****",
                "var_type": "secret",
                "description": "",
                "required": False,
                "scope": "app",
            }
        ]
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    ) as client:
        resp = await client.put("/api/v1/repos/o/r/environment", json=payload)
    assert resp.status_code == 400, "a mask with no stored secret was accepted"
    env_repo.set_repo.assert_not_awaited()


@pytest.mark.asyncio
async def test_put_encrypts_a_secret_when_the_client_sends_a_new_value():
    from httpx import ASGITransport, AsyncClient

    app, env_repo, _provider = _put_app()
    payload = {
        "variables": [
            {
                "key": "AZURE_KEY",
                "value": "rotated-value",
                "var_type": "secret",
                "description": "",
                "required": False,
                "scope": "app",
            }
        ]
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"X-Requested-With": "XMLHttpRequest"}
    ) as client:
        resp = await client.put("/api/v1/repos/o/r/environment", json=payload)
    assert resp.status_code == 200
    saved = {row["key"]: row for row in env_repo.set_repo.await_args.args[1]}
    assert saved["AZURE_KEY"]["value"] == "enc:rotated-value"


@pytest.mark.asyncio
async def test_repo_environment_preview_fails_open_on_decrypt_error():
    from unittest.mock import AsyncMock

    from httpx import ASGITransport, AsyncClient

    app, _env_repo, provider = _env_app()
    provider.decrypt = AsyncMock(side_effect=RuntimeError("bad key"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/repos/o/r/environment")
    assert resp.status_code == 200
    secret = next(v for v in resp.json()["variables"] if v["key"] == "AZURE_KEY")
    assert secret["value"] == "****"
    assert secret["masked_preview"] is None


@pytest.mark.asyncio
async def test_list_repos_annotates_env_var_counts():
    from unittest.mock import AsyncMock, MagicMock

    from httpx import ASGITransport, AsyncClient

    from embry0.api.app import create_app
    from embry0.config import Embry0Config

    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config)
    repos_repo = MagicMock()
    repos_repo.list_all = AsyncMock(
        return_value=[
            {"repo": "acme/backend", "active": True, "notes": ""},
            {"repo": "acme/console", "active": True, "notes": ""},
            {"repo": "acme/empty", "active": True, "notes": ""},
        ]
    )
    app.state.repos_repo = repos_repo
    configs_repo = MagicMock()
    configs_repo.list_repos = AsyncMock(return_value=["acme/backend"])
    app.state.qa_configs_repo = configs_repo
    env_repo = MagicMock()
    env_repo.counts_by_repo = AsyncMock(
        return_value={
            "acme/backend": {"total": 2, "secrets": 2},
            "acme/console": {"total": 22, "secrets": 20},
        }
    )
    app.state.env_repo = env_repo

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/repos")
    assert resp.status_code == 200
    by_repo = {r["repo"]: r for r in resp.json()}
    assert by_repo["acme/backend"]["env_var_count"] == 2
    assert by_repo["acme/console"]["env_var_count"] == 22
    assert by_repo["acme/console"]["env_secret_count"] == 20
    assert by_repo["acme/empty"]["env_var_count"] == 0
