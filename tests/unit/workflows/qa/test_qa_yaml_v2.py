import pytest
from pydantic import ValidationError

from embry0.workflows.qa.qa_yaml_v2 import (
    parse_app_local_yaml,
    parse_qa_yaml_v2,
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
        parse_qa_yaml_v2(
            "version: 1\nworkspace_provider:\n  type: x\ndefaults:\n  mode: process\n  sandbox_profile: slim\n  ready_checks: [{http: 'http://x'}]\napps: {}"
        )
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
    raw = "acceptance_criteria:\n  - 'customer list renders'\n  - 'no 4xx requests'\n"
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


# -------- target: deployed (EMB-27) --------

DEPLOYED_V2 = """
version: 2
qa_required: always
defaults:
  sandbox_profile: qa-external
apps:
  web:
    target: deployed
    frontend_url: "http://app.internal.example:8080/"
    ready_checks:
      - http: "http://app.internal.example:8080/health"
        expect_status: [200, 302, 401]
"""


def test_deployed_app_parses_without_provider_or_boot_command():
    cfg = parse_qa_yaml_v2(DEPLOYED_V2)
    assert cfg.workspace_provider is None
    assert cfg.apps["web"].target == "deployed"
    assert cfg.apps["web"].boot_command is None
    assert cfg.deployed_app_names() == ["web"]
    assert cfg.managed_app_names() == []


def test_target_defaults_to_managed():
    cfg = parse_qa_yaml_v2(MINIMAL_V2)
    assert cfg.apps["hub"].target == "managed"


def test_managed_app_requires_boot_command():
    bad = MINIMAL_V2.replace('    boot_command: "npm run start"\n', "")
    with pytest.raises(ValidationError) as exc:
        parse_qa_yaml_v2(bad)
    assert "boot_command is required" in str(exc.value)


def test_deployed_app_rejects_boot_command():
    bad = DEPLOYED_V2.replace("    target: deployed\n", '    target: deployed\n    boot_command: "npm start"\n')
    with pytest.raises(ValidationError) as exc:
        parse_qa_yaml_v2(bad)
    assert "boot_command must not be set" in str(exc.value)


def test_deployed_app_rejects_seed_command():
    bad = DEPLOYED_V2.replace("    target: deployed\n", '    target: deployed\n    seed_command: "make seed"\n')
    with pytest.raises(ValidationError) as exc:
        parse_qa_yaml_v2(bad)
    assert "seed_command must not be set" in str(exc.value)


def test_deployed_app_rejects_loopback_frontend_url():
    bad = DEPLOYED_V2.replace("http://app.internal.example:8080/", "http://localhost:8080/")
    with pytest.raises(ValidationError) as exc:
        parse_qa_yaml_v2(bad)
    assert "loopback" in str(exc.value)


def test_deployed_app_rejects_loopback_ready_check():
    bad = DEPLOYED_V2.replace(
        'http: "http://app.internal.example:8080/health"',
        'http: "http://127.0.0.1:8080/health"',
    )
    with pytest.raises(ValidationError) as exc:
        parse_qa_yaml_v2(bad)
    assert "loopback" in str(exc.value)


def test_provider_omission_rejected_when_any_app_is_managed():
    bad = MINIMAL_V2.replace("workspace_provider:\n  type: npm-workspaces-turbo\n", "")
    with pytest.raises(ValidationError) as exc:
        parse_qa_yaml_v2(bad)
    assert "workspace_provider is required" in str(exc.value)


def test_provider_omission_rejected_when_no_apps():
    raw = "version: 2\ndefaults:\n  sandbox_profile: slim\napps: {}\n"
    with pytest.raises(ValidationError) as exc:
        parse_qa_yaml_v2(raw)
    assert "workspace_provider is required" in str(exc.value)


def test_deployed_corpus_fixture_parses():
    from pathlib import Path

    fixture = Path(__file__).parents[3] / "fixtures" / "qa-yaml-corpus" / "v2" / "deployed-target.yaml"
    cfg = parse_qa_yaml_v2(fixture.read_text())
    assert cfg.apps["web"].target == "deployed"


# -------- auth: storage_state_from (EMB-40) --------


def _deployed_with_auth(auth_yaml: str) -> str:
    return DEPLOYED_V2.replace(
        "    target: deployed\n",
        f"    target: deployed\n{auth_yaml}",
    )


def test_auth_secret_source_parses_on_app_entry():
    raw = _deployed_with_auth("    auth:\n      storage_state_from:\n        secret: QA_STORAGE_STATE\n")
    cfg = parse_qa_yaml_v2(raw)
    auth = cfg.apps["web"].auth
    assert auth is not None
    assert auth.storage_state_from.secret == "QA_STORAGE_STATE"
    assert auth.storage_state_from.command is None


def test_auth_command_source_parses_on_defaults():
    raw = DEPLOYED_V2.replace(
        "defaults:\n",
        "defaults:\n  auth:\n    storage_state_from:\n      command: 'node scripts/qa-login.mjs'\n",
    )
    cfg = parse_qa_yaml_v2(raw)
    auth = cfg.defaults.auth
    assert auth is not None
    assert auth.storage_state_from.command == "node scripts/qa-login.mjs"
    assert auth.storage_state_from.timeout_seconds == 300


def test_auth_requires_exactly_one_source_neither():
    raw = _deployed_with_auth("    auth:\n      storage_state_from: {}\n")
    with pytest.raises(ValidationError) as exc:
        parse_qa_yaml_v2(raw)
    assert "exactly one" in str(exc.value)


def test_auth_requires_exactly_one_source_both():
    raw = _deployed_with_auth(
        "    auth:\n      storage_state_from:\n        secret: QA_STORAGE_STATE\n        command: 'node login.mjs'\n"
    )
    with pytest.raises(ValidationError) as exc:
        parse_qa_yaml_v2(raw)
    assert "exactly one" in str(exc.value)


def test_auth_secret_must_have_qa_prefix():
    raw = _deployed_with_auth("    auth:\n      storage_state_from:\n        secret: STORAGE_STATE\n")
    with pytest.raises(ValidationError) as exc:
        parse_qa_yaml_v2(raw)
    assert "QA_" in str(exc.value)


def test_auth_secret_rejects_reserved_key():
    raw = _deployed_with_auth("    auth:\n      storage_state_from:\n        secret: QA_JOB_ID\n")
    with pytest.raises(ValidationError) as exc:
        parse_qa_yaml_v2(raw)
    assert "reserved" in str(exc.value)


def test_auth_secret_rejects_invalid_env_var_name():
    raw = _deployed_with_auth("    auth:\n      storage_state_from:\n        secret: 'QA_bad name'\n")
    with pytest.raises(ValidationError):
        parse_qa_yaml_v2(raw)


def test_auth_extra_field_rejected():
    raw = _deployed_with_auth(
        "    auth:\n      storage_state_from:\n        secret: QA_STORAGE_STATE\n      inject_into: [web]\n"
    )
    with pytest.raises(ValidationError):
        parse_qa_yaml_v2(raw)


def test_auth_allowed_on_app_local_file():
    cfg = parse_app_local_yaml("auth:\n  storage_state_from:\n    secret: QA_STORAGE_STATE\n")
    assert cfg.auth is not None
    assert cfg.auth.storage_state_from.secret == "QA_STORAGE_STATE"


def test_auth_corpus_fixture_parses():
    from pathlib import Path

    fixture = Path(__file__).parents[3] / "fixtures" / "qa-yaml-corpus" / "v2" / "deployed-with-auth.yaml"
    cfg = parse_qa_yaml_v2(fixture.read_text())
    auth = cfg.apps["web"].auth
    assert auth is not None
    assert auth.storage_state_from.secret == "QA_STORAGE_STATE"
