"""Test doubles for workspace_provider.

`FakeWorkspaceProvider` lets unit tests for the orchestrator and downstream
code exercise the provider contract without filesystem or shell-out cost.
Records call args so tests can assert about *how* it was called.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from embry0.workspace_providers.provider import (
    AffectedSet,
    WorkspaceApp,
    WorkspacePackage,
)


@dataclass(frozen=True, slots=True)
class FakeAffectedCall:
    changed_files: list[Path]
    no_cascade_packages: frozenset[str]


@dataclass
class FakeWorkspaceProvider:
    """Records calls; returns canned results.

    Construct with concrete `apps`, `packages`, and `affected_result`. The
    `validate_warnings` list is returned by `validate()` regardless of input.
    """

    apps: list[WorkspaceApp]
    packages: list[WorkspacePackage]
    affected_result: AffectedSet
    validate_warnings: list[str] = field(default_factory=list)
    name: str = "fake"

    affected_calls: list[FakeAffectedCall] = field(default_factory=list, init=False, repr=False)
    discover_call_count: int = field(default=0, init=False, repr=False)
    validate_calls: list[list[str]] = field(default_factory=list, init=False, repr=False)

    def __init__(
        self,
        apps: list[WorkspaceApp] | None = None,
        packages: list[WorkspacePackage] | None = None,
        affected_result: AffectedSet | None = None,
        validate_warnings: list[str] | None = None,
        repo_root: Path | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        # Mirror the WorkspaceProvider __init__ signature so registry-loaded
        # tests can drop the fake in transparently.
        self.apps = apps or []
        self.packages = packages or []
        self.affected_result = affected_result or AffectedSet(frozenset(), frozenset(), frozenset())
        self.validate_warnings = validate_warnings or []
        self.name = "fake"
        self.affected_calls = []
        self.discover_call_count = 0
        self.validate_calls = []

    def discover(self) -> tuple[list[WorkspaceApp], list[WorkspacePackage]]:
        self.discover_call_count += 1
        return (list(self.apps), list(self.packages))

    def affected(
        self,
        changed_files: list[Path],
        no_cascade_packages: frozenset[str],
    ) -> AffectedSet:
        self.affected_calls.append(
            FakeAffectedCall(
                changed_files=list(changed_files),
                no_cascade_packages=frozenset(no_cascade_packages),
            )
        )
        return self.affected_result

    def validate(self, apps_declared_in_qa_config: list[str]) -> list[str]:
        self.validate_calls.append(list(apps_declared_in_qa_config))
        return list(self.validate_warnings)
