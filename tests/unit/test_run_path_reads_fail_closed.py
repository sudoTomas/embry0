"""No broad ``except`` on the run path takes a stored value too deep to read for a default (#527).

``StoredValueTooDeep`` is an ``Exception``, so an ``except Exception`` around a
stored-row read turns it into whatever that handler falls back to: the code's
agent definition (a widened tool list), no model policy (a disabled model runs),
the variables' budget, no preferences. Every such ``try`` on the run path must
handle it first (``except StoredValueTooDeep``, raising it or failing the job
with it), unless it is listed below with why its fallback is not a config the
run acts on.
"""

from __future__ import annotations

import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: The modules a job's run goes through.
RUN_PATH = sorted(
    {
        *ROOT.glob("embry0/orchestration/**/*.py"),
        *ROOT.glob("embry0/workflows/**/*.py"),
        ROOT / "embry0/services/_executor_run.py",
        ROOT / "embry0/services/budget_policy.py",
        ROOT / "embry0/services/done_when.py",
        ROOT / "embry0/services/job_log_access.py",
    }
)

#: An awaited read of stored rows: a read method on a repository, a store, a policy or a pool.
_READS = {"get", "get_global", "get_repo", "list", "list_all", "snapshot", "settings", "fetch", "fetchrow", "fetchval"}
_RECEIVER = re.compile(r"(repo|Repository|_jobs\b|\bjobs\b|prefs|store|polic|_db\b|\bdb\b|stacks)")

#: (module, function, the read its ``try`` makes) whose broad ``except`` is not a default the run acts on, and why.
NOT_CONFIG = {
    (
        "embry0/orchestration/nodes/agent.py",
        "_job_failures_for_run",
        "traces_repo.list_all",
    ): "traces, for an account wait",
    ("embry0/services/_executor_run.py", "_fail_setup_proposal", "self._jobs.get"): "marks a proposal failed",
    ("embry0/services/_executor_run.py", "_run_workflow", "self._jobs.get"): "the run's own except fails the job",
    ("embry0/services/_executor_run.py", "_run_onboard_workflow", "self._jobs.get"): "the row only binds the trace id",
    ("embry0/services/_executor_run.py", "_run_qa_workflow", "self._jobs.get"): "the row only binds the trace id",
    ("embry0/workflows/issue_to_pr/nodes/_developer.py", "developer_node", "agent_sessions_repo.get"): (
        "a session to resume"
    ),
    ("embry0/workflows/issue_to_pr/nodes/_review.py", "review_node", "agent_sessions_repo.get"): "a session to resume",
    ("embry0/workflows/issue_to_pr/nodes/_init.py", "init_node", "profiles_repo.get"): (
        "sandbox set-up: its except fails the job (sandbox_init_failed)"
    ),
    ("embry0/workflows/issue_to_pr/nodes/_triage.py", "triage_node", "agent_sessions_repo.get"): "a session to resume",
    (
        "embry0/workflows/issue_to_pr/nodes/_verify.py",
        "_stored_config",
        "prefs_repo.get",
    ): "its except reports a problem",
    ("embry0/workflows/qa/nodes.py", "qa_node", "agent_sessions_repo.get"): "a session to resume",
    ("embry0/workflows/qa/orchestrator.py", "_init_orchestrator_node_impl", "qa_configs_repo.get"): (
        "sandbox set-up: its except fails the QA run"
    ),
    ("embry0/workflows/qa/orchestrator.py", "_qa_orchestrator_node_impl", "profiles_repo.get"): (
        "an advisory browser check: a profile it cannot read skips only that app's check"
    ),
}


def _reads(node: ast.AST) -> list[str]:
    """The stored-row reads ``node`` awaits, not counting those inside a ``try`` of its own."""
    found = []
    stack = [node]
    while stack:
        sub = stack.pop()
        if isinstance(sub, ast.Try) and sub is not node:
            continue
        if isinstance(sub, ast.Await) and isinstance(sub.value, ast.Call):
            func = sub.value.func
            if isinstance(func, ast.Attribute) and func.attr in _READS and _RECEIVER.search(ast.unparse(func.value)):
                found.append(ast.unparse(func))
        stack.extend(ast.iter_child_nodes(sub))
    return found


def _broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(ast.unparse(t) in ("Exception", "BaseException") for t in types)


def _handles(handler: ast.ExceptHandler) -> bool:
    """An ``except`` naming ``StoredValueTooDeep``: the value too deep to read is handled on purpose."""
    return handler.type is not None and "StoredValueTooDeep" in ast.unparse(handler.type)


def _swallowing_reads() -> list[str]:
    found = []
    for path in RUN_PATH:
        module = str(path.relative_to(ROOT))
        tree = ast.parse(path.read_text())
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(function):
                if not isinstance(node, ast.Try):
                    continue
                reads = [r for stmt in node.body for r in _reads(stmt)]
                if not reads or any((module, function.name, read) in NOT_CONFIG for read in reads):
                    continue
                for i, handler in enumerate(node.handlers):
                    if _broad(handler) and not any(_handles(h) for h in node.handlers[:i]):
                        found.append(f"{module}:{node.lineno} in {function.name}(): {', '.join(dict.fromkeys(reads))}")
                        break
    return sorted(set(found))


def test_no_broad_except_on_the_run_path_takes_a_value_too_deep_to_read_for_a_default() -> None:
    found = _swallowing_reads()
    assert not found, (
        "a broad except around a stored-row read on the run path turns StoredValueTooDeep into a default; "
        "handle it first (except StoredValueTooDeep: raise), or list it in NOT_CONFIG with why:\n" + "\n".join(found)
    )


def test_the_guard_finds_a_broad_except_around_a_read() -> None:
    """The guard can fail: a planted swallow is found, and one with the value too deep to read handled is not."""
    planted = ast.parse(
        "async def f(repo):\n    try:\n        return await repo.get('x')\n    except Exception:\n        return None\n"
    )
    [node] = [n for n in ast.walk(planted) if isinstance(n, ast.Try)]
    assert _reads(node.body[0]) == ["repo.get"] and _broad(node.handlers[0])
    fixed = ast.parse(
        "async def f(repo):\n    try:\n        return await repo.get('x')\n"
        "    except StoredValueTooDeep:\n        raise\n    except Exception:\n        return None\n"
    )
    [node] = [n for n in ast.walk(fixed) if isinstance(n, ast.Try)]
    assert _handles(node.handlers[0]) and _broad(node.handlers[1])


def test_every_listed_exception_still_names_a_read() -> None:
    """A NOT_CONFIG entry that no longer names a function's read in a try hides nothing, or was renamed."""
    stale = []
    for module, function, read in NOT_CONFIG:
        tree = ast.parse((ROOT / module).read_text())
        bodies = [
            r
            for f in ast.walk(tree)
            if isinstance(f, ast.FunctionDef | ast.AsyncFunctionDef) and f.name == function
            for t in ast.walk(f)
            if isinstance(t, ast.Try)
            for stmt in t.body
            for r in _reads(stmt)
        ]
        if read not in bodies:
            stale.append(f"{module}:{function}:{read}")
    assert not stale, f"NOT_CONFIG names reads that are gone: {stale}"
