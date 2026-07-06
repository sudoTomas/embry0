from athanor.execution.github_tokens import resolve_github_token


def test_resolves_per_owner_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN__RAVEN_CARGO", "ghp_raven")
    assert resolve_github_token("client-project", "ghp_default") == "ghp_raven"


def test_sanitizes_hyphens_and_uppercases(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN__ALQVIMIA_LABS", "ghp_alq")
    assert resolve_github_token("former-org", "ghp_default") == "ghp_alq"


def test_falls_back_to_default_when_no_owner_env(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN__RAVEN_CARGO", raising=False)
    assert resolve_github_token("client-project", "ghp_default") == "ghp_default"


def test_none_owner_returns_default():
    assert resolve_github_token(None, "ghp_default") == "ghp_default"


def test_empty_owner_returns_default():
    assert resolve_github_token("", "ghp_default") == "ghp_default"
