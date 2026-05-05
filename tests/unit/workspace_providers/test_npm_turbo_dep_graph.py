"""Tests for _dep_graph — pure-Python dependency graph builder."""

from __future__ import annotations

from pathlib import Path

from athanor.workspace_providers.npm_workspaces_turbo._dep_graph import (
    DependencyGraph,
    build_dep_graph,
)


def _toy_repo() -> Path:
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "toy-monorepo"


def test_build_dep_graph_from_toy_monorepo():
    """Build the dep graph from the committed toy-monorepo fixture.

    Expected edges (consumer → provider):
      @toy/hub   → @toy/auth, @toy/types
      @toy/companion → @toy/auth
      @toy/lane  → @toy/types
      @toy/auth  → (none)
      @toy/types → (none)
    """
    graph = build_dep_graph(
        repo_root=_toy_repo(),
        apps_glob="apps/*",
        packages_glob="packages/*",
    )
    assert isinstance(graph, DependencyGraph)
    assert graph.nodes == frozenset(
        {"@toy/hub", "@toy/companion", "@toy/lane", "@toy/auth", "@toy/types"}
    )
    assert graph.edges_out("@toy/hub") == frozenset({"@toy/auth", "@toy/types"})
    assert graph.edges_out("@toy/companion") == frozenset({"@toy/auth"})
    assert graph.edges_out("@toy/lane") == frozenset({"@toy/types"})
    assert graph.edges_out("@toy/auth") == frozenset()
    assert graph.edges_out("@toy/types") == frozenset()


def test_consumers_of_returns_reverse_edges():
    """consumers_of(@toy/auth) → {@toy/hub, @toy/companion} (reverse closure)."""
    graph = build_dep_graph(
        repo_root=_toy_repo(),
        apps_glob="apps/*",
        packages_glob="packages/*",
    )
    assert graph.consumers_of("@toy/auth") == frozenset({"@toy/hub", "@toy/companion"})
    assert graph.consumers_of("@toy/types") == frozenset({"@toy/hub", "@toy/lane"})
    assert graph.consumers_of("@toy/hub") == frozenset()  # no one consumes apps


def test_dev_dependencies_and_peer_dependencies_count_as_edges(tmp_path: Path):
    """Workspace-internal references in `devDependencies` and `peerDependencies`
    must produce edges — turbo treats them as part of the dep graph."""
    (tmp_path / "package.json").write_text(
        '{"name": "root", "private": true, "workspaces": ["apps/*", "packages/*"]}'
    )
    (tmp_path / "apps" / "x").mkdir(parents=True)
    (tmp_path / "apps" / "x" / "package.json").write_text(
        '{"name": "@x/x", "private": true, "devDependencies": {"@x/lib": "*"}, '
        '"peerDependencies": {"@x/peer": "*"}}'
    )
    (tmp_path / "packages" / "lib").mkdir(parents=True)
    (tmp_path / "packages" / "lib" / "package.json").write_text(
        '{"name": "@x/lib", "private": true}'
    )
    (tmp_path / "packages" / "peer").mkdir(parents=True)
    (tmp_path / "packages" / "peer" / "package.json").write_text(
        '{"name": "@x/peer", "private": true}'
    )

    graph = build_dep_graph(repo_root=tmp_path, apps_glob="apps/*", packages_glob="packages/*")
    assert graph.edges_out("@x/x") == frozenset({"@x/lib", "@x/peer"})


def test_external_deps_are_filtered_out(tmp_path: Path):
    """Dependencies on packages NOT in the workspace (e.g. `react`) must NOT
    appear as graph edges. The graph contains only workspace-internal members."""
    (tmp_path / "package.json").write_text(
        '{"name": "root", "private": true, "workspaces": ["apps/*", "packages/*"]}'
    )
    (tmp_path / "apps" / "a").mkdir(parents=True)
    (tmp_path / "apps" / "a" / "package.json").write_text(
        '{"name": "@x/a", "private": true, '
        '"dependencies": {"@x/lib": "*", "react": "^19.0.0", "next": "^16.0.0"}}'
    )
    (tmp_path / "packages" / "lib").mkdir(parents=True)
    (tmp_path / "packages" / "lib" / "package.json").write_text(
        '{"name": "@x/lib", "private": true}'
    )
    graph = build_dep_graph(repo_root=tmp_path, apps_glob="apps/*", packages_glob="packages/*")
    assert graph.edges_out("@x/a") == frozenset({"@x/lib"})  # react/next filtered


def test_missing_workspace_member_raises(tmp_path: Path):
    """A `workspaces:` entry whose dir has no package.json must NOT crash —
    discover() already silently skips these. dep-graph follows the same rule."""
    (tmp_path / "package.json").write_text(
        '{"name": "root", "private": true, "workspaces": ["apps/*"]}'
    )
    (tmp_path / "apps" / "empty").mkdir(parents=True)  # no package.json
    (tmp_path / "apps" / "real").mkdir(parents=True)
    (tmp_path / "apps" / "real" / "package.json").write_text('{"name": "@x/real"}')

    graph = build_dep_graph(repo_root=tmp_path, apps_glob="apps/*", packages_glob="packages/*")
    assert graph.nodes == frozenset({"@x/real"})


def test_unknown_node_lookup_returns_empty():
    """edges_out and consumers_of must return frozenset() for unknown names."""
    graph = build_dep_graph(
        repo_root=_toy_repo(),
        apps_glob="apps/*",
        packages_glob="packages/*",
    )
    assert graph.edges_out("@nonexistent/x") == frozenset()
    assert graph.consumers_of("@nonexistent/x") == frozenset()
