"""Production wires the scout loop (spec 2026-10-27) to its settings and starts it.

A loop built without its gate or its job starter, or never started, constructs
happily and scouts nothing, which looks exactly like "no model is set".
A loop built with its section read once would run, and ignore every change to
it; one built with the deployment's values alone would ignore every
organization's layer. Read from the AST of ``lifespan``, as
test_context_audit_wiring.py does; ``tests/unit/api/test_scout_settings.py``
and ``tests/unit/api/test_scout_per_organization.py`` run the real lifespan.
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


def test_the_loop_reads_its_settings_and_its_collaborators_and_is_started() -> None:
    passed = {kw.arg: ast.unparse(kw.value) for kw in _construction("ScoutLoop").keywords if kw.arg}
    assert passed.get("settings") == "tick_settings(app.state.settings)", (
        "the scout reads its scout section, per repository (#278), and its model (background_models.scout, "
        "#164) every tick, both from one snapshot of the store"
    )
    assert "models" not in passed, "no second reader: a tick that read the store twice could mix two versions"
    assert passed.get("leads") == "app.state.leads_repo", "harvested cracks and weeds go where every lead goes"
    assert passed.get("jobs") == "app.state.jobs_repo"
    assert passed.get("ask_gate") == "_scout_gate" and passed.get("submit") == "_scout_submit"
    assert any(
        isinstance(node, ast.Call)
        and ast.unparse(node.func) == "asyncio.create_task"
        and node.args
        and ast.unparse(node.args[0]) == "app.state.scout.start()"
        for node in ast.walk(_tree())
    ), "app.py never starts the scout loop"


def test_each_start_asks_the_executors_gate_for_the_scout_actor_and_writes_its_audit_row() -> None:
    gate = ast.unparse(_function("_scout_gate"))
    assert "dispatch_refusal(actor=_scout_actors.SCOUT)" in gate, "the scout is rate-limited like any actor"
    submit = ast.unparse(_function("_scout_submit"))
    assert "'job_created'" in submit and "_scout_actors.SCOUT" in submit
    assert "submit_job(" in submit and "kind='scout'" in submit
