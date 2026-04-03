"""Base workflow protocol."""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Workflow(Protocol):
    name: str
    description: str

    def compile(self, config: Any = None) -> Any: ...
