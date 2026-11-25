"""What a module of ``embry0/`` spells, for the tests that keep a path or a module out of the code.

A test that looks for a path in the source text misses it once it is pieced
together (``".embry0/" + "areas.yml"``), held in a constant, put in an
f-string, joined as a path or URL-encoded (``.embry0%2Fareas.yml``, as
GitLab's files API wants it); and a test that looks for one form of import
misses the others. :func:`strings_in` folds what a module spells into the
strings it makes, as far as constants go, following a constant it imports
from another module of the checkout (:class:`Tree`); and :func:`imports_of`
finds every way a module reaches another or a name of it.
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


class Tree:
    """The modules of a checkout at ``root``, for following a constant one module imports from another."""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self._constants: dict[str, dict[str, str]] = {}
        self._reading: set[str] = set()

    def file_of(self, dotted: str) -> pathlib.Path | None:
        base = self.root.joinpath(*dotted.split("."))
        for candidate in (base.with_suffix(".py"), base / "__init__.py"):
            if candidate.is_file():
                return candidate
        return None

    def constants(self, dotted: str) -> dict[str, str]:
        """The string constants the module ``dotted`` defines or imports, folded; {} for none, or one of a cycle."""
        if dotted in self._constants:
            return self._constants[dotted]
        path = self.file_of(dotted)
        if path is None or dotted in self._reading:
            return {}
        self._reading.add(dotted)
        try:
            constants = _Folder(ast.parse(path.read_text()), self, path).constants
        finally:
            self._reading.discard(dotted)
        self._constants[dotted] = constants
        return constants


def _resolved(node: ast.ImportFrom, path: pathlib.Path | None, root: pathlib.Path | None) -> str:
    """The module an ``import from`` names, a relative one resolved from ``path``'s package."""
    base = node.module or ""
    if node.level and path is not None and root is not None:
        package = list(path.relative_to(root).with_suffix("").parts)[:-1]
        package = package[: len(package) - (node.level - 1)] if node.level > 1 else package
        base = ".".join([*package, *([base] if base else [])])
    return base


class _Folder:
    def __init__(self, tree: ast.Module, modules: Tree | None = None, path: pathlib.Path | None = None) -> None:
        self.constants: dict[str, str] = {}
        #: Each name bound to a module of the checkout (``import a.b as m``, ``from a import b``): its dotted name.
        self.modules: dict[str, str] = {}
        self._tree = modules
        if modules is not None:
            self._imports(tree, modules, path)
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

    def _imports(self, tree: ast.Module, modules: Tree, path: pathlib.Path | None) -> None:
        """The constants and modules this module imports from the checkout, wherever it imports them."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname is not None:
                        self.modules[alias.asname] = alias.name
                    else:
                        top = alias.name.split(".")[0]
                        self.modules.setdefault(top, top)
            elif isinstance(node, ast.ImportFrom):
                base = _resolved(node, path, modules.root)
                for alias in node.names:
                    bound = alias.asname or alias.name
                    if modules.file_of(f"{base}.{alias.name}") is not None:
                        self.modules[bound] = f"{base}.{alias.name}"
                    else:
                        value = modules.constants(base).get(alias.name)
                        if value is not None:
                            self.constants[bound] = value

    def _attribute(self, node: ast.Attribute) -> str | None:
        """A constant reached through a module: ``m.NAME``, ``a.b.NAME``."""
        parts = [node.attr]
        value: ast.expr = node.value
        while isinstance(value, ast.Attribute):
            parts.insert(0, value.attr)
            value = value.value
        if self._tree is None or not isinstance(value, ast.Name) or value.id not in self.modules:
            return None
        dotted = ".".join([self.modules[value.id], *parts[:-1]])
        return self._tree.constants(dotted).get(parts[-1])

    def fold(self, node: ast.expr) -> str | None:
        """The string ``node`` makes, with :data:`UNKNOWN` for each part that is not a constant; None when it makes
        no string this can tell."""
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else None
        if isinstance(node, ast.Name):
            return self.constants.get(node.id)
        if isinstance(node, ast.Attribute):
            return self._attribute(node)
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


def strings_in(
    source: str, *, text: bool = True, tree: Tree | None = None, path: pathlib.Path | None = None
) -> Iterator[str]:
    """Every string ``source`` spells or makes from constants, URL-decoded and lower-cased; its whole text first,
    comments included, unless ``text`` is False. Given the checkout (``tree``) and where the module is in it
    (``path``), a constant it imports from another module of the checkout is followed there."""
    if text:
        yield unquote(source).lower()
    parsed = ast.parse(source)
    folder = _Folder(parsed, tree, path)
    for node in ast.walk(parsed):
        if isinstance(node, ast.expr):
            folded = folder.fold(node)
            if folded is not None:
                yield unquote(folded).lower()


def spells(source: str, *fragments: str, tree: Tree | None = None, path: pathlib.Path | None = None) -> bool:
    """Whether ``source`` spells, or makes from constants, any of ``fragments`` (lower-case)."""
    return any(fragment in text for text in strings_in(source, tree=tree, path=path) for fragment in fragments)


def imports_of(path: pathlib.Path, root: pathlib.Path, module: str, names: tuple[str, ...] = ()) -> list[str]:
    """How the module at ``path`` (under ``root``) reaches ``module``: each name it takes from it; ``"<module>"``
    for the module itself (``import a.b``, ``from a import b``, relative forms included); ``"<.leaf>"`` for the
    module reached as an attribute (``import a as m; m.b.f()``); ``"<by name>"`` for its dotted name in a string
    (``importlib.import_module``); and, for each of its ``names`` taken from another module that passes it on
    (``from x import f``), ``"f (from x)"``, or reached as an attribute, ``"<.f>"``."""
    parent, _, leaf = module.rpartition(".")
    source = path.read_text()
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += ["<module>" for alias in node.names if alias.name == module or alias.name.startswith(module + ".")]
        elif isinstance(node, ast.ImportFrom):
            base = _resolved(node, path, root)
            if base == module:
                found += [alias.name for alias in node.names]
            else:
                found += ["<module>" for alias in node.names if base == parent and alias.name == leaf]
                found += [f"{alias.name} (from {base})" for alias in node.names if alias.name in names]
        elif isinstance(node, ast.Attribute) and (node.attr == leaf or node.attr in names):
            found.append(f"<.{node.attr}>")
    if any(module in text for text in strings_in(source, text=False)):
        found.append("<by name>")
    return found
