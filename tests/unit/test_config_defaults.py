"""Embry0Config new defaults for execution_mode / auth_mode."""

from embry0.config import Embry0Config


def test_default_execution_mode_is_sdk() -> None:
    cfg = Embry0Config()
    assert cfg.default_execution_mode == "sdk"


def test_default_auth_mode_is_oauth() -> None:
    cfg = Embry0Config()
    assert cfg.default_auth_mode == "oauth"


def test_defaults_overridable_via_env(monkeypatch) -> None:
    monkeypatch.setenv("DEFAULT_EXECUTION_MODE", "cli")
    monkeypatch.setenv("DEFAULT_AUTH_MODE", "api_key")
    cfg = Embry0Config()
    assert cfg.default_execution_mode == "cli"
    assert cfg.default_auth_mode == "api_key"
