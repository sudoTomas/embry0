"""Check out the exact head of a pull request in the review sandbox (#30).

The base repository is cloned through the git-proxy first
(``prep_qa_sandbox_clone``, base branch, depth 50). This script then fetches
the head into ``refs/embry0-review/head``:

* a same-repo pull request: its branch, ``refs/heads/<head_ref>``;
* a fork: ``refs/pull/<n>/head`` (GitHub) or ``refs/merge-requests/<iid>/head``
  (GitLab). Both refs live in the base repository, so the git-proxy's
  one-repository rule holds (it forwards upload-pack without filtering refs).

It checks out the sha the trigger named, deepens until base and head share a
merge base, and writes the change, the changed paths and, for a re-review,
the delta since the previously reviewed sha into ``REVIEW_DIR``. That
directory is under ``/workspace`` (the Ring-2 allow-list) and inside ``.git``,
where no pull request can put a file of its own.

The last stdout line is ``embry0-review-checkout: delta=<state> base=<sha>``.
``delta`` is ``none`` (no previous review), ``yes``, or ``rewritten``: the
previous sha is gone or no longer an ancestor of the head (a force-push or a
rebase), so a delta from it would be meaningless. ``base`` is the merge base
the change is measured from.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

REVIEW_DIR = "/workspace/.git/embry0-review"
CHANGES_DIFF = f"{REVIEW_DIR}/changes.diff"
DELTA_DIFF = f"{REVIEW_DIR}/delta.diff"
FILES_LIST = f"{REVIEW_DIR}/files.txt"
HEAD_GONE_EXIT = 3
DELTA_STATES: tuple[str, ...] = ("none", "yes", "rewritten")

_RESULT_PREFIX = "embry0-review-checkout:"
_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")


@dataclass(frozen=True)
class CheckoutResult:
    delta: str  # one of DELTA_STATES
    base_sha: str  # the merge base; "" when git could not name one


def head_fetch_ref(*, forge: str, number: int, head_ref: str, from_fork: bool) -> str:
    if from_fork or not head_ref:
        return f"refs/merge-requests/{number}/head" if forge == "gitlab" else f"refs/pull/{number}/head"
    return f"refs/heads/{head_ref}"


def build_checkout_script(
    *,
    fetch_ref: str,
    head_sha: str,
    base_ref: str,
    previous_sha: str = "",
    workspace: str = "/workspace",
    out_dir: str = REVIEW_DIR,
    depth: int = 50,
) -> str:
    if not _SHA_RE.match(head_sha):
        raise ValueError(f"head_sha {head_sha!r} is not a commit sha")
    if previous_sha and not _SHA_RE.match(previous_sha):
        raise ValueError(f"previous_sha {previous_sha!r} is not a commit sha")
    if not fetch_ref or not base_ref:
        raise ValueError("fetch_ref and base_ref are required")
    q = shlex.quote
    head_spec = q(f"+{fetch_ref}:refs/embry0-review/head")
    base_remote = f"refs/remotes/origin/{base_ref}"
    base_spec = q(f"+refs/heads/{base_ref}:{base_remote}")
    commit = q(f"{head_sha}^{{commit}}")
    gone = (
        f"embry0-review: commit {head_sha} is no longer on the forge; "
        "the branch was rewritten after the review was asked for"
    )
    lines = [
        "set -u",
        f"cd {q(workspace)} || exit 4",
        f"out={q(out_dir)}",
        'mkdir -p "$out"',
        f"git fetch --quiet --depth={depth} origin {head_spec} || true",
        f"if ! git cat-file -e {commit} 2>/dev/null; then",
        f"  git fetch --quiet --depth={depth} origin {q(head_sha)} 2>/dev/null || true",
        "fi",
        f"if ! git cat-file -e {commit} 2>/dev/null; then",
        f"  echo {q(gone)} >&2",
        f"  exit {HEAD_GONE_EXIT}",
        "fi",
        f"git -c advice.detachedHead=false checkout --quiet --force --detach {q(head_sha)} || exit 4",
        f"base={q(base_remote)}",
        "for _ in 1 2 3 4 5; do",
        '  git merge-base "$base" HEAD >/dev/null 2>&1 && break',
        f"  git fetch --quiet --deepen=200 origin {base_spec} {head_spec} 2>/dev/null || break",
        "done",
        'mb=$(git merge-base "$base" HEAD 2>/dev/null || echo "$base")',
        'git diff --no-color "$mb" HEAD > "$out/changes.diff" || exit 4',
        'git diff --name-only "$mb" HEAD > "$out/files.txt" || exit 4',
        "delta=none",
    ]
    if previous_sha:
        lines += [
            f"prev={q(previous_sha)}",
            'git cat-file -e "$prev^{commit}" 2>/dev/null || '
            f'git fetch --quiet --depth={depth} origin "$prev" 2>/dev/null || true',
            'if git cat-file -e "$prev^{commit}" 2>/dev/null && git merge-base --is-ancestor "$prev" HEAD 2>/dev/null; then',
            '  git diff --no-color "$prev" HEAD > "$out/delta.diff" && delta=yes',
            "else",
            "  delta=rewritten",
            "fi",
        ]
    lines.append('base_sha=$(git rev-parse --verify --quiet "$mb^{commit}" 2>/dev/null || true)')
    lines.append(f'echo "{_RESULT_PREFIX} delta=$delta base=$base_sha"')
    return "\n".join(lines) + "\n"


def parse_checkout_output(stdout: str) -> CheckoutResult | None:
    for line in reversed(stdout.splitlines()):
        if not line.startswith(_RESULT_PREFIX):
            continue
        fields = dict(part.split("=", 1) for part in line[len(_RESULT_PREFIX) :].split() if "=" in part)
        delta = fields.get("delta", "")
        if delta not in DELTA_STATES:
            return None
        base_sha = fields.get("base", "")
        return CheckoutResult(delta=delta, base_sha=base_sha if _SHA_RE.match(base_sha) else "")
    return None
