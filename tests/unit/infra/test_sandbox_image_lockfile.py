"""The sandbox images install uv.lock's versions, and check it (#288).

The sandbox images installed the runner's packages from versions written in
their Dockerfiles: `langchain-core>=0.3,<1` ran 0.3.x in sandboxes while CI
tested the lock's 1.x, `anthropic` was in no lock at all, and the exact
`claude-agent-sdk` pin was kept equal to the lock by hand. Now pyproject's
`sandbox` dependency group names the packages, both images install that
group's export with every hash checked, and `check-image-lock.py` fails the
build when what is installed differs from it. These tests pin the group
against the code the images carry, and the recipes against the lock.
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


def _required_imports(tree: ast.AST) -> set[str]:
    """Top-level module names a file imports when it runs.

    Imports under `if TYPE_CHECKING:` never run, and an import inside a
    `try:` is optional by construction (the code falls back when it fails).
    Every other import, a lazy one inside a function included, must succeed
    in the image.
    """
    found: set[str] = set()

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and _is_type_checking(node.test):
            for child in node.orelse:
                visit(child)
            return
        if isinstance(node, ast.Try):
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


def _third_party_imports() -> dict[str, set[str]]:
    """Distribution name -> the baked files that import it."""
    imports: dict[str, set[str]] = {}
    for path in _baked_files():
        for module in _required_imports(ast.parse(path.read_text(encoding="utf-8"))):
            if module in sys.stdlib_module_names or module == "embry0":
                continue
            imports.setdefault(_canonical(module), set()).add(str(path.relative_to(ROOT)))
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


def _group_export(dockerfile: Path, export: list[str]) -> None:
    assert {"--locked", "--no-emit-project"} <= set(export) and _option(export, "--only-group") == "sandbox", (
        f"{dockerfile.name}: export the `sandbox` group alone, --locked: {export}"
    )


def _assert_locked_install(dockerfile: Path, before: int | None = None) -> int:
    """The one hash-checked install of the group's export, checked in full in the same RUN."""
    instructions = _instructions(dockerfile)
    locked = [
        (i, argv)
        for i, ins in enumerate(instructions)
        if ins.startswith("RUN ")
        for argv in _commands(ins)
        if argv[:2] == ["pip", "install"] and "-r" in argv
    ]
    assert len(locked) == 1, f"{dockerfile.name}: expected one `pip install -r <exported group>`, found {locked}"
    at, argv = locked[0]
    if before is not None:
        assert at < before, (
            f"{dockerfile.name}: the locked install must be in the base stage, which every stage builds on"
        )
    assert "--require-hashes" in argv, f"{dockerfile.name}: install the export with every hash checked: {argv}"
    export = next((e for e in _exports(instructions[at]) if _option(e, "--output-file") == _option(argv, "-r")), None)
    assert export is not None, f"{dockerfile.name}: the pip install -r is not fed by a `uv export` in the same RUN"
    _group_export(dockerfile, export)
    assert "--no-hashes" not in export, f"{dockerfile.name}: the installed export must carry the lock's hashes"
    check = next((a for a in _commands(instructions[at]) if a[:1] == ["python"]), None)
    assert check is not None and check[1].endswith("check-image-lock.py"), (
        f"{dockerfile.name}: the build never checks its install against the lock"
    )
    assert check[2] == _option(argv, "-r"), f"{dockerfile.name}: checked against another file than it installed"
    assert "--subset" not in check, f"{dockerfile.name}: the image installs its whole export: check it in full"
    allowed = {check[j + 1] for j, arg in enumerate(check) if arg == "--allow"}
    assert allowed == {"pip"}, f"{dockerfile.name}: only pip may be installed outside the export: {allowed}"
    return at


def _other_pip_installs(dockerfile: Path, locked_at: int) -> list[tuple[int, list[str]]]:
    return [
        (i, argv)
        for i, ins in enumerate(_instructions(dockerfile))
        if ins.startswith("RUN ") and i != locked_at
        for argv in _commands(ins)
        if argv[:2] == ["pip", "install"]
    ]


def _assert_constrained(dockerfile: Path, at: int, argv: list[str]) -> None:
    """A tool's pip install that may share the locked versions but never move one."""
    constraints = _option(argv, "-c")
    assert constraints, (
        f"{dockerfile.name}: `{' '.join(argv)}` installs into the runner's interpreter without the "
        "sandbox group's locked versions as constraints, so it could move one"
    )
    export = next(
        (e for e in _exports(_instructions(dockerfile)[at]) if _option(e, "--output-file") == constraints), None
    )
    assert export is not None, f"{dockerfile.name}: the constraints {constraints} are not a `uv export` of the same RUN"
    _group_export(dockerfile, export)


def test_the_sandbox_image_installs_its_group_from_the_lock():
    stages = [i for i, ins in enumerate(_instructions(SANDBOX)) if ins.startswith("FROM base AS ")]
    assert stages, "Dockerfile.sandbox has no stage built on `base`; update this guard"
    at = _assert_locked_install(SANDBOX, before=min(stages))
    others = _other_pip_installs(SANDBOX, at)
    assert others, "Dockerfile.sandbox no longer installs Poetry in its dev stages; update this guard"
    for i, argv in others:
        assert i > min(stages), f"Dockerfile.sandbox: a second pip install in the base stage: {argv}"
        _assert_constrained(SANDBOX, i, argv)


def test_the_qa_sandbox_image_installs_its_group_from_the_lock():
    at = _assert_locked_install(SANDBOX_QA)
    others = _other_pip_installs(SANDBOX_QA, at)
    assert not others, f"Dockerfile.sandbox.qa: every Python package comes from the locked install: {others}"


def test_no_sandbox_image_pins_a_package_by_hand():
    for dockerfile in (SANDBOX, SANDBOX_QA):
        written = re.findall(r'"([A-Za-z0-9._-]+(?:==|>=|<=|~=|<|>)[^"]*)"', dockerfile.read_text(encoding="utf-8"))
        hand = [req for req in written if not req.startswith("poetry==")]
        assert not hand, f"{dockerfile.name} writes versions by hand; they come from uv.lock now: {hand}"
