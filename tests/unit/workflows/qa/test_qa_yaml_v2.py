import pytest
from pydantic import ValidationError

from athanor.workflows.qa.qa_yaml_v2 import (
    AppLocalConfig,
    QAYamlConfigV2,
    parse_qa_yaml_v2,
    parse_app_local_yaml,
)

MINIMAL_V2 = """
version: 2
workspace_provider:
  type: npm-workspaces-turbo
defaults:
  mode: process
  sandbox_profile: slim
  ready_checks:
    - http: "http://localhost:3000"
apps:
  hub:
    boot_command: "npm run start"
    frontend_url: "http://localhost:3000"
"""


def test_minimal_v2_parses():
    cfg = parse_qa_yaml_v2(MINIMAL_V2)
    assert cfg.version == 2
    assert cfg.workspace_provider.type == "npm-workspaces-turbo"
    assert cfg.defaults.mode == "process"
    assert "hub" in cfg.apps
    assert cfg.apps["hub"].frontend_url == "http://localhost:3000"


def test_version_must_be_2():
    with pytest.raises(ValidationError) as exc:
        parse_qa_yaml_v2("version: 1\nworkspace_provider:\n  type: x\ndefaults:\n  mode: process\n  sandbox_profile: slim\n  ready_checks: [{http: 'http://x'}]\napps: {}")
    assert "version" in str(exc.value).lower()


def test_extra_top_level_field_rejected():
    bad = MINIMAL_V2 + "\nunknown_field: 1\n"
    with pytest.raises(ValidationError):
        parse_qa_yaml_v2(bad)


def test_apps_can_be_empty_at_parse_time():
    """Parser does not require non-empty apps — orchestrator will skip QA
    cleanly when apps_to_qa is empty. Allowing parse with empty apps lets
    a fresh repo land qa.yaml before declaring any apps."""
    raw = (
        "version: 2\n"
        "workspace_provider:\n  type: npm-workspaces-turbo\n"
        "defaults:\n  mode: process\n  sandbox_profile: slim\n"
        "  ready_checks: [{http: 'http://x'}]\n"
        "apps: {}\n"
    )
    cfg = parse_qa_yaml_v2(raw)
    assert cfg.apps == {}


def test_packages_no_cascade_default_false():
    raw = MINIMAL_V2 + 'packages:\n  "@x/types": {}\n'
    cfg = parse_qa_yaml_v2(raw)
    assert cfg.packages["@x/types"].no_cascade is False


def test_packages_no_cascade_true():
    raw = MINIMAL_V2 + 'packages:\n  "@x/types":\n    no_cascade: true\n'
    cfg = parse_qa_yaml_v2(raw)
    assert cfg.packages["@x/types"].no_cascade is True


def test_parallelism_defaults_apply():
    cfg = parse_qa_yaml_v2(MINIMAL_V2)
    assert cfg.parallelism.max_concurrent_apps == 4


def test_parallelism_explicit_value():
    raw = MINIMAL_V2 + "parallelism:\n  max_concurrent_apps: 7\n"
    cfg = parse_qa_yaml_v2(raw)
    assert cfg.parallelism.max_concurrent_apps == 7


def test_qa_required_default_auto():
    cfg = parse_qa_yaml_v2(MINIMAL_V2)
    assert cfg.qa_required == "auto"


def test_qa_required_invalid_value():
    raw = MINIMAL_V2 + "qa_required: maybe\n"
    with pytest.raises(ValidationError):
        parse_qa_yaml_v2(raw)


def test_app_local_file_parses_with_sandbox_profile_override():
    raw = "sandbox_profile: qa-jvm\n"
    cfg = parse_app_local_yaml(raw)
    assert cfg.sandbox_profile == "qa-jvm"
    assert cfg.acceptance_criteria is None
    assert cfg.e2e is None


def test_app_local_file_acceptance_criteria_replaces_template():
    raw = (
        "acceptance_criteria:\n"
        "  - 'customer list renders'\n"
        "  - 'no 4xx requests'\n"
    )
    cfg = parse_app_local_yaml(raw)
    assert cfg.acceptance_criteria == ["customer list renders", "no 4xx requests"]


def test_app_local_file_extra_field_rejected():
    with pytest.raises(ValidationError):
        parse_app_local_yaml("unknown_field: 1\n")


def test_yaml_root_must_be_mapping():
    with pytest.raises(ValueError):
        parse_qa_yaml_v2("- 1\n- 2\n")


# ---------------------------------------------------------------------------
# QAReadyCheck.expect_status: int | list[int] (2026-05-06)
# ---------------------------------------------------------------------------


def _yaml_with_ready_check(rc_yaml: str) -> str:
    return f"""
version: 2
workspace_provider:
  type: npm-workspaces-turbo
defaults:
  mode: process
  ready_checks:
    - http: "http://localhost:3000/"
{rc_yaml}
apps:
  hub:
    boot_command: "x"
    frontend_url: "http://localhost:3000/"
"""


def test_ready_check_expect_status_legacy_int_form():
    """Single-int form must still parse and produce a list with one entry."""
    cfg = parse_qa_yaml_v2(_yaml_with_ready_check("      expect_status: 200"))
    rc = cfg.defaults.ready_checks[0]
    assert rc.expect_status == 200
    assert rc.status_codes() == [200]


def test_ready_check_expect_status_list_form_for_auth_gated_apps():
    """List form lets auth-gated apps mark 401/403 as 'dev server is up'."""
    cfg = parse_qa_yaml_v2(_yaml_with_ready_check("      expect_status: [200, 401, 403]"))
    rc = cfg.defaults.ready_checks[0]
    assert rc.expect_status == [200, 401, 403]
    assert rc.status_codes() == [200, 401, 403]


def test_ready_check_expect_status_list_must_be_non_empty():
    with pytest.raises(ValidationError):
        parse_qa_yaml_v2(_yaml_with_ready_check("      expect_status: []"))


def test_ready_check_expect_status_out_of_range_rejected():
    with pytest.raises(ValidationError):
        parse_qa_yaml_v2(_yaml_with_ready_check("      expect_status: [200, 700]"))


def test_ready_check_expect_status_default_is_200():
    cfg = parse_qa_yaml_v2(_yaml_with_ready_check(""))  # no expect_status
    assert cfg.defaults.ready_checks[0].expect_status == 200
