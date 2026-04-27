"""AthanorConfig new defaults for execution_mode / auth_mode."""

from athanor.config import AthanorConfig


def test_default_execution_mode_is_sdk() -> None:
    cfg = AthanorConfig()
    assert cfg.default_execution_mode == "sdk"


def test_default_auth_mode_is_oauth() -> None:
    cfg = AthanorConfig()
    assert cfg.default_auth_mode == "oauth"


def test_defaults_overridable_via_env(monkeypatch) -> None:
    monkeypatch.setenv("DEFAULT_EXECUTION_MODE", "cli")
    monkeypatch.setenv("DEFAULT_AUTH_MODE", "api_key")
    cfg = AthanorConfig()
    assert cfg.default_execution_mode == "cli"
    assert cfg.default_auth_mode == "api_key"
