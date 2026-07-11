"""Pure-Python affected-set algorithm.

Given:
  - directly_changed: workspace packages whose files appear in the diff
  - the dependency graph (consumer → provider edges)
  - the set of runnable apps
  - no_cascade_packages: opt-out from cascade reach

Compute:
  - cascade_closure: BFS *upstream* (reverse direction) from directly_changed
    via the graph's reverse edges. Includes directly_changed itself.
  - apps_to_qa: the apps in cascade_closure that are reachable through at
    least one path that does NOT pass exclusively through no_cascade nodes
    (using the directly_changed nodes themselves as path origins). Apps in
    directly_changed are unconditionally included.

Why "removing no_cascade" instead of just subtracting them: the spec says
"changes to no_cascade packages do NOT expand the affected set" — that's a
reach property, not a membership property. We achieve it by computing a
SECOND closure where every no_cascade package contributes no upstream
expansion, and intersecting that closure with the apps set.
"""

from __future__ import annotations

from collections import deque

from embry0.workspace_providers.npm_workspaces_turbo._dep_graph import (
    DependencyGraph,
)
from embry0.workspace_providers.provider import AffectedSet


def compute_affected(
    *,
    directly_changed: frozenset[str],
    graph: DependencyGraph,
    apps: frozenset[str],
    no_cascade_packages: frozenset[str],
) -> AffectedSet:
    """Compute the AffectedSet from a diff + dep graph + no_cascade tags.

    Parameters
    ----------
    directly_changed
        Workspace package_names whose files appear in the diff. May contain
        names not in the graph (treated as leaves; included in directly_changed
        and cascade_closure but contribute no closure expansion).
    graph
        The workspace dep graph. Use `graph.consumers_of(node)` to walk
        upstream.
    apps
        The set of runnable workspace apps (package_names). Used to filter
        cascade_closure → apps_to_qa.
    no_cascade_packages
        Package_names tagged `no_cascade: true` in qa.yaml. Changes to these
        packages do not expand the apps_to_qa set through them.

    Returns
    -------
    AffectedSet
        With invariants:
          - directly_changed ⊆ cascade_closure
          - apps_to_qa ⊆ cascade_closure
          - apps_to_qa ⊆ apps
    """
    # 1. Full closure (no_cascade ignored) — used for reporting cascade_closure.
    cascade = _bfs_upstream(graph, sources=directly_changed)

    # 2. Restricted closure for apps_to_qa.
    # Drop no_cascade nodes from the BFS sources unless they're directly_changed
    # apps (an app being itself tagged no_cascade would be a config error; treat
    # it as the app being unreachable via cascade BUT still self-included if
    # directly_changed and an app).
    cascade_no_nc = _bfs_upstream(
        graph,
        sources=directly_changed,
        block=no_cascade_packages,
    )

    apps_to_qa = (cascade_no_nc & apps) | (directly_changed & apps)

    return AffectedSet(
        directly_changed=frozenset(directly_changed),
        cascade_closure=cascade,
        apps_to_qa=apps_to_qa,
    )


def _bfs_upstream(
    graph: DependencyGraph,
    *,
    sources: frozenset[str],
    block: frozenset[str] = frozenset(),
) -> frozenset[str]:
    """Reverse-edge BFS from `sources`, treating each node in `block` as a
    sink — included if it's a source itself, but never expanded.

    Returns the set of all reached nodes, including the sources themselves.
    Unknown source names (not in the graph) are kept as leaves but contribute
    no further expansion.
    """
    visited: set[str] = set()
    queue: deque[str] = deque()
    for s in sources:
        if s not in visited:
            visited.add(s)
            queue.append(s)

    while queue:
        node = queue.popleft()
        if node in block:
            # Node is a sink: visited (so it's part of the closure if it was
            # a source) but its consumers are NOT walked.
            continue
        for consumer in graph.consumers_of(node):
            if consumer not in visited:
                visited.add(consumer)
                queue.append(consumer)

    return frozenset(visited)
