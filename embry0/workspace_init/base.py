"""WorkspaceInitializer Protocol + shared context/error types.

An initializer owns exactly one concern: populating ``/workspace`` inside
an already-created sandbox for its context type. It does NOT create or
destroy the sandbox — ``init_node`` keeps the container lifecycle (and its
destroy-on-failure path), so a raising ``initialize`` still leaves no
orphan containers. There is no per-strategy cleanup hook for the same
reason: sandbox destroy is the single teardown for every strategy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


class WorkspaceInitError(RuntimeError):
    """A strategy failed to populate the workspace.

    Subclasses RuntimeError so callers that historically treated init
    failures as RuntimeError keep working; error_code_for_exception checks
    the specific subclasses before its generic RuntimeError branches.
    """


class LocalContextDeniedError(WorkspaceInitError):
    """A local context path was rejected by the allowlist or bounds."""


class HttpFetchError(WorkspaceInitError):
    """An http context source could not be fetched within bounds."""


@dataclass(frozen=True)
class InitContext:
    """Everything a strategy may need to populate ``/workspace``.

    ``docker`` is the orchestrator's DockerClient; ``emit`` forwards to the
    LangGraph stream writer so strategies can surface progress events.
    """

    job_id: str
    context: dict[str, Any]
    container_id: str
    sandbox_token: str
    docker: Any
    git_proxy_url: str = ""
    repo_preferences_repo: Any | None = None
    config: Any | None = None
    emit: Callable[[dict[str, Any]], None] = lambda _event: None


@runtime_checkable
class WorkspaceInitializer(Protocol):
    """Per-context-type workspace population strategy."""

    name: str

    def validate(self, context: dict[str, Any], config: Any | None) -> None:
        """Reject a context BEFORE any sandbox exists.

        Raising here must leave no side effects — init_node calls this
        ahead of ``sandbox_mgr.create`` so denied contexts (bad allowlist
        path, malformed URL) never allocate a container.
        """
        ...

    async def initialize(self, ctx: InitContext) -> dict[str, Any]:
        """Populate ``/workspace`` in the running sandbox.

        Returns a state-update fragment merged into init_node's return
        (may be empty). Raise WorkspaceInitError subclasses on failure —
        init_node's except path destroys the container.
        """
        ...
