"""npm-workspaces discovery — read root package.json, resolve workspaces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from athanor.workspace_providers.provider import (
    WorkspaceApp,
    WorkspacePackage,
    WorkspaceProviderError,
)


def _read_package_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise WorkspaceProviderError(f"missing package.json at {path}")
    try:
        parsed: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return parsed
    except json.JSONDecodeError as exc:
        raise WorkspaceProviderError(f"invalid JSON in {path}: {exc}") from exc


def discover_workspaces(
    repo_root: Path,
    apps_glob: str,
    packages_glob: str,
) -> tuple[list[WorkspaceApp], list[WorkspacePackage]]:
    """Walk the workspace globs and read each child package.json.

    Apps are workspace members whose path matches `apps_glob`. Packages
    include both apps and libs — apps appear in the packages list with
    `is_app=True`.
    """
    root_pkg = _read_package_json(repo_root / "package.json")

    workspaces = root_pkg.get("workspaces", [])
    if isinstance(workspaces, dict):
        # npm 7+ supports {"packages": [...]} form
        workspaces = workspaces.get("packages", [])
    if not isinstance(workspaces, list):
        raise WorkspaceProviderError(
            f"root package.json `workspaces` must be a list (got {type(workspaces).__name__})"
        )

    apps: list[WorkspaceApp] = []
    packages: list[WorkspacePackage] = []

    apps_glob_dirs = sorted(repo_root.glob(apps_glob))
    packages_glob_dirs = sorted(repo_root.glob(packages_glob))

    apps_dirs = {p.resolve() for p in apps_glob_dirs if p.is_dir()}

    seen: set[str] = set()

    for ws_dir in sorted({*apps_glob_dirs, *packages_glob_dirs}, key=str):
        if not ws_dir.is_dir():
            continue
        pkg_json_path = ws_dir / "package.json"
        if not pkg_json_path.is_file():
            continue
        pkg = _read_package_json(pkg_json_path)
        package_name = pkg.get("name")
        if not isinstance(package_name, str):
            raise WorkspaceProviderError(
                f"workspace at {ws_dir.relative_to(repo_root)} has no `name` in package.json"
            )

        if package_name in seen:
            continue
        seen.add(package_name)

        rel_path = ws_dir.relative_to(repo_root)
        is_app = ws_dir.resolve() in apps_dirs

        if is_app:
            apps.append(
                WorkspaceApp(
                    name=ws_dir.name,
                    path=rel_path,
                    package_name=package_name,
                )
            )

        packages.append(
            WorkspacePackage(
                name=package_name,
                path=rel_path,
                is_app=is_app,
            )
        )

    return apps, packages
