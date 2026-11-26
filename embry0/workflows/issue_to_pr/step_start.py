"""Starting a step of an issue-to-pr job (#395): what earlier steps left running is stopped, and the job's branch
opens to push at the first step that may put files on it.

Every step runs in the job's one sandbox. init enrolls the sandbox with the push right and no branch (the git-proxy
then refuses every ref update), so nothing is pushed until a step that holds ``coder`` or ``docs``
(``row_behaviour.PUSH_ROLES``) starts. That step opens the job's own branch (#559), which stays open for the rest of
the job, and the job records it (``push_open``), so a resume enrolls the sandbox with what the job had
(``embry0.services._executor_run``). A job none of whose steps holds either never opens it.

Before each step starts, and so before the branch opens, every process an earlier step left in the sandbox is
stopped (``SandboxManager.stop_leftover_processes``): a step's background process ends with its step. When the stop
cannot end clean, a step that may push (it holds ``coder`` or ``docs``, or the branch is open already) does not start
(``LeftoversNotStopped``, ``ERR_SANDBOX_LEFTOVERS``); a step with no role, before the branch is open, still runs, and
opens nothing. The stop covers processes only, not files a step leaves in the sandbox (the spec, D7).

A step's roles are the ones plan_route recorded on its route step (``row_behaviour.step_roles``); triage, which runs
before the route is planned, reads its own agent's row when it starts.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from embry0.execution.sandbox_processes import LeftoversNotStopped
from embry0.storage.database import StoredValueTooDeep
from embry0.workflows.row_behaviour import CODER, NEVER_CODERS, PUSH_ROLES, builtin_roles, roles, step_roles

logger = structlog.get_logger(__name__)

#: The kinds of step a node starts, as the dispatcher names a route step's node (``route_plan._step_target``), and
#: triage and the orchestrator's own test gate, which are no route step.
STEP_KINDS: Final = frozenset({"triage", "developer", "review", "verify", "agent", "qa", "output"})

Node = Callable[[dict[str, Any], RunnableConfig], Awaitable[Any]]


async def _triage_roles(config: RunnableConfig) -> frozenset[str]:
    """The roles triage's agent holds now; its seed's without a database, none when its row cannot be read."""
    db = (config.get("configurable") or {}).get("db")
    if db is None:
        return builtin_roles().get("triage", frozenset())
    try:
        from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository

        row = await AgentDefinitionsRepository(db).get("triage")
    except StoredValueTooDeep:
        raise  # a row too deep to read is no default (#527)
    except Exception:  # noqa: BLE001 — not knowing opens nothing
        logger.warning("step_start_triage_roles_unavailable", exc_info=True)
        return frozenset()
    held = builtin_roles().get("triage", frozenset()) if row is None else roles(row)
    return held - {CODER} if "triage" in NEVER_CODERS else held


def _route_step(state: Mapping[str, Any], kind: str) -> Mapping[str, Any] | None:
    """The route step this node runs: the one under the cursor when it is this kind's; else, for a node that runs
    one fixed step of the route (review, QA, output), the route's first of this kind. A generic agent's step is only
    ever the one under the cursor: another generic step is another agent. None for a job with no route, or no step
    of the kind."""
    from embry0.workflows.issue_to_pr.route_plan import _step_target

    plan = list(state.get("route_plan") or [])
    cursor = int(state.get("route_cursor", 0) or 0)
    steps: list[Mapping[str, Any]] = [step for step in plan if isinstance(step, Mapping)]
    if 0 <= cursor < len(plan) and isinstance(plan[cursor], Mapping) and _step_target(dict(plan[cursor])) == kind:
        current: Mapping[str, Any] = plan[cursor]
        return current
    if kind == "agent":
        return None
    return next((step for step in steps if _step_target(dict(step)) == kind), None)


async def step_roles_at_start(state: Mapping[str, Any], config: RunnableConfig, kind: str) -> frozenset[str]:
    """The roles the step a node of ``kind`` starts holds."""
    if kind == "triage":
        return await _triage_roles(config)
    if kind == "developer":
        return frozenset({CODER})  # the developer node runs the route's coder, or the legacy route's developer
    step = _route_step(state, kind)
    if step is not None:
        return step_roles(step)
    if kind == "review" and not state.get("route_plan"):
        return step_roles({"agent_type": "reviewer"})  # the legacy route's reviewer, as seeded
    return frozenset()


async def _stop_leftovers(state: Mapping[str, Any], config: RunnableConfig) -> LeftoversNotStopped | None:
    """Stop what earlier steps left running in the job's sandbox; why it could not, None when it did (or there is
    no sandbox)."""
    container = state.get("sandbox_container_id")
    sandbox = (config.get("configurable") or {}).get("sandbox_manager")
    stop = getattr(sandbox, "stop_leftover_processes", None) if sandbox is not None else None
    if not container or stop is None:
        return None
    try:
        stopped = stop(str(container))
        if inspect.isawaitable(stopped):
            await stopped
    except LeftoversNotStopped as exc:
        logger.warning("step_start_leftovers_not_stopped", job_id=state.get("job_id"), error=str(exc))
        return exc
    except Exception as exc:  # noqa: BLE001 — not knowing they were stopped is their not being stopped
        logger.warning("step_start_leftovers_not_stopped", job_id=state.get("job_id"), exc_info=True)
        return LeftoversNotStopped(str(container), f"{type(exc).__name__}: {str(exc)[:200]}")
    return None


async def open_push_branch(state: Mapping[str, Any], config: RunnableConfig) -> bool:
    """Let the job's sandbox push the job's own branch (#559), for the rest of the job; whether it was opened.

    The branch is the job's own name until the developer chooses one, which the developer node swaps in. With no
    git-proxy there is no enrollment to change, and nothing to push through. A refusal is logged: the step runs, and
    a coder's push then fails and says why (``ERR_DEV_BRANCH_NOT_PUSHED``).
    """
    from embry0.workflows.issue_to_pr.nodes._init import _job_push_branch

    proxy_mgr = (config.get("configurable") or {}).get("proxy_manager")
    container_id = state.get("sandbox_container_id")
    if not proxy_mgr or not getattr(proxy_mgr, "git_proxy_url", "") or not container_id:
        return False
    branch = _job_push_branch(dict(state))
    try:
        await proxy_mgr.set_git_push_branches(str(container_id), [branch])
    except Exception as exc:  # noqa: BLE001 — the job carries on; its push fails, and says so
        logger.warning("step_start_push_branch_not_opened", job_id=state.get("job_id"), branch=branch, error=str(exc))
        return False
    logger.info("step_start_push_branch_opened", job_id=state.get("job_id"), branch=branch)
    return True


def _step_agent(state: Mapping[str, Any], kind: str) -> str | None:
    """The agent type the step of ``kind`` runs as, for its row; None for triage, whose row is read already."""
    from embry0.workflows.issue_to_pr.route_plan import coding_agent_of, step_run_type
    from embry0.workflows.row_behaviour import DEFAULT_CODER

    if kind == "triage":
        return None
    if kind == "developer":
        plan = list(state.get("route_plan") or [])
        return coding_agent_of(plan) if plan else DEFAULT_CODER
    step = _route_step(state, kind)
    if step is None:
        return "review" if kind == "review" else None
    return step_run_type(str(step.get("agent_type") or ""))


async def _still_held(state: Mapping[str, Any], config: RunnableConfig, kind: str, held: frozenset[str]) -> bool:
    """Whether the step's agent still holds one of the push roles in ``held`` now (#395): an agent deleted since the
    route was planned, or one a role was taken from, opens nothing (``role_check.role_revoked``)."""
    from embry0.workflows.issue_to_pr.role_check import role_revoked

    agent = _step_agent(state, kind)
    if agent is None:
        return True
    try:
        for role in sorted(held & PUSH_ROLES):
            if await role_revoked(config, agent, role) is None:
                return True
    except StoredValueTooDeep:
        raise  # a row too deep to read is no default (#527)
    except Exception:  # noqa: BLE001 — not knowing opens nothing
        logger.warning("step_start_roles_unreadable", job_id=state.get("job_id"), agent=agent, exc_info=True)
        return False
    logger.warning("step_start_role_revoked", job_id=state.get("job_id"), agent=agent, roles=sorted(held))
    return False


async def begin_step(state: Mapping[str, Any], config: RunnableConfig, kind: str) -> dict[str, Any]:
    """Start a step of ``kind``: stop what earlier steps left running, then open the job's branch when the step holds
    a push role, its agent still holds it, and the job has not opened the branch yet. The state's updates:
    ``push_open`` once the branch is open."""
    not_stopped = await _stop_leftovers(state, config)
    held = await step_roles_at_start(state, config, kind)
    if not_stopped is not None:
        # The branch is open already (an old checkpoint's is), or this step may open it: it does not start beside a
        # process that was not stopped. A step with no role, before the branch is open, runs and opens nothing.
        if held & PUSH_ROLES or state.get("push_open") is not False:
            raise not_stopped
        return {}
    if state.get("push_open") is True:
        return {}
    if not held & PUSH_ROLES or not await _still_held(state, config, kind, held):
        return {}
    return {"push_open": True} if await open_push_branch(state, config) else {}


def _with_updates(result: Any, updates: Mapping[str, Any]) -> Any:
    """``result`` (a node's dict, Command or None) with ``updates`` under it: the node's own values win."""
    if not updates:
        return result
    if isinstance(result, Command):
        update = result.update
        if update is None:
            merged: Any = dict(updates)
        elif isinstance(update, Mapping):
            merged = {**updates, **update}
        else:
            merged = [*updates.items(), *list(update)]
        return Command(graph=result.graph, update=merged, resume=result.resume, goto=result.goto)
    if result is None:
        return dict(updates)
    return {**updates, **result}


def starts_a_step(node: Node, kind: str) -> Node:
    """``node``, starting its step first (:func:`begin_step`). The graph wraps each node a step starts at."""
    if kind not in STEP_KINDS:
        raise ValueError(f"not a kind of step: {kind!r}")
    # The node is called as LangGraph would call it: with the config only when it takes one.
    try:
        takes_config = len(inspect.signature(node).parameters) > 1
    except (TypeError, ValueError):
        takes_config = True

    async def step(state: dict[str, Any], config: RunnableConfig) -> Any:
        updates = await begin_step(state, config, kind)
        given = {**state, **updates}
        result = node(given, config) if takes_config else node(given)  # type: ignore[call-arg]
        return _with_updates(await result if inspect.isawaitable(result) else result, updates)

    step.__name__ = getattr(node, "__name__", "step")
    step.__qualname__ = getattr(node, "__qualname__", step.__name__)
    step.__doc__ = node.__doc__
    step.starts_step = kind  # type: ignore[attr-defined]
    step.wrapped = node  # type: ignore[attr-defined]
    return step
