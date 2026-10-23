"""Production wires the context audit schedule (ADR 0020) to its settings and starts it.

A loop built without its cap or its job starter, or never started, constructs
happily and starts nothing, which looks exactly like "no repository is due
yet" (the #369 shape). Read from the AST of ``lifespan``, which needs Docker
and a database; see test_merge_approvals_wiring.py for the same approach.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import embry0.api.app as app_module


def _tree() -> ast.Module:
    return ast.parse(pathlib.Path(app_module.__file__).read_text())


def _construction(name: str) -> ast.Call:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name:
            return node
    pytest.fail(f"embry0/api/app.py never constructs {name}")


def _function(name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(_tree()):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    pytest.fail(f"embry0/api/app.py defines no {name}")


def _started(attr: str) -> bool:
    return any(
        isinstance(node, ast.Call)
        and ast.unparse(node.func) == "asyncio.create_task"
        and node.args
        and ast.unparse(node.args[0]) == f"app.state.{attr}.start()"
        for node in ast.walk(_tree())
    )


def test_the_loop_reads_its_cap_and_its_collaborators_and_is_started() -> None:
    call = _construction("ContextAuditLoop")
    passed = {kw.arg: ast.unparse(kw.value) for kw in call.keywords if kw.arg}
    assert passed.get("max_jobs_per_tick") == "config.context_audit_max_jobs_per_tick", (
        "ContextAuditLoop is not given CONTEXT_AUDIT_MAX_JOBS_PER_TICK"
    )
    assert passed.get("organizations") == "app.state.organizations_repo"
    assert passed.get("registry") == "app.state.repos_repo", "the loop reads the registered repositories"
    assert passed.get("jobs") == "app.state.jobs_repo", "the loop anchors on the recorded jobs"
    assert passed.get("client_for") == "_context_audit_client"
    assert passed.get("start_job") == "_start_context_audit"
    assert _started("context_audit"), "app.py never starts the context audit loop"


def test_the_forge_is_read_with_the_organizations_credential() -> None:
    body = ast.unparse(_function("_context_audit_client"))
    assert "org_forge_client(" in body, "the loop must read the forge the way discovery does"


def test_every_scheduled_job_gets_the_hard_budget_and_the_job_path() -> None:
    start = _function("_start_context_audit")
    calls = [n for n in ast.walk(start) if isinstance(n, ast.Call) and ast.unparse(n.func) == "start_context_audit_job"]
    assert calls, "_start_context_audit does not start jobs through start_context_audit_job"
    passed = {kw.arg: ast.unparse(kw.value) for kw in calls[0].keywords if kw.arg}
    assert passed.get("budget_usd") == "job_budget_usd(config)", "the job is not given CONTEXT_AUDIT_JOB_BUDGET_USD"
    assert passed.get("templates") == "app.state.templates_repo", "the template check needs the templates"
    assert passed.get("jobs") == "app.state.jobs_repo"
    assert "issue_executor" in passed.get("executor", ""), "a job row nobody runs sits in pending forever"
    assert passed.get("db") == "db", "the job_created audit row goes to the database"
