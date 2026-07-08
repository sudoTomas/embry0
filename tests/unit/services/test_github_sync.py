"""Label parsing edge cases."""

import pytest


def test_extract_labels_from_payload_skips_non_dicts():
    """Labels that aren't dicts must be ignored, not crash."""
    from athanor.services.github_sync import _extract_label_names

    payload_labels = [
        {"name": "bug"},
        None,
        "accidentally-a-string",
        {"color": "red"},  # dict but no "name" key
        {"name": "Athanor"},
    ]
    assert _extract_label_names(payload_labels) == ["bug", "Athanor"]


def test_extract_labels_from_empty_list():
    from athanor.services.github_sync import _extract_label_names

    assert _extract_label_names([]) == []


def test_extract_labels_from_none():
    """labels field missing/None should also be safe."""
    from athanor.services.github_sync import _extract_label_names

    assert _extract_label_names(None) == []


def test_extract_labels_skips_non_string_name():
    """A dict with a non-string name value must be skipped."""
    from athanor.services.github_sync import _extract_label_names

    assert _extract_label_names([{"name": 42}, {"name": "ok"}, {"name": None}]) == ["ok"]


@pytest.fixture(autouse=True)
def _clean_owner_tokens(monkeypatch):
    """Clean up GITHUB_TOKEN__ env vars for isolated tests."""
    import os

    for key in [k for k in os.environ if k.startswith("GITHUB_TOKEN__")]:
        monkeypatch.delenv(key)


def test_headers_resolve_per_owner_token(monkeypatch):
    from athanor.services.github_sync import GitHubSyncService

    monkeypatch.setenv("GITHUB_TOKEN__RAVEN_CARGO", "tok-rc")
    svc = GitHubSyncService(github_token="tok-default")
    assert svc._headers("client-project/ai-quoting")["Authorization"] == "Bearer tok-rc"
    assert svc._headers("former-org/embry0")["Authorization"] == "Bearer tok-default"
    assert svc._headers()["Authorization"] == "Bearer tok-default"  # back-compat


def test_headers_no_tokens_at_all(monkeypatch):
    from athanor.services.github_sync import GitHubSyncService

    monkeypatch.delenv("GITHUB_TOKEN__RAVEN_CARGO", raising=False)
    svc = GitHubSyncService(github_token=None)
    assert "Authorization" not in svc._headers("client-project/ai-quoting")
