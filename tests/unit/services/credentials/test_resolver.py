"""CredentialResolver precedence, availability and env fallback (spec §3.1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import structlog

from embry0.safety.error_codes import ErrorCode
from embry0.services.credentials.resolver import (
    CredentialMissingError,
    CredentialResolver,
    CredentialUnavailableError,
    forge_token,
)


class FakeSecrets:
    async def decrypt(self, ciphertext: str) -> str:
        if ciphertext == "enc:BAD":
            from cryptography.fernet import InvalidToken

            raise InvalidToken()
        return ciphertext.removeprefix("enc:")

    async def encrypt(self, plaintext: str) -> str:
        return "enc:" + plaintext


class FakeRepo:
    """candidates() answers from a {(scope_kind, scope_value, role, provider): [rows]} table."""

    def __init__(self, table=None, *, raise_on_candidates=False):
        self.table = table or {}
        self.uses = []
        self.status_calls = []
        self.raise_on_candidates = raise_on_candidates
        self.raise_on_writes = False
        self.lookups = []
        self.known = {}  # forge -> the owners registered there

    async def known_owners(self):
        return {forge: set(owners) for forge, owners in self.known.items()}

    async def candidates(self, *, scope_kind, scope_value, role, provider):
        self.lookups.append((scope_kind, scope_value))
        if self.raise_on_candidates:
            raise RuntimeError("db down")
        return list(self.table.get((scope_kind, scope_value, role, provider), []))

    async def record_use(self, **kw):
        if self.raise_on_writes:
            raise RuntimeError("insert failed")
        self.uses.append(kw)

    async def set_status(self, cid, status, *, reason=None, quarantined_until=None):
        if self.raise_on_writes:
            raise RuntimeError("update failed")
        self.status_calls.append((cid, status, reason))
        return {"id": cid, "status": status}

    async def any_for_provider(self, provider):
        return any(k[3] == provider and rows for k, rows in self.table.items())


def _row(label, secret="tok", *, status="ok", pinned=False, quarantined_until=None, author=None):
    return {
        "id": f"cred-{label}",
        "kind": "forge_token",
        "provider": "github",
        "host": "github.com",
        "label": label,
        "secret_ciphertext": "enc:" + secret,
        "git_author_name": author,
        "git_author_email": None,
        "status": status,
        "quarantined_until": quarantined_until,
        "pinned": pinned,
        "priority": 0,
    }


def _cfg(**kw):
    return SimpleNamespace(
        github_token=kw.get("github_token", ""),
        gitlab_token=kw.get("gitlab_token", ""),
        gitlab_url=kw.get("gitlab_url", "https://gitlab.example.com"),
    )


def _resolver(table=None, *, env=None, config=None, repo=None):
    return CredentialResolver(repo or FakeRepo(table), FakeSecrets(), config or _cfg(), environ=env or {})


async def test_repo_binding_wins_and_records_use() -> None:
    repo = FakeRepo(
        {
            ("repo", "acme/widgets", "forge", "github"): [_row("alice", "tok-repo", author="Alice")],
            ("owner", "github:acme", "forge", "github"): [_row("bob", "tok-owner")],
        }
    )
    r = _resolver(repo=repo)
    got = await r.resolve_forge("acme/widgets", "github", job_id="job-1")
    assert got.secret == "tok-repo" and got.label == "alice" and got.source == "registry"
    assert got.git_author_name == "Alice"
    assert got.trace[0].startswith("repo:acme/widgets")
    assert repo.uses == [
        {
            "credential_id": "cred-alice",
            "credential_label": "alice",
            "job_id": "job-1",
            "role": "forge",
            "provider": "github",
        }
    ]


async def test_no_job_id_does_not_record_use() -> None:
    repo = FakeRepo({("global", "", "forge", "github"): [_row("alice")]})
    await _resolver(repo=repo).resolve_forge("acme/widgets", "github")
    assert repo.uses == []


async def test_owner_scope_is_case_insensitive() -> None:
    """Review focus 1."""
    r = _resolver({("owner", "github:acme", "forge", "github"): [_row("alice")]})
    assert (await r.resolve_forge("Acme/Widgets", "github")).label == "alice"


async def test_gitlab_owner_is_top_level_group() -> None:
    """Review focus 2."""
    row = dict(_row("grp"), provider="gitlab", host="gitlab.example.com")
    r = _resolver({("owner", "gitlab:group", "forge", "gitlab"): [row]})
    got = await r.resolve_forge("group/sub/project", "gitlab")
    assert got.label == "grp" and got.host == "gitlab.example.com"


async def test_host_then_global_precedence() -> None:
    r = _resolver(
        {
            ("host", "github.com", "forge", "github"): [_row("hosty")],
            ("global", "", "forge", "github"): [_row("glob")],
        }
    )
    assert (await r.resolve_forge("acme/widgets", "github")).label == "hosty"
    r2 = _resolver({("global", "", "forge", "github"): [_row("glob")]})
    assert (await r2.resolve_forge("acme/widgets", "github")).label == "glob"


async def test_disabled_and_active_quarantine_skipped_expired_quarantine_ok() -> None:
    future = datetime.now(UTC) + timedelta(hours=1)
    past = datetime.now(UTC) - timedelta(hours=1)
    r = _resolver(
        {
            ("global", "", "forge", "github"): [
                _row("dis", status="disabled"),
                _row("quar", status="quarantined", quarantined_until=future),
                _row("manual", status="quarantined", quarantined_until=None),
                _row("expired", status="quarantined", quarantined_until=past),
            ]
        }
    )
    got = await r.resolve_forge("acme/widgets", "github")
    assert got.label == "expired"
    assert any("skipped" in step for step in got.trace)


async def test_pinned_unavailable_raises() -> None:
    r = _resolver(
        {("repo", "acme/widgets", "forge", "github"): [_row("p", "s3cr3t-pinned", status="disabled", pinned=True)]}
    )
    with pytest.raises(CredentialUnavailableError) as exc:
        await r.resolve_forge("acme/widgets", "github")
    assert exc.value.error_code == ErrorCode.CREDENTIAL_UNAVAILABLE
    assert "'p'" in str(exc.value) and "s3cr3t-pinned" not in str(exc.value)


async def test_decrypt_failure_disables_and_skips() -> None:
    repo = FakeRepo({("global", "", "forge", "github"): [_row("bad", "BAD"), _row("good", "ok")]})
    got = await _resolver(repo=repo).resolve_forge("acme/widgets", "github")
    assert got.label == "good"
    assert repo.status_calls == [("cred-bad", "disabled", "decryption_failed")]


async def test_env_fallback_owner_then_default_and_once_per_key_warning() -> None:
    env = {"GITHUB_TOKEN__ACME": "tok-acme"}
    r = _resolver(env=env, config=_cfg(github_token="tok-default"))
    with structlog.testing.capture_logs() as logs:
        a = await r.resolve_forge("acme/widgets", "github")
        b = await r.resolve_forge("other/thing", "github")
        await r.resolve_forge("other/again", "github")
    assert (a.secret, a.source, a.label) == ("tok-acme", "env", "GITHUB_TOKEN__ACME")
    assert (b.secret, b.label) == ("tok-default", "GITHUB_TOKEN")
    assert len([e for e in logs if e["event"] == "credential_env_fallback"]) == 2
    assert r.env_fallback_keys() == ["GITHUB_TOKEN", "GITHUB_TOKEN__ACME"]


async def test_missing_everywhere_raises_with_code() -> None:
    with pytest.raises(CredentialMissingError) as exc:
        await _resolver().resolve_forge("acme/widgets", "github")
    assert exc.value.error_code == ErrorCode.CREDENTIAL_MISSING


async def test_db_error_degrades_to_env() -> None:
    """Review focus 3."""
    repo = FakeRepo(raise_on_candidates=True)
    r = _resolver(repo=repo, config=_cfg(github_token="tok-env"))
    with structlog.testing.capture_logs() as logs:
        got = await r.resolve_forge("acme/widgets", "github")
    assert got.secret == "tok-env" and got.source == "env"
    assert any(e["event"] == "credential_registry_error" for e in logs)


async def test_has_forge_credential_registry_or_env() -> None:
    assert await _resolver({("global", "", "forge", "gitlab"): [_row("x")]}).has_forge_credential("gitlab")
    assert not await _resolver().has_forge_credential("gitlab")
    assert await _resolver(env={"GITLAB_TOKEN__GROUP": "t"}).has_forge_credential("gitlab")
    assert await _resolver(config=_cfg(github_token="t")).has_forge_credential("github")


async def test_forge_lookup_is_cached_for_a_short_ttl() -> None:
    """#439: token_for must not run the four scope SELECTs on every REST page."""
    repo = FakeRepo({("global", "", "forge", "github"): [_row("alice", "tok-alice")]})
    now = {"t": 100.0}
    r = CredentialResolver(repo, FakeSecrets(), _cfg(), environ={}, cache_ttl_seconds=5, clock=lambda: now["t"])
    await r.resolve_forge("acme/widgets", "github", job_id="job-1")
    first = len(repo.lookups)
    await r.resolve_forge("acme/widgets", "github", job_id="job-2")
    assert len(repo.lookups) == first, "second resolve hit the registry again inside the TTL"
    assert [u["job_id"] for u in repo.uses] == ["job-1", "job-2"]
    now["t"] = 106.0
    await r.resolve_forge("acme/widgets", "github")
    assert len(repo.lookups) > first


async def test_invalidate_drops_the_forge_cache() -> None:
    repo = FakeRepo({("global", "", "forge", "github"): [_row("alice", "tok-alice")]})
    r = CredentialResolver(repo, FakeSecrets(), _cfg(), environ={}, cache_ttl_seconds=60, clock=lambda: 0.0)
    await r.resolve_forge("acme/widgets", "github")
    first = len(repo.lookups)
    r.invalidate()
    await r.resolve_forge("acme/widgets", "github")
    assert len(repo.lookups) > first


async def test_forge_token_bridge() -> None:
    assert await forge_token(None, "acme/widgets", "github", "tok-env") == "tok-env"
    r = _resolver({("global", "", "forge", "github"): [_row("g", "tok-reg")]})
    assert await forge_token(r, "acme/widgets", "github", "unused") == "tok-reg"
    assert await forge_token(_resolver(), "acme/widgets", "github", "") == ""


async def test_pinned_decrypt_failure_raises_unavailable() -> None:
    """Ruling B: a pinned credential never falls back to another account."""
    repo = FakeRepo(
        {
            ("repo", "acme/widgets", "forge", "github"): [_row("p", "BAD", pinned=True)],
            ("global", "", "forge", "github"): [_row("other")],
        }
    )
    with pytest.raises(CredentialUnavailableError) as exc:
        await _resolver(repo=repo).resolve_forge("acme/widgets", "github")
    assert "decryption_failed" in str(exc.value)
    assert repo.status_calls == [("cred-p", "disabled", "decryption_failed")]


async def test_failed_record_use_still_returns_registry_credential() -> None:
    """Ruling A: a failed usage write never changes which credential the job gets."""
    repo = FakeRepo({("repo", "acme/widgets", "forge", "github"): [_row("alice", "tok-repo")]})
    repo.raise_on_writes = True
    r = _resolver(repo=repo, config=_cfg(github_token="tok-env"))
    with structlog.testing.capture_logs() as logs:
        got = await r.resolve_forge("acme/widgets", "github", job_id="job-1")
    assert (got.secret, got.source, got.label) == ("tok-repo", "registry", "alice")
    assert any(e["event"] == "credential_registry_error" and e.get("op") == "record_use" for e in logs)


async def test_failed_set_status_still_tries_later_candidates() -> None:
    repo = FakeRepo({("global", "", "forge", "github"): [_row("bad", "BAD"), _row("good", "tok-good")]})
    repo.raise_on_writes = True
    got = await _resolver(repo=repo, config=_cfg(github_token="tok-env")).resolve_forge("acme/widgets", "github")
    assert (got.secret, got.source) == ("tok-good", "registry")


async def test_gitlab_env_fallback_top_level_group_key() -> None:
    env = {"GITLAB_TOKEN__GROUP_IO": "tok-gl"}
    r = _resolver(env=env, config=_cfg(gitlab_token="tok-default"))
    a = await r.resolve_forge("group.io/sub/proj", "gitlab")
    assert (a.secret, a.label, a.source) == ("tok-gl", "GITLAB_TOKEN__GROUP_IO", "env")
    b = await r.resolve_forge("other/x", "gitlab")
    assert (b.secret, b.label) == ("tok-default", "GITLAB_TOKEN")


async def test_slashless_repo_skips_owner_step() -> None:
    repo = FakeRepo()
    await _resolver(repo=repo, config=_cfg(github_token="t")).resolve_forge("widgets", "github")
    assert repo.lookups == [("repo", "widgets"), ("host", "github.com"), ("global", "")]


async def test_repo_scope_is_case_insensitive() -> None:
    r = _resolver({("repo", "acme/widgets", "forge", "github"): [_row("alice")]})
    assert (await r.resolve_forge("Acme/Widgets", "github")).label == "alice"


async def test_unknown_forge_rejected() -> None:
    r = _resolver(config=_cfg(github_token="t"))
    with pytest.raises(ValueError, match="unknown forge 'bitbucket'"):
        await r.resolve_forge("acme/widgets", "bitbucket")
    with pytest.raises(ValueError, match="unknown forge"):
        r.forge_host("bitbucket")
    with pytest.raises(ValueError, match="unknown forge"):
        await r.has_forge_credential("bitbucket")


async def test_owner_scope_is_the_owner_lowercased_and_otherwise_exact() -> None:
    """My-Org's repositories look up github:my-org, never github:my_org, another owner's scope."""
    r = _resolver(
        {
            ("owner", "github:my-org", "forge", "github"): [_row("org")],
            ("owner", "github:my_org", "forge", "github"): [_row("other")],
        }
    )
    got = {repo: (await r.resolve_forge(repo, "github")).label for repo in ("My-Org/Widgets", "my_org/widgets")}
    assert got == {"My-Org/Widgets": "org", "my_org/widgets": "other"}, f"each owner reads its own scope: {got}"


async def test_gitlab_owner_scope_keeps_dots() -> None:
    row = dict(_row("grp"), provider="gitlab", host="gitlab.example.com")
    repo = FakeRepo({("owner", "gitlab:acme.io", "forge", "gitlab"): [row]})
    r = CredentialResolver(repo, FakeSecrets(), _cfg(gitlab_token="tok-env"), environ={})
    assert (await r.resolve_forge("acme.io/proj", "gitlab")).label == "grp", f"looked up {repo.lookups}"


async def test_a_resolver_without_a_secrets_provider_names_the_cause_and_falls_back_to_env() -> None:
    """_init_app_state builds the resolver from app.state.secrets_provider, which a caller that
    skips the lifespan may not have set. A stored row then counts as a registry error, as any
    other: the env still serves the job, and the log says what was missing."""
    table = {("global", "", "forge", "github"): [_row("alice")]}
    r = CredentialResolver(FakeRepo(table), None, _cfg(github_token="tok-env"), environ={})
    with structlog.testing.capture_logs() as logs:
        got = await r.resolve_forge("acme/widgets", "github")
    assert (got.secret, got.source) == ("tok-env", "env")
    assert "registry error: RuntimeError" in got.trace, got.trace
    errors = [e["error"] for e in logs if e["event"] == "credential_registry_error"]
    assert errors and "no secrets provider" in errors[0], f"the log does not name the cause: {errors}"
