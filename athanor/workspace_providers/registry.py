"""Provider registry — entry-point discovery + lookup.

Providers register via Python entry points under the
``athanor.workspace_providers`` group. Lookup is cached per (name, success)
because resolution shells out to importlib.metadata which is non-trivial.
"""

from __future__ import annotations

from functools import lru_cache
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from athanor.workspace_providers.provider import (
    WorkspaceProvider,
    WorkspaceProviderError,
)

_GROUP = "athanor.workspace_providers"


def _resolve_entry_point(name: str) -> Any:
    """Resolve a provider class by name, or return None if not registered.

    Separate from the cached wrapper so tests can monkeypatch this.
    """
    eps = entry_points(group=_GROUP)
    for ep in eps:
        if ep.name == name:
            try:
                return ep.load()
            except Exception as exc:  # noqa: BLE001 — provider import errors are surfaced
                raise WorkspaceProviderError(
                    f"workspace_provider '{name}' is registered but failed to load: {exc}"
                ) from exc
    return None


@lru_cache(maxsize=32)
def _cached_resolve(name: str) -> Any:
    return _resolve_entry_point(name)


def _cache_clear() -> None:
    """Test-only: reset the resolution cache."""
    _cached_resolve.cache_clear()


def available_provider_names() -> list[str]:
    """List every registered provider name.

    Resilient to broken entry points: a provider whose import fails is
    listed by name but cannot be loaded via load_provider.
    """
    return sorted({ep.name for ep in entry_points(group=_GROUP)})


def load_provider(
    name: str, repo_root: Path, config: dict[str, Any]
) -> WorkspaceProvider:
    """Instantiate a registered provider.

    Raises WorkspaceProviderError if `name` is not registered or the
    resolved class doesn't satisfy WorkspaceProvider.
    """
    cls = _cached_resolve(name)
    if cls is None:
        registered = available_provider_names()
        raise WorkspaceProviderError(
            f"no workspace_provider registered named {name!r} — "
            f"installed providers are: {registered or '(none)'}"
        )

    instance = cls(repo_root, config)
    if not isinstance(instance, WorkspaceProvider):
        raise WorkspaceProviderError(
            f"workspace_provider '{name}' resolved to {cls!r} which does not "
            "satisfy the WorkspaceProvider Protocol (missing methods?)"
        )
    return instance
