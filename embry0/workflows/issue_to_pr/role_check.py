"""Whether the agent a step was planned with still holds the role it was planned with (#395).

plan_route records each step's roles when it plans the job, so routing is what the rows said then. A step whose role
lets it do more than its tools (``coder``, ``docs``) asks its agent's row again when it starts: an agent deleted since,
or one that no longer holds the role, does not run with it. The code's built-in definition answers only for a
built-in agent, or where there is no database at all.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from embry0.workflows.row_behaviour import CODER, NEVER_CODERS, builtin_roles, roles


async def role_revoked(config: RunnableConfig | None, agent_type: str, role: str) -> str | None:
    """Why the agent ``agent_type`` may no longer run its step as ``role``; None when it may.

    Without a database the built-in rows answer, as everywhere else. A read that fails raises: a step that cannot
    tell whether its agent still holds its role does not run as it.
    """
    configurable: dict[str, Any] = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    db = configurable.get("db")
    seeded = builtin_roles().get(agent_type)
    if db is None:
        return None
    from embry0.storage.repositories.agent_definitions import AgentDefinitionsRepository

    row = await AgentDefinitionsRepository(db).get(agent_type)
    if row is None and seeded is None:
        return f"the agent {agent_type!r} this step was planned with no longer exists, so it does not run as {role}"
    # A row read without the column (a release before it, a fake) holds what a built-in's seed says, as a missing row
    # does; one of the operator's own holds none.
    held = (seeded or frozenset()) if row is None or "roles" not in row else roles(row)
    if role == CODER and agent_type in NEVER_CODERS:
        held = held - {CODER}
    if role not in held:
        return f"the agent {agent_type!r} no longer holds the {role} role this step was planned with"
    return None
