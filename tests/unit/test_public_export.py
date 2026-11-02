"""#533: scripts/export_public.py builds the public tree from a ref, as one commit.

Each step is tested on a small synthetic repository: nothing here reads this
repository's history or names anything a denylist guards. The made-up term
stands in for a denylisted name, as in test_denylist_check.py.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "export_public.py"
TERM = "glorbnax"  # made up; names nothing
AUTHOR = "Release Bot <release@example.invalid>"


def _load() -> ModuleType:
    assert SCRIPT.is_file(), f"the export script {SCRIPT} is missing"
    spec = importlib.util.spec_from_file_location("export_public", SCRIPT)
    assert spec is not None and spec.loader is not None, f"cannot load {SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ex = _load()


def _git(repo: Path, *args: str, name: str = "Ada Example", email: str = "ada@example.invalid") -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True)
    return out.stdout


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """A private-shaped repository: shipped files, every DROP path, the denylist checker."""
    repo = tmp_path / "private"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    files = {
        "README.md": f"# Widget\n\nhttps://github.com/{ex.PUBLIC_REPO}\n",
        "docs/guide.md": "# Guide\n\nRelease notes go under `internal/`.\n",
        "src/app.py": "print('app')\n",
        ".github/CODEOWNERS": f"* @{ex.PUBLIC_REPO.split('/')[0]}\n",
        ".gitleaks.toml": "[extend]\nuseDefault = true\n",
    }
    for drop in ex.DROP:
        files[drop.path + "README.md" if drop.is_prefix else drop.path] = "private material\n"
    _write(repo, files)
    (repo / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "check_denylist.py", repo / "scripts" / "check_denylist.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "first")
    _write(repo, {"src/more.py": "x = 1\n"})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "second", name="Bo Example", email="bo@example.invalid")
    # Unmerged work by someone else: never credited.
    _git(repo, "checkout", "-q", "-b", "unmerged")
    _write(repo, {"src/side.py": "y = 2\n"})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "side", name="Cy Outside", email="cy@example.invalid")
    _git(repo, "checkout", "-q", "main")
    return repo


@pytest.fixture
def denylist(tmp_path: Path) -> Path:
    path = tmp_path / "denylist.txt"
    path.write_text(f"{TERM}\n")
    return path


def _export(source: Path, dest: Path, denylist: Path | None, **kwargs: object) -> str:
    return ex.export(
        repo=source,
        ref="main",
        dest=dest,
        author=AUTHOR,
        message=None,
        denylist_setting=str(denylist) if denylist else "",
        skip_gitleaks=kwargs.pop("skip_gitleaks", True),
        **kwargs,
    )


# --- the declarative lists -------------------------------------------------------


def test_drop_list_holds_internal_and_the_roadmap():
    """internal/ never ships (internal/README.md); the owner dropped the roadmap on 2026-10-09."""
    paths = {drop.path for drop in ex.DROP}
    assert "internal/" in paths, "the export no longer drops internal/"
    assert "docs/enterprise-scale-roadmap.md" in paths, "the export no longer drops the roadmap"
    assert all(drop.reason.strip() for drop in ex.DROP), "every DROP entry says why"


def test_public_repo_matches_the_canonical_urls():
    urls = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["urls"]
    assert urls["Repository"] == ex.PUBLIC_REPO_URL == f"https://github.com/{ex.PUBLIC_REPO}", urls


def test_dependabot_config_is_the_validated_one():
    config = yaml.safe_load(ex.ADD[".github/dependabot.yml"])
    assert config["version"] == 2
    ecosystems = {update["package-ecosystem"] for update in config["updates"]}
    assert ecosystems == {"uv", "npm", "docker", "github-actions"}, ecosystems
    docker = next(u for u in config["updates"] if u["package-ecosystem"] == "docker")
    assert docker["ignore"][0]["dependency-name"] == "python", "the python base image must move with .python-version"
    assert not (ROOT / ".github" / "dependabot.yml").exists() or not (ROOT / "internal").exists(), (
        "the private repository must not carry .github/dependabot.yml: the export adds it"
    )


def test_dependabot_bumps_the_agent_sdk_alone():
    """claude-agent-sdk bundles the Claude CLI agents run (#497, #288): its bump is a pull request of its own."""
    copies = {"scripts/export_public.py": ex.ADD[".github/dependabot.yml"]}
    if (ROOT / ".github" / "dependabot.yml").exists():
        copies[".github/dependabot.yml"] = (ROOT / ".github" / "dependabot.yml").read_text()
    for where, text in copies.items():
        uv = next(u for u in yaml.safe_load(text)["updates"] if u["package-ecosystem"] == "uv")
        groups = uv.get("groups", {})
        alone = [name for name, group in groups.items() if group.get("patterns") == ["claude-agent-sdk"]]
        assert alone, f"{where}: no uv group holds claude-agent-sdk alone, so its bump rides with others: {groups}"
        for name, group in groups.items():
            if name in alone:
                continue
            assert "claude-agent-sdk" in group.get("exclude-patterns", []), (
                f"{where}: the uv group {name!r} could take claude-agent-sdk: exclude it there"
            )


def test_canonical_allowances_cover_only_the_public_repository():
    owner = ex.PUBLIC_REPO.split("/")[0]
    assert ex.canonical_allowances() == [(None, f"github.com/{ex.PUBLIC_REPO}"), (".github/CODEOWNERS", f"@{owner}")]


# --- the steps -------------------------------------------------------------------


def test_drops_fail_on_an_entry_the_ref_lacks(tmp_path: Path):
    (tmp_path / "kept.md").write_text("x\n")
    with pytest.raises(ex.ExportError, match="DROP names gone/, which the exported ref does not have"):
        ex.apply_drops(tmp_path, (ex.Drop("gone/", "test"),))


def test_url_rewrite_is_exact(tmp_path: Path):
    _write(
        tmp_path,
        {
            "a.md": (
                "https://github.com/old/widget\n"
                "git@github.com:old/widget.git\n"
                "https://github.com/old/widget-other\n"
                "https://gitlab.com/old/widget\n"
            ),
            "b.bin": "\0binary github.com/old/widget",
        },
    )
    assert ex.rewrite_urls(tmp_path, {}) == []
    assert ex.rewrite_urls(tmp_path, {"old/widget": "new/gadget"}) == ["a.md"]
    assert (tmp_path / "a.md").read_text() == (
        "https://github.com/new/gadget\n"
        "git@github.com:new/gadget.git\n"
        "https://github.com/old/widget-other\n"
        "https://gitlab.com/old/widget\n"
    )


def test_added_file_must_not_already_exist(tmp_path: Path):
    _write(tmp_path, {".github/dependabot.yml": "version: 2\n"})
    with pytest.raises(ex.ExportError, match="already exists"):
        ex.add_files(tmp_path)


def test_contributors_are_merged_authors_by_name_only(source: Path, tmp_path: Path):
    sha = _git(source, "rev-parse", "main").strip()
    assert ex.contributor_names(source, sha) == ["Ada Example", "Bo Example"]

    mailmap = tmp_path / "mailmap"
    mailmap.write_text("Ada Example <bo@example.invalid>\n")
    assert ex.contributor_names(source, sha, mailmap) == ["Ada Example"], "the mailmap did not fold Bo into Ada"

    ex.write_contributors(tmp_path, ["Ada Example"])
    text = (tmp_path / "CONTRIBUTORS").read_text()
    assert "Ada Example" in text and "@" not in text, text


# --- the whole export --------------------------------------------------------------


def test_export_is_one_commit_without_dropped_paths(source: Path, tmp_path: Path, denylist: Path):
    dest = tmp_path / "public"
    sha = _export(source, dest, denylist)

    assert _git(dest, "rev-list", "--count", "HEAD").strip() == "1"
    assert _git(dest, "rev-list", "--parents", "-n", "1", "HEAD").split() == [sha], "the commit has a parent"
    assert _git(dest, "log", "-1", "--format=%an <%ae>").strip() == AUTHOR
    tracked = set(_git(dest, "ls-files").split())
    for drop in ex.DROP:
        assert not any(drop.covers(rel) for rel in tracked), f"{drop.path} was exported"
    assert {".github/dependabot.yml", "CONTRIBUTORS", "src/app.py", "src/more.py"} <= tracked, tracked
    assert "src/side.py" not in tracked, "unmerged work was exported"
    contributors = (dest / "CONTRIBUTORS").read_text()
    assert "Ada Example" in contributors and "Bo Example" in contributors
    assert "Cy Outside" not in contributors, "an author of unmerged work was credited"


def test_export_writes_no_bytecode_into_the_tree(source: Path, tmp_path: Path, denylist: Path, monkeypatch):
    # The checker is loaded from the exported tree; importing it must not leave
    # __pycache__ there for the denylist to scan and the commit to pick up.
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    monkeypatch.delenv("PYTHONPYCACHEPREFIX", raising=False)
    monkeypatch.setattr(sys, "pycache_prefix", None)
    dest = tmp_path / "public"
    _export(source, dest, denylist)

    written = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.name == "__pycache__" or p.suffix == ".pyc")
    assert not written, f"the export wrote bytecode into the exported tree: {written}"
    assert sys.dont_write_bytecode is False, "the export left bytecode writing switched off for its caller"


def test_export_fails_on_a_denylisted_term(source: Path, tmp_path: Path, denylist: Path):
    _write(source, {"docs/guide.md": f"Built for {TERM}.\n"})
    _git(source, "commit", "-qam", "leak")
    with pytest.raises(ex.ExportError, match="found denylisted terms"):
        _export(source, tmp_path / "public", denylist)


def test_export_allows_the_owner_only_in_canonical_urls(source: Path, tmp_path: Path):
    """The public repository's owner may be denylisted, yet appear in its own canonical URLs."""
    owner = ex.PUBLIC_REPO.split("/")[0]
    denylist = tmp_path / "owner-denylist.txt"
    denylist.write_text(f"{owner}\n")
    _export(source, tmp_path / "clean", denylist)  # README and CODEOWNERS name the owner, as allowed

    _write(source, {"src/app.py": f"REPO = '{owner}/other'\n"})
    _git(source, "commit", "-qam", "owner outside a canonical URL")
    with pytest.raises(ex.ExportError, match="found denylisted terms"):
        _export(source, tmp_path / "dirty", denylist)


def test_export_allows_a_credited_contributor_only_in_contributors(source: Path, tmp_path: Path):
    """A credited author may be denylisted, yet must be named in CONTRIBUTORS and nowhere else."""
    denylist = tmp_path / "author-denylist.txt"
    denylist.write_text("Bo Example\n")
    _export(source, tmp_path / "clean", denylist)
    assert "Bo Example" in (tmp_path / "clean" / "CONTRIBUTORS").read_text()

    _write(source, {"docs/guide.md": "Thanks to Bo Example.\n"})
    _git(source, "commit", "-qam", "author outside CONTRIBUTORS")
    with pytest.raises(ex.ExportError, match="found denylisted terms"):
        _export(source, tmp_path / "dirty", denylist)


def test_export_without_a_denylist_is_not_a_pass(source: Path, tmp_path: Path):
    with pytest.raises(ex.ExportError, match="did not run, which is not a pass"):
        _export(source, tmp_path / "public", None)


def test_export_fails_on_a_reference_to_a_dropped_path(source: Path, tmp_path: Path, denylist: Path):
    _write(source, {"docs/guide.md": "See [the plan](../internal/README.md).\n"})
    _git(source, "commit", "-qam", "dangling link")
    with pytest.raises(ex.ExportError, match=r"docs/guide.md:1: internal/README.md"):
        _export(source, tmp_path / "public", denylist)


def test_export_refuses_a_non_empty_destination(source: Path, tmp_path: Path, denylist: Path):
    dest = tmp_path / "public"
    _write(dest, {"stale.txt": "x\n"})
    with pytest.raises(ex.ExportError, match="is not empty"):
        _export(source, dest, denylist)


def test_export_runs_gitleaks_unless_skipped(source: Path, tmp_path: Path, denylist: Path, monkeypatch):
    monkeypatch.setattr(ex.shutil, "which", lambda name: None)
    with pytest.raises(ex.ExportError, match="gitleaks is not on PATH"):
        _export(source, tmp_path / "public", denylist, skip_gitleaks=False)


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks is not installed")
def test_export_fails_on_a_leaked_secret(source: Path, tmp_path: Path, denylist: Path):
    _export(source, tmp_path / "clean", denylist, skip_gitleaks=False)  # gitleaks runs and passes a clean tree

    # Assembled at run time so this file holds no key-shaped literal.
    key = "ghp_" + "Zq7Lm2Vx9Rt4Kp8Wn3Ys6Hd1Fj5Gc0Ba2Ne7"
    _write(source, {"src/config.py": f'GITHUB_TOKEN = "{key}"\n'})
    _git(source, "add", "-A")
    _git(source, "commit", "-qm", "leak a token")
    with pytest.raises(ex.ExportError, match="gitleaks reported findings"):
        _export(source, tmp_path / "public", denylist, skip_gitleaks=False)
