"""A test that opens the Postgres checkpointer is marked ``changes_schema`` (#437).

``checkpointer_context`` runs ``AsyncPostgresSaver.setup()``, which creates
the checkpoint tables in the test's database: a schema change. A test that
makes one unmarked passes or fails by what ran before it in its process:
the check after it (``tests/fixtures/postgres.py``, ``after_test``) fails it
only when an earlier database test left a digest of the schema to compare
with. So the mark is checked here, where the order cannot hide it.

The check reads each test module's syntax tree, not its text, so a comment
naming the mark does not count and an alias does not escape:

* A use is any reference to ``checkpointer_context``: a call, an attribute
  (``checkpoint.checkpointer_context``), or a name bound to it by an import
  (``import ... as``) or an assignment, wherever that name is used.
* A function that uses it, or uses such a function or fixture, reaches it.
  A test reaches it the same way, through its body, its decorators or its
  fixtures. A ``pytest.param(..., marks=pytest.mark.changes_schema)`` is the
  marked case itself, so what it names does not make its test reach.
* A test that reaches it must carry the mark: the module's ``pytestmark``,
  its class's, or its own decorator. A ``test_migration*`` module is marked
  by name (``tests/conftest.py``).

A string naming it, as ``patch("...checkpointer_context", stub)`` does,
replaces the checkpointer with a stub and is not a use.

What it cannot see is a test that opens the checkpointer only through
production code: ``test_restart_resume.py``'s drained-job test does, through
``IssueExecutor``. Its module is marked whole, which covers it.
"""

from __future__ import annotations

import ast
import pathlib
import textwrap
from collections.abc import Collection

TESTS = pathlib.Path(__file__).resolve().parents[1]
NAME = "checkpointer_context"
MARK = "changes_schema"


def _is_mark(node: ast.AST) -> bool:
    """Whether ``node`` is ``<...>.mark.changes_schema``, or a call of it."""
    if isinstance(node, ast.Call):
        node = node.func
    return (
        isinstance(node, ast.Attribute)
        and node.attr == MARK
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
    )


def _marks(value: ast.AST) -> bool:
    """Whether a ``pytestmark`` value carries the mark: the mark, or a list or tuple holding it."""
    if isinstance(value, ast.List | ast.Tuple):
        return any(_is_mark(element) for element in value.elts)
    return _is_mark(value)


def _pytestmark(body: list[ast.stmt]) -> bool:
    """Whether a module's or a class's body assigns ``pytestmark`` a value carrying the mark."""
    for statement in body:
        if isinstance(statement, ast.Assign):
            targets, value = statement.targets, statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets, value = [statement.target], statement.value
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets) and _marks(value):
            return True
    return False


def _marked_param(node: ast.AST) -> bool:
    """Whether ``node`` is ``pytest.param(..., marks=<the mark>)``: a case that carries the mark itself."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "param"
        and any(k.arg == "marks" and _marks(k.value) for k in node.keywords)
    )


def _names_used(node: ast.AST) -> set[str]:
    """Every name and attribute ``node`` reads, leaving out what a marked ``pytest.param`` names."""
    found: set[str] = set()
    stack = [node]
    while stack:
        current = stack.pop()
        if _marked_param(current):
            continue
        if isinstance(current, ast.Name):
            found.add(current.id)
        elif isinstance(current, ast.Attribute):
            found.add(current.attr)
        elif isinstance(current, ast.alias) and current.name.split(".")[-1] == NAME:
            found.add(NAME)
        stack.extend(ast.iter_child_nodes(current))
    return found


def _aliases(tree: ast.Module) -> set[str]:
    """``NAME`` and every name bound to it, by an import or an assignment, at any depth."""
    names = {NAME}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            bound: list[str] = []
            if isinstance(node, ast.ImportFrom):
                bound = [a.asname or a.name for a in node.names if a.name == NAME]
            elif isinstance(node, ast.Assign) and _names_used(node.value) & names:
                bound = [t.id for t in node.targets if isinstance(t, ast.Name)]
            for name in bound:
                if name not in names:
                    names.add(name)
                    changed = True
    return names


def _functions(tree: ast.Module) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.ClassDef | None]]:
    """The module's functions and its classes' methods, each with its class."""
    found: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.ClassDef | None]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            found.append((node, None))
        elif isinstance(node, ast.ClassDef):
            found += [(n, node) for n in node.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]
    return found


def _parameters(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    args = function.args
    return {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}


def _reaching(tree: ast.Module, outside: Collection[str] = ()) -> set[str]:
    """The names of the functions that reach the checkpointer, through each other and their fixtures.

    ``outside`` names fixtures from a ``conftest.py`` that reach it.
    """
    targets = _aliases(tree) | set(outside)
    functions = _functions(tree)
    reaching: set[str] = set()
    changed = True
    while changed:
        changed = False
        for function, _cls in functions:
            if function.name in reaching:
                continue
            if _names_used(function) & (targets | reaching) or _parameters(function) & (targets | reaching):
                reaching.add(function.name)
                changed = True
    return reaching


def unmarked_tests(source: str, *, module: str = "test_x.py", outside: Collection[str] = ()) -> list[str]:
    """The tests in ``source`` that reach the checkpointer and do not carry the mark."""
    tree = ast.parse(source)
    if module.startswith("test_migration") or _pytestmark(tree.body):
        return []
    reaching = _reaching(tree, outside)
    unmarked = []
    for function, cls in _functions(tree):
        if not function.name.startswith("test") or function.name not in reaching:
            continue
        if any(_is_mark(d) for d in function.decorator_list):
            continue
        if cls is not None and (any(_is_mark(d) for d in cls.decorator_list) or _pytestmark(cls.body)):
            continue
        unmarked.append(f"{cls.name}.{function.name}" if cls is not None else function.name)
    return unmarked


def _modules() -> list[pathlib.Path]:
    return [p for p in sorted(TESTS.rglob("*.py")) if p.resolve() != pathlib.Path(__file__).resolve()]


def _conftest_fixtures() -> set[str]:
    """Fixtures in any ``conftest.py`` that reach the checkpointer: a test reaches it by taking one."""
    found: set[str] = set()
    for path in _modules():
        if path.name == "conftest.py":
            found |= _reaching(ast.parse(path.read_text()))
    return found


def test_every_test_that_opens_the_checkpointer_is_marked_changes_schema() -> None:
    outside = _conftest_fixtures()
    using = [p for p in _modules() if _reaching(ast.parse(p.read_text()), outside)]
    unmarked = {
        str(p.relative_to(TESTS)): found
        for p in using
        if (found := unmarked_tests(p.read_text(), module=p.name, outside=outside))
    }
    assert using, "no test module opens the checkpointer, so this check measures nothing"
    assert unmarked == {}, f"these tests open the Postgres checkpointer and are not marked changes_schema: {unmarked}"


# ---- the check itself, on modules written for it ------------------------------------------------------


def _unmarked(source: str, *, module: str = "test_x.py", outside: Collection[str] = ()) -> list[str]:
    return unmarked_tests(textwrap.dedent(source), module=module, outside=outside)


def test_a_comment_naming_the_mark_does_not_mark_the_module() -> None:
    assert _unmarked(
        """
        import pytest
        from embry0.orchestration.checkpoint import checkpointer_context
        pytestmark = pytest.mark.requires_postgres  # no pytest.mark.changes_schema needed here

        async def test_x(url):
            async with checkpointer_context(url):
                pass
        """
    ) == ["test_x"], "a comment counted as the mark"


def test_an_alias_does_not_escape() -> None:
    attribute = """
        from embry0.orchestration import checkpoint

        async def test_x(url):
            opener = checkpoint.checkpointer_context
            async with opener(url):
                pass
        """
    imported = """
        from embry0.orchestration.checkpoint import checkpointer_context as opener

        def _open(url):
            return opener(url)

        @pytest.fixture
        def saver(url):
            return _open(url)

        async def test_x(saver):
            pass
        """
    module_level = """
        from embry0.orchestration import checkpoint
        OPEN = checkpoint.checkpointer_context

        async def test_x(url):
            async with OPEN(url):
                pass
        """
    assert _unmarked(attribute) == ["test_x"], "an attribute bound to a name escaped the check"
    assert _unmarked(module_level) == ["test_x"], "a name bound to it at the module's level escaped the check"
    assert _unmarked(imported) == ["test_x"], "an import alias reached through a helper and a fixture escaped"
    assert _unmarked("async def test_x(conftest_saver):\n    pass\n", outside={"conftest_saver"}) == ["test_x"], (
        "a conftest fixture that opens the checkpointer escaped"
    )


def test_each_way_of_marking_a_test_is_read() -> None:
    marked = [
        "import pytest\npytestmark = [pytest.mark.requires_postgres, pytest.mark.changes_schema]\n",
        "import pytest\n@pytest.mark.changes_schema\n",
    ]
    body = "async def test_x(url):\n    async with checkpointer_context(url):\n        pass\n"
    for head in marked:
        assert _unmarked(head + body) == [], f"a marked test was flagged:\n{head}"
    assert _unmarked(body, module="test_migration_x.py") == [], "a test_migration* module is marked by name"
    in_class = "import pytest\n@pytest.mark.changes_schema\nclass TestX:\n    async def test_x(self, url):\n" + (
        "        async with checkpointer_context(url):\n            pass\n"
    )
    assert _unmarked(in_class) == [], "a marked class was flagged"
    param = """
        import pytest

        async def _postgres(url):
            async with checkpointer_context(url):
                pass

        async def _memory(url):
            pass

        @pytest.mark.parametrize("open_", [_memory, pytest.param(_postgres, marks=pytest.mark.changes_schema)])
        async def test_x(open_):
            await open_("u")
        """
    assert _unmarked(param) == [], "a case marked in its pytest.param was flagged"
    stub = 'from unittest.mock import patch\n\ndef test_x():\n    with patch("a.checkpointer_context", None):\n        pass\n'
    assert _unmarked(stub) == [], "a patch naming the checkpointer in a string counted as opening it"
