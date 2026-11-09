"""Regression test for the 2026-05-06 QA-pipeline silent-fail.

The sandbox image's `embry0/execution/` directory is a hand-picked subset
(see infra/Dockerfile.sandbox). When `agent_runner.py` imported
`SandboxManager` at module load, the import chain transitively required
`embry0.execution.image_registry` — a module that the Dockerfile does
NOT copy into the sandbox. The runner crashed with
`ModuleNotFoundError: No module named 'embry0.execution.image_registry'`
before any agent code could run, killing all 14 sub-tasks of every QA
job with no usable diagnostic until orchestrator-side stderr capture
landed earlier in the same session.

Fix: agent_runner.py now guards its SandboxManager import behind
TYPE_CHECKING. This test verifies that constraint stays in place: the
sandbox runner must be importable without any module from
`embry0/execution/` other than the four files the Dockerfile actually
copies (events, agent_runner, auth_provider, docker_client).

The check runs in a subprocess so it can wipe sys.modules without
breaking the test runner's own import cache (notably for fixtures that
hold references to e.g. SandboxInitError).
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
import textwrap
from pathlib import Path

# The exhaustive list of embry0.execution.* modules that the sandbox image
# is allowed to load. Mirrors infra/Dockerfile.sandbox COPY directives.
_SANDBOX_ALLOWED_EXECUTION_MODULES = {
    "embry0.execution",
    "embry0.execution.events",
    "embry0.execution.agent_runner",
    "embry0.execution.auth_provider",
    "embry0.execution.docker_client",
}


def test_sandbox_runner_import_surface_does_not_pull_orchestrator_only_modules():
    """Loading the sandbox runner's transitive deps must not touch
    embry0.execution.* modules outside the Dockerfile's COPY list.

    A new file in embry0/execution/ that gets imported eagerly from any
    sandbox-side module will fail this test, forcing the author to either
    add it to the Dockerfile or guard the import (TYPE_CHECKING /
    function-local import).
    """
    probe = textwrap.dedent(
        """
        import importlib, json, sys
        importlib.import_module("embry0.sandbox.runner")
        importlib.import_module("embry0.execution.agent_runner")
        importlib.import_module("embry0.agents.executor_factory")
        loaded = sorted(
            n for n in sys.modules if n.startswith("embry0.execution.")
        )
        sys.stdout.write(json.dumps(loaded))
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", probe],
        check=True,
        capture_output=True,
        text=True,
    )
    loaded = set(json.loads(result.stdout))
    leaked = sorted(loaded - _SANDBOX_ALLOWED_EXECUTION_MODULES)
    assert not leaked, (
        f"Sandbox runner pulled in {leaked!r}, but those modules are not "
        f"copied into the sandbox image (see infra/Dockerfile.sandbox). "
        f"Either add the file to the Dockerfile's COPY list or guard the "
        f"import behind TYPE_CHECKING / a function-local import."
    )


#: The embry0 packages the sandbox images copy whole. Any other embry0 module
#: (``embry0/json_text.py``, ``embry0/config.py``, ...) is absent from them.
_SANDBOX_COPIED_PACKAGES = ("embry0.sandbox", "embry0.safety", "embry0.agents")

#: Modules under those packages that run in the ORCHESTRATOR, never in a
#: sandbox: none of their imports is the image's concern.
_ORCHESTRATOR_SIDE = {
    # Resolved by sandbox_manager, workspace_init and the QA prep before the
    # sandbox starts; it reads embry0.branding, which the images do not copy.
    "embry0.sandbox.git_identity",
}

#: Imports of a module the images do not copy, made inside a function of a
#: copied module, that only the orchestrator ever calls: ``(file, function,
#: module)``. Each is an exception to the rule, so each says why it holds.
_ORCHESTRATOR_ONLY_CALLS = {
    # Tier models for triage and pr-review jobs, resolved by the orchestrator.
    ("embry0/agents/model_policy.py", "resolve_tier_models", "embry0.config"),
    # The servers embry0 adds for a run and the tool catalog: the orchestrator
    # resolves both when it builds an invocation (they read the workflows).
    ("embry0/agents/resolver.py", "_image_mcp_servers", "embry0.services.image_mcp_servers"),
    ("embry0/agents/resolver.py", "_catalog_names", "embry0.services.tool_catalog"),
    ("embry0/agents/resolver.py", "gated_tools", "embry0.services.tool_catalog"),
    # AgentRunner runs the sandbox from the orchestrator; the sandbox loads
    # agent_runner.py only for AgentOutput and never builds an AgentRunner.
    ("embry0/execution/agent_runner.py", "_exec_runner", "embry0.workflows.qa.watchdogs"),
    ("embry0/execution/agent_runner.py", "_exec_runner", "embry0.execution.sandbox_files"),
    ("embry0/execution/agent_runner.py", "_stage_resume_session", "embry0.execution.sandbox_files"),
}

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_sandbox_images_copy_the_packages_this_test_allows():
    for dockerfile in ("Dockerfile.sandbox", "Dockerfile.sandbox.qa"):
        text = (_REPO_ROOT / "infra" / dockerfile).read_text()
        for package in _SANDBOX_COPIED_PACKAGES:
            path = package.replace(".", "/")
            assert f"COPY {path}/ /app/{path}/" in text, f"{dockerfile} no longer copies {path}/"


def _module_of(path: Path) -> str:
    parts = path.relative_to(_REPO_ROOT).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _copied_files() -> list[Path]:
    files = [
        f
        for package in _SANDBOX_COPIED_PACKAGES
        for f in sorted((_REPO_ROOT / package.replace(".", "/")).rglob("*.py"))
        if _module_of(f) not in _ORCHESTRATOR_SIDE
    ]
    return files + [
        _REPO_ROOT / (name.replace(".", "/") + ".py")
        for name in sorted(_SANDBOX_ALLOWED_EXECUTION_MODULES - {"embry0.execution"})
    ]


def _in_image(module: str) -> bool:
    return (
        module == "embry0"
        or module in _SANDBOX_ALLOWED_EXECUTION_MODULES
        or any(module == p or module.startswith(p + ".") for p in _SANDBOX_COPIED_PACKAGES)
    )


def _target(node: ast.ImportFrom | ast.Import) -> list[str]:
    """The embry0 modules an import statement loads: ``from a.b import c`` loads ``a.b.c`` when that is a module."""
    if isinstance(node, ast.Import):
        return [a.name for a in node.names if a.name.split(".")[0] == "embry0"]
    if node.level or not node.module or node.module.split(".")[0] != "embry0":
        return []
    modules = []
    for alias in node.names:
        sub = f"{node.module}.{alias.name}"
        path = _REPO_ROOT / sub.replace(".", "/")
        modules.append(sub if path.with_suffix(".py").exists() or path.is_dir() else node.module)
    return modules


def _type_checking_only(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _imports(path: Path) -> list[tuple[int, str, str | None, str]]:
    """``(line, module, enclosing function or None, kind)`` for each embry0 import in ``path``.

    ``kind`` is ``"type-checking"`` under ``if TYPE_CHECKING:`` (never run),
    else ``"runs"``. Every statement is visited, at any depth: at module
    level, in a function, a class, a branch or a ``try``.
    """
    found: list[tuple[int, str, str | None, str]] = []

    def visit(node: ast.AST, function: str | None, type_checking: bool) -> None:
        if isinstance(node, ast.Import | ast.ImportFrom):
            for module in _target(node):
                found.append((node.lineno, module, function, "type-checking" if type_checking else "runs"))
            return
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            function = node.name if function is None else function
        if isinstance(node, ast.If) and _type_checking_only(node.test):
            for child in node.body:
                visit(child, function, True)
            for child in node.orelse:
                visit(child, function, type_checking)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, function, type_checking)

    visit(ast.parse(path.read_text()), None, False)
    return found


def test_sandbox_code_imports_no_embry0_module_outside_the_image():
    """No module the sandbox images copy imports an embry0 module they do not.

    Read from the source of every copied module, not by loading it, so an
    import inside a function counts as much as one at module level: it fails
    in the sandbox only when the function runs. Exempt are imports under
    ``if TYPE_CHECKING:``, the modules in :data:`_ORCHESTRATOR_SIDE` and the
    calls in :data:`_ORCHESTRATOR_ONLY_CALLS`. ``embry0/json_text.py`` is not
    copied, so the sandbox-side readers of outside JSON catch its exceptions
    inline instead.
    """
    files = _copied_files()
    assert any(f.name == "context_mcp.py" for f in files) and any(f.name == "mcp_client.py" for f in files), (
        "the check reads none of the copied modules"
    )
    leaked = [
        f"{path.relative_to(_REPO_ROOT)}:{line} imports {module}" + (f" in {function}()" if function else "")
        for path in files
        for line, module, function, kind in _imports(path)
        if kind == "runs"
        and not _in_image(module)
        and (str(path.relative_to(_REPO_ROOT)), function, module) not in _ORCHESTRATOR_ONLY_CALLS
    ]
    assert not leaked, (
        f"{leaked}: the sandbox images do not copy these modules (infra/Dockerfile.sandbox, "
        "infra/Dockerfile.sandbox.qa), so the import fails in a sandbox when it runs"
    )


def test_the_exceptions_are_still_accurate():
    """An exception that no longer matches anything hides nothing, or was renamed; each must name a real import."""
    import importlib.util

    for name in _ORCHESTRATOR_SIDE:
        assert importlib.util.find_spec(name) is not None, f"{name} is excluded but no longer exists"
    for file, function, module in _ORCHESTRATOR_ONLY_CALLS:
        assert importlib.util.find_spec(module) is not None, f"{module} (allowed in {file} {function}) does not exist"
        imports = _imports(_REPO_ROOT / file)
        assert any(m == module and f == function and k == "runs" for _line, m, f, k in imports), (
            f"{file} no longer imports {module} in {function}(): remove the exception"
        )
