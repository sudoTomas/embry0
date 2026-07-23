"""Initializer registry — builtins + entry-point overrides.

Mirrors ``embry0/workspace_providers/registry.py``: external packages may
register initializers via the ``embry0.workspace_init`` entry-point group;
the four builtin context types resolve without any installed entry points
so a plain checkout works. An entry point with a builtin's name overrides
the builtin (deliberate extension seam).
"""

from __future__ import annotations

from functools import lru_cache
from importlib.metadata import entry_points
from typing import Any

from embry0.orchestration.state import UnsupportedContextError
from embry0.workspace_init.base import WorkspaceInitError, WorkspaceInitializer

_GROUP = "embry0.workspace_init"


def _builtin_classes() -> dict[str, Any]:
    # Imported lazily so a syntax error in one strategy doesn't take down
    # the registry import chain (init_node imports this module).
    from embry0.workspace_init.git import GitWorkspaceInitializer
    from embry0.workspace_init.http import HttpWorkspaceInitializer
    from embry0.workspace_init.local import LocalWorkspaceInitializer
    from embry0.workspace_init.none import NoneWorkspaceInitializer

    return {
        "git": GitWorkspaceInitializer,
        "http": HttpWorkspaceInitializer,
        "local": LocalWorkspaceInitializer,
        "none": NoneWorkspaceInitializer,
    }


def _resolve_entry_point(name: str) -> Any:
    """Resolve an initializer class by name, or None if not registered.

    Separate from the cached wrapper so tests can monkeypatch this.
    """
    eps = entry_points(group=_GROUP)
    for ep in eps:
        if ep.name == name:
            try:
                return ep.load()
            except Exception as exc:  # noqa: BLE001 — import errors are surfaced
                raise WorkspaceInitError(
                    f"workspace_init strategy '{name}' is registered but failed to load: {exc}"
                ) from exc
    return None


@lru_cache(maxsize=32)
def _cached_resolve(name: str) -> Any:
    return _resolve_entry_point(name)


def _cache_clear() -> None:
    """Test-only: reset the resolution cache."""
    _cached_resolve.cache_clear()


def available_initializer_names() -> list[str]:
    """Every resolvable strategy name (builtins + entry points)."""
    names = set(_builtin_classes())
    names.update(ep.name for ep in entry_points(group=_GROUP))
    return sorted(names)


def load_initializer(name: str) -> WorkspaceInitializer:
    """Instantiate the strategy for a context type.

    Unknown names raise UnsupportedContextError so the existing
    ERR_UNSUPPORTED_CONTEXT mapping keeps meaning "no such strategy".
    """
    cls = _cached_resolve(name) or _builtin_classes().get(name)
    if cls is None:
        raise UnsupportedContextError(name)

    instance = cls()
    if not isinstance(instance, WorkspaceInitializer):
        raise WorkspaceInitError(
            f"workspace_init strategy '{name}' resolved to {cls!r} which does not "
            "satisfy the WorkspaceInitializer Protocol (missing methods?)"
        )
    return instance
