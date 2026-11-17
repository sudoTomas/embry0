"""Which containers a job's log tool may read: the scope its sandbox is enrolled with at the context proxy.

The deployment's ``context_proxy_container_allowlist`` says which containers an agent may read at all, not whose
they are, so on its own it grants a job nothing. A job reads its own repository's containers:

- a developer job, the containers its repository's watched stack names in the stack's own
  ``context_proxy_container_allowlist`` (exact names, and the names its patterns admit) that the job's evidence
  names;
- the log analysis of a stack, the stack's own list the same way, or, for a stack that names none, the containers
  the stack's own query returned in the tick that started it.

Every scope is also cut to the deployment's allowlist. A scope a job carries (an analysis job's context, which a
job started again copies, and a paused job's checkpoint) is a ceiling, never a grant: whenever the job's sandbox is
enrolled, at init and at resume, it is cut again to what the stored stack and the deployment admit then. A lookup
that does not answer grants nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import structlog

from embry0.execution.proxy.container_allowlist import ContainerAllowlist, is_pattern
from embry0.storage.database import StoredValueTooDeep

logger = structlog.get_logger(__name__)

#: The setting, of the process config and of a stack, that names containers.
SETTING = "context_proxy_container_allowlist"


def deployment_allowlist(config: Any) -> ContainerAllowlist:
    """The deployment's container allowlist, read off the process config."""
    return ContainerAllowlist(str(getattr(config, SETTING, "") or "").split(","))


def own_entries(stack: Any) -> list[str] | None:
    """The entries of ``stack``'s own container allowlist, or None when it has none of its own.

    An empty one is its own, and names nothing. A stored value that no longer checks is none of its own
    (``usable_settings``).
    """
    from embry0.services.watcher_stacks import usable_settings

    settings = usable_settings(stack)
    if SETTING not in settings:
        return None
    return [entry.strip() for entry in str(settings[SETTING] or "").split(",") if entry.strip()]


def admitted(
    names: Iterable[Any], deployment: ContainerAllowlist, ceiling: ContainerAllowlist | None = None
) -> list[str]:
    """The ``names`` that ``ceiling`` (when given) and the deployment's allowlist both admit, in the deployment's
    spelling, sorted and once each."""
    out: set[str] = set()
    for name in names:
        text = str(name or "")
        if ceiling is not None and ceiling.resolve(text) is None:
            continue
        resolved = deployment.resolve(text)
        if resolved is not None:
            out.add(resolved)
    return sorted(out)


def analysis_scope(own: list[str] | None, deployment: ContainerAllowlist, seen: Iterable[str]) -> list[str]:
    """The containers the analysis of one tick of a stack may query.

    ``seen`` are the containers the stack's own query returned in the tick. A stack with a list of its own (``own``)
    may query that list's exact entries, and each seen container one of its patterns admits; a stack without one,
    the seen containers. Either way only those the deployment's allowlist admits.
    """
    if own is None:
        return admitted(seen, deployment)
    mine = ContainerAllowlist(own)
    # A pattern entry is no name: the deployment's allowlist admits only plain names, so ``admitted`` drops it.
    return admitted(
        [*mine.entries, *(name for name in (mine.resolve(s) for s in seen) if name is not None)], deployment
    )


async def watched_stack(repo: str | None, configurable: Mapping[str, Any]) -> Any | None:
    """The stored watched stack of ``repo``; None for none, and when it could not be read.

    ``configurable`` is a graph's: its ``watcher_stacks_repo`` when it has one, else a repository over its ``db``.
    """
    if not repo:
        return None
    stacks = configurable.get("watcher_stacks_repo")
    db = configurable.get("db")
    try:
        if stacks is None and db is not None:
            from embry0.storage.repositories.watcher_stacks import WatcherStacksRepository

            stacks = WatcherStacksRepository(db)
        return await stacks.get(repo) if stacks is not None else None
    except StoredValueTooDeep:
        raise  # the stack's own allowlist is unknown, not absent (#527)
    except Exception:
        logger.warning("watched_stack_unavailable", repo=repo, exc_info=True)
        return None


async def repository_log_containers(repo: str | None, configurable: Mapping[str, Any], config: Any) -> list[str]:
    """The containers whose logs belong to ``repo``: the entries its watched stack's own container allowlist names
    (exact names and patterns), those the deployment's allowlist admits too. None for a repository with no stack, or
    whose stack names none.

    An exact name the deployment does not admit is dropped; a pattern is kept, as each name it admits is checked
    against the deployment's allowlist again where it is resolved.
    """
    stack = await watched_stack(repo, configurable)
    own = own_entries(stack) if stack is not None else None
    if not own:
        return []
    deployment = deployment_allowlist(config)
    return [entry for entry in own if is_pattern(entry) or deployment.resolve(entry) is not None]


async def analysis_job_scope(
    repo: str | None, granted: Iterable[Any], configurable: Mapping[str, Any], config: Any
) -> list[str]:
    """The containers an analysis job of ``repo``'s stack may query now: those of ``granted`` (the watcher's choice
    for its tick) that the stored stack still admits (its own list, else every name) and the deployment's allowlist
    admits. None without a stored stack."""
    stack = await watched_stack(repo, configurable)
    if stack is None:
        return []
    own = own_entries(stack)
    return admitted(granted, deployment_allowlist(config), None if own is None else ContainerAllowlist(own))


async def developer_job_scope(
    repo: str | None, granted: Iterable[Any], configurable: Mapping[str, Any], config: Any
) -> list[str]:
    """The containers a developer job may query now: those of ``granted`` (its grant at init) that its repository's
    containers and the deployment's allowlist still admit, while the developer log tool is on."""
    if not getattr(config, "developer_log_tool_enabled", False):
        return []
    mine = ContainerAllowlist(await repository_log_containers(repo, configurable, config))
    if not mine:
        return []
    return admitted(granted, deployment_allowlist(config), mine)
