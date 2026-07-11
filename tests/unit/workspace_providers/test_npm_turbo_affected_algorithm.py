"""Tests for _affected — pure-Python closure with no_cascade subtraction.

The toy-monorepo dep shape (Phase 3 reference):

  @toy/hub   → {@toy/auth, @toy/types}
  @toy/companion → {@toy/auth}
  @toy/lane  → {@toy/types}
  @toy/auth  → {}
  @toy/types → {}

Apps: @toy/hub, @toy/companion, @toy/lane
no_cascade (per qa.yaml): {@toy/types}
"""

from __future__ import annotations

import pytest

from embry0.workspace_providers.npm_workspaces_turbo._affected import (
    compute_affected,
)
from embry0.workspace_providers.npm_workspaces_turbo._dep_graph import (
    DependencyGraph,
)


@pytest.fixture
def toy_graph() -> DependencyGraph:
    return DependencyGraph(
        nodes=frozenset({"@toy/hub", "@toy/companion", "@toy/lane", "@toy/auth", "@toy/types"}),
        _out={
            "@toy/hub": frozenset({"@toy/auth", "@toy/types"}),
            "@toy/companion": frozenset({"@toy/auth"}),
            "@toy/lane": frozenset({"@toy/types"}),
            "@toy/auth": frozenset(),
            "@toy/types": frozenset(),
        },
        _in={
            "@toy/hub": frozenset(),
            "@toy/companion": frozenset(),
            "@toy/lane": frozenset(),
            "@toy/auth": frozenset({"@toy/hub", "@toy/companion"}),
            "@toy/types": frozenset({"@toy/hub", "@toy/lane"}),
        },
    )


_TOY_APPS = frozenset({"@toy/hub", "@toy/companion", "@toy/lane"})


def test_no_changes_yields_empty_affected_set(toy_graph: DependencyGraph):
    result = compute_affected(
        directly_changed=frozenset(),
        graph=toy_graph,
        apps=_TOY_APPS,
        no_cascade_packages=frozenset(),
    )
    assert result.directly_changed == frozenset()
    assert result.cascade_closure == frozenset()
    assert result.apps_to_qa == frozenset()


def test_app_only_change_qas_just_that_app(toy_graph: DependencyGraph):
    """Diff in apps/hub only → apps_to_qa = {@toy/hub}."""
    result = compute_affected(
        directly_changed=frozenset({"@toy/hub"}),
        graph=toy_graph,
        apps=_TOY_APPS,
        no_cascade_packages=frozenset(),
    )
    assert result.directly_changed == frozenset({"@toy/hub"})
    assert result.cascade_closure == frozenset({"@toy/hub"})
    assert result.apps_to_qa == frozenset({"@toy/hub"})


def test_package_change_cascades_to_dependent_apps(toy_graph: DependencyGraph):
    """@toy/auth changed (no_cascade=False) → @toy/hub + @toy/companion affected."""
    result = compute_affected(
        directly_changed=frozenset({"@toy/auth"}),
        graph=toy_graph,
        apps=_TOY_APPS,
        no_cascade_packages=frozenset(),
    )
    assert result.directly_changed == frozenset({"@toy/auth"})
    assert result.cascade_closure == frozenset({"@toy/auth", "@toy/hub", "@toy/companion"})
    assert result.apps_to_qa == frozenset({"@toy/hub", "@toy/companion"})


def test_no_cascade_package_change_yields_no_apps(toy_graph: DependencyGraph):
    """@toy/types is no_cascade → diff there → no apps QA'd through that path."""
    result = compute_affected(
        directly_changed=frozenset({"@toy/types"}),
        graph=toy_graph,
        apps=_TOY_APPS,
        no_cascade_packages=frozenset({"@toy/types"}),
    )
    # cascade_closure still includes the structural reach (for reporting),
    # but apps_to_qa subtracts apps that are reachable ONLY via no_cascade.
    assert "@toy/types" in result.directly_changed
    assert result.apps_to_qa == frozenset()


def test_no_cascade_does_not_block_independent_paths(toy_graph: DependencyGraph):
    """If both @toy/types AND @toy/auth changed, @toy/hub is reachable via
    @toy/auth (which is NOT no_cascade), so @toy/hub IS QA'd. @toy/lane is
    reachable only via @toy/types, so @toy/lane is NOT QA'd."""
    result = compute_affected(
        directly_changed=frozenset({"@toy/types", "@toy/auth"}),
        graph=toy_graph,
        apps=_TOY_APPS,
        no_cascade_packages=frozenset({"@toy/types"}),
    )
    assert result.apps_to_qa == frozenset({"@toy/hub", "@toy/companion"})


def test_directly_changed_app_is_always_qad_even_if_only_path_is_via_no_cascade(
    toy_graph: DependencyGraph,
):
    """If an APP itself appears in directly_changed, it's QA'd regardless of
    cascade-via-no_cascade reasoning. no_cascade is about cascade reach, not
    self-membership."""
    # @toy/lane only reaches via @toy/types in the graph, but if lane itself
    # is in directly_changed, lane stays in apps_to_qa.
    result = compute_affected(
        directly_changed=frozenset({"@toy/lane"}),
        graph=toy_graph,
        apps=_TOY_APPS,
        no_cascade_packages=frozenset({"@toy/types"}),
    )
    assert result.apps_to_qa == frozenset({"@toy/lane"})


def test_invariants_hold_in_general(toy_graph: DependencyGraph):
    """apps_to_qa ⊆ cascade_closure; directly_changed ⊆ cascade_closure."""
    for changed in (
        frozenset(),
        frozenset({"@toy/auth"}),
        frozenset({"@toy/types"}),
        frozenset({"@toy/auth", "@toy/types"}),
        frozenset({"@toy/hub"}),
    ):
        for nc in (frozenset(), frozenset({"@toy/types"}), frozenset({"@toy/types", "@toy/auth"})):
            result = compute_affected(
                directly_changed=changed,
                graph=toy_graph,
                apps=_TOY_APPS,
                no_cascade_packages=nc,
            )
            assert result.directly_changed <= result.cascade_closure
            assert result.apps_to_qa <= result.cascade_closure


def test_unknown_directly_changed_node_does_not_crash(toy_graph: DependencyGraph):
    """A `directly_changed` entry not in the graph (e.g. a package whose
    dir was deleted on the head branch) is tolerated — it appears in
    `directly_changed` but contributes no closure."""
    result = compute_affected(
        directly_changed=frozenset({"@toy/auth", "@toy/ghost"}),
        graph=toy_graph,
        apps=_TOY_APPS,
        no_cascade_packages=frozenset(),
    )
    assert "@toy/ghost" in result.directly_changed
    assert "@toy/ghost" in result.cascade_closure
    # Real cascade still works for the known one.
    assert {"@toy/hub", "@toy/companion"} <= result.apps_to_qa
