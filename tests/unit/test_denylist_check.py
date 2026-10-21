"""#530: the name denylist check, tested against a synthetic term.

scripts/check_denylist.py replaces two guard tests that spelled out the names
they protected. Its real terms live outside the repository, so these tests
give it a temporary denylist holding a made-up word and check that it fires,
that it never echoes what it matched, and that every way of checking nothing
ends in "NOT RUN" rather than a pass.
"""

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_denylist.py"
ENV_VAR = "EMBRY0_DENYLIST_FILE"

# Made up; names nothing.
TERM = "glorbnax"


def _load_checker() -> ModuleType:
    assert SCRIPT.is_file(), f"the denylist checker {SCRIPT} is missing"
    spec = importlib.util.spec_from_file_location("check_denylist", SCRIPT)
    assert spec is not None and spec.loader is not None, f"cannot load {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    # dataclasses look their module up in sys.modules while the class is built.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(root: Path, *paths: str, denylist: Path | None) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != ENV_VAR}
    if denylist is not None:
        env[ENV_VAR] = str(denylist)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *paths],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "guide.md").write_text("# Guide\n\nNothing to see.\n")
    (root / "src").mkdir()
    (root / "src" / "app.ts").write_text('export const name = "app"\n')
    return root


@pytest.fixture
def denylist(tmp_path: Path) -> Path:
    path = tmp_path / "denylist.txt"
    path.write_text(f"# synthetic\n\n{TERM}\n")
    return path


def _assert_not_run(result: subprocess.CompletedProcess[str], why: str) -> None:
    assert result.returncode == 2, f"expected exit 2 (not run), got {result.returncode}: {result.stderr}"
    assert "denylist check NOT RUN" in result.stderr, f"no NOT RUN notice in stderr: {result.stderr!r}"
    assert why in result.stderr, f"stderr does not say {why!r}: {result.stderr!r}"
    assert "passed" not in result.stdout, f"a check that did not run reported a pass: {result.stdout!r}"


def test_clean_tree_passes(tree: Path, denylist: Path) -> None:
    result = _run(tree, "docs", "src", denylist=denylist)
    assert result.returncode == 0, f"clean tree failed: {result.stdout}{result.stderr}"
    assert "denylist check passed: 0 matches (1 patterns, 2 files)" in result.stdout, result.stdout


def test_a_term_fires_without_being_printed(tree: Path, denylist: Path) -> None:
    (tree / "docs" / "guide.md").write_text("# Guide\n\nBuilt for GlorbNax Inc.\n")
    result = _run(tree, "docs", "src", denylist=denylist)
    assert result.returncode == 1, f"a denylisted term did not fail the check: {result.stdout}{result.stderr}"
    assert "docs/guide.md:3: matches denylist line 3" in result.stdout, result.stdout
    assert "denylist check FAILED: 1 matches in 1 files" in result.stdout, result.stdout
    output = result.stdout + result.stderr
    assert not re.search(TERM, output, re.IGNORECASE), f"the output repeats the matched term: {output!r}"


def test_a_term_in_a_file_path_fires(tree: Path, denylist: Path) -> None:
    (tree / "docs" / f"{TERM}-notes.md").write_text("neutral text\n")
    result = _run(tree, "docs", denylist=denylist)
    assert result.returncode == 1, f"a denylisted file name did not fail the check: {result.stdout}"
    assert "(its path): matches denylist line 3" in result.stdout, result.stdout


def test_a_git_tree_is_checked_by_what_it_tracks(tree: Path, denylist: Path) -> None:
    git = ["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "docs", "src"], check=True)
    (tree / "docs" / "scratch.md").write_text(f"{TERM}\n")

    untracked = _run(tree, "docs", denylist=denylist)
    assert untracked.returncode == 0, f"an untracked file was scanned: {untracked.stdout}"
    assert "(1 patterns, 1 files)" in untracked.stdout, untracked.stdout

    subprocess.run([*git, "add", "docs/scratch.md"], check=True)
    tracked = _run(tree, "docs", denylist=denylist)
    assert tracked.returncode == 1, f"a tracked file holding the term passed: {tracked.stdout}"
    assert "docs/scratch.md:1: matches denylist line 3" in tracked.stdout, tracked.stdout


def test_no_denylist_configured_is_not_a_pass(tree: Path) -> None:
    _assert_not_run(_run(tree, "docs", denylist=None), f"{ENV_VAR} is not set")


def test_a_missing_denylist_file_is_not_a_pass(tree: Path, tmp_path: Path) -> None:
    _assert_not_run(_run(tree, "docs", denylist=tmp_path / "absent.txt"), "cannot read the denylist")


def test_a_denylist_without_patterns_is_not_a_pass(tree: Path, tmp_path: Path) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_text("# only a comment\n\n")
    _assert_not_run(_run(tree, "docs", denylist=empty), "holds no patterns")


def test_a_pattern_that_matches_everything_is_refused(tree: Path, tmp_path: Path) -> None:
    lax = tmp_path / "lax.txt"
    lax.write_text(f"{TERM}\n.*\n")
    _assert_not_run(_run(tree, "docs", denylist=lax), "denylist line 2 matches the empty string")


def test_a_missing_scope_path_is_not_a_pass(tree: Path, denylist: Path) -> None:
    _assert_not_run(_run(tree, "docs", "moved-away.md", denylist=denylist), "scope path moved-away.md does not exist")


def test_an_empty_scope_directory_is_not_a_pass(tree: Path, denylist: Path) -> None:
    (tree / "empty").mkdir()
    _assert_not_run(_run(tree, "empty", denylist=denylist), "scope path empty holds no file")


@pytest.mark.skipif(os.geteuid() == 0, reason="root can list a mode-000 directory")
def test_an_unlistable_directory_is_not_a_pass(tree: Path, denylist: Path) -> None:
    locked = tree / "docs" / "locked"
    locked.mkdir()
    (locked / "inner.md").write_text(f"{TERM}\n")
    locked.chmod(0)
    try:
        _assert_not_run(_run(tree, "docs", denylist=denylist), "cannot list")
    finally:
        locked.chmod(0o755)


def test_a_failing_git_listing_is_not_a_pass(tree: Path, denylist: Path) -> None:
    subprocess.run(["git", "-C", str(tree), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(tree), "add", "docs"], check=True)
    (tree / ".git" / "index").write_bytes(b"not an index")
    _assert_not_run(_run(tree, "docs", denylist=denylist), "git ls-files failed")


def test_a_denylist_inside_the_tree_is_refused(tree: Path) -> None:
    inside = tree / "denylist.txt"
    inside.write_text(f"{TERM}\n")
    _assert_not_run(_run(tree, "docs", denylist=inside), "is inside the scanned tree")


def _local_imports(module: Path, src: Path) -> list[Path]:
    """The project modules `module` imports, by `@/` alias or relative path."""
    found = []
    for spec in re.findall(r"""(?:from|import)\s*["'](@/[^"']+|\.{1,2}/[^"']+)["']""", module.read_text()):
        base = src / spec[2:] if spec.startswith("@/") else module.parent / spec
        candidates = [base, Path(f"{base}.ts"), Path(f"{base}.tsx"), base / "index.ts", base / "index.tsx"]
        resolved = next((c for c in candidates if c.is_file()), None)
        assert resolved is not None, f"{module} imports {spec!r}, which resolves to no file"
        found.append(resolved.resolve())
    return found


def test_the_default_scope_covers_what_the_retired_guards_covered() -> None:
    scope = _load_checker().DEFAULT_SCOPE
    for entry in scope:
        assert (ROOT / entry).exists(), f"DEFAULT_SCOPE names {entry}, which no longer exists"

    def covered(path: Path) -> bool:
        rel = path.relative_to(ROOT).as_posix()
        return any(rel == entry or rel.startswith(entry.rstrip("/") + "/") for entry in scope)

    # tests/unit/docs/test_credentials_docs.py guarded this doc.
    assert covered(ROOT / "docs" / "credentials.md"), "DEFAULT_SCOPE dropped docs/credentials.md"

    # AuthGate.test.tsx guarded the text AuthGate renders, which can come from
    # AuthGate.tsx or from any project module it imports, directly or not.
    src = ROOT / "frontend" / "src"
    pending = [src / "components" / "auth" / "AuthGate.tsx"]
    seen: set[Path] = set()
    while pending:
        module = pending.pop().resolve()
        if module not in seen:
            seen.add(module)
            pending.extend(_local_imports(module, src))
    uncovered = sorted(path.relative_to(ROOT).as_posix() for path in seen if not covered(path))
    assert not uncovered, f"DEFAULT_SCOPE misses modules AuthGate renders from: {uncovered}"


# The public export allows a term inside the public repository's canonical
# URLs, which name its owner. A made-up owner and repository stand in here.
CANONICAL = f"github.com/{TERM}/widget"


def test_an_allowed_literal_masks_only_itself(tree: Path, denylist: Path) -> None:
    (tree / "docs" / "guide.md").write_text(f"See https://{CANONICAL}/issues.\nClone https://{CANONICAL}.git\n")
    allowed = _run(tree, "docs", "--allow", CANONICAL, denylist=denylist)
    assert allowed.returncode == 0, f"an allowed canonical URL failed the check: {allowed.stdout}{allowed.stderr}"
    assert "(1 patterns, 1 files, 1 allowances)" in allowed.stdout, allowed.stdout

    unallowed = _run(tree, "docs", denylist=denylist)
    assert unallowed.returncode == 1, f"the URL passed without an allowance: {unallowed.stdout}"


def test_an_allowance_does_not_cover_the_term_elsewhere(tree: Path, denylist: Path) -> None:
    (tree / "docs" / "guide.md").write_text(
        f"https://{CANONICAL}\n"
        f"https://{CANONICAL} and {TERM} again\n"
        f"https://{CANONICAL}-private\n"
        f"https://x{CANONICAL}\n"
        f"Built for {TERM}.\n"
    )
    result = _run(tree, "docs", "--allow", CANONICAL, denylist=denylist)
    assert result.returncode == 1, f"a term outside the allowed literal passed: {result.stdout}"
    hits = re.findall(r"docs/guide\.md:(\d+):", result.stdout)
    assert hits == ["2", "3", "4", "5"], f"expected lines 2-5 to match, got {hits}: {result.stdout}"


def test_an_allowance_scoped_to_one_file_covers_only_that_file(tree: Path, denylist: Path) -> None:
    (tree / "docs" / "owners").write_text(f"* @{TERM}\n")
    (tree / "docs" / "guide.md").write_text(f"ask @{TERM}\n")
    result = _run(tree, "docs", "--allow-in", "docs/owners", f"@{TERM}", denylist=denylist)
    assert result.returncode == 1, f"the scoped allowance leaked into another file: {result.stdout}"
    assert "docs/guide.md:1: matches denylist line 3" in result.stdout, result.stdout
    assert "docs/owners" not in result.stdout, f"the scoped allowance did not cover its own file: {result.stdout}"


def test_an_allowance_for_a_file_not_scanned_is_not_a_pass(tree: Path, denylist: Path) -> None:
    result = _run(tree, "docs", "--allow-in", "docs/moved.md", f"@{TERM}", denylist=denylist)
    _assert_not_run(result, "an allowance names docs/moved.md, which is not among the scanned files")


def test_an_empty_allowance_is_not_a_pass(tree: Path, denylist: Path) -> None:
    _assert_not_run(_run(tree, "docs", "--allow", " ", denylist=denylist), "an allowance has an empty literal")


# CI scans the published tree whole (--public), with the allowances the public
# export passed: the canonical URLs anywhere, the owner's handle in
# .github/CODEOWNERS, and each name CONTRIBUTORS lists, there only.
def _public_tree(tree: Path) -> Path:
    git = ["git", "-C", str(tree), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    (tree / ".github").mkdir()
    (tree / ".github" / "CODEOWNERS").write_text("* @someone\n")
    (tree / "CONTRIBUTORS").write_text(f"# The people who wrote it.\n\nAda {TERM.title()}\n")
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    return tree


def test_public_mode_scans_every_tracked_file(tree: Path, denylist: Path) -> None:
    root = _public_tree(tree)
    clean = _run(root, "--public", denylist=denylist)
    assert clean.returncode == 0, f"a clean tree failed the public scan: {clean.stdout}{clean.stderr}"
    assert "(1 patterns, 4 files" in clean.stdout, f"--public did not scan every tracked file: {clean.stdout}"

    plans = root / "docs" / "superpowers" / "plans"
    plans.mkdir(parents=True)
    (plans / "plan.md").write_text(f"venv at /Users/{TERM}/repos/.venv\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    dirty = _run(root, "--public", denylist=denylist)
    assert dirty.returncode == 1, f"a term in a file outside DEFAULT_SCOPE passed: {dirty.stdout}"
    assert "docs/superpowers/plans/plan.md:1: matches denylist line 3" in dirty.stdout, dirty.stdout


def test_public_mode_allows_a_contributor_only_in_contributors(tree: Path, denylist: Path) -> None:
    root = _public_tree(tree)
    (root / "docs" / "guide.md").write_text(f"Thanks, Ada {TERM.title()}.\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    result = _run(root, "--public", denylist=denylist)
    assert result.returncode == 1, f"a contributor's name outside CONTRIBUTORS passed: {result.stdout}"
    assert "docs/guide.md:1: matches denylist line 3" in result.stdout, result.stdout
    assert "CONTRIBUTORS" not in result.stdout, f"CONTRIBUTORS itself was flagged: {result.stdout}"


def test_public_mode_uses_the_export_allowances(tree: Path, denylist: Path) -> None:
    checker = _load_checker()
    root = _public_tree(tree)
    allowances = checker.public_allowances(root, public_repo=f"{TERM}/widget")
    assert {(a.path, a.literal) for a in allowances} == {
        (None, CANONICAL),
        (".github/CODEOWNERS", f"@{TERM}"),
        ("CONTRIBUTORS", f"Ada {TERM.title()}"),
    }


def test_public_mode_takes_no_paths(tree: Path, denylist: Path) -> None:
    result = _run(_public_tree(tree), "--public", "docs", denylist=denylist)
    assert result.returncode == 2 and "--public scans the whole tree" in result.stderr, result.stderr
