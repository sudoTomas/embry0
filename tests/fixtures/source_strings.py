"""What a module of ``embry0/`` spells, for the tests that keep a path or a module out of the code.

A test that looks for a path in the source text misses it once it is pieced
together (``".embry0/" + "areas.yml"``), held in a constant, put in an
f-string, joined as a path or URL-encoded (``.embry0%2Fareas.yml``, as
GitLab's files API wants it); and a test that looks for one form of import
misses the others. :func:`strings_in` folds what a module spells into the
strings it makes, as far as constants go, and :func:`imports_of` finds every
way a module imports another.
"""

from __future__ import annotations

import ast
import pathlib
import re
from collections.abc import Iterator
from urllib.parse import unquote

#: What a part of a string that is not a constant stands for.
UNKNOWN = "\x00"
#: The calls that join their arguments as a path.
_PATH_CALLS = {"join", "Path", "PurePath", "PurePosixPath"}
#: The calls whose result spells their first argument (URL-encoded, or only typed).
_SAME_CALLS = {"quote", "quote_plus", "str"}
#: A placeholder of ``%`` formatting, and of ``str.format``.
_PERCENT = re.compile(r"%[-#0 +]*\d*(?:\.\d+)?[sdrfx]")
_BRACES = re.compile(r"\{[^{}]*\}")


def _name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


class _Folder:
    def __init__(self, tree: ast.Module) -> None:
        self.constants: dict[str, str] = {}
        for node in tree.body:
            target, value = None, None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign):
                target, value = node.target, node.value
            if isinstance(target, ast.Name) and value is not None:
                folded = self.fold(value)
                if folded is not None:
                    self.constants[target.id] = folded

    def fold(self, node: ast.expr) -> str | None:
        """The string ``node`` makes, with :data:`UNKNOWN` for each part that is not a constant; None when it makes
        no string this can tell."""
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else None
        if isinstance(node, ast.Name):
            return self.constants.get(node.id)
        if isinstance(node, ast.JoinedStr):
            return "".join(
                (value.value if isinstance(value, ast.Constant) else None)
                or (self.fold(value.value) if isinstance(value, ast.FormattedValue) else None)
                or UNKNOWN
                for value in node.values
            )
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            template = self.fold(node.left)
            if template is None:
                return None
            values = node.right.elts if isinstance(node.right, ast.Tuple) else [node.right]
            return self._fill(_PERCENT, template, [self.fold(value) for value in values])
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Div):
            left, right = self.fold(node.left), self.fold(node.right)
            if left is None and right is None:
                return None
            joint = "/" if isinstance(node.op, ast.Div) else ""
            return (left or UNKNOWN) + joint + (right or UNKNOWN)
        if isinstance(node, ast.Call):
            called = _name(node.func)
            parts = [self.fold(arg) for arg in node.args]
            if called == "format" and isinstance(node.func, ast.Attribute):
                template = self.fold(node.func.value)
                if template is not None:
                    return self._fill(_BRACES, template, parts)
            if called == "join" and isinstance(node.func, ast.Attribute):
                separator = self.fold(node.func.value)
                if separator is not None and len(node.args) == 1 and isinstance(node.args[0], ast.List | ast.Tuple):
                    return separator.join(self.fold(e) or UNKNOWN for e in node.args[0].elts)
            if called in _PATH_CALLS and any(part is not None for part in parts):
                return "/".join(part or UNKNOWN for part in parts)
            if called in _SAME_CALLS and parts and parts[0] is not None:
                return parts[0]
        return None

    @staticmethod
    def _fill(placeholder: re.Pattern[str], template: str, values: list[str | None]) -> str:
        """``template`` with its placeholders filled in order; one past the values, or not a constant, unknown."""
        given = iter(values)
        return placeholder.sub(lambda _match: next(given, None) or UNKNOWN, template)


def strings_in(source: str, *, text: bool = True) -> Iterator[str]:
    """Every string ``source`` spells or makes from constants, URL-decoded and lower-cased; its whole text first,
    comments included, unless ``text`` is False."""
    if text:
        yield unquote(source).lower()
    tree = ast.parse(source)
    folder = _Folder(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.expr):
            folded = folder.fold(node)
            if folded is not None:
                yield unquote(folded).lower()


def spells(source: str, *fragments: str) -> bool:
    """Whether ``source`` spells, or makes from constants, any of ``fragments`` (lower-case)."""
    return any(fragment in text for text in strings_in(source) for fragment in fragments)


def _package(path: pathlib.Path, root: pathlib.Path) -> list[str]:
    """The package a module at ``path`` is in: what its relative imports start from."""
    return list(path.relative_to(root).with_suffix("").parts)[:-1]


def imports_of(path: pathlib.Path, root: pathlib.Path, module: str) -> list[str]:
    """How the module at ``path`` (under ``root``) imports ``module``: each name it takes from it, ``"<module>"``
    for the module itself (``import a.b``, ``from a import b``, relative forms included), and ``"<by name>"`` for
    its dotted name in a string (``importlib.import_module``)."""
    parent, _, leaf = module.rpartition(".")
    source = path.read_text()
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += ["<module>" for alias in node.names if alias.name == module or alias.name.startswith(module + ".")]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                package = _package(path, root)
                package = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                base = ".".join([*package, *([base] if base else [])])
            if base == module:
                found += [alias.name for alias in node.names]
            elif base == parent:
                found += ["<module>" for alias in node.names if alias.name == leaf]
    if any(module in text for text in strings_in(source, text=False)):
        found.append("<by name>")
    return found
