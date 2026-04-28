"""Tests for startup-time configuration resolution."""

import os
from unittest.mock import patch

import pytest

from athanor.api.app import _resolve_proxy_admin_token
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
