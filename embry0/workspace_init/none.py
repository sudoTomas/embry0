"""No-op workspace initializer — an empty /workspace for context-free jobs."""

from __future__ import annotations

from typing import Any

from embry0.workspace_init.base import InitContext


class NoneWorkspaceInitializer:
    name = "none"

    def validate(self, context: dict[str, Any], config: Any | None) -> None:
        # JobContext already rejects stray fields for type=none.
        return

    async def initialize(self, ctx: InitContext) -> dict[str, Any]:
        # Downstream agents always get a working directory, even with no input.
        await ctx.docker.run_cmd(
            ctx.docker.build_exec_cmd(ctx.container_id, ["mkdir", "-p", "/workspace"]),
            timeout=10,
        )
        return {}
