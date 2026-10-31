#!/usr/bin/env python3
"""Build the public tree from a ref of this repository, as one commit with no history.

The public repository gets a fresh export, never this repository's history:
that history names other parties, and holds material that never ships. One
export, in order:

1. extract the ref's tracked files (``git archive``) into an empty directory;
2. remove every path in ``DROP``, failing if one is missing from the ref, so
   a renamed path cannot slip through unnoticed;
3. rewrite repository URLs by ``URL_REWRITES``;
4. add the files in ``ADD``, which exist only in the public repository;
5. write ``CONTRIBUTORS`` from the ref's commit authors, names only;
6. fail if any exported file still refers to a dropped path;
7. run the name denylist over the whole tree (``scripts/check_denylist.py``,
   patterns from the file ``EMBRY0_DENYLIST_FILE`` names), allowing a term
   only inside the public repository's canonical URLs;
8. commit the tree as the only commit of a new repository;
9. run gitleaks over that commit with the tree's own ``.gitleaks.toml``.

Any failure stops the export and leaves the directory for inspection; a step
that cannot run is a failure, never a pass. Nothing is pushed: pushing to the
public repository is a separate, deliberate step.

Usage::

    EMBRY0_DENYLIST_FILE=/outside/the/repo/denylist.txt \\
      python3 scripts/export_public.py --ref origin/main --dest /tmp/embry0-public \\
        --author "Name <id+handle@users.noreply.github.com>" [--mailmap /outside/mailmap]
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[1]

# The public repository. Its canonical URLs are the only place a denylisted
# term (its owner's handle) is allowed in the exported tree, so it is spelled
# here as one of them.
PUBLIC_REPO_URL = "https://github.com/sudoTomas/embry0"
PUBLIC_REPO = PUBLIC_REPO_URL.removeprefix("https://github.com/")  # owner/name


@dataclass(frozen=True)
class Drop:
    """A path that never ships. One ending in "/" is a directory prefix."""

    path: str
    reason: str

    @property
    def is_prefix(self) -> bool:
        return self.path.endswith("/")

    def covers(self, rel: str) -> bool:
        if self.is_prefix:
            return rel == self.path.rstrip("/") or rel.startswith(self.path)
        return rel == self.path


# Everything the export removes, and why. The one list: the export, its
# dangling-reference check and the guard test that public files do not refer
# to these paths all read it.
DROP: tuple[Drop, ...] = (
    Drop("internal/", "audits, RCAs, specs and plans; internal/README.md says what goes there"),
    Drop("docs/enterprise-scale-roadmap.md", "owner decision 2026-10-09: not published"),
)

# Repository URL rewrites, "old owner/name" -> "new owner/name", applied to
# github.com URLs in every exported text file. Empty while the public
# repository keeps this repository's canonical name.
URL_REWRITES: dict[str, str] = {}

# Files only the public repository has, as exported path -> content.
DEPENDABOT_YML = """\
# Dependabot version updates.
# https://docs.github.com/en/code-security/dependabot/working-with-dependabot/dependabot-options-reference
#
# Weekly, one grouped pull request per ecosystem for minor and patch updates;
# each major update gets its own pull request. Security updates are not
# configured here: they are switched on in the repository's settings.
version: 2
updates:
  # pyproject.toml and uv.lock. Bumping ruff in uv.lock also needs the ruff rev
  # in .pre-commit-config.yaml (tests/unit/test_repo_metadata.py checks it).
  - package-ecosystem: uv
    directory: /
    schedule:
      interval: weekly
    open-pull-requests-limit: 5
    groups:
      python-minor-and-patch:
        update-types: [minor, patch]

  - package-ecosystem: npm
    directory: /frontend
    schedule:
      interval: weekly
    open-pull-requests-limit: 5
    groups:
      npm-minor-and-patch:
        update-types: [minor, patch]

  # Base images in infra/Dockerfile.* and frontend/Dockerfile.
  - package-ecosystem: docker
    directories:
      - /infra
      - /frontend
    schedule:
      interval: weekly
    open-pull-requests-limit: 5
    ignore:
      # The Python minor version moves on purpose, together with .python-version,
      # requires-python and mypy's python_version (test_repo_metadata.py).
      - dependency-name: python
        update-types: [version-update:semver-major, version-update:semver-minor]
    groups:
      # The `uv` stage of infra/Dockerfile.orchestrator and Dockerfile.proxy
      # moves with UV_VERSION in ci.yml, which Dependabot does not track. It
      # gets a pull request of its own, which fails
      # tests/unit/infra/test_image_lockfile.py until UV_VERSION is raised in
      # it, so a uv bump never holds back the base images' patch updates.
      uv:
        patterns: ["*astral-sh/uv"]
      docker-minor-and-patch:
        update-types: [minor, patch]
        exclude-patterns: ["*astral-sh/uv"]

  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: weekly
    open-pull-requests-limit: 5
    groups:
      actions-minor-and-patch:
        update-types: [minor, patch]
"""

ADD: dict[str, str] = {
    ".github/dependabot.yml": DEPENDABOT_YML,
}

CONTRIBUTORS_FILE = "CONTRIBUTORS"

# Files that name dropped paths on purpose: this script holds the list, and
# its tests pin it.
REFERENCE_EXEMPT: frozenset[str] = frozenset({"scripts/export_public.py", "tests/unit/test_public_export.py"})


class ExportError(Exception):
    """The export stopped. Never a partial success."""


# --- 1. extract ---------------------------------------------------------------


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if check and result.returncode != 0:
        raise ExportError(f"git {' '.join(args)} failed: {result.stderr.decode(errors='replace').strip()}")
    return result


def extract(repo: Path, ref: str, dest: Path) -> str:
    """Write the ref's tracked files into the empty directory dest; return the full commit sha."""
    if dest.exists() and any(dest.iterdir()):
        raise ExportError(f"{dest} is not empty; the export needs an empty directory")
    sha = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").stdout.decode().strip()
    archive = _git(repo, "archive", "--format=tar", sha).stdout
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, filter="tar")
    return sha


# --- 2. drop ------------------------------------------------------------------


def _tree_files(root: Path) -> list[str]:
    files = []
    for directory, subdirs, names in os.walk(root):
        subdirs[:] = [name for name in subdirs if name != ".git"]
        for name in names:
            files.append(Path(directory, name).relative_to(root).as_posix())
    return sorted(files)


def apply_drops(root: Path, drops: Sequence[Drop] = DROP) -> list[str]:
    """Remove every dropped path; fail on an entry that matches nothing. Returns what was removed."""
    removed = []
    for drop in drops:
        target = root / drop.path.rstrip("/")
        if drop.is_prefix and target.is_dir():
            removed += [f for f in _tree_files(root) if drop.covers(f)]
            shutil.rmtree(target)
        elif not drop.is_prefix and target.is_file():
            target.unlink()
            removed.append(drop.path)
        else:
            raise ExportError(f"DROP names {drop.path}, which the exported ref does not have; update DROP")
    return removed


# --- 3. rewrite URLs ----------------------------------------------------------


def _read_text(path: Path) -> str | None:
    data = path.read_bytes()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def rewrite_urls(root: Path, rewrites: dict[str, str] = URL_REWRITES) -> list[str]:
    """Rewrite github.com/<old> to github.com/<new> in every text file. Returns the files changed."""
    if not rewrites:
        return []
    alternatives = "|".join(re.escape(old) for old in sorted(rewrites, key=len, reverse=True))
    url = re.compile(rf"(?<=github\.com[/:])({alternatives})(?![\w-])")
    changed = []
    for rel in _tree_files(root):
        path = root / rel
        text = _read_text(path)
        if text is None:
            continue
        new = url.sub(lambda m: rewrites[m.group(1)], text)
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed.append(rel)
    return changed


# --- 4. add -------------------------------------------------------------------


def add_files(root: Path, files: dict[str, str] = ADD) -> None:
    for rel, content in files.items():
        path = root / rel
        if path.exists():
            raise ExportError(f"{rel} already exists in the exported ref; the export adds it, so remove one of them")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


# --- 5. contributors ----------------------------------------------------------


def contributor_names(repo: Path, sha: str, mailmap: Path | None = None) -> list[str]:
    """The distinct commit author names in the ref's history, sorted; never an email.

    Only commits the ref contains count, so unmerged work is not credited. A
    mailmap (git's format) folds one person's identities into one name; keep
    it outside the repository, since it names people.
    """
    args = ["log", "--use-mailmap", "--format=%aN", sha]
    if mailmap is not None:
        if not mailmap.is_file():
            raise ExportError(f"the mailmap {mailmap} does not exist")
        args = ["-c", f"mailmap.file={mailmap}", *args]
    names = {line.strip() for line in _git(repo, *args).stdout.decode().splitlines() if line.strip()}
    if not names:
        raise ExportError(f"no commit authors found for {sha}")
    return sorted(names, key=str.casefold)


def write_contributors(root: Path, names: Iterable[str]) -> None:
    path = root / CONTRIBUTORS_FILE
    if path.exists():
        raise ExportError(f"{CONTRIBUTORS_FILE} already exists in the exported ref; the export writes it")
    body = "".join(f"{name}\n" for name in names)
    path.write_text(
        "# The people who wrote embry0, from the commit authors of the history this\n"
        "# release was exported from. Generated by scripts/export_public.py.\n\n" + body,
        encoding="utf-8",
    )


# --- 6. references to dropped paths -------------------------------------------

_PATH_TOKEN = re.compile(r"[\w./-]+")
_EXTENSION = re.compile(r"\.[A-Za-z0-9]+$")


def _candidates(src: str, token: str) -> set[str]:
    """Where a path-like token in file src may point, relative to the root."""
    token = token.rstrip(".")
    if not token or token.startswith("//"):
        return set()
    found = set()
    for base in ("", posixpath.dirname(src)):
        joined = posixpath.normpath(posixpath.join(base, token.lstrip("/")) if base else token.lstrip("/"))
        if not joined.startswith("../") and joined != "..":
            found.add(joined)
    return found


def dropped_references(
    root: Path,
    files: Iterable[str],
    drops: Sequence[Drop] = DROP,
    exempt: Iterable[str] = REFERENCE_EXEMPT,
) -> list[str]:
    """Every place a file refers to a dropped path, as "file:line: path".

    A reference is a path, relative to the root or to the file, that names a
    dropped file, a file under a dropped directory, or a Markdown link into
    one. Naming a dropped directory itself (``internal/``) describes where
    material goes and is not a reference to something missing.
    """
    skip = set(exempt)
    found = []
    for rel in files:
        if rel in skip or any(drop.covers(rel) for drop in drops):
            continue
        text = _read_text(root / rel)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for token in _PATH_TOKEN.finditer(line):
                linked = line[: token.start()].endswith("](")
                for candidate in _candidates(rel, token.group()):
                    for drop in drops:
                        if not drop.covers(candidate):
                            continue
                        names_a_file = not drop.is_prefix or bool(_EXTENSION.search(candidate))
                        if names_a_file or linked:
                            found.append(f"{rel}:{number}: {candidate}")
    return sorted(set(found))


# --- 7. denylist --------------------------------------------------------------


def _load_denylist_checker(root: Path) -> ModuleType:
    script = root / "scripts" / "check_denylist.py"
    spec = importlib.util.spec_from_file_location("export_check_denylist", script)
    if spec is None or spec.loader is None:
        raise ExportError(f"cannot load the denylist checker {script}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    # The checker lives in the tree being exported. Importing it normally
    # writes scripts/__pycache__ there, which the denylist then scans and
    # the commit would carry (#623).
    writes_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = writes_bytecode
    return module


def canonical_allowances(public_repo: str = PUBLIC_REPO) -> list[tuple[str | None, str]]:
    """(path or None for any file, literal): where the public repository's owner may be named."""
    owner = public_repo.split("/", 1)[0]
    return [
        (None, f"github.com/{public_repo}"),
        (".github/CODEOWNERS", f"@{owner}"),
    ]


def contributor_allowances(names: Iterable[str]) -> list[tuple[str | None, str]]:
    """Each credited author may be named in CONTRIBUTORS, and only there."""
    return [(CONTRIBUTORS_FILE, name) for name in names]


def run_denylist(
    root: Path,
    denylist_setting: str,
    public_repo: str = PUBLIC_REPO,
    contributors: Iterable[str] = (),
) -> None:
    checker = _load_denylist_checker(root)
    allowed = canonical_allowances(public_repo) + contributor_allowances(contributors)
    allowances = [checker.Allowance(literal, path) for path, literal in allowed]
    code = checker.run(denylist_setting, root, ["."], allowances)
    if code != checker.EXIT_CLEAN:
        what = "found denylisted terms" if code == checker.EXIT_MATCH else "did not run, which is not a pass"
        raise ExportError(f"the name denylist {what} (exit {code})")


# --- 8. commit ----------------------------------------------------------------


def commit(root: Path, author: str, message: str) -> str:
    match = re.fullmatch(r"\s*(.+?)\s*<([^<>\s]+@[^<>\s]+)>\s*", author)
    if not match:
        raise ExportError(f"--author must be 'Name <email>', got {author!r}")
    name, email = match.groups()
    identity = {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }
    env = {**os.environ, **identity}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "--no-verify", "-m", message]):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, env=env, check=False)
        if result.returncode != 0:
            raise ExportError(f"git {args[0]} failed: {result.stderr.decode(errors='replace').strip()}")
    return _git(root, "rev-parse", "HEAD").stdout.decode().strip()


# --- 9. gitleaks --------------------------------------------------------------


def run_gitleaks(root: Path) -> None:
    gitleaks = shutil.which("gitleaks")
    if gitleaks is None:
        raise ExportError("gitleaks is not on PATH; install it, or pass --skip-gitleaks to export unscanned")
    result = subprocess.run(
        [
            gitleaks,
            "detect",
            "--source",
            str(root),
            "--config",
            str(root / ".gitleaks.toml"),
            "--redact",
            "--no-banner",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    sys.stderr.write(result.stdout + result.stderr)
    if result.returncode != 0:
        raise ExportError(f"gitleaks reported findings or failed (exit {result.returncode})")


# --- the whole export ---------------------------------------------------------


def export(
    *,
    repo: Path,
    ref: str,
    dest: Path,
    author: str,
    message: str | None,
    denylist_setting: str,
    mailmap: Path | None = None,
    skip_gitleaks: bool = False,
) -> str:
    """Run every step; return the new commit's sha."""
    sha = extract(repo, ref, dest)
    print(f"exported {sha[:12]} into {dest}")
    removed = apply_drops(dest)
    print(f"dropped {len(removed)} files under {', '.join(drop.path for drop in DROP)}")
    rewritten = rewrite_urls(dest)
    print(f"rewrote repository URLs in {len(rewritten)} files")
    add_files(dest)
    print(f"added {', '.join(ADD)}")
    names = contributor_names(repo, sha, mailmap)
    write_contributors(dest, names)
    print(f"wrote {CONTRIBUTORS_FILE} with {len(names)} names")
    dangling = dropped_references(dest, _tree_files(dest))
    if dangling:
        raise ExportError("exported files refer to dropped paths:\n" + "\n".join(dangling))
    print("no exported file refers to a dropped path")
    run_denylist(dest, denylist_setting, contributors=names)
    new_sha = commit(dest, author, message or "Initial public release")
    print(f"committed {new_sha[:12]} as the only commit")
    if skip_gitleaks:
        print("gitleaks SKIPPED on request: this tree has not been scanned for secrets", file=sys.stderr)
    else:
        run_gitleaks(dest)
    return new_sha


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the public tree from a ref, as one commit with no history.")
    parser.add_argument("--ref", default="origin/main", help="the ref to export (default: origin/main)")
    parser.add_argument("--dest", type=Path, required=True, help="an empty or missing directory for the export")
    parser.add_argument("--author", required=True, help="the commit's author and committer, 'Name <email>'")
    parser.add_argument("--message", help="the commit message (default: 'Initial public release')")
    parser.add_argument("--mailmap", type=Path, help="a git mailmap, kept outside the repository, for CONTRIBUTORS")
    parser.add_argument("--repo", type=Path, default=REPO_ROOT, help="the repository to export from")
    parser.add_argument("--skip-gitleaks", action="store_true", help="do not run gitleaks (reported, not silent)")
    args = parser.parse_args(argv)
    try:
        export(
            repo=args.repo,
            ref=args.ref,
            dest=args.dest.resolve(),
            author=args.author,
            message=args.message,
            denylist_setting=os.environ.get("EMBRY0_DENYLIST_FILE", ""),
            mailmap=args.mailmap,
            skip_gitleaks=args.skip_gitleaks,
        )
    except ExportError as exc:
        print(f"export FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"export done: {args.dest}. Nothing was pushed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
