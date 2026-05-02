import pytest
from pydantic import ValidationError

from athanor.workflows.qa.qa_yaml import (
    QAE2E,  # noqa: F401  (symbol-export probe)
    QAReadyCheck,
    QASeed,  # noqa: F401  (symbol-export probe)
    QAStartup,
    QAYamlConfig,
    parse_qa_yaml,
)


def test_minimal_dind_config():
    cfg = QAYamlConfig(
        version=1,
        mode="dind",
        sandbox_profile="qa-jvm",
        startup=QAStartup(
            command="cd infra && docker compose up -d",
            ready_checks=[QAReadyCheck(http="http://gateway:8080/health", expect_status=200)],
            boot_timeout_seconds=180,
        ),
        frontend_url="http://frontend:3000",
    )
    assert cfg.mode == "dind"
    assert cfg.qa_required == "auto"
    assert cfg.seed is None
    assert cfg.e2e is None


def test_minimal_process_config():
    cfg = QAYamlConfig(
        version=1,
        mode="process",
        sandbox_profile="slim",
        startup=QAStartup(
            command="npm run dev",
            ready_checks=[QAReadyCheck(http="http://localhost:3000")],
            boot_timeout_seconds=60,
        ),
        frontend_url="http://localhost:3000",
    )
    assert cfg.mode == "process"


def test_unknown_version_rejected():
    with pytest.raises(ValidationError):
        QAYamlConfig(
            version=2,
            mode="dind",
            sandbox_profile="qa-jvm",
            startup=QAStartup(command="x", ready_checks=[QAReadyCheck(http="http://x")], boot_timeout_seconds=60),
            frontend_url="http://x",
        )


def test_unknown_mode_rejected():
    with pytest.raises(ValidationError):
        QAYamlConfig(
            version=1,
            mode="kubernetes",
            sandbox_profile="qa-jvm",
            startup=QAStartup(command="x", ready_checks=[QAReadyCheck(http="http://x")], boot_timeout_seconds=60),
            frontend_url="http://x",
        )


def test_qa_required_validation():
    with pytest.raises(ValidationError):
        QAYamlConfig(
            version=1,
            mode="dind",
            sandbox_profile="qa-jvm",
            startup=QAStartup(command="x", ready_checks=[QAReadyCheck(http="http://x")], boot_timeout_seconds=60),
            frontend_url="http://x",
            qa_required="sometimes",
        )


def test_parse_full_yaml():
    raw = """
version: 1
mode: dind
sandbox_profile: qa-jvm

startup:
  command: "cd infra && docker compose --profile dev up -d"
  ready_checks:
    - http: "http://macrolab-gateway:8080/actuator/health"
      expect_status: 200
      expect_body_regex: '"status":"UP"'
    - http: "http://macrolab-frontend-dev:3000/"
      expect_status: 200
  boot_timeout_seconds: 240

seed:
  command: "cd backend && ./scripts/seed-qa.sh"
  timeout_seconds: 120

frontend_url: "http://macrolab-frontend-dev:3000"

e2e:
  command: "cd frontend && npm run test:e2e"
  timeout_seconds: 900

acceptance_criteria_template:
  - "home page loads"

qa_required: auto
"""
    cfg = parse_qa_yaml(raw)
    assert cfg.mode == "dind"
    assert cfg.startup.boot_timeout_seconds == 240
    assert len(cfg.startup.ready_checks) == 2
    assert cfg.startup.ready_checks[0].expect_body_regex == '"status":"UP"'
    assert cfg.seed.command == "cd backend && ./scripts/seed-qa.sh"
    assert cfg.e2e.timeout_seconds == 900
    assert cfg.acceptance_criteria_template == ["home page loads"]


def test_parse_yaml_with_unknown_field_rejected():
    raw = """
version: 1
mode: dind
sandbox_profile: qa-jvm
startup:
  command: x
  ready_checks: [{http: "http://x"}]
  boot_timeout_seconds: 60
frontend_url: "http://x"
unexpected_field: oops
"""
    with pytest.raises(ValidationError):
        parse_qa_yaml(raw)


def test_ready_check_url_must_be_http():
    with pytest.raises(ValidationError):
        QAReadyCheck(http="ftp://x")
