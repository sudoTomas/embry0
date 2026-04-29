"""Shared pipeline validation helpers.

Extracted from WorkflowRegistry so the same checks can run at both workflow
registration time and pipeline-template create/update time (DB-stored templates
must not be able to smuggle deferred tools past the registry gate).
"""

from __future__ import annotations

from typing import Any

_DEFERRED_TOOLS = frozenset({"CreateIssue", "RequestInput", "UpdateStatus"})


def validate_pipeline_tools(name: str, pipeline_config: dict[str, Any]) -> None:
    """Reject pipelines that declare tools served by the deferred Athanor API proxy.

    ``pipeline_config`` is any dict that may contain an ``agent_tools`` key
    mapping agent names to lists of tool names.  For workflow registration
    callers this is ``workflow.pipeline_config``; for pipeline-template callers
    this is the ``graph_definition`` dict stored in the DB.

    Raises:
        ValueError: if any agent declares one of the reserved deferred tools.
    """
    agent_tools = pipeline_config.get("agent_tools", {}) if isinstance(pipeline_config, dict) else {}
    for agent, tools in (agent_tools or {}).items():
        bad = _DEFERRED_TOOLS.intersection(tools or [])
        if bad:
            raise ValueError(
                f"Pipeline {name!r} declares deferred tool(s) {sorted(bad)} "
                f"for agent {agent!r}. The Athanor API proxy that serves these is "
                "not yet implemented; see docs/architecture.md § Athanor API Proxy."
            )
