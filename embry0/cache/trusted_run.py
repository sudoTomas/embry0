"""Which QA runs may populate a repository's shared cache.

A per-repo shared volume is reused by every QA job of its repository, and a
pre-built QA image becomes the base of every QA sandbox of its repository. So
both are only ever populated from the repository's default branch, decided
from what the forge reports, never from a value a job or a preference
supplies:

* the **default branch and its current head** come from the forge itself:
  ``git ls-remote --symref <repo url> HEAD``, run through
  :mod:`embry0.execution.sandbox_git` in the QA bootstrap sandbox, where the
  git-proxy answers for the repository with its own credential;
* a run is **trusted** when it QAs that branch *and* its resolved head is that
  branch's current head. A job's ``base_branch`` override and the repository's
  ``base_branch`` preference choose the diff base only; they never make a run
  trusted.

When the forge does not answer, nothing is trusted: the run gets a per-job
volume and no pre-built image is selected for it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import structlog

from embry0.execution import sandbox_git
from embry0.git_refs import valid_ref

logger = structlog.get_logger(__name__)

#: A full commit id: SHA-1 (40) or SHA-256 (64) hex.
_COMMIT = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?")
_SYMREF_PREFIX = "ref: refs/heads/"


@dataclass(frozen=True, slots=True)
class DefaultHead:
    """The repository's default branch and the commit it points at, as the forge reported them."""

    branch: str
    sha: str


def is_commit_id(value: object) -> bool:
    """True for a full, lower-case SHA-1 or SHA-256 commit id."""
    return isinstance(value, str) and _COMMIT.fullmatch(value) is not None


def parse_symref_head(text: str) -> DefaultHead | None:
    """The default branch and its head in ``git ls-remote --symref <url> HEAD`` output, or None.

    The output has two lines for a repository with a default branch::

        ref: refs/heads/main\tHEAD
        3f1c...e9\tHEAD

    Anything else answers None, so the run is not trusted: no symref line or
    no commit line, more than one of either (the answer does not name one
    branch and one commit), or a branch name or commit id that is not well
    formed.
    """
    branches: list[str] = []
    shas: list[str] = []
    for line in str(text or "").splitlines():
        value, sep, ref = line.partition("\t")
        if not sep or ref.strip() != "HEAD":
            continue
        value = value.strip()
        if value.startswith(_SYMREF_PREFIX):
            branches.append(value[len(_SYMREF_PREFIX) :])
        else:
            shas.append(value)
    if len(branches) != 1 or len(shas) != 1:
        return None
    branch, sha = branches[0], shas[0]
    if not valid_ref(branch) or not is_commit_id(sha):
        return None
    return DefaultHead(branch=branch, sha=sha)


async def forge_default_head(
    docker: Any,
    container_id: str,
    *,
    origin_url: str,
    job_id: str | None = None,
) -> DefaultHead | None:
    """Ask the forge for the repository's default branch and its head; None when it does not answer.

    ``origin_url`` is the repository's forge URL (the one the clone uses);
    embry0's own git configuration in the sandbox sends it through the
    git-proxy. The command runs outside any repository (``repo=None``), so
    nothing in the workspace's ``.git`` is read.
    """
    argv = sandbox_git.script(sandbox_git.git("ls-remote", "--symref", origin_url, "HEAD"), repo=None)
    try:
        raw = await sandbox_git.run(
            docker, container_id, argv, step=sandbox_git.GitStep.DEFAULT_BRANCH, timeout=30, job_id=job_id
        )
    except Exception as exc:  # noqa: BLE001 — no answer means "not trusted", never a failed run
        logger.warning("qa_forge_default_branch_unavailable", job_id=job_id, error=str(exc)[:300])
        return None
    head = parse_symref_head(raw)
    if head is None:
        logger.warning("qa_forge_default_branch_unreadable", job_id=job_id)
    return head


def trusted_per_repo_volume(
    requested_scope: str,
    *,
    branch: str | None,
    head_sha: str | None,
    default: DefaultHead | None,
) -> bool:
    """Whether a ``per-repo`` shared volume may back this QA run.

    True only when the config asks for ``per-repo``, the forge reported the
    repository's default branch (``default``), the run QAs that branch, and
    the run's resolved head (``head_sha``) is that branch's current head.
    Every other run gets a per-job volume, which is removed with its job.
    """
    if requested_scope != "per-repo" or default is None:
        return False
    if not branch or branch != default.branch:
        return False
    return bool(head_sha) and head_sha == default.sha
