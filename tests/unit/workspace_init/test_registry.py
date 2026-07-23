"""Registry: builtin resolution, entry-point override, unknown names."""

import pytest

from embry0.orchestration.state import UnsupportedContextError
from embry0.workspace_init import WorkspaceInitializer, load_initializer
from embry0.workspace_init import registry as reg
from embry0.workspace_init.none import NoneWorkspaceInitializer


@pytest.fixture(autouse=True)
def _clear_cache():
    reg._cache_clear()
    yield
    reg._cache_clear()


@pytest.mark.parametrize("name", ["git", "http", "local", "none"])
def test_builtins_resolve_and_satisfy_protocol(name):
    initializer = load_initializer(name)
    assert isinstance(initializer, WorkspaceInitializer)
    assert initializer.name == name


def test_unknown_name_raises_unsupported_context():
    with pytest.raises(UnsupportedContextError) as ei:
        load_initializer("bogus")
    assert ei.value.context_type == "bogus"


def test_entry_point_overrides_builtin(monkeypatch):
    class CustomNone(NoneWorkspaceInitializer):
        pass

    monkeypatch.setattr(reg, "_resolve_entry_point", lambda name: CustomNone if name == "none" else None)
    reg._cache_clear()
    assert type(load_initializer("none")) is CustomNone


def test_available_names_include_builtins():
    names = reg.available_initializer_names()
    assert {"git", "http", "local", "none"} <= set(names)
