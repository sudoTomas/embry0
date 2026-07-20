import pytest

from embry0.workflows.qa.qa_yaml_resolve import (
    resolve_app_config,
)
from embry0.workflows.qa.qa_yaml_v2 import (
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
    from embry0.workflows.qa.qa_yaml_v2 import (
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


# -------- target: deployed (EMB-27) --------

_DEPLOYED_ROOT_YAML = """
version: 2
qa_required: always
defaults:
  mode: dind
  sandbox_profile: qa-external
  seed_command: "make seed"
apps:
  web:
    target: deployed
    frontend_url: "http://app.internal.example:8080/"
    ready_checks:
      - http: "http://app.internal.example:8080/health"
        expect_status: [200, 302]
"""


def test_deployed_resolves_target_and_null_boot_command():
    cfg = parse_qa_yaml_v2(_DEPLOYED_ROOT_YAML)
    resolved = resolve_app_config("web", cfg, app_local=None)
    assert resolved.target == "deployed"
    assert resolved.boot_command is None


def test_deployed_forces_process_mode():
    """defaults.mode: dind must not leak into a deployed app — nothing boots
    in-sandbox, so a per-subtask qa-net would be pure waste."""
    cfg = parse_qa_yaml_v2(_DEPLOYED_ROOT_YAML)
    resolved = resolve_app_config("web", cfg, app_local=None)
    assert resolved.mode == "process"


def test_deployed_clears_defaults_level_seed_command():
    cfg = parse_qa_yaml_v2(_DEPLOYED_ROOT_YAML)
    resolved = resolve_app_config("web", cfg, app_local=None)
    assert resolved.seed_command is None


def test_deployed_with_empty_merged_ready_checks_raises():
    raw = _DEPLOYED_ROOT_YAML.replace(
        """    ready_checks:
      - http: "http://app.internal.example:8080/health"
        expect_status: [200, 302]
""",
        "    ready_checks: []\n",
    )
    cfg = parse_qa_yaml_v2(raw)
    with pytest.raises(ValueError, match="no ready_checks"):
        resolve_app_config("web", cfg, app_local=None)


def test_managed_resolution_unchanged_by_target_field():
    cfg = parse_qa_yaml_v2(_ROOT_YAML)
    resolved = resolve_app_config("hub", cfg, app_local=None)
    assert resolved.target == "managed"
    assert resolved.boot_command == "PORT=3000 npm run start"
    assert resolved.mode == "process"


# -------- auth: storage_state_from merge (EMB-40) --------


def test_auth_none_when_undeclared():
    cfg = parse_qa_yaml_v2(_ROOT_YAML)
    resolved = resolve_app_config("hub", cfg, app_local=None)
    assert resolved.auth is None


def test_auth_inherited_from_defaults():
    raw = _ROOT_YAML.replace(
        "defaults:\n",
        "defaults:\n  auth:\n    storage_state_from:\n      command: 'node scripts/qa-login.mjs'\n",
    )
    cfg = parse_qa_yaml_v2(raw)
    resolved = resolve_app_config("hub", cfg, app_local=None)
    assert resolved.auth is not None
    assert resolved.auth.storage_state_from.command == "node scripts/qa-login.mjs"


def test_auth_app_entry_overrides_defaults():
    raw = _ROOT_YAML.replace(
        "defaults:\n",
        "defaults:\n  auth:\n    storage_state_from:\n      command: 'node scripts/qa-login.mjs'\n",
    ).replace(
        "  hub:\n",
        "  hub:\n    auth:\n      storage_state_from:\n        secret: QA_HUB_STORAGE_STATE\n",
    )
    cfg = parse_qa_yaml_v2(raw)
    resolved = resolve_app_config("hub", cfg, app_local=None)
    assert resolved.auth.storage_state_from.secret == "QA_HUB_STORAGE_STATE"
    assert resolved.auth.storage_state_from.command is None
    # companion has no app-level auth — inherits the defaults-level one
    companion = resolve_app_config("companion", cfg, app_local=None)
    assert companion.auth.storage_state_from.command == "node scripts/qa-login.mjs"


def test_auth_app_local_overrides_all():
    raw = _ROOT_YAML.replace(
        "  hub:\n",
        "  hub:\n    auth:\n      storage_state_from:\n        secret: QA_HUB_STORAGE_STATE\n",
    )
    cfg = parse_qa_yaml_v2(raw)
    local = parse_app_local_yaml("auth:\n  storage_state_from:\n    secret: QA_LOCAL_STORAGE_STATE\n")
    resolved = resolve_app_config("hub", cfg, app_local=local)
    assert resolved.auth.storage_state_from.secret == "QA_LOCAL_STORAGE_STATE"


def test_deployed_keeps_auth():
    """Unlike seed_command, auth survives the deployed-target branch — the
    login/session-injection step is the whole point for deployed apps."""
    raw = _DEPLOYED_ROOT_YAML.replace(
        "    target: deployed\n",
        "    target: deployed\n    auth:\n      storage_state_from:\n        secret: QA_STORAGE_STATE\n",
    )
    cfg = parse_qa_yaml_v2(raw)
    resolved = resolve_app_config("web", cfg, app_local=None)
    assert resolved.seed_command is None
    assert resolved.auth is not None
    assert resolved.auth.storage_state_from.secret == "QA_STORAGE_STATE"


def test_managed_app_empty_ready_checks_by_omission_raises():
    """EMB-34: a managed app with no ready_checks anywhere is a config
    error — boot would pass with zero verification."""
    root = parse_qa_yaml_v2(
        """
version: 2
workspace_provider:
  type: npm-workspaces-turbo
defaults:
  mode: process
  boot_timeout_seconds: 60
apps:
  web:
    boot_command: "npm start"
    frontend_url: "http://localhost:3000"
"""
    )
    with pytest.raises(ValueError, match="no ready_checks after merge"):
        resolve_app_config("web", root, app_local=None)


def test_managed_app_explicit_empty_ready_checks_is_allowed():
    """An explicit `ready_checks: []` is an acknowledged opt-out."""
    root = parse_qa_yaml_v2(
        """
version: 2
workspace_provider:
  type: npm-workspaces-turbo
defaults:
  mode: process
  boot_timeout_seconds: 60
apps:
  web:
    boot_command: "npm start"
    frontend_url: "http://localhost:3000"
    ready_checks: []
"""
    )
    resolved = resolve_app_config("web", root, app_local=None)
    assert resolved.ready_checks == []


def test_managed_app_defaults_ready_checks_still_flow():
    root = parse_qa_yaml_v2(
        """
version: 2
workspace_provider:
  type: npm-workspaces-turbo
defaults:
  mode: process
  boot_timeout_seconds: 60
  ready_checks:
    - http: "http://localhost:3000/"
      expect_status: 200
apps:
  web:
    boot_command: "npm start"
    frontend_url: "http://localhost:3000"
"""
    )
    resolved = resolve_app_config("web", root, app_local=None)
    assert len(resolved.ready_checks) == 1
