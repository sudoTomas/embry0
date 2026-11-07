"""Every ``run_cmd`` call bounds the output its command can make the orchestrator hold.

``DockerClient.run_cmd`` reads at most ``max_bytes`` of a command's stdout,
``DEFAULT_MAX_OUTPUT_BYTES`` unless its caller names another cap, and refuses
one above ``MAX_OUTPUT_CAP_BYTES`` (``test_docker_client.py`` proves the reader
keeps to it). This module pins the call sites, ``run_cmd``, ``run_cmd_status``
(the same reader, for a caller whose answer is the exit status) and the git
steps' ``sandbox_git.run``: each one either names no cap or names one by a
constant (never a literal), and the sites that name one are exactly the ones
listed in ``NAMED_CAPS``, with the cap each was given. ``run_cmd``,
``run_cmd_bytes`` and ``run_cmd_status`` are only ever called directly, so no
alias, ``partial`` or ``getattr`` can carry a call past this check. A new call
site that reads more than the default names a constant, says why in that
constant's comment, and adds its line here.
"""

from __future__ import annotations

import ast
import functools
import importlib
import inspect
from pathlib import Path
from typing import Any

import embry0
from embry0.execution.docker_client import DockerClient

_PACKAGE = Path(embry0.__file__).resolve().parent

# (module, function) -> the caps its run_cmd and sandbox_git.run calls name, in source
# order; ``+tail`` marks keep_tail (only the output's end is kept, the command is not stopped).
NAMED_CAPS: dict[tuple[str, str], list[str]] = {
    # A listing of paths grows with the change: git diff --name-only / --numstat, status, a PR's files.
    ("embry0.execution.workspace_diff", "compute_changed_files_via_diff"): ["GIT_LISTING_MAX_BYTES"],
    ("embry0.execution.workspace_diff", "diff_line_counts"): ["GIT_LISTING_MAX_BYTES"],
    ("embry0.workflows.issue_to_pr.nodes._developer", "_commit_unfinished_work"): ["GIT_LISTING_MAX_BYTES"],
    ("embry0.workflows.pr_review.nodes", "init_pr_review_node"): ["GIT_LISTING_MAX_BYTES"],
    # A lockfile is a file of the repository; a large monorepo's runs to tens of MiB.
    ("embry0.cache.image_builder", "build_qa_image"): [
        "LOCKFILE_MAX_BYTES",
        "LOG_TAIL_BYTES+tail",
        "LOG_TAIL_BYTES+tail",
    ],
    ("embry0.cache.image_builder_cli", "_compute_remote_lockfile_sha"): ["LOCKFILE_MAX_BYTES"],
    ("embry0.workflows.qa.orchestrator", "_init_orchestrator_node_impl"): ["LOCKFILE_MAX_BYTES"],
    ("embry0.workflows.qa._orchestrator_workspace", "stage_workspace_for_provider"): ["LOCKFILE_MAX_BYTES"],
    # An install, a build, a seed script, a login script, an e2e suite: their output is a log.
    ("embry0.cache.volume_warmer", "warm_shared_volume"): ["LOG_TAIL_BYTES+tail"],
    ("embry0.execution.runtime.docker", "DockerRuntime.build_image"): ["LOG_TAIL_BYTES+tail"],
    ("embry0.workflows.qa.subtask_nodes", "seed_node"): ["LOG_TAIL_BYTES+tail"],
    ("embry0.workflows.qa.subtask_nodes", "auth_setup_node"): ["LOG_TAIL_BYTES+tail", "STORAGE_STATE_MAX_BYTES"],
    ("embry0.workflows.qa.subtask_nodes", "e2e_node"): ["LOG_TAIL_BYTES+tail"],
    # The test gate reads back a 1 MiB log tail and a marker line, nothing more.
    ("embry0.workflows.verification.runner", "_run_once"): ["RUN_OUTPUT_MAX_BYTES"],
    # Listings of the daemon's own objects grow with the daemon, not with what one process prints.
    ("embry0.execution.docker_client", "DockerClient.inspect_network"): ["DAEMON_LISTING_MAX_BYTES"],
    ("embry0.execution.runtime.egress", "EgressEnforcer._run_in_dind_netns"): ["DAEMON_LISTING_MAX_BYTES"],
    ("embry0.execution.runtime.docker", "DockerRuntime.list_containers"): ["DAEMON_LISTING_MAX_BYTES"],
    ("embry0.execution.runtime.docker", "DockerRuntime.mounted_sources"): [
        "DAEMON_LISTING_MAX_BYTES",
        "DAEMON_LISTING_MAX_BYTES",
    ],
    ("embry0.execution.runtime.docker", "DockerRuntime.list_volumes"): ["DAEMON_LISTING_MAX_BYTES"],
    ("embry0.execution.runtime.docker", "DockerRuntime.inspect_volumes"): ["DAEMON_LISTING_MAX_BYTES"],
    ("embry0.execution.runtime.docker", "DockerRuntime.list_networks"): ["DAEMON_LISTING_MAX_BYTES"],
    ("embry0.execution.runtime.docker", "DockerRuntime.list_images"): [
        "DAEMON_LISTING_MAX_BYTES",
        "DAEMON_LISTING_MAX_BYTES",
    ],
    ("embry0.execution.runtime.docker", "DockerRuntime.disk_usage"): ["DAEMON_LISTING_MAX_BYTES"],
}

# Helpers that hand run_cmd keyword arguments they build themselves (``**kwargs``): the
# cap their callers name, else the default. sandbox_git.run's callers are checked here as
# call sites, and test_docker_client.py runs both of its paths
# (test_a_git_step_that_floods_its_output_is_stopped_at_the_default_cap,
# test_a_git_listing_reads_past_the_default_cap_up_to_its_own).
FORWARDERS = {("embry0.execution.sandbox_git", "run")}

_METHODS = frozenset({"run_cmd", "run_cmd_bytes", "run_cmd_status"})
#: The methods whose call sites are checked for their cap: run_cmd, and run_cmd_status
#: for a caller whose answer is the exit status.
_CAPPED = frozenset({"run_cmd", "run_cmd_status"})


class _Site:
    def __init__(self, module: str, function: str, call: ast.Call) -> None:
        self.module, self.function, self.call = module, function, call
        self.keywords = {k.arg: k.value for k in call.keywords}

    def __str__(self) -> str:
        return f"{self.module}:{self.function} (line {self.call.lineno})"


class _Collector(ast.NodeVisitor):
    """Every ``<expr>.run_cmd(...)`` and ``<expr>.run_cmd_status(...)`` call, by the function it is in."""

    def __init__(self, module: str) -> None:
        self.module = module
        self.scope: list[tuple[str, str]] = []
        self.sites: list[_Site] = []

    def _enter(self, kind: str, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scope.append((kind, node.name))
        self.generic_visit(node)
        self.scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._enter("class", node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter("def", node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter("def", node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _CAPPED:
            self.sites.append(_Site(self.module, self._function(), node))
        elif _is_git_step(self.module, func):
            self.sites.append(_Site(self.module, self._function(), node))
        self.generic_visit(node)

    def _function(self) -> str:
        """The scope down to its outermost function: a nested helper reports as the function that defines it."""
        names: list[str] = []
        for kind, name in self.scope:
            names.append(name)
            if kind == "def":
                break
        return ".".join(names) or "<module>"


def _is_git_step(module: str, func: ast.expr) -> bool:
    """``sandbox_git.run(...)``, or ``run(...)`` inside sandbox_git itself."""
    if isinstance(func, ast.Attribute):
        return func.attr == "run" and isinstance(func.value, ast.Name) and func.value.id == "sandbox_git"
    return module == "embry0.execution.sandbox_git" and isinstance(func, ast.Name) and func.id == "run"


@functools.cache
def _modules() -> list[tuple[str, ast.Module]]:
    found: list[tuple[str, ast.Module]] = []
    for path in sorted(_PACKAGE.rglob("*.py")):
        module = ".".join(path.relative_to(_PACKAGE.parent).with_suffix("").parts)
        found.append((module, ast.parse(path.read_text(encoding="utf-8"))))
    return found


def _sites() -> list[_Site]:
    sites: list[_Site] = []
    for module, tree in _modules():
        collector = _Collector(module)
        collector.visit(tree)
        sites += collector.sites
    # A guard that finds nothing measures nothing.
    assert len(sites) > 80, f"found only {len(sites)} run_cmd calls; the collector is broken"
    assert any(s.module == "embry0.execution.sandbox_git" for s in sites), "sandbox_git.run's call was not found"
    assert any(s.module == "embry0.execution.workspace_diff" for s in sites), "no sandbox_git.run call site was found"
    return sites


def _value(site: _Site, node: ast.expr) -> Any:
    """The value a cap expression has in its module (a name or an attribute of one)."""
    return eval(compile(ast.Expression(node), str(site), "eval"), vars(importlib.import_module(site.module)))  # noqa: S307 — names from embry0's own source


def _default_cap() -> int:
    """The cap a call site gets when it names none: one default, the same for every method checked here."""
    defaults: dict[str, Any] = {}
    for name in sorted(_CAPPED):
        method = getattr(DockerClient, name, None)
        assert method is not None, f"DockerClient.{name} is missing, so its call sites cannot be checked"
        param = inspect.signature(method).parameters.get("max_bytes")
        assert param is not None, (
            f"{name} must cap the stdout it holds (max_bytes), or every call site reads without one"
        )
        assert isinstance(param.default, int) and param.default > 0, f"{name}'s default cap must be positive: {param}"
        defaults[name] = param.default
    assert len(set(defaults.values())) == 1, f"the checked methods must share one default cap: {defaults}"
    return defaults["run_cmd"]


def test_every_run_cmd_call_takes_the_default_cap_or_names_one() -> None:
    from embry0.execution import docker_client

    default = _default_cap()
    ceiling = getattr(docker_client, "MAX_OUTPUT_CAP_BYTES", None)
    assert isinstance(ceiling, int), "docker_client must define MAX_OUTPUT_CAP_BYTES, the largest cap run_cmd takes"
    problems: list[str] = []
    for site in _sites():
        if None in site.keywords and (site.module, site.function) not in FORWARDERS:
            problems.append(f"{site}: passes **kwargs to run_cmd, so its cap cannot be read here")
        cap = site.keywords.get("max_bytes")
        tail = site.keywords.get("keep_tail")
        if tail is not None and not (isinstance(tail, ast.Constant) and tail.value is True):
            problems.append(f"{site}: keep_tail is written as {ast.unparse(tail)!r}, not True")
        if cap is None:
            if tail is not None:
                problems.append(f"{site}: keep_tail without a named tail size (max_bytes)")
            continue
        if not isinstance(cap, (ast.Name, ast.Attribute)):
            problems.append(f"{site}: max_bytes={ast.unparse(cap)} is not a named constant")
            continue
        value = _value(site, cap)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= ceiling:
            problems.append(f"{site}: max_bytes={ast.unparse(cap)} is {value!r}, not 1..{ceiling}")
        elif value == default:
            problems.append(f"{site}: names a cap equal to the default; leave it out")
    assert not problems, "\n".join(problems)


def test_the_call_sites_that_name_a_cap_are_the_listed_ones() -> None:
    _default_cap()
    found: dict[tuple[str, str], list[str]] = {}
    for site in _sites():
        cap = site.keywords.get("max_bytes")
        if cap is None:
            continue
        name = ast.unparse(cap).rsplit(".", 1)[-1] + ("+tail" if "keep_tail" in site.keywords else "")
        found.setdefault((site.module, site.function), []).append(name)
    missing = {k: v for k, v in NAMED_CAPS.items() if found.get(k) != v}
    extra = {k: v for k, v in found.items() if k not in NAMED_CAPS}
    assert not missing and not extra, (
        f"call sites whose cap differs from NAMED_CAPS: {missing} (found {[found.get(k) for k in missing]}); "
        f"call sites naming a cap that NAMED_CAPS does not list: {extra}"
    )


def test_run_cmd_is_only_ever_called_directly() -> None:
    """An alias (``run = docker.run_cmd``), a ``partial`` or a ``getattr`` would carry a call past the checks above."""
    problems: list[str] = []
    for module, tree in _modules():
        called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in _METHODS and id(node) not in called:
                problems.append(f"{module} line {node.lineno}: {ast.unparse(node)} is used without being called")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"getattr", "setattr", "hasattr", "delattr"}
                and any(isinstance(a, ast.Constant) and a.value in _METHODS for a in node.args)
            ):
                problems.append(f"{module} line {node.lineno}: {ast.unparse(node)} reaches run_cmd by name")
    assert not problems, "\n".join(problems)
