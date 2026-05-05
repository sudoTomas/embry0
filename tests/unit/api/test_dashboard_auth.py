"""Tests for Phase-4 dashboard JWT issuance + verification."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from athanor.api.auth import (
    DashboardAuthError,
    issue_dashboard_jwt,
    verify_dashboard_jwt,
)


def test_issue_then_verify_round_trip():
    secret = "test-secret-32-characters-minimum-here"
    token, expires_at = issue_dashboard_jwt(api_key="some-key", secret=secret, ttl_seconds=60)
    payload = verify_dashboard_jwt(token, secret=secret)
    assert payload is not None
    assert payload["sub"] == "dashboard"
    assert isinstance(expires_at, datetime)
    assert expires_at > datetime.now(UTC)


def test_verify_returns_none_on_wrong_secret():
    secret = "test-secret-32-characters-minimum-here"
    other = "different-secret-also-32-chars-here"
    token, _ = issue_dashboard_jwt(api_key="some-key", secret=secret, ttl_seconds=60)
    assert verify_dashboard_jwt(token, secret=other) is None


def test_verify_returns_none_on_malformed_token():
    secret = "test-secret-32-characters-minimum-here"
    assert verify_dashboard_jwt("not-a-jwt", secret=secret) is None
    assert verify_dashboard_jwt("", secret=secret) is None


def test_expired_token_fails_verification():
    secret = "test-secret-32-characters-minimum-here"
    token, _ = issue_dashboard_jwt(api_key="x", secret=secret, ttl_seconds=1)
    time.sleep(2)
    assert verify_dashboard_jwt(token, secret=secret) is None


def test_issue_raises_on_short_secret():
    """Secrets shorter than 32 chars are rejected to avoid weak HMACs."""
    with pytest.raises(DashboardAuthError):
        issue_dashboard_jwt(api_key="x", secret="too-short", ttl_seconds=60)
