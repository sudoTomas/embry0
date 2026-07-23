"""bootstrap_repo tests (EMB-49) — templates, gates, GitHub verification."""

from __future__ import annotations

import httpx
import pytest

from embry0.services.bootstrap import (
    BootstrapError,
    bootstrap_repo,
    render_starter_qa_yaml,
)
from embry0.workflows.qa.qa_config_store import QA_CONFIG_DIR_ENV
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


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(QA_CONFIG_DIR_ENV, str(tmp_path))
    return tmp_path


@pytest.fixture
def github_ok(monkeypatch):
    async def _fake(repo, token):
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


async def test_bootstrap_minimal_writes_store(store, github_ok):
    result = await bootstrap_repo("acme/newrepo", github_token="tok")
    assert (store / "acme__newrepo" / "qa.yaml").is_file()
    assert result.qa_required == "never"
    assert result.default_branch == "main"
    assert any("config store" in s for s in result.steps)


async def test_bootstrap_refuses_existing_without_force(store, github_ok):
    await bootstrap_repo("acme/newrepo", github_token="tok")
    with pytest.raises(BootstrapError, match="already exists"):
        await bootstrap_repo("acme/newrepo", github_token="tok")
    # force overwrites
    result = await bootstrap_repo("acme/newrepo", github_token="tok", app="web", force=True)
    assert result.qa_required == "auto"


async def test_bootstrap_seeds_prefs_with_valid_profile(store, github_ok):
    prefs = _FakePrefs()
    await bootstrap_repo(
        "acme/newrepo",
        github_token="tok",
        prefs_repo=prefs,
        profiles_repo=_FakeProfiles({"slim"}),
        sandbox_profile="slim",
    )
    assert prefs.rows["acme/newrepo"]["sandbox_profile"] == "slim"


async def test_bootstrap_rejects_unknown_profile(store, github_ok):
    with pytest.raises(BootstrapError, match="does not exist"):
        await bootstrap_repo(
            "acme/newrepo",
            github_token="tok",
            prefs_repo=_FakePrefs(),
            profiles_repo=_FakeProfiles(set()),
            sandbox_profile="nope",
        )


async def test_bootstrap_fails_before_store_write_on_github_404(store, monkeypatch):
    def _handler(request):
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(_handler)
    real_client = httpx.AsyncClient

    def _client(*a, **kw):
        kw["transport"] = transport
        return real_client(*a, **kw)

    monkeypatch.setattr("embry0.services.bootstrap.httpx.AsyncClient", _client)
    with pytest.raises(BootstrapError, match="not found"):
        await bootstrap_repo("acme/ghost", github_token="tok")
    assert not (store / "acme__ghost").exists()


async def test_bootstrap_requires_token(store, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN__ACME", raising=False)
    with pytest.raises(BootstrapError, match="no GitHub token"):
        await bootstrap_repo("acme/newrepo", github_token="")
