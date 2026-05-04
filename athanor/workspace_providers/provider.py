"""Workspace provider types and Protocol.

The single abstraction that lets athanor stay ecosystem-agnostic. Provider
implementations own *workspace topology only*: what apps/packages exist,
what depends on what, what changed in a diff. Boot/build commands live in
qa.yaml per-app — providers do not own them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class WorkspaceApp:
    """A runnable workspace member.

    `name` is the short, human-friendly identifier used as the key under
    `apps:` in qa.yaml (e.g. "hub"). `package_name` is the importable name
    (e.g. "@raven/hub") used by the workspace dependency graph.
    """

    name: str
    path: Path
    package_name: str


@dataclass(frozen=True, slots=True)
class WorkspacePackage:
    """A non-runnable workspace member (library) OR a runnable app.

    `is_app=True` when this entry is also a runnable app — the lib/app
    distinction is workspace-provider specific. Apps appear in BOTH
    `discover()` return tuples (as `WorkspaceApp` and as `WorkspacePackage
    is_app=True`).
    """

    name: str
    path: Path
    is_app: bool


@dataclass(frozen=True, slots=True)
class AffectedSet:
    """Result of `affected()`. All three sets are package_name-keyed.

    Invariants (enforced via assertions in callers, not __post_init__,
    because frozen dataclasses with slots don't support __post_init__
    that mutates):
      - apps_to_qa ⊆ cascade_closure
      - directly_changed ⊆ cascade_closure
    """

    directly_changed: frozenset[str]
    cascade_closure: frozenset[str]
    apps_to_qa: frozenset[str]


class WorkspaceProviderError(Exception):
    """Raised when a workspace_provider impl cannot operate.

    Examples: missing package.json, unparseable lockfile, malformed
    workspaces glob, child of `tools` shelling out and exiting non-zero.

    Distinct from `pydantic.ValidationError` (config-shape errors).
    """
