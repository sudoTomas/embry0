# tests/unit/api/v1/test_stats_credentials.py
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from embry0.api.v1.stats import _credentials_summary


async def test_summary_counts_and_env_keys():
    request = MagicMock()
    request.state.principal = None  # a session: no repository allowlist
    request.app.state.credentials_repo = MagicMock(
        list_all=AsyncMock(
            return_value=[
                {"status": "ok"},
                {"status": "quarantined"},
                {"status": "disabled"},
                {"status": "ok"},
            ]
        )
    )
    request.app.state.credential_resolver = MagicMock(env_fallback_keys=MagicMock(return_value=["GITHUB_TOKEN"]))
    assert await _credentials_summary(request) == {
        "count": 4,
        "quarantined": 1,
        "disabled": 1,
        "env_fallback_keys": ["GITHUB_TOKEN"],
    }


async def test_expired_quarantine_is_not_counted():
    request = MagicMock()
    request.state.principal = None
    past = datetime.now(UTC) - timedelta(minutes=1)
    future = datetime.now(UTC) + timedelta(minutes=1)
    request.app.state.credentials_repo = MagicMock(
        list_all=AsyncMock(
            return_value=[
                {"status": "quarantined", "quarantined_until": past},
                {"status": "quarantined", "quarantined_until": future},
                {"status": "quarantined", "quarantined_until": None},
                {"status": "ok"},
            ]
        )
    )
    request.app.state.credential_resolver = None
    summary = await _credentials_summary(request)
    assert summary["quarantined"] == 2, "a past quarantined_until still counted as quarantined"
    assert summary["count"] == 4


async def test_summary_is_none_without_repo():
    request = MagicMock()
    del request.app.state.credentials_repo
    assert await _credentials_summary(request) is None


async def test_summary_swallows_errors():
    request = MagicMock()
    request.app.state.credentials_repo = MagicMock(list_all=AsyncMock(side_effect=RuntimeError("db")))
    assert await _credentials_summary(request) is None
