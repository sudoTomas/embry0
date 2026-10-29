"""One registry of audit action names, read by every writer and by the Audit filter.

Before it, each writer spelled its own action: QA and onboard jobs wrote one
event under two names, and the dashboard's filter list, a copy kept in the
frontend, could not select a third of the actions recorded.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import re
import typing
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

import embry0
from embry0.audit.actions import AUDIT_ACTION_LABELS, AUDIT_ACTIONS, RETIRED_AUDIT_ACTIONS, AuditAction

_SRC = pathlib.Path(embry0.__file__).parent
#: The dashboard's copy of the labels and the renames, which the Audit page reads.
_AUDIT_LABELS_TS = _SRC.parent / "frontend" / "src" / "lib" / "auditLabels.ts"
# Every function that writes an audit row, and the position of its action argument.
_WRITERS = {"emit_audit": 1, "emit_audit_event": 0, "emit_audit_event_db": 1, "_emit_audit": 1, "_audit": 1, "audit": 1}


def _module_name(path: pathlib.Path) -> str:
    return ".".join(path.relative_to(_SRC.parent).with_suffix("").parts)


class _Scan(ast.NodeVisitor):
    """Collects the action each writer call passes, resolved to its string."""

    def __init__(self, path: pathlib.Path, tree: ast.Module) -> None:
        self.where = str(path.relative_to(_SRC.parent))
        self.constants: dict[str, str] = {}
        self.imports: dict[str, tuple[str, str]] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign | ast.AnnAssign) and isinstance(node.value, ast.Constant):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and isinstance(node.value.value, str):
                        self.constants[target.id] = node.value.value
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    self.imports[alias.asname or alias.name] = (node.module, alias.name)
        self.functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
        self.written: list[tuple[str, str]] = []
        self.problems: list[str] = []

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.functions.append(node)
        self.generic_visit(node)
        self.functions.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        if name in _WRITERS:
            action = next((kw.value for kw in node.keywords if kw.arg == "action"), None)
            if action is None and len(node.args) > _WRITERS[name]:
                action = node.args[_WRITERS[name]]
            if action is None:
                self.problems.append(f"{self.where}:{node.lineno} calls {name} with no action")
            else:
                self._resolve(action, node.lineno)
        self.generic_visit(node)

    def _resolve(self, expr: ast.expr, line: int) -> None:
        where = f"{self.where}:{line}"
        if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
            self.written.append((where, expr.value))
        elif isinstance(expr, ast.IfExp):
            self._resolve(expr.body, line)
            self._resolve(expr.orelse, line)
        elif isinstance(expr, ast.Name) and self._is_audit_action_parameter(expr.id):
            pass  # a writer's own parameter: its callers are scanned instead
        elif isinstance(expr, ast.Name) and expr.id in self.constants:
            self.written.append((where, self.constants[expr.id]))
        elif isinstance(expr, ast.Name) and expr.id in self.imports:
            module, attr = self.imports[expr.id]
            self.written.append((where, getattr(importlib.import_module(module), attr)))
        else:
            self.problems.append(f"{where} passes an action this scan cannot resolve: {ast.unparse(expr)}")

    def _is_audit_action_parameter(self, name: str) -> bool:
        if not self.functions:
            return False
        args = self.functions[-1].args
        for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
            if arg.arg == name:
                return arg.annotation is not None and ast.unparse(arg.annotation) == "AuditAction"
        return False


def _scan() -> tuple[list[tuple[str, str]], list[str]]:
    written: list[tuple[str, str]] = []
    problems: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text())
        scan = _Scan(path, tree)
        scan.visit(tree)
        written += scan.written
        problems += scan.problems
    return written, problems


def test_every_written_action_is_registered_and_every_registered_one_is_written() -> None:
    written, problems = _scan()
    assert problems == [], problems
    unregistered = sorted({f"{where} {action}" for where, action in written if action not in AUDIT_ACTIONS})
    assert unregistered == [], f"audit actions written but not in embry0/audit/actions.py: {unregistered}"
    unwritten = sorted(AUDIT_ACTIONS - {action for _where, action in written})
    assert unwritten == [], f"registered audit actions nothing writes: {unwritten}"


def test_the_registry_is_one_list_with_a_label_for_each_name() -> None:
    names = typing.get_args(AuditAction)
    assert len(names) == len(set(names)), "an audit action is listed twice"
    assert AUDIT_ACTIONS == frozenset(names)
    assert list(AUDIT_ACTION_LABELS) == list(names), "AUDIT_ACTION_LABELS must list the actions in registry order"
    assert all(label.strip() for label in AUDIT_ACTION_LABELS.values())


def test_no_event_has_two_spellings() -> None:
    """``qa.job_created`` and ``qa_job_created`` were one event."""
    spellings: dict[str, list[str]] = {}
    for name in AUDIT_ACTIONS:
        spellings.setdefault(name.replace(".", "_"), []).append(name)
    assert [names for names in spellings.values() if len(names) > 1] == []


def test_a_retired_name_is_never_written_and_names_the_action_that_replaced_it() -> None:
    """Old rows keep a renamed action's old name; nothing may write it again, or one event has two names."""
    assert not set(RETIRED_AUDIT_ACTIONS) & AUDIT_ACTIONS, "a retired name is still a registered action"
    unknown = sorted(f"{old} -> {new}" for old, new in RETIRED_AUDIT_ACTIONS.items() if new not in AUDIT_ACTIONS)
    assert unknown == [], f"retired names replaced by no registered action: {unknown}"
    written, _problems = _scan()
    still = sorted({f"{where} {action}" for where, action in written if action in RETIRED_AUDIT_ACTIONS})
    assert still == [], f"retired audit actions still written: {still}"


def _ts_record(source: str, name: str) -> dict[str, str]:
    """A ``Record<string, string>`` constant of a TypeScript module, as its quoted keys and values."""
    block = re.search(rf"export const {name}: Record<string, string> = \{{(.*?)\n\}};", source, re.S)
    assert block, f"auditLabels.ts no longer declares {name} as a Record<string, string>"
    return dict(re.findall(r'^\s*"?([\w.]+)"?:\s*"([^"]*)",?\s*$', block.group(1), re.M))


def test_the_dashboard_reads_each_retired_name_as_the_backend_does() -> None:
    """The Audit page keeps its own copy of the renames and labels: it must name the same pairs and words."""
    source = _AUDIT_LABELS_TS.read_text()
    retired, labels = _ts_record(source, "RETIRED_ACTIONS"), _ts_record(source, "ACTION_LABELS")
    assert retired == dict(RETIRED_AUDIT_ACTIONS), (
        f"auditLabels.ts RETIRED_ACTIONS {retired} differs from embry0/audit/actions.py {dict(RETIRED_AUDIT_ACTIONS)}"
    )
    words = {new: (labels.get(new), AUDIT_ACTION_LABELS[new]) for new in RETIRED_AUDIT_ACTIONS.values()}
    assert all(ts == py for ts, py in words.values()), f"a renamed action reads otherwise in the dashboard: {words}"


def _writer_functions() -> dict[str, Any]:
    from embry0.api.v1 import _audit, credentials, hosts, standards
    from embry0.audit.db_logger import emit_audit_event_db
    from embry0.audit.helpers import emit_audit
    from embry0.audit.logger import emit_audit_event
    from embry0.services.issue_executor import IssueExecutor

    return {
        "emit_audit": emit_audit,
        "emit_audit_event": emit_audit_event,
        "emit_audit_event_db": emit_audit_event_db,
        "IssueExecutor._emit_audit": IssueExecutor._emit_audit,
        "credentials._audit": credentials._audit,
        "hosts._audit": hosts._audit,
        "standards._audit": standards._audit,
        "_audit.audit": _audit.audit,
    }


@pytest.mark.parametrize("name", sorted(_writer_functions()))
def test_every_writer_takes_a_registered_action(name: str) -> None:
    """The annotation is what makes mypy refuse an unregistered name at each call site."""
    hints = typing.get_type_hints(_writer_functions()[name])
    assert hints["action"] == AuditAction, f"{name} takes action: {hints.get('action')}"


@pytest.mark.asyncio
async def test_the_audit_filter_list_is_served_from_the_registry() -> None:
    from embry0.api.app import create_app
    from embry0.config import Embry0Config

    @asynccontextmanager
    async def _noop(_app):
        yield

    config = Embry0Config(_env_file=None, auth_dev_mode=True, webhook_dev_mode=True)
    app = create_app(config, lifespan_override=_noop)
    app.state.config = config
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/audit/actions")
    assert resp.status_code == 200, resp.text
    current = [{"action": a, "label": label, "replaced_by": None} for a, label in AUDIT_ACTION_LABELS.items()]
    retired = [
        {"action": old, "label": AUDIT_ACTION_LABELS[new], "replaced_by": new}
        for old, new in RETIRED_AUDIT_ACTIONS.items()
    ]
    assert resp.json() == {"actions": [*current, *retired]}
