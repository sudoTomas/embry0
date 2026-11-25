import os
from unittest.mock import patch

import pytest

from embry0.config import Embry0Config


def test_config_defaults():
    """Config loads with sensible defaults when no env vars set."""
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(
            _env_file=None,  # type: ignore[call-arg]
        )
    assert config.database_url == "postgresql://embry0:embry0@localhost:5432/embry0"
    for moved in ("max_budget_usd", "daily_budget_cap_usd", "monthly_budget_cap_usd"):
        assert moved not in Embry0Config.model_fields, f"{moved} is the settings document's budget, not the config's"


def test_dead_env_var_is_silently_ignored():
    """Unknown env vars (like the deleted max_global_concurrent_jobs) must not raise."""
    env = {"MAX_GLOBAL_CONCURRENT_JOBS": "5", "SANDBOX_MEMORY": "8g", "SANDBOX_CPUS": "4"}
    with patch.dict(os.environ, env, clear=True):
        cfg = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    # Pydantic extra="ignore" means this just works; no assertion needed beyond no exception.
    assert cfg is not None


def test_config_from_env():
    """Config reads from environment variables."""
    env = {
        "DATABASE_URL": "postgresql://user:pass@db:5432/mydb",
        "GITHUB_TOKEN": "ghp_test123",
        "ANTHROPIC_API_KEY": "sk-ant-test",
    }
    with patch.dict(os.environ, env, clear=True):
        config = Embry0Config(
            _env_file=None,  # type: ignore[call-arg]
        )
    assert config.database_url == "postgresql://user:pass@db:5432/mydb"
    assert config.github_token == "ghp_test123"
    assert config.anthropic_api_key == "sk-ant-test"


def test_config_cors_origins_parsing():
    """Comma-separated CORS origins are parsed into a list."""
    env = {"ALLOWED_CORS_ORIGINS": "http://localhost:3001, http://example.com"}
    with patch.dict(os.environ, env, clear=True):
        config = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    assert config.allowed_cors_origins_list == ["http://localhost:3001", "http://example.com"]


def test_config_cors_origins_default():
    """Empty CORS origins defaults to localhost:3001."""
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    assert config.allowed_cors_origins_list == ["http://localhost:3001"]


def test_config_trigger_labels_parsing():
    """Comma-separated trigger labels are parsed into a list."""
    env = {"TRIGGER_LABELS": "embry0, auto-fix, bot"}
    with patch.dict(os.environ, env, clear=True):
        config = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    assert config.trigger_labels_list == ["embry0", "auto-fix", "bot"]


def test_config_telegram_sender_allowlist_parsing():
    """Comma-separated ids parse to ints; junk entries are kept as invalid, not dropped."""
    env = {"TELEGRAM_ALLOWED_SENDER_IDS": "111, -222 ,, notanid, --5"}
    with patch.dict(os.environ, env, clear=True):
        config = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    allowlist = config.telegram_sender_allowlist
    assert allowlist is not None
    assert allowlist.ids == frozenset({111, -222})
    assert allowlist.invalid == ("notanid", "--5")


def test_config_telegram_sender_allowlist_unset_means_no_gate():
    """Unset means no extra gate beyond the chat id."""
    with patch.dict(os.environ, {}, clear=True):
        config = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    assert config.telegram_sender_allowlist is None


def test_config_telegram_sender_allowlist_all_invalid_admits_nobody():
    """Usernames are not ids: a set-but-unparseable allowlist must fail closed (#247)."""
    env = {"TELEGRAM_ALLOWED_SENDER_IDS": "@operator, @alice"}
    with patch.dict(os.environ, env, clear=True):
        config = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    allowlist = config.telegram_sender_allowlist
    assert allowlist is not None
    assert allowlist.ids == frozenset()
    assert allowlist.invalid == ("@operator", "@alice")


def test_config_reads_oauth_token():
    """The OAuth token env var flows into config."""
    env = {
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-test",
    }
    with patch.dict(os.environ, env, clear=True):
        config = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    assert config.claude_code_oauth_token == "sk-ant-oat01-test"


def test_retired_provider_env_vars_are_ignored():
    """RAV-1239: PROVIDER_MODE/OLLAMA_* are gone from config — setting them
    must neither raise nor resurface as attributes."""
    env = {
        "PROVIDER_MODE": "ollama",
        "OLLAMA_BASE_URL": "http://gpu:11434",
        "OLLAMA_MODEL": "codellama",
    }
    with patch.dict(os.environ, env, clear=True):
        config = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    assert not hasattr(config, "provider_mode")
    assert not hasattr(config, "ollama_base_url")
    assert not hasattr(config, "ollama_model")


def test_legacy_dev_mode_var_is_ignored():
    """The legacy DEV_MODE env var (split into AUTH_DEV_MODE/WEBHOOK_DEV_MODE
    on 2026-04-28, shim removed 2026-07-10) must have no effect."""
    env = {"DEV_MODE": "true"}
    with patch.dict(os.environ, env, clear=True):
        cfg = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    assert cfg.auth_dev_mode is False
    assert cfg.webhook_dev_mode is False


# -------- qa_browser_backend_default (#229 / #232) --------


def test_qa_browser_backend_default_is_playwright():
    from embry0.config import Embry0Config

    cfg = Embry0Config(_env_file=None)
    assert cfg.qa_browser_backend_default == "playwright"


def test_qa_browser_backend_default_rejects_unknown_at_startup():
    import pytest

    from embry0.config import Embry0Config

    with pytest.raises(Exception, match="unknown browser backend 'netscape'"):
        Embry0Config(_env_file=None, qa_browser_backend_default="netscape")


# -------- default_forge (Spec 1A §3.8) --------


def test_default_forge_defaults_to_github():
    with patch.dict(os.environ, {}, clear=True):
        cfg = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    assert cfg.default_forge == "github"


def test_default_forge_can_be_emptied_to_refuse_unregistered_repos():
    with patch.dict(os.environ, {"DEFAULT_FORGE": ""}, clear=True):
        cfg = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    assert cfg.default_forge == ""


# ---- WATCHER_LOGQL has no code default (#270) -------------------------------


def test_watcher_logql_has_no_code_default():
    """The old default named a compose project that no longer exists, so an
    unset WATCHER_LOGQL watched nothing and said nothing."""
    with patch.dict(os.environ, {}, clear=True):
        cfg = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    assert cfg.watcher_enabled is False
    assert cfg.watcher_logql == ""


def test_watcher_deploy_logql_has_no_code_default():
    """#527: the old default selected one deployment's Watchtower container.
    Empty skips the deploy markers; it is not a fault."""
    with patch.dict(os.environ, {}, clear=True):
        cfg = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    assert cfg.watcher_deploy_logql == "", (
        f"WATCHER_DEPLOY_LOGQL must default to empty, got {cfg.watcher_deploy_logql!r}"
    )


@pytest.mark.parametrize("logql", [None, "", "   "])
def test_watcher_enabled_without_logql_still_loads(logql):
    """The watcher is optional. Refusing to build the config would crash-loop
    the orchestrator under restart: unless-stopped and take the dashboard and
    every pipeline down with it — the watcher reports missing_logql instead."""
    env = {"WATCHER_ENABLED": "true"}
    if logql is not None:
        env["WATCHER_LOGQL"] = logql
    with patch.dict(os.environ, env, clear=True):
        cfg = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    assert cfg.watcher_enabled is True
    assert cfg.watcher_logql.strip() == ""


def test_watcher_enabled_with_logql_loads():
    env = {"WATCHER_ENABLED": "true", "WATCHER_LOGQL": '{compose_project="acme"} |~ "(?i)error"'}
    with patch.dict(os.environ, env, clear=True):
        cfg = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    assert cfg.watcher_logql == '{compose_project="acme"} |~ "(?i)error"'


def test_escaped_quote_watcher_logql_loads_verbatim():
    """Config cannot judge LogQL — Loki does, at watcher startup. What config
    must NOT do is 'repair' the value: the backslashes reach Loki as written,
    so the startup probe sees exactly what every tick would send."""
    escaped = '{compose_project=\\"acme\\"}'
    with patch.dict(os.environ, {"WATCHER_ENABLED": "true", "WATCHER_LOGQL": escaped}, clear=True):
        cfg = Embry0Config(_env_file=None)  # type: ignore[call-arg]
    assert cfg.watcher_logql == escaped
