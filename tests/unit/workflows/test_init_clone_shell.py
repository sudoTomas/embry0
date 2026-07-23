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
