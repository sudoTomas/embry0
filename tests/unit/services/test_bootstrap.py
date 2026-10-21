"""bootstrap_repo tests (EMB-49, DB-backed per RAV-1251) — templates, gates, GitHub verification."""

from __future__ import annotations

import httpx
import pytest

from embry0.services.bootstrap import (
    BootstrapError,
    bootstrap_repo,
    render_starter_qa_yaml,
)
from embry0.workflows.qa.qa_yaml_resolve import resolve_app_config
from embry0.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2


def test_minimal_template_validates_and_skips_qa():
    text = render_starter_qa_yaml("acme/newrepo")
    cfg = parse_qa_yaml_v2(text)
    assert cfg.qa_required == "never"
    assert cfg.apps == {}


def test_app_template_validates_with_ready_check():
    text = render_starter_qa_yaml(
        "acme/newrepo", app="web", boot_command="npm run dev", frontend_url="http://localhost:5173"
    )
    cfg = parse_qa_yaml_v2(text)
    assert cfg.qa_required == "auto"
    assert cfg.apps["web"].boot_command == "npm run dev"
    assert cfg.apps["web"].ready_checks[0].http == "http://localhost:5173"


def test_app_template_tests_on_a_work_machine_with_a_browser():
    """slim has no browser, so a starter that fell back to the schema default
    failed every test run. The starter names the browser machine itself."""
    cfg = parse_qa_yaml_v2(render_starter_qa_yaml("acme/widgets", app="web"))
    resolved = resolve_app_config("web", cfg, cfg.app_local.get("web"))
    assert resolved.sandbox_profile == "qa-external"


def test_app_template_round_trips_quotes_and_backslashes():
    boot = 'npm run dev -- --host "0.0.0.0" --label "a\\b"'
    url = 'http://localhost:3000/?q="x"'
    cfg = parse_qa_yaml_v2(
        render_starter_qa_yaml("acme/widgets", app="web", boot_command=boot, frontend_url=url, ready_check_url=url)
    )
    assert cfg.apps["web"].boot_command == boot
    assert cfg.apps["web"].frontend_url == url
    assert cfg.apps["web"].ready_checks[0].http == url


@pytest.mark.parametrize("app", [None, "web"])
def test_starter_header_says_embry0_keeps_it(app):
    text = render_starter_qa_yaml("acme/widgets", app=app)
    assert text.splitlines()[0] == (
        "# Starter test setup for acme/widgets. embry0 keeps it; the repository needs no copy."
    )


class _FakeConfigs:
    """In-memory RepoQAConfigsRepository stand-in."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    async def get(self, repo):
        return self.rows.get(repo)

    async def upsert(self, repo, yaml_text, updated_by=""):
        self.rows[repo] = {
            "repo": repo,
            "yaml_text": yaml_text,
            "updated_by": updated_by,
            "updated_at": "now",
        }
        return self.rows[repo]


@pytest.fixture
def configs():
    return _FakeConfigs()


@pytest.fixture
def github_ok(monkeypatch):
    async def _fake(repo, token, credential_resolver=None):
        assert token == "tok"
        return "main", False

    monkeypatch.setattr("embry0.services.bootstrap._verify_github_repo", _fake)


class _FakePrefs:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    async def upsert(self, repo, sandbox_profile=None, **kw):
        self.rows[repo] = {"sandbox_profile": sandbox_profile}
        return self.rows[repo]


class _FakeProfiles:
    def __init__(self, names):
        self.names = names

    async def get(self, name):
        return {"name": name} if name in self.names else None


async def test_bootstrap_minimal_writes_db(configs, github_ok):
    result = await bootstrap_repo("acme/newrepo", github_token="tok", qa_configs_repo=configs)
    row = configs.rows["acme/newrepo"]
    assert row["updated_by"] == "bootstrap"
    parse_qa_yaml_v2(row["yaml_text"])
    assert result.qa_required == "never"
    assert result.default_branch == "main"
    assert result.qa_config_path == "db:repo_qa_configs/acme/newrepo"
    assert any("embry0 DB" in s for s in result.steps)


async def test_bootstrap_refuses_existing_without_force(configs, github_ok):
    await bootstrap_repo("acme/newrepo", github_token="tok", qa_configs_repo=configs)
    with pytest.raises(BootstrapError) as exc_info:
        await bootstrap_repo("acme/newrepo", github_token="tok", qa_configs_repo=configs)
    assert str(exc_info.value) == (
        "This repository already has a test setup. Replacing it keeps the current one in History "
        "(send force=true, or --force on the command line)."
    )
    # force overwrites
    result = await bootstrap_repo("acme/newrepo", github_token="tok", qa_configs_repo=configs, app="web", force=True)
    assert result.qa_required == "auto"


async def test_bootstrap_seeds_prefs_with_valid_profile(configs, github_ok):
    prefs = _FakePrefs()
    await bootstrap_repo(
        "acme/newrepo",
        github_token="tok",
        qa_configs_repo=configs,
        prefs_repo=prefs,
        profiles_repo=_FakeProfiles({"slim"}),
        sandbox_profile="slim",
    )
    assert prefs.rows["acme/newrepo"]["sandbox_profile"] == "slim"


async def test_bootstrap_rejects_unknown_profile_before_writing_the_setup(configs, github_ok):
    async def _no_write(repo, yaml_text, updated_by=""):
        raise AssertionError("the test setup was written before the work machine was checked")

    configs.upsert = _no_write
    with pytest.raises(BootstrapError) as exc_info:
        await bootstrap_repo(
            "acme/newrepo",
            github_token="tok",
            qa_configs_repo=configs,
            prefs_repo=_FakePrefs(),
            profiles_repo=_FakeProfiles(set()),
            sandbox_profile="nope",
        )
    assert str(exc_info.value) == "Work machine 'nope' does not exist on this embry0."
    assert configs.rows == {}


async def test_bootstrap_fails_before_db_write_on_github_404(configs, monkeypatch):
    def _handler(request):
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(_handler)
    real_client = httpx.AsyncClient

    def _client(*a, **kw):
        kw["transport"] = transport
        return real_client(*a, **kw)

    monkeypatch.setattr("embry0.services.bootstrap.httpx.AsyncClient", _client)
    with pytest.raises(BootstrapError, match="not found"):
        await bootstrap_repo("acme/ghost", github_token="tok", qa_configs_repo=configs)
    assert configs.rows == {}


async def test_bootstrap_requires_token(configs, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN__ACME", raising=False)
    with pytest.raises(BootstrapError, match="no GitHub token"):
        await bootstrap_repo("acme/newrepo", github_token="", qa_configs_repo=configs)


@pytest.mark.asyncio
async def test_bootstrap_surfaces_an_unregisterable_repo_as_a_caller_error(configs, github_ok):
    """The QA-config store auto-registers the repo, and on a DEFAULT_FORGE=""
    deployment it refuses rather than inventing a forge. That must reach the
    caller as a 400-able BootstrapError, not an opaque ValueError."""

    async def refusing_upsert(repo, yaml_text, updated_by=""):
        raise ValueError(f"repo {repo!r} is not registered and DEFAULT_FORGE is empty")

    configs.upsert = refusing_upsert
    with pytest.raises(BootstrapError, match="is not registered"):
        await bootstrap_repo("acme/newrepo", github_token="tok", qa_configs_repo=configs)


async def test_verify_maps_an_unavailable_pinned_credential_to_bootstrap_error():
    from unittest.mock import AsyncMock, MagicMock

    from embry0.services.bootstrap import _verify_github_repo
    from embry0.services.credentials.resolver import CredentialUnavailableError

    resolver = MagicMock(
        resolve_forge=AsyncMock(side_effect=CredentialUnavailableError("credential 'ci' is pinned but disabled"))
    )
    with pytest.raises(BootstrapError, match="pinned"):
        await _verify_github_repo("acme/newrepo", "tok", credential_resolver=resolver)


async def test_verify_without_any_token_mentions_the_registry(monkeypatch):
    from embry0.services.bootstrap import _verify_github_repo

    monkeypatch.delenv("GITHUB_TOKEN__ACME", raising=False)
    with pytest.raises(BootstrapError, match="registry"):
        await _verify_github_repo("acme/newrepo", "")


async def test_bootstrap_repo_forwards_the_credential_resolver(configs, monkeypatch):
    seen = {}

    async def _fake(repo, token, credential_resolver=None):
        seen["resolver"] = credential_resolver
        return "main", False

    monkeypatch.setattr("embry0.services.bootstrap._verify_github_repo", _fake)
    resolver = object()
    await bootstrap_repo("acme/newrepo", github_token="tok", qa_configs_repo=configs, credential_resolver=resolver)
    assert seen["resolver"] is resolver
