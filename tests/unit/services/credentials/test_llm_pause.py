"""Provider fallback (spec 2026-10-25): which providers count as paused, read without recording a use."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from embry0.services.credentials.resolver import CredentialResolver
from tests.unit.services.credentials.test_resolve_llm import LlmRepo, _row
from tests.unit.services.credentials.test_resolver import FakeSecrets, _cfg

SOON = datetime.now(UTC) + timedelta(hours=3)
LATER = datetime.now(UTC) + timedelta(days=2)


def _spent(label: str, secret: str, until: datetime | None = SOON, reason: str = "quota_exhausted") -> dict:
    return dict(_row(label, secret, status="quarantined", quarantined_until=until), status_reason=reason)


def _resolver(rows, *, env=None, broken=False):
    repo = LlmRepo({("global", "", "llm", "anthropic"): list(rows)})
    repo.raise_on_candidates = broken
    return repo, CredentialResolver(repo, FakeSecrets(), _cfg(), environ=env or {})


async def test_a_usable_account_is_not_paused():
    repo, r = _resolver([_spent("spent", "tok-a"), _row("ok", "tok-b")])
    assert await r.llm_pause("acme/app", "anthropic") is None
    assert repo.uses == [], "asking records no use"


async def test_every_account_spent_and_no_env_is_paused_until_the_first_comes_back():
    _, r = _resolver([_spent("late", "tok-a", LATER), _spent("soon", "tok-b", SOON, reason="rate_limited")])
    pause = await r.llm_pause("acme/app", "anthropic")
    assert pause is not None
    assert (pause.provider, pause.reason, pause.until) == ("anthropic", "rate_limited", SOON)


async def test_the_environment_copy_of_a_spent_account_is_the_same_account():
    """The case that failed every job: the registry row is quarantined, and the
    env token the resolver would fall through to is that same token."""
    _, r = _resolver([_spent("imported", "tok-same")], env={"CLAUDE_CODE_OAUTH_TOKEN": "tok-same"})
    pause = await r.llm_pause("acme/app", "anthropic")
    assert pause is not None and pause.reason == "quota_exhausted" and pause.until == SOON


async def test_a_different_environment_account_still_serves():
    _, r = _resolver([_spent("imported", "tok-a")], env={"CLAUDE_CODE_OAUTH_TOKEN": "tok-other"})
    assert await r.llm_pause("acme/app", "anthropic") is None


async def test_no_account_at_all_is_paused_as_missing_and_an_env_account_is_enough():
    _, r = _resolver([])
    pause = await r.llm_pause("acme/app", "anthropic")
    assert pause is not None and (pause.reason, pause.until) == ("missing", None)
    _, r = _resolver([], env={"CLAUDE_CODE_OAUTH_TOKEN": "tok"})
    assert await r.llm_pause("acme/app", "anthropic") is None


async def test_a_disabled_account_names_no_end_and_a_provider_not_enabled_is_paused():
    _, r = _resolver([dict(_row("off", "tok", status="disabled"), status_reason="operator")])
    pause = await r.llm_pause("acme/app", "anthropic")
    assert pause is not None and pause.until is None
    _, r = _resolver([])
    not_enabled = await r.llm_pause("acme/app", "openrouter")
    assert not_enabled is not None and not_enabled.reason == "not_enabled"


async def test_a_registry_error_never_moves_a_job():
    _, r = _resolver([], broken=True)
    assert await r.llm_pause("acme/app", "anthropic") is None


async def test_job_pinned_reads_the_run_with_pin():
    async def pins(job_id):
        return {"anthropic": "cred-chosen"} if job_id == "job-1" else {}

    r = CredentialResolver(LlmRepo({}), FakeSecrets(), _cfg(), environ={}, job_pins=pins)
    assert await r.job_pinned("job-1", "anthropic") is True
    assert await r.job_pinned("job-1", "xai") is False
    assert await r.job_pinned("job-2", "anthropic") is False
