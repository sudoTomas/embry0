"""NpmWorkspacesTurboProvider — first workspace_provider implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from athanor.workspace_providers.npm_workspaces_turbo._affected import (
    compute_affected,
)
from athanor.workspace_providers.npm_workspaces_turbo._dep_graph import (
    build_dep_graph,
)
from athanor.workspace_providers.npm_workspaces_turbo._discover import (
    discover_workspaces,
)
from athanor.workspace_providers.npm_workspaces_turbo._path_index import (
    build_path_index,
)
from athanor.workspace_providers.npm_workspaces_turbo.config import NpmTurboConfig
from athanor.workspace_providers.provider import (
    AffectedSet,
    WorkspaceApp,
    WorkspacePackage,
)


class NpmWorkspacesTurboProvider:
    """Reads npm `workspaces` from root package.json; computes affected set
    from the dep graph in pure Python.

    Phase 3: real diff-aware affected() (replaces the Phase-1 stub).
    No `npx turbo` invocation — see plans/2026-05-05-...-phase-3 §"Open spec
    questions resolved" for rationale.
    """

    name = "npm-workspaces-turbo"

    def __init__(self, repo_root: Path, config: dict[str, Any]) -> None:
        self.repo_root = Path(repo_root)
        self.config = NpmTurboConfig.model_validate(config or {})

    def discover(self) -> tuple[list[WorkspaceApp], list[WorkspacePackage]]:
        return discover_workspaces(
            self.repo_root,
            apps_glob=self.config.apps_glob,
            packages_glob=self.config.packages_glob,
        )

    def affected(
        self,
        changed_files: list[Path],
        no_cascade_packages: frozenset[str],
    ) -> AffectedSet:
        """Compute the affected set from a diff using the workspace dep graph.

        Phase 3 implementation — pure-Python closure with no_cascade
        subtraction. Reads every package.json under apps_glob/packages_glob
        from `self.repo_root` to build the graph; walks reverse edges from
        the diff-touched packages.
        """
        graph = build_dep_graph(
            repo_root=self.repo_root,
            apps_glob=self.config.apps_glob,
            packages_glob=self.config.packages_glob,
        )
        path_index = build_path_index(
            repo_root=self.repo_root,
            apps_glob=self.config.apps_glob,
            packages_glob=self.config.packages_glob,
        )

        # Translate file paths → owning package names. Files at the root
        # (or in unmatched directories) contribute nothing to the diff.
        directly_changed = path_index.owners_of_paths(list(changed_files))

        apps, _ = self.discover()
        all_app_packages = frozenset(a.package_name for a in apps)

        return compute_affected(
            directly_changed=directly_changed,
            graph=graph,
            apps=all_app_packages,
            no_cascade_packages=no_cascade_packages,
        )

    def validate(self, apps_declared_in_qa_config: list[str]) -> list[str]:
        """Cross-check qa.yaml `apps:` keys against the workspace.

        Returns mixed list:
          - "error: app 'X' declared in qa.yaml but not found in workspace"
          - "warning: app 'Y' present in workspace but missing from qa.yaml apps:"

        Callers distinguish severity by the lowercase prefix.
        """
        apps, _ = self.discover()
        workspace_app_names = {a.name for a in apps}
        declared = set(apps_declared_in_qa_config)

        messages: list[str] = []

        for missing in sorted(declared - workspace_app_names):
            messages.append(
                f"error: app {missing!r} declared in qa.yaml apps: but not found "
                f"in workspace (apps_glob={self.config.apps_glob!r})"
            )

        for orphan in sorted(workspace_app_names - declared):
            messages.append(
                f"warning: app {orphan!r} present in workspace but missing from qa.yaml apps: — athanor will not QA it"
            )

        return messages
