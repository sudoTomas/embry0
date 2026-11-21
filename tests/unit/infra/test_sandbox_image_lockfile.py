"""The sandbox images install uv.lock's versions, and check it (#288).

The sandbox images installed the runner's packages from versions written in
their Dockerfiles: `langchain-core>=0.3,<1` ran 0.3.x in sandboxes while CI
tested the lock's 1.x, `anthropic` was in no lock at all, and the exact
`claude-agent-sdk` pin was kept equal to the lock by hand. Now pyproject's
`sandbox` dependency group names the packages, both images install that
group's export with every hash checked, and `check-image-lock.py` fails the
build when what is installed differs from it. These tests pin the group
against the code the images carry, and the recipes against the lock.

The dev stages add Poetry the same way (#645): pyproject's `poetry` group,
locked with the rest, its export installed with every hash checked, and the
stage checked against both groups. No package of any sandbox image is
installed from outside the lock.
"""

from __future__ import annotations

import ast
import re
import shlex
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SANDBOX = ROOT / "infra" / "Dockerfile.sandbox"
SANDBOX_QA = ROOT / "infra" / "Dockerfile.sandbox.qa"


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _instructions(dockerfile: Path) -> list[str]:
    """Dockerfile instructions, one per entry: continuations joined, comments dropped."""
    lines = [ln for ln in dockerfile.read_text(encoding="utf-8").splitlines() if not ln.lstrip().startswith("#")]
    return [" ".join(ins.split()) for ins in "\n".join(lines).replace("\\\n", " ").splitlines() if ins.strip()]


def _commands(instruction: str) -> list[list[str]]:
    """The shell commands of a RUN instruction, split on `&&`, each as argv."""
    body = re.sub(r"^RUN\s+(--mount=\S+\s+)*", "", instruction)
    return [shlex.split(part) for part in body.split("&&") if part.strip()]


def _option(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


def _options(argv: list[str], name: str) -> list[str]:
    """Every value of an option given more than once, in order."""
    return [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == name]


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _sandbox_group() -> list[str]:
    group = _pyproject().get("dependency-groups", {}).get("sandbox")
    assert group, "pyproject.toml has no `sandbox` dependency group naming what the sandbox images install"
    return group


# --- the group against the code the images carry ------------------------------


def _baked_files() -> list[Path]:
    """The embry0 Python files the sandbox images copy in, read from their COPY lines."""
    files: set[Path] = set()
    for dockerfile in (SANDBOX, SANDBOX_QA):
        for ins in _instructions(dockerfile):
            if not ins.startswith("COPY "):
                continue
            for source in ins.split()[1:-1]:
                if source.startswith("--") or not source.startswith("embry0/"):
                    continue
                path = ROOT / source
                files.update(path.rglob("*.py") if path.is_dir() else [path])
    return sorted(files)


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


# What a handler must catch for an import in its `try:` to be optional.
_IMPORT_FAILURES = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}


def _catches_import_failure(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    caught = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(ast.unparse(name).split(".")[-1] in _IMPORT_FAILURES for name in caught)


def _required_imports(tree: ast.AST) -> set[str]:
    """Top-level module names a file imports when it runs.

    Imports under `if TYPE_CHECKING:` never run, and an import in a `try:`
    whose handler catches its failure (ImportError, Exception or a bare
    `except:`) is optional by construction: the code falls back. Every other
    import must succeed in the image: a lazy one inside a function, and one
    in a `try:` that has only a `finally:` or catches something else.
    """
    found: set[str] = set()

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for child in node.orelse:
                visit(child)
            return
        if isinstance(node, ast.Try) and any(_catches_import_failure(h) for h in node.handlers):
            for child in [*node.handlers, *node.orelse, *node.finalbody]:
                visit(child)
            return
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


#: The distributions whose import name is not their name.
_DISTRIBUTION_OF = {"yaml": "pyyaml"}


def _third_party_imports() -> dict[str, set[str]]:
    """Distribution name -> the baked files that import it."""
    imports: dict[str, set[str]] = {}
    for path in _baked_files():
        for module in _required_imports(ast.parse(path.read_text(encoding="utf-8"))):
            if module in sys.stdlib_module_names or module == "embry0":
                continue
            distribution = _DISTRIBUTION_OF.get(module, module)
            imports.setdefault(_canonical(distribution), set()).add(str(path.relative_to(ROOT)))
    return imports


def _lock_closure(roots: list[str]) -> set[str]:
    """Every package uv.lock installs for `roots`: their dependencies, extras included, on any platform."""
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = {_canonical(p["name"]): p for p in lock["package"]}
    seen: set[str] = set()
    todo = [(_canonical(name), ()) for name in roots]
    while todo:
        name, extras = todo.pop()
        package = packages.get(name)
        assert package is not None, f"uv.lock has no package {name}"
        deps = list(package.get("dependencies", []))
        for extra in extras:
            deps += package.get("optional-dependencies", {}).get(extra, [])
        if name in seen and not extras:
            continue
        seen.add(name)
        todo += [(_canonical(dep["name"]), tuple(dep.get("extra", ()))) for dep in deps]
    return seen


def test_the_sandbox_group_installs_every_package_the_baked_code_imports():
    files = _baked_files()
    assert any(f.name == "runner.py" for f in files), (
        "no embry0/sandbox/runner.py among the files the sandbox images copy: the COPY lines no longer parse"
    )
    imports = _third_party_imports()
    assert {"claude-agent-sdk", "langchain-core"} <= set(imports), (
        f"the import scan no longer finds the runner's own packages: {sorted(imports)}"
    )
    installed = _lock_closure(_sandbox_group())
    missing = {dist: sorted(where) for dist, where in imports.items() if dist not in installed}
    assert not missing, (
        "code the sandbox images carry imports packages the `sandbox` group does not install; "
        f"add them to the group in pyproject.toml: {missing}"
    )
    # Named in the group itself, not only installed through another package's dependencies: a package that
    # stops depending on one the baked code imports must not take it out of the images.
    named = set(map(_canonical, _sandbox_group()))
    unnamed = {dist: sorted(where) for dist, where in imports.items() if dist not in named}
    assert not unnamed, (
        "code the sandbox images carry imports packages the `sandbox` group installs only through another "
        f"package's dependencies; name them in the group in pyproject.toml: {unnamed}"
    )


def test_every_sandbox_group_package_is_imported_by_the_baked_code():
    unused = sorted(set(map(_canonical, _sandbox_group())) - set(_third_party_imports()))
    assert not unused, f"the `sandbox` group installs packages no code in the sandbox images imports: {unused}"


def test_each_sandbox_package_has_one_declared_range_and_one_locked_version():
    pyproject = _pyproject()
    declared: dict[str, list[str]] = {}
    for requirement in [*pyproject["project"]["dependencies"], *pyproject["project"]["optional-dependencies"]["dev"]]:
        name = re.match(r"[A-Za-z0-9._-]+", requirement)
        assert name is not None, requirement
        declared.setdefault(_canonical(name.group()), []).append(requirement)
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    for entry in _sandbox_group():
        assert re.fullmatch(r"[A-Za-z0-9._-]+", entry), (
            f"the sandbox group names packages; their ranges live in the dependencies or the dev extra: {entry!r}"
        )
        ranges = declared.get(_canonical(entry), [])
        assert len(ranges) == 1 and re.search(r"[<>=]", ranges[0]), (
            f"{entry}: declare its range once, in [project] dependencies or the dev extra, "
            f"so CI installs the version the sandbox images do: {ranges}"
        )
        versions = [p["version"] for p in lock["package"] if _canonical(p["name"]) == _canonical(entry)]
        assert len(versions) == 1, f"uv.lock pins {entry} at {versions}: CI and the sandboxes could run different ones"


# --- the recipes --------------------------------------------------------------


def _exports(instruction: str) -> list[list[str]]:
    return [argv for argv in _commands(instruction) if argv[0].endswith("/uv") and argv[1:2] == ["export"]]


def _group_export(dockerfile: Path, export: list[str], groups: tuple[str, ...] = ("sandbox",)) -> None:
    assert {"--locked", "--no-emit-project"} <= set(export) and tuple(_options(export, "--only-group")) == groups, (
        f"{dockerfile.name}: export the {' and '.join(groups)} group(s) alone, --locked: {export}"
    )


def _locked_installs(dockerfile: Path) -> list[tuple[int, list[str]]]:
    """Every ``pip install -r <file>`` of the Dockerfile, with the index of its instruction."""
    return [
        (i, argv)
        for i, ins in enumerate(_instructions(dockerfile))
        if ins.startswith("RUN ")
        for argv in _commands(ins)
        if argv[:2] == ["pip", "install"] and "-r" in argv
    ]


def _assert_group_install(
    dockerfile: Path, at: int, argv: list[str], *, installs: tuple[str, ...], checks: tuple[str, ...]
) -> None:
    """A hash-checked install of the ``installs`` groups' export, then the whole stage checked against the
    ``checks`` groups' export, in the same RUN."""
    instruction = _instructions(dockerfile)[at]
    assert "--require-hashes" in argv, f"{dockerfile.name}: install the export with every hash checked: {argv}"
    export = next((e for e in _exports(instruction) if _option(e, "--output-file") == _option(argv, "-r")), None)
    assert export is not None, f"{dockerfile.name}: the pip install -r is not fed by a `uv export` in the same RUN"
    _group_export(dockerfile, export, installs)
    assert "--no-hashes" not in export, f"{dockerfile.name}: the installed export must carry the lock's hashes"
    pins = _option(argv, "-r")
    if checks != installs:
        against = next((e for e in _exports(instruction) if tuple(_options(e, "--only-group")) == checks), None)
        assert against is not None, f"{dockerfile.name}: no export of the {checks} groups to check the stage against"
        _group_export(dockerfile, against, checks)
        pins = _option(against, "--output-file")
    check = next((a for a in _commands(instruction) if a[:1] == ["python"]), None)
    assert check is not None, f"{dockerfile.name}: the build never checks its install against the lock"
    # Exact: the whole export (no --subset), only pip outside it, and nothing
    # after it on the line that could swallow its exit status (`|| true`).
    expected = ["python", "/opt/check-image-lock.py", pins, "--allow", "pip"]
    assert check == expected, f"{dockerfile.name}: the drift check must run as {expected}, not {check}"
    assert _CHECKER_MOUNT in instruction, f"{dockerfile.name}: /opt/check-image-lock.py is not {_CHECKER_MOUNT}"
    _assert_fails_on_any_error(dockerfile, instruction)


def _assert_locked_install(dockerfile: Path) -> int:
    """The one hash-checked install of the sandbox group's export, checked in full in the same RUN."""
    locked = _locked_installs(dockerfile)
    assert len(locked) == 1, f"{dockerfile.name}: expected one `pip install -r <exported group>`, found {locked}"
    at, argv = locked[0]
    _assert_group_install(dockerfile, at, argv, installs=("sandbox",), checks=("sandbox",))
    return at


def _stage_starts(dockerfile: Path) -> dict[str, int]:
    """Each named stage of the Dockerfile and the index of its FROM."""
    found = {}
    for i, ins in enumerate(_instructions(dockerfile)):
        match = re.match(r"FROM\s+\S+\s+AS\s+(\S+)$", ins)
        if match:
            found[match.group(1)] = i
    return found


_CHECKER_MOUNT = "--mount=type=bind,source=infra/scripts/check-image-lock.py,target=/opt/check-image-lock.py"


def _assert_fails_on_any_error(dockerfile: Path, instruction: str) -> None:
    """The RUN is a plain `&&` chain, so a failing install or check fails the build.

    `install && check || true` and `check; true` both build green when the check fails.
    """
    body = re.sub(r"^RUN\s+(--mount=\S+\s+)*", "", instruction)
    operators = re.findall(r"\|\||;|(?<!\|)\|(?!\|)|\bset\s+\+e\b", body)
    assert not operators, f"{dockerfile.name}: {operators} in this RUN could hide a failed install or check: {body}"


def _other_pip_installs(dockerfile: Path, locked_at: int) -> list[tuple[int, list[str]]]:
    return [
        (i, argv)
        for i, ins in enumerate(_instructions(dockerfile))
        if ins.startswith("RUN ") and i != locked_at
        for argv in _commands(ins)
        if argv[:2] == ["pip", "install"]
    ]


#: The stages of Dockerfile.sandbox that add Poetry to the base stage.
DEV_STAGES = ("dev-python", "dev-jvm-python")


def test_the_sandbox_image_installs_its_group_from_the_lock():
    starts = _stage_starts(SANDBOX)
    assert {"base", *DEV_STAGES} <= set(starts), f"Dockerfile.sandbox's stages changed; update this guard: {starts}"
    order = sorted(starts.values())
    end = {name: next((j for j in order if j > i), len(_instructions(SANDBOX))) for name, i in starts.items()}
    locked = _locked_installs(SANDBOX)
    base = [(i, argv) for i, argv in locked if starts["base"] < i < end["base"]]
    assert len(base) == 1, f"Dockerfile.sandbox: expected one locked install in the base stage, found {base}"
    _assert_group_install(SANDBOX, *base[0], installs=("sandbox",), checks=("sandbox",))
    for name in DEV_STAGES:
        mine = [(i, argv) for i, argv in locked if starts[name] < i < end[name]]
        assert len(mine) == 1, f"Dockerfile.sandbox: expected one locked install of Poetry in {name}, found {mine}"
        # #645: Poetry and what it needs from the lock's `poetry` group, hash-checked; the stage checked whole.
        _assert_group_install(SANDBOX, *mine[0], installs=("poetry",), checks=("sandbox", "poetry"))
    others = [(i, argv) for i, argv in _other_pip_installs(SANDBOX, -1) if (i, argv) not in locked]
    assert not others, f"Dockerfile.sandbox: every Python package comes from a locked install: {others}"


def test_the_poetry_group_is_poetry_alone_locked_once_past_its_advisories():
    """#645, #331: the `poetry` group names Poetry with a range, uv.lock holds one version of it at or past 2.3.3,
    and one version of each package it needs, so the stage's two exports cannot disagree."""
    group = _pyproject().get("dependency-groups", {}).get("poetry")
    assert group and len(group) == 1 and re.match(r"poetry\s*[<>=~]", group[0]), (
        f"pyproject.toml's `poetry` group must name Poetry alone, with a range: {group}"
    )
    assert "poetry" not in (_pyproject().get("tool", {}).get("uv", {}).get("default-groups") or []), (
        "the `poetry` group must not be a default group: `uv sync` and the orchestrator image leave it out"
    )
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    for name in sorted(_lock_closure(["poetry"])):
        versions = [p["version"] for p in lock["package"] if _canonical(p["name"]) == name]
        assert len(versions) == 1, f"uv.lock pins {name} at {versions}: the dev images' two exports could differ"
    [version] = [p["version"] for p in lock["package"] if _canonical(p["name"]) == "poetry"]
    assert tuple(int(part) for part in version.split(".")[:3]) >= (2, 3, 3), f"uv.lock locks Poetry {version}"


def test_the_qa_sandbox_image_installs_its_group_from_the_lock():
    at = _assert_locked_install(SANDBOX_QA)
    others = _other_pip_installs(SANDBOX_QA, at)
    assert not others, f"Dockerfile.sandbox.qa: every Python package comes from the locked install: {others}"


def test_the_import_scan_skips_only_imports_guarded_against_their_failure():
    optional = {
        "except ImportError": "try:\n    import alpha\nexcept ImportError:\n    alpha = None\n",
        "except ModuleNotFoundError": "try:\n    import alpha\nexcept ModuleNotFoundError:\n    pass\n",
        "except Exception": "try:\n    from alpha import beta\nexcept Exception:\n    pass\n",
        "a bare except": "try:\n    import alpha\nexcept:\n    pass\n",
        "a tuple naming ImportError": "try:\n    import alpha\nexcept (ValueError, ImportError):\n    pass\n",
    }
    for case, source in optional.items():
        found = _required_imports(ast.parse(source))
        assert found == set(), f"{case}: the code falls back when the import fails, so it is optional: {found}"
    required = {
        "try/finally": "try:\n    import alpha\nfinally:\n    pass\n",
        "except ValueError": "try:\n    import alpha\nexcept ValueError:\n    pass\n",
        "a lazy import": "def load():\n    import alpha\n",
        "the else of TYPE_CHECKING": "if TYPE_CHECKING:\n    import gamma\nelse:\n    import alpha\n",
    }
    for case, source in required.items():
        found = _required_imports(ast.parse(source))
        assert found == {"alpha"}, (
            f"{case}: nothing catches the import's failure, so the image must provide it: {found}"
        )


def test_no_sandbox_image_pins_a_package_by_hand():
    for dockerfile in (SANDBOX, SANDBOX_QA):
        written = re.findall(r'"([A-Za-z0-9._-]+(?:==|>=|<=|~=|<|>)[^"]*)"', dockerfile.read_text(encoding="utf-8"))
        assert not written, f"{dockerfile.name} writes versions by hand; they come from uv.lock now: {written}"
