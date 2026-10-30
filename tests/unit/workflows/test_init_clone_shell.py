"""The git-initializer clone shell must fail loudly when the clone fails.

`a && b || true` rescues the WHOLE chain, not just `b` — that precedence bug
let a failed clone exit 0, log "Repository cloned" 0.4s after sandbox create,
and hand every downstream agent an empty /workspace (found 2026-07-06 during
an access smoke test). Only the best-effort fetches may be rescued, so they
must be brace-scoped.
"""

import subprocess

from embry0.workspace_init.git import build_clone_shell


def _run_with_git_stub(git_stub: str, ref: str | None = None) -> int:
    """Run the clone shell in bash with `git` replaced by a stub function."""
    script = f"{git_stub}; {build_clone_shell('owner/repo', ref)}"
    return subprocess.run(["bash", "-c", script], capture_output=True).returncode


def test_clone_failure_propagates_nonzero():
    assert _run_with_git_stub("git() { return 1; }") != 0


def test_clone_and_fetch_success_exits_zero():
    assert _run_with_git_stub("git() { return 0; }") == 0


def test_fetch_failure_alone_is_best_effort():
    # clone (`git clone ...` -> $1=clone) succeeds; fetch (`git -C ...` -> $1=-C) fails.
    stub = 'git() { if [ "$1" = clone ]; then return 0; else return 1; fi; }'
    assert _run_with_git_stub(stub) == 0


def test_ref_checkout_failure_propagates_nonzero():
    # With a ref, the ref fetch stays best-effort but a failed checkout must
    # fail the chain — a job on the wrong ref is as bad as an empty workspace.
    stub = 'git() { if [ "$1" = clone ]; then return 0; else return 1; fi; }'
    assert _run_with_git_stub(stub, ref="feature/x") != 0


def test_ref_checkout_success_exits_zero():
    assert _run_with_git_stub("git() { return 0; }", ref="feature/x") == 0


def test_a_ref_reaches_git_as_one_argument_even_unvalidated(tmp_path):
    """build_clone_shell quotes what it interpolates, so a ref that skipped
    validate() still reaches git whole, as one argument."""
    calls = tmp_path / "calls"
    stub = f'git() {{ printf "%s\\n" "$@" >> {calls}; return 0; }}'
    status = _run_with_git_stub(stub, ref="a;b c")
    args = calls.read_text().splitlines()
    assert status == 0, f"the clone line must still run: exit {status}"
    assert "a;b c" in args, f"the ref must reach git as one argument: {args}"
    assert "+refs/heads/a;b c:refs/remotes/origin/a;b c" in args, f"the refspec must be one argument: {args}"
    assert "b" not in args and "c" not in args, f"no part of the ref may become a word of its own: {args}"


def test_every_fetch_of_the_ref_takes_it_after_the_end_of_options(tmp_path):
    """Each ``git fetch`` of the ref puts it after ``--``, so git reads it as a
    ref even if a caller skipped validate(). The stub fails the branch fetch,
    so the sha/tag fallback runs too."""
    calls = tmp_path / "calls"
    stub = (
        f'git() {{ printf "%s\\t" "$@" >> {calls}; echo >> {calls}; '
        'case "$*" in *"+refs/heads/"*) return 1;; esac; return 0; }'
    )
    assert _run_with_git_stub(stub, ref="-x") == 0, "the fallback fetch and checkout must run"
    fetches = [line.split("\t") for line in calls.read_text().splitlines() if "\tfetch\t" in f"\t{line}"]
    of_the_ref = [args for args in fetches if any("-x" in a for a in args)]
    assert len(of_the_ref) == 2, f"both fetches of the ref must run: {fetches}"
    for args in of_the_ref:
        where = [i for i, a in enumerate(args) if "-x" in a]
        assert "--" in args and all(i > args.index("--") for i in where), f"the ref must follow '--': {args}"
