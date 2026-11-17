"""No broad ``except`` on the run path takes a stored value too deep to read for a default (#527).

``StoredValueTooDeep`` is an ``Exception``, so an ``except Exception`` around a
stored-row read turns it into whatever that handler falls back to: the code's
agent definition (a widened tool list), no model policy (a disabled model runs),
the variables' budget, no preferences, the deployment's token for a pinned
credential. Every such ``try`` on the run path must handle it first with an
``except StoredValueTooDeep`` that raises it, unless it is listed below with why
its fallback is not a config the run acts on.

What the guard reads as a broad catch: ``except Exception`` or
``BaseException`` (``builtins.`` included, in a tuple too), a bare ``except``,
and ``contextlib.suppress`` of either. An ``except StoredValueTooDeep`` that does
not raise is a swallow too. What it reads as a stored-row read: an awaited read
method (``get``, ``list_all``, ``snapshot``, ``fetch``...) on a repository, a
store, a policy or a pool, and an awaited credential lookup (``forge_token``,
``resolve_forge``, ``resolve_llm``, ``resolve_llm_for_job``).

Known limits: a read gathered with ``asyncio.gather(..., return_exceptions=True)``;
a read made in a helper the ``try`` calls, past the one level the helper's own
module is scanned at; a receiver or method outside the pattern above; and a
``raise`` the handler reaches only on some paths (``if x: raise`` then a
default) counts as handled only when it is a statement of the handler itself,
not one nested in a branch. Those shapes are found by the per-site tests
(``tests/unit/api/test_unreadable_values.py``), not here.

Never scanned: ``embry0/api/``, ``embry0/execution/`` and the rest of
``embry0/services/`` (only ``_executor_run``, ``budget_policy``, ``done_when``,
``job_log_access`` and ``credentials/`` are). A read there is not a run's, or is
covered by a per-site test.
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
        *ROOT.glob("embry0/services/credentials/*.py"),
        ROOT / "embry0/services/_executor_run.py",
        ROOT / "embry0/services/budget_policy.py",
        ROOT / "embry0/services/done_when.py",
        ROOT / "embry0/services/job_log_access.py",
    }
)

#: An awaited read of stored rows: a read method on a repository, a store, a policy or a pool.
_READS = {"get", "get_global", "get_repo", "list", "list_all", "snapshot", "settings", "fetch", "fetchrow", "fetchval"}
_RECEIVER = re.compile(r"(repo|Repository|_jobs\b|\bjobs\b|prefs|store|polic|_db\b|\bdb\b|stacks|_credentials\b)")
#: Credential lookups, which read the registry: ``forge_token(...)``, and the resolver's methods (a bare
#: ``resolve_forge(...)`` is the forge resolver, which reads the repository registry, not credentials).
_CREDENTIAL_CALLS = {"forge_token"}
_CREDENTIAL_METHODS = {"resolve_forge", "resolve_llm", "resolve_llm_for_job"}
_BROAD = {"Exception", "BaseException", "builtins.Exception", "builtins.BaseException"}

#: (module, function, the read its ``try`` makes) whose broad ``except`` is not a default the run acts on, and why.
NOT_CONFIG = {
    (
        "embry0/orchestration/nodes/agent.py",
        "_job_failures_for_run",
        "traces_repo.list_all",
    ): "traces, for an account wait",
    ("embry0/services/_executor_run.py", "_fail_setup_proposal", "self._jobs.get"): "marks a proposal failed",
    ("embry0/services/_executor_run.py", "_run_workflow", "self._jobs.get"): (
        "never takes this error for a default: the job's row, whose except keeps it and raises it inside the "
        "run's error handling"
    ),
    ("embry0/services/_executor_run.py", "_run_pr_review_workflow", "self._jobs.get"): (
        "never takes this error for a default: the job's row, whose except keeps it and raises it inside the "
        "review's error handling"
    ),
    ("embry0/services/_executor_run.py", "_run_workflow", "forge_token"): (
        "the duplicate-PR check is skipped, never run with another token; the sandbox's enrollment resolves the "
        "credential again and fails the job"
    ),
    ("embry0/services/credentials/discovery.py", "forge_credential_entries", "repo.list_all"): (
        "repository discovery's tokens to try: a registry it cannot read leaves fewer to try, never another's"
    ),
    ("embry0/services/credentials/resolver.py", "pinned_pause", "self._repo.get"): "the resolve that follows raises",
    ("embry0/workflows/qa/orchestrator.py", "_init_orchestrator_node_impl", "qa_configs_repo.get"): (
        "never takes this error for a default: its except ends the QA run as infra_error, saying why"
    ),
    ("embry0/workflows/qa/orchestrator_report.py", "qa_report_node", "forge_token"): (
        "the report is skipped, never posted with another token"
    ),
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
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            attribute = isinstance(func, ast.Attribute)
            if (
                (not attribute and name in _CREDENTIAL_CALLS)
                or (attribute and name in _CREDENTIAL_METHODS)
                or (attribute and name in _READS and _RECEIVER.search(ast.unparse(func.value)))
            ):
                found.append(ast.unparse(func))
        stack.extend(ast.iter_child_nodes(sub))
    return found


def _broad(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    types = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(ast.unparse(t) in _BROAD for t in types)


def _catches(handler: ast.ExceptHandler) -> bool:
    """An ``except`` naming ``StoredValueTooDeep``."""
    return handler.type is not None and "StoredValueTooDeep" in ast.unparse(handler.type)


def _handles(handler: ast.ExceptHandler) -> bool:
    """An ``except StoredValueTooDeep`` that raises it again on every path: a ``raise`` that is a statement of the
    handler itself, not one nested in a branch (``if x: raise`` then a default is a swallow)."""
    return _catches(handler) and any(isinstance(stmt, ast.Raise) for stmt in handler.body)


def _suppresses(node: ast.With | ast.AsyncWith) -> bool:
    """``with contextlib.suppress(Exception)`` (or ``BaseException``, or ``suppress`` imported by name)."""
    for item in node.items:
        call = item.context_expr
        if isinstance(call, ast.Call) and ast.unparse(call.func) in ("contextlib.suppress", "suppress"):
            if any(ast.unparse(arg) in _BROAD for arg in call.args):
                return True
    return False


def _swallowing_reads() -> list[str]:
    found = []
    for path in RUN_PATH:
        module = str(path.relative_to(ROOT))
        tree = ast.parse(path.read_text())
        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(function):
                if not isinstance(node, ast.Try | ast.With | ast.AsyncWith):
                    continue
                reads = [r for stmt in node.body for r in _reads(stmt)]
                if not reads or any((module, function.name, read) in NOT_CONFIG for read in reads):
                    continue
                where = f"{module}:{node.lineno} in {function.name}(): {', '.join(dict.fromkeys(reads))}"
                if isinstance(node, ast.With | ast.AsyncWith):
                    if _suppresses(node):
                        found.append(where)
                    continue
                for i, handler in enumerate(node.handlers):
                    if _catches(handler) and not _handles(handler):
                        found.append(where)
                        break
                    if _broad(handler) and not any(_handles(h) for h in node.handlers[:i]):
                        found.append(where)
                        break
    return sorted(set(found))


def test_no_broad_except_on_the_run_path_takes_a_value_too_deep_to_read_for_a_default() -> None:
    found = _swallowing_reads()
    assert not found, (
        "a broad except around a stored-row read on the run path turns StoredValueTooDeep into a default; "
        "handle it first (except StoredValueTooDeep: raise), or list it in NOT_CONFIG with why:\n" + "\n".join(found)
    )


def _found_in(source: str) -> bool:
    tree = ast.parse(source)
    nodes = [n for n in ast.walk(tree) if isinstance(n, ast.Try | ast.With | ast.AsyncWith)]
    for node in nodes:
        if not [r for stmt in node.body for r in _reads(stmt)]:
            continue
        if isinstance(node, ast.With | ast.AsyncWith):
            return _suppresses(node)
        for i, handler in enumerate(node.handlers):
            if _catches(handler) and not _handles(handler):
                return True
            if _broad(handler) and not any(_handles(h) for h in node.handlers[:i]):
                return True
    return False


def test_the_guard_finds_each_shape_it_reads() -> None:
    read = "async def f(repo):\n    {}\n"
    body = "try:\n        return await repo.get('x')\n    "
    for shape, found in [
        (body + "except Exception:\n        return None", True),
        (body + "except builtins.Exception:\n        return None", True),
        (body + "except (ValueError, BaseException):\n        return None", True),
        (body + "except:\n        return None", True),
        (body + "except StoredValueTooDeep:\n        return None", True),
        ("with contextlib.suppress(Exception):\n        return await repo.get('x')", True),
        ("with suppress(BaseException):\n        return await repo.get('x')", True),
        (body + "except StoredValueTooDeep:\n        raise\n    except Exception:\n        return None", False),
        (body + "except StoredValueTooDeep:\n        if x:\n            raise\n        return None", True),
        (body + "except ValueError:\n        return None", False),
        (
            "try:\n        return await forge_token(resolver, 'r', 'github', '')\n    except Exception:\n        return ''",
            True,
        ),
        ("try:\n        return await resolver.resolve_llm('r', 'p')\n    except Exception:\n        return None", True),
        (
            "try:\n        return await resolve_forge(repos_repo, 'r')\n    except Exception:\n        return None",
            False,
        ),
    ]:
        assert _found_in(read.format(shape)) is found, f"the guard reads this wrong ({found}): {shape!r}"


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
