from pathlib import Path

import pytest

from athanor.workspace_providers.npm_workspaces_turbo.provider import (
    NpmWorkspacesTurboProvider,
)


@pytest.fixture
def toy_repo() -> Path:
    return Path(__file__).resolve().parents[2] / "fixtures" / "toy-monorepo"


def test_validate_returns_empty_when_qa_apps_match_workspace(toy_repo: Path):
    provider = NpmWorkspacesTurboProvider(toy_repo, {})
    warnings = provider.validate(["hub", "companion", "lane"])
    assert warnings == []


def test_validate_flags_unknown_app_as_error(toy_repo: Path):
    provider = NpmWorkspacesTurboProvider(toy_repo, {})
    warnings = provider.validate(["hub", "ghost"])
    assert any("ghost" in w and "not found" in w for w in warnings)


def test_validate_warns_when_workspace_app_missing_from_qa_config(toy_repo: Path):
    """An app in the workspace but absent from qa.yaml is a warning, not an
    error — athanor will silently skip it. Surface so the user notices."""
    provider = NpmWorkspacesTurboProvider(toy_repo, {})
    warnings = provider.validate(["hub", "companion"])  # `lane` missing
    assert any("lane" in w and "qa.yaml" in w for w in warnings)


def test_validate_distinguishes_errors_from_warnings_via_prefix(toy_repo: Path):
    """Strings starting with 'error:' are blocking; 'warning:' is informational."""
    provider = NpmWorkspacesTurboProvider(toy_repo, {})
    warnings = provider.validate(["hub", "ghost"])
    assert any(w.lower().startswith("error:") for w in warnings)


def test_validate_empty_qa_config_warns_about_every_app(toy_repo: Path):
    provider = NpmWorkspacesTurboProvider(toy_repo, {})
    warnings = provider.validate([])
    # 3 warnings — one per workspace app missing from config
    assert sum(1 for w in warnings if w.lower().startswith("warning:")) == 3
