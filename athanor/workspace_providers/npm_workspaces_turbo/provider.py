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
        # Stub for Task 7. Always-clean for now so discover tests pass.
        return []
