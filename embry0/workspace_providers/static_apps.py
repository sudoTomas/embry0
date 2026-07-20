"""StaticAppsProvider — declared apps, no discovery (EMB-32).

For repos where workspace discovery doesn't map: mixed-language monorepos,
non-npm stacks, or repos whose runnable surfaces aren't workspace members.
The provider config declares the apps directly; the affected set is
computed by simple path-prefix matching (no dependency graph).

qa.yaml shape::

    workspace_provider:
      type: static-apps
      config:
        apps:
          hub:
            path: "apps/hub"          # optional, default "."
            prefixes: ["apps/hub/", "shared/"]
          leads: {}                   # no prefixes -> always affected

    # or the short list form (every app always affected):
    workspace_provider:
      type: static-apps
      config:
        apps: [hub, leads]

Semantics:
- ``discover()`` returns exactly the declared apps (each also as an
  is_app package, per the provider contract).
- ``affected()``: an app with ``prefixes`` is affected when any changed
  file starts with one of them; an app with NO prefixes is ALWAYS
  affected. No cascade — there is no dependency graph to close over, so
  ``no_cascade_packages`` is irrelevant by construction.
- ``validate()`` cross-checks qa.yaml ``apps:`` keys against the declared
  set.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from embry0.workspace_providers.provider import (
    AffectedSet,
    WorkspaceApp,
    WorkspacePackage,
)


class StaticAppEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = "."
    prefixes: list[str] = Field(default_factory=list)
    """Repo-root-relative path prefixes that mark this app affected.
    Empty means the app is always affected."""


class StaticAppsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    apps: dict[str, StaticAppEntry] = Field(default_factory=dict)

    @field_validator("apps", mode="before")
    @classmethod
    def _coerce_list_form(cls, v: Any) -> Any:
        """Accept the short list form ``apps: [hub, leads]``."""
        if isinstance(v, list):
            return {str(name): {} for name in v}
        return v


class StaticAppsProvider:
    name = "static-apps"

    def __init__(self, repo_root: Path, config: dict[str, Any]) -> None:
        self.repo_root = Path(repo_root)
        self.config = StaticAppsConfig.model_validate(config or {})

    def discover(self) -> tuple[list[WorkspaceApp], list[WorkspacePackage]]:
        apps = [
            WorkspaceApp(name=name, path=Path(entry.path), package_name=name)
            for name, entry in self.config.apps.items()
        ]
        packages = [WorkspacePackage(name=a.name, path=a.path, is_app=True) for a in apps]
        return apps, packages

    def affected(
        self,
        changed_files: list[Path],
        no_cascade_packages: frozenset[str],  # noqa: ARG002 — no dep graph, nothing cascades
    ) -> AffectedSet:
        changed = [str(p) for p in changed_files]
        hit: set[str] = set()
        for name, entry in self.config.apps.items():
            if not entry.prefixes:
                hit.add(name)
                continue
            if any(f.startswith(prefix) for f in changed for prefix in entry.prefixes):
                hit.add(name)
        frozen = frozenset(hit)
        return AffectedSet(
            directly_changed=frozen,
            cascade_closure=frozen,
            apps_to_qa=frozen,
        )

    def validate(self, apps_declared_in_qa_config: list[str]) -> list[str]:
        if not self.config.apps:
            return [
                "static-apps provider has no apps declared — set "
                "workspace_provider.config.apps to the runnable app names"
            ]
        declared = set(self.config.apps)
        return [
            f"qa.yaml app {name!r} is not declared in the static-apps provider config (declared: {sorted(declared)})"
            for name in apps_declared_in_qa_config
            if name not in declared
        ]
