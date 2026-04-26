"""Smoke-test fixtures — real sandbox container required."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def smoke_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY not set — skipping smoke test")
    return key


@pytest.fixture(scope="session")
def smoke_oauth_token() -> str:
    return os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
