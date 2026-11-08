"""The startup check of the kernel's Yama ``ptrace_scope``, read from a file the test writes.

The test gate's cleanup step assumes no sandbox process can trace another of
the same user. At 1 to 3 the check says nothing; at 0, without Yama, or with a
value it cannot read, it logs ``ptrace_scope_permissive`` at warning and
writes one audit row, each with a remedy of its own, and the start goes on.
"""

from __future__ import annotations

import ast
import importlib
import json
import pathlib
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from structlog.testing import capture_logs

MODULE = "embry0.execution.runtime.ptrace_scope"


def _check() -> ModuleType:
    try:
        return importlib.import_module(MODULE)
    except ImportError as exc:
        pytest.fail(f"{MODULE} is missing: {exc}")


class _Db:
    """The audit_log table, as the audit writer reaches it."""

    def __init__(self) -> None:
        self.rows: list[tuple[Any, ...]] = []

    async def execute(self, query: str, *args: Any) -> str:
        assert "INSERT INTO audit_log" in query, query
        self.rows.append(args)
        return "INSERT 0 1"


def _setting(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "ptrace_scope"
    path.write_text(text)
    return path


async def _run(path: Path, tmp_path: Path) -> tuple[Any, list[dict[str, Any]], _Db, Path]:
    check = _check()
    db, audit_file = _Db(), tmp_path / "audit.jsonl"
    with capture_logs() as logs:
        scope = await check.warn_if_same_user_ptrace_allowed(db, audit_log_path=audit_file, path=path)
    return scope, logs, db, audit_file


def _warnings(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in logs if e["event"] == "ptrace_scope_permissive"]


def _flagged(logs: list[dict[str, Any]], db: _Db) -> tuple[dict[str, Any], dict[str, Any]]:
    """The one warning and the details of the one audit row, which carry the same reason and remedy."""
    warned = _warnings(logs)
    assert len(warned) == 1, f"one warning, under its stable name: {[e['event'] for e in logs]}"
    assert warned[0]["log_level"] == "warning", warned[0]
    assert len(db.rows) == 1, f"one audit row in the database: {db.rows}"
    action, actor, details = db.rows[0][:3]
    assert (action, actor) == ("kernel.ptrace_scope.flagged", "system"), db.rows[0]
    assert (details["reason"], details["remedy"]) == (warned[0]["reason"], warned[0]["remedy"]), (
        f"the audit row says what the log line says: {details} {warned[0]}"
    )
    return warned[0], details


@pytest.mark.parametrize("value", [1, 2, 3])
async def test_a_kernel_that_restricts_ptrace_is_not_reported(tmp_path: Path, value: int) -> None:
    scope, logs, db, audit_file = await _run(_setting(tmp_path, f"{value}\n"), tmp_path)
    assert (scope.value, scope.restricted) == (value, True), scope
    assert _warnings(logs) == [], f"ptrace_scope {value} restricts tracing; nothing to report: {logs}"
    assert db.rows == [] and not audit_file.exists(), "no audit row when the kernel restricts tracing"


async def test_scope_0_is_warned_about_once_with_one_audit_row(tmp_path: Path) -> None:
    check = _check()
    scope, logs, db, audit_file = await _run(_setting(tmp_path, "0\n"), tmp_path)
    assert (scope.value, scope.reason) == (0, "same_user_ptrace_allowed"), scope
    warning, details = _flagged(logs, db)
    assert (warning["value"], warning["reason"]) == (0, "same_user_ptrace_allowed"), warning
    assert details["value"] == 0, details
    lines = audit_file.read_text().splitlines()
    assert [json.loads(line)["action"] for line in lines] == ["kernel.ptrace_scope.flagged"], lines
    assert check.AUDIT_ACTION == "kernel.ptrace_scope.flagged"


async def test_at_0_the_remedy_is_the_sysctl_then_a_restart(tmp_path: Path) -> None:
    _, logs, db, _ = await _run(_setting(tmp_path, "0\n"), tmp_path)
    warning, _ = _flagged(logs, db)
    remedy = warning["remedy"]
    assert "sysctl kernel.yama.ptrace_scope=1" in remedy and "/etc/sysctl.d/" in remedy, (
        f"at 0 the setting is raised, and persisted: {remedy}"
    )
    assert "restart the orchestrator" in remedy, f"the warning clears at the next start: {remedy}"
    assert "is 0" in warning["msg"], f"the message names the value it read: {warning['msg']}"


async def test_a_kernel_without_yama_is_warned_about(tmp_path: Path) -> None:
    scope, logs, db, _ = await _run(tmp_path / "no-such-file", tmp_path)
    assert (scope.value, scope.reason) == (None, "yama_absent"), scope
    assert [e["reason"] for e in _warnings(logs)] == ["yama_absent"], f"no Yama is no restriction: {logs}"
    assert len(db.rows) == 1 and db.rows[0][0] == "kernel.ptrace_scope.flagged", db.rows


async def test_without_yama_the_remedy_is_a_kernel_with_yama_not_the_sysctl(tmp_path: Path) -> None:
    _, logs, db, _ = await _run(tmp_path / "no-such-file", tmp_path)
    warning, _ = _flagged(logs, db)
    remedy = warning["remedy"]
    assert "CONFIG_SECURITY_YAMA" in remedy and "kernel with Yama enabled" in remedy, (
        f"without Yama the host needs a kernel that has it: {remedy}"
    )
    assert "sysctl kernel.yama.ptrace_scope=1" not in remedy, (
        f"there is no setting for sysctl to change on a kernel without Yama: {remedy}"
    )
    assert "no Yama" in warning["msg"], f"the message says why: {warning['msg']}"


@pytest.mark.parametrize("text", ["", "abc\n", "7\n", "1 2\n", "-1\n", "١\n"], ids=repr)
async def test_a_setting_that_cannot_be_read_as_0_to_3_is_warned_about(tmp_path: Path, text: str) -> None:
    """Only 1 to 3 is silence: anything the check cannot read as a value is no proof of a restriction."""
    scope, logs, db, _ = await _run(_setting(tmp_path, text), tmp_path)
    assert scope.reason == "unreadable" and not scope.restricted, scope
    assert len(_warnings(logs)) == 1 and len(db.rows) == 1, f"{text!r} is not a restriction: {logs}"


async def test_a_setting_that_is_not_a_file_is_warned_about_not_raised(tmp_path: Path) -> None:
    (tmp_path / "ptrace_scope").mkdir()
    scope, logs, db, _ = await _run(tmp_path / "ptrace_scope", tmp_path)
    assert scope.reason == "unreadable", scope
    assert len(_warnings(logs)) == 1 and len(db.rows) == 1, logs


@pytest.mark.parametrize("unreadable", ["not a value", "out of range", "not a file"])
async def test_an_unreadable_setting_is_reported_as_unknown_not_as_a_permissive_kernel(
    tmp_path: Path, unreadable: str
) -> None:
    """The kernel may well restrict tracing; what failed is the orchestrator's read, and the remedy checks that."""
    if unreadable == "not a file":
        (tmp_path / "ptrace_scope").mkdir()
        path = tmp_path / "ptrace_scope"
    else:
        path = _setting(tmp_path, "abc\n" if unreadable == "not a value" else "7\n")
    _, logs, db, _ = await _run(path, tmp_path)
    warning, _ = _flagged(logs, db)
    assert warning["reason"] == "unreadable", warning
    message, remedy = warning["msg"], warning["remedy"]
    assert "could not be established" in message, f"an unreadable value establishes nothing: {message}"
    claims = [claim for claim in ("lets", "allows", "does not stop", "does not apply") if claim in message]
    assert claims == [], f"an unreadable value says nothing about what the kernel allows: {claims} in {message}"
    assert "sysctl" not in remedy, f"the kernel may be fine; the remedy checks the read, it changes nothing: {remedy}"
    assert "cat /proc/sys/kernel/yama/ptrace_scope" in remedy and "orchestrator's container" in remedy, (
        f"the remedy says what to check, on the host and where the orchestrator reads it: {remedy}"
    )


async def test_an_audit_write_that_fails_never_stops_the_start(tmp_path: Path) -> None:
    check = _check()
    with (
        patch("embry0.audit.helpers.emit_audit", new=AsyncMock(side_effect=RuntimeError("database gone"))),
        capture_logs() as logs,
    ):
        scope = await check.warn_if_same_user_ptrace_allowed(None, path=_setting(tmp_path, "0\n"))
    assert scope.reason == "same_user_ptrace_allowed", scope
    events = [e["event"] for e in logs]
    assert "ptrace_scope_permissive" in events and "ptrace_scope_audit_failed" in events, events


def test_every_start_runs_the_check_with_the_database_and_the_audit_file() -> None:
    """Read from the AST of ``lifespan``, which needs Docker and a database (see test_retention_wiring.py)."""
    import embry0.api.app as app_module

    tree = ast.parse(pathlib.Path(app_module.__file__).read_text())
    (lifespan,) = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan"]
    expected = "warn_if_same_user_ptrace_allowed(db, audit_log_path=config.audit_log_path)"

    def is_check(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Await)
            and isinstance(node.value, ast.Call)
            and ast.unparse(node.value.func) == "warn_if_same_user_ptrace_allowed"
        )

    anywhere = [ast.unparse(node) for node in ast.walk(lifespan) if is_check(node)]
    assert anywhere == [f"await {expected}"], (
        f"the orchestrator's start must run the ptrace check once, with the database and the audit file: {anywhere}"
    )
    top_level = [ast.unparse(stmt) for stmt in lifespan.body if isinstance(stmt, ast.Expr) and is_check(stmt.value)]
    assert top_level == [f"await {expected}"], (
        "the check must be a statement of lifespan's own body, so every start runs it, "
        f"not one under a condition, a loop or a handler: {top_level}"
    )
