import pytest

from athanor.workflows.qa.qa_yaml_resolve import (
    resolve_app_config,
)
from athanor.workflows.qa.qa_yaml_v2 import (
    AppEntry,
    DefaultsBlock,
    QAReadyCheck,
    QAYamlConfigV2,
    parse_app_local_yaml,
    parse_qa_yaml_v2,
)

_ROOT_YAML = """
version: 2
workspace_provider:
  type: npm-workspaces-turbo
defaults:
  mode: process
  sandbox_profile: slim
  ready_checks:
    - http: "http://localhost:${port}"
      expect_status: 200
  boot_timeout_seconds: 600
  acceptance_criteria_template:
    - "page loads"
    - "primary nav reachable"
apps:
  hub:
    boot_command: "PORT=3000 npm run start"
    frontend_url: "http://localhost:3000"
  companion:
    boot_command: "PORT=3001 npm run start"
    frontend_url: "http://localhost:3001"
    sandbox_profile: qa-jvm
    boot_timeout_seconds: 1200
"""


def test_resolve_app_inherits_defaults_when_no_overrides():
    cfg = parse_qa_yaml_v2(_ROOT_YAML)
    resolved = resolve_app_config("hub", cfg, app_local=None)
    assert resolved.sandbox_profile == "slim"
    assert resolved.boot_timeout_seconds == 600
    assert resolved.acceptance_criteria == ["page loads", "primary nav reachable"]
    assert resolved.boot_command == "PORT=3000 npm run start"
    assert resolved.frontend_url == "http://localhost:3000"


def test_root_app_entry_overrides_defaults():
    cfg = parse_qa_yaml_v2(_ROOT_YAML)
    resolved = resolve_app_config("companion", cfg, app_local=None)
    assert resolved.sandbox_profile == "qa-jvm"
    assert resolved.boot_timeout_seconds == 1200


def test_app_local_overrides_root_app_entry_and_defaults():
    cfg = parse_qa_yaml_v2(_ROOT_YAML)
    local = parse_app_local_yaml("sandbox_profile: super-jvm\nboot_timeout_seconds: 2400\n")
    resolved = resolve_app_config("companion", cfg, app_local=local)
    assert resolved.sandbox_profile == "super-jvm"
    assert resolved.boot_timeout_seconds == 2400


def test_app_local_acceptance_criteria_replaces_template():
    cfg = parse_qa_yaml_v2(_ROOT_YAML)
    local = parse_app_local_yaml("acceptance_criteria:\n  - 'specific A'\n  - 'specific B'\n")
    resolved = resolve_app_config("hub", cfg, app_local=local)
    assert resolved.acceptance_criteria == ["specific A", "specific B"]


def test_app_local_omitted_acceptance_criteria_keeps_template():
    cfg = parse_qa_yaml_v2(_ROOT_YAML)
    local = parse_app_local_yaml("sandbox_profile: x\n")
    resolved = resolve_app_config("hub", cfg, app_local=local)
    assert resolved.acceptance_criteria == ["page loads", "primary nav reachable"]


def test_resolve_unknown_app_raises():
    cfg = parse_qa_yaml_v2(_ROOT_YAML)
    with pytest.raises(KeyError) as exc:
        resolve_app_config("ghost", cfg, app_local=None)
    assert "ghost" in str(exc.value)


def test_ready_checks_use_root_defaults_when_app_does_not_override():
    cfg = parse_qa_yaml_v2(_ROOT_YAML)
    resolved = resolve_app_config("hub", cfg, app_local=None)
    assert len(resolved.ready_checks) == 1
    assert resolved.ready_checks[0].http == "http://localhost:${port}"


def test_app_local_ready_checks_replace_defaults_entirely():
    cfg = parse_qa_yaml_v2(_ROOT_YAML)
    local = parse_app_local_yaml("ready_checks:\n  - http: 'http://localhost:9999/health'\n    expect_status: 204\n")
    resolved = resolve_app_config("hub", cfg, app_local=local)
    assert len(resolved.ready_checks) == 1
    assert resolved.ready_checks[0].http == "http://localhost:9999/health"
    assert resolved.ready_checks[0].expect_status == 204


def test_resolved_is_immutable():
    cfg = parse_qa_yaml_v2(_ROOT_YAML)
    resolved = resolve_app_config("hub", cfg, app_local=None)
    with pytest.raises(Exception):
        resolved.sandbox_profile = "other"  # type: ignore[misc]


def test_app_can_disable_ready_checks_with_explicit_empty_list():
    """Regression for ultrareview bug_015. An explicit `ready_checks: []`
    at the app level must opt out of inherited defaults, not collapse
    to defaults via the truthy `or` chain."""
    from athanor.workflows.qa.qa_yaml_v2 import (
        WorkspaceProviderRef,
    )

    cfg = QAYamlConfigV2(
        version=2,
        workspace_provider=WorkspaceProviderRef(type="fake"),
        defaults=DefaultsBlock(
            mode="process",
            sandbox_profile="slim",
            ready_checks=[QAReadyCheck(http="http://localhost:3000")],
            boot_timeout_seconds=600,
        ),
        apps={
            "worker": AppEntry(
                boot_command="python worker.py",
                frontend_url="http://localhost:0",
                ready_checks=[],  # explicit opt-out
            )
        },
    )

    resolved = resolve_app_config("worker", cfg, app_local=None)
    assert resolved.ready_checks == []
