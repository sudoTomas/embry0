"""NpmWorkspacesTurboProvider — first workspace_provider implementation."""

from __future__ import annotations

from pathlib import Path

from athanor.workspace_providers.npm_workspaces_turbo._discover import (
    discover_workspaces,
)
from athanor.workspace_providers.npm_workspaces_turbo.config import NpmTurboConfig
from athanor.workspace_providers.provider import (
    AffectedSet,
    WorkspaceApp,
    WorkspacePackage,
)


class NpmWorkspacesTurboProvider:
    """Reads npm `workspaces` from root package.json; uses turbo for affected().

    Phase 1: `affected()` returns ALL apps regardless of diff (Phase 3 wires
    in real diff filtering via `turbo run --filter`).
    """

    name = "npm-workspaces-turbo"

    def __init__(self, repo_root: Path, config: dict) -> None:
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
        # Phase-1 stub: every app is "affected". Phase 3 replaces this with
        # `turbo run build --dry=json --filter=...` and proper closure.
        apps, _ = self.discover()
        all_app_packages = frozenset(a.package_name for a in apps)
        return AffectedSet(
            directly_changed=all_app_packages,
            cascade_closure=all_app_packages,
            apps_to_qa=all_app_packages,
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

        # Errors: declared but not present.
        for missing in sorted(declared - workspace_app_names):
            messages.append(
                f"error: app {missing!r} declared in qa.yaml apps: but not found "
                f"in workspace (apps_glob={self.config.apps_glob!r})"
            )

        # Warnings: present but not declared. Athanor will not QA these.
        for orphan in sorted(workspace_app_names - declared):
            messages.append(
                f"warning: app {orphan!r} present in workspace but missing from "
                f"qa.yaml apps: — athanor will not QA it"
            )

        return messages
