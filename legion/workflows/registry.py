"""Workflow registry — register and discover available workflows."""

from typing import Any

import structlog

from legion.workflows.base import Workflow

logger = structlog.get_logger(__name__)


class WorkflowRegistry:
    def __init__(self) -> None:
        self._workflows: dict[str, Workflow] = {}

    def register(self, workflow: Workflow) -> None:
        self._workflows[workflow.name] = workflow
        logger.info("workflow_registered", name=workflow.name)

    def get(self, name: str) -> Workflow | None:
        return self._workflows.get(name)

    def list(self) -> list[dict[str, Any]]:
        return [{"name": w.name, "description": w.description} for w in self._workflows.values()]
