"""GET /tools: the tool names an agent definition can list (spec 3.4)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from embry0.services.tool_catalog import BUILTIN_TOOLS, tool_catalog

router = APIRouter()

__all__ = ["BUILTIN_TOOLS", "router", "tool_catalog"]


@router.get("/tools")
async def list_tools() -> list[dict[str, Any]]:
    return tool_catalog()
