from pathlib import Path

import pytest

from athanor.workflows.qa.orchestrator import (
    OrchestratorContext,
    OrchestratorOutcome,
    resolve_apps_to_qa,
    validate_against_qa_config,
)
from athanor.workflows.qa.qa_yaml_v2 import parse_qa_yaml_v2
from athanor.workspace_providers import (
    AffectedSet,
    WorkspaceApp,
    WorkspacePackage,
)
from athanor.workspace_providers.fakes import FakeWorkspaceProvider


_QA_YAML = """
version: 2
workspace_provider:
  type: fake
defaults:
  mode: process
  sandbox_profile: slim
  ready_checks:
    - http: "http://localhost:3000"
apps:
  hub:
    boot_command: "x"
    frontend_url: "http://localhost:3000"
  companion:
    boot_command: "y"
    frontend_url: "http://localhost:3001"
packages:
  "@x/types":
    no_cascade: true
"""


def test_resolve_apps_to_qa_uses_provider_and_intersects_with_qa_yaml():
    cfg = parse_qa_yaml_v2(_QA_YAML)
    provider = FakeWorkspaceProvider(
        apps=[
            WorkspaceApp("hub", Path("apps/hub"), "@x/hub"),
            WorkspaceApp("companion", Path("apps/companion"), "@x/companion"),
        ],
        packages=[
            WorkspacePackage("@x/hub", Path("apps/hub"), is_app=True),
            WorkspacePackage("@x/companion", Path("apps/companion"), is_app=True),
        ],
        affected_result=AffectedSet(
            directly_changed=frozenset({"@x/hub"}),
            cascade_closure=frozenset({"@x/hub"}),
            apps_to_qa=frozenset({"@x/hub"}),
        ),
    )
    apps = resolve_apps_to_qa(provider, cfg, changed_files=[Path("apps/hub/app/page.tsx")])
    assert apps == ["hub"]


def test_resolve_apps_to_qa_passes_no_cascade_packages_to_provider():
    cfg = parse_qa_yaml_v2(_QA_YAML)
    provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(frozenset(), frozenset(), frozenset({"@x/hub"})),
    )
    resolve_apps_to_qa(provider, cfg, changed_files=[])
    assert provider.affected_calls[0].no_cascade_packages == frozenset({"@x/types"})


def test_resolve_apps_to_qa_filters_out_apps_not_in_qa_yaml():
    """Provider may know about an app the user hasn't declared in qa.yaml.
    Such apps are silently skipped (they appear as warnings via validate)."""
    cfg = parse_qa_yaml_v2(_QA_YAML)
    provider = FakeWorkspaceProvider(
        apps=[
            WorkspaceApp("hub", Path("apps/hub"), "@x/hub"),
            WorkspaceApp("ghost", Path("apps/ghost"), "@x/ghost"),
        ],
        packages=[],
        affected_result=AffectedSet(
            frozenset(), frozenset(), frozenset({"@x/hub", "@x/ghost"})
        ),
    )
    apps = resolve_apps_to_qa(provider, cfg, changed_files=[])
    assert apps == ["hub"]  # ghost dropped


def test_validate_against_qa_config_fails_fast_on_unknown_app():
    cfg = parse_qa_yaml_v2(_QA_YAML)
    provider = FakeWorkspaceProvider(
        apps=[WorkspaceApp("hub", Path("apps/hub"), "@x/hub")],
        packages=[],
        affected_result=AffectedSet(frozenset(), frozenset(), frozenset()),
        validate_warnings=["error: app 'companion' declared in qa.yaml apps: but not found in workspace"],
    )
    errors, warnings = validate_against_qa_config(provider, cfg)
    assert any("companion" in e for e in errors)
    assert warnings == []


def test_validate_against_qa_config_separates_warnings_from_errors():
    cfg = parse_qa_yaml_v2(_QA_YAML)
    provider = FakeWorkspaceProvider(
        apps=[],
        packages=[],
        affected_result=AffectedSet(frozenset(), frozenset(), frozenset()),
        validate_warnings=[
            "warning: app 'lane' present in workspace but missing from qa.yaml apps:",
            "error: app 'hub' declared in qa.yaml apps: but not found in workspace",
        ],
    )
    errors, warnings = validate_against_qa_config(provider, cfg)
    assert len(errors) == 1
    assert len(warnings) == 1
