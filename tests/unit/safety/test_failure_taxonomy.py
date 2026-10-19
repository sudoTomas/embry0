"""#281 — the failure taxonomy: one classifier, a category per code, and a
structural guard that no purpose-built pipeline exception lands in
ERR_UNKNOWN."""

from __future__ import annotations

import ast
import asyncio
import errno
import importlib
from pathlib import Path

import pytest
import structlog.testing

from embry0.safety.error_codes import (
    ERROR_CATEGORIES,
    ErrorCode,
    FailureCategory,
    category_for_code,
    classify_agent_error,
    classify_failure,
    classify_qa_outcome,
    error_code_for_exception,
    failure_categories,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Packages whose exceptions can travel up a workflow run to the executor's
# `except Exception`. API / CLI / MCP / notification / healing errors never
# reach a job row.
_PIPELINE_PACKAGES = (
    "embry0/orchestration",
    "embry0/workflows",
    "embry0/execution",
    "embry0/workspace_init",
    "embry0/workspace_providers",
    "embry0/agents",
    "embry0/cache",
    "embry0/storage/repositories/jobs.py",
    "embry0/storage/repositories/repos.py",
    "embry0/services/model_enablement.py",
)

# Raised and caught inside one module, never a job outcome.
# EnrollRejected (SH-P4.4) lives in the PROXY container's aiohttp app: it is
# raised by a proxy's enroll-body extension and caught by the shared admin
# handler that turns it into an HTTP 400. Nothing in the orchestrator process
# raises it, so it can never reach a job row and has no meaningful job code.
# MalformedCommandList (#559) likewise lives in the git-proxy: raised while it
# reads a push's command list and caught by its own handler, which refuses
# the push with HTTP 403.
# XaiDeviceLoginError (#457) is raised by the operator's xAI device login,
# which only the credentials API runs (api/v1/credentials.py turns it into an
# HTTP error). No job starts a device login; the check below keeps it so.
# ImagePinError and RegistryAuthError (#523) are raised only while the
# orchestrator starts, by the functions that read the image pins and the
# registry login; api/app.py turns them into a refused start. No job calls
# those functions; the check below keeps it so.
# UnknownSkillError is raised only by check_skill_names, which only the
# agent and template save routes call (they turn it into an HTTP 422). A job
# resolves skills with effective_skills and never checks the catalog; the
# check below keeps it so.
_NEVER_ESCAPES = {
    "_TerminalRefreshFailure",
    "EnrollRejected",
    "MalformedCommandList",
    "XaiDeviceLoginError",
    "ImagePinError",
    "RegistryAuthError",
    "UnknownSkillError",
}

# The only callers of the functions that raise ImagePinError / RegistryAuthError.
_STARTUP_ONLY_RAISERS = {
    "parse_image_pins": "embry0/execution/image_registry.py",
    "load_image_pins": "embry0/execution/image_registry.py",
    "install_image_pins": "embry0/execution/image_registry.py",
    "read_registry_credential": "embry0/execution/registry_auth.py",
    "write_docker_login": "embry0/execution/registry_auth.py",
}


def _pipeline_exception_classes() -> list[type[BaseException]]:
    found: list[type[BaseException]] = []
    for entry in _PIPELINE_PACKAGES:
        root = _REPO_ROOT / entry
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for path in files:
            tree = ast.parse(path.read_text())
            names = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
            if not names:
                continue
            module = importlib.import_module(".".join(path.relative_to(_REPO_ROOT).with_suffix("").parts))
            for name in names:
                obj = getattr(module, name, None)
                if isinstance(obj, type) and issubclass(obj, BaseException) and name not in _NEVER_ESCAPES:
                    found.append(obj)
    return found


def test_no_job_path_starts_an_xai_device_login():
    """XaiDeviceLoginError is exempt only while nothing a job runs can raise it (#461)."""
    importers = []
    for entry in _PIPELINE_PACKAGES:
        root = _REPO_ROOT / entry
        for path in [root] if root.is_file() else sorted(root.rglob("*.py")):
            if path.name != "xai_device_login.py" and "xai_device_login" in path.read_text():
                importers.append(str(path.relative_to(_REPO_ROOT)))
    assert importers == [], f"a job path now reaches the device login; give XaiDeviceLoginError a code: {importers}"


def test_no_job_path_reads_the_image_pins_or_the_registry_login():
    """ImagePinError / RegistryAuthError are exempt only while startup alone raises them (#523)."""
    callers = []
    for entry in _PIPELINE_PACKAGES:
        root = _REPO_ROOT / entry
        for path in [root] if root.is_file() else sorted(root.rglob("*.py")):
            rel = str(path.relative_to(_REPO_ROOT))
            text = path.read_text()
            callers += [f"{rel}: {fn}" for fn, home in _STARTUP_ONLY_RAISERS.items() if fn in text and rel != home]
    assert callers == [], f"a job path now reads the pins or the registry login; give its errors a code: {callers}"


# The catalog check and its one caller, which only the save routes (embry0/api/v1) import.
_SKILL_CHECK_HOMES = {"embry0/agents/skills.py", "embry0/agents/skill_catalog.py"}


def test_no_job_path_checks_skill_names_against_the_catalog():
    """UnknownSkillError is exempt only while the save routes alone raise it."""
    callers = []
    catalog_importers = []
    for entry in _PIPELINE_PACKAGES:
        root = _REPO_ROOT / entry
        for path in [root] if root.is_file() else sorted(root.rglob("*.py")):
            rel = str(path.relative_to(_REPO_ROOT))
            if rel in _SKILL_CHECK_HOMES:
                continue
            text = path.read_text()
            if "check_skill_names" in text:
                callers.append(rel)
            if "skill_catalog" in text:
                catalog_importers.append(rel)
    assert callers == [], f"a job path now checks skill names; give UnknownSkillError a code: {callers}"
    assert catalog_importers == [], (
        f"a job path now reaches the catalog check; give UnknownSkillError a code: {catalog_importers}"
    )


def _instantiate(cls: type[BaseException]) -> BaseException:
    # Bypass __init__: constructors differ and the classifier must key on the
    # TYPE, not on whatever message a call site happened to pass.
    exc = cls.__new__(cls)
    exc.args = ("synthetic",)
    return exc


def test_the_walk_finds_the_known_exception_classes():
    names = {c.__name__ for c in _pipeline_exception_classes()}
    assert {"BudgetOverrunError", "TriageParseError", "SandboxInitError", "StatusTransitionConflict"} <= names


@pytest.mark.parametrize("cls", _pipeline_exception_classes(), ids=lambda c: c.__name__)
def test_every_purpose_built_pipeline_exception_has_a_specific_code(cls):
    assert classify_failure(exc=_instantiate(cls)) is not ErrorCode.UNKNOWN


def test_every_code_has_a_category():
    assert set(ERROR_CATEGORIES) == set(ErrorCode)
    assert all(isinstance(c, FailureCategory) for c in ERROR_CATEGORIES.values())


def test_unknown_stored_codes_fall_into_the_unknown_category():
    assert category_for_code("ERR_FROM_A_NEWER_DEPLOY") is FailureCategory.UNKNOWN
    assert category_for_code(None) is FailureCategory.UNKNOWN
    assert category_for_code("ERR_AGENT_MAX_TURNS") is FailureCategory.AGENT


def _invalid_update_error() -> Exception:
    from langgraph.errors import InvalidUpdateError

    return InvalidUpdateError("At key 'current_stage': Can receive only one value per step.")


def _recursion_error() -> Exception:
    from langgraph.errors import GraphRecursionError

    return GraphRecursionError("Recursion limit of 25 reached")


def _pg_error() -> Exception:
    import asyncpg

    return asyncpg.NotNullViolationError('null value in column "issue_id"')


def _http_error() -> Exception:
    import httpx

    return httpx.ConnectError("boom")


@pytest.mark.parametrize(
    ("make", "expected"),
    [
        (_invalid_update_error, ErrorCode.STATE_CONCURRENT_UPDATE),
        (_recursion_error, ErrorCode.GRAPH_RECURSION),
        (_pg_error, ErrorCode.STORAGE),
        (_http_error, ErrorCode.UPSTREAM_HTTP),
        (lambda: OSError(errno.E2BIG, "Argument list too long"), ErrorCode.SANDBOX_ARGV_TOO_LONG),
        (
            lambda: asyncio.LimitOverrunError("Separator is not found, and chunk exceed the limit", 10),
            ErrorCode.AGENT_OUTPUT_OVERFLOW,
        ),
        (lambda: ValueError("Separator is found, but chunk is longer than limit"), ErrorCode.AGENT_OUTPUT_OVERFLOW),
        (lambda: RuntimeError("Sandbox initialization failed: boom"), ErrorCode.SANDBOX_INIT),
        (lambda: RuntimeError("workflow 'x' not registered"), ErrorCode.WORKFLOW_UNKNOWN),
    ],
)
def test_third_party_and_stdlib_failures_seen_in_production(make, expected):
    assert classify_failure(exc=make()) is expected


def test_an_exception_carrying_its_own_code_wins():
    from embry0.execution.sandbox_manager import SandboxInitError

    exc = SandboxInitError("nope", error_code=ErrorCode.DOCKER_TIMEOUT)
    assert classify_failure(exc=exc) is ErrorCode.DOCKER_TIMEOUT


def test_legacy_entry_point_delegates():
    assert error_code_for_exception(_invalid_update_error()) is ErrorCode.STATE_CONCURRENT_UPDATE


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("analysis: Claude Code returned an error result: Reached maximum number of turns (40)", "AGENT_MAX_TURNS"),
        ("xai agent reached max_turns (25)", "AGENT_MAX_TURNS"),
        (
            "developer: Command failed with exit code 1 (exit code: 1)\nError output: Check stderr",
            "AGENT_PROCESS_FAILED",
        ),
        ("Agent timed out after 1800s", "AGENT_TIMEOUT"),
        ("agent produced no events for 600s (idle watchdog); run killed", "AGENT_IDLE"),
        ("No final result received from sandbox; runner stderr: Traceback", "AGENT_NO_RESULT"),
        ("Separator is found, but chunk is longer than limit", "AGENT_OUTPUT_OVERFLOW"),
        ("[Errno 7] Argument list too long", "SANDBOX_ARGV_TOO_LONG"),
        ("triage_malformed: Invalid JSON: Expecting value: line 1 column 1 (char 0)", "TRIAGE_MALFORMED"),
        ("qa_failure_unresolved: triage_qa_failure_action_invalid_payload", "TRIAGE_QA_ACTION_INVALID"),
        (
            "Developer agent did not push branch 'embry0/x' to origin. This breaks the QA handoff",
            "DEV_BRANCH_NOT_PUSHED",
        ),
        ("At key 'qa': Can receive only one value per step. Use an Annotated key", "STATE_CONCURRENT_UPDATE"),
        ("Abandoned after 3 retries", "ABANDONED"),
        ("Exceeded 3 user-initiated retries", "MAX_RETRIES"),
        ("No agent runner available", "AGENT_RUNNER_UNAVAILABLE"),
    ],
)
def test_error_text_clusters_from_the_audit(message, expected):
    assert classify_agent_error(message) is ErrorCode[expected]


def test_unrecognised_text_is_not_guessed():
    assert classify_agent_error("something nobody has seen before") is None
    assert classify_agent_error("") is None
    assert classify_agent_error(None) is None


def test_stage_decides_when_nothing_else_does():
    assert classify_failure(stage="abandoned") is ErrorCode.ABANDONED


def test_agent_error_outranks_stage_and_collected_errors_are_read():
    assert (
        classify_failure(stage="abandoned", agent_error="Reached maximum number of turns (25)")
        is ErrorCode.AGENT_MAX_TURNS
    )
    assert classify_failure(stage="failed", errors=["noise", "Agent timed out after 5s"]) is ErrorCode.AGENT_TIMEOUT


def test_last_resort_logs_the_class_and_stage_for_the_next_audit():
    class NobodyMappedThis(Exception):
        pass

    with structlog.testing.capture_logs() as logs:
        code = classify_failure(exc=NobodyMappedThis("x"), stage="developer_complete")
    assert code is ErrorCode.UNKNOWN
    event = next(e for e in logs if e["event"] == "failure_unclassified")
    assert event["exception_class"].endswith("NobodyMappedThis")
    assert event["stage"] == "developer_complete"
    assert event["log_level"] == "error"


def test_a_classified_failure_does_not_log_unclassified():
    with structlog.testing.capture_logs() as logs:
        classify_failure(stage="abandoned")
    assert not [e for e in logs if e["event"] == "failure_unclassified"]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"qa_error_code": "ERR_QA_IDLE", "failed_apps": ["a"]}, ErrorCode.QA_IDLE),
        ({"validation_errors": ["bad group"], "infra_apps": ["a"]}, ErrorCode.QA_CONFIG_INVALID),
        ({"infra_apps": ["a"], "failed_apps": ["b"]}, ErrorCode.QA_INFRA),
        ({"failed_apps": ["b"], "inconclusive_apps": ["c"]}, ErrorCode.QA_FAILED),
        ({"inconclusive_apps": ["c"]}, ErrorCode.QA_INCONCLUSIVE),
        ({"qa_error_code": "ERR_QA_NO_APPS"}, ErrorCode.QA_NO_APPS),
        ({}, ErrorCode.QA_NO_VERDICT),
    ],
)
def test_standalone_qa_outcomes(kwargs, expected):
    assert classify_qa_outcome(**kwargs) is expected


def test_no_apps_is_a_configuration_error_with_public_text():
    from embry0.notifications.public_errors import public_error_message
    from embry0.safety.error_codes import ERROR_CATEGORIES, FailureCategory

    assert ERROR_CATEGORIES[ErrorCode.QA_NO_APPS] is FailureCategory.CONFIG
    assert "no app" in public_error_message("ERR_QA_NO_APPS")


def test_an_unknown_qa_code_from_state_is_not_trusted():
    assert classify_qa_outcome(qa_error_code="ERR_MADE_UP", failed_apps=["a"]) is ErrorCode.QA_FAILED


def test_a_code_with_no_category_entry_does_not_raise_into_stats():
    """The structural test above keeps ERROR_CATEGORIES complete in THIS
    build. A parallel branch adding an ErrorCode member is exactly the case
    category_for_code promises to tolerate: the member passes the enum
    lookup, so only catching ValueError left a KeyError to 500 /stats."""
    victim = ErrorCode.ORPHANED
    saved = ERROR_CATEGORIES.pop(victim)
    try:
        assert category_for_code(victim.value) is FailureCategory.UNKNOWN
        assert failure_categories([(victim.value, 3), ("ERR_AGENT_IDLE", 2)]) == {
            **{c.value: 0 for c in FailureCategory},
            "unknown": 3,
            "agent": 2,
        }
    finally:
        ERROR_CATEGORIES[victim] = saved
