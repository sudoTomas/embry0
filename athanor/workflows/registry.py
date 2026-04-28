"""Workflow registry — register and discover available workflows."""

from typing import Any

import structlog

from athanor.workflows.base import Workflow

logger = structlog.get_logger(__name__)

_DEFERRED_TOOLS = frozenset({"CreateIssue", "RequestInput", "UpdateStatus"})


def _validate_workflow_tools(workflow_name: str, pipeline_config: dict) -> None:
    """Reject pipelines that declare tools served by the deferred Athanor API proxy."""
    agent_tools = pipeline_config.get("agent_tools", {}) if isinstance(pipeline_config, dict) else {}
    for agent, tools in (agent_tools or {}).items():
        bad = _DEFERRED_TOOLS.intersection(tools or [])
        if bad:
            raise ValueError(
                f"Workflow {workflow_name!r} declares deferred tool(s) {sorted(bad)} "
                f"for agent {agent!r}. The Athanor API proxy that serves these is "
                "not yet implemented; see docs/architecture.md § Athanor API Proxy."
            )


class WorkflowRegistry:
    def __init__(self) -> None:
        self._workflows: dict[str, Workflow] = {}

    def register(self, workflow: Workflow) -> None:
        # Validate the pipeline_config if present on the workflow object
        pipeline_config = getattr(workflow, "pipeline_config", None) or {}
        _validate_workflow_tools(workflow.name, pipeline_config)
        self._workflows[workflow.name] = workflow
        logger.info("workflow_registered", name=workflow.name)

    def get(self, name: str) -> Workflow | None:
        return self._workflows.get(name)

    def list(self) -> list[dict[str, Any]]:
        return [{"name": w.name, "description": w.description} for w in self._workflows.values()]
