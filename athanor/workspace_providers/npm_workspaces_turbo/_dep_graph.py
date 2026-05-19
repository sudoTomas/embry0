"""Workspace dependency graph — pure-Python builder.

Reads every package.json in the workspace and constructs a directed graph
of (consumer → provider) edges. Only workspace-internal package names appear
as nodes; external deps (e.g. `react`, `next`) are filtered out.

The graph is the input to the affected-set algorithm: when a package's
files change, every node reachable in the *reverse* direction is part
of the cascade closure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from athanor.workspace_providers.provider import WorkspaceProviderError

_EDGE_FIELDS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


@dataclass(frozen=True, slots=True)
class DependencyGraph:
    """A directed graph (consumer → provider) over workspace-internal packages.

    Lookup helpers:
      - edges_out(node): packages this node depends on (forward edges).
      - consumers_of(node): packages that depend on this node (reverse edges).

    Both return frozenset() for unknown nodes (never raises).
    """

    nodes: frozenset[str]
    _out: dict[str, frozenset[str]] = field(default_factory=dict)
    _in: dict[str, frozenset[str]] = field(default_factory=dict)

    def edges_out(self, node: str) -> frozenset[str]:
        return self._out.get(node, frozenset())

    def consumers_of(self, node: str) -> frozenset[str]:
        return self._in.get(node, frozenset())


def _read_package_json(path: Path) -> dict[str, Any] | None:
    """Read a package.json. Returns None if missing or unparseable; never raises.

    Note: contract differs from _discover._read_package_json (which raises).
    Per-member failures degrade gracefully here so a single broken member
    does not block the whole graph; only root failure is fatal (raised
    by build_dep_graph itself).
    """
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _enumerate_workspace_dirs(repo_root: Path, *globs: str) -> list[Path]:
    """Expand the workspace globs and return matching directories."""
    out: list[Path] = []
    seen: set[Path] = set()
    for glob in globs:
        for p in sorted(repo_root.glob(glob)):
            if p.is_dir():
                resolved = p.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    out.append(p)
    return out


def build_dep_graph(
    *,
    repo_root: Path,
    apps_glob: str,
    packages_glob: str,
) -> DependencyGraph:
    """Build the dep graph from disk.

    Reads every package.json under apps_glob/packages_glob, collects the union
    of `dependencies`, `devDependencies`, `peerDependencies`, and
    `optionalDependencies`, and keeps only edges pointing at *workspace-internal*
    members (external deps like `react` are dropped).

    Workspace dirs that have no package.json are silently skipped — same policy
    as `_discover.discover_workspaces`.

    Raises `WorkspaceProviderError` only when the root package.json is missing
    or unparseable; per-member parse failures degrade gracefully (skipped).
    """
    root_pkg_path = repo_root / "package.json"
    if _read_package_json(root_pkg_path) is None:
        raise WorkspaceProviderError(f"missing or unparseable root package.json at {root_pkg_path}")

    member_dirs = _enumerate_workspace_dirs(repo_root, apps_glob, packages_glob)

    # First pass: collect every workspace-internal package name.
    name_to_pkg: dict[str, dict[str, Any]] = {}
    for d in member_dirs:
        pkg = _read_package_json(d / "package.json")
        if pkg is None:
            continue
        name = pkg.get("name")
        if isinstance(name, str) and name and name not in name_to_pkg:
            name_to_pkg[name] = pkg

    nodes = frozenset(name_to_pkg.keys())

    # Second pass: build forward + reverse edge maps, filtered to internal names.
    out_edges: dict[str, frozenset[str]] = {}
    in_edges: dict[str, set[str]] = {n: set() for n in nodes}

    for name, pkg in name_to_pkg.items():
        deps: set[str] = set()
        for dep_field in _EDGE_FIELDS:
            block = pkg.get(dep_field)
            if not isinstance(block, dict):
                continue
            for dep_name in block.keys():
                if isinstance(dep_name, str) and dep_name in nodes and dep_name != name:
                    deps.add(dep_name)
        out_edges[name] = frozenset(deps)
        for dep in deps:
            in_edges[dep].add(name)

    return DependencyGraph(
        nodes=nodes,
        _out=out_edges,
        _in={k: frozenset(v) for k, v in in_edges.items()},
    )
