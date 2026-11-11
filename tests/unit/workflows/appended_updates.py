"""Find state updates that hand an appended key its own stored list again (#434).

``JobState.errors`` and ``JobState.agent_outputs`` are ``Annotated[list,
operator.add]``: LangGraph adds whatever list a node returns to the stored
one, so an update that carries the stored list stores it twice. This reads
the source, not a run, so a path no test drives is still checked.

Where it looks: the functions the graphs built on ``JobState`` run as nodes
(``builder.add_node(name, fn)`` beside ``StateGraph(JobState)``), and every
function they call, by name, in the sources given. A function only another
state type's graph reaches (the QA sub-task graph's ``SubTaskState``, whose
lists have no reducer and are replaced) is not looked at.

What is an update: a dict, literal or ``dict(...)``, whose keys are all
``JobState`` fields, and a dict variable whose keys (from its literal, its
``x[key] = ...`` and its ``x.update(...)``) all are. A dict with another key
(an event's ``type``, a summary's) is not an update, nor is one that spreads
the state (``{**state, ...}``, a copy of it).

What is an echo: an appended key's value that carries the stored value of
the same key, ``s.get(key)`` or ``s[key]`` for a parameter ``state`` or
``<anything>_state``, through ``+``, ``or``, a list or tuple, ``*``,
``list(...)``, ``.copy()``, a slice (``x[:]``) or an entry (``x[-1]``), a
comprehension over it, a conditional, a local variable that holds it, or a
call of a function that returns it; and ``x[key] += <stored>``. A key may
be a local name holding the key's string. The stored value inside anything
else (its length in a message, a comparison) is not carried.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

Function = ast.FunctionDef | ast.AsyncFunctionDef
_CARRIERS = {"list", "tuple", "sorted"}


@dataclass
class _Scope:
    """What one function's body says about names: the state's parameters, key names, and carriers."""

    states: set[str]
    keys: dict[str, str] = field(default_factory=dict)
    carriers: dict[str, set[str]] = field(default_factory=dict)


def _functions(tree: ast.Module) -> Iterator[Function]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


def _called(node: ast.AST) -> set[str]:
    """The names ``node`` calls: ``f(...)`` and ``x.f(...)``."""
    names: set[str] = set()
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            if isinstance(call.func, ast.Name):
                names.add(call.func.id)
            elif isinstance(call.func, ast.Attribute):
                names.add(call.func.attr)
    return names


def _referenced(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)
    }


def graph_nodes(trees: Iterable[ast.Module], state_type: str) -> set[str]:
    """The functions the graphs built on ``state_type`` run as nodes."""
    nodes: set[str] = set()
    for tree in trees:
        for scope in [tree, *_functions(tree)]:
            body = ast.Module(body=list(getattr(scope, "body", [])), type_ignores=[])
            builds = any(
                isinstance(c, ast.Call)
                and isinstance(c.func, ast.Name)
                and c.func.id == "StateGraph"
                and c.args
                and isinstance(c.args[0], ast.Name)
                and c.args[0].id == state_type
                for c in ast.walk(body)
            )
            if not builds:
                continue
            for call in ast.walk(body):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "add_node"
                    and call.args
                ):
                    nodes |= _referenced(call.args[1] if len(call.args) > 1 else call.args[0])
    return nodes


def _scope(function: Function) -> _Scope:
    args = function.args
    params = [a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]]
    scope = _Scope(states={p for p in params if p == "state" or p.endswith("_state")})
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    scope.keys[target.id] = node.value.value
    return scope


def _key(node: ast.AST | None, scope: _Scope) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return scope.keys.get(node.id)
    return None


def _stored(node: ast.AST, key: str, scope: _Scope) -> bool:
    """Whether ``node`` is the stored value of ``key``: ``s.get(key, ...)`` or ``s[key]`` for a state ``s``."""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in scope.states
        and node.args
    ):
        return _key(node.args[0], scope) == key
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id in scope.states
        and _key(node.slice, scope) == key
    )


def _carries(node: ast.AST, key: str, scope: _Scope, helpers: dict[str, set[str]]) -> bool:
    """Whether the value ``node`` carries the stored value of ``key`` into an update."""
    if _stored(node, key, scope):
        return True
    if isinstance(node, ast.Name):
        return node.id in scope.carriers.get(key, set())
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _carries(node.left, key, scope, helpers) or _carries(node.right, key, scope, helpers)
    if isinstance(node, ast.BoolOp):
        return any(_carries(v, key, scope, helpers) for v in node.values)
    if isinstance(node, ast.List | ast.Tuple):
        return any(_carries(e, key, scope, helpers) for e in node.elts)
    if isinstance(node, ast.Starred):
        return _carries(node.value, key, scope, helpers)
    if isinstance(node, ast.IfExp):
        return _carries(node.body, key, scope, helpers) or _carries(node.orelse, key, scope, helpers)
    if isinstance(node, ast.Await):
        return _carries(node.value, key, scope, helpers)
    if isinstance(node, ast.Subscript):
        # A slice of it (``x[:]``) holds its entries, and an entry of it (``x[-1]``) is one stored again.
        return _carries(node.value, key, scope, helpers)
    if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp):
        # A comprehension over it (``[e for e in x]``) holds its entries.
        return any(_carries(g.iter, key, scope, helpers) for g in node.generators)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr == "copy" and not node.args:
            return _carries(node.func.value, key, scope, helpers)
        name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        if name in _CARRIERS:
            return any(_carries(a, key, scope, helpers) for a in node.args)
        passes_state = any(isinstance(a, ast.Name) and a.id in scope.states for a in [*node.args])
        return passes_state and key in helpers.get(name, set())
    return False


def _with_carriers(function: Function, keys: set[str], helpers: dict[str, set[str]]) -> _Scope:
    """``function``'s scope, with the local names that hold a stored value (to a fixpoint)."""
    scope = _scope(function)
    changed = True
    while changed:
        changed = False
        for node in ast.walk(function):
            if not isinstance(node, ast.Assign | ast.AnnAssign) or node.value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for key in keys:
                if not _carries(node.value, key, scope, helpers):
                    continue
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in scope.carriers.setdefault(key, set()):
                        scope.carriers[key].add(target.id)
                        changed = True
    return scope


def _helpers(functions: Iterable[Function], keys: set[str]) -> dict[str, set[str]]:
    """The functions that return a stored value they were given, by name, with its keys."""
    found: dict[str, set[str]] = {}
    changed = True
    while changed:
        changed = False
        for function in functions:
            scope = _with_carriers(function, keys, found)
            for node in ast.walk(function):
                if not isinstance(node, ast.Return) or node.value is None:
                    continue
                for key in keys:
                    if _carries(node.value, key, scope, found) and key not in found.setdefault(function.name, set()):
                        found[function.name].add(key)
                        changed = True
    return found


def _dict_pairs(node: ast.AST, scope: _Scope) -> list[tuple[str | None, ast.AST]] | None:
    """A dict literal's or ``dict(...)``'s (key, value) pairs; None when ``node`` is neither."""
    if isinstance(node, ast.Dict):
        return [(_key(k, scope) if k is not None else None, v) for k, v in zip(node.keys, node.values, strict=True)]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
        return [(k.arg, k.value) for k in node.keywords]
    return None


def _variable_keys(function: Function, scope: _Scope) -> dict[str, set[str | None]]:
    """For each local dict variable, the keys it is given: its literal's, ``x[key] =``'s, ``x.update``'s."""
    keys: dict[str, set[str | None]] = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            pairs = _dict_pairs(node.value, scope)
            for target in targets:
                if isinstance(target, ast.Name) and pairs is not None:
                    keys.setdefault(target.id, set()).update(k for k, _ in pairs if k is not None)
                elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name):
                    keys.setdefault(target.value.id, set()).add(_key(target.slice, scope))
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Subscript):
            if isinstance(node.target.value, ast.Name):
                keys.setdefault(node.target.value.id, set()).add(_key(node.target.slice, scope))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
            and isinstance(node.func.value, ast.Name)
        ):
            given = keys.setdefault(node.func.value.id, set())
            given.update(k.arg for k in node.keywords if k.arg is not None)
            for arg in node.args:
                given.update(k for k, _ in (_dict_pairs(arg, scope) or []) if k is not None)
    return keys


def _echoes_in(
    function: Function, keys: set[str], fields: set[str], helpers: dict[str, set[str]]
) -> list[tuple[int, str]]:
    scope = _with_carriers(function, keys, helpers)
    updates = {name for name, given in _variable_keys(function, scope).items() if given and given <= fields}
    found: list[tuple[int, str]] = []

    def check(line: int, key: str | None, value: ast.AST) -> None:
        if key in keys and _carries(value, key, scope, helpers):
            found.append((line, key))

    for node in ast.walk(function):
        pairs = _dict_pairs(node, scope)
        # A dict that spreads the state (``{**state, ...}``) is a copy of it, not an update.
        copies_state = any(k is None and isinstance(v, ast.Name) and v.id in scope.states for k, v in pairs or [])
        if pairs is not None and not copies_state and {k for k, _ in pairs if k is not None} <= fields:
            for key, value in pairs:
                check(getattr(node, "lineno", 0), key, value)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in updates
                ):
                    check(node.lineno, _key(target.slice, scope), node.value)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Subscript):
            if isinstance(node.target.value, ast.Name) and node.target.value.id in updates:
                key = _key(node.target.slice, scope)
                if key in keys and isinstance(node.op, ast.Add) and _carries(node.value, key, scope, helpers):
                    found.append((node.lineno, key))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in updates
        ):
            for keyword in node.keywords:
                check(node.lineno, keyword.arg, keyword.value)
    return sorted(set(found))


def echoes(sources: dict[str, str], *, keys: set[str], fields: set[str], state_type: str = "JobState") -> list[str]:
    """Every update in ``sources`` (path -> text) that stores an appended key's own list again, as ``path:line key``."""
    trees = {path: ast.parse(text) for path, text in sources.items()}
    index: dict[str, list[tuple[str, Function]]] = {}
    for path, tree in trees.items():
        for function in _functions(tree):
            index.setdefault(function.name, []).append((path, function))
    reached: set[str] = set()
    frontier = graph_nodes(trees.values(), state_type) & set(index)
    while frontier:
        name = frontier.pop()
        reached.add(name)
        for _path, function in index[name]:
            frontier |= (_called(function) & set(index)) - reached
    functions = [(path, f) for name in sorted(reached) for path, f in index[name]]
    helpers = _helpers([f for _p, f in functions], keys)
    return sorted(
        f"{path}:{line} {key}"
        for path, function in functions
        for line, key in _echoes_in(function, keys, fields, helpers)
    )
