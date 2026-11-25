"""The token loop's reaction to the xai-proxy's per-account upstream counts.

A stale-token 403 means a refresh fixes the account, so it is force-refreshed
and pushed, never quarantined. 429 and 401 keep their quarantines.
"""

from __future__ import annotations

from typing import Any

import pytest

from embry0.api.app import _apply_xai_proxy_health
from embry0.execution.xai_credential import XaiCredentialError
from embry0.safety.error_codes import ErrorCode


class _Registry:
    def __init__(self, fail: tuple[str, ...] = ()) -> None:
        self.calls: list[tuple[str, bool]] = []
        self.fail = set(fail)

    async def refresh_credential(self, credential_id: str, *, force: bool = False) -> Any:
        from embry0.execution.xai_credential import FreshToken

        self.calls.append((credential_id, force))
        if credential_id in self.fail:
            raise XaiCredentialError(f"refresh of {credential_id} failed")
        return FreshToken(f"token-{credential_id}", "2030-01-01T00:00:00Z", 7)


class _Proxy:
    def __init__(self) -> None:
        self.pushes: list[tuple[str, str, str]] = []
        self.seqs: list[int | None] = []

    async def push_xai_token(self, credential_id: str, token: str, expires_at: str, *, seq: int | None = None) -> None:
        self.pushes.append((credential_id, token, expires_at))
        self.seqs.append(seq)


class _Health:
    def __init__(self) -> None:
        self.reports: list[tuple[str, str]] = []

    async def report(self, credential_id: str, code: str) -> None:
        self.reports.append((credential_id, code))


@pytest.mark.asyncio
async def test_a_stale_token_hit_forces_a_refresh_and_a_push():
    registry, proxy, health = _Registry(), _Proxy(), _Health()
    await _apply_xai_proxy_health(
        {"credentials": {"cred-a": {"401": 0, "429": 0, "stale": 2}}}, registry, proxy, health
    )
    assert registry.calls == [("cred-a", True)], "a stale-token hit must force a refresh of that account"
    assert proxy.pushes == [("cred-a", "token-cred-a", "2030-01-01T00:00:00Z")], "the new token must reach the proxy"
    assert health.reports == [], "a stale token is refreshed, never quarantined"


@pytest.mark.asyncio
async def test_rate_limits_and_refused_tokens_still_quarantine():
    registry, proxy, health = _Registry(), _Proxy(), _Health()
    await _apply_xai_proxy_health({"credentials": {"cred-a": {"401": 1, "429": 1}}}, registry, proxy, health)
    assert health.reports == [
        ("cred-a", ErrorCode.PROVIDER_RATE_LIMITED),
        ("cred-a", ErrorCode.AUTH_REJECTED),
    ], "401 last, so a window that saw both keeps auth_rejected"
    assert registry.calls == []


@pytest.mark.asyncio
async def test_one_failed_forced_refresh_does_not_stop_the_others():
    registry, proxy, health = _Registry(fail=("cred-a",)), _Proxy(), _Health()
    report = {"credentials": {"cred-a": {"stale": 1}, "cred-b": {"stale": 1}}}
    await _apply_xai_proxy_health(report, registry, proxy, health)
    assert registry.calls == [("cred-a", True), ("cred-b", True)]
    assert [push[0] for push in proxy.pushes] == ["cred-b"], "cred-b must still be refreshed and pushed"


@pytest.mark.asyncio
@pytest.mark.parametrize("report", [{}, {"credentials": None}, {"credentials": {"cred-a": "garbage"}}])
async def test_a_malformed_report_does_nothing(report):
    registry, proxy, health = _Registry(), _Proxy(), _Health()
    await _apply_xai_proxy_health(report, registry, proxy, health)
    assert (registry.calls, proxy.pushes, health.reports) == ([], [], [])


async def test_a_forced_refresh_is_pushed_with_the_number_it_was_fetched_with() -> None:
    registry, proxy = _Registry(), _Proxy()
    await _apply_xai_proxy_health({"credentials": {"cred-a": {"stale": 1}}}, registry, proxy, _Health())
    assert proxy.seqs == [7], f"the push was not numbered when its token was fetched: {proxy.seqs}"
