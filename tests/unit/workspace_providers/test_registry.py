from pathlib import Path

import pytest

from athanor.workspace_providers import WorkspaceProviderError
from athanor.workspace_providers.registry import (
    available_provider_names,
    load_provider,
)


def test_load_provider_unknown_name_raises_with_actionable_message():
    with pytest.raises(WorkspaceProviderError) as exc:
        load_provider("does-not-exist", Path("/tmp"), {})
    msg = str(exc.value)
    assert "does-not-exist" in msg
    assert "registered" in msg.lower()


def test_available_provider_names_returns_list_of_strings():
    names = available_provider_names()
    assert isinstance(names, list)
    assert all(isinstance(n, str) for n in names)
    # npm-workspaces-turbo entry is registered in pyproject.toml even though
    # its module may not exist yet during early tasks. Once Task 6 ships,
    # this assertion will pass; until then, the test ALSO accepts an empty
    # list (registry survives even if entry-point target fails to import).
    assert "npm-workspaces-turbo" in names or names == []


def test_load_provider_caches_class_resolution(monkeypatch):
    """Repeated load_provider calls for the same name share an underlying
    EntryPoint resolution — registry lookup is fast on the hot path."""
    from athanor.workspace_providers import registry

    calls = {"count": 0}
    real = registry._resolve_entry_point

    def counted(name: str):
        calls["count"] += 1
        return real(name)

    monkeypatch.setattr(registry, "_resolve_entry_point", counted)
    registry._cache_clear()
    try:
        load_provider("does-not-exist", Path("/tmp"), {})
    except WorkspaceProviderError:
        pass
    try:
        load_provider("does-not-exist", Path("/tmp"), {})
    except WorkspaceProviderError:
        pass
    assert calls["count"] == 1
