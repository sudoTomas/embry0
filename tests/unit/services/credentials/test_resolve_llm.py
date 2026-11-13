"""resolve_llm precedence: repo, owner, pool, env (spec §3.4)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
import structlog

from embry0.safety.error_codes import ErrorCode
from embry0.services.credentials.resolver import (
    CredentialMissingError,
    CredentialResolver,
    CredentialUnavailableError,
)
from tests.unit.services.credentials.test_resolver import FakeRepo, FakeSecrets, _cfg, _resolver


def _row(
    label,
    secret="tok",
    *,
    status="ok",
    pinned=False,
    quarantined_until=None,
    kind="llm_oauth",
    provider="anthropic",
    priority=0,
    last_used_at=None,
):
    return {
        "id": f"cred-{label}",
        "kind": kind,
        "provider": provider,
        "host": "api.anthropic.com" if provider == "anthropic" else "api.x.ai",
        "label": label,
        "secret_ciphertext": "enc:" + secret,
        "git_author_name": None,
        "git_author_email": None,
        "status": status,
        "quarantined_until": quarantined_until,
        "pinned": pinned,
        "priority": priority,
        "last_used_at": last_used_at,
    }


class LlmRepo(FakeRepo):
    """candidates() returns the SQL order: priority ASC, last_used_at ASC NULLS FIRST."""

    def __init__(self, table=None):
        super().__init__(table)
        self.by_id = {}
        self.job_uses = {}
        for rows in (table or {}).values():
            for row in rows:
                self.by_id[row["id"]] = row

    async def candidates(self, *, scope_kind, scope_value, role, provider):
        rows = await super().candidates(scope_kind=scope_kind, scope_value=scope_value, role=role, provider=provider)

        def _key(row):
            used = row.get("last_used_at")
            return (row.get("priority", 0), used is not None, used or datetime.min.replace(tzinfo=UTC))

        return sorted(rows, key=_key)

    async def get(self, credential_id):
        row = self.by_id.get(credential_id)
        return dict(row) if row else None

    async def uses_for_job(self, job_id):
        return list(self.job_uses.get(job_id, []))


def _llm(table=None, *, env=None, credentials_file=None, forge="github"):
    async def repo_forge(_repo):
        return forge

    return CredentialResolver(
        LlmRepo(table),
        FakeSecrets(),
        _cfg(),
        environ=env or {},
        credentials_file=credentials_file or "/no/such/credentials.json",
        repo_forge=repo_forge,
    )


async def test_pool_orders_by_priority_then_lru_and_skips_quarantined():
    future = datetime.now(UTC) + timedelta(hours=1)
    earlier = datetime.now(UTC) - timedelta(hours=2)
    later = datetime.now(UTC) - timedelta(minutes=5)
    repo = LlmRepo(
        {
            ("global", "", "llm", "anthropic"): [
                _row("low", "tok-low", priority=5, last_used_at=None),
                _row("second", "tok-second", priority=0, last_used_at=later),
                _row("hot", "tok-hot", status="quarantined", quarantined_until=future, priority=0),
                _row("first", "tok-first", priority=0, last_used_at=earlier),
            ]
        }
    )
    got = await CredentialResolver(repo, FakeSecrets(), _cfg(), environ={}).resolve_llm(
        "acme/widgets", "anthropic", job_id="job-1"
    )
    assert got.label == "first" and got.secret == "tok-first" and got.source == "registry"
    assert repo.uses == [
        {
            "credential_id": "cred-first",
            "credential_label": "first",
            "job_id": "job-1",
            "role": "llm",
            "provider": "anthropic",
        }
    ]


async def test_repo_pinned_unavailable_raises_even_with_pool():
    future = datetime.now(UTC) + timedelta(hours=1)
    r = _llm(
        {
            ("repo", "acme/widgets", "llm", "anthropic"): [
                _row("pinned", "s3cr3t-pinned", status="quarantined", quarantined_until=future, pinned=True)
            ],
            ("global", "", "llm", "anthropic"): [_row("pool", "tok-pool")],
        }
    )
    with pytest.raises(CredentialUnavailableError) as exc:
        await r.resolve_llm("acme/widgets", "anthropic")
    assert exc.value.error_code == ErrorCode.CREDENTIAL_UNAVAILABLE
    assert "pinned" in str(exc.value) and "s3cr3t-pinned" not in str(exc.value)


async def test_repo_unpinned_unavailable_falls_to_pool():
    r = _llm(
        {
            ("repo", "acme/widgets", "llm", "xai"): [
                _row("busy", "tok-busy", provider="xai", kind="llm_api_key", status="disabled", pinned=False)
            ],
            ("global", "", "llm", "xai"): [_row("pool", "tok-pool", provider="xai", kind="llm_api_key")],
        }
    )
    got = await r.resolve_llm("acme/widgets", "xai")
    assert got.label == "pool" and got.secret == "tok-pool"


async def test_an_owner_binding_serves_the_owners_repositories_on_its_own_forge():
    """github:acme and gitlab:acme are two owners: a repository reads the one on the forge it is on, and a
    repository whose forge is unknown reads neither."""
    table = {
        ("owner", "gitlab:acme", "llm", "anthropic"): [_row("from-gitlab", "tok-gl")],
        ("owner", "github:acme", "llm", "anthropic"): [_row("from-github", "tok-gh")],
        ("global", "", "llm", "anthropic"): [_row("pool", "tok-pool")],
    }
    got = {}
    for forge in ("github", "gitlab", None):
        r = _llm(table, forge=forge)
        got[forge] = (await r.resolve_llm("Acme/Widgets", "anthropic")).label
    assert got == {"github": "from-github", "gitlab": "from-gitlab", None: "pool"}, (
        f"each forge's repository reads its own owner's account, an unknown one neither: {got}"
    )
    trace = (await _llm(table, forge=None).resolve_llm("Acme/Widgets", "anthropic")).trace
    assert "owner:acme -> skipped (the repository's forge is unknown)" in trace, trace


async def test_env_fallback_anthropic_token_then_file(tmp_path):
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "file-token"}}))
    with structlog.testing.capture_logs() as logs:
        env_hit = await _llm(env={"CLAUDE_CODE_OAUTH_TOKEN": "env-token"}, credentials_file=str(creds)).resolve_llm(
            "acme/widgets", "anthropic"
        )
        file_resolver = _llm(credentials_file=str(creds))
        file_hit = await file_resolver.resolve_llm("acme/widgets", "anthropic")
        await file_resolver.resolve_llm("acme/widgets", "anthropic")
    assert (env_hit.secret, env_hit.kind, env_hit.label, env_hit.source) == (
        "env-token",
        "llm_oauth",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "env",
    )
    assert (file_hit.secret, file_hit.label, file_hit.host) == (
        "file-token",
        "claude-credentials-file",
        "api.anthropic.com",
    )
    assert len([e for e in logs if e["event"] == "credential_env_fallback"]) == 2


async def test_env_fallback_xai_api_key():
    got = await _llm(env={"XAI_API_KEY": "xai-key"}).resolve_llm("acme/widgets", "xai")
    assert (got.secret, got.kind, got.label, got.host) == ("xai-key", "llm_api_key", "XAI_API_KEY", "api.x.ai")


async def test_unknown_provider_rejected():
    with pytest.raises(ValueError, match="openai"):
        await _resolver().resolve_llm("acme/widgets", "openai")


async def test_missing_llm_raises():
    with pytest.raises(CredentialMissingError) as exc:
        await _llm().resolve_llm("acme/widgets", "anthropic")
    assert exc.value.error_code == ErrorCode.CREDENTIAL_MISSING
    assert "sk-" not in str(exc.value)


async def test_resolve_llm_for_job_reuses_parent_choice():
    earlier = datetime.now(UTC) - timedelta(hours=1)
    cred_a = _row("a", "tok-a", provider="xai", kind="llm_api_key", last_used_at=None)
    cred_b = _row("b", "tok-b", provider="xai", kind="llm_api_key", last_used_at=earlier)
    repo = LlmRepo({("global", "", "llm", "xai"): [cred_a, cred_b]})
    repo.job_uses["parent-1"] = [{"role": "llm", "provider": "xai", "credential_id": "cred-b", "credential_label": "b"}]
    r = CredentialResolver(repo, FakeSecrets(), _cfg(), environ={})
    got = await r.resolve_llm_for_job("acme/widgets", ["xai"], job_id="child-1", parent_job_id="parent-1")
    assert got["xai"].id == "cred-b"
    assert repo.uses[-1]["job_id"] == "child-1" and repo.uses[-1]["role"] == "llm"


async def test_expired_quarantine_is_usable():
    past = datetime.now(UTC) - timedelta(minutes=1)
    r = _llm(
        {("global", "", "llm", "anthropic"): [_row("old", "tok-old", status="quarantined", quarantined_until=past)]}
    )
    assert (await r.resolve_llm("acme/widgets", "anthropic")).label == "old"
