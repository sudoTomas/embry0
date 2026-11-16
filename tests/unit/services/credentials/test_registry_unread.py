"""A registry that cannot be read never hands a job to another account where one may be pinned.

The resolver walks the repository's binding, its owner's, then the global
pool, and ends on the environment. A pin may be stored at the repository's
and the owner's steps of a model account's walk (``pin_allowed``), so a
registry error before both are read fails the resolution: the environment
would otherwise serve a repository whose pinned account went unread. After
them, and on a forge account's walk, whose bindings are never pinned, the
environment serves on as before. The key a person chose for a job is read
from the job, for its derived sandboxes too.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import structlog

from embry0.safety.error_codes import ErrorCode
from embry0.services.credentials import resolver as resolver_module
from embry0.services.credentials.resolver import CredentialResolver, CredentialUnavailableError
from tests.unit.services.credentials.test_resolve_llm import LlmRepo, _row
from tests.unit.services.credentials.test_resolver import FakeSecrets, _cfg
from tests.unit.services.credentials.test_resolver import _row as _forge_row

ENV = {"XAI_API_KEY": "env-xai"}
PINNED_AT_REPO = _row("repo-key", "tok-repo", provider="xai", kind="llm_api_key", pinned=True)
PINNED_AT_OWNER = _row("owner-key", "tok-owner", provider="xai", kind="llm_api_key", pinned=True)
POOL = _row("pool", "tok-pool", provider="xai", kind="llm_api_key")


class FailingAt(LlmRepo):
    """candidates() raises at the named scopes, as a registry that cannot be read does."""

    def __init__(self, table=None, *, fail_at=()):
        super().__init__(table)
        self.fail_at = set(fail_at)

    async def candidates(self, *, scope_kind, scope_value, role, provider):
        if scope_kind in self.fail_at:
            self.lookups.append((scope_kind, scope_value))
            raise RuntimeError("db down")
        return await super().candidates(scope_kind=scope_kind, scope_value=scope_value, role=role, provider=provider)


def _llm(repo, *, forge_fails=False):
    async def repo_forge(_repo):
        if forge_fails:
            raise RuntimeError("db down")
        return "github"

    return CredentialResolver(repo, FakeSecrets(), _cfg(), environ=dict(ENV), repo_forge=repo_forge)


@pytest.mark.parametrize(
    ("fails", "table"),
    [
        ("repo", {("repo", "acme/app", "llm", "xai"): [PINNED_AT_REPO]}),
        ("owner", {("owner", "github:acme", "llm", "xai"): [PINNED_AT_OWNER]}),
        ("forge", {("owner", "github:acme", "llm", "xai"): [PINNED_AT_OWNER]}),
    ],
)
async def test_a_registry_error_before_a_pin_could_be_read_fails_instead_of_serving_the_environment(fails, table):
    repo = FailingAt(table, fail_at={fails} - {"forge"})
    r = _llm(repo, forge_fails=fails == "forge")
    with structlog.testing.capture_logs() as logs, pytest.raises(CredentialUnavailableError) as raised:
        await r.resolve_llm("acme/app", "xai", job_id="job-1")
    err = raised.value
    assert err.error_code == ErrorCode.CREDENTIAL_UNAVAILABLE
    assert str(err) == (
        "the xai credential for 'acme/app' could not be resolved: the credential registry could not be read"
        "; GET /credentials/resolve?repo=acme/app&role=llm&provider=xai shows why"
    ), str(err)
    assert err.detail == str(err).removesuffix(err.pointer), "the detail is the message without where to read why"
    assert err.missing is None, "an unread registry is not answered as nothing resolving: it says nothing was read"
    assert err.trace[-1] == "registry error: RuntimeError", err.trace
    assert not any(step.startswith("env:") for step in err.trace), f"the environment was read: {err.trace}"
    assert repo.uses == [], "no account was recorded for the job"
    assert r.env_fallback_keys() == [], "the environment served nothing"
    assert any(e["event"] == "credential_registry_unread" for e in logs), logs


async def test_a_registry_error_at_the_global_pool_falls_back_to_the_environment():
    """The repository's step and its owner's were read and pin nothing, and the pool cannot pin: env serves."""
    repo = FailingAt(fail_at={"global"})
    got = await _llm(repo).resolve_llm("acme/app", "xai")
    assert (got.secret, got.source) == ("env-xai", "env")
    assert got.trace == (
        "repo:acme/app -> none",
        "owner:github:acme -> none",
        "registry error: RuntimeError",
        "env:XAI_API_KEY",
    ), got.trace


async def test_a_resolution_for_no_repository_falls_back_to_the_environment_on_a_registry_error():
    """A provider's probe resolves for no repository: only the pool is read, and it pins nothing."""
    repo = FailingAt({("global", "", "llm", "xai"): [POOL]}, fail_at={"global"})
    got = await _llm(repo).resolve_llm("", "xai")
    assert (got.secret, got.source) == ("env-xai", "env")


async def test_a_registry_that_answers_still_serves_the_pin():
    """The rule changes nothing when the registry is read: the pinned account serves."""
    table = {("repo", "acme/app", "llm", "xai"): [PINNED_AT_REPO], ("global", "", "llm", "xai"): [POOL]}
    got = await _llm(FailingAt(table)).resolve_llm("acme/app", "xai")
    assert (got.secret, got.source) == ("tok-repo", "registry")


async def test_a_forge_resolution_falls_back_to_the_environment_at_every_step():
    """A forge binding is never pinned, so a registry error at any step leaves the environment to serve."""
    for step in ("repo", "owner", "host", "global"):
        repo = FailingAt(fail_at={step})
        r = CredentialResolver(repo, FakeSecrets(), _cfg(github_token="tok-env"), environ={})
        got = await r.resolve_forge("acme/app", "github")
        assert (got.secret, got.source) == ("tok-env", "env"), step


async def test_the_forge_walk_reads_the_same_pin_rule(monkeypatch):
    """Were a forge binding pinnable at the repository's step, an error there would fail as a model account's does,
    and one past it would not: the forge walk asks the store's rule, not a rule of its own."""
    monkeypatch.setattr(resolver_module, "pin_allowed", lambda role, scope_kind: scope_kind == "repo")
    table = {("repo", "acme/app", "forge", "github"): [_forge_row("bot", pinned=True)]}
    r = CredentialResolver(FailingAt(table, fail_at={"repo"}), FakeSecrets(), _cfg(github_token="tok-env"), environ={})
    with pytest.raises(CredentialUnavailableError, match="the credential registry could not be read"):
        await r.resolve_forge("acme/app", "github")
    past = CredentialResolver(FailingAt(fail_at={"owner"}), FakeSecrets(), _cfg(github_token="tok-env"), environ={})
    assert (await past.resolve_forge("acme/app", "github")).source == "env"


def test_the_store_and_the_resolver_share_one_pin_rule():
    from embry0.storage.repositories import credentials as store

    assert resolver_module.pin_allowed is store.pin_allowed
    allowed = {
        (role, scope)
        for role in ("forge", "llm", "deploy")
        for scope in ("repo", "owner", "host", "global")
        if store.pin_allowed(role, scope)
    }
    assert allowed == {("llm", "repo"), ("llm", "owner")}, allowed


# -- the key chosen for the job ------------------------------------------------------------------------------------

CHOSEN = _row("chosen", "tok-chosen", provider="xai", kind="llm_api_key")


class Ledger(LlmRepo):
    """A registry whose reads of rows by id, or of a job's uses, can fail."""

    def __init__(self, table=None, *, get_fails=False, uses_fail=False):
        super().__init__(table)
        self.get_fails = get_fails
        self.uses_fail = uses_fail

    async def get(self, credential_id):
        if self.get_fails:
            raise RuntimeError("db down")
        return await super().get(credential_id)

    async def uses_for_job(self, job_id):
        if self.uses_fail:
            raise RuntimeError("db down")
        return await super().uses_for_job(job_id)


def _with_pins(repo, pins):
    async def job_pins(job_id):
        return pins.get(job_id, {})

    return CredentialResolver(repo, FakeSecrets(), _cfg(), environ=dict(ENV), job_pins=job_pins)


async def test_a_chosen_key_that_cannot_be_read_fails_the_job():
    repo = Ledger({("global", "", "llm", "xai"): [POOL]}, get_fails=True)
    repo.by_id[CHOSEN["id"]] = CHOSEN
    r = _with_pins(repo, {"job-1": {"xai": CHOSEN["id"]}})
    with pytest.raises(CredentialUnavailableError) as raised:
        await r.resolve_llm_for_job("acme/app", ["xai"], job_id="job-1")
    assert str(raised.value) == f"the key chosen for this job ({CHOSEN['id']}) could not be read: RuntimeError"


async def test_a_derived_sandbox_runs_on_the_job_s_chosen_key_when_its_uses_cannot_be_read():
    repo = Ledger({("global", "", "llm", "xai"): [POOL]}, uses_fail=True)
    repo.by_id[CHOSEN["id"]] = CHOSEN
    repo.job_uses["job-1"] = [{"role": "llm", "provider": "xai", "credential_id": CHOSEN["id"]}]
    r = _with_pins(repo, {"job-1": {"xai": CHOSEN["id"]}})
    got = await r.resolve_llm_for_job("acme/app", ["xai"], job_id="job-1__bootstrap", parent_job_id="job-1")
    assert got["xai"].id == CHOSEN["id"], f"the derived sandbox ran on {got['xai'].label}, not the job's chosen key"
    assert repo.uses[-1]["job_id"] == "job-1__bootstrap", "the use is the derived sandbox's"


async def test_a_derived_sandbox_fails_when_the_job_s_chosen_key_cannot_serve():
    spent = dict(CHOSEN, status="quarantined", quarantined_until=datetime.now(UTC) + timedelta(hours=1))
    repo = Ledger({("global", "", "llm", "xai"): [POOL]})
    repo.by_id[CHOSEN["id"]] = spent
    repo.job_uses["job-1"] = [{"role": "llm", "provider": "xai", "credential_id": CHOSEN["id"]}]
    r = _with_pins(repo, {"job-1": {"xai": CHOSEN["id"]}})
    with pytest.raises(CredentialUnavailableError, match="quarantined"):
        await r.resolve_llm_for_job("acme/app", ["xai"], job_id="job-1__bootstrap", parent_job_id="job-1")
    assert repo.uses == [], "no other account was recorded for the derived sandbox"


async def test_a_derived_sandbox_of_a_job_that_chose_nothing_reuses_what_the_job_used():
    other = _row("other", "tok-other", provider="xai", kind="llm_api_key")
    repo = Ledger({("global", "", "llm", "xai"): [POOL, other]})
    repo.job_uses["job-1"] = [{"role": "llm", "provider": "xai", "credential_id": other["id"]}]
    r = _with_pins(repo, {})
    got = await r.resolve_llm_for_job("acme/app", ["xai"], job_id="job-1__bootstrap", parent_job_id="job-1")
    assert got["xai"].id == other["id"], "as before: the parent's account, not the pool's first"


async def test_a_key_chosen_for_the_derived_sandbox_itself_outranks_the_job_s():
    own = _row("own", "tok-own", provider="xai", kind="llm_api_key")
    repo = Ledger({("global", "", "llm", "xai"): [POOL]})
    repo.by_id.update({CHOSEN["id"]: CHOSEN, own["id"]: own})
    r = _with_pins(repo, {"job-1": {"xai": CHOSEN["id"]}, "job-1__bootstrap": {"xai": own["id"]}})
    got = await r.resolve_llm_for_job("acme/app", ["xai"], job_id="job-1__bootstrap", parent_job_id="job-1")
    assert got["xai"].id == own["id"]
