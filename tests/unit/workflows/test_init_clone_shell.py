"""The init-node clone shell must fail loudly when the clone fails.

`a && b || true` rescues the WHOLE chain, not just `b` — that precedence bug
let a failed clone exit 0, log "Repository cloned" 0.4s after sandbox create,
and hand every downstream agent an empty /workspace (found 2026-07-06 during
the INT-655 access smoke). Only the best-effort main:main fetch may be
rescued, so it must be brace-scoped.
"""

import subprocess

from athanor.workflows.issue_to_pr.nodes import _build_clone_shell


def _run_with_git_stub(git_stub: str) -> int:
    """Run the clone shell in bash with `git` replaced by a stub function."""
    script = f"{git_stub}; {_build_clone_shell('owner/repo')}"
    return subprocess.run(["bash", "-c", script], capture_output=True).returncode


def test_clone_failure_propagates_nonzero():
    assert _run_with_git_stub("git() { return 1; }") != 0


def test_clone_and_fetch_success_exits_zero():
    assert _run_with_git_stub("git() { return 0; }") == 0


def test_fetch_failure_alone_is_best_effort():
    # clone (`git clone ...` -> $1=clone) succeeds; fetch (`git -C ...` -> $1=-C) fails.
    stub = 'git() { if [ "$1" = clone ]; then return 0; else return 1; fi; }'
    assert _run_with_git_stub(stub) == 0
