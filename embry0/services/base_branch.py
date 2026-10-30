"""One place that answers "which branch is this repo's integration branch?".

Before this module the answer was guessed three times and never the same
way: the init clone fetched ``main:main``
(``workspace_init/git.py``), the MR/PR fallback asked the forge for its
default branch (``services/gitlab_mrs.py``, ``services/github_prs.py``),
and the review/QA diff walked ``origin/HEAD`` in the sandbox
(``execution/workspace_diff.py``). A repo whose work lands on ``dev``
needed all three to agree, and nothing could tell them.

Precedence (Spec 1A §3.7): explicit job override → ``repo_preferences.
base_branch`` → the forge project's own default branch → ``main``.

Note the sibling with the same name: ``embry0.execution.workspace_diff.
resolve_base_branch`` asks the *sandbox's git* (symref) and takes a
``configured=`` value. This one asks *config and the forge API* and is what
produces that ``configured`` value. Import both fully qualified where they
meet.

Never raises: a failed preference read or forge call degrades to the next
step. A genuinely wrong branch still fails loudly — at the clone, where the
fetch error names it (Spec 1A §6).
"""

from __future__ import annotations

from typing import Any

import structlog

from embry0.git_refs import valid_ref

logger = structlog.get_logger(__name__)

DEFAULT_BASE_BRANCH = "main"

# A stored base branch is checked with embry0.git_refs.valid_ref, the rule the
# clone and the diff apply. The API validator (schemas/repo_preferences.py) is
# the boundary, but a row written before that validator existed — or by hand —
# must not reach a shell line either.


def _safe(name: Any) -> str | None:
    """``name`` when it is a usable branch name, else None."""
    if not isinstance(name, str):
        return None
    candidate = name.strip()
    if valid_ref(candidate):
        return candidate
    return None


async def preferred_base_branch(prefs_repo: Any, repo: str) -> str | None:
    """``repo_preferences.base_branch`` for ``repo``, or None.

    Separate from :func:`resolve_base_branch` because the QA orchestrator
    wants "preference or nothing": when there is no preference it has a
    cheaper, network-free answer available (the clone's ``origin/HEAD``)
    and must not be pushed onto the forge API instead.
    """
    if prefs_repo is None or not repo:
        return None
    try:
        row = await prefs_repo.get(repo)
    except Exception:
        logger.warning("base_branch_preference_lookup_failed", repo=repo, exc_info=True)
        return None
    if not row:
        return None
    return _safe(row.get("base_branch"))


#: ``source`` value meaning "nothing answered — this is the module default".
#: A caller that has a cheaper, network-free answer of its own (the sandbox
#: clone's ``origin/HEAD``) must treat a branch carrying this source as
#: *unconfigured* rather than as an answer, or it replaces a correct symref
#: lookup with a guess (RAV-1405).
FALLBACK_SOURCE = "fallback"


async def resolve_base_branch(
    *,
    repo: str,
    forge: str = "github",
    override: str | None = None,
    prefs_repo: Any = None,
    gitlab_client: Any = None,
    github_client: Any = None,
) -> str:
    """The branch a job for ``repo`` clones, diffs against and targets.

    ``override`` is the job-level value (``QAJobOverrides.base_branch``).
    ``gitlab_client``/``github_client`` are optional: pass only the one
    matching ``forge``; omitting it skips the forge-default step.
    """
    branch, _source = await resolve_base_branch_with_source(
        repo=repo,
        forge=forge,
        override=override,
        prefs_repo=prefs_repo,
        gitlab_client=gitlab_client,
        github_client=github_client,
    )
    return branch


async def resolve_base_branch_with_source(
    *,
    repo: str,
    forge: str = "github",
    override: str | None = None,
    prefs_repo: Any = None,
    gitlab_client: Any = None,
    github_client: Any = None,
) -> tuple[str, str]:
    """:func:`resolve_base_branch`, plus which step answered.

    The ``source`` is ``"override"``, ``"preference"``, ``"forge"`` or
    :data:`FALLBACK_SOURCE` — the same label this logs. It exists because
    "main, because nobody knew" and "main, because the project says so" are
    the same string but not the same fact, and a caller with its own cheaper
    lookup needs to tell them apart (see :data:`FALLBACK_SOURCE`).
    """
    chosen = _safe(override)
    if chosen:
        logger.info("base_branch_resolved", repo=repo, base_branch=chosen, source="override")
        return chosen, "override"

    chosen = await preferred_base_branch(prefs_repo, repo)
    if chosen:
        logger.info("base_branch_resolved", repo=repo, base_branch=chosen, source="preference")
        return chosen, "preference"

    chosen = await _forge_default(
        repo=repo,
        forge=forge,
        gitlab_client=gitlab_client,
        github_client=github_client,
    )
    if chosen:
        logger.info("base_branch_resolved", repo=repo, base_branch=chosen, source="forge")
        return chosen, "forge"

    logger.info("base_branch_resolved", repo=repo, base_branch=DEFAULT_BASE_BRANCH, source=FALLBACK_SOURCE)
    return DEFAULT_BASE_BRANCH, FALLBACK_SOURCE


async def _forge_default(
    *,
    repo: str,
    forge: str,
    gitlab_client: Any,
    github_client: Any,
) -> str | None:
    """The forge project's own default branch, or None when unavailable.

    Deliberately the RAW project reads (``GitLabClient.get_project`` /
    ``GitHubClient.get_repo``) rather than the ``default_branch_for`` /
    ``get_repo_default_branch`` wrappers those two forges keep for the MR/PR
    create path. Both wrappers answer the literal ``"main"`` when the call
    fails, which is indistinguishable here from a project that really does
    default to ``main`` — so ``resolve_base_branch`` logged ``source="forge"``
    for a guess, and that guess became the clone fetch ref, the review diff
    base and the MR target for the whole job (Spec 1A §3.8). None means "the
    forge did not answer", and the caller falls through to its own fallback
    with an honest ``source="fallback"``.
    """
    try:
        if forge == "gitlab" and gitlab_client is not None:
            project = await gitlab_client.get_project(repo)
            return _safe((project or {}).get("default_branch"))
        if forge == "github" and github_client is not None:
            record = await github_client.get_repo(repo)
            return _safe((record or {}).get("default_branch"))
    except Exception:
        logger.warning("base_branch_forge_default_failed", repo=repo, forge=forge, exc_info=True)
    return None
