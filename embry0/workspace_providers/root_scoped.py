"""RootScopedProvider — run any workspace provider on a repo subdirectory.

EMB-32: some repos keep their workspace below the repo root (e.g.
ai-quoting's turbo workspace lives in ``frontend/`` inside a
Java+Python+TS monorepo). Declaring ``root:`` in the provider config::

    workspace_provider:
      type: npm-workspaces-turbo
      config:
        root: "frontend"
        apps_glob: "apps/*"

makes the orchestrator (a) construct the inner provider with
``repo_root/<root>`` and (b) wrap it in this class, which rebases the
repo-root-relative ``changed_files`` into workspace-relative paths for
``affected()``. Changed files OUTSIDE the root cannot belong to any
workspace package and are dropped from the mapping.

``root`` is stripped from the config before the inner provider sees it —
provider configs are strict (``extra="forbid"``) and must not need to
know about the wrapper.
"""

from __future__ import annotations

from pathlib import Path

from embry0.workspace_providers.provider import (
    AffectedSet,
    WorkspaceApp,
    WorkspacePackage,
    WorkspaceProvider,
)


class RootScopedProvider:
    def __init__(self, inner: WorkspaceProvider, root: str) -> None:
        self._inner = inner
        self._root = root.strip("/")
        self.name = inner.name

    def discover(self) -> tuple[list[WorkspaceApp], list[WorkspacePackage]]:
        return self._inner.discover()

    def affected(
        self,
        changed_files: list[Path],
        no_cascade_packages: frozenset[str],
    ) -> AffectedSet:
        prefix = f"{self._root}/"
        rebased = [Path(str(p)[len(prefix) :]) for p in changed_files if str(p).startswith(prefix)]
        return self._inner.affected(rebased, no_cascade_packages)

    def validate(self, apps_declared_in_qa_config: list[str]) -> list[str]:
        return self._inner.validate(apps_declared_in_qa_config)
