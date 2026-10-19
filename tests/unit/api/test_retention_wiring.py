"""Production wires the retention sweeps (ADR 0017) to their settings and starts them.

A sweep built without its setting, or never started, constructs happily and
removes nothing, which looks exactly like "nothing old enough yet" (the #369
shape). Read from the AST of ``lifespan``, which needs Docker and a database;
see test_merge_approvals_wiring.py for the same approach.
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


def _started(attr: str) -> bool:
    return any(
        isinstance(node, ast.Call)
        and ast.unparse(node.func) == "asyncio.create_task"
        and node.args
        and ast.unparse(node.args[0]) == f"app.state.{attr}.start()"
        for node in ast.walk(_tree())
    )


def test_the_job_log_sweep_reads_its_setting_and_is_started() -> None:
    call = _construction("JobLogRetentionSweep")
    passed = {kw.arg: ast.unparse(kw.value) for kw in call.keywords if kw.arg}
    assert passed.get("retention_days") == "config.job_log_retention_days", (
        "JobLogRetentionSweep is not given JOB_LOG_RETENTION_DAYS"
    )
    assert ast.unparse(call.args[0]).startswith("JobLogRetentionRepository("), "the sweep needs its repository"
    assert _started("job_log_retention"), "app.py never starts the job log retention loop"
