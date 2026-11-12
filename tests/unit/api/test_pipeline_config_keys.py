"""Every key embry0 writes into a job's ``pipeline_config`` is one a request may not set, or one ``POST /jobs`` writes
from a field of its own.

``POST /jobs`` stores the request's ``pipeline_config`` on the job, and the
executor reads a job's ``pipeline_config`` for what steers it. So a key embry0
writes itself, for work it started and checked, must not come from a request
(``embry0.api.schemas.INTERNAL_PIPELINE_KEYS``), unless ``POST /jobs`` writes it
from one of its own fields, the same value held to the same bound
(``CLIENT_PIPELINE_KEYS``).

The walk reads embry0's source: every call that passes a ``pipeline_config``
(a keyword, or a ``**`` mapping naming it), and the keys of the dicts its value
is built from, through the variables it is assigned and the functions it is
returned by. A value passed through from a parameter is followed to where its
keys come from (:data:`PASSED_THROUGH`). A new key fails until it is placed.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path

import embry0
from embry0.api.schemas import CLIENT_PIPELINE_KEYS, INTERNAL_PIPELINE_KEYS

ROOT = Path(embry0.__file__).parent

#: A ``pipeline_config`` a function passes on from one of its parameters, and the functions its keys come from.
PASSED_THROUGH: dict[tuple[str, str], tuple[str, ...]] = {
    # A pull request review's frozen config.
    ("services/issue_executor.py", "start_pr_review_job"): ("build_review_config", "apply_run_with"),
    # A follow-up's: its callers send a Run with choice.
    ("services/comment_dispatch.py", "start_followup"): ("apply_run_with",),
}

#: Keys the walk must find, so it is shown to read each kind of writer.
FOUND_AT_LEAST = frozenset(
    {
        "run_with",
        "agent_models_override",
        "proposal_id",
        "open_pr",
        "context_fingerprint",
        "head_sha",
        "local_first_model",
        "budget_overrun_mode",
        "actor",
        "pipeline",
        "skip_smoke",
        "review_id",
        "max_budget_usd",
        "execution_mode_override",
    }
)


def _functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


class Source:
    """Every function of a set of modules, by name, and what the walk reads of them."""

    def __init__(self, modules: dict[str, ast.Module]) -> None:
        self.modules = modules
        self.by_name: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
        for tree in modules.values():
            for fn in _functions(tree):
                self.by_name.setdefault(fn.name, []).append(fn)

    def returned_keys(self, name: str, seen: frozenset[str]) -> set[str]:
        """The keys of the dicts the functions called ``name`` return, followed through their variables."""
        if name in seen:
            return set()
        out: set[str] = set()
        for fn in self.by_name.get(name, []):
            for node in ast.walk(fn):
                if isinstance(node, ast.Return) and node.value is not None:
                    out |= self.keys(node.value, fn, seen | {name})
        return out

    def keys(self, expr: ast.AST, fn: ast.AST, seen: frozenset[str] = frozenset()) -> set[str]:
        """The literal keys ``expr``, read inside ``fn``, may give a ``pipeline_config``."""
        out: set[str] = set()
        if isinstance(expr, ast.Dict):
            for key, value in zip(expr.keys, expr.values, strict=True):
                if key is None:
                    out |= self.keys(value, fn, seen)
                elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                    out.add(key.value)
        elif isinstance(expr, ast.BinOp | ast.BoolOp | ast.IfExp):
            for child in ast.iter_child_nodes(expr):
                out |= self.keys(child, fn, seen)
        elif isinstance(expr, ast.Name):
            out |= self.assigned_keys(expr.id, fn, seen)
        elif isinstance(expr, ast.Call):
            callee = expr.func.attr if isinstance(expr.func, ast.Attribute) else getattr(expr.func, "id", "")
            if callee == "get" and isinstance(expr.func, ast.Attribute):
                # A stored value read back (a job's own config, a row's field): what it holds was written where it
                # was stored, which the walk reads there.
                return out
            out |= self.returned_keys(callee, seen)
            for arg in [*expr.args, *(k.value for k in expr.keywords)]:
                out |= self.keys(arg, fn, seen)
        return out

    def assigned_keys(self, name: str, fn: ast.AST, seen: frozenset[str]) -> set[str]:
        """The keys every assignment to ``name`` in ``fn`` gives it, its ``name["key"] = ...`` included."""
        variable = f"{id(fn)}:{name}"
        if variable in seen:
            return set()
        seen = seen | {variable}
        out: set[str] = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        out |= self.keys(node.value, fn, seen)
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == name
                        and isinstance(target.slice, ast.Constant)
                        and isinstance(target.slice.value, str)
                    ):
                        out.add(target.slice.value)
        return out


def _parameters(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    args = fn.args
    return {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}


def _assignments(fn: ast.AST, name: str) -> list[ast.AST]:
    """The values assigned to ``name`` in ``fn``."""
    return [
        node.value
        for node in ast.walk(fn)
        if isinstance(node, ast.Assign | ast.AnnAssign)
        and node.value is not None
        and any(
            isinstance(t, ast.Name) and t.id == name
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
    ]


def _mapping_names(expr: ast.AST, fn: ast.FunctionDef | ast.AsyncFunctionDef, seen: frozenset[str]) -> set[str]:
    """The parameters of ``fn`` whose mapping ``expr`` is built on: the expression itself, a ``**`` in a dict, either
    side of ``|``, ``or`` or a conditional, and the first argument of a call that copies a mapping
    (``apply_run_with(config, ...)``, ``dict(config)``), followed through the variables it is assigned to."""
    if isinstance(expr, ast.Name):
        if expr.id in seen:
            return set()
        values = _assignments(fn, expr.id)
        if not values:
            return {expr.id} if expr.id in _parameters(fn) else set()
        return {n for value in values for n in _mapping_names(value, fn, seen | {expr.id})}
    if isinstance(expr, ast.Dict):
        return {
            n
            for key, value in zip(expr.keys, expr.values, strict=True)
            if key is None
            for n in _mapping_names(value, fn, seen)
        }
    if isinstance(expr, ast.BinOp | ast.BoolOp):
        return {n for child in ast.iter_child_nodes(expr) for n in _mapping_names(child, fn, seen)}
    if isinstance(expr, ast.IfExp):
        return _mapping_names(expr.body, fn, seen) | _mapping_names(expr.orelse, fn, seen)
    if isinstance(expr, ast.Call) and expr.args:
        return _mapping_names(expr.args[0], fn, seen)
    return set()


def _passed(call: ast.Call) -> Iterator[ast.AST]:
    """The values ``call``, a job's ``create`` or ``update``, passes as its ``pipeline_config``: a keyword, or a
    ``**{"pipeline_config": ...}``."""
    if not (isinstance(call.func, ast.Attribute) and call.func.attr in ("create", "update")):
        return
    for keyword in call.keywords:
        if keyword.arg == "pipeline_config":
            yield keyword.value
        elif keyword.arg is None:
            for node in ast.walk(keyword.value):
                if isinstance(node, ast.Dict):
                    for key, value in zip(node.keys, node.values, strict=True):
                        if isinstance(key, ast.Constant) and key.value == "pipeline_config":
                            yield value


def written_keys(modules: dict[str, ast.Module]) -> tuple[dict[str, set[str]], list[str]]:
    """Each key written into a job's ``pipeline_config``, with where; and each value passed through from a parameter
    that :data:`PASSED_THROUGH` does not place."""
    source = Source(modules)
    found: dict[str, set[str]] = {}
    unplaced: list[str] = []
    for path, tree in modules.items():
        for fn in _functions(tree):
            for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
                for value in _passed(call):
                    where = f"{path}:{call.lineno} {fn.name}"
                    for key in source.keys(value, fn):
                        found.setdefault(key, set()).add(where)
                    params = _mapping_names(value, fn, frozenset())
                    if not params:
                        continue
                    origins = PASSED_THROUGH.get((path, fn.name))
                    if origins is None:
                        unplaced.append(f"{where} passes {sorted(params)}")
                        continue
                    for origin in origins:
                        for key in source.returned_keys(origin, frozenset()):
                            found.setdefault(key, set()).add(f"{where} via {origin}")
    return found, unplaced


def package_modules(paths: Iterable[Path] | None = None) -> dict[str, ast.Module]:
    files = paths if paths is not None else ROOT.rglob("*.py")
    return {
        str(path.relative_to(ROOT)): ast.parse(path.read_text(), str(path))
        for path in files
        if "migrations" not in path.parts
    }


def unplaced_keys(found: dict[str, set[str]]) -> dict[str, list[str]]:
    placed = set(INTERNAL_PIPELINE_KEYS) | set(CLIENT_PIPELINE_KEYS)
    return {key: sorted(where) for key, where in sorted(found.items()) if key not in placed}


def test_every_key_embry0_writes_is_refused_to_a_request_or_one_of_post_jobs_own_fields() -> None:
    found, unplaced = written_keys(package_modules())
    assert unplaced == [], (
        f"pipeline_config values passed through from a parameter, whose keys the walk cannot read: {unplaced}; "
        "list each in PASSED_THROUGH with the functions its keys come from"
    )
    missed = sorted(FOUND_AT_LEAST - set(found))
    assert missed == [], f"the walk does not find these keys embry0 writes, so it measures less than it says: {missed}"
    assert unplaced_keys(found) == {}, (
        "keys embry0 writes into a job's pipeline_config that a request may still set: add each to "
        "embry0.api.schemas.INTERNAL_PIPELINE_KEYS, or to CLIENT_PIPELINE_KEYS with the field POST /jobs writes it from"
    )
    stale = sorted(set(INTERNAL_PIPELINE_KEYS) - set(found) - {"areas_proposal_id"})
    assert stale == [], f"listed as embry0's but written nowhere: {stale}"


def test_the_requests_test_calls_every_refused_key() -> None:
    """test_pipeline_config_refused.py sends each key of INTERNAL_PIPELINE_KEYS, and no other, as a refused one."""
    from tests.unit.api.test_pipeline_config_refused import SAMPLE

    assert sorted(SAMPLE) == sorted(INTERNAL_PIPELINE_KEYS)


def test_the_walk_finds_a_new_key_written_into_a_job_s_pipeline_config() -> None:
    """Not quiet by construction: a planted writer of each shape is found, and a pass-through it cannot follow is
    reported."""
    planted = ast.parse(
        "async def start(jobs, cap, given):\n"
        "    config = {'max_budget_usd': cap}\n"
        "    config['planted_switch'] = True\n"
        "    await jobs.create(repo='acme/widgets', pipeline_config=_extra(config))\n"
        "    await jobs.create(**({'pipeline_config': {'planted_literal': 1}} if cap else {}))\n"
        "    await jobs.create(pipeline_config=given)\n"
        "def _extra(config):\n"
        "    out = dict(config)\n"
        "    out['planted_returned'] = 1\n"
        "    return out\n"
    )
    found, unplaced = written_keys({"services/planted.py": planted})
    assert unplaced_keys(found) == {
        "planted_literal": ["services/planted.py:5 start"],
        "planted_returned": ["services/planted.py:4 start"],
        "planted_switch": ["services/planted.py:4 start"],
    }, unplaced_keys(found)
    assert unplaced == ["services/planted.py:6 start passes ['given']"], unplaced
