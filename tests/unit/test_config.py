import os
from unittest.mock import patch

from athanor.config import AthanorConfig


def test_config_defaults():
    """Config loads with sensible defaults when no env vars set."""
    with patch.dict(os.environ, {}, clear=True):
        config = AthanorConfig(
            _env_file=None,  # type: ignore[call-arg]
        )
    assert config.database_url == "postgresql://legion:legion@localhost:5432/legion"
    assert config.max_budget_usd == 10.0
    assert config.daily_budget_cap_usd == 100.0
    assert config.monthly_budget_cap_usd == 500.0
    assert config.max_global_concurrent_jobs == 10
    assert config.sandbox_memory == "8g"
    assert config.sandbox_cpus == "4"
    assert config.provider_mode == "anthropic_api"


def test_config_from_env():
    """Config reads from environment variables."""
    env = {
        "DATABASE_URL": "postgresql://user:pass@db:5432/mydb",
        "GITHUB_TOKEN": "ghp_test123",
        "MAX_BUDGET_USD": "25.0",
        "PROVIDER_MODE": "anthropic_api",
        "ANTHROPIC_API_KEY": "sk-ant-test",
    }
    with patch.dict(os.environ, env, clear=True):
        config = AthanorConfig(
            _env_file=None,  # type: ignore[call-arg]
        )
    assert config.database_url == "postgresql://user:pass@db:5432/mydb"
    assert config.github_token == "ghp_test123"
    assert config.max_budget_usd == 25.0
    assert config.anthropic_api_key == "sk-ant-test"


def test_config_cors_origins_parsing():
    """Comma-separated CORS origins are parsed into a list."""
    env = {"ALLOWED_CORS_ORIGINS": "http://localhost:3001, http://example.com"}
    with patch.dict(os.environ, env, clear=True):
        config = AthanorConfig(_env_file=None)  # type: ignore[call-arg]
    assert config.allowed_cors_origins_list == ["http://localhost:3001", "http://example.com"]


def test_config_cors_origins_default():
    """Empty CORS origins defaults to localhost:3001."""
    with patch.dict(os.environ, {}, clear=True):
        config = AthanorConfig(_env_file=None)  # type: ignore[call-arg]
    assert config.allowed_cors_origins_list == ["http://localhost:3001"]


def test_config_trigger_labels_parsing():
    """Comma-separated trigger labels are parsed into a list."""
    env = {"TRIGGER_LABELS": "Legion, auto-fix, bot"}
    with patch.dict(os.environ, env, clear=True):
        config = AthanorConfig(_env_file=None)  # type: ignore[call-arg]
    assert config.trigger_labels_list == ["Legion", "auto-fix", "bot"]


def test_config_claude_max_provider():
    """Claude Max provider mode reads OAuth token."""
    env = {
        "PROVIDER_MODE": "claude_max",
        "CLAUDE_MAX_OAUTH_TOKEN": "sk-ant-oat01-test",
    }
    with patch.dict(os.environ, env, clear=True):
        config = AthanorConfig(_env_file=None)  # type: ignore[call-arg]
    assert config.provider_mode == "claude_max"
    assert config.claude_max_oauth_token == "sk-ant-oat01-test"


def test_config_ollama_provider():
    """Ollama provider mode reads base URL and model."""
    env = {
        "PROVIDER_MODE": "ollama",
        "OLLAMA_BASE_URL": "http://gpu:11434",
        "OLLAMA_MODEL": "codellama",
    }
    with patch.dict(os.environ, env, clear=True):
        config = AthanorConfig(_env_file=None)  # type: ignore[call-arg]
    assert config.provider_mode == "ollama"
    assert config.ollama_base_url == "http://gpu:11434"
    assert config.ollama_model == "codellama"
