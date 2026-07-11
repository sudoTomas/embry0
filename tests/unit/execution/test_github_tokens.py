import pytest

from embry0.execution.github_tokens import all_github_tokens, resolve_for_repo, resolve_github_token


@pytest.fixture(autouse=True)
def _clean_owner_tokens(monkeypatch):
    import os

    for key in [k for k in os.environ if k.startswith("GITHUB_TOKEN__")]:
        monkeypatch.delenv(key)


def test_resolves_per_owner_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN__ACME_CORP", "ghp_acme")
    assert resolve_github_token("acme-corp", "ghp_default") == "ghp_acme"


def test_sanitizes_hyphens_and_uppercases(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN__OCTO_ORG", "ghp_octo")
    assert resolve_github_token("octo-org", "ghp_default") == "ghp_octo"


def test_falls_back_to_default_when_no_owner_env(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN__ACME_CORP", raising=False)
    assert resolve_github_token("acme-corp", "ghp_default") == "ghp_default"


def test_none_owner_returns_default():
    assert resolve_github_token(None, "ghp_default") == "ghp_default"


def test_empty_owner_returns_default():
    assert resolve_github_token("", "ghp_default") == "ghp_default"


def test_resolve_for_repo_hits_owner_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN__ACME_CORP", "tok-rc")
    assert resolve_for_repo("acme-corp/widgets", "tok-default") == "tok-rc"


def test_resolve_for_repo_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN__SOMEORG", raising=False)
    assert resolve_for_repo("someorg/repo", "tok-default") == "tok-default"


def test_resolve_for_repo_none_and_slashless_use_default():
    assert resolve_for_repo(None, "tok-default") == "tok-default"
    assert resolve_for_repo("just-a-name", "tok-default") == "tok-default"


def test_all_github_tokens_default_first_then_sorted_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN__ZED_ORG", "tok-z")
    monkeypatch.setenv("GITHUB_TOKEN__ACME", "tok-a")
    assert all_github_tokens("tok-default") == ["tok-default", "tok-a", "tok-z"]


def test_all_github_tokens_dedups_and_drops_empty(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN__ACME", "tok-default")  # duplicate of default
    monkeypatch.setenv("GITHUB_TOKEN__EMPTY", "")
    assert all_github_tokens("tok-default") == ["tok-default"]


def test_all_github_tokens_no_default(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN__ACME", "tok-a")
    assert all_github_tokens("") == ["tok-a"]
