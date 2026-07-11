from pathlib import Path

from embry0.workspace_providers import (
    AffectedSet,
    WorkspaceApp,
    WorkspacePackage,
    WorkspaceProvider,
)


class _MinimalImpl:
    """Smallest possible implementation that satisfies the Protocol."""

    name = "minimal"

    def __init__(self, repo_root: Path, config: dict) -> None:
        self.repo_root = repo_root
        self.config = config

    def discover(self) -> tuple[list[WorkspaceApp], list[WorkspacePackage]]:
        return ([], [])

    def affected(
        self,
        changed_files: list[Path],
        no_cascade_packages: frozenset[str],
    ) -> AffectedSet:
        return AffectedSet(frozenset(), frozenset(), frozenset())

    def validate(self, apps_declared_in_qa_config: list[str]) -> list[str]:
        return []


def test_minimal_impl_satisfies_protocol():
    impl = _MinimalImpl(Path("/tmp/x"), {})
    assert isinstance(impl, WorkspaceProvider)


def test_class_missing_method_does_not_satisfy_protocol():
    class _Bad:
        name = "bad"

        def __init__(self, repo_root: Path, config: dict) -> None:
            pass

        # missing: discover, affected, validate

    assert not isinstance(_Bad(Path("/tmp/x"), {}), WorkspaceProvider)


def test_protocol_attribute_name_is_class_attribute():
    """Provider implementations must expose `name` as a class attribute,
    not a property — registry lookup is class-level."""
    assert _MinimalImpl.name == "minimal"
