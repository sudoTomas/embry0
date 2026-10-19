"""Near-miss detection for misspelled embry0 env vars (#248)."""

import os
import sys
from unittest.mock import patch

import structlog

from embry0.config import Embry0Config
from embry0.config_lint import declared_env_names, find_unknown_env_vars

DECLARED = declared_env_names(Embry0Config)


# Every key of .env.example, the orchestrator service's compose ``environment:``
# block, a container/shell baseline and the Claude Code CLI's own variables —
# the environment the orchestrator actually boots in. None of it is a typo.
_REPRESENTATIVE_ENV: dict[str, str] = dict.fromkeys(
    [
        # .env.example
        "ALLOWED_CORS_ORIGINS",
        "ANTHROPIC_API_KEY",
        "API_KEY",
        "CUSTOM_LLM_API_KEY",
        "CUSTOM_LLM_BASE_URL",
        "DEEPSEEK_API_KEY",
        "GEMINI_API_KEY",
        "GROQ_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_COMPATIBLE_PROVIDERS",
        "OPENROUTER_API_KEY",
        "API_RATE_LIMIT_PER_MINUTE",
        "ASK_USER_ROUNDS_CAP",
        "AUDIT_LOG_PATH",
        "AUDIT_RETENTION_DAYS",
        "AUTH_DEV_MODE",
        "AUTH_PROXY_ENABLED",
        "BUDGET_OVERRUN_MODE",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLOUDFLARED_TUNNEL_TOKEN",
        "CONTEXT_PROXY_CONTAINER_ALLOWLIST",
        "CONTEXT_PROXY_ENABLED",
        "CONTEXT_PROXY_QUERY_QUOTA",
        "DAILY_BUDGET_CAP_USD",
        "DASHBOARD_PUBLIC_URL",
        "DATABASE_URL",
        "DEFAULT_FORGE",
        "DEFAULT_MODEL",
        "DEVELOPER_LOG_TOOL_ENABLED",
        "DIND_CPUS",
        "DIND_MEMORY",
        "ENVIRONMENT_SECRET_KEY",
        "GITHUB_TOKEN",
        "GITHUB_WEBHOOK_SECRET",
        "IMAGE_REGISTRY",
        "MAX_BUDGET_USD",
        "MAX_WEBHOOK_BODY_BYTES",
        "MINIO_ROOT_PASSWORD",
        "MINIO_ROOT_USER",
        "MODEL_HEAVY",
        "MODEL_LIGHT",
        "MODEL_MEDIUM",
        "MONTHLY_BUDGET_CAP_USD",
        "ORCHESTRATOR_CPUS",
        "ORCHESTRATOR_MEMORY",
        "PAUSED_JOB_TTL_HOURS",
        "POSTGRES_DB",
        "POSTGRES_PASSWORD",
        "POSTGRES_USER",
        "PROD_PORT",
        "PROVIDER_MODE",
        "PROXY_ADMIN_TOKEN",
        "QA_ARTIFACT_RETENTION_DAYS",
        "RATE_LIMIT_PER_AUTHOR_PER_HOUR",
        "SANDBOX_IMAGE",
        "SLACK_WEBHOOK_URL",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "TELEGRAM_WEBHOOK_URL",
        "TRIGGER_LABELS",
        "TUNNEL_TOKEN",
        "TZ",
        "USER_RETRY_ROUNDS_CAP",
        "VITE_API_KEY",
        "WEBHOOK_DEV_MODE",
        # infra/docker-compose.yml, orchestrator service
        "DOCKER_CERT_PATH",
        "DOCKER_HOST",
        "DOCKER_TLS_VERIFY",
        "FORWARDED_ALLOW_IPS",
        "IMAGE_PINS_FILE",
        "REGISTRY_AUTH_DIR",
        # container / shell baseline
        "HOME",
        "HOSTNAME",
        "LANG",
        "PATH",
        "PWD",
        "PYTHONUNBUFFERED",
        "SHELL",
        "TERM",
        "USER",
        "VIRTUAL_ENV",
        # Claude Code CLI
        "ANTHROPIC_MODEL",
        "ANTHROPIC_SMALL_FAST_MODEL",
        "CLAUDE_CODE_ENTRYPOINT",
        "MAX_THINKING_TOKENS",
    ],
    "x",
)


def _names(environ: dict[str, str]) -> dict[str, str | None]:
    return {f.name: f.did_you_mean for f in find_unknown_env_vars(environ, DECLARED)}


# ── declared_env_names ───────────────────────────────────────────────────────


def test_declared_names_are_uppercase_field_names():
    assert "AUTH_DEV_MODE" in DECLARED
    assert "IMAGE_REGISTRY" in DECLARED
    assert "ENVIRONMENT_SECRET_KEYS_OLD" in DECLARED
    assert "auth_dev_mode" not in DECLARED


# ── find_unknown_env_vars ────────────────────────────────────────────────────


def test_near_miss_suggests_the_declared_name():
    assert _names({"AUTH_DEV_MOD": "true"}) == {"AUTH_DEV_MOD": "AUTH_DEV_MODE"}


def test_near_miss_on_image_registry():
    assert _names({"IMAGE_REGISTY": "registry:5000"}) == {"IMAGE_REGISTY": "IMAGE_REGISTRY"}


def test_declared_vars_produce_nothing():
    assert _names({"AUTH_DEV_MODE": "true", "IMAGE_REGISTRY": "registry:5000", "GITHUB_TOKEN": "x"}) == {}


def test_unrelated_vars_produce_nothing():
    assert _names({"PATH": "/usr/bin", "HOME": "/root", "TZ": "UTC", "PROD_PORT": "8200"}) == {}


def test_per_owner_github_token_produces_nothing():
    assert _names({"GITHUB_TOKEN__ACME": "x", "GITLAB_TOKEN__ACME": "y"}) == {}


def test_reserved_infrastructure_vars_produce_nothing():
    """auth_provider's reserved registry is orchestrator-owned, not a typo."""
    assert _names({"XAI_API_KEY": "x", "ANTHROPIC_BASE_URL": "u", "DOCKER_TLS_CERTDIR": "/certs"}) == {}


def test_undeclared_orchestrator_switch_produces_nothing():
    """XAI_DIRECT_EXECUTOR is read off os.environ, not declared as a field (EMB-46)."""
    assert _names({"XAI_DIRECT_EXECUTOR": "true"}) == {}


def test_unknown_var_in_an_embry0_owned_prefix_without_a_close_match():
    assert _names({"AUTH_BANANA_SPLIT": "1"}) == {"AUTH_BANANA_SPLIT": None}


def test_shared_prefixes_report_only_near_misses():
    """CI runners and the Claude Code CLI fill GITHUB_*/CLAUDE_* with their own keys."""
    assert _names({"GITHUB_ACTIONS": "true", "CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PID": "1"}) == {}
    assert _names({"CLAUDE_CODE_OAUTH_TOKE": "x"}) == {"CLAUDE_CODE_OAUTH_TOKE": "CLAUDE_CODE_OAUTH_TOKEN"}


def test_anthropic_and_max_prefixes_report_only_near_misses():
    """ANTHROPIC_*/MAX_THINKING_TOKENS belong to the Claude CLI, not to embry0."""
    assert (
        _names(
            {
                "ANTHROPIC_MODEL": "x",
                "ANTHROPIC_SMALL_FAST_MODEL": "x",
                "ANTHROPIC_LOG": "debug",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "x",
                "MAX_THINKING_TOKENS": "31999",
            }
        )
        == {}
    )
    assert _names({"ANTHROPIC_API_KE": "x"}) == {"ANTHROPIC_API_KE": "ANTHROPIC_API_KEY"}
    assert _names({"MAX_BUDGET_USDD": "5"}) == {"MAX_BUDGET_USDD": "MAX_BUDGET_USD"}


def test_removed_sandbox_limit_settings_are_reported():
    """SANDBOX_MEMORY / SANDBOX_CPUS were read by nothing and are gone (#590).

    An operator whose .env still sets them must be told at startup that they
    do nothing, not left believing they size the sandboxes.
    """
    found = _names({"SANDBOX_MEMORY": "8g", "SANDBOX_CPUS": "4"})
    assert found == {"SANDBOX_CPUS": None, "SANDBOX_MEMORY": None}, (
        f"a .env that sets the removed sandbox limit settings must warn, got {found}"
    )


def test_findings_are_sorted_by_name():
    findings = find_unknown_env_vars({"WEBHOOK_DEV_MOD": "1", "AUTH_DEV_MOD": "1"}, DECLARED)
    assert [f.name for f in findings] == ["AUTH_DEV_MOD", "WEBHOOK_DEV_MOD"]


def test_a_representative_environment_is_quiet():
    """Guards the prefix heuristic against false positives on a real deployment.

    Frozen on purpose: asserting against the live ``os.environ`` made the test
    fail on any developer machine that happened to export an unrelated variable.
    """
    assert find_unknown_env_vars(_REPRESENTATIVE_ENV, DECLARED) == []


# ── startup wiring ───────────────────────────────────────────────────────────


def test_startup_logs_one_warning_per_finding():
    from embry0.api.app import _warn_unknown_env_vars

    with structlog.testing.capture_logs() as captured:
        with patch.dict(os.environ, {"AUTH_DEV_MOD": "true"}, clear=True):
            _warn_unknown_env_vars(os.environ)

    events = [e for e in captured if e["event"] == "unknown_env_var"]
    assert len(events) == 1
    assert events[0]["name"] == "AUTH_DEV_MOD"
    assert events[0]["did_you_mean"] == "AUTH_DEV_MODE"
    assert events[0]["log_level"] == "warning"


def test_startup_is_silent_for_a_clean_environment():
    from embry0.api.app import _warn_unknown_env_vars

    with structlog.testing.capture_logs() as captured:
        with patch.dict(os.environ, {"AUTH_DEV_MODE": "true"}, clear=True):
            _warn_unknown_env_vars(os.environ)

    assert [e for e in captured if e["event"] == "unknown_env_var"] == []


def test_startup_survives_an_import_failure():
    """The hook's import sits inside its try: a broken module must not fail boot."""
    from embry0.api.app import _warn_unknown_env_vars

    with patch.dict(sys.modules, {"embry0.config_lint": None}):
        with structlog.testing.capture_logs() as captured:
            _warn_unknown_env_vars({"AUTH_DEV_MOD": "true"})

    assert [e["event"] for e in captured] == ["unknown_env_var_scan_failed"]


def test_startup_survives_a_scan_failure():
    """A raising scan is swallowed, so lifespan continues past the hook."""
    from embry0.api.app import _warn_unknown_env_vars

    with patch("embry0.config_lint.find_unknown_env_vars", side_effect=RuntimeError("boom")):
        with structlog.testing.capture_logs() as captured:
            _warn_unknown_env_vars({"AUTH_DEV_MOD": "true"})

    assert [e["event"] for e in captured] == ["unknown_env_var_scan_failed"]


def test_startup_survives_a_logging_failure():
    """The per-finding logging loop sits inside the try too."""
    from embry0.api import app as app_module

    seen: list[str] = []

    def _warning(event: str, **kwargs: object) -> None:
        seen.append(event)
        if event == "unknown_env_var":
            raise RuntimeError("log sink is down")

    with patch.object(app_module.logger, "warning", _warning):
        app_module._warn_unknown_env_vars({"AUTH_DEV_MOD": "true"})

    assert seen == ["unknown_env_var", "unknown_env_var_scan_failed"]
