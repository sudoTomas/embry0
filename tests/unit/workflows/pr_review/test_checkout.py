"""The review sandbox's checkout script, run against real git repositories (#30)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from embry0.workflows.pr_review.checkout import (
    HEAD_GONE_EXIT,
    CheckoutResult,
    build_checkout_script,
    head_fetch_ref,
    parse_checkout_output,
)

needs_git = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None, reason="needs git and bash")


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> SimpleNamespace:
    """An origin with main, a two-commit feature branch and a fork-style refs/pull/7/head,
    and a workspace cloned the way prep_qa_sandbox_clone clones (shallow, base branch only)."""
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "--quiet", "--initial-branch=main")
    (src / "app.py").write_text("a = 1\n")
    _git(src, "add", ".")
    _git(src, "commit", "--quiet", "-m", "base")
    base = _git(src, "rev-parse", "HEAD")
    _git(src, "checkout", "--quiet", "-b", "feature/login")
    (src / "app.py").write_text("a = 1\nb = 2\n")
    _git(src, "commit", "--quiet", "-am", "first")
    first = _git(src, "rev-parse", "HEAD")
    (src / "login.py").write_text("def login():\n    return True\n")
    _git(src, "add", ".")
    _git(src, "commit", "--quiet", "-m", "second")
    head = _git(src, "rev-parse", "HEAD")
    bare = tmp_path / "origin.git"
    _git(tmp_path, "clone", "--quiet", "--bare", str(src), str(bare))
    _git(bare, "update-ref", "refs/pull/7/head", head)
    _git(bare, "config", "uploadpack.allowAnySHA1InWant", "true")
    ws = tmp_path / "ws"
    _git(tmp_path, "clone", "--quiet", "--depth=50", "--branch", "main", f"file://{bare}", str(ws))
    return SimpleNamespace(
        src=src, bare=bare, ws=ws, out=ws / ".git" / "embry0-review", base=base, first=first, head=head
    )


def _run(repo: SimpleNamespace, **kwargs: str) -> subprocess.CompletedProcess[str]:
    script = build_checkout_script(workspace=str(repo.ws), out_dir=str(repo.out), base_ref="main", **kwargs)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


@needs_git
@pytest.mark.parametrize("fetch_ref", ["refs/heads/feature/login", "refs/pull/7/head"])
def test_the_exact_head_is_checked_out_and_the_change_written(repo: SimpleNamespace, fetch_ref: str) -> None:
    done = _run(repo, fetch_ref=fetch_ref, head_sha=repo.head)

    assert done.returncode == 0, done.stderr
    result = parse_checkout_output(done.stdout)
    assert result is not None and result.delta == "none"
    assert result.base_sha == repo.base, "the merge base is the sha the prompt names as the base"
    assert _git(repo.ws, "rev-parse", "HEAD") == repo.head
    assert _git(repo.ws, "rev-parse", "refs/embry0-review/head") == repo.head, (
        f"the head must come through {fetch_ref}, not only through the fetch-by-sha fallback"
    )
    assert (repo.out / "files.txt").read_text().split() == ["app.py", "login.py"]
    diff = (repo.out / "changes.diff").read_text()
    assert "+b = 2" in diff and "+def login():" in diff


@needs_git
def test_a_re_review_gets_the_delta_since_the_previous_sha(repo: SimpleNamespace) -> None:
    done = _run(repo, fetch_ref="refs/heads/feature/login", head_sha=repo.head, previous_sha=repo.first)

    assert done.returncode == 0, done.stderr
    assert (parse_checkout_output(done.stdout) or SimpleNamespace(delta=None)).delta == "yes"
    delta = (repo.out / "delta.diff").read_text()
    assert "login.py" in delta and "+b = 2" not in delta, "the delta must hold only what changed since the last review"


@needs_git
def test_a_rewritten_branch_reports_delta_rewritten(repo: SimpleNamespace) -> None:
    _git(repo.src, "checkout", "--quiet", "feature/login")
    _git(repo.src, "reset", "--quiet", "--hard", "main")
    (repo.src / "other.py").write_text("x = 1\n")
    _git(repo.src, "add", ".")
    _git(repo.src, "commit", "--quiet", "-m", "rewritten")
    new_head = _git(repo.src, "rev-parse", "HEAD")
    _git(repo.src, "push", "--quiet", "--force", str(repo.bare), "feature/login")

    done = _run(repo, fetch_ref="refs/heads/feature/login", head_sha=new_head, previous_sha=repo.head)

    assert done.returncode == 0, done.stderr
    assert (parse_checkout_output(done.stdout) or SimpleNamespace(delta=None)).delta == "rewritten", (
        "a previous sha that is not an ancestor is not a delta"
    )
    assert not (repo.out / "delta.diff").exists()
    assert (repo.out / "files.txt").read_text().split() == ["other.py"]


@needs_git
def test_a_shallow_clone_is_deepened_until_base_and_head_meet(repo: SimpleNamespace, tmp_path: Path) -> None:
    _git(repo.src, "checkout", "--quiet", "main")
    (repo.src / "later.py").write_text("c = 3\n")
    _git(repo.src, "add", ".")
    _git(repo.src, "commit", "--quiet", "-m", "main moves on")
    _git(repo.src, "push", "--quiet", str(repo.bare), "main")
    ws = tmp_path / "shallow"
    _git(tmp_path, "clone", "--quiet", "--depth=1", "--branch", "main", f"file://{repo.bare}", str(ws))
    script = build_checkout_script(
        workspace=str(ws),
        out_dir=str(ws / ".git" / "embry0-review"),
        base_ref="main",
        fetch_ref="refs/heads/feature/login",
        head_sha=repo.head,
        depth=1,
    )
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    assert done.returncode == 0, done.stderr
    result = parse_checkout_output(done.stdout)
    assert result is not None and result.base_sha == repo.base, (
        "a depth-1 clone has no merge base until deepened, and main has moved past it"
    )
    assert (ws / ".git" / "embry0-review" / "files.txt").read_text().split() == ["app.py", "login.py"]


@needs_git
def test_head_that_is_gone_exits_3_with_a_message(repo: SimpleNamespace) -> None:
    done = _run(repo, fetch_ref="refs/heads/feature/login", head_sha="f" * 40)

    assert done.returncode == HEAD_GONE_EXIT == 3
    assert "is no longer on the forge" in done.stderr


@pytest.mark.parametrize(
    "kwargs",
    [
        {"fetch_ref": "refs/heads/x", "head_sha": "HEAD; rm -rf /", "base_ref": "main"},
        {"fetch_ref": "refs/heads/x", "head_sha": "a" * 40, "base_ref": "main", "previous_sha": "nope"},
        {"fetch_ref": "", "head_sha": "a" * 40, "base_ref": "main"},
        {"fetch_ref": "refs/heads/x", "head_sha": "a" * 40, "base_ref": ""},
    ],
)
def test_malformed_input_is_refused_before_any_shell_runs(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        build_checkout_script(**kwargs)


def test_refs_are_quoted() -> None:
    script = build_checkout_script(fetch_ref="refs/heads/feat/$(touch x)", head_sha="a" * 40, base_ref="main")
    assert "'+refs/heads/feat/$(touch x):refs/embry0-review/head'" in script


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"forge": "github", "number": 7, "head_ref": "feature/login", "from_fork": False}, "refs/heads/feature/login"),
        ({"forge": "github", "number": 7, "head_ref": "feature/login", "from_fork": True}, "refs/pull/7/head"),
        (
            {"forge": "gitlab", "number": 5, "head_ref": "feature/search", "from_fork": True},
            "refs/merge-requests/5/head",
        ),
        ({"forge": "gitlab", "number": 5, "head_ref": "", "from_fork": False}, "refs/merge-requests/5/head"),
    ],
)
def test_the_head_ref_follows_the_forge_and_the_fork(kwargs: dict, expected: str) -> None:
    assert head_fetch_ref(**kwargs) == expected


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        (f"fetching\nembry0-review-checkout: delta=yes base={'b' * 40}", CheckoutResult("yes", "b" * 40)),
        ("embry0-review-checkout: delta=rewritten base=\n", CheckoutResult("rewritten", "")),
        ("embry0-review-checkout: delta=maybe base=", None),
        ("embry0-review-checkout: delta=yes base=origin/main", CheckoutResult("yes", "")),
        ("", None),
    ],
)
def test_the_checkout_result_line_is_read(stdout: str, expected: CheckoutResult | None) -> None:
    assert parse_checkout_output(stdout) == expected
