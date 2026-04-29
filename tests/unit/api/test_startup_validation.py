"""Tests for startup-time configuration resolution."""

import os
from typing import Any
from unittest.mock import patch

import pytest

from athanor.api.app import _resolve_proxy_admin_token, _resolve_secrets_provider
from athanor.config import AthanorConfig


def test_proxy_admin_token_passed_through_when_set():
    """Token is passed through unchanged if already set."""
    with patch.dict(os.environ, {}, clear=True):
        cfg = AthanorConfig(
            _env_file=None,
            auth_dev_mode=False,
            proxy_admin_token="preset-token-1234567890ab",
        )
    _resolve_proxy_admin_token(cfg)
    assert cfg.proxy_admin_token == "preset-token-1234567890ab"


def test_proxy_admin_token_auto_generated_in_auth_dev_mode():
    """Token is auto-generated when auth_dev_mode=True and token is empty."""
    with patch.dict(os.environ, {}, clear=True):
        cfg = AthanorConfig(
            _env_file=None,
            auth_dev_mode=True,
            proxy_admin_token="",
        )
    _resolve_proxy_admin_token(cfg)
    # token_urlsafe(32) generates a ~43 character token
    assert len(cfg.proxy_admin_token) >= 40
    assert cfg.proxy_admin_token != ""


def test_proxy_admin_token_required_in_production():
    """RuntimeError is raised when auth_dev_mode=False and token is empty."""
    with patch.dict(os.environ, {}, clear=True):
        cfg = AthanorConfig(
            _env_file=None,
            auth_dev_mode=False,
            proxy_admin_token="",
        )
    with pytest.raises(RuntimeError, match="PROXY_ADMIN_TOKEN must be set in production"):
        _resolve_proxy_admin_token(cfg)


def test_proxy_admin_token_auto_generated_in_webhook_dev_mode():
    """smee.io workflow: webhook_dev_mode alone should still trigger auto-gen."""
    with patch.dict(os.environ, {}, clear=True):
        cfg = AthanorConfig(_env_file=None, auth_dev_mode=False, webhook_dev_mode=True, proxy_admin_token="")
    _resolve_proxy_admin_token(cfg)
    assert cfg.proxy_admin_token != ""


def test_proxy_admin_token_required_when_no_dev_mode():
    """Production posture: both flags off, missing token → refuse to start."""
    with patch.dict(os.environ, {}, clear=True):
        cfg = AthanorConfig(_env_file=None, auth_dev_mode=False, webhook_dev_mode=False, proxy_admin_token="")
    with pytest.raises(RuntimeError, match="must be set in production"):
        _resolve_proxy_admin_token(cfg)


# ---------------------------------------------------------------------------
# _resolve_secrets_provider tests
# ---------------------------------------------------------------------------


def _make_config(**overrides: Any) -> AthanorConfig:
    """Minimal AthanorConfig for secrets-provider startup tests."""
    with patch.dict(os.environ, {}, clear=True):
        return AthanorConfig(_env_file=None, **overrides)  # type: ignore[call-arg]


class TestResolveSecretsProvider:
    def test_empty_key_prod_raises(self) -> None:
        """Production boot with empty key must raise RuntimeError."""
        cfg = _make_config(environment_secret_key="", auth_dev_mode=False)
        with pytest.raises(RuntimeError, match="ENVIRONMENT_SECRET_KEY must be set"):
            _resolve_secrets_provider(cfg)

    def test_default_key_prod_raises(self) -> None:
        """Production boot with the hardcoded example default must raise RuntimeError."""
        cfg = _make_config(environment_secret_key="athanor-dev-secret-key", auth_dev_mode=False)
        with pytest.raises(RuntimeError, match="insecure example default"):
            _resolve_secrets_provider(cfg)

    def test_empty_key_dev_mode_generates_ephemeral(self) -> None:
        """Dev-mode with empty key generates a functional ephemeral provider."""
        import asyncio

        cfg = _make_config(environment_secret_key="", auth_dev_mode=True)
        # Must not raise — returns a working ephemeral provider.
        provider = _resolve_secrets_provider(cfg)
        assert provider is not None
        # Provider must be functional end-to-end.
        ct = asyncio.run(provider.encrypt("hello"))
        pt = asyncio.run(provider.decrypt(ct))
        assert pt == "hello"
        # Two calls produce different ciphertexts (Fernet random IV).
        ct2 = asyncio.run(provider.encrypt("hello"))
        assert ct != ct2

    def test_default_key_dev_mode_generates_ephemeral(self) -> None:
        """Dev-mode with the insecure default key also generates an ephemeral provider."""
        cfg = _make_config(environment_secret_key="athanor-dev-secret-key", auth_dev_mode=True)
        provider = _resolve_secrets_provider(cfg)
        assert provider is not None

    def test_valid_key_returns_provider(self) -> None:
        """A strong custom key returns a working provider in production mode."""
        import asyncio

        cfg = _make_config(environment_secret_key="a-valid-strong-key-here", auth_dev_mode=False)
        provider = _resolve_secrets_provider(cfg)
        assert provider is not None
        ct = asyncio.run(provider.encrypt("secret-value"))
        pt = asyncio.run(provider.decrypt(ct))
        assert pt == "secret-value"
